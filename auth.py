"""Self-hosted OAuth Authorization Server for this MCP server.

This protects Claude -> MCP server access only. It has nothing to do with
GitHub. GitHub access uses a separate per-member server-side PAT (looked up
via the team member registry) that is never exposed through this OAuth layer
or sent to Claude.

Adapted from the MCP Python SDK reference example
(examples/servers/simple-auth/mcp_simple_auth/simple_auth_provider.py),
swapping demo-hardcoded credentials for registry-backed multi-member
credentials, so any team member can authenticate and have their OAuth
subject stamped with their member id.
"""

import secrets
import time

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from pydantic import AnyHttpUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from oauth_store import InMemoryOAuthStore, LoginState, OAuthStore
from registry import MemberRegistry

MCP_SCOPE = "mcp"
ACCESS_TOKEN_TTL_SECONDS = 2592000  # 30 days
AUTH_CODE_TTL_SECONDS = 300
LOGIN_STATE_TTL_SECONDS = 600


class TeamOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Registry-backed, multi-member OAuth Authorization Server.

    Gates a self-rendered /login form against the team member registry, so
    any registered member can authenticate, then issues short-lived bearer
    tokens whose subject is the authenticated member's id. On every token
    load, the subject is re-checked against the registry membership, as a
    second gate independent of the login form.
    """

    def __init__(
        self,
        server_url: str,
        login_path: str,
        registry: "MemberRegistry",
        login_label: str = "your vault",
        store: OAuthStore | None = None,
    ) -> None:
        self.server_url = server_url
        self.login_path = login_path
        self.registry = registry
        # Human-facing heading on the login page — the repo this server is
        # connected to (e.g. "owner/repo"), so a member sees what they are
        # signing in to access.
        self.login_label = login_label
        self.store = store or InMemoryOAuthStore()

    # -- Dynamic Client Registration --------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("No client_id provided")
        self.store.save_client(client_info)

    # -- Authorization code flow -------------------------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state = params.state or secrets.token_hex(16)
        if not client.client_id:
            raise ValueError("No client_id provided")
        self.store.save_login_state(
            state,
            LoginState(
                redirect_uri=str(params.redirect_uri),
                code_challenge=params.code_challenge,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                client_id=client.client_id,
                resource=params.resource,
                expires_at=time.time() + LOGIN_STATE_TTL_SECONDS,
            ),
        )
        return f"{self.login_path}?state={state}&client_id={client.client_id}"

    async def get_login_page(self, state: str) -> HTMLResponse:
        if not state:
            raise HTTPException(400, "Missing state parameter")
        if self.store.get_login_state(state) is None:
            raise HTTPException(400, "Invalid or expired state parameter")

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Sign in</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 420px; margin: 60px auto; padding: 20px; }}
                .form-group {{ margin-bottom: 15px; }}
                input {{ width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box; }}
                button {{ background-color: #4CAF50; color: white; padding: 10px 15px; border: none; cursor: pointer; width: 100%; }}
            </style>
        </head>
        <body>
            <h2>Sign in to {self.login_label}</h2>
            <form action="{self.login_path}/callback" method="post">
                <input type="hidden" name="state" value="{state}">
                <div class="form-group">
                    <label>Username</label>
                    <input type="text" name="username" autocomplete="username" required>
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input type="password" name="password" autocomplete="current-password" required>
                </div>
                <button type="submit">Sign In</button>
            </form>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    async def handle_login_callback(self, request: Request) -> Response:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        state = form.get("state")

        if not isinstance(username, str) or not isinstance(password, str) or not isinstance(state, str):
            raise HTTPException(400, "Missing or invalid username, password, or state parameter")

        redirect_uri = await self._complete_login(username, password, state)
        return RedirectResponse(url=redirect_uri, status_code=302)

    async def _complete_login(self, username: str, password: str, state: str) -> str:
        state_data = self.store.get_login_state(state)
        if not state_data:
            raise HTTPException(400, "Invalid state parameter")

        redirect_uri = state_data.redirect_uri
        code_challenge = state_data.code_challenge
        redirect_uri_provided_explicitly = state_data.redirect_uri_provided_explicitly
        client_id = state_data.client_id
        resource = state_data.resource

        member = self.registry.authenticate(username, password)
        if member is None:
            raise HTTPException(401, "Invalid credentials")

        new_code = f"mcp_{secrets.token_hex(16)}"
        self.store.save_authorization_code(
            AuthorizationCode(
                code=new_code,
                client_id=client_id,
                redirect_uri=AnyHttpUrl(redirect_uri),
                redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
                expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
                scopes=[MCP_SCOPE],
                code_challenge=code_challenge,
                resource=resource,
                subject=member.member_id,
            )
        )

        self.store.delete_login_state(state)
        return construct_redirect_uri(redirect_uri, code=new_code, state=state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.store.get_authorization_code(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self.store.get_authorization_code(authorization_code.code) is None:
            raise ValueError("Invalid authorization code")
        if not client.client_id:
            raise ValueError("No client_id provided")

        token = f"mcp_{secrets.token_hex(32)}"
        self.store.save_access_token(
            AccessToken(
                token=token,
                client_id=client.client_id,
                scopes=authorization_code.scopes,
                expires_at=int(time.time()) + ACCESS_TOKEN_TTL_SECONDS,
                resource=authorization_code.resource,
                subject=authorization_code.subject,
            )
        )

        self.store.delete_authorization_code(authorization_code.code)

        return OAuthToken(
            access_token=token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(authorization_code.scopes),
        )

    # -- Token lifecycle -----------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self.store.get_access_token(token)
        if not access_token:
            return None

        if access_token.expires_at and access_token.expires_at < time.time():
            self.store.delete_access_token(token)
            return None

        if access_token.subject is None or self.registry.by_id(access_token.subject) is None:
            return None

        return access_token

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        raise NotImplementedError("Refresh tokens are not supported")

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self.store.delete_access_token(token.token)
