import os

from dotenv import load_dotenv

# Load a local .env (if present) so `uv run python server.py` works without
# manually exporting variables. On DigitalOcean there is no .env file; the
# platform's configured env vars are used and this call is a harmless no-op.
load_dotenv()

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from auth import MCP_SCOPE, TeamOAuthProvider
from github_tools import register_github_tools
from registry import load_registry_from_env

REQUIRED_ENV_VARS = (
    "MCP_PUBLIC_BASE_URL",
    "OAUTH_ISSUER_URL",
    "TEAM_MEMBER_IDS",
    "GITHUB_ALLOWED_OWNER",
    "GITHUB_ALLOWED_REPO",
)

missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

# Build the member registry (validates each MEMBER_<ID>_* block).
registry = load_registry_from_env()

public_base_url = os.environ["MCP_PUBLIC_BASE_URL"].rstrip("/")
issuer_url = os.environ["OAUTH_ISSUER_URL"]
login_path = f"{public_base_url}/login"
server_name = os.environ.get("MCP_SERVER_NAME", "second-brain-github-mcp")
github_target = f"{os.environ['GITHUB_ALLOWED_OWNER']}/{os.environ['GITHUB_ALLOWED_REPO']}"

oauth_provider = TeamOAuthProvider(
    server_url=public_base_url,
    login_path=login_path,
    registry=registry,
    login_label=github_target,
)

mcp = FastMCP(
    server_name,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    stateless_http=True,
    json_response=True,
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(issuer_url),
        resource_server_url=AnyHttpUrl(f"{public_base_url}/mcp"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[MCP_SCOPE],
            default_scopes=[MCP_SCOPE],
        ),
        required_scopes=[MCP_SCOPE],
    ),
)


@mcp.custom_route("/login", methods=["GET"])
async def login_page(request: Request) -> Response:
    state = request.query_params.get("state")
    if not state:
        raise HTTPException(400, "Missing state parameter")
    return await oauth_provider.get_login_page(state)


@mcp.custom_route("/login/callback", methods=["POST"])
async def login_callback(request: Request) -> Response:
    return await oauth_provider.handle_login_callback(request)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> Response:
    return JSONResponse({"status": "ok", "server": server_name})


@mcp.tool()
def ping() -> dict[str, str]:
    return {
        "status": "ok",
        "server": server_name,
        "github_tools": "read-write (per-member, repo-scoped)",
    }


register_github_tools(mcp, registry)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
