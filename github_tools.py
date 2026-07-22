"""GitHub tools, registered on the FastMCP server.

Seven read tools plus one write tool (``github_write_file``). All are
hard-scoped to the one configured owner/repo via ``GITHUB_ALLOWED_OWNER`` /
``GITHUB_ALLOWED_REPO`` and go through a per-member ``GitHubClient`` (see
``github_client.py``), resolved from the calling member's OAuth subject so
commits are attributed to that member's own GitHub account. No tool accepts
an arbitrary owner, repo, or URL. Within that one repo, every path is
readable and writable; the write tool can never delete. No member's GitHub
PAT ever appears in any tool argument or result.

Tool docstrings double as the descriptions Claude sees, so they are written
for an agent that may be running from a phone with no vault CLAUDE.md loaded.
"""

from __future__ import annotations

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP

from github_client import GitHubClient, GitHubToolError
from registry import MemberRegistry


def resolve_client(
    registry: MemberRegistry, clients_cache: dict[str, GitHubClient]
) -> GitHubClient:
    """Return a GitHubClient built with the calling member's PAT.

    The OAuth subject (set at login) is the member id. We look up that member
    in the registry and build/cache one client per member id so each member's
    commits are authored by their own GitHub account. Runs in the same context
    as the request, so the auth contextvar is visible to this sync tool.
    """
    token = get_access_token()
    if token is None or not token.subject:
        raise GitHubToolError("Not authenticated.")
    member = registry.by_id(token.subject)
    if member is None:
        raise GitHubToolError("Unknown member identity.")
    client = clients_cache.get(member.member_id)
    if client is None:
        client = GitHubClient(member.github_token)
        clients_cache[member.member_id] = client
    return client


def register_github_tools(mcp: FastMCP, registry: MemberRegistry) -> None:
    """Register all read-only GitHub tools on the given FastMCP instance."""

    # One client per member, cached per process. Constructed lazily on first
    # tool call by the calling member so import of this module never requires
    # the GitHub env vars to be present.
    clients_cache: dict[str, GitHubClient] = {}

    def get_client() -> GitHubClient:
        return resolve_client(registry, clients_cache)

    @mcp.tool()
    def github_repo_info() -> dict[str, Any]:
        """Return basic metadata for the one connected repository.

        Use this to learn the default branch, whether the repo is private, and
        its URL. Takes no arguments — the repository is fixed by the server.
        """
        try:
            return get_client().repo_info()
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def github_list_tree(ref: str | None = None) -> dict[str, Any]:
        """List every Markdown note path in the repository (recursive).

        This is the best way to FIND a note when you don't know its exact path:
        get the full list of paths, then pick the one that matches what the
        user asked for (e.g. "the Q3 launch plan" ->
        "projects-and-plans/q3-launch.md"). Reflects the live repository, so
        notes edited moments ago are included. Prefer this over
        github_search_notes for finding files.

        ref: branch/tag/commit to list; defaults to the repository's default
        branch.
        """
        try:
            return get_client().list_tree(ref)
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def github_read_file(path: str, ref: str | None = None) -> dict[str, Any]:
        """Read a single text file from the repository.

        Returns the file content plus its 'sha'. Keep that 'sha' if you intend
        to edit the file later — the write tool uses it to avoid overwriting
        changes.

        path: repository-relative path, e.g.
        "projects-and-plans/q3-launch.md". Must not be absolute and must not
        contain "..".
        ref: branch/tag/commit to read from; defaults to the default branch.
        """
        try:
            return get_client().read_file(path, ref)
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def github_list_directory(
        path: str = "", ref: str | None = None
    ) -> dict[str, Any]:
        """List the files and folders directly under one directory.

        Returns up to 200 entries. For finding a note anywhere in the vault,
        github_list_tree is usually better (one call, all paths).

        path: repository-relative directory path; defaults to the repo root.
        ref: branch/tag/commit; defaults to the default branch.
        """
        try:
            return get_client().list_directory(path, ref)
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def github_search_notes(query: str, limit: int = 10) -> dict[str, Any]:
        """Search note CONTENT for a phrase (best-effort).

        Use this only when you can't locate a note by path/title with
        github_list_tree — for example "the note where I wrote about pricing".
        Caveat: GitHub's search index lags behind commits, so a note created or
        edited in the last few minutes may not appear yet. For recent or
        complete results, use github_list_tree instead.

        query: words to search for in file contents.
        limit: max results (max 25).
        """
        try:
            return {"results": get_client().search_notes(query, limit)}
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def github_list_issues(state: str = "open", limit: int = 20) -> dict[str, Any]:
        """List issues in the repository (likely empty for a notes vault).

        state: "open", "closed", or "all".
        limit: max issues (max 50).
        """
        try:
            return {"issues": get_client().list_issues(state, limit)}
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def github_list_pull_requests(
        state: str = "open", limit: int = 20
    ) -> dict[str, Any]:
        """List pull requests in the repository (likely empty for a notes vault).

        state: "open", "closed", or "all".
        limit: max pull requests (max 50).
        """
        try:
            return {"pull_requests": get_client().list_pull_requests(state, limit)}
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def github_write_file(
        path: str,
        content: str,
        message: str | None = None,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Create or update one file, committed directly to the main branch.

        Use this to capture a new note or save edits to an existing one.
        Any path in the repository can be created or updated — nothing is
        off-limits — but this tool cannot delete anything. To learn this
        vault's conventions before writing, github_read_file("CLAUDE.md").

        IMPORTANT — this REPLACES the whole file with `content`; there is no
        partial/patch mode. To edit an existing note, first github_read_file to
        get its current text and `sha`, modify the full text, then pass the
        complete new body here together with that `sha`. The `sha` guards
        against overwriting a change made since you read: if the file moved on,
        the write fails and you should re-read and retry rather than clobber.

        path: repository-relative path, e.g.
          "projects-and-plans/q3-launch.md". Must not be absolute or contain "..".
        content: the FULL new file body (UTF-8 text).
        message: commit message; defaults to "Update <path> via Claude".
        sha: the `sha` from github_read_file when editing an existing file.
          Omit only when creating a brand-new file.
        """
        try:
            return get_client().write_file(path, content, message, sha)
        except GitHubToolError as exc:
            raise ValueError(str(exc)) from exc
