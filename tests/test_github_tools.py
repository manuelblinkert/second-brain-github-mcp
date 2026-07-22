# tests/test_github_tools.py
import pytest

import github_tools
from github_tools import resolve_client
from github_client import GitHubToolError
from registry import Member, MemberRegistry


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_ALLOWED_OWNER", "acme")
    monkeypatch.setenv("GITHUB_ALLOWED_REPO", "acme-vault")


def _registry():
    return MemberRegistry([
        Member("alice", "alice", "pw", "tok-alice"),
    ])


def test_resolve_client_uses_callers_member_token(monkeypatch):
    monkeypatch.setattr(github_tools, "get_access_token", lambda: _FakeToken("alice"))
    cache = {}
    client = resolve_client(_registry(), cache)
    assert client._token == "tok-alice"
    # second call returns the cached instance
    assert resolve_client(_registry(), cache) is client


def test_resolve_client_rejects_unauthenticated(monkeypatch):
    monkeypatch.setattr(github_tools, "get_access_token", lambda: None)
    with pytest.raises(GitHubToolError):
        resolve_client(_registry(), {})


def test_resolve_client_rejects_unknown_member(monkeypatch):
    monkeypatch.setattr(github_tools, "get_access_token", lambda: _FakeToken("intruder"))
    with pytest.raises(GitHubToolError):
        resolve_client(_registry(), {})


def test_resolve_client_differs_per_member(monkeypatch):
    reg = MemberRegistry([
        Member("alice", "alice", "pw", "tok-alice"),
        Member("bob", "bob", "pw", "tok-bob"),
    ])
    cache = {}
    monkeypatch.setattr(github_tools, "get_access_token", lambda: _FakeToken("alice"))
    c1 = resolve_client(reg, cache)
    monkeypatch.setattr(github_tools, "get_access_token", lambda: _FakeToken("bob"))
    c2 = resolve_client(reg, cache)
    assert c1 is not c2
    assert c1._token == "tok-alice"
    assert c2._token == "tok-bob"


class _FakeToken:
    def __init__(self, subject):
        self.subject = subject
