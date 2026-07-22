# Connector Setup

How to configure, validate, and connect the server — for a single user or a
team. A single user is just a one-member roster; the steps are the same, you
only fill in one `MEMBER_*` block instead of several.

This is a standard remote MCP server with OAuth, so it works with **any MCP
client that supports remote connectors** — Claude (web, desktop, mobile),
ChatGPT, and other MCP-capable apps. The client-specific step is only *where*
you paste the server URL; everything else (the OAuth login, the tools) is the
same. Claude is used as the worked example below; for other apps, follow their
own "add a remote MCP server / connector" flow with the same URL.

## 1. Configure

Copy the example env file and fill in real values:

```bash
cp .env.example .env
# edit .env — every variable is documented inline
```

`.env` is gitignored and loaded automatically on startup (no `export` needed).

**Roster.** `TEAM_MEMBER_IDS` is a comma-separated list of member ids. Each id
needs a matching `MEMBER_<ID>_*` block (uppercased id as the prefix) with a
`USERNAME`, a `PASSWORD`, and that member's own fine-grained `GITHUB_TOKEN`.

- **Single user:** `TEAM_MEMBER_IDS=you` + one `MEMBER_YOU_*` block.
- **Team:** one id per member + one block each.

**GitHub target.** `GITHUB_ALLOWED_OWNER` / `GITHUB_ALLOWED_REPO` pin the one
repo everyone reads/writes (also shown on the login page). Each member's PAT
must be a fine-grained token scoped to **only** that repo with **Contents: Read
and write**.

## 2. Local validation (do this first)

```bash
uv sync
uv run python server.py
```

The server validates that every listed member has a complete `MEMBER_<ID>_*`
block and refuses to start otherwise. Once it starts, confirm the gate is up:

```bash
curl -i http://localhost:8000/mcp
# expect: 401 + WWW-Authenticate: Bearer ... resource_metadata=".../.well-known/oauth-protected-resource/mcp"
```

Then run MCP Inspector against `http://localhost:8000/mcp`:

```bash
npx -y @modelcontextprotocol/inspector
```

Inspector should discover the OAuth metadata, register itself (DCR), redirect
you to `/login` in a browser tab, prompt for a member's username/password, then
let you call `ping` and see a result like:

```json
{ "status": "ok", "server": "<your-server-name>", "github_tools": "read-write (per-member, repo-scoped)" }
```

## 3. Deploy (only after step 2 passes)

The app deploys as a Docker image built from the repo's `Dockerfile`
(`python:3.12-slim` → install `uv` → `uv sync` → `uv run python server.py`);
`.dockerignore` keeps `.git`, `.venv`, and any local `.env` out of the build
context. The container listens on `$PORT` (falls back to `8000`) on `0.0.0.0`.
This runs on any Docker host — e.g. DigitalOcean App Platform, or a plain VPS
via `docker compose`.

1. Point your platform at the repo; it auto-detects the `Dockerfile`.
2. Add every variable from `.env.example` as a platform env var with real
   values (mark each `PASSWORD` and `GITHUB_TOKEN` **encrypted**). Do **not**
   upload a `.env` file.
3. Set `MCP_PUBLIC_BASE_URL` and `OAUTH_ISSUER_URL` to the real public URL once
   known (e.g. `https://<your-app>`). A wrong/placeholder URL breaks OAuth
   discovery — confirm the actual assigned URL first (some platforms add a
   random suffix).
4. Re-run the step-2 checks against the deployed `/mcp` endpoint with Inspector
   before connecting an AI app.
5. Redeploy after any env-var change (PAT rotation, new member, URL) — env is
   read only at startup.

## 4. Add the connector in your AI app

Once the deployed endpoint passes Inspector, add it as a remote MCP connector.
In **Claude**, this looks like:

1. Open connector settings and add a remote MCP connector pointing at
   `https://<your-app>/mcp`. Leave OAuth Client ID/Secret blank — DCR registers
   the client automatically.
2. The app discovers the OAuth metadata and redirects you to the `/login` form.
   Sign in with your (or the member's) username/password.
3. In the chat, open **Tools access** and enable this connector for the chat.
   If you skip this, the app may claim it can't reach the tool, or write code /
   an artifact instead of calling it.
4. Prompt the app directly to exercise the tools, e.g.:

   ```text
   Do not create an artifact. Do not write code.
   Use the github_list_tree tool, then read one note with github_read_file.
   Return only the raw JSON results.
   ```

   Expect the vault's paths from `github_list_tree`, and content + `sha` from
   `github_read_file`.

**Other clients (ChatGPT, etc.):** use their "add remote MCP server / connector"
flow with the same `https://<your-app>/mcp` URL. You'll be sent through the same
OAuth login, and the same tools appear.

**Mobile:** same connector, same OAuth login. This is the on-the-go case
(read/edit the vault from a phone) and works identically.

## 5. Validate writes (and, for teams, attribution)

Have each member, from their own app, write a dated test note (e.g. under an
`_inbox/` or daily-notes path), reading it first with `github_read_file` for its
`sha`, then writing back with `github_write_file`. Expect a result with a
`commit_sha` / `html_url`.

- **Team:** confirm on GitHub that each commit is authored by **that member's**
  GitHub account — this verifies per-member attribution end to end.
- **Delete guardrail:** there is no delete tool; `github_write_file` can only
  create or update. Writes go straight to `main`.

## If OAuth doesn't work

Stop and note exactly which step failed — discovery, registration, login
redirect, token exchange, or the authenticated call. That pinpoints the fix
(usually a mismatched `MCP_PUBLIC_BASE_URL` / `OAUTH_ISSUER_URL` vs. the real
public URL).
