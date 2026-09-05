# obsidian-mcp

[![CI](https://github.com/ykoellmann/obsidian-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ykoellmann/obsidian-mcp/actions/workflows/ci.yml)

An MCP (Model Context Protocol) server for [Obsidian](https://obsidian.md) vaults. Connects Claude (or any MCP client) directly to your vault — read, write, search, navigate links, and manage notes, canvases, and kanban boards.

## Why obsidian-mcp?

The official Obsidian MCP plugin requires the Obsidian desktop app to be running and only works on the same machine. **obsidian-mcp is a standalone server** — no Obsidian app needed.

The intended setup is to host obsidian-mcp on a server or NAS where your vault is continuously synced (via [Syncthing](https://syncthing.net), [git](https://github.com/denolehov/obsidian-git), [rclone](https://rclone.org), or [Obsidian Sync](https://obsidian.md/sync)). Claude then connects to that server over the network, so:

- **Always up to date** — the server sees every change your Obsidian app writes, immediately
- **Access from anywhere** — connect from Claude Desktop, Claude Code, or any MCP client on any machine, without the vault being present locally
- **Multiple clients** — several Claude sessions can read the vault simultaneously; writes are serialized with per-file locking
- **No app dependency** — the server runs headless and starts automatically (systemd, Docker, etc.)

```
[Obsidian app]  ──sync──►  [vault on server]  ◄──MCP──  [Claude on any machine]
  (phone/laptop)              (NAS / VPS)                  (Claude Desktop / Code)
```

## Features

- Raw Markdown reads, section outlines and bounded batch reads.
- Paginated file/attachment discovery and literal AND search with typed YAML filters.
- Create-only writes, revision-required full replacements, exact patches and literal appends.
- Targeted frontmatter updates, backlinks, tasks and create-only attachment uploads.
- Read/write path policies, audit logging, multi-vault identity isolation and API-key/GitHub authentication.
- Optional Canvas, Excalidraw, Kanban and Bases tool groups.
- Daily-note and weekly-review prompts guided by user-authored vault conventions.

This branch is a clean MCP interface break: **17 base tools, no profiles or legacy
aliases**. Reconnect clients to refresh discovery. See the [interface contract](docs/mcp-surface-harmonization.md).

## Installation

**Via uvx (no clone needed):**
```bash
VAULT_PATH=/your/vault uvx obsidian-remote-mcp
```

**Via Docker (no Python needed):**
```bash
docker compose up -d   # see docker-compose.yml
```

**From source:**
```bash
git clone https://github.com/ykoellmann/obsidian-mcp.git
cd obsidian-mcp
uv sync
uv run obsidian-remote-mcp
```

## Configuration

Copy `.env.example` to `.env` and set your vault path:

```env
VAULT_PATH=/path/to/your/obsidian/vault
# Optional:
# READ_ONLY=true            # safe default for network Compose deployments
# READ_PATHS=Notes/,Inbox/  # restrict all reads to specific rooted scopes
# WRITE_PATHS=Notes/,Inbox/ # restrict writes to specific folders
# DENY_READ_PATHS=.obsidian/,.trash/ # security boundary for all reads
# DENY_WRITE_PATHS=.obsidian/,.trash/,_AI_INSTRUCTIONS.md
# ALLOW_PERMANENT_DELETE=false
# REQUIRE_WRITE_PRECONDITIONS=true # strict replacements for optional format tools
# INDEX_RECONCILE_INTERVAL=900     # full Markdown hash sweep every 15 minutes
# TRANSPORT=stdio           # stdio (default), http (recommended for network use), or sse (legacy)
```

Slash-suffixed policy entries such as `Notes/` cover that directory and its
descendants. An entry without a trailing slash grants or denies only the exact
path. For compatibility, native configuration still permits unrestricted
writes when both `READ_ONLY=false` and `WRITE_PATHS` is empty; choose that
combination explicitly, not as a network default. Use a case-sensitive
filesystem for authoritative path scopes.

> **Docker upgrade note:** this Compose configuration now defaults
> `READ_ONLY=true`, while a native `Config` invocation retains its historical
> `false` default. Existing Docker deployments that intentionally write must
> explicitly set `READ_ONLY=false`; pair it with a narrow `WRITE_PATHS` value.
> For a nested scope such as `deep/nested/`, create `deep/` beforehand. MCP may
> create the configured `nested/` scope and descendants, but never ancestors
> above the configured write boundary.

`READ_PATHS` is an optional allowlist using the same rooted exact/recursive
syntax. When set, direct reads, listings, search, index construction, and
resources are limited to those scopes; `DENY_READ_PATHS` still takes
precedence. Ancestor directories may be traversed only to reach an allowed
scope and do not expose sibling content.

`EXCLUDE_PATHS` uses those same rooted exact/recursive matching rules, but is
only a discovery filter—not an access-control boundary. For example,
`private/` hides that root directory and its descendants, while it does not
hide `Projects/private/`.

Note mutation tools require read access to validate revisions and derive
incremental edits. They
therefore reject paths covered by `DENY_READ_PATHS` even if `WRITE_PATHS` also
contains the path. Avoid overlapping those scopes for note workflows. The
storage policy can still support intentionally write-only capabilities that do
not inspect existing content, but the canonical note tools are not among them.

The best-effort JSONL audit log is application state, not vault content. It
defaults beneath `LOCK_PATH` for native runs and to `/data/audit.jsonl` in
Docker. `AUDIT_LOG_PATH` must remain outside `VAULT_PATH`; the final log file
is opened without following symlinks. In multi-vault mode it must remain
outside every vault root configured in `vaults.json`; startup validation
rejects any overlap.

New files and directories use the normal `0666`/`0777` creation modes filtered
by the MCP process umask. Atomic overwrites preserve the existing file's
permission bits. In a shared sync deployment, run the MCP and sync daemon with
compatible UID/GID and umask settings so both can continue reading and updating
new notes; the home-server profile exposes these as `PUID` and `PGID`.

Direct note reads return an opaque `sha256:...` revision. Pass it as
`expectedRevision` when replacing or appending to an existing note so an edit
landed by Obsidian Sync during the client's think time is reported as a conflict
instead of silently overwritten. Canonical whole-file replacement always requires the read revision. Incremental patch/frontmatter tools
always protect the exact version they read internally. This is optimistic
concurrency, not exactly-once execution: after a lost append response, re-read
and verify the result before retrying without the old revision.

Watcher events are debounced, and the index additionally hashes readable,
indexable Markdown every 15 minutes by default to repair missed events. PDFs,
images, other attachments, excluded paths, and Excalidraw files are not hashed.
See [the design note](docs/implementation/phase-3-sync-concurrency.md) for the
scope, measured cost, health fields, and remaining final-rename race.

Full list of variables — including `API_KEY`, `PUBLIC_BASE_URL`, and the
`OAUTH_GITHUB_*` variables for the optional second auth variant — is
documented with inline comments in `.env.example`; see [Remote Setup](#remote-setup-recommended)
for the two auth variants in detail.

### Optional format tools

Set `ENABLE_CANVAS`, `ENABLE_EXCALIDRAW`, `ENABLE_KANBAN` or `ENABLE_BASES` to `true`
to register the corresponding format group. All default to false. There is no
focused/full selection. Delete, move, folder and bulk-edit MCP tools are not exposed.

## Usage with Claude Code

Add to your MCP config (e.g. `~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "uv",
      "args": ["--directory", "/path/to/obsidian-mcp", "run", "obsidian-remote-mcp"],
      "env": {
        "VAULT_PATH": "/path/to/your/obsidian/vault"
      }
    }
  }
}
```

## Usage with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "uv",
      "args": ["--directory", "/path/to/obsidian-mcp", "run", "obsidian-remote-mcp"],
      "env": {
        "VAULT_PATH": "/path/to/your/obsidian/vault"
      }
    }
  }
}
```

## Docker

```bash
# 1. Copy and edit the environment variables
cp .env.example .env

# 2. Set HOST_VAULT_PATH and API_KEY in .env, then:
docker compose up -d
```

The `docker-compose.yml` pulls the pre-built image from GHCR — no cloning or building required. To build locally instead, swap `image:` for `build: .` in the compose file.

GitHub OAuth state is stored under `/data/fastmcp` by default, inside the
Compose `mcp-data` volume, so logins survive container restarts.

The image has a built-in `HEALTHCHECK` against `GET /health` (unauthenticated,
with no vault content or filesystem paths). It reports index readiness plus the
last reconciliation time, duration, and error, and is visible in
`docker ps`/`docker compose ps`. Only meaningful for `TRANSPORT=http`/`sse`; a
no-op for `stdio`.

### Hardened home-server Compose profile

For a home server where Cloudflare Tunnel is the only network entry point,
use [`docker-compose.home-server.yml`](docker-compose.home-server.yml). It
builds the MCP image from the checked-out source, bind-mounts the complete
vault read-only, overlays only the two configured AI memory/output directories
read-write, sets matching `WRITE_PATHS`, runs as the configured non-root
UID/GID, and publishes no host port. The MCP service is only on an internal
network; `cloudflared` has that network plus a separate normal egress network
so it can reach Cloudflare without making MCP externally reachable.

Create the nested writable directories before starting it, set
`HOST_VAULT_PATH`, `AI_MEMORY_PATH`, `AI_OUTPUT_PATH`, `PUID`, `PGID`,
`MCP_DATA_PATH`, `API_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, and a digest-pinned
`CLOUDFLARED_IMAGE` (for example,
`cloudflare/cloudflared@sha256:<digest>`) in `.env`. Create the data directory
and make it owned by `PUID:PGID`; it stores `/data/locks` and application state.
Cloudflare Access Managed OAuth authenticates the edge, but it does not inject
this application's bearer API key. Clients must still send `API_KEY` to the
origin; trusted-proxy header injection is a future phase, not part of this
profile. Then run:

```bash
docker compose -f docker-compose.home-server.yml up -d
```

Folder/note trash, restore and move tools are not exposed by this interface.

The static Compose checks are covered by the test suite. A real deployment
test (Docker mount precedence, host UID/GID permissions, and the Cloudflare
Tunnel route) remains environment-specific and must be run on the target
server before relying on it. To update Cloudflared, choose a reviewed release,
resolve its immutable `RepoDigest`, update `CLOUDFLARED_IMAGE`, then recreate
the sidecar; rebuild the MCP service after source changes with
`docker compose -f docker-compose.home-server.yml build --pull`.

## Remote Setup (Recommended)

Run obsidian-mcp on a server and connect to it remotely. Network transports
(`sse`/`streamable-http`) need at least one of the two auth variants below —
**API key** and **GitHub OAuth** are independent and can be used at the same
time: keep the API key for Claude Code/Desktop/curl while adding OAuth only
for claude.ai, or use either one alone.

> **Security:** Always put a TLS-terminating reverse proxy (e.g. [Caddy](https://caddyserver.com)) in front when exposing to the internet — required for GitHub OAuth callbacks in particular, since GitHub rejects plain `http://` callback URLs except on `localhost`. `API_KEY`/OAuth are not needed for stdio transport (local use only).

### Option A: API key (Claude Code, Claude Desktop, curl, mcp-remote)

**1. Generate an API key:**
```bash
openssl rand -hex 32
```

**2. Configure the server** (`docker-compose.yml` or `.env`):
```env
VAULT_PATH=/data/vault
TRANSPORT=http
API_KEY=your-generated-key
```
(`sse` also works here, but see the note under Option B — `http` is the
more robust choice and works identically for this bearer-key setup.)

**3. Start:**
```bash
docker compose up -d   # or: uv run obsidian-remote-mcp
```

**4. Connect from your MCP client** (anywhere on the network):
```json
{
  "mcpServers": {
    "obsidian": {
      "type": "http",
      "url": "https://your-server/mcp",
      "headers": {
        "Authorization": "Bearer your-generated-key"
      }
    }
  }
}
```

### Option B: GitHub OAuth (claude.ai Web/Mobile Custom Connector)

claude.ai's "Custom Connector" UI only has fields for OAuth (Authorization
URL, Token URL, Client ID/Secret) — no field for a bearer token or custom
header. obsidian-mcp handles the whole OAuth protocol for you (discovery
endpoints, PKCE, token exchange); you only ever hand claude.ai your server's
URL.

**1. Create a GitHub OAuth App:** GitHub → Settings → Developer settings →
[OAuth Apps](https://github.com/settings/developers) → New OAuth App.
- Homepage URL: your server's public URL (e.g. `https://obsidian.example.com`)
- Authorization callback URL: the same URL + `/auth/callback`
  (e.g. `https://obsidian.example.com/auth/callback`)

Copy the generated **Client ID** and **Client Secret**.

**2. Configure the server** (`docker-compose.yml` or `.env`):
```env
VAULT_PATH=/data/vault
TRANSPORT=http
PUBLIC_BASE_URL=https://obsidian.example.com   # must match the GitHub callback host
OAUTH_GITHUB_CLIENT_ID=your-client-id
OAUTH_GITHUB_CLIENT_SECRET=your-client-secret
OAUTH_GITHUB_ALLOWED_LOGINS=your-github-username # comma-separated; required, no "allow anyone" fallback
```
`OAUTH_GITHUB_ALLOWED_LOGINS` is enforced at login: only the listed GitHub
accounts can authenticate, everyone else is rejected, even with a valid
GitHub account.

> **Use `TRANSPORT=http`, not `sse`.** `sse` caused OAuth authorization
> errors with claude.ai specifically (token issued fine server-side, but
> claude.ai never followed up with a request) — `http` fixed it.

**3. Start:**
```bash
docker compose up -d   # or: uv run obsidian-remote-mcp
```

**4. Connect from claude.ai:** Settings → Connectors → Add Custom Connector,
and enter just the server URL (`https://obsidian.example.com/mcp`). claude.ai
discovers everything else (`/.well-known/oauth-authorization-server`, PKCE,
etc.) automatically and redirects you to GitHub to log in on first connect.

> **Persistence in Docker:** OAuth client registrations and tokens are stored
> under FastMCP's own data directory, which is *not* inside the vault volume
> by default. Without a persistent mount there, every container restart logs
> claude.ai out and forces re-authentication. Set `FASTMCP_HOME` to a mounted
> path (see `docker-compose.yml`) to avoid that.

Keep the vault synced on the server with Syncthing, git+cron, rclone, or
Obsidian Sync. The file watcher picks up normal changes, while periodic
Markdown reconciliation repairs missed watcher events.

## Multi-Vault Setup

By default obsidian-mcp serves one vault (`VAULT_PATH`). If you need several
completely separate vaults from one deployment — e.g. a private vault and a
work vault, each only reachable by its own identity — set `VAULTS_CONFIG` to
the path of a JSON file instead. Vault path-policy settings (`VAULT_PATH`,
`READ_PATHS`, `WRITE_PATHS`, `DENY_READ_PATHS`, `DENY_WRITE_PATHS`, and
`EXCLUDE_PATHS`) and identity settings (`API_KEY` and
`OAUTH_GITHUB_ALLOWED_LOGINS`) then come from that file. See
[`vaults.json.example`](vaults.json.example):

```json
{
  "vaults": {
    "private": {"path": "/vaults/private", "exclude_paths": ["private/", ".obsidian/", ".trash/"]},
    "monari":  {"path": "/vaults/monari",  "write_paths": ["02-Areas/monari/"]}
  },
  "identities": [
    {"type": "api_key",      "value": "sk-...",              "vaults": ["private"]},
    {"type": "github_login", "value": "your-github-username", "vaults": ["private", "monari"], "default": "private"}
  ]
}
```

Each vault entry can set `read_paths`, `write_paths`, `deny_read_paths`,
`deny_write_paths`, `exclude_paths`, and `read_only` independently. Path rules
are rooted: a trailing slash includes descendants, while a rule without one
matches only that exact path. `exclude_paths` controls discovery/indexing;
the read/write/deny fields are the access-control boundary.

```env
VAULT_PATH=              # unused — vaults.json defines paths instead
VAULTS_CONFIG=/data/vaults.json
TRANSPORT=http
```

Multi-vault mode requires an authenticated network transport (`http`, `sse`,
or `streamable-http`). It cannot be used with `stdio`, because stdio has no
authenticated request identity to map to a vault.

Each `identities` entry is either an **API key** (`Authorization: Bearer
<value>`, same as [Option A](#option-a-api-key-claude-code-claude-desktop-curl-mcp-remote)
above) or a **GitHub login** (same allowlist mechanism as
[Option B](#option-b-github-oauth-claudeai-webmobile-custom-connector) — set
`OAUTH_GITHUB_CLIENT_ID`/`SECRET`/`PUBLIC_BASE_URL` as usual, just skip
`OAUTH_GITHUB_ALLOWED_LOGINS` since `vaults.json` replaces it). Both kinds
can be mixed and used at the same time, exactly like today. Whichever
identity a request authenticates as, every tool call is transparently
scoped to that identity's vault(s) — there is no way to reach a vault an
identity isn't listed for.

An identity with more than one entry in `"vaults"` can switch between them:
every tool accepts an optional `vault=<name>` argument for that one call. If
omitted, it uses the identity's configured `"default"`; an identity with
several vaults and no default must pass `vault=` explicitly.
`list_vaults()` returns `{vaults: [{name, description, is_default}]}`
for whichever identity is calling, so an MCP client can discover what it's
allowed to pass — the built-in instructions tell Claude to call it first
and pass `vault=` when the conversation clearly points at a non-default
vault. There's no server-side memory of which vault was picked last; it's
re-selected on every call, same as any other argument.

`/attachments/*` (the direct binary upload/download route) is fully
multi-vault-aware: a plain `Authorization: Bearer` request resolves to that
identity's default vault, or pass `?vault=<name>` in the URL to pick a
different one of its allowed vaults (same rule as the `vault=` tool
argument). Existing signed transfer URLs remain valid; token minting is no longer
an MCP tool.


> **Known limitation:** `/health` doesn't go through per-request auth/vault
> resolution — it always reports on the first vault listed in
> `vaults.json`, regardless of which identity would be calling. It exposes
> no vault content either way (just process liveness), so this doesn't leak
> anything.

## Vault Conventions (Customization)

Create `_AI_INSTRUCTIONS.md` in your vault root to teach the AI how your specific vault is organized:

```markdown
## Structure
- `Notes/` — evergreen notes and concepts
- `Projects/` — active and archived projects (tag: #project/active, #project/done)
- `Journal/` — daily notes (YYYY-MM-DD.md)

## Frontmatter Schema
- status: active | done | inbox
- tags: nested with / (e.g. #concept/programming)

## Conventions
- Link by stem only, never by full path
- Every note needs a created: date in frontmatter
```

The model reads this file through `read_file` after selecting and authorizing the
vault. Its contents are user context, not privileged server instructions. When
conventions are absent, the model asks for daily-note paths/timezones rather than
inventing them.

## Tool Reference

| Intent | Tools |
|---|---|
| Vault | `list_vaults` |
| Discovery | `list_files`, `search_files`, `list_attachments` |
| Read | `read_file`, `read_files`, `read_frontmatter`, `get_file_outline`, `read_attachment` |
| Write | `create_file`, `edit_file`, `append_file`, `patch_file`, `patch_frontmatter`, `add_attachment` |
| Semantic extras | `get_backlinks`, `get_tasks` |

`read_file` returns raw Markdown including YAML. `edit_file` replaces exactly that
content and requires `expectedRevision`. Incremental writes accept an optional
revision; pass the one read. `append_file` inserts no separators, and `patch_file`
requires a unique literal match unless `replaceAll=true`. Frontmatter arrays replace;
use `remove` for key deletion. Attachments are read as base64 and uploaded create-only.

Listing uses `prefix`, search uses `pathPrefix`; both accept `limit` and `cursor`.
Follow cursors until absent for exhaustive results. Search supports typed YAML
filters and selected properties, including queries without text. Regex/fuzzy and
Dataview inline-field search are not exposed. See [schemas, examples, limits and
pagination semantics](docs/mcp-surface-harmonization.md).

## Architecture

```
src/obsidian_mcp/
├── config.py          # env-based config and read/write security boundaries
├── server.py          # FastMCP entry point, authentication, optional formats
├── canonical_server.py # the 17-tool MCP surface
├── domain/
│   ├── models.py      # Note dataclass (frontmatter, tags, wikilinks, tasks, …)
│   ├── parser.py      # Markdown parser (YAML frontmatter, wikilinks, block refs, …)
│   └── index.py       # VaultIndex — alias resolution, backlinks, tag tree, BFS
├── storage/
│   ├── filesystem.py  # VaultStorage authorization and atomic writes
│   ├── locking.py     # hashed locks outside the synced vault
│   └── watcher.py     # watchdog-based vault change detection (polling fallback)
└── tools/
    ├── canonical.py   # raw Markdown, exact mutations and paginated search
    ├── read.py        # internal parsing/search helpers
    ├── write.py       # write_note, patch_note, move_note, manage_tags, …
    ├── query.py       # graph tools, task aggregation, periodic notes, query_notes
    ├── folders.py     # folder management
    ├── canvas.py      # Obsidian Canvas (.canvas JSON) tools
    ├── excalidraw.py  # Obsidian Excalidraw (*.excalidraw.md) tools
    ├── kanban.py      # Obsidian Kanban plugin tools
    ├── bases.py       # Obsidian Bases (.base YAML) tools
    ├── attachments.py # binary and text attachment handling
    ├── templates.py   # template rendering with variable substitution
    └── prompts.py     # MCP Prompts (weekly_review, daily_note)
```

## Development

```bash
uv run pytest -q       # complete automated suite
uv run ruff check src/ tests/ scripts/run_local_smoke_test.py scripts/smoke_test_mcp.py
```

### Local HTTP functional smoke test

The automated suite exercises most behavior in process. This command also
starts the installed server entry point and connects a real authenticated MCP
client over HTTP:

```bash
uv run python scripts/run_local_smoke_test.py
```

The runner chooses a free localhost port, creates a disposable vault and API
key, starts and health-checks the server, then invokes `smoke_test_mcp.py`. The
client lists tools, creates and reads a uniquely named note, overwrites it,
verifies the bytes on disk, and confirms that a write outside `WRITE_PATHS` is
rejected. The runner always stops the server and removes successful test data;
pass `--keep` to retain the disposable vault and server log for inspection.

Use `smoke_test_mcp.py` directly for an already-running local, containerized,
or remote server. It reads the bearer token from `OBSIDIAN_MCP_API_KEY` (or
prompts securely), so secrets do not need to appear in command history or
process arguments. Its denied-write probe is opt-in via `--denied-note PATH`;
only provide a path known to be outside that server's configured write scope.

## License

MIT
