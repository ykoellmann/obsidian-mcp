"""FastMCP server entry point."""

from __future__ import annotations

import hmac
import logging
import mimetypes
import os
import threading
from dataclasses import dataclass
from functools import wraps

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import (
    ConfigError,
    get_config,
    load_vaults_file,
    reset_current_vault,
    set_current_vault,
)
from .domain.index import VaultIndex
from .domain.models import PreconditionRequiredError, RevisionConflictError
from .storage.filesystem import VaultStorage
from .storage.policy import (
    InvalidFileTypeError,
    ReadPermissionError,
    VaultAccessPolicy,
    VaultPathError,
    WritePermissionError,
)
from .storage.watcher import VaultWatcher
from .tools.attachments import (
    AttachmentTooLargeError,
    add_attachment,
    create_attachment_token,
    list_attachments,
    read_attachment,
    validate_attachment_path,
    verify_attachment_token,
    write_attachment_bytes,
)
from .tools.audit import get_audit_log, get_note_history, log_write
from .tools.bases import list_bases, patch_base, read_base, write_base
from .tools.canvas import list_canvases, patch_canvas, read_canvas, write_canvas
from .tools.excalidraw import (
    list_excalidraw,
    patch_excalidraw,
    read_excalidraw,
    write_excalidraw,
)
from .tools.folders import (
    create_folder,
    delete_folder,
    list_files,
    list_folder,
    list_trash,
    rename_folder,
    restore_folder,
)
from .tools.kanban import (
    add_kanban_card,
    create_kanban_board,
    delete_kanban_card,
    move_kanban_card,
    read_kanban,
)
from .tools.lint import lint_schema
from .tools.prompts import daily_note_prompt, weekly_review_prompt
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
from .tools.similarity import find_similar_notes
from .tools.templates import create_from_template, list_templates
from .tools.write import (
    append_to_note,
    delete_note,
    find_replace_in_vault,
    manage_tags,
    move_note,
    patch_frontmatter,
    patch_frontmatter_batch,
    patch_note,
    patch_note_text,
    restore_note,
    write_note,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _mutation_boundary(function):
    """Return expected optimistic-concurrency failures as MCP error results."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (RevisionConflictError, PreconditionRequiredError) as exc:
            return ToolResult(structured_content=exc.to_dict(), is_error=True)

    return wrapped


_DEFAULT_INSTRUCTIONS = r"""\
You are connected to **obsidian-mcp**, an MCP server for an Obsidian vault.

## Multiple Vaults
Call `list_vaults_tool()` first, before anything else. If it returns more
than one vault, this identity has access to several completely separate
ones — figure out from what the user is asking which one they mean (a
vault's `description` is there to help with that), and pass `vault=<name>`
on every other tool call where it matters. Leaving `vault` out uses
whichever entry has `is_default: true` — fine when the user's request
doesn't point at a specific one, wrong if they clearly mean the
non-default vault. There's no server-side memory of which vault you picked
last time; pass `vault=` again on each subsequent call in the same
conversation, the same way you already repeat `path=` on each call. If
`list_vaults_tool()` returns exactly one vault, ignore all of this — there's
nothing to choose between.

## Vault Conventions
Call `get_vault_conventions_tool()` first. If the vault has an `_AI_INSTRUCTIONS.md`,
it defines the folder structure, tag schema, and naming rules for this specific vault.
If it doesn't exist, explore the vault with `list_folder_tool()` and `list_notes_tool()`
to understand the structure before writing anything.
Always use `search_notes_tool` or `query_notes_tool` before creating notes to avoid duplicates.

Obsidian uses the **filename** (without `.md`) as the note title in the UI, graph,
and default wikilink text. Do **not** start a note with a level-1 heading that
repeats the filename. Put the first heading at `##` or below only if the body
needs structure; many notes need no heading at all.

## Tool Reference

### Reading & Search
- `list_notes_tool(folder, include_meta)` — list notes; include_meta=True adds title/tags/status/mtime
- `list_files_tool(folder, extension)` — list every file (any type, not just notes), optional extension filter
- `read_note_tool(path)` — full note: content, frontmatter, tags, wikilinks, tasks, inline_fields
- `get_note_outline_tool(path)` — headings, block refs, frontmatter keys — efficient for large notes
- `search_notes_tool(query, tag, mode, limit, frontmatter_filter, field, threshold)` — full-text search
  with snippets; mode: exact|regex|fuzzy; field: None|'filename'; combine with frontmatter_filter in one call
- `find_similar_notes_tool(text, limit, exclude_path, min_score)` — TF-IDF similarity search for
  duplicate prevention ("does this topic already exist under different wording?")
- `render_note_tool(path, depth)` — resolves ![[embed]] transclusions inline
- `lint_schema_tool()` — validate frontmatter against the enums declared in _AI_INSTRUCTIONS.md

### Writing
- Direct reads return an opaque `revision`. Pass it as `expected_revision` to pin a write
  to those bytes. On a revision conflict, read again before deciding whether to retry.
  A retried append with an old revision fails safely; a blind retry can append twice.
- `write_note_tool(path, content, dry_run)` — create or overwrite a note; preserves existing
  frontmatter if the new content has none; dry_run previews {preview, diff} without writing
- `patch_note_tool(path, section, new_content, mode, target_type)` — edit one section;
  mode: replace|insert_before|insert_after|append — target_type: heading|block_ref
- `patch_note_text_tool(path, find, replace, mode, count, dry_run)` — find/replace anywhere in a
  note's body, no heading/block-ref anchor required
- `append_to_note_tool(path, content, section, create)` — append to end or under a heading
- `patch_frontmatter_tool(path, updates, merge_arrays, dry_run)` — update YAML keys without touching
  the body; dry_run previews {preview, diff} without writing
- `patch_frontmatter_batch_tool(updates)` — patch frontmatter on multiple notes in one call
- `manage_tags_tool(path, add, remove)` — add/remove tags in frontmatter and inline
- `delete_note_tool(path, trash)` — trash=True (default) moves to .trash/
- `restore_note_tool(trashed_name, to_path)` — undo a trashed delete; trashed_name from list_trash_tool
- `move_note_tool(from_path, to_path)` — rename/move + rewrites all wikilinks vault-wide
- `find_replace_in_vault_tool(search, replace, mode, folder, dry_run)` — bulk find/replace across
  every note; dry_run=True (default) previews matches before writing anything

High-impact mutation tools are disabled by default and absent from the tool
list until explicitly enabled: `ENABLE_DELETE` registers note/folder deletion,
`ENABLE_MOVE` registers note moves, `ENABLE_FOLDER_RENAME` registers folder
renames, and `ENABLE_BULK_REPLACE` registers bulk replacement.

### Folders
- `list_folder_tool(path, recursive, max_depth)` — contents (path="" = vault root); hides dotfiles;
  recursive=True returns a full tree dump in one call
- `create_folder_tool(path)` — create folder, parents auto-created
- `delete_folder_tool(path, trash)` — delete or trash a folder
- `restore_folder_tool(trashed_name, to_path)` — undo a trashed delete; trashed_name from list_trash_tool
- `rename_folder_tool(from_path, to_path)` — rename + rewrites path-based wikilinks
- `list_trash_tool()` — see what's sitting in .trash/, with the names restore_*_tool expects

### Querying & Graph
- `query_notes_tool(tags, status, frontmatter_filter, inline_field_filter, sort_by, limit, folder)` —
  Dataview-like filter; frontmatter_filter values may be exact or an operator dict
  ($ne/$eq/$in/$nin/$exists)
- `get_backlinks_tool(path)` — notes that link to this note
- `get_broken_links_tool()` — wikilinks pointing to non-existent notes
- `get_orphans_tool(exclude_folders)` — notes with no incoming backlinks
- `get_link_graph_tool(root, depth, direction)` — BFS link graph; direction: outgoing|incoming|both
- `get_audit_log_tool(path, tool, since, limit)` / `get_note_history_tool(path, limit)` — write-action
  audit trail (what changed, when, with which tool), most recent first
- `get_vault_stats_tool()` — note/link counts, orphans, most-linked notes
- `get_tasks_tool(status, folder, tag)` — tasks across vault; status: open|done|all
- `get_notes_by_tag_tool(tag)`, `get_tag_tree_tool()`, `list_all_tags_tool(sort_by)`
- `get_daily_note_tool(date)` / `get_periodic_note_tool(period, date)` — periodic notes; date: today|yesterday|YYYY-MM-DD
- `resolve_alias_tool(name)` — alias or stem → canonical vault path

### Multi-Vault
- `list_vaults_tool()` — vault(s) the current identity may access:
  [{name, description, is_default}]. Every other tool accepts an optional
  `vault=<name>` argument to operate on a non-default one for that call.

### Attachments
- `list_attachments_tool(folder)`, `read_attachment_tool(path)`, `add_attachment_tool(path, content_base64)`
- For large/many binary files, `create_attachment_token_tool(path, method, expires_in)` mints a short-lived,
  single-file token, then `GET`/`PUT /attachments/{path}?exp=&sig=` reads/writes raw bytes directly on disk
  — no base64, no MCP tool-call channel, and no need to ever expose the server's master API_KEY.

### Templates
- `list_templates_tool()`, `create_from_template_tool(template_path, output_path, variables)`
- Built-in variables: `{{date}}`, `{{title}}`, `{{week}}`, `{{month}}`, `{{year}}`, `{{weekday}}`, `{{time}}`

The Canvas/Kanban/Excalidraw/Bases tool groups below are each opt-in on the
server (`ENABLE_CANVAS`/`ENABLE_KANBAN`/`ENABLE_EXCALIDRAW`/`ENABLE_BASES`).
If a tool from one of these groups isn't in your tool list, the operator
hasn't enabled it — that's expected, not an error; don't retry or assume it's
broken.

### Canvas (.canvas files)
- `list_canvases_tool()`, `read_canvas_tool(path)`
- `write_canvas_tool(path, nodes, edges)` — node types: text|file|group|link
- `patch_canvas_tool(path, add_nodes, update_nodes, delete_node_ids, add_edges, delete_edge_ids)`

### Kanban (Obsidian Kanban plugin)
- `read_kanban_tool(path)`, `create_kanban_board_tool(path, columns)`
- `add_kanban_card_tool(path, column, text, done)`, `move_kanban_card_tool(...)`, `delete_kanban_card_tool(...)`

### Excalidraw (*.excalidraw.md, Obsidian Excalidraw plugin)
- `list_excalidraw_tool()`, `read_excalidraw_tool(path)` — returns {path, elements, app_state, files}
- `write_excalidraw_tool(path, elements, app_state)` — element types: rectangle|ellipse|text|arrow|freedraw|...
- `patch_excalidraw_tool(path, add_elements, update_elements, delete_element_ids)`

### Bases (.base files, Obsidian core plugin since 1.9.0)
- `list_bases_tool()`, `read_base_tool(path)` — returns {path, filters, formulas, properties, views}
- `write_base_tool(path, filters, formulas, properties, views)` — returns known_properties from existing bases
- `patch_base_tool(path, update_formulas, delete_formula_keys, update_properties, delete_property_keys, set_filters, add_views, update_views, delete_view_names)`

## MCP Resources
- `vault://notes/{path}` — raw note content as context
- `vault://stats` — live vault statistics
- `vault://tags` — all tags with counts

## MCP Prompts
- `weekly_review` — summarize the past week: overdue/due-soon tasks, daily note highlights
- `daily_note(date)` — open or create a daily note, carrying over yesterday's open tasks

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
- [ ] Tasks-plugin syntax: 📅 2026-08-10 due, 🔁 every week recurring, ⏫/🔼/🔽 priority
- [x] Done with a date ✅ 2026-08-01
```
Emoji markers are parsed out of `text` into their own fields (`due`, `recurrence`,
`priority`, `done_date`) by `read_note_tool`/`get_tasks_tool` — `text` itself
stays clean of the markers.

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

Each format below is only available if its tool group is enabled on the
server (see the note at the top of "## Tool Reference"). Pick the right
format for the job — they overlap in what they *can* represent, but each has
a format it's the natural fit for:

### Kanban
Frontmatter `kanban-plugin: basic`, columns as `## Name`, cards as `- [ ] text`.
Always use the kanban tools instead of editing raw Markdown.
Use it for column-based task workflows (To-do/Doing/Done, sprint boards).
For a simple checklist inside one note, plain `- [ ]` tasks via
`patch_note_tool` are lighter — don't reach for a Kanban board just to track
a handful of to-dos.

### Canvas
`.canvas` files: JSON with `nodes` (text/file/group/link) and `edges`. IDs auto-generated.
Use it for spatial/visual relationships between notes (mind maps, linking
diagrams, freeform boards). It's not a replacement for ordinary links — if
all you need is "which notes relate to this one", wikilinks or
`get_link_graph_tool` are the lighter tool.

### Excalidraw
`*.excalidraw.md` files: frontmatter `excalidraw-plugin: parsed`, drawing scene
(`elements`/`appState`/`files`) embedded as JSON in a `## Drawing` code block.
Always use the excalidraw tools instead of editing raw Markdown — the
surrounding file structure (warning banner, `## Text Elements` section) is
regenerated on every write and not meaningful to edit by hand.
Use it for freehand sketches/diagrams that go beyond boxes-and-arrows (e.g.
architecture sketches). For structured node-and-edge relationships, prefer
Canvas instead.

### Bases
`.base` files: plain YAML (not JSON, no frontmatter wrapper) with up to four
top-level keys — `filters` (boolean and/or/not tree of comparisons and
function calls like `file.hasTag("book")`), `formulas` (name -> expression),
`properties` (per-property `displayName`), `views` (list of
`{type, name, limit, filters, order, groupBy, summaries}`, `type` required —
e.g. `table`|`cards`|`list`). A Base never changes notes — it only defines a
filtered/grouped/sorted *view* over existing frontmatter properties.
Use it when the user wants a table/cards/list overview across multiple notes
that share frontmatter properties (e.g. "show me all open projects", "table
of recipes grouped by category") — not for editing a single note (use
`patch_note_tool`/`patch_frontmatter_tool` for that) and not as a substitute
for tags or links.
Before creating a new Base, call `list_bases_tool()`/`read_base_tool()` on
existing `.base` files to reuse established property names — `write_base_tool`
also returns `known_properties` collected from them, so use that rather than
inventing new names. Before filtering/grouping by a property, check that it's
written consistently across the target notes (e.g. via `query_notes_tool` or
`get_vault_conventions_tool`) — inconsistent casing/values (`"offen"` vs.
`"Offen"`) silently break filters. The server only validates that a `.base`
file has the right overall shape (mapping/list types, `views[].type` present);
it does not parse Obsidian's filter/formula expression grammar, so keep filter
strings to the documented syntax (https://obsidian.md/help/bases/syntax) —
a malformed expression won't be caught until the file is opened in Obsidian.
Use `write_base_tool` for a new Base, `patch_base_tool` for a targeted change
(add a view, adjust filters) instead of rewriting the whole file.

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
    except ConfigError:
        # Importing the module before the process environment is configured
        # is supported (tests, tooling, and FastMCP discovery).
        return _DEFAULT_INSTRUCTIONS
    if cfg.multi_vault:
        # FastMCP exposes server instructions before request middleware has
        # authenticated an identity and selected one of its allowed vaults.
        # Per-vault conventions remain available through the authorized tool.
        return _DEFAULT_INSTRUCTIONS
    try:
        vault = VaultStorage.from_config(cfg).read_text("_AI_INSTRUCTIONS.md")
    except (FileNotFoundError, PermissionError, VaultPathError, OSError):
        # Missing or unreadable optional instructions do not prevent startup;
        # descriptor/policy failures are not converted into unrestricted I/O.
        return _DEFAULT_INSTRUCTIONS
    return (
        _DEFAULT_INSTRUCTIONS
        + "\n\n# Vault-specific instructions (_AI_INSTRUCTIONS.md)\n\n"
        + vault
    )


class _APIKeyAuthProvider(TokenVerifier):
    """Static API-key auth, one or more keys. Clients must send:
    Authorization: Bearer <key>. client_id on the returned AccessToken is
    the matched key itself — in multi-vault mode that's what
    VaultResolutionMiddleware looks up in the identities table (single-key
    mode has no such lookup, so the exact client_id value doesn't matter
    there beyond being stable)."""

    def __init__(self, api_keys: list[str]) -> None:
        super().__init__()
        self._keys = api_keys

    async def verify_token(self, token: str) -> AccessToken | None:
        for key in self._keys:
            if hmac.compare_digest(token, key):
                return AccessToken(token=token, client_id=key, scopes=[])
        logger.warning("Rejected request with invalid API key")
        return None


class _RestrictedGitHubVerifier(TokenVerifier):
    """Wraps GitHubProvider's token validator to reject logins not on an allowlist.

    Runs once per GitHub token exchange (not per request) — GitHubProvider
    itself has no concept of restricting which GitHub account may authenticate,
    so any account could otherwise get full vault access.
    """

    def __init__(self, base: TokenVerifier, allowed_logins: list[str]) -> None:
        super().__init__()
        self._base = base
        self._allowed_logins = set(allowed_logins)

    async def verify_token(self, token: str) -> AccessToken | None:
        result = await self._base.verify_token(token)
        if result is None:
            return None
        login = str((result.claims or {}).get("login", "")).lower()
        if login not in self._allowed_logins:
            logger.warning("Rejected GitHub login not on allowlist: %s", login or "<unknown>")
            return None
        return result


def _identities_from_env() -> tuple[list[str], list[str]]:
    """(api_keys, allowed_github_logins) — from VAULTS_CONFIG if set, else
    the legacy single-vault env vars. Reads os.environ directly (not
    get_config()) so this module can still be imported without VAULT_PATH
    set (e.g. during testing or linting); a broken VAULTS_CONFIG here is
    swallowed (falls back to empty — no auth configured) rather than
    raised, since Config.__init__ is the actual place that validates it and
    fails loudly at real startup. Don't let a parse error here silently
    grant unauthenticated access, though — an empty result just means no
    verifier gets built at all, so the server refuses to start over the
    network per the has_api_keys/oauth_configured check in Config.__init__."""
    vaults_config_path = os.environ.get("VAULTS_CONFIG", "")
    if vaults_config_path:
        try:
            _vaults, identities = load_vaults_file(vaults_config_path)
        except ConfigError:
            return [], []
        api_keys = [i.value for i in identities if i.type == "api_key"]
        allowed_logins = [i.value for i in identities if i.type == "github_login"]
        return api_keys, allowed_logins

    key = os.environ.get("API_KEY") or os.environ.get("OBSIDIAN_MCP_API_KEY")
    api_keys = [key] if key else []
    allowed_logins = [
        login.strip().lower()
        for login in os.environ.get("OAUTH_GITHUB_ALLOWED_LOGINS", "").split(",")
        if login.strip()
    ]
    return api_keys, allowed_logins


def _build_auth() -> AuthProvider | None:
    api_keys, allowed_logins = _identities_from_env()
    api_key_verifier = _APIKeyAuthProvider(api_keys) if api_keys else None

    client_id = os.environ.get("OAUTH_GITHUB_CLIENT_ID")
    client_secret = os.environ.get("OAUTH_GITHUB_CLIENT_SECRET")
    github_provider = None
    if client_id and client_secret:
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

        # Client registrations + encrypted tokens persist under FastMCP's own
        # data directory (FASTMCP_HOME, defaults to a platformdirs path).
        # Mount that directory as a volume in Docker, or set FASTMCP_HOME to a
        # path inside an existing mount, or logins won't survive a restart.
        github_provider = GitHubProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            # Otherwise every MCP request waits on GitHub /user and /user/repos.
            cache_ttl_seconds=300,
        )
        # Relies on OAuthProxy's private _token_validator attribute — the only
        # hook that runs at token-exchange time, before an allowlist check
        # could otherwise happen. May break on a fastmcp upgrade; if it does,
        # this will raise AttributeError loudly at startup rather than
        # silently allowing any GitHub account through.
        github_provider._token_validator = _RestrictedGitHubVerifier(
            github_provider._token_validator, allowed_logins
        )
        logger.info("GitHub OAuth enabled (allowed logins: %s)", ", ".join(allowed_logins))

    if github_provider and api_key_verifier:
        logger.info("API key auth enabled (alongside GitHub OAuth)")
        # required_scopes must be cleared here: MultiAuth defaults it to the
        # server's (GitHubProvider's, i.e. ["user"]) and enforces it across
        # every verifier. _APIKeyAuthProvider's tokens carry scopes=[] since
        # a static key has no OAuth scopes, so without this override every
        # API-key request would fail with "insufficient_scope" even though
        # the key itself checked out.
        return MultiAuth(server=github_provider, verifiers=[api_key_verifier], required_scopes=[])
    if github_provider:
        return github_provider
    if api_key_verifier:
        logger.info("API key auth enabled")
        return api_key_verifier
    return None


class _CurrentVaultIndex:
    """Every existing @mcp.tool()/@mcp.resource() call site passes/uses
    `_index` as a single VaultIndex — this proxy lets that keep working
    completely unchanged even though there's now one VaultIndex per
    configured vault. Attribute access is forwarded to whichever vault's
    index the current context resolves to (contextvars, set per tool call
    by VaultResolutionMiddleware in multi-vault mode; the single configured
    vault otherwise)."""

    def __getattr__(self, name: str):
        vault_name = get_config().resolve_vault_name()
        return getattr(_indices[vault_name], name)


def _identity_for_token(cfg, access_token: AccessToken | None) -> object:
    """Find the Identity (config.Identity) matching a given AccessToken —
    GitHub login if the token carries one (OAuth), else the API key itself
    (client_id, see _APIKeyAuthProvider). Raises PermissionError if there's
    no token or no VAULTS_CONFIG entry for it — never silently falls back
    to some default vault for an identity nothing was configured for.

    Split out from _resolve_identity() (which reads the token from FastMCP's
    request context via get_access_token()) so callers that already have an
    AccessToken in hand from doing their own manual verification — the
    /attachments/* custom route, which bypasses FastMCP's normal auth
    middleware entirely — can resolve an identity too, without needing
    get_access_token() to work in a context it doesn't cover.
    """
    if access_token is None:
        raise PermissionError("No authenticated identity available to resolve a vault for")

    login = str((access_token.claims or {}).get("login", "")).lower()
    candidates = [("github_login", login)] if login else []
    candidates.append(("api_key", access_token.client_id))

    for itype, value in candidates:
        if not value:
            continue
        for identity in cfg.identities:
            if identity.type == itype and identity.value == value:
                return identity

    raise PermissionError("This identity has no vault access configured in VAULTS_CONFIG")


def _resolve_identity(cfg) -> object:
    """_identity_for_token() using the current request's AccessToken from
    FastMCP's own context (get_access_token()) — the normal case for
    everything that goes through MCP tool-call/resource-read dispatch."""
    return _identity_for_token(cfg, get_access_token())


def _select_vault(identity, requested: str | None) -> str:
    """Which vault an identity's call actually resolves to.

    requested=None (no vault= argument given): the identity's configured
    "default" — every identity has one automatically if it only has a
    single allowed vault (see load_vaults_file); an identity with several
    allowed vaults and no explicit "default" in VAULTS_CONFIG must pass
    vault= explicitly, every single call, rather than have the server
    silently guess which one it means.
    requested=<name>: must be one of the identity's allowed vaults.
    """
    if requested is None:
        if identity.default is None:
            raise PermissionError(
                f"This identity has access to multiple vaults ({identity.vaults}) and no "
                "default is configured — pass vault=<name> explicitly on this call"
            )
        return identity.default
    if requested not in identity.vaults:
        raise PermissionError(f"This identity does not have access to vault {requested!r}")
    return requested


class VaultResolutionMiddleware(Middleware):
    """In multi-vault mode, resolves which vault a tool call/resource read
    operates on and sets the current-vault contextvar for its duration —
    every tools/*.py function then transparently sees the right
    get_config().vault_path/write_paths/exclude_paths/read_only and the
    right VaultIndex (via _CurrentVaultIndex above), without any of them
    needing to know multi-vault exists. A no-op pass-through in
    single-vault mode (VAULTS_CONFIG unset).

    Every @mcp.tool() has an optional `vault: str | None = None` parameter
    (mechanical addition, not used by the tool bodies themselves — this
    middleware is the only thing that reads it, from the raw call
    arguments, before the tool function ever runs). Omit it to use the
    calling identity's default vault; pass it to operate on a different one
    of that identity's allowed vaults for just this one call. See
    list_vaults_tool for discovering which vaults + which one is default
    for the current identity. Resource reads (on_read_resource) have no
    such parameter to read (ReadResourceRequestParams carries a uri, not
    tool-style arguments) and always use the identity's default.

    Not covered here: /health doesn't go through MCP tool-call dispatch, so
    this middleware never runs for it — it stays scoped to
    Config.default_vault_name regardless of which identity is calling (it
    exposes no vault content, just liveness, so this doesn't leak anything).
    /attachments/* is also a custom route rather than tool-call dispatch,
    but does its own equivalent vault resolution by hand — see
    attachment_route below — since it's the one HTTP route that genuinely
    needs it.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        cfg = get_config()
        if not cfg.multi_vault:
            return await call_next(context)

        identity = _resolve_identity(cfg)
        if getattr(context.message, "name", None) == "list_vaults_tool":
            # This identity-only discovery call does not touch vault content.
            # In particular, it must work for identities intentionally
            # configured with several vaults and no default.
            return await call_next(context)
        requested = None
        arguments = getattr(context.message, "arguments", None)
        if arguments:
            requested = arguments.get("vault")
        vault_name = _select_vault(identity, requested)

        token = set_current_vault(vault_name)
        try:
            return await call_next(context)
        finally:
            reset_current_vault(token)

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        cfg = get_config()
        if not cfg.multi_vault:
            return await call_next(context)

        identity = _resolve_identity(cfg)
        vault_name = _select_vault(identity, None)

        token = set_current_vault(vault_name)
        try:
            return await call_next(context)
        finally:
            reset_current_vault(token)


# Initialized in main() — empty at import time so the module can be imported
# without VAULT_PATH/VAULTS_CONFIG set (e.g. during testing or linting).
_cfg = None
_indices: dict[str, VaultIndex] = {}
_watchers: dict[str, VaultWatcher] = {}
_index = _CurrentVaultIndex()

mcp = FastMCP(name="obsidian-mcp", instructions=_load_instructions(), auth=_build_auth())
mcp.add_middleware(VaultResolutionMiddleware())


# Gates which optional plugin-format and high-impact mutation tool groups get
# registered below, so disabled tools never appear in the client's tool list.
# Deliberately not the real Config object (that needs VAULT_PATH,
# see _build_auth() above) — just the feature flags, read straight from the
# environment.
@dataclass(frozen=True)
class _FeatureFlags:
    enable_canvas: bool
    enable_excalidraw: bool
    enable_kanban: bool
    enable_bases: bool
    enable_move: bool
    enable_folder_rename: bool
    enable_bulk_replace: bool
    enable_delete: bool

    @classmethod
    def from_env(cls) -> _FeatureFlags:
        """Read named flags without constructing the full vault config."""
        def enabled(name: str) -> bool:
            return os.environ.get(name, "false").lower() in ("1", "true", "yes")

        return cls(
            enable_canvas=enabled("ENABLE_CANVAS"),
            enable_excalidraw=enabled("ENABLE_EXCALIDRAW"),
            enable_kanban=enabled("ENABLE_KANBAN"),
            enable_bases=enabled("ENABLE_BASES"),
            enable_move=enabled("ENABLE_MOVE"),
            enable_folder_rename=enabled("ENABLE_FOLDER_RENAME"),
            enable_bulk_replace=enabled("ENABLE_BULK_REPLACE"),
            enable_delete=enabled("ENABLE_DELETE"),
        )


_feature_flags = _FeatureFlags.from_env()


# ── Prompts ───────────────────────────────────────────────────────────────────

@mcp.prompt()
def weekly_review() -> str:
    """Summarize the past week: overdue/due-soon tasks, daily note highlights."""
    return weekly_review_prompt()


@mcp.prompt()
def daily_note(date: str = "today") -> str:
    """Open or create a daily note, carrying over yesterday's open tasks.
    date: 'today' | 'yesterday' | 'YYYY-MM-DD'."""
    return daily_note_prompt(date=date)


# ── Read ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_notes_tool(folder: str = "", include_meta: bool = False, vault: str | None = None) -> list:
    """List all Markdown notes in the vault (or a subfolder).
    Set include_meta=True to get title, tags, status, created per note."""
    return list_notes(folder, include_meta=include_meta)


@mcp.tool()
def read_note_tool(path: str, vault: str | None = None) -> dict:
    """Read a note – returns content, frontmatter, tags, aliases, wikilinks,
    block_refs, callouts, and tasks."""
    return read_note(path)


@mcp.tool()
def search_notes_tool(
    query: str,
    tag: str | None = None,
    mode: str = "exact",
    limit: int = 20,
    frontmatter_filter: dict | None = None,
    field: str | None = None,
    threshold: float = 0.8,
    vault: str | None = None,
) -> list[dict]:
    """Full-text search with snippets and relevance ranking.
    mode: 'exact' (default) | 'regex' | 'fuzzy'. Returns [{path, score, snippets, tags}].
    frontmatter_filter: combine with the text search in one call — same shape
    as query_notes_tool's (plain value = exact match, or {"$ne": v} /
    {"$nin": [...]} / {"$exists": bool}).
    field: None/'body' (default, search note content) | 'filename' (match
    only the file name).
    threshold: fuzzy-match similarity cutoff 0-1 (only used when mode='fuzzy';
    lower = looser matches, higher = less noise)."""
    return search_notes(
        query, tag=tag, mode=mode, limit=limit,
        frontmatter_filter=frontmatter_filter, field=field, threshold=threshold,
    )


@mcp.tool()
def render_note_tool(path: str, depth: int = 1, vault: str | None = None) -> str:
    """Read a note with all ![[embed]] transclusions resolved inline.
    depth: 0=raw, 1=one level of embeds (default), 2=nested embeds."""
    return render_note(path, depth=depth)


@mcp.tool()
def get_note_outline_tool(path: str, vault: str | None = None) -> dict:
    """Return the structural map of a note without its body text.
    Returns {headings, block_refs, frontmatter_keys, tags, word_count, line_count}.
    Efficient for large notes where you only need structure."""
    return get_note_outline(path)


@mcp.tool()
def find_similar_notes_tool(
    text: str,
    limit: int = 5,
    exclude_path: str | None = None,
    min_score: float = 0.1,
    vault: str | None = None,
) -> list[dict]:
    """Find conceptually related notes even when the wording differs — for
    duplicate prevention before creating a new note ("does this topic
    already exist under different vocabulary?"). Ranks by TF-IDF cosine
    similarity over the vault's own vocabulary (a lightweight heuristic,
    not a transformer embedding model — it catches shared distinctive
    words across differently-phrased notes, not pure synonym rewrites).
    exclude_path: skip a note (e.g. the one you're editing) from results.
    min_score: filters out noise-level matches (0-1, higher = stricter).
    Returns [{path, score}], most similar first."""
    return find_similar_notes(text, _index, limit=limit, exclude_path=exclude_path, min_score=min_score)


# ── Write ─────────────────────────────────────────────────────────────────────

@mcp.tool()
@_mutation_boundary
def write_note_tool(
    path: str,
    content: str,
    dry_run: bool = False,
    expected_revision: str | None = None,
    create_only: bool = False,
    vault: str | None = None,
) -> dict:
    """Write (create or overwrite) a note. Respects READ_ONLY and WRITE_PATHS.
    If `content` has no frontmatter of its own and a note already exists at
    `path`, its existing frontmatter is preserved rather than dropped —
    check the returned `frontmatter_preserved` flag. The result also carries
    a `diff` (unified diff against the current file).
    dry_run=True previews {preview, diff, frontmatter_preserved} without
    writing anything — check it, then call again with dry_run=False."""
    result = write_note(
        path,
        content,
        index=_index,
        dry_run=dry_run,
        expected_revision=expected_revision,
        create_only=create_only,
    )
    if result.get("status") != "dry_run":
        log_write("write_note_tool", path, "wrote note")
    return result


@mcp.tool()
@_mutation_boundary
def patch_note_tool(
    path: str,
    section: str,
    new_content: str,
    mode: str = "replace",
    target_type: str = "heading",
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict:
    """Edit a section or block reference inside a note.
    mode: 'replace' (default) | 'insert_before' | 'insert_after' | 'append'.
    target_type: 'heading' (default) | 'block_ref' (use section='^block-id')."""
    result = patch_note(
        path,
        section,
        new_content,
        mode=mode,
        target_type=target_type,
        index=_index,
        expected_revision=expected_revision,
    )
    log_write("patch_note_tool", path, f"patched section {section!r} ({mode})")
    return result


@mcp.tool()
@_mutation_boundary
def patch_note_text_tool(
    path: str,
    find: str,
    replace: str,
    mode: str = "exact",
    count: int = 1,
    dry_run: bool = False,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict:
    """Find and replace text anywhere in one note's body — no heading/block-ref
    anchor required, unlike patch_note_tool. Cheaper than write_note_tool for
    a scattered single-note edit (e.g. bumping one enum value inside a long note).
    mode: 'exact' (default, literal substring) | 'regex'.
    count: max replacements (default 1, first match only); 0 = replace all.
    dry_run=True previews {replacements, preview, diff} without writing.
    Raises ValueError if `find` doesn't match anything."""
    result = patch_note_text(
        path,
        find,
        replace,
        mode=mode,
        count=count,
        dry_run=dry_run,
        index=_index,
        expected_revision=expected_revision,
    )
    if result.get("status") != "dry_run":
        log_write("patch_note_text_tool", path, f"replaced {result.get('replacements')} match(es)")
    return result


@_mutation_boundary
def delete_note_tool(
    path: str,
    trash: bool = True,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict:
    """Delete a note from the vault.
    trash=True (default) moves it to .trash/ instead of permanent deletion."""
    result = delete_note(path, trash=trash, index=_index, expected_revision=expected_revision)
    log_write("delete_note_tool", path, f"deleted (trash={trash})")
    return result


if _feature_flags.enable_delete:
    mcp.tool()(delete_note_tool)


def restore_note_tool(trashed_name: str, to_path: str, vault: str | None = None) -> dict:
    """Restore a note previously moved to .trash/ (see list_trash_tool for names).
    to_path: where to put it back — the original folder can't be recovered
    from the trash entry alone, so you choose the destination.
    Returns {from, to, status}."""
    result = restore_note(trashed_name, to_path, index=_index)
    log_write("restore_note_tool", to_path, f"restored from .trash/{trashed_name}")
    return result


if _feature_flags.enable_delete:
    mcp.tool()(restore_note_tool)


def find_replace_in_vault_tool(
    search: str,
    replace: str,
    mode: str = "exact",
    folder: str = "",
    dry_run: bool = True,
    vault: str | None = None,
) -> dict:
    """Find and replace text across every note in the vault (or a subfolder).
    mode: 'exact' (default, literal substring) | 'regex'.
    dry_run=True (default) only previews matches — {matches: [{path, match_count, preview}], total_matches}.
    Always run once with dry_run=True first, then dry_run=False to actually write.
    .trash/ and EXCLUDE_PATHS are always skipped; write-protected files
    (READ_ONLY or outside WRITE_PATHS) are skipped and listed under
    skipped_write_protected rather than aborting the whole run.
    Returns {replaced_in, total_replacements, skipped_write_protected} when dry_run=False."""
    result = find_replace_in_vault(search, replace, mode=mode, folder=folder, dry_run=dry_run, index=_index)
    if not result.get("dry_run"):
        log_write(
            "find_replace_in_vault_tool",
            folder or None,
            f"replaced in {len(result.get('replaced_in', []))} note(s)",
        )
    return result


if _feature_flags.enable_bulk_replace:
    mcp.tool()(find_replace_in_vault_tool)


@mcp.tool()
@_mutation_boundary
def append_to_note_tool(
    path: str,
    content: str,
    section: str | None = None,
    create: bool = True,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict:
    """Append content to a note without reading and rewriting the whole file.
    section: optional heading to append under. create=True creates the note if missing."""
    result = append_to_note(
        path,
        content,
        section=section,
        create=create,
        index=_index,
        expected_revision=expected_revision,
    )
    log_write("append_to_note_tool", path, "appended content" + (f" under {section!r}" if section else ""))
    return result


@mcp.tool()
@_mutation_boundary
def patch_frontmatter_tool(
    path: str,
    updates: dict,
    merge_arrays: bool = True,
    dry_run: bool = False,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict:
    """Update specific YAML frontmatter keys without touching the note body.
    merge_arrays=True merges list values (e.g. tags); False replaces them.
    Result carries a `diff` (unified diff against the current file).
    dry_run=True previews {preview, diff, updated_keys} without writing —
    check it, then call again with dry_run=False."""
    result = patch_frontmatter(
        path,
        updates,
        merge_arrays=merge_arrays,
        index=_index,
        dry_run=dry_run,
        expected_revision=expected_revision,
    )
    if result.get("status") != "dry_run":
        log_write("patch_frontmatter_tool", path, f"updated keys: {list(updates.keys())}")
    return result


@mcp.tool()
def patch_frontmatter_batch_tool(updates: list[dict], vault: str | None = None) -> dict:
    """Patch frontmatter on multiple notes in one call.
    updates: list of {"path": str, "updates": dict, "merge_arrays": bool}
    (merge_arrays defaults to True per entry). One entry failing doesn't
    abort the rest — returns {succeeded: [...], failed: [{path, error}]}."""
    result = patch_frontmatter_batch(updates, index=_index)
    for entry in result.get("succeeded", []):
        log_write("patch_frontmatter_batch_tool", entry.get("path"), f"updated keys: {entry.get('updated_keys')}")
    return result


@mcp.tool()
@_mutation_boundary
def manage_tags_tool(
    path: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    expected_revision: str | None = None,
    vault: str | None = None,
) -> dict:
    """Add or remove tags on a note. Updates frontmatter tags array and strips
    inline #tag occurrences from the body. Returns {added, removed}."""
    result = manage_tags(
        path, add=add, remove=remove, index=_index, expected_revision=expected_revision
    )
    log_write("manage_tags_tool", path, f"+{add or []} -{remove or []}")
    return result


def move_note_tool(from_path: str, to_path: str, vault: str | None = None) -> dict:
    """Rename or move a note. Automatically rewrites all wikilinks in the vault
    that reference the old path. Returns {from, to, updated_links_in}."""
    result = move_note(from_path, to_path, index=_index)
    log_write("move_note_tool", to_path, f"moved from {from_path}")
    return result


if _feature_flags.enable_move:
    mcp.tool()(move_note_tool)


# ── Query / Graph ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_backlinks_tool(path: str, vault: str | None = None) -> list[str]:
    """Return all notes that link to the given note (alias-aware)."""
    return get_backlinks(path, _index)


@mcp.tool()
def get_notes_by_tag_tool(tag: str, vault: str | None = None) -> list[str]:
    """Return all notes that have the given tag."""
    return get_notes_by_tag(tag, _index)


@mcp.tool()
def get_vault_conventions_tool(vault: str | None = None) -> str:
    """Return the vault's AI instructions / conventions from _AI_INSTRUCTIONS.md."""
    return get_vault_conventions()


@mcp.tool()
def get_audit_log_tool(
    path: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    limit: int = 50,
    vault: str | None = None,
) -> list[dict]:
    """Query the append-only log of write-tool activity (who/what changed,
    not just the .trash/ state after the fact). Most recent first.
    path/tool/since are optional filters (since: ISO timestamp, inclusive).
    Entries: {timestamp, tool, path, summary}. Covers the core note/folder
    write tools; canvas/kanban/excalidraw/bases writes aren't logged yet."""
    return get_audit_log(path=path, tool=tool, since=since, limit=limit)


@mcp.tool()
def get_note_history_tool(path: str, limit: int = 20, vault: str | None = None) -> list[dict]:
    """Audit-log entries for one specific note, most recent first —
    what changed and when, without needing to know which tool was used."""
    return get_note_history(path, limit=limit)


@mcp.tool()
def list_vaults_tool() -> list[dict]:
    """List the vault(s) the current identity (API key or GitHub login) may
    access. Returns [{name, description, is_default}]. Call this at the
    start of a session whenever more than one vault comes back — pass
    vault=<name> on any other tool to operate on a non-default one for that
    single call; omit it to use whichever entry has is_default=true. In
    single-vault mode (no VAULTS_CONFIG) this always returns exactly one
    entry with is_default=true — there's nothing to choose between."""
    cfg = get_config()
    if not cfg.multi_vault:
        vault = cfg.vaults[cfg.default_vault_name]
        return [{"name": vault.name, "description": vault.description, "is_default": True}]

    identity = _resolve_identity(cfg)
    return [
        {
            "name": name,
            "description": cfg.vaults[name].description,
            "is_default": name == identity.default,
        }
        for name in identity.vaults
    ]


@mcp.tool()
def lint_schema_tool(vault: str | None = None) -> dict:
    """Validate every note's frontmatter against the enum fields declared in
    the vault's _AI_INSTRUCTIONS.md (under a "Frontmatter Schema" heading,
    e.g. `status: inbox | active | done | archived`). Returns
    {schema, violations: [{path, field, found, expected_enum}]} — only the
    deviations, not a full vault dump. A field that's simply missing on a
    note isn't a violation, only a present value outside the declared enum
    is. Returns an empty schema/violations pair if no enum schema can be
    parsed from _AI_INSTRUCTIONS.md."""
    return lint_schema(_index)


@mcp.tool()
def get_broken_links_tool(vault: str | None = None) -> list[dict]:
    """Find all wikilinks in the vault that point to non-existent notes.
    Returns [{source, link}]."""
    return get_broken_links(_index)


@mcp.tool()
def get_orphans_tool(exclude_folders: list[str] | None = None, vault: str | None = None) -> list[str]:
    """Find notes that no other note links to.
    Excludes Journal and Templates by default."""
    return get_orphans(_index, exclude_folders=exclude_folders or ["Journal", "Templates"])


@mcp.tool()
def get_link_graph_tool(
    root: str,
    depth: int = 2,
    direction: str = "both",
    vault: str | None = None,
) -> dict:
    """Return a traversable link graph starting from a note.
    direction: 'outgoing' | 'incoming' | 'both'.
    Returns {root, nodes: [{path, title, tags}], edges: [{from, to, type}]}."""
    return get_link_graph(root, _index, depth=depth, direction=direction)


@mcp.tool()
def get_vault_stats_tool(vault: str | None = None) -> dict:
    """Return vault statistics: note count, link count, orphans, broken links,
    most-linked notes."""
    return get_vault_stats(_index)


@mcp.tool()
def get_tag_tree_tool(vault: str | None = None) -> dict:
    """Return all tags as a nested tree (e.g. konzept → python, ki → llm)."""
    return get_tag_tree(_index)


@mcp.tool()
def list_all_tags_tool(sort_by: str = "count", vault: str | None = None) -> list[dict]:
    """Return all tags in the vault with note counts.
    sort_by: 'count' (descending, default) | 'name' (alphabetical).
    Returns [{tag, count}]."""
    return list_all_tags(_index, sort_by=sort_by)


@mcp.tool()
def get_tasks_tool(
    status: str = "open",
    folder: str = "",
    tag: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    vault: str | None = None,
) -> list[dict]:
    """Return tasks from across the vault.
    status: 'open' | 'done' | 'all'. Optionally filter by folder or tag.
    due_before/due_after: 'YYYY-MM-DD', inclusive; matches the Tasks-plugin
    📅 due date (tasks without one never match either filter).
    Parses Tasks-plugin emoji markers: 📅 due, ✅ done date, 🔁 recurrence,
    ⏫/🔼/🔽 priority (high/medium/low) — stripped from `text` into their own fields.
    Returns [{text, done, source, line, due, recurrence, priority, done_date}]."""
    return get_tasks(_index, status=status, folder=folder, tag=tag, due_before=due_before, due_after=due_after)


@mcp.tool()
def get_daily_note_tool(date: str = "today", vault: str | None = None) -> dict:
    """Read a daily note from Journal/.
    date: 'today' | 'yesterday' | 'YYYY-MM-DD'.
    Returns {path, exists, content, frontmatter, tasks}."""
    return get_daily_note(_index, date_str=date)


@mcp.tool()
def get_periodic_note_tool(period: str = "daily", date: str = "today", vault: str | None = None) -> dict:
    """Read or preview a periodic note.
    period: 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'yearly'.
    date: 'today' | 'yesterday' | 'YYYY-MM-DD'.
    Returns {path, period, date, exists, content, frontmatter, tasks}."""
    return get_periodic_note(_index, period=period, date_str=date)


@mcp.tool()
def resolve_alias_tool(name: str, vault: str | None = None) -> str | None:
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
    vault: str | None = None,
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
def list_attachments_tool(folder: str = "", vault: str | None = None) -> list[dict]:
    """List all non-Markdown files in the vault: images, PDFs, audio, etc.
    Returns [{path, size_bytes, mime_type, mtime}]."""
    return list_attachments(folder)


@mcp.tool()
def read_attachment_tool(path: str, vault: str | None = None) -> dict:
    """Read an attachment file. Text files returned as UTF-8 string.
    Binary files (images, PDFs) returned as base64-encoded content with mime_type."""
    return read_attachment(path)


@mcp.tool()
def add_attachment_tool(path: str, content_base64: str, vault: str | None = None) -> dict:
    """Write a binary attachment (image, PDF, etc.) to the vault from base64-encoded content.
    Returns {path, status, size_bytes, mime_type}."""
    return add_attachment(path, content_base64)


@mcp.tool()
def create_attachment_token_tool(path: str, method: str = "PUT", expires_in: int = 300, vault: str | None = None) -> dict:
    """Create a short-lived, single-file upload/download token for the
    GET/PUT /attachments/{path} HTTP route, instead of handing out the
    server's master API_KEY. method: 'PUT' (upload) or 'GET' (download).
    expires_in: seconds until the token expires (default 300, max 3600).

    Returns {path, method, vault, expires_at, sig, url?}. If the server has
    PUBLIC_BASE_URL configured, `url` is the ready-to-use request URL — use
    it as-is, it already has everything this token needs (including
    ?vault= when relevant) baked in. Otherwise build it yourself as:
        curl -X PUT --data-binary @file.png \\
            "http://host:port/attachments/{path}?exp={expires_at}&sig={sig}&vault={vault}"
    (omit &vault=... only if the returned `vault` equals the server's
    single/default vault — see list_vaults_tool). The token only authorizes
    this exact path + method + vault and stops working after expires_at. In
    multi-vault mode, requires an api_key identity — GitHub-OAuth identities
    have no static secret of their own to sign with; use a plain
    Authorization: Bearer request against /attachments/* instead (that path
    works for any identity type, but needs ?vault=<name> added by hand if
    targeting a non-default vault, since nothing was pre-signed for it)."""
    cfg = get_config()
    vault_name = cfg.resolve_vault_name()
    if cfg.multi_vault:
        identity = _resolve_identity(cfg)
        if identity.type != "api_key":
            raise PermissionError(
                "Scoped attachment tokens require an api_key identity — this request "
                "authenticated as a github_login, which has no static secret to sign "
                "with. Use a plain 'Authorization: Bearer <token>' request against "
                "/attachments/* instead."
            )
        signing_key = identity.value
    else:
        signing_key = cfg.api_key
        if not signing_key:
            raise ValueError("API_KEY is not configured on this server; attachment tokens require it")
    return create_attachment_token(
        path, signing_key=signing_key, vault=vault_name, method=method, expires_in=expires_in
    )


async def _check_bearer_token(request: Request, cfg) -> AccessToken | None:
    """Returns the AccessToken for a valid Authorization: Bearer header, or
    None if missing/invalid. Manual verification, not FastMCP's own auth
    middleware — this is a @mcp.custom_route, which bypasses that pipeline
    entirely, so get_access_token() won't see anything for this request."""
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return None
    if cfg.api_key and hmac.compare_digest(token, cfg.api_key):
        return AccessToken(token=token, client_id=cfg.api_key, scopes=[])
    if mcp.auth is not None:
        return await mcp.auth.verify_token(token)
    return None


def _check_scoped_token(request: Request, cfg, method: str, path: str) -> str | None:
    """Returns the vault name a valid scoped token (?exp=&sig=[&vault=])
    grants access to for this exact path+method, or None if missing/
    invalid/expired. Tries every api_key identity's own value as the HMAC
    signing key in multi-vault mode (there's no bearer header here to say
    up front which identity minted it), or the single global API_KEY in
    single-vault mode. A key matching the signature but not actually
    allowed the requested vault is treated the same as no match."""
    exp = request.query_params.get("exp")
    sig = request.query_params.get("sig")
    if not exp or not sig:
        return None
    vault_param = request.query_params.get("vault", cfg.default_vault_name)

    if cfg.multi_vault:
        candidates = [(identity.value, identity) for identity in cfg.identities if identity.type == "api_key"]
    else:
        candidates = [(cfg.api_key, None)] if cfg.api_key else []

    for signing_key, identity in candidates:
        if not verify_attachment_token(signing_key, method, path, vault_param, exp, sig):
            continue
        if identity is not None and vault_param not in identity.vaults:
            continue
        return vault_param
    return None


@mcp.custom_route("/health", methods=["GET"])
async def health_route(request: Request) -> Response:
    """Unauthenticated liveness/readiness check for Docker HEALTHCHECK,
    uptime monitors, etc. Returns no vault content, so no auth is required.

    Returns {status: "starting"|"ok", index_ready, reconciliation telemetry}.
    Returns 503 until the initial index build and vault-wide reconciliation
    finish, then 200 while the built index remains usable. A later per-note
    reconciliation error is exposed as telemetry without discarding readiness.
    In multi-vault mode, telemetry is for Config.default_vault_name because
    this unauthenticated route exposes no per-identity vault details.
    """
    if _cfg is None or not _indices:
        return JSONResponse({"status": "starting"}, status_code=503)
    index = _indices[_cfg.default_vault_name]
    ready = index.is_ready()
    return JSONResponse(
        {
            "status": "ok" if ready else "starting",
            "index_ready": ready,
            **index.reconcile_status(),
        },
        status_code=200 if ready else 503,
    )


@mcp.custom_route("/attachments/{path:path}", methods=["PUT", "GET"])
async def attachment_route(request: Request) -> Response:
    """Direct binary upload/download, outside the MCP tool-call channel.

    add_attachment_tool/read_attachment_tool move file content as base64
    inside a tool call/result, which forces the bytes through whatever
    client/model is driving the MCP session — expensive and risky for large
    or many files. This route lets a client PUT/GET raw bytes straight to/from
    disk instead. Accepts the server's static bearer token, a valid GitHub
    OAuth access token (if configured), or a short-lived scoped token from
    create_attachment_token_tool (?exp=&sig=), so callers never need to be
    handed the long-lived master key.

    Usage:
        curl -X PUT --data-binary @file.png \\
            -H "Authorization: Bearer <API_KEY>" http://host:port/attachments/path/to/file.png
        curl -o file.png \\
            -H "Authorization: Bearer <API_KEY>" http://host:port/attachments/path/to/file.png

    Multi-vault mode: this route doesn't go through VaultResolutionMiddleware
    (it's a plain Starlette route, not MCP tool-call dispatch), so vault
    resolution is done by hand here — a bearer token resolves to its
    identity's default vault (override with ?vault=<name>, same rule as the
    vault= tool argument: must be one of that identity's allowed vaults); a
    scoped token from create_attachment_token_tool carries its vault baked
    into the signature already.
    """
    cfg = get_config()
    path = request.path_params["path"]
    method = request.method

    # Authenticate and choose a vault before any path-policy check so callers
    # cannot probe protected path boundaries through response differences.
    vault_name: str | None = None
    access_token = await _check_bearer_token(request, cfg)
    if access_token is not None:
        if cfg.multi_vault:
            try:
                identity = _identity_for_token(cfg, access_token)
                requested = request.query_params.get("vault")
                vault_name = _select_vault(identity, requested)
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=403)
        else:
            vault_name = cfg.default_vault_name
    else:
        vault_name = _check_scoped_token(request, cfg, method, path)

    if vault_name is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    context_token = set_current_vault(vault_name)
    try:
        storage = VaultStorage.from_config(cfg)
        try:
            path = validate_attachment_path(path, write=method == "PUT")
        except InvalidFileTypeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (VaultPathError, ReadPermissionError, WritePermissionError):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        if method == "GET":
            try:
                data = storage.read_bytes(path)
            except FileNotFoundError:
                return JSONResponse({"error": "Attachment not found"}, status_code=404)
            mime, _ = mimetypes.guess_type(path)
            return Response(data, media_type=mime or "application/octet-stream")

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
            if declared_length < 0:
                return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
            if declared_length > cfg.max_attachment_bytes:
                return JSONResponse({"error": "Attachment too large"}, status_code=413)

        chunks: list[bytes] = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > cfg.max_attachment_bytes:
                return JSONResponse({"error": "Attachment too large"}, status_code=413)
            chunks.append(chunk)
        data = b"".join(chunks)
        try:
            result = write_attachment_bytes(path, data)
        except AttachmentTooLargeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=413)
        except (ValueError, InvalidFileTypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (VaultPathError, WritePermissionError):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse(result)
    finally:
        reset_current_vault(context_token)


# ── Templates ─────────────────────────────────────────────────────────────────

@mcp.tool()
def list_templates_tool(vault: str | None = None) -> list[str]:
    """List all template files in the Templates/ folder."""
    return list_templates()


@mcp.tool()
@_mutation_boundary
def create_from_template_tool(
    template_path: str,
    output_path: str,
    variables: dict | None = None,
    expected_revision: str | None = None,
    create_only: bool = False,
    vault: str | None = None,
) -> dict:
    """Render a template and write it as a new note.
    Built-in variables: {{date}}, {{time}}, {{title}}, {{week}}, {{month}}, {{year}}, {{weekday}}.
    Supports format specs: {{date:YYYY-MM}} → '2026-07'.
    Custom variables passed in 'variables' dict override built-ins.
    Unknown {{vars}} are preserved as-is."""
    return create_from_template(
        template_path,
        output_path,
        variables=variables,
        index=_index,
        expected_revision=expected_revision,
        create_only=create_only,
    )


# ── Canvas ────────────────────────────────────────────────────────────────────

if _feature_flags.enable_canvas:

    @mcp.tool()
    def list_canvases_tool(vault: str | None = None) -> list[str]:
        """List all Obsidian Canvas (.canvas) files in the vault."""
        return list_canvases()

    @mcp.tool()
    def read_canvas_tool(path: str, vault: str | None = None) -> dict:
        """Read an Obsidian Canvas file.
        Returns {path, nodes: [{id, type, text, file, x, y}], edges: [{from, to, label}]}."""
        return read_canvas(path)

    @mcp.tool()
    @_mutation_boundary
    def write_canvas_tool(
        path: str,
        nodes: list[dict] | None = None,
        edges: list[dict] | None = None,
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create or fully overwrite an Obsidian Canvas file.
        Node fields: type ('text'|'file'|'group'|'link'), x, y, width, height.
        Text nodes: text. File nodes: file (vault path). Link nodes: url.
        Edge fields: fromNode, toNode, label (optional). IDs are auto-generated if omitted.
        Returns {path, status, nodes, edges}."""
        return write_canvas(
            path,
            nodes=nodes,
            edges=edges,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def patch_canvas_tool(
        path: str,
        add_nodes: list[dict] | None = None,
        update_nodes: list[dict] | None = None,
        delete_node_ids: list[str] | None = None,
        add_edges: list[dict] | None = None,
        delete_edge_ids: list[str] | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
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
            expected_revision=expected_revision,
        )


# ── Excalidraw ────────────────────────────────────────────────────────────────

if _feature_flags.enable_excalidraw:

    @mcp.tool()
    def list_excalidraw_tool(vault: str | None = None) -> list[str]:
        """List all Obsidian Excalidraw (*.excalidraw.md) files in the vault."""
        return list_excalidraw()

    @mcp.tool()
    def read_excalidraw_tool(path: str, vault: str | None = None) -> dict:
        """Read an Obsidian Excalidraw file.
        Returns {path, elements, app_state, files}."""
        return read_excalidraw(path)

    @mcp.tool()
    @_mutation_boundary
    def write_excalidraw_tool(
        path: str,
        elements: list[dict] | None = None,
        app_state: dict | None = None,
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create or fully overwrite an Excalidraw file.
        Element fields: type ('rectangle'|'ellipse'|'text'|'arrow'|'freedraw'|...), x, y,
        width, height. Element 'id' is auto-generated if omitted.
        Returns {path, status, elements}."""
        return write_excalidraw(
            path,
            elements=elements,
            app_state=app_state,
            index=_index,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def patch_excalidraw_tool(
        path: str,
        add_elements: list[dict] | None = None,
        update_elements: list[dict] | None = None,
        delete_element_ids: list[str] | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Atomically update an existing Excalidraw file without rewriting the whole file.
        update_elements: each dict must include 'id'.
        Returns {path, status, elements}."""
        return patch_excalidraw(
            path,
            add_elements=add_elements,
            update_elements=update_elements,
            delete_element_ids=delete_element_ids,
            index=_index,
            expected_revision=expected_revision,
        )


# ── Kanban ────────────────────────────────────────────────────────────────────

if _feature_flags.enable_kanban:

    @mcp.tool()
    def read_kanban_tool(path: str, vault: str | None = None) -> dict:
        """Read an Obsidian Kanban board (requires kanban-plugin in frontmatter).
        Returns {path, plugin, columns: [{name, cards: [{text, done}]}], total_cards}."""
        return read_kanban(path)

    @mcp.tool()
    @_mutation_boundary
    def create_kanban_board_tool(
        path: str,
        columns: list[str],
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create a new Kanban board with the given column names.
        Returns {path, status, columns}."""
        return create_kanban_board(
            path,
            columns,
            index=_index,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def add_kanban_card_tool(
        path: str,
        column: str,
        text: str,
        done: bool = False,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Add a card to a Kanban column. Card is inserted at the top of the column.
        Returns {path, status, column, card, done}."""
        return add_kanban_card(
            path,
            column,
            text,
            done=done,
            index=_index,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    @_mutation_boundary
    def move_kanban_card_tool(
        path: str,
        card_text: str,
        from_column: str,
        to_column: str,
        done: bool | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Move a card from one column to another. done=true/false updates the tick state.
        Returns {path, status, card, from, to}."""
        return move_kanban_card(
            path,
            card_text,
            from_column,
            to_column,
            done=done,
            index=_index,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    @_mutation_boundary
    def delete_kanban_card_tool(
        path: str,
        card_text: str,
        column: str | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Delete a card from the Kanban board. column limits the search to one column.
        Returns {path, status, card}."""
        return delete_kanban_card(
            path,
            card_text,
            column=column,
            index=_index,
            expected_revision=expected_revision,
        )


# ── Bases ─────────────────────────────────────────────────────────────────────

if _feature_flags.enable_bases:

    @mcp.tool()
    def list_bases_tool(vault: str | None = None) -> list[str]:
        """List all Obsidian Bases (.base) files in the vault."""
        return list_bases()

    @mcp.tool()
    def read_base_tool(path: str, vault: str | None = None) -> dict:
        """Read an Obsidian Bases file.
        Returns {path, filters, formulas, properties, views}."""
        return read_base(path)

    @mcp.tool()
    @_mutation_boundary
    def write_base_tool(
        path: str,
        filters: dict | None = None,
        formulas: dict | None = None,
        properties: dict | None = None,
        views: list[dict] | None = None,
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create or fully overwrite a .base file.
        filters: boolean tree ({and:[...]}, {or:[...]}, {not:...}) or a single
        string statement, e.g. 'status != "done"' or 'file.hasTag("book")'.
        formulas: name -> expression string. properties: name -> {displayName}.
        views: list of {type, name, limit, filters, order, groupBy, summaries};
        'type' (e.g. 'table'|'cards'|'list') is required per view.
        Returns {path, status, views, known_properties} — known_properties is
        collected from existing .base files in the vault to keep naming consistent."""
        return write_base(
            path,
            filters=filters,
            formulas=formulas,
            properties=properties,
            views=views,
            index=_index,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def patch_base_tool(
        path: str,
        update_formulas: dict | None = None,
        delete_formula_keys: list[str] | None = None,
        update_properties: dict | None = None,
        delete_property_keys: list[str] | None = None,
        set_filters: dict | None = None,
        add_views: list[dict] | None = None,
        update_views: list[dict] | None = None,
        delete_view_names: list[str] | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Atomically update an existing .base file without rewriting it wholesale.
        update_formulas/update_properties are merged by key. set_filters replaces
        the whole filters block. update_views: each dict must include 'name'.
        Returns {path, status, views}."""
        return patch_base(
            path,
            update_formulas=update_formulas,
            delete_formula_keys=delete_formula_keys,
            update_properties=update_properties,
            delete_property_keys=delete_property_keys,
            set_filters=set_filters,
            add_views=add_views,
            update_views=update_views,
            delete_view_names=delete_view_names,
            index=_index,
            expected_revision=expected_revision,
        )


# ── Folders ───────────────────────────────────────────────────────────────────

@mcp.tool()
def list_folder_tool(path: str = "", recursive: bool = False, max_depth: int | None = None, vault: str | None = None) -> dict:
    """List the contents of a vault folder (non-hidden items only).
    path='': root of the vault.
    recursive=False (default): immediate contents only — {path, folders, files}.
    recursive=True: full tree dump in one call — {path, tree: {folders: {name: tree}, files: [...]}}.
    max_depth limits how many levels deep to descend (None = unlimited)."""
    return list_folder(path, recursive=recursive, max_depth=max_depth)


@mcp.tool()
def list_files_tool(folder: str = "", extension: str | None = None, vault: str | None = None) -> list[str]:
    """List every file in the vault (or a subfolder), any type — not just
    notes/attachments/bases/canvases (e.g. .lock files, stray non-Markdown
    files). extension filters by suffix without the dot (e.g. "lock",
    "canvas"); omit for everything. Hidden files/folders are skipped."""
    return list_files(folder, extension=extension)


@mcp.tool()
def create_folder_tool(path: str, vault: str | None = None) -> dict:
    """Create a folder (and any missing parents) in the vault.
    Returns {path, status}."""
    result = create_folder(path)
    log_write("create_folder_tool", path, "created folder")
    return result


def delete_folder_tool(path: str, trash: bool = True, vault: str | None = None) -> dict:
    """Delete a vault folder.
    trash=True (default) moves it to .trash/ instead of permanent deletion.
    Returns {path, status, trash}."""
    result = delete_folder(path, trash=trash)
    log_write("delete_folder_tool", path, f"deleted folder (trash={trash})")
    return result


if _feature_flags.enable_delete:
    mcp.tool()(delete_folder_tool)


def rename_folder_tool(from_path: str, to_path: str, vault: str | None = None) -> dict:
    """Rename or move a vault folder. Rewrites path-based wikilinks in all
    notes that reference notes inside the moved folder.
    Returns {from, to, notes_moved, updated_links_in}."""
    result = rename_folder(from_path, to_path, index=_index)
    log_write("rename_folder_tool", to_path, f"renamed from {from_path}")
    return result


if _feature_flags.enable_folder_rename:
    mcp.tool()(rename_folder_tool)


def list_trash_tool(vault: str | None = None) -> dict:
    """List items sitting in .trash/ (from delete_note_tool/delete_folder_tool
    with trash=True). Names here are what restore_note_tool/restore_folder_tool
    expect as trashed_name.
    Returns {items: [{name, type, size_bytes, mtime}]}."""
    return list_trash()


if _feature_flags.enable_delete:
    mcp.tool()(list_trash_tool)


def restore_folder_tool(trashed_name: str, to_path: str, vault: str | None = None) -> dict:
    """Restore a folder previously moved to .trash/ (see list_trash_tool for names).
    to_path: where to put it back — the original parent path can't be
    recovered from the trash entry alone, so you choose the destination.
    Returns {path, status, notes_restored}."""
    result = restore_folder(trashed_name, to_path, index=_index)
    log_write("restore_folder_tool", to_path, f"restored from .trash/{trashed_name}")
    return result


if _feature_flags.enable_delete:
    mcp.tool()(restore_folder_tool)


# ── MCP Resources ─────────────────────────────────────────────────────────────

@mcp.resource("vault://notes/{path}")
def vault_note_resource(path: str) -> str:
    """Raw content of a vault note — use as context without calling a tool."""
    try:
        return VaultStorage.from_config().read_text(path)
    except (ConfigError, FileNotFoundError, PermissionError, VaultPathError, OSError):
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
    global _cfg
    _cfg = get_config()
    for name, vault in _cfg.vaults.items():
        context_token = set_current_vault(name)
        try:
            policy = VaultAccessPolicy.from_config(_cfg)
        finally:
            reset_current_vault(context_token)
        VaultStorage(policy).probe_create_only_support()
        index = VaultIndex(vault.path, exclude_paths=vault.exclude_paths, policy=policy)
        watcher = VaultWatcher(
            vault.path,
            debounce_ms=_cfg.watcher_debounce_ms,
            reconcile_interval=_cfg.index_reconcile_interval,
            policy=policy,
        )
        _indices[name] = index
        _watchers[name] = watcher

        def initialize_index(
            index: VaultIndex = index,
            watcher: VaultWatcher = watcher,
            name: str = name,
        ) -> None:
            try:
                index.build(publish_ready=False)
                watcher.start(on_change=index.update, on_reconcile=index.reconcile)
                index.reconcile()
                index.mark_ready()
            except Exception:
                logger.exception("Initial index build/reconciliation failed for vault %s", name)

        threading.Thread(target=initialize_index, daemon=True).start()
    if _cfg.multi_vault:
        logger.info("Multi-vault mode: %d vault(s) configured (%s)", len(_cfg.vaults), ", ".join(_cfg.vaults))
    if _cfg.transport == "stdio":
        logger.info("Starting obsidian-mcp (transport=stdio)")
        mcp.run(transport=_cfg.transport)
    else:
        logger.info(
            "Starting obsidian-mcp (transport=%s, host=%s, port=%d)",
            _cfg.transport, _cfg.host, _cfg.port,
        )
        mcp.run(transport=_cfg.transport, host=_cfg.host, port=_cfg.port)


if __name__ == "__main__":
    main()
