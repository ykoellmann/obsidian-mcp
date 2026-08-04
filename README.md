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

- **Read & Search** — read notes, search full-text (exact/regex/fuzzy), render embedded transclusions, inspect note outlines
- **Write** — create/overwrite notes, patch sections, append content, update frontmatter, manage tags, move notes with automatic wikilink rewriting
- **Folders** — list, create, delete, rename folders; renaming rewrites path-based wikilinks vault-wide
- **Query & Graph** — backlinks, broken links, orphan detection, BFS link graph, vault stats, task collection across vault
- **Dataview-like queries** — filter notes by tags, status, frontmatter fields, or inline fields (`key:: value`)
- **Periodic Notes** — read/preview daily, weekly, monthly, quarterly, yearly journal notes from templates
- **Canvas** — read, create, and patch Obsidian Canvas (`.canvas`) files
- **Kanban** — read, create, and manipulate Obsidian Kanban boards (columns and cards)
- **Attachments** — list, read (text or base64), and add binary files
- **Two auth variants** — a static API key (Claude Code, Desktop, curl) and, optionally, GitHub OAuth (claude.ai Web/Mobile Custom Connector) — usable independently or at the same time
- **Templates** — render Obsidian templates with built-in (`{{date}}`, `{{title}}`, …) and custom variables
- **MCP Resources** — expose vault notes, stats, and tags as MCP resources for direct context injection

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
| **Write** | `write_note`, `patch_note`, `delete_note`, `append_to_note`, `patch_frontmatter`, `manage_tags`, `move_note` |
| **Folders** | `list_folder`, `create_folder`, `delete_folder`, `rename_folder` |
| **Query** | `query_notes`, `get_backlinks`, `get_broken_links`, `get_orphans`, `get_link_graph`, `get_vault_stats`, `get_tasks`, `resolve_alias` |
| **Tags** | `get_notes_by_tag`, `get_tag_tree`, `list_all_tags` |
| **Periodic** | `get_daily_note`, `get_periodic_note` |
| **Canvas** | `list_canvases`, `read_canvas`, `write_canvas`, `patch_canvas` |
| **Kanban** | `read_kanban`, `create_kanban_board`, `add_kanban_card`, `move_kanban_card`, `delete_kanban_card` |
| **Attachments** | `list_attachments`, `read_attachment`, `add_attachment` |
| **Templates** | `list_templates`, `create_from_template` |

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
    ├── kanban.py      # Obsidian Kanban plugin tools
    ├── attachments.py # binary and text attachment handling
    └── templates.py   # template rendering with variable substitution
```

## Development

```bash
uv run pytest                  # run tests (268 tests)
uv run ruff check src/ tests/  # lint
```

## License

MIT
