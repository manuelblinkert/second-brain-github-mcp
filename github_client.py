"""Single GitHub REST API helper for this MCP server.

Everything that talks to GitHub goes through here. This is the only place a
per-member GitHub PAT is attached to a request. The PAT is passed in by the
caller (resolved from the member registry) and is never logged, never returned
in a tool result, and never included in an error surfaced to Claude.

All access is hard-scoped to one configured owner/repo
(``GITHUB_ALLOWED_OWNER`` / ``GITHUB_ALLOWED_REPO``, read from environment).
No tool accepts an arbitrary owner, repo, or URL. Within that repo, every
path is readable and writable.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx

GITHUB_API_BASE = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 15.0

# Largest decoded text file github_read_file will return (spec section 9).
MAX_FILE_BYTES = 100 * 1024


class GitHubToolError(Exception):
    """A clean, user-facing error for a GitHub-backed tool.

    The message is safe to surface to Claude: it never contains the PAT or any
    other secret. Raise this for both input-validation failures and converted
    GitHub API errors.
    """


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise GitHubToolError(f"Server is misconfigured: missing {name}.")
    return value


class GitHubClient:
    """Thin, hard-scoped wrapper over the GitHub REST API."""

    def __init__(self, token: str, *, transport: "httpx.BaseTransport | None" = None) -> None:
        self.owner = _require_env("GITHUB_ALLOWED_OWNER")
        self.repo = _require_env("GITHUB_ALLOWED_REPO")
        # Per-member PAT, passed in by the caller (resolved from the member
        # registry by OAuth subject). Never read from a tool argument, never
        # logged, never returned.
        self._token = token
        self._transport = transport
        self._client: httpx.Client | None = None
        self._default_branch: str | None = None

    # -- low level ---------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=GITHUB_API_BASE,
                timeout=REQUEST_TIMEOUT_SECONDS,
                transport=self._transport,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "second-brain-github-mcp",
                },
            )
        return self._client

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make one GitHub request and convert failures into clean errors.

        Never logs or echoes the Authorization header. ``path`` is always a
        server-constructed API path, never a raw URL from Claude.
        """
        try:
            response = self._http().request(method, path, **kwargs)
        except httpx.TimeoutException as exc:  # noqa: PERF203 - clarity over micro-opt
            raise GitHubToolError("GitHub request timed out. Try again.") from exc
        except httpx.HTTPError as exc:
            # Deliberately do not include exc detail: it can echo the request
            # URL/headers in some httpx versions.
            raise GitHubToolError("Could not reach GitHub.") from exc

        if response.is_success:
            return response

        self._raise_for_status(response)
        return response  # unreachable, keeps type checkers happy

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code

        # Rate limiting: GitHub uses 403/429 with these headers.
        remaining = response.headers.get("x-ratelimit-remaining")
        if status in (403, 429) and remaining == "0":
            raise GitHubToolError(
                "GitHub rate limit reached. Wait a bit and try again."
            )

        if status == 401:
            # A 401 here means the server-side PAT is bad/expired. Do not leak
            # the token; just say the server can't authenticate to GitHub.
            raise GitHubToolError(
                "Server could not authenticate to GitHub (check the server PAT)."
            )

        if status == 403:
            raise GitHubToolError(
                "GitHub denied access to the requested resource in the "
                "configured repository."
            )

        if status == 404:
            # Do not distinguish "missing" from "no access" in a way that
            # leaks the existence of other private repos. Everything is scoped
            # to the one configured repo, so a 404 is simply "not found here".
            raise GitHubToolError("Not found in the configured repository.")

        if status == 409:
            raise GitHubToolError(
                "The file changed on GitHub since it was read. Re-read it and "
                "retry the write."
            )

        if status == 422:
            raise GitHubToolError("GitHub rejected the request as invalid.")

        raise GitHubToolError(f"GitHub returned an unexpected error ({status}).")

    def _repo_path(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    # -- repo metadata -----------------------------------------------------

    def repo_info(self) -> dict[str, Any]:
        data = self._request("GET", self._repo_path("")).json()
        self._default_branch = data.get("default_branch")
        return {
            "owner": self.owner,
            "repo": self.repo,
            "default_branch": data.get("default_branch"),
            "private": data.get("private"),
            "html_url": data.get("html_url"),
            "description": data.get("description"),
        }

    def default_branch(self) -> str:
        if self._default_branch is None:
            self.repo_info()
        # repo_info always sets it on success.
        return self._default_branch or "main"

    def verify_identity(self) -> str:
        """Return the GitHub login that owns this client's PAT.

        A "whoami" diagnostic for confirming a PAT is wired to the right
        account. Never logs or returns the token.
        """
        data = self._request("GET", "/user").json()
        login = data.get("login")
        if not login:
            raise GitHubToolError(
                "Could not read the GitHub identity for a configured PAT."
            )
        return login

    # -- files -------------------------------------------------------------

    def read_file(self, path: str, ref: str | None) -> dict[str, Any]:
        clean = validate_repo_path(path, allow_empty=False)
        params = {"ref": ref} if ref else None
        data = self._request(
            "GET", self._repo_path(f"/contents/{clean}"), params=params
        ).json()

        if isinstance(data, list):
            raise GitHubToolError(
                f"'{clean}' is a directory, not a file. Use github_list_directory."
            )

        if data.get("type") != "file":
            raise GitHubToolError(f"'{clean}' is not a readable file.")

        size = data.get("size", 0)
        if size > MAX_FILE_BYTES:
            raise GitHubToolError(
                f"File is too large to read ({size} bytes; limit "
                f"{MAX_FILE_BYTES})."
            )

        raw = base64.b64decode(data.get("content", ""))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitHubToolError("File is binary or not UTF-8 text.") from exc

        return {
            "owner": self.owner,
            "repo": self.repo,
            "path": clean,
            "ref": ref or self.default_branch(),
            "content": text,
            "sha": data.get("sha"),
            "encoding": "utf-8",
        }

    def list_directory(self, path: str, ref: str | None) -> dict[str, Any]:
        clean = validate_repo_path(path, allow_empty=True)
        params = {"ref": ref} if ref else None
        suffix = f"/contents/{clean}" if clean else "/contents"
        data = self._request("GET", self._repo_path(suffix), params=params).json()

        if not isinstance(data, list):
            raise GitHubToolError(
                f"'{clean}' is a file, not a directory. Use github_read_file."
            )

        entries = [
            {
                "name": item.get("name"),
                "path": item.get("path"),
                "type": item.get("type"),
                "sha": item.get("sha"),
                "size": item.get("size"),
            }
            for item in data[:200]
        ]
        return {
            "owner": self.owner,
            "repo": self.repo,
            "path": clean,
            "ref": ref or self.default_branch(),
            "entries": entries,
        }

    def list_tree(self, ref: str | None) -> dict[str, Any]:
        branch = ref or self.default_branch()
        # Resolve the branch to its tree SHA, then pull the recursive tree.
        branch_data = self._request(
            "GET", self._repo_path(f"/branches/{branch}")
        ).json()
        tree_sha = (
            branch_data.get("commit", {})
            .get("commit", {})
            .get("tree", {})
            .get("sha")
        )
        if not tree_sha:
            raise GitHubToolError("Could not resolve the repository tree.")

        tree_data = self._request(
            "GET",
            self._repo_path(f"/git/trees/{tree_sha}"),
            params={"recursive": "1"},
        ).json()

        paths = [
            node["path"]
            for node in tree_data.get("tree", [])
            if node.get("type") == "blob" and str(node.get("path", "")).endswith(".md")
        ]
        return {
            "owner": self.owner,
            "repo": self.repo,
            "ref": branch,
            "truncated": bool(tree_data.get("truncated")),
            "paths": paths,
        }

    # -- issues / PRs ------------------------------------------------------

    def list_issues(self, state: str, limit: int) -> list[dict[str, Any]]:
        state = _validate_state(state)
        per_page = _validate_limit(limit, maximum=50)
        data = self._request(
            "GET",
            self._repo_path("/issues"),
            params={"state": state, "per_page": per_page},
        ).json()

        issues = []
        for item in data:
            # The issues endpoint also returns PRs; skip those.
            if "pull_request" in item:
                continue
            issues.append(
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "html_url": item.get("html_url"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "labels": [label.get("name") for label in item.get("labels", [])],
                }
            )
        return issues

    def list_pull_requests(self, state: str, limit: int) -> list[dict[str, Any]]:
        state = _validate_state(state)
        per_page = _validate_limit(limit, maximum=50)
        data = self._request(
            "GET",
            self._repo_path("/pulls"),
            params={"state": state, "per_page": per_page},
        ).json()

        return [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "html_url": item.get("html_url"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "head_ref": item.get("head", {}).get("ref"),
                "base_ref": item.get("base", {}).get("ref"),
            }
            for item in data
        ]

    # -- writes ------------------------------------------------------------

    def _current_file_sha(self, clean_path: str, branch: str) -> str | None:
        """Return the current blob sha for a file, or None if it doesn't exist.

        Used only on the convenience path of github_write_file (agent supplied
        no sha). Does not raise on 404 — a missing file means "create".
        """
        try:
            response = self._http().request(
                "GET",
                self._repo_path(f"/contents/{clean_path}"),
                params={"ref": branch},
            )
        except httpx.TimeoutException as exc:
            raise GitHubToolError("GitHub request timed out. Try again.") from exc
        except httpx.HTTPError as exc:
            raise GitHubToolError("Could not reach GitHub.") from exc

        if response.status_code == 404:
            return None
        if not response.is_success:
            self._raise_for_status(response)

        data = response.json()
        if isinstance(data, list):
            raise GitHubToolError(
                f"'{clean_path}' is a directory, not a file."
            )
        return data.get("sha")

    def write_file(
        self,
        path: str,
        content: str,
        message: str | None,
        sha: str | None,
    ) -> dict[str, Any]:
        clean = validate_write_path(path)

        if content is None:
            raise GitHubToolError("content is required.")
        if not isinstance(content, str):
            raise GitHubToolError("content must be a string.")

        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise GitHubToolError(
                f"Content is too large to write ({len(encoded)} bytes; limit "
                f"{MAX_FILE_BYTES})."
            )

        branch = self.default_branch()
        body: dict[str, Any] = {
            "message": (message or f"Update {clean} via Claude").strip()
            or f"Update {clean} via Claude",
            "content": base64.b64encode(encoded).decode("ascii"),
            "branch": branch,
        }

        # Optimistic concurrency: prefer the agent-supplied sha (from a prior
        # github_read_file). A stale sha makes GitHub return 409, surfaced as a
        # retryable error rather than a silent clobber. Only when the agent
        # supplied no sha do we look up the current one — and only to overwrite
        # an existing file; absence means create. Never overwrite an
        # agent-supplied sha with a freshly fetched one.
        if sha:
            body["sha"] = sha
        else:
            existing = self._current_file_sha(clean, branch)
            if existing:
                body["sha"] = existing

        data = self._request(
            "PUT", self._repo_path(f"/contents/{clean}"), json=body
        ).json()

        commit = data.get("commit", {}) or {}
        content_meta = data.get("content", {}) or {}
        return {
            "owner": self.owner,
            "repo": self.repo,
            "path": clean,
            "ref": branch,
            "commit_sha": commit.get("sha"),
            "sha": content_meta.get("sha"),
            "html_url": content_meta.get("html_url"),
        }

    # -- search ------------------------------------------------------------

    def search_notes(self, query: str, limit: int) -> list[dict[str, Any]]:
        if not query or not query.strip():
            raise GitHubToolError("query is required.")
        per_page = _validate_limit(limit, maximum=25)
        scoped = f"{query.strip()} repo:{self.owner}/{self.repo}"
        data = self._request(
            "GET",
            "/search/code",
            params={"q": scoped, "per_page": per_page},
        ).json()

        return [
            {
                "path": item.get("path"),
                "name": item.get("name"),
                "html_url": item.get("html_url"),
                "score": item.get("score"),
            }
            for item in data.get("items", [])
        ]


# -- shared validation -----------------------------------------------------


def validate_write_path(path: str | None) -> str:
    """Validate a path for github_write_file. Returns the cleaned path.

    Same rules as ``validate_repo_path`` (rejects ``..``, absolute,
    Windows-drive, backslash, empty) — writes are allowed anywhere in the
    repo, same as reads.
    """
    return validate_repo_path(path, allow_empty=False)


def validate_repo_path(path: str | None, *, allow_empty: bool) -> str:
    """Validate a relative repo path. Returns the cleaned, slash-trimmed path.

    Rejects absolute paths, Windows drive paths, backslashes, and any segment
    equal to '..'. Empty is allowed only when ``allow_empty`` is True (repo
    root for directory listings).
    """
    if path is None:
        if allow_empty:
            return ""
        raise GitHubToolError("path is required.")

    p = path.strip()
    if p == "":
        if allow_empty:
            return ""
        raise GitHubToolError("path must not be empty.")

    if "\\" in p:
        raise GitHubToolError("Use forward slashes in paths, not backslashes.")
    if p.startswith("/"):
        raise GitHubToolError("path must be relative to the repository root.")
    if len(p) >= 2 and p[1] == ":":
        raise GitHubToolError("path must be relative to the repository root.")
    if any(segment == ".." for segment in p.split("/")):
        raise GitHubToolError("path must not contain '..'.")

    return p.strip("/")


def _validate_state(state: str | None) -> str:
    value = (state or "open").strip().lower()
    if value not in ("open", "closed", "all"):
        raise GitHubToolError("state must be one of: open, closed, all.")
    return value


def _validate_limit(limit: int | None, *, maximum: int) -> int:
    if limit is None:
        return min(20, maximum)
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise GitHubToolError("limit must be an integer.") from exc
    if value < 1:
        raise GitHubToolError("limit must be at least 1.")
    return min(value, maximum)
