# Security Notes

This server gives an AI app (Claude, ChatGPT, or any MCP client) read/write
access to **one** GitHub repository through a small set of narrow tools. It
works for a single user and for a team — a single user is just a one-member
roster. The security model is identical in both cases.

## Three layers to keep separate

```text
AI app   -> MCP server:  OAuth (this server is its own Authorization Server)
Login    -> member id:   the member registry (TEAM_MEMBER_IDS + MEMBER_<ID>_*)
MCP server -> GitHub:     that member's own fine-grained PAT (server-side only)
```

Do not conflate these. No GitHub PAT is ever sent to, stored by, or visible to
the AI app. A PAT is read only from the environment (in `registry.py`) and
attached to outbound GitHub requests (in `github_client.py`). It never appears in
a tool argument, a tool result, or an error surfaced to the app — a deliberately
wrong token produces a clean `"Server could not authenticate to GitHub…"`
message with the token value absent from the response.

## How the OAuth layer works

The server implements `mcp.server.auth.provider.OAuthAuthorizationServerProvider`
(see `auth.py`) and runs as its own complete OAuth Authorization Server with
Dynamic Client Registration. There is no external identity provider.

- `/register` — Dynamic Client Registration: an MCP client (e.g. a new Claude
  or ChatGPT device) registers itself on first connection. This only yields a
  client ID —
  it is **not** a path to a usable token; the login form still gates access.
- `/authorize` — redirects to `/login`, a server-rendered HTML form.
- `/login` + `/login/callback` — the only place credentials are checked.
  Submitted username/password are compared against the member registry with
  `secrets.compare_digest` (constant-time), iterating every member without
  short-circuiting so neither a wrong username nor a wrong password is faster
  to reject (no timing-based username enumeration).
- `/token` — exchanges the PKCE-verified authorization code for a bearer token.
- Access tokens live in an in-memory dict, expire after 30 days, and are never
  logged.

This is a real OAuth flow — the AI app performs the redirect/consent/exchange
itself — not a static token pasted into the UI. Static pasted tokens, custom
`X-API-Key` headers, query-string tokens, and secret URL paths are explicitly
not used.

## The member registry = identity + attribution

The registry is configured entirely from env vars (`TEAM_MEMBER_IDS` plus a
`MEMBER_<ID>_*` block per member) and loaded once, immutably, at startup. It is
the single source of truth for who may authenticate.

- On login, the OAuth token's `subject` is set to the authenticated **member
  id**. On every tool call, that subject is resolved back to a member and a
  `GitHubClient` built with **that member's PAT** — so every commit is authored
  by the GitHub account the PAT belongs to.
- **Single user:** `TEAM_MEMBER_IDS=you` with one `MEMBER_YOU_*` block. One
  login, one PAT, all commits authored as you.
- **Team:** one block per member, each with their own login and their own PAT.
  Attribution follows the token — whichever GitHub account a member's PAT
  belongs to is who their commits are authored as, so each
  `MEMBER_<ID>_GITHUB_TOKEN` must be pasted carefully.
- The registry **is** the identity allowlist: only configured member ids can
  ever be a valid OAuth subject. No separate allowlist env var is needed.
- At startup the server validates that every listed member has a complete
  `MEMBER_<ID>_*` block and refuses to start otherwise. (It does **not** verify
  each PAT's real GitHub owner — a wrong/revoked token surfaces as a normal
  GitHub `401` on first use.)

## Write scope

`github_write_file` can create or update any file anywhere in the repo,
committed **directly to `main`**:

- **No delete capability, ever.** No tool exposes delete — the write tool only
  ever issues a `PUT` (create/update) to the GitHub Contents API, never a
  `DELETE`. This is the main hard boundary on what a connected session can do.
- **Full-file replacement only** (no patch/diff mode). Optimistic concurrency:
  the agent passes the `sha` from `github_read_file`; a stale sha yields a `409`
  surfaced as a retryable error rather than a silent clobber.
- **100 KB size cap** on both reads and writes (`MAX_FILE_BYTES`). A larger file
  is refused with a clear error, never silently truncated.

Reads cover the whole repo too — read and write scope are the same. There is no
folder allowlist; blast radius is controlled by the single fixed repo plus the
no-delete rule.

## What's restricted, deliberately

- **One GitHub owner/repo only** (`GITHUB_ALLOWED_OWNER` / `GITHUB_ALLOWED_REPO`).
  No tool accepts an arbitrary owner, repo, or URL. This is the primary
  blast-radius control.
- **No raw GitHub API proxy tool** — every tool is a narrow, named operation.
- **The registry is the allowlist** — only configured member ids are valid
  OAuth subjects.
- **This server is reachable over the public internet**, so the OAuth login gate
  matters more than for a purely local tool: only someone who knows a member's
  username/password can complete `/login` and obtain a token. There is no
  anonymous or guest path to a valid token. Use long, random passwords — they,
  plus the no-delete rule, are the perimeter.

## Secrets handling

- No secret is ever printed, logged, or returned in a tool result or error.
- `.env` is gitignored; never commit real credentials.
- On a hosted platform, set each variable as an encrypted env var — do not
  upload a `.env` file. The server reads env only at startup, so redeploy after
  any change (PAT rotation, new member, etc.).
