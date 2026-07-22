import asyncio

import pytest

from auth import TeamOAuthProvider
from registry import Member, MemberRegistry


def _registry():
    return MemberRegistry([
        Member("alice", "alice", "pw-alice", "tok-m"),
        Member("bob", "bob", "pw-bob", "tok-n"),
    ])


def _provider():
    return TeamOAuthProvider(
        server_url="http://localhost:8000",
        login_path="http://localhost:8000/login",
        registry=_registry(),
    )


def _seed_state(provider, state="s1"):
    provider.state_mapping[state] = {
        "redirect_uri": "http://localhost:9000/callback",
        "code_challenge": "challenge",
        "redirect_uri_provided_explicitly": "True",
        "client_id": "client-1",
        "resource": None,
    }


def test_login_with_valid_member_sets_member_id_subject():
    provider = _provider()
    _seed_state(provider)
    asyncio.run(provider._complete_login("bob", "pw-bob", "s1"))
    # exactly one auth code minted, subject is the member id
    (code,) = list(provider.auth_codes.values())
    assert code.subject == "bob"


def test_login_with_wrong_password_is_rejected():
    from starlette.exceptions import HTTPException
    provider = _provider()
    _seed_state(provider)
    with pytest.raises(HTTPException):
        asyncio.run(provider._complete_login("bob", "wrong", "s1"))


def test_load_access_token_accepts_known_member_subject():
    from mcp.server.auth.provider import AccessToken
    provider = _provider()
    provider.tokens["t1"] = AccessToken(
        token="t1", client_id="c", scopes=["mcp"],
        expires_at=None, resource=None, subject="alice",
    )
    loaded = asyncio.run(provider.load_access_token("t1"))
    assert loaded is not None and loaded.subject == "alice"


def test_load_access_token_rejects_unknown_subject():
    from mcp.server.auth.provider import AccessToken
    provider = _provider()
    provider.tokens["t1"] = AccessToken(
        token="t1", client_id="c", scopes=["mcp"],
        expires_at=None, resource=None, subject="intruder",
    )
    assert asyncio.run(provider.load_access_token("t1")) is None
