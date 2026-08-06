# second-brain-github-mcp

> An AI assistant can discuss your plans. It cannot maintain the system where
> those plans live.

This closes that gap. It is a self-hosted MCP server that gives an AI app —
Claude, ChatGPT, or any MCP client with remote connectors — **read and write
access to exactly one GitHub repository**: your Markdown notes, typically an
Obsidian vault.

The assistant reads the current state of your notes, proposes a change, and
writes it back. Every change is an ordinary Git commit — reviewable, revertible,
and readable without any of the tools involved.

You run it yourself. There is no hosted service and no account: you deploy the
server, point it at your own repository, and connect your AI app to it.

Background article:
[Beyond Chat History: Building an AI-Native Second Brain with Obsidian, GitHub, and MCP](https://manuelblinkert.com/blog/beyond-chat-history-ai-native-second-brain)

## What it does

Once connected, you talk to your assistant normally and it works on the real
files:

- *"What did I decide about pricing last month?"* — it searches the vault and
  answers from your actual notes, not from chat history.
- *"Add today's outcome to my daily note."* — it reads the note, edits it, and
  commits the change.
- *"Restructure this project note to match my conventions."* — it reads the
  vault's rules file first, then applies them.

Afterwards you open Obsidian, or GitHub, and see exactly what changed.

## How it works

```text
AI client (Claude, ChatGPT, …)
  → OAuth login against this server
  → remote MCP server
  → member's fine-grained GitHub PAT (server-side only)
  → one Markdown vault repository
  → Git history
```

Access and behaviour are two separate layers. This server controls *what the
assistant is allowed to touch*. The vault's own root-level `CLAUDE.md` — plain
Markdown, versioned alongside the notes — defines *how it should behave*:
folder conventions, frontmatter contracts, what it must ask about before doing.
The `github_write_file` tool points the assistant at that file explicitly.

## Design constraints

Giving an AI app write access to your knowledge base is only reasonable if the
access is bounded. These are the boundaries:

- **One repo, hard-scoped.** Every tool is pinned to a single configured
  `owner/repo`. No tool accepts an arbitrary owner, repo, or URL.
- **Credentials never reach the model.** GitHub PATs are held server-side. The
  AI app sees explicit tools, not the token behind them.
- **No delete tool.** Files can be created and replaced. Nothing can be removed.
- **Writes are full-file, straight to `main`, capped at 100 KB.** No patch mode,
  no branch sprawl — and a `sha` precondition, so a write fails rather than
  clobbering a change made since the assistant last read the file.
- **Every change is a commit.** What changed, when, and what it replaced stays
  visible in Git history.
- **Own OAuth server.** The AI app authenticates through a real OAuth flow
  against a self-hosted login — no static tokens pasted into a connector UI.
- **Single user or team.** A member registry maps each login to that member's
  own fine-grained PAT, so commits are attributed to the right person. A single
  user is just a one-member roster.

Full auth and threat model:
[`docs/security-notes.md`](docs/security-notes.md).

## Tools

| Tool | Purpose |
|---|---|
| `github_list_tree` | Every note path, recursive — the primary way to find a note |
| `github_read_file` | Read one file; returns content plus the `sha` needed to edit it |
| `github_write_file` | Create or replace one file, committed to `main`. No delete |
| `github_list_directory` | Entries directly under one directory |
| `github_search_notes` | Full-text search (best-effort; GitHub's index lags recent commits) |
| `github_repo_info` | Default branch, visibility, URL of the connected repo |
| `github_list_issues` | Issues, if the vault repo uses them |
| `github_list_pull_requests` | Pull requests, if the vault repo uses them |
| `ping` | Connectivity check — returns the server name and tool mode |

## Run locally

```bash
cp .env.example .env
# edit .env — each variable is documented inline

uv sync
uv run python server.py
```

Then validate the OAuth gate and connect it to your AI app (Claude, ChatGPT, …)
following [`docs/connector-setup.md`](docs/connector-setup.md).

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

## Health check

Use `/health` for uptime monitors. It is intentionally unauthenticated and
returns HTTP 200 when the server process is running:

```text
https://<your-domain>/health
```

## Tests

```bash
uv run pytest
```

## Requirements

- **Python** 3.12+, managed with [`uv`](https://docs.astral.sh/uv/).
- Built on the official MCP Python SDK (`FastMCP`, Streamable HTTP transport).

## Author

Built by [Manuel Blinkert](https://manuelblinkert.com).

## License

[MIT](LICENSE)
