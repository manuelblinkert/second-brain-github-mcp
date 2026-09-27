# OAuth State Storage

The server's OAuth authorization layer maintains four kinds of state:

- registered clients created through Dynamic Client Registration (DCR);
- access tokens presented to the MCP endpoint;
- short-lived authorization codes;
- short-lived login state, including the redirect URI and PKCE challenge.

This state is separate from the GitHub PATs configured for members. GitHub PATs
remain environment variables and are never written to the OAuth database.

## Available backends

| Backend | Intended use | Status |
|---|---|---|
| In memory | Local experiments and disposable deployments | Built in; state is lost on restart |
| SQLite on a persistent volume | One MCP process or container | Implemented and recommended for a single VPS |
| PostgreSQL | Multiple workers or replicas | Extension option; not implemented |
| Redis | Shared short-lived codes and login state | Extension option; not implemented; persistence must be configured if used for durable records |
| PostgreSQL + Redis | Larger multi-instance deployments | Extension option; not implemented |

The storage layer is deliberately isolated from the OAuth provider so another
backend can be added without changing the login screen or MCP connector flow.

## SQLite configuration

Set `OAUTH_DB_PATH` to a path whose parent directory is mounted from the host:

```env
OAUTH_DB_PATH=/data/oauth.sqlite3
```

The repository includes `docker-compose.example.yml`. Copy it for a deployment
that builds directly from the repository root:

```bash
cp docker-compose.example.yml docker-compose.yml
```

The example mounts `./.oauth-data` on the host at `/data` in the container. Set
`OAUTH_DB_PATH=/data/oauth.sqlite3` in `.env`, then create the host directory
with restrictive permissions before starting the container:

```bash
mkdir -p .oauth-data
chmod 700 .oauth-data
```

If the Compose file lives one directory above the checked-out repository, set
its build context to `./repo` and choose a sibling host directory such as
`./data:/data`. The container path and `OAUTH_DB_PATH` stay unchanged.

The server creates the database and its tables automatically. Use a separate
database file and volume for every deployed MCP instance. Do not share one
SQLite file between different vaults or multiple running server processes.

If `OAUTH_DB_PATH` is omitted, the server remains usable with its in-memory
store and prints a startup warning. Registered clients and issued tokens will
then be lost on every process or container restart.

## Security and operations

The SQLite database contains OAuth client secrets and bearer tokens. Treat it
like any other credential store:

- keep it outside the repository and container image;
- restrict access to the service account;
- do not print its contents in logs or support output;
- protect backups with the same care as the live database;
- delete the database only when intentionally revoking every connected client.

SQLite persistence does not change token lifetimes or the login UI. The server
currently issues 30-day access tokens and does not issue refresh tokens. Adding
refresh-token rotation is a separate enhancement to reduce future password
prompts after an access token expires.
