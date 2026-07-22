# tests/test_github_client.py
import json

import httpx
import pytest

import github_client
from github_client import GitHubClient, GitHubToolError, validate_write_path


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_ALLOWED_OWNER", "acme")
    monkeypatch.setenv("GITHUB_ALLOWED_REPO", "acme-vault")


# -- write path validation ---------------------------------------------------

@pytest.mark.parametrize("path", [
    "_inbox/idea.md",
    "projects-and-plans/q3-launch.md",
    "_inbox/daily-notes/2026-06-24.md",
    "CLAUDE.md",
    "README.md",
    "10-company/strategy.md",
    "90-archive/old.md",
    "_ai-context-packs/brand-context.md",
    "00-meta/vault-architecture.md",
    ".obsidian/app.json",
])
def test_validate_write_path_allows_anywhere_in_repo(path):
    assert validate_write_path(path) == path


@pytest.mark.parametrize("path", ["../x.md", "/abs.md", "a\\b.md", ".."])
def test_validate_write_path_rejects_traversal(path):
    with pytest.raises(GitHubToolError):
        validate_write_path(path)


# -- verify_identity --------------------------------------------------------

def _mock_client(handler, token="tok-secret"):
    return GitHubClient(token, transport=httpx.MockTransport(handler))


def test_verify_identity_returns_login():
    def handler(request):
        assert request.url.path == "/user"
        return httpx.Response(200, json={"login": "octocat"})
    client = _mock_client(handler)
    assert client.verify_identity() == "octocat"


def test_token_never_leaks_in_auth_error():
    def handler(request):
        return httpx.Response(401, json={"message": "Bad credentials"})
    client = _mock_client(handler, token="super-secret-pat")
    with pytest.raises(GitHubToolError) as exc:
        client.verify_identity()
    assert "super-secret-pat" not in str(exc.value)


def test_write_file_sends_branch_and_attributes_via_pat():
    seen = {}

    def handler(request):
        if request.method == "GET":
            # _current_file_sha lookup for a new file -> 404 (create)
            return httpx.Response(404, json={"message": "Not Found"})
        body = json.loads(request.content)
        seen.update(body)
        return httpx.Response(201, json={
            "commit": {"sha": "commitsha"},
            "content": {"sha": "blobsha", "html_url": "https://example/x"},
        })

    client = _mock_client(handler)
    # default_branch is needed by write_file; stub it to avoid a repo_info call.
    client._default_branch = "main"
    result = client.write_file("_inbox/note.md", "hello", None, None)
    assert seen["branch"] == "main"
    assert result["commit_sha"] == "commitsha"
