"""FastMCP server entry point."""

from __future__ import annotations

import logging
import threading

from fastmcp import FastMCP

from .config import get_config
from .domain.index import VaultIndex
from .storage.filesystem import read_file
from .storage.watcher import VaultWatcher
from .tools.attachments import add_attachment, list_attachments, read_attachment
from .tools.canvas import list_canvases, patch_canvas, read_canvas, write_canvas
from .tools.folders import (
    create_folder,
    delete_folder,
    list_folder,
    rename_folder,
)
from .tools.kanban import (
    add_kanban_card,
    create_kanban_board,
    delete_kanban_card,
    move_kanban_card,
    read_kanban,
)
from .tools.query import (
    get_backlinks,
    get_broken_links,
    get_daily_note,
    get_link_graph,
    get_notes_by_tag,
    get_orphans,
    get_periodic_note,
    get_tag_tree,
    get_tasks,
    get_vault_conventions,
    get_vault_stats,
    list_all_tags,
    query_notes,
    resolve_alias,
)
from .tools.read import get_note_outline, list_notes, read_note, render_note, search_notes
from .tools.templates import create_from_template, list_templates
from .tools.write import (
    append_to_note,
    delete_note,
    manage_tags,
    move_note,
    patch_frontmatter,
    patch_note,
    write_note,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


_DEFAULT_INSTRUCTIONS = """\
You are connected to **obsidian-mcp**, an MCP server for an Obsidian vault.

## Vault Conventions
Call `get_vault_conventions_tool()` first. If the vault has an `_AI_INSTRUCTIONS.md`,
it defines the folder structure, tag schema, and naming rules for this specific vault.
If it doesn't exist, explore the vault with `list_folder_tool()` and `list_notes_tool()`
to understand the structure before writing anything.
Always use `search_notes_tool` or `query_notes_tool` before creating notes to avoid duplicates.

## Tool Reference

### Reading & Search
- `list_notes_tool(folder, include_meta)` — list notes; include_meta=True adds title/tags/status/mtime
- `read_note_tool(path)` — full note: content, frontmatter, tags, wikilinks, tasks, inline_fields
- `get_note_outline_tool(path)` — headings, block refs, frontmatter keys — efficient for large notes
- `search_notes_tool(query, tag, mode, limit)` — full-text search with snippets; mode: exact|regex|fuzzy
- `render_note_tool(path, depth)` — resolves ![[embed]] transclusions inline

### Writing
- `write_note_tool(path, content)` — create or overwrite a note
- `patch_note_tool(path, section, new_content, mode, target_type)` — edit one section;
  mode: replace|insert_before|insert_after|append — target_type: heading|block_ref
- `append_to_note_tool(path, content, section, create)` — append to end or under a heading
- `patch_frontmatter_tool(path, updates, merge_arrays)` — update YAML keys without touching the body
- `manage_tags_tool(path, add, remove)` — add/remove tags in frontmatter and inline
- `delete_note_tool(path, trash)` — trash=True (default) moves to .trash/
- `move_note_tool(from_path, to_path)` — rename/move + rewrites all wikilinks vault-wide

### Folders
- `list_folder_tool(path)` — immediate contents (path="" = vault root); hides dotfiles
- `create_folder_tool(path)` — create folder, parents auto-created
- `delete_folder_tool(path, trash)` — delete or trash a folder
- `rename_folder_tool(from_path, to_path)` — rename + rewrites path-based wikilinks

### Querying & Graph
- `query_notes_tool(tags, status, frontmatter_filter, inline_field_filter, sort_by, limit, folder)` — Dataview-like filter
- `get_backlinks_tool(path)` — notes that link to this note
- `get_broken_links_tool()` — wikilinks pointing to non-existent notes
- `get_orphans_tool(exclude_folders)` — notes with no incoming backlinks
- `get_link_graph_tool(root, depth, direction)` — BFS link graph; direction: outgoing|incoming|both
- `get_vault_stats_tool()` — note/link counts, orphans, most-linked notes
- `get_tasks_tool(status, folder, tag)` — tasks across vault; status: open|done|all
- `get_notes_by_tag_tool(tag)`, `get_tag_tree_tool()`, `list_all_tags_tool(sort_by)`
- `get_daily_note_tool(date)` / `get_periodic_note_tool(period, date)` — periodic notes; date: today|yesterday|YYYY-MM-DD
- `resolve_alias_tool(name)` — alias or stem → canonical vault path

### Attachments
- `list_attachments_tool(folder)`, `read_attachment_tool(path)`, `add_attachment_tool(path, content_base64)`

### Templates
- `list_templates_tool()`, `create_from_template_tool(template_path, output_path, variables)`
- Built-in variables: `{{date}}`, `{{title}}`, `{{week}}`, `{{month}}`, `{{year}}`, `{{weekday}}`, `{{time}}`

### Canvas (.canvas files)
- `list_canvases_tool()`, `read_canvas_tool(path)`
- `write_canvas_tool(path, nodes, edges)` — node types: text|file|group|link
- `patch_canvas_tool(path, add_nodes, update_nodes, delete_node_ids, add_edges, delete_edge_ids)`

### Kanban (Obsidian Kanban plugin)
- `read_kanban_tool(path)`, `create_kanban_board_tool(path, columns)`
- `add_kanban_card_tool(path, column, text, done)`, `move_kanban_card_tool(...)`, `delete_kanban_card_tool(...)`

## MCP Resources
- `vault://notes/{path}` — raw note content as context
- `vault://stats` — live vault statistics
- `vault://tags` — all tags with counts

---

## Obsidian Markdown Syntax

### Frontmatter
```yaml
---
title: Note Title
aliases: [Short Name, Other Alias]
tags: [category/sub, other-tag]
status: active
created: 2026-01-01
---
```

### Headings
Use `##`, `###`, `####` inside notes. Avoid `#` — it's the implicit document title level.

### Wikilinks
| Syntax | Meaning |
|---|---|
| `[[Note Name]]` | link by filename stem |
| `[[Note Name\|Display Text]]` | link with alias |
| `[[Note Name#Heading]]` | link to heading |
| `[[Note Name^block-id]]` | link to block |
| `![[Note Name]]` | embed/transclude note |

Links are case-insensitive and alias-aware. Prefer stem links (`[[Note]]` not `[[Folder/Note]]`) — they survive folder renames.

### Block References
```
Important paragraph. ^my-block-id
```
Reference with `[[Note^my-block-id]]`. IDs: lowercase letters, digits, hyphens.

### Tags
- Frontmatter: `tags: [category/sub]`
- Inline: `#category/sub`
- Nested with `/` for hierarchy.

### Tasks
```markdown
- [ ] Open task
- [x] Done task
```

### Callouts
```markdown
> [!NOTE] Title
> Content.

> [!WARNING], [!TIP], [!IMPORTANT], [!QUESTION] also supported.
```

### Dataview Inline Fields
```
rating:: 8
due date:: 2026-08-01
```
Accessible via `read_note_tool` → `inline_fields`, filterable in `query_notes_tool`.

---

## Supported Plugin Formats

### Kanban
Frontmatter `kanban-plugin: basic`, columns as `## Name`, cards as `- [ ] text`.
Always use the kanban tools instead of editing raw Markdown.

### Canvas
`.canvas` files: JSON with `nodes` (text/file/group/link) and `edges`. IDs auto-generated.

### Dataview
Only `key:: value` inline fields are parsed server-side. DQL block queries are Obsidian-app-only.

### Periodic Notes
Default paths: `Journal/YYYY-MM-DD.md` (daily), `Journal/Weekly/YYYY-Www.md`, etc.
Check `_AI_INSTRUCTIONS.md` — the vault may use different paths.
Templates use `{{date}}`, `{{title}}`, `{{week}}` etc.
"""


def _load_instructions() -> str:
    try:
        cfg = get_config()
        instructions_file = cfg.vault_path / "_AI_INSTRUCTIONS.md"
        if instructions_file.exists():
            return instructions_file.read_text(encoding="utf-8")
    except Exception:
        pass
    return _DEFAULT_INSTRUCTIONS


# Initialized in main() — None at import time so the module can be imported
# without VAULT_PATH set (e.g. during testing or linting).
_cfg = None
_index: VaultIndex | None = None
_watcher = None

mcp = FastMCP(name="obsidian-mcp", instructions=_load_instructions())


# ── Read ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_notes_tool(folder: str = "", include_meta: bool = False) -> list:
    """List all Markdown notes in the vault (or a subfolder).
    Set include_meta=True to get title, tags, status, created per note."""
    return list_notes(folder, include_meta=include_meta)


@mcp.tool()
def read_note_tool(path: str) -> dict:
    """Read a note – returns content, frontmatter, tags, aliases, wikilinks,
    block_refs, callouts, and tasks."""
    return read_note(path)


@mcp.tool()
def search_notes_tool(
    query: str,
    tag: str | None = None,
    mode: str = "exact",
    limit: int = 20,
) -> list[dict]:
    """Full-text search with snippets and relevance ranking.
    mode: 'exact' (default) | 'regex' | 'fuzzy'. Returns [{path, score, snippets, tags}]."""
    return search_notes(query, tag=tag, mode=mode, limit=limit)


@mcp.tool()
def render_note_tool(path: str, depth: int = 1) -> str:
    """Read a note with all ![[embed]] transclusions resolved inline.
    depth: 0=raw, 1=one level of embeds (default), 2=nested embeds."""
    return render_note(path, depth=depth)


@mcp.tool()
def get_note_outline_tool(path: str) -> dict:
    """Return the structural map of a note without its body text.
    Returns {headings, block_refs, frontmatter_keys, tags, word_count, line_count}.
    Efficient for large notes where you only need structure."""
    return get_note_outline(path)


# ── Write ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def write_note_tool(path: str, content: str) -> dict:
    """Write (create or overwrite) a note. Respects READ_ONLY and WRITE_PATHS."""
    return write_note(path, content, index=_index)


@mcp.tool()
def patch_note_tool(
    path: str,
    section: str,
    new_content: str,
    mode: str = "replace",
    target_type: str = "heading",
) -> dict:
    """Edit a section or block reference inside a note.
    mode: 'replace' (default) | 'insert_before' | 'insert_after' | 'append'.
    target_type: 'heading' (default) | 'block_ref' (use section='^block-id')."""
    return patch_note(path, section, new_content, mode=mode, target_type=target_type, index=_index)


@mcp.tool()
def delete_note_tool(path: str, trash: bool = True) -> dict:
    """Delete a note from the vault.
    trash=True (default) moves it to .trash/ instead of permanent deletion."""
    return delete_note(path, trash=trash, index=_index)


@mcp.tool()
def append_to_note_tool(
    path: str,
    content: str,
    section: str | None = None,
    create: bool = True,
) -> dict:
    """Append content to a note without reading and rewriting the whole file.
    section: optional heading to append under. create=True creates the note if missing."""
    return append_to_note(path, content, section=section, create=create, index=_index)


@mcp.tool()
def patch_frontmatter_tool(
    path: str,
    updates: dict,
    merge_arrays: bool = True,
) -> dict:
    """Update specific YAML frontmatter keys without touching the note body.
    merge_arrays=True merges list values (e.g. tags); False replaces them."""
    return patch_frontmatter(path, updates, merge_arrays=merge_arrays, index=_index)


@mcp.tool()
def manage_tags_tool(
    path: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict:
    """Add or remove tags on a note. Updates frontmatter tags array and strips
    inline #tag occurrences from the body. Returns {added, removed}."""
    return manage_tags(path, add=add, remove=remove, index=_index)


@mcp.tool()
def move_note_tool(from_path: str, to_path: str) -> dict:
    """Rename or move a note. Automatically rewrites all wikilinks in the vault
    that reference the old path. Returns {from, to, updated_links_in}."""
    return move_note(from_path, to_path, index=_index)


# ── Query / Graph ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_backlinks_tool(path: str) -> list[str]:
    """Return all notes that link to the given note (alias-aware)."""
    return get_backlinks(path, _index)


@mcp.tool()
def get_notes_by_tag_tool(tag: str) -> list[str]:
    """Return all notes that have the given tag."""
    return get_notes_by_tag(tag, _index)


@mcp.tool()
def get_vault_conventions_tool() -> str:
    """Return the vault's AI instructions / conventions from _AI_INSTRUCTIONS.md."""
    return get_vault_conventions()


@mcp.tool()
def get_broken_links_tool() -> list[dict]:
    """Find all wikilinks in the vault that point to non-existent notes.
    Returns [{source, link}]."""
    return get_broken_links(_index)


@mcp.tool()
def get_orphans_tool(exclude_folders: list[str] | None = None) -> list[str]:
    """Find notes that no other note links to.
    Excludes Journal and Templates by default."""
    return get_orphans(_index, exclude_folders=exclude_folders or ["Journal", "Templates"])


@mcp.tool()
def get_link_graph_tool(
    root: str,
    depth: int = 2,
    direction: str = "both",
) -> dict:
    """Return a traversable link graph starting from a note.
    direction: 'outgoing' | 'incoming' | 'both'.
    Returns {root, nodes: [{path, title, tags}], edges: [{from, to, type}]}."""
    return get_link_graph(root, _index, depth=depth, direction=direction)


@mcp.tool()
def get_vault_stats_tool() -> dict:
    """Return vault statistics: note count, link count, orphans, broken links,
    most-linked notes."""
    return get_vault_stats(_index)


@mcp.tool()
def get_tag_tree_tool() -> dict:
    """Return all tags as a nested tree (e.g. konzept → python, ki → llm)."""
    return get_tag_tree(_index)


@mcp.tool()
def list_all_tags_tool(sort_by: str = "count") -> list[dict]:
    """Return all tags in the vault with note counts.
    sort_by: 'count' (descending, default) | 'name' (alphabetical).
    Returns [{tag, count}]."""
    return list_all_tags(_index, sort_by=sort_by)


@mcp.tool()
def get_tasks_tool(
    status: str = "open",
    folder: str = "",
    tag: str | None = None,
) -> list[dict]:
    """Return tasks from across the vault.
    status: 'open' | 'done' | 'all'. Optionally filter by folder or tag.
    Returns [{text, done, source, line}]."""
    return get_tasks(_index, status=status, folder=folder, tag=tag)


@mcp.tool()
def get_daily_note_tool(date: str = "today") -> dict:
    """Read a daily note from Journal/.
    date: 'today' | 'yesterday' | 'YYYY-MM-DD'.
    Returns {path, exists, content, frontmatter, tasks}."""
    return get_daily_note(_index, date_str=date)


@mcp.tool()
def get_periodic_note_tool(period: str = "daily", date: str = "today") -> dict:
    """Read or preview a periodic note.
    period: 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly'.
    date: 'today' | 'yesterday' | 'YYYY-MM-DD'.
    Returns {path, period, date, exists, content, frontmatter, tasks}."""
    return get_periodic_note(_index, period=period, date_str=date)


@mcp.tool()
def resolve_alias_tool(name: str) -> str | None:
    """Resolve a note alias or stem to its real vault path.
    Returns None if not found."""
    return resolve_alias(name, _index)


@mcp.tool()
def query_notes_tool(
    tags: list[str] | None = None,
    status: str | None = None,
    frontmatter_filter: dict | None = None,
    inline_field_filter: dict | None = None,
    sort_by: str = "path",
    sort_desc: bool = False,
    limit: int = 50,
    folder: str = "",
) -> list[dict]:
    """Dataview-like query: filter notes by tags, status, frontmatter, or inline fields.
    tags: all must match (AND). sort_by: 'path'|'title'|'created'|'mtime'.
    inline_field_filter: match Dataview inline fields (key:: value syntax).
    Returns [{path, title, tags, status, created, mtime, frontmatter, inline_fields}]."""
    return query_notes(
        _index,
        tags=tags,
        status=status,
        frontmatter_filter=frontmatter_filter,
        inline_field_filter=inline_field_filter,
        sort_by=sort_by,
        sort_desc=sort_desc,
        limit=limit,
        folder=folder,
    )


# ── Attachments ───────────────────────────────────────────────────────────────

@mcp.tool()
def list_attachments_tool(folder: str = "") -> list[dict]:
    """List all non-Markdown files in the vault: images, PDFs, audio, etc.
    Returns [{path, size_bytes, mime_type, mtime}]."""
    return list_attachments(folder)


@mcp.tool()
def read_attachment_tool(path: str) -> dict:
    """Read an attachment file. Text files returned as UTF-8 string.
    Binary files (images, PDFs) returned as base64-encoded content with mime_type."""
    return read_attachment(path)


@mcp.tool()
def add_attachment_tool(path: str, content_base64: str) -> dict:
    """Write a binary attachment (image, PDF, etc.) to the vault from base64-encoded content.
    Returns {path, status, size_bytes, mime_type}."""
    return add_attachment(path, content_base64)


# ── Templates ─────────────────────────────────────────────────────────────────

@mcp.tool()
def list_templates_tool() -> list[str]:
    """List all template files in the Templates/ folder."""
    return list_templates()


@mcp.tool()
def create_from_template_tool(
    template_path: str,
    output_path: str,
    variables: dict | None = None,
) -> dict:
    """Render a template and write it as a new note.
    Built-in variables: {{date}}, {{time}}, {{title}}, {{week}}, {{month}}, {{year}}, {{weekday}}.
    Supports format specs: {{date:YYYY-MM}} → '2026-07'.
    Custom variables passed in 'variables' dict override built-ins.
    Unknown {{vars}} are preserved as-is."""
    return create_from_template(template_path, output_path, variables=variables, index=_index)


# ── Canvas ────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_canvases_tool() -> list[str]:
    """List all Obsidian Canvas (.canvas) files in the vault."""
    return list_canvases()


@mcp.tool()
def read_canvas_tool(path: str) -> dict:
    """Read an Obsidian Canvas file.
    Returns {path, nodes: [{id, type, text, file, x, y}], edges: [{from, to, label}]}."""
    return read_canvas(path)


@mcp.tool()
def write_canvas_tool(
    path: str,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> dict:
    """Create or fully overwrite an Obsidian Canvas file.
    Node fields: type ('text'|'file'|'group'|'link'), x, y, width, height.
    Text nodes: text. File nodes: file (vault path). Link nodes: url.
    Edge fields: fromNode, toNode, label (optional). IDs are auto-generated if omitted.
    Returns {path, status, nodes, edges}."""
    return write_canvas(path, nodes=nodes, edges=edges)


@mcp.tool()
def patch_canvas_tool(
    path: str,
    add_nodes: list[dict] | None = None,
    update_nodes: list[dict] | None = None,
    delete_node_ids: list[str] | None = None,
    add_edges: list[dict] | None = None,
    delete_edge_ids: list[str] | None = None,
) -> dict:
    """Atomically update an existing canvas without rewriting the whole file.
    update_nodes: each dict must include 'id'. delete_node_ids also removes
    all edges connected to those nodes. Returns {path, status, nodes, edges}."""
    return patch_canvas(
        path,
        add_nodes=add_nodes,
        update_nodes=update_nodes,
        delete_node_ids=delete_node_ids,
        add_edges=add_edges,
        delete_edge_ids=delete_edge_ids,
    )


# ── Kanban ────────────────────────────────────────────────────────────────────

@mcp.tool()
def read_kanban_tool(path: str) -> dict:
    """Read an Obsidian Kanban board (requires kanban-plugin in frontmatter).
    Returns {path, plugin, columns: [{name, cards: [{text, done}]}], total_cards}."""
    return read_kanban(path)


@mcp.tool()
def create_kanban_board_tool(path: str, columns: list[str]) -> dict:
    """Create a new Kanban board with the given column names.
    Returns {path, status, columns}."""
    return create_kanban_board(path, columns, index=_index)


@mcp.tool()
def add_kanban_card_tool(
    path: str,
    column: str,
    text: str,
    done: bool = False,
) -> dict:
    """Add a card to a Kanban column. Card is inserted at the top of the column.
    Returns {path, status, column, card, done}."""
    return add_kanban_card(path, column, text, done=done, index=_index)


@mcp.tool()
def move_kanban_card_tool(
    path: str,
    card_text: str,
    from_column: str,
    to_column: str,
    done: bool | None = None,
) -> dict:
    """Move a card from one column to another. done=true/false updates the tick state.
    Returns {path, status, card, from, to}."""
    return move_kanban_card(path, card_text, from_column, to_column, done=done, index=_index)


@mcp.tool()
def delete_kanban_card_tool(
    path: str,
    card_text: str,
    column: str | None = None,
) -> dict:
    """Delete a card from the Kanban board. column limits the search to one column.
    Returns {path, status, card}."""
    return delete_kanban_card(path, card_text, column=column, index=_index)


# ── Folders ───────────────────────────────────────────────────────────────────

@mcp.tool()
def list_folder_tool(path: str = "") -> dict:
    """List the immediate contents of a vault folder (non-hidden items only).
    path='': root of the vault. Returns {path, folders: [...], files: [...]}."""
    return list_folder(path)


@mcp.tool()
def create_folder_tool(path: str) -> dict:
    """Create a folder (and any missing parents) in the vault.
    Returns {path, status}."""
    return create_folder(path)


@mcp.tool()
def delete_folder_tool(path: str, trash: bool = True) -> dict:
    """Delete a vault folder.
    trash=True (default) moves it to .trash/ instead of permanent deletion.
    Returns {path, status, trash}."""
    return delete_folder(path, trash=trash)


@mcp.tool()
def rename_folder_tool(from_path: str, to_path: str) -> dict:
    """Rename or move a vault folder. Rewrites path-based wikilinks in all
    notes that reference notes inside the moved folder.
    Returns {from, to, notes_moved, updated_links_in}."""
    return rename_folder(from_path, to_path, index=_index)


# ── MCP Resources ─────────────────────────────────────────────────────────────

@mcp.resource("vault://notes/{path}")
def vault_note_resource(path: str) -> str:
    """Raw content of a vault note — use as context without calling a tool."""
    try:
        return read_file(get_config().vault_path, path)
    except Exception:
        return ""


@mcp.resource("vault://stats")
def vault_stats_resource() -> dict:
    """Current vault statistics (note count, links, orphans, broken links)."""
    return get_vault_stats(_index)


@mcp.resource("vault://tags")
def vault_tags_resource() -> list:
    """All tags in the vault with note counts, sorted by frequency."""
    return list_all_tags(_index, sort_by="count")


# ── Startup ───────────────────────────────────────────────────────────────────

def main() -> None:
    global _cfg, _index, _watcher
    _cfg = get_config()
    _index = VaultIndex(_cfg.vault_path, exclude_paths=_cfg.exclude_paths)
    _watcher = VaultWatcher(_cfg.vault_path)
    threading.Thread(target=_index.build, daemon=True).start()
    _watcher.start(on_change=_index.update)
    logger.info("Starting obsidian-mcp (transport=%s)", _cfg.transport)
    mcp.run(transport=_cfg.transport)


if __name__ == "__main__":
    main()
