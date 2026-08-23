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

- **Read & Search** — read notes, search full-text (exact/regex/fuzzy, optionally combined with a frontmatter filter or scoped to filenames), render embedded transclusions, inspect note outlines, list every file in the vault regardless of type
- **Duplicate prevention** — `find_similar_notes_tool` ranks notes by TF-IDF similarity so a new note doesn't duplicate an existing one under different wording
- **Schema linting** — `lint_schema_tool` validates frontmatter against the enums declared in your own `_AI_INSTRUCTIONS.md`, plus an optional cron-friendly health-check script
- **Write** — create/overwrite notes (with automatic frontmatter preservation, dry-run previews, and unified diffs), patch sections or anchor-less body text, append content, update frontmatter (single or batch), manage tags, move notes with automatic wikilink rewriting
- **Folders** — list (optionally recursive with a full tree dump), create, delete, rename folders; renaming rewrites path-based wikilinks vault-wide
- **Query & Graph** — backlinks, broken links, orphan detection, BFS link graph, vault stats, task collection across vault
- **Dataview-like queries** — filter notes by tags, status, frontmatter fields (exact match or `$ne`/`$in`/`$nin`/`$exists` operators), or inline fields (`key:: value`)
- **Audit log** — every write-tool call is recorded ({timestamp, tool, path, summary}); `get_audit_log_tool`/`get_note_history_tool` query it
- **Periodic Notes** — read/preview daily, weekly, monthly, quarterly, yearly journal notes from templates
- **Canvas** *(opt-in via `ENABLE_CANVAS`)* — read, create, and patch Obsidian Canvas (`.canvas`) files
- **Excalidraw** *(opt-in via `ENABLE_EXCALIDRAW`)* — read, create, and patch Obsidian Excalidraw (`*.excalidraw.md`) drawings
- **Kanban** *(opt-in via `ENABLE_KANBAN`)* — read, create, and manipulate Obsidian Kanban boards (columns and cards)
- **Bases** *(opt-in via `ENABLE_BASES`)* — read, create, and patch Obsidian Bases (`.base`) files — YAML-defined table/cards/list views over existing frontmatter properties
- **Attachments** — list, read (text or base64), and add binary files
- **Two auth variants** — a static API key (Claude Code, Desktop, curl) and, optionally, GitHub OAuth (claude.ai Web/Mobile Custom Connector) — usable independently or at the same time
- **Multi-vault** *(opt-in via `VAULTS_CONFIG`)* — serve several fully isolated vaults from one deployment, each identity (API key or GitHub login) mapped to only the vault(s) it may access
- **Templates** — render Obsidian templates with built-in (`{{date}}`, `{{title}}`, …) and custom variables
- **MCP Resources** — expose vault notes, stats, and tags as MCP resources for direct context injection
- **MCP Prompts** — `weekly_review`, `daily_note` starting points for common workflows

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
# READ_ONLY=true            # prevent all writes
# WRITE_PATHS=Notes/,Inbox/ # restrict writes to specific folders
# TRANSPORT=stdio           # stdio (default), http (recommended for network use), or sse (legacy)
```

Full list of variables — including `API_KEY`, `PUBLIC_BASE_URL`, and the
`OAUTH_GITHUB_*` variables for the optional second auth variant — is
documented with inline comments in `.env.example`; see [Remote Setup](#remote-setup-recommended)
for the two auth variants in detail.

### Optional plugin-format tools (Canvas / Excalidraw / Kanban / Bases)

> [!WARNING]
> **Breaking change:** as of this version, the Canvas, Excalidraw, and Kanban
> tool groups are disabled by default, alongside the new Bases tools. If you
> already rely on any of them, set the matching flag(s) below — otherwise
> those tools disappear from your client's tool list after upgrading.

```env
# ENABLE_CANVAS=true      # .canvas file tools
# ENABLE_EXCALIDRAW=true  # *.excalidraw.md file tools
# ENABLE_KANBAN=true      # Kanban board tools
# ENABLE_BASES=true       # .base file tools (Obsidian core plugin, 1.9.0+)
```

Each defaults to `false`. A disabled group's tools aren't just refused at
call time — they're never registered, so they don't appear in the tool list
at all.

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

If you enable GitHub OAuth (see [Option B](#option-b-github-oauth-claudeai-webmobile-custom-connector)), uncomment the `fastmcp-data` volume in `docker-compose.yml` so logins survive container restarts.

The image has a built-in `HEALTHCHECK` against `GET /health` (unauthenticated, no vault content — just `{status, vault_path, index_ready}`), visible in `docker ps`/`docker compose ps`. Only meaningful for `TRANSPORT=http`/`sse`; a no-op for `stdio`.

### Health-Check Cron (Frontmatter Schema)

Separate from the `/health` liveness check above: `scripts/health_check.py` runs `lint_schema_tool`'s logic directly (no MCP client needed) and, only if it finds notes whose frontmatter violates the enums declared in your `_AI_INSTRUCTIONS.md`, drops a report note into your vault's inbox folder. Silent when the vault is clean — no note, no noise.

```bash
# One-off / manual run:
VAULT_PATH=/path/to/vault HEALTH_CHECK_INBOX=00-Inbox python scripts/health_check.py
```

To run it weekly via cron against the running container:

```cron
# crontab -e (on the Docker host)
0 6 * * 1 docker exec obsidian-mcp-obsidian-mcp-1 \
  env VAULT_PATH=/vault HEALTH_CHECK_INBOX=00-Inbox python scripts/health_check.py
```

Swap the container name for whatever `docker compose ps` shows, and `HEALTH_CHECK_INBOX` for your vault's actual inbox folder (default `Inbox`).

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

Keep the vault synced on the server with Syncthing, git+cron, rclone, or Obsidian Sync — obsidian-mcp picks up changes automatically via its file watcher.

## Multi-Vault Setup

By default obsidian-mcp serves one vault (`VAULT_PATH`). If you need several
completely separate vaults from one deployment — e.g. a private vault and a
work vault, each only reachable by its own identity — set `VAULTS_CONFIG` to
the path of a JSON file instead, and every `VAULT_PATH`/`WRITE_PATHS`/
`EXCLUDE_PATHS`/`API_KEY`/`OAUTH_GITHUB_ALLOWED_LOGINS` setting is ignored in
favor of it. See [`vaults.json.example`](vaults.json.example):

```json
{
  "vaults": {
    "private": {"path": "/vaults/private", "exclude_paths": ["private", ".obsidian"]},
    "monari":  {"path": "/vaults/monari",  "write_paths": ["02-Areas/monari/"]}
  },
  "identities": [
    {"type": "api_key",      "value": "sk-...",              "vaults": ["private"]},
    {"type": "github_login", "value": "your-github-username", "vaults": ["private", "monari"], "default": "private"}
  ]
}
```

```env
VAULT_PATH=              # unused — vaults.json defines paths instead
VAULTS_CONFIG=/data/vaults.json
TRANSPORT=http
```

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
every tool accepts an optional `vault=<name>` argument for that one call
(defaults to `"default"` if omitted — set it explicitly whenever an
identity has several vaults, or every call without `vault=` fails asking
for one). `list_vaults_tool()` returns `[{name, description, is_default}]`
for whichever identity is calling, so an MCP client can discover what it's
allowed to pass — the built-in instructions tell Claude to call it first
and pass `vault=` when the conversation clearly points at a non-default
vault. There's no server-side memory of which vault was picked last; it's
re-selected on every call, same as any other argument.

`/attachments/*` (the direct binary upload/download route) is fully
multi-vault-aware: a plain `Authorization: Bearer` request resolves to that
identity's default vault, or pass `?vault=<name>` in the URL to pick a
different one of its allowed vaults (same rule as the `vault=` tool
argument). `create_attachment_token_tool`'s short-lived scoped tokens
(`?exp=&sig=`) work too, signed against the calling identity's own key and
bound to a specific vault — but only for **api_key** identities, since a
GitHub login has no static secret of its own to sign with; use a plain
`Authorization: Bearer` request for those instead.

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

The server loads this file at startup and sends it to the AI as system instructions. Without it, built-in generic Obsidian syntax guidance is used. The `_AI_INSTRUCTIONS.md` is the right place for everything vault-specific — folder layout, tag schema, naming conventions, and any workflow rules.

## Tool Reference

| Category | Tools |
|---|---|
| **Read** | `list_notes`, `read_note`, `search_notes`, `render_note`, `get_note_outline` |
| **Write** | `write_note`, `patch_note`, `delete_note`, `restore_note`, `append_to_note`, `patch_frontmatter`, `manage_tags`, `move_note`, `find_replace_in_vault` |
| **Folders** | `list_folder`, `create_folder`, `delete_folder`, `restore_folder`, `rename_folder`, `list_trash` |
| **Query** | `query_notes`, `get_backlinks`, `get_broken_links`, `get_orphans`, `get_link_graph`, `get_vault_stats`, `get_tasks`, `resolve_alias` |
| **Tags** | `get_notes_by_tag`, `get_tag_tree`, `list_all_tags` |
| **Periodic** | `get_daily_note`, `get_periodic_note` |
| **Canvas** | `list_canvases`, `read_canvas`, `write_canvas`, `patch_canvas` |
| **Excalidraw** | `list_excalidraw`, `read_excalidraw`, `write_excalidraw`, `patch_excalidraw` |
| **Kanban** | `read_kanban`, `create_kanban_board`, `add_kanban_card`, `move_kanban_card`, `delete_kanban_card` |
| **Bases** | `list_bases`, `read_base`, `write_base`, `patch_base` |
| **Attachments** | `list_attachments`, `read_attachment`, `add_attachment` |
| **Templates** | `list_templates`, `create_from_template` |

Canvas, Excalidraw, Kanban, and Bases are each opt-in (see [Optional plugin-format tools](#optional-plugin-format-tools-canvas--excalidraw--kanban--bases) above) — their tools only appear once the matching `ENABLE_*` flag is set.

Full parameter documentation is embedded in the server and shown automatically to connected AI clients.

## Architecture

```
src/obsidian_mcp/
├── config.py          # env-based config (VAULT_PATH, READ_ONLY, WRITE_PATHS)
├── server.py          # FastMCP entry point, tool and resource registrations
├── domain/
│   ├── models.py      # Note dataclass (frontmatter, tags, wikilinks, tasks, …)
│   ├── parser.py      # Markdown parser (YAML frontmatter, wikilinks, block refs, …)
│   └── index.py       # VaultIndex — alias resolution, backlinks, tag tree, BFS
├── storage/
│   ├── filesystem.py  # atomic writes (temp + os.replace), path validation
│   ├── locking.py     # per-file filelock to prevent concurrent write conflicts
│   └── watcher.py     # watchdog-based vault change detection (polling fallback)
└── tools/
    ├── read.py        # read_note, search_notes, render_note, get_note_outline
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
uv run pytest                  # run tests (330 tests)
uv run ruff check src/ tests/  # lint
```

## License

MIT
