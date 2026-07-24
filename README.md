# second-brain-mcp

MCP server for Obsidian vaults – read, write, search, backlinks.

## Quick Start

```bash
cp .env.example .env
# Edit .env: set VAULT_PATH to your vault's absolute path

uv sync
uv run python -m second_brain_mcp.server
```

## Configuration

See `.env.example` for all options.

| Variable | Required | Default | Description |
|---|---|---|---|
| `VAULT_PATH` | yes | – | Absolute path to your Obsidian vault |
| `READ_ONLY` | no | `false` | Disable all write tools |
| `WRITE_PATHS` | no | (all) | Comma-separated allowed write paths |
| `EXCLUDE_PATHS` | no | `private,.obsidian` | Paths to exclude |
| `TRANSPORT` | no | `stdio` | `stdio` or `streamable-http` |
| `AUTH_TOKEN` | if http | – | Token for `streamable-http` transport |

## Docker

```bash
HOST_VAULT_PATH=/path/to/vault AUTH_TOKEN=secret docker compose up
```

## Tools

| Tool | Description |
|---|---|
| `list_notes_tool` | List all .md files in vault (or subfolder) |
| `read_note_tool` | Read a note with frontmatter, tags, wikilinks |
| `search_notes_tool` | Full-text search, optional tag filter |
| `write_note_tool` | Create or overwrite a note |
| `patch_note_tool` | Replace a named section (## Heading) |
| `get_backlinks_tool` | All notes linking to a given note |
| `get_notes_by_tag_tool` | All notes with a given tag |
| `get_vault_conventions_tool` | Read `_AI_INSTRUCTIONS.md` from vault root |
