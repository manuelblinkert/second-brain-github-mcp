import asyncio
import time
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import AccessToken, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyHttpUrl

from auth import TeamOAuthProvider
from oauth_store import InMemoryOAuthStore, LoginState, SQLiteOAuthStore
from registry import Member, MemberRegistry


def _registry():
    return MemberRegistry([
        Member("alice", "alice", "pw-alice", "tok-m"),
        Member("bob", "bob", "pw-bob", "tok-n"),
    ])


def _provider(store=None):
    return TeamOAuthProvider(
        server_url="http://localhost:8000",
        login_path="http://localhost:8000/login",
        registry=_registry(),
        store=store,
    )


def _seed_state(provider, state="s1"):
    provider.store.save_login_state(
        state,
        LoginState(
            redirect_uri="http://localhost:9000/callback",
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
            client_id="client-1",
            resource=None,
            expires_at=time.time() + 60,
        ),
    )


def test_login_with_valid_member_sets_member_id_subject():
    store = InMemoryOAuthStore()
    provider = _provider(store)
    _seed_state(provider)
    asyncio.run(provider._complete_login("bob", "pw-bob", "s1"))
    # exactly one auth code minted, subject is the member id
    (code,) = list(store.authorization_codes.values())
    assert code.subject == "bob"


def test_login_with_wrong_password_is_rejected():
    from starlette.exceptions import HTTPException
    provider = _provider()
    _seed_state(provider)
    with pytest.raises(HTTPException):
        asyncio.run(provider._complete_login("bob", "wrong", "s1"))


def test_load_access_token_accepts_known_member_subject():
    store = InMemoryOAuthStore()
    provider = _provider(store)
    store.save_access_token(AccessToken(
        token="t1", client_id="c", scopes=["mcp"],
        expires_at=None, resource=None, subject="alice",
    ))
    loaded = asyncio.run(provider.load_access_token("t1"))
    assert loaded is not None and loaded.subject == "alice"


def test_load_access_token_rejects_unknown_subject():
    store = InMemoryOAuthStore()
    provider = _provider(store)
    store.save_access_token(AccessToken(
        token="t1", client_id="c", scopes=["mcp"],
        expires_at=None, resource=None, subject="intruder",
    ))
    assert asyncio.run(provider.load_access_token("t1")) is None


def test_sqlite_store_survives_provider_restarts(tmp_path):
    database_path = tmp_path / "oauth.sqlite3"
    redirect_uri = AnyHttpUrl("http://localhost:9000/callback")
    client = OAuthClientInformationFull(
        client_id="chatgpt-client",
        client_secret="client-secret",
        redirect_uris=[redirect_uri],
        token_endpoint_auth_method="client_secret_post",
        scope="mcp",
    )

    provider_1 = _provider(SQLiteOAuthStore(database_path))
    asyncio.run(provider_1.register_client(client))
    asyncio.run(provider_1.authorize(
        client,
        AuthorizationParams(
            state="state-1",
            scopes=["mcp"],
            code_challenge="challenge",
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=True,
            resource="http://localhost:8000/mcp",
        ),
    ))

    provider_2 = _provider(SQLiteOAuthStore(database_path))
    loaded_client = asyncio.run(provider_2.get_client("chatgpt-client"))
    assert loaded_client is not None
    assert loaded_client.client_secret == "client-secret"
    callback = asyncio.run(provider_2._complete_login("alice", "pw-alice", "state-1"))
    authorization_code_value = parse_qs(urlparse(callback).query)["code"][0]
    assert provider_2.store.get_login_state("state-1") is None

    provider_3 = _provider(SQLiteOAuthStore(database_path))
    authorization_code = asyncio.run(
        provider_3.load_authorization_code(loaded_client, authorization_code_value)
    )
    assert authorization_code is not None
    issued_token = asyncio.run(
        provider_3.exchange_authorization_code(loaded_client, authorization_code)
    )
    assert provider_3.store.get_authorization_code(authorization_code_value) is None

    provider_4 = _provider(SQLiteOAuthStore(database_path))
    access_token = asyncio.run(provider_4.load_access_token(issued_token.access_token))
    assert access_token is not None
    assert access_token.subject == "alice"


def test_sqlite_store_removes_expired_login_state(tmp_path):
    store = SQLiteOAuthStore(tmp_path / "oauth.sqlite3")
    store.save_login_state(
        "expired",
        LoginState(
            redirect_uri="http://localhost:9000/callback",
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
            client_id="client-1",
            expires_at=time.time() - 1,
        ),
    )

    assert store.get_login_state("expired") is None
