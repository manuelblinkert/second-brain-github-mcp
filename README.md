# second-brain-github-mcp

A small, authenticated remote MCP server that lets an AI app — Claude, ChatGPT,
or any MCP client with remote connectors — safely **read and write one GitHub
repository**, built for a second-brain / Obsidian vault stored on GitHub.

- **One repo, hard-scoped.** Every tool is pinned to a single configured
  `owner/repo`; no tool accepts an arbitrary owner, repo, or URL.
- **Own OAuth server.** The AI app authenticates via a real OAuth flow against a
  self-hosted login — no static tokens pasted into the UI.
- **Single user or team.** A member registry maps each login to that member's
  own fine-grained GitHub PAT, so commits are attributed to the right person. A
  single user is just a one-member roster.
- **Safe by construction.** GitHub PATs are server-side only (never sent to the
  AI app); writes go directly to `main`, full-file only, with **no delete** and
  a 100 KB size cap.

Tools: `github_repo_info`, `github_list_tree`, `github_read_file`,
`github_list_directory`, `github_search_notes`, `github_list_issues`,
`github_list_pull_requests`, and `github_write_file`.

## Run locally

```bash
cp .env.example .env
# edit .env — each variable is documented inline

uv sync
uv run python server.py
```

Then validate the OAuth gate and connect it to your AI app (Claude, ChatGPT, …)
following [`docs/connector-setup.md`](docs/connector-setup.md). The auth and
security model is described in [`docs/security-notes.md`](docs/security-notes.md).

## Health check

Use `/health` for uptime monitors. It is intentionally unauthenticated and
returns HTTP 200 when the server process is running:

```text
https://<your-domain>/health
```

## Configure

All configuration is via environment variables (see `.env.example`):

- `MCP_PUBLIC_BASE_URL` / `OAUTH_ISSUER_URL` — the server's own public URL.
- `TEAM_MEMBER_IDS` + a `MEMBER_<ID>_*` block per member — the roster. One entry
  for single-user, several for a team.
- `GITHUB_ALLOWED_OWNER` / `GITHUB_ALLOWED_REPO` — the one repo everyone reads
  and writes.
- `MCP_SERVER_NAME` (optional) — the name this instance reports (login page,
  `ping`). Defaults to `second-brain-github-mcp`.

## Deploy

The app builds from the included `Dockerfile` and runs on any Docker host
(e.g. DigitalOcean App Platform, or a VPS via `docker compose`). Set every
variable from `.env.example` as an encrypted platform env var — never upload a
`.env` file — and set the public-URL vars to the real assigned URL. The server
reads env only at startup, so redeploy after any change (PAT rotation, new
member, URL). Full steps are in the connector-setup doc.

## Tests

```bash
uv run pytest
```

## Requirements

- **Python** 3.12+, managed with [`uv`](https://docs.astral.sh/uv/).
- Built on the official MCP Python SDK (`FastMCP`, Streamable HTTP transport).

## License

[MIT](LICENSE)
