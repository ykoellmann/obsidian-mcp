# obsidian-mcp

An MCP (Model Context Protocol) server for [Obsidian](https://obsidian.md) vaults. Connects Claude (or any MCP client) directly to your vault — read, write, search, navigate links, and manage notes, canvases, and kanban boards.

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
- **Templates** — render Obsidian templates with built-in (`{{date}}`, `{{title}}`, …) and custom variables
- **MCP Resources** — expose vault notes, stats, and tags as MCP resources for direct context injection

## Installation

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/ykoellmann/obsidian-mcp.git
cd obsidian-mcp
uv sync
```

## Configuration

Copy `.env.example` to `.env` and set your vault path:

```env
VAULT_PATH=/path/to/your/obsidian/vault
# Optional:
# READ_ONLY=true            # prevent all writes
# WRITE_PATHS=Notes/,Inbox/ # restrict writes to specific folders
# TRANSPORT=stdio           # stdio (default) or sse
```

## Usage with Claude Code

Add to your MCP config (e.g. `~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "uv",
      "args": ["--directory", "/path/to/obsidian-mcp", "run", "obsidian-mcp"],
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
      "args": ["--directory", "/path/to/obsidian-mcp", "run", "obsidian-mcp"],
      "env": {
        "VAULT_PATH": "/path/to/your/obsidian/vault"
      }
    }
  }
}
```

## Docker

```bash
# Edit docker-compose.yml to set VAULT_PATH and the volume mount, then:
docker compose up
```

## Vault Conventions

Create `_AI_INSTRUCTIONS.md` in your vault root to define conventions for the AI (folder structure, tag schema, naming rules). The server loads this file at startup and uses it as the system instructions — if absent, built-in Obsidian syntax guidance is used instead.

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
uv run pytest                  # run tests (238 tests)
uv run ruff check src/ tests/  # lint
```

## License

MIT
