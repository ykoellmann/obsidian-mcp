# MCP surface harmonization

This branch makes a clean interface break: one 17-tool base surface, no
focused/full profiles and no legacy MCP aliases. Reconnect clients after upgrading.
The earlier default-surface proposal in TOOL_SURFACE_SIMPLIFICATION_PLAN.md is
superseded. Optional Canvas, Excalidraw, Kanban and Bases groups remain opt-in.

## Surface

| Intent | Tools |
|---|---|
| Select vault | `list_vaults` |
| Discover | `list_files`, `search_files`, `list_attachments` |
| Read | `read_file`, `read_files`, `read_frontmatter`, `get_file_outline`, `read_attachment` |
| Write Markdown | `create_file`, `edit_file`, `append_file`, `patch_file`, `patch_frontmatter` |
| Filesystem extras | `get_backlinks`, `get_tasks`, `add_attachment` |

Every vault operation accepts optional `vault`; a batch selects one vault.
`list_vaults` returns `{vaults: [{name, description, is_default}]}` and works even
when an identity has no default vault. Backlinks and tasks return `{backlinks: [...]}`
and `{tasks: [...]}`. Existing Python helpers are implementation details, not a
supported legacy MCP surface. Delete/move/folder/bulk flags no longer register tools.

## Raw reads and literal edits

`read_file(path, startLine?, endLine?, expectedRevision?, vault?)` returns
`{path, revision, content}`. Content includes the original YAML, whitespace,
newlines and BOM. UTF-8 is decoded strictly. Ranged reads use inclusive one-based
whole-file lines and add `startLine`, `endLine`, `totalLines`, `partial`.
A range's revision describes the whole file. Empty files have zero lines.

`get_file_outline` supplies CommonMark heading text, levels and section ranges,
ignoring frontmatter and code fences. Limits: 8192 lines, 1024 headings, 32768
heading-source bytes. `read_frontmatter` supplies JSON-compatible properties and
revision, without body text. Malformed, duplicate-key, non-mapping or excessive
YAML is rejected; YAML aliases are currently unsupported. Timestamps, binary and
non-finite YAML values normalize to strings.

`read_files({files: [{path, startLine?, endLine?, expectedRevision?}], vault?})`
accepts 1–10 requests and returns `{files: [{index, path, result}]}`. Each result is
`{ok: true, data}`, `{ok: false, error}`, or
`{omitted: true, reason: "response_budget"}`. Later small reads are still attempted
when earlier large results do not fit. This is not a snapshot.

- `create_file`: exact Markdown, permitted parents implicit, never overwrite.
- `edit_file`: exact whole-file replacement, existing file only, mandatory
  `expectedRevision`; omitted frontmatter is removed.
- `append_file`: literal append to an existing file, no separators or trimming.
- `patch_file`: literal `oldText`/`newText`, unique match required unless
  `replaceAll=true`; no regex and no replacement escape processing.
- `patch_frontmatter`: `updates` replaces values/arrays; `remove` explicitly
  deletes keys. Null remains a value. Body bytes and unrelated values survive;
  YAML comments and formatting can normalize. Use exact patches when formatting matters.

Incremental mutations accept optional `expectedRevision`; instructions encourage
using the revision actually read. All existing-file transformations re-read under
the per-file lock and check the derived revision at commit. Locks are not shared
with Obsidian Sync, and the final check/rename is not distributed CAS. Never
blindly retry appends after a conflict or lost response.

Note reads/writes and attachment reads: 512000 bytes. Patch strings: 64000 bytes.
Ranges still require the whole file to fit the read limit. Batch combined wire
budget: 1 MiB. Overall combined JSON text + structured result budget: 8 MiB.
Attachment reads always return `contentBase64`, `revision`, `mimeType`, `sizeBytes`.
`add_attachment` is create-only and retains MAX_ATTACHMENT_BYTES and extension
policy. Existing HTTP attachment transfer authorization is unchanged.

## Search and pagination

Listing matches LiveSync's input names: `prefix`, `limit`, `cursor`.
Search uses `query`, `filters`, `properties`, `pathPrefix`, `limit`, `cursor`.
Prefix ending `/` selects descendants; other prefixes match literal path starts.
Prefixes need not name directories. Filesystem path matching is case-sensitive.

Listing defaults to 50 entries, maximum 100, returning `{files, cursor?}` or
`{attachments, cursor?}`. Entries include path, opaque content revision, sizeBytes,
modifiedAt in Unix milliseconds; attachments add mimeType. No synthetic creation
time is returned. Attachment discovery retains the filesystem extension allowlist.

Search requires text or nonempty typed filters. Whitespace-separated literal
terms combine with AND, case-insensitively, across raw content and path. Ranking
prefers terms in the filename then sorts by path. No FTS, regex, fuzzy or inline
field filtering is exposed. Ranking/tokenization can differ from LiveSync FTS.

```json
{
  "query": "meeting budget",
  "filters": [{"property": "status", "operator": "eq", "value": "active"}],
  "properties": ["status", "tags"],
  "pathPrefix": "Projects/",
  "limit": 20
}
```

Filter operators are `eq`, `ne`, `contains`, `exists`, `lt`, `lte`, `gt`, `gte`.
Ordering requires `type: "number"` or `type: "date"`. Dates must be ISO calendar
dates or timezone-qualified timestamps. Missing differs from null; numeric values
differ from booleans and strings; contains means list membership. Tags are
normalized frontmatter tags only. Selected absent properties are omitted.

Search defaults to 20 results, maximum 50. Query limits are 16 terms / 256 UTF-8
bytes; filters max 16, selected properties max 20. Snippets max 1024 bytes; combined
response budget 128 KiB. Results have path, revision, snippet and optional properties.
Responses include `truncated`, `incomplete`, `unindexedFiles`, optional
`unqueryableFiles`, and `cursor` when another page exists. The first flag indicates
more results; the latter fields report eligible files skipped due to content or
property-read failures. Unauthorized/excluded files do not contribute to counts.

Continue with identical query arguments and the returned cursor; page size may
change. No cursor means exhausted. Cursors are stateless, scoped to operation,
vault, read policy and query. They never grant access.

- Malformed or query-mismatched cursor: `invalid_input`.
- Changed scoped search content/coverage: `cursor_expired`; restart search.
- Listing uses live keyset continuation by canonical path, not a snapshot. Newly
  inserted paths before the continuation may be absent from that traversal.

Search rescans and hashes eligible notes on each page. This deliberately avoids a
new database, persistent index or cache. It trades CPU/I/O for simplicity; benchmark
before introducing a derived search index. The existing semantic index still serves
backlinks/tasks and retains its watcher/reconciliation behaviour.

## Instructions and migration

Read `_AI_INSTRUCTIONS.md` through an authorized ordinary read before writing;
it is not embedded into server instructions. Conventions are subordinate to the
user's request. Missing conventions do not justify inventing daily-note paths,
timezones, attachment placement or templates. Prompts use the canonical tools.

`TOOL_PROFILE` no longer controls anything. Old MCP names are absent even if it is
set to `full`. Optional format groups retain their existing names and flags.
`REQUIRE_WRITE_PRECONDITIONS` remains relevant to optional format implementations;
canonical `edit_file` always requires a revision. Readonly/path policy remains
authoritative even though mutation tools are advertised.

LiveSync retains its existing 15/9 surface and contract 5. Its only intended change
is an instruction to read the conventions file as ordinary user context. Shared
names, read shapes, typed filters and pagination align; filesystem content hashes,
optional incremental revisions, path ordering and scan ranking remain explicit differences.

## Validation

Automated tests exercise raw round trips, mutation preconditions, malformed YAML,
section ranges, pagination/cursor invalidation, coverage, budgets, attachments,
MCP wire shapes and authorization. Input schemas are snapshotted. Local HTTP smoke
checks use disposable vaults. Evaluation/benchmark results are recorded separately
in `docs/implementation/mcp-harmonization-validation.md`.
