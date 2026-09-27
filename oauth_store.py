"""Storage backends for the self-hosted OAuth authorization server.

SQLite is the restart-safe backend for a single server process. The in-memory
backend preserves the original lightweight behavior for local or disposable
deployments.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Protocol

from mcp.server.auth.provider import AccessToken, AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import BaseModel


class LoginState(BaseModel):
    redirect_uri: str
    code_challenge: str
    redirect_uri_provided_explicitly: bool
    client_id: str
    resource: str | None = None
    expires_at: float


class OAuthStore(Protocol):
    def get_client(self, client_id: str) -> OAuthClientInformationFull | None: ...

    def save_client(self, client: OAuthClientInformationFull) -> None: ...

    def get_authorization_code(self, code: str) -> AuthorizationCode | None: ...

    def save_authorization_code(self, code: AuthorizationCode) -> None: ...

    def delete_authorization_code(self, code: str) -> None: ...

    def get_access_token(self, token: str) -> AccessToken | None: ...

    def save_access_token(self, token: AccessToken) -> None: ...

    def delete_access_token(self, token: str) -> None: ...

    def get_login_state(self, state: str) -> LoginState | None: ...

    def save_login_state(self, state: str, data: LoginState) -> None: ...

    def delete_login_state(self, state: str) -> None: ...


class InMemoryOAuthStore:
    """Non-persistent storage for local development and disposable servers."""

    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.authorization_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.login_states: dict[str, LoginState] = {}

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    def save_client(self, client: OAuthClientInformationFull) -> None:
        if not client.client_id:
            raise ValueError("No client_id provided")
        self.clients[client.client_id] = client

    def get_authorization_code(self, code: str) -> AuthorizationCode | None:
        authorization_code = self.authorization_codes.get(code)
        if authorization_code and authorization_code.expires_at < time.time():
            self.delete_authorization_code(code)
            return None
        return authorization_code

    def save_authorization_code(self, code: AuthorizationCode) -> None:
        self.authorization_codes[code.code] = code

    def delete_authorization_code(self, code: str) -> None:
        self.authorization_codes.pop(code, None)

    def get_access_token(self, token: str) -> AccessToken | None:
        access_token = self.access_tokens.get(token)
        if access_token and access_token.expires_at and access_token.expires_at < time.time():
            self.delete_access_token(token)
            return None
        return access_token

    def save_access_token(self, token: AccessToken) -> None:
        self.access_tokens[token.token] = token

    def delete_access_token(self, token: str) -> None:
        self.access_tokens.pop(token, None)

    def get_login_state(self, state: str) -> LoginState | None:
        login_state = self.login_states.get(state)
        if login_state and login_state.expires_at < time.time():
            self.delete_login_state(state)
            return None
        return login_state

    def save_login_state(self, state: str, data: LoginState) -> None:
        self.login_states[state] = data

    def delete_login_state(self, state: str) -> None:
        self.login_states.pop(state, None)


class SQLiteOAuthStore:
    """SQLite-backed OAuth state for one MCP process or container."""

    _TABLES = {
        "oauth_clients",
        "oauth_authorization_codes",
        "oauth_access_tokens",
        "oauth_login_states",
    }

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        parent = self.database_path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            file_descriptor = os.open(
                self.database_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(file_descriptor)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS oauth_access_tokens (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS oauth_login_states (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL
                );
                """
            )
        try:
            os.chmod(self.database_path, 0o600)
        except OSError:
            # Best effort on platforms whose filesystem does not expose POSIX modes.
            pass

    def _put(self, table: str, key: str, payload: str, expires_at: float | int | None = None) -> None:
        self._validate_table(table)
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM {table} WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),),
            )
            connection.execute(
                f"""INSERT INTO {table} (key, payload, expires_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        payload = excluded.payload,
                        expires_at = excluded.expires_at""",
                (key, payload, expires_at),
            )

    def _get(self, table: str, key: str) -> str | None:
        self._validate_table(table)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload, expires_at FROM {table} WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            payload, expires_at = row
            if expires_at is not None and expires_at < time.time():
                connection.execute(f"DELETE FROM {table} WHERE key = ?", (key,))
                return None
            return str(payload)

    def _delete(self, table: str, key: str) -> None:
        self._validate_table(table)
        with self._connect() as connection:
            connection.execute(f"DELETE FROM {table} WHERE key = ?", (key,))

    def _validate_table(self, table: str) -> None:
        if table not in self._TABLES:
            raise ValueError(f"Unknown OAuth storage table: {table}")

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        payload = self._get("oauth_clients", client_id)
        return OAuthClientInformationFull.model_validate_json(payload) if payload else None

    def save_client(self, client: OAuthClientInformationFull) -> None:
        if not client.client_id:
            raise ValueError("No client_id provided")
        self._put("oauth_clients", client.client_id, client.model_dump_json())

    def get_authorization_code(self, code: str) -> AuthorizationCode | None:
        payload = self._get("oauth_authorization_codes", code)
        return AuthorizationCode.model_validate_json(payload) if payload else None

    def save_authorization_code(self, code: AuthorizationCode) -> None:
        self._put("oauth_authorization_codes", code.code, code.model_dump_json(), code.expires_at)

    def delete_authorization_code(self, code: str) -> None:
        self._delete("oauth_authorization_codes", code)

    def get_access_token(self, token: str) -> AccessToken | None:
        payload = self._get("oauth_access_tokens", token)
        return AccessToken.model_validate_json(payload) if payload else None

    def save_access_token(self, token: AccessToken) -> None:
        self._put("oauth_access_tokens", token.token, token.model_dump_json(), token.expires_at)

    def delete_access_token(self, token: str) -> None:
        self._delete("oauth_access_tokens", token)

    def get_login_state(self, state: str) -> LoginState | None:
        payload = self._get("oauth_login_states", state)
        return LoginState.model_validate_json(payload) if payload else None

    def save_login_state(self, state: str, data: LoginState) -> None:
        self._put("oauth_login_states", state, data.model_dump_json(), data.expires_at)

    def delete_login_state(self, state: str) -> None:
        self._delete("oauth_login_states", state)
