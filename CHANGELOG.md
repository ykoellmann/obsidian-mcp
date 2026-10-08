# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.1] - 2026-10-08

### Fixed

- `get_audit_log_tool` advertised a wrapped array `outputSchema` (from a stale
  `-> list[dict]` annotation) while returning the standard envelope, so clients
  that validate structured content, such as Claude Code, rejected every call
  ([#10](https://github.com/ykoellmann/obsidian-mcp/issues/10)).

## [2.1.0] - 2026-09-16

### Added — per-identity path policy overrides

`identities[].vaults` in `vaults.json` now accepts an object mapping a vault
name to a policy override (`read_paths`, `write_paths`, `deny_read_paths`,
`deny_write_paths`, `read_only`) in addition to the existing array of vault
names, which keeps working unchanged and means "inherit each vault's policy".

An override can only restrict: allowlists intersect (a rule outside the
vault's own scope fails the server at startup), denylists union, and
`read_only` ORs toward `true`. The vault index and file watcher stay
vault-wide, so read scoping is applied per call when a tool formats its
response — backlinks, orphans, broken links, the link graph, tag tree,
`list_all_tags_tool`, tasks, vault stats, `query_notes_tool`,
`resolve_alias_tool`, `lint_schema_tool` and `find_similar_notes_tool` can no
longer name (or traverse through) a note outside the caller's read scope. See
[Per-Identity Path Policy](README.md#per-identity-path-policy).

## [2.0.0] — Tool Surface v2

A breaking release. **Every tool's response shape changed**, and 11 tools were
removed or merged into others. With all optional groups enabled the tool count
drops from 65 to 56. Clients that parse tool results or call the removed tools
need updating; the parameters of the tools that remain are unchanged except
where noted.

### Changed — every response now uses one envelope

Tools used to each return their own ad-hoc shape: a dict here, a bare list
there, a bare string from `render_note_tool`, and a `status: "written"` string
that callers compared against. They now all return one object, built through
the shared constructors in the new `obsidian_mcp.envelope` module:

```json
{
  "success": true,
  "path": "02-Areas/monari/ticket-123.md",
  "revision": "sha256:ab12…",
  "data": { "...": "tool-specific payload" },
  "meta": { "...": "action, dry_run, count, truncated, …" }
}
```

- `success` is always present and always a boolean. The `status` string is gone
  from the tool surface; a write's verb moved to `meta.action`, and a dry run
  additionally sets `meta.dry_run`.
- `path` is present for single-item results, omitted for vault-wide and list
  results.
- `data` is always present. Listings moved their rows into `data.items`, with
  `meta.count` and, where applicable, `meta.truncated`.
- `revision` is returned by every read and every successful single-file write.
- Batch results report per-item outcomes in `data.results`
  (`{success, path, revision}` or `{success: false, path, error}`, where
  `error.type` is the raised exception's class name) and tallies in
  `data.summary`. Top-level `success` only reports that the batch ran — check
  `data.summary.failed`.
- Hard failures (bad path, permission denied, revision conflict) still arrive
  as MCP errors. `success: false` is used only for per-item batch failures.

### Added

- **Optimistic concurrency.** Every write tool accepts an optional
  `expected_revision`. Pass the `revision` from a previous read to pin the
  write to those exact bytes; if the file changed in between, the write fails
  with a distinguishable `revision_conflict` error instead of silently
  discarding the other change. `create_only` requires the target to be absent,
  committed race-free via `linkat`.
- **Index reconciliation.** A periodic full content-revision sweep repairs
  index drift from edits the watcher missed (Obsidian Sync, git checkouts).
  Telemetry is exposed on `/health`. Watcher events are debounced per path.
- New configuration: `REQUIRE_WRITE_PRECONDITIONS` (require
  `expected_revision` on every overwrite), `INDEX_RECONCILE_INTERVAL`,
  `WATCHER_DEBOUNCE_MS`.
- `patch_note_tool` gained `dry_run`, which it lacked while the other patch and
  write tools had it.

### Removed — tools that duplicated another tool

| Removed | Use instead |
| --- | --- |
| `get_notes_by_tag_tool` | `query_notes_tool(tags=[...])` |
| `get_daily_note_tool` | `get_periodic_note_tool(period="daily")` |
| `get_note_history_tool` | `get_audit_log_tool(path=...)` |

### Changed — tools merged behind a parameter

| Removed | Merged into |
| --- | --- |
| `get_note_outline_tool`, `render_note_tool` | `read_note_tool(path, mode="full" \| "outline" \| "rendered", depth=1)` |
| `get_tag_tree_tool` | `list_all_tags_tool(sort_by, mode="flat" \| "tree")` |
| `patch_frontmatter_batch_tool` | `patch_frontmatter_tool(path=... \| paths=[...])` |
| `delete_note_tool`, `delete_folder_tool` | `delete_tool(path, trash=True)` |
| `restore_note_tool`, `restore_folder_tool` | `restore_tool(trashed_name, to_path)` |

Notes on the merges:

- `read_note_tool`'s `depth` applies to `mode="rendered"` only. Modes `full`
  and `outline` carry a `revision`; a rendered read spans several notes and so
  pins nothing.
- `patch_frontmatter_tool(paths=[...])` applies **one** set of `updates` to
  every listed note. The old batch tool's per-entry `updates` dicts have no
  equivalent — varying updates per note now means one call per distinct set.
  `expected_revision` is rejected alongside `paths`, since one token can only
  pin one note.
- `delete_tool` and `restore_tool` dispatch on what the path actually is, so
  callers no longer need to know whether a target is a file or a directory.
  `meta.kind` reports which branch ran. `expected_revision` is rejected for a
  folder, which has no single revision. Both remain behind `ENABLE_DELETE`,
  which now registers `delete_tool`, `restore_tool` and `list_trash_tool`.
- An unknown `mode` raises a `ValueError` naming the valid values rather than
  silently falling back to a default.

### Unchanged on purpose

`list_notes_tool` / `list_folder_tool` / `list_files_tool` stay separate
(different result shapes), as do `patch_note_tool` / `patch_note_text_tool` /
`append_to_note_tool` (distinct write intentions) and the graph tools
(incompatible signatures). `manage_tags_tool` is not folded into
`patch_frontmatter_tool` because it also strips inline body tags.
