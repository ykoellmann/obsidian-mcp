# Tool Surface Simplification Plan

Status: proposed implementation plan  
Branch: `codex/simplify-tool-surface`  
Base: upstream `main` at `3b89345` (`v1.2.0`)

## Executive summary

The server's capabilities are useful, but the default MCP surface is larger
than an agent needs for common note-memory workflows:

- 48 tools are registered with the four optional format groups disabled.
- 65 tools are registered when Canvas, Excalidraw, Kanban, and Bases are all
  enabled.
- The default tool definitions contain about 28 KB of names, descriptions,
  and input schemas (38 KB with every optional format enabled).
- The default surface has 153 input properties across its 48 tools.

The size and count of the resulting profile should be reported as diagnostics,
not optimized as acceptance thresholds. A tool stays when it represents a
distinct, useful intention; it is omitted when it is an alias, a higher-risk
workflow outside the profile's purpose, or an uncommon administrative helper.

The first change should reduce what the model sees without rewriting the
underlying, tested capabilities or replacing several clear tools with one
large schema full of conditionally valid arguments.

The implementation keeps `full` as the compatibility default. Operators can
set `TOOL_PROFILE=focused` for a deliberately selected set of existing,
intent-oriented tools, and the internal Python functions remain available for
later consolidation.

This is deliberately narrower than a v2 API redesign: it adds an optional
curated surface without removing underlying implementations or true aliases.

## Goals

1. Reduce tool-selection overhead for general AI note-memory and writing
   workflows.
2. Preserve distinct user intentions, especially safe append, targeted edit,
   search, semantic graph operations, and template creation.
3. Keep mutation behaviour, permissions, audit behaviour, and multi-vault
   routing unchanged.
4. Avoid a single "do anything" tool with a large, weakly validated schema.
5. Preserve compatibility for existing installations and clients.
6. Make descriptions say when to choose a tool over its nearest neighbours
   and expose relevant read/write cost.
7. Establish tests and measurements that let a later v2 consolidation be
   decided from evidence.

## Non-goals for the first implementation

- Removing underlying functions from `src/obsidian_mcp/tools/`.
- Changing note parsing, indexing, storage, locking, or watcher behaviour.
- Changing authentication, vault selection, path authorization, or feature
  flags for Canvas, Excalidraw, Kanban, and Bases.
- Renaming all tools or introducing a versioned MCP endpoint.
- Adding a vault-refactor mutation. A read-only planning tool can be assessed
  separately after the public surface is settled.
- Optimizing for an arbitrary tool-count threshold.

## What is actually duplicated

### True aliases or strict subsets

These are the safest candidates to omit from a focused surface:

| Tool | Covered by | Notes |
| --- | --- | --- |
| `get_daily_note_tool` | `get_periodic_note_tool(period="daily")` | The implementation is a direct wrapper. |
| `get_note_history_tool` | `get_audit_log_tool(path=...)` | The implementation is the same audit read with a path filter. |
| `get_notes_by_tag_tool` | `query_notes_tool(tags=[...])` | The generic query also supports folder, status, and metadata filters. |

These wrappers should remain in the `full` profile during the compatibility
period. Their underlying functions do not need to be deleted.

### Related, but not equivalent

These families should not be merged merely because their names are close:

| Family | Important distinction |
| --- | --- |
| `list_notes`, `list_folder`, `list_files` | Notes can return parsed metadata; folder listing returns directory structure; file listing recursively finds any file type. A combined tool would have conditional parameters and incompatible result shapes. |
| `read_note`, `render_note`, `get_note_outline` | They return different shapes and have different I/O/output costs. Consolidation is plausible, but only with a stable discriminated result schema. |
| `patch_note`, `patch_note_text`, `append_to_note` | Section/block edits, arbitrary text replacement, and append are distinct intentions. Append is especially valuable for agent memory and should remain obvious rather than become one operation value among many. |
| `patch_frontmatter`, `manage_tags` | Tag management also removes matching inline tags; it is not just a YAML-key update. Hiding that side effect inside a generic metadata tool would be risky. |
| `patch_frontmatter_batch` | Partial-success batch semantics differ from a single-note mutation and deserve an explicit contract. |
| `get_tag_tree`, `list_all_tags` | Same index data, but different result shapes and intentions: taxonomy versus counts. They may be consolidated later with an explicit mode. |

### Dedicated semantic operations to retain

The following tools communicate a distinct user intention or perform useful
server-side work and should not be reconstructed by the model from generic
file primitives:

- `search_notes_tool`
- `find_similar_notes_tool`
- `query_notes_tool`
- `get_backlinks_tool`
- `get_link_graph_tool`
- `get_broken_links_tool`
- `get_orphans_tool`
- `lint_schema_tool`
- `get_tasks_tool`
- `create_from_template_tool`
- `get_vault_conventions_tool`

## Proposed profiles

Add one setting:

```dotenv
TOOL_PROFILE=full
```

Accepted values:

- `full` (default): preserve the larger compatibility surface.
- `focused`: register the intentionally curated core below.

Unknown values must fail at startup with a clear configuration error. There
should not be a growing collection of per-tool environment flags. The four
existing optional format flags remain independent: if enabled, their tools
are added to either profile because an operator explicitly requested those
formats.

Selecting focused is a public tool-discovery migration. Existing clients keep
the larger surface by default and can opt into focused after migrating hidden
tool names.

### Proposed focused profile

#### Orientation, reading, and discovery

1. `list_vaults_tool`
2. `get_vault_conventions_tool`
3. `list_notes_tool`
4. `list_folder_tool`
5. `read_note_tool`
6. `get_note_outline_tool`
7. `search_notes_tool`
8. `find_similar_notes_tool`
9. `query_notes_tool`

#### Single-note writing

10. `write_note_tool`
11. `patch_note_tool`
12. `patch_note_text_tool`
13. `append_to_note_tool`
14. `patch_frontmatter_tool`
15. `manage_tags_tool`
16. `create_folder_tool`

#### Semantic vault operations

17. `get_backlinks_tool`
18. `get_broken_links_tool`
19. `get_orphans_tool`
20. `get_link_graph_tool`
21. `get_tasks_tool`
22. `get_periodic_note_tool`
23. `lint_schema_tool`
24. `get_vault_stats_tool`
25. `list_all_tags_tool`

#### Templates

26. `list_templates_tool`
27. `create_from_template_tool`

#### Attachments

28. `list_attachments_tool`
29. `read_attachment_tool`
30. `add_attachment_tool`
31. `create_attachment_token_tool`

This profile is intentionally conservative:

- High-impact move, rename, deletion, restore, trash, and vault-wide replacement
  tools are absent by default but added to either profile when an operator
  explicitly enables their capability group.
- It omits audit inspection, explicit alias resolution, rendered/transcluded
  reads, generic file discovery, and uncommon folder administration. Their
  capabilities remain in `full`.
- It does not claim that omitted tools are unimportant. It is a curated
  runtime view for the common workflow, not a code deletion list.

If another omitted family proves essential in normal focused usage, prefer
adjusting the single curated profile after testing. Do not add an environment
flag for every family in the first version. Recount the surface after the
selection is agreed; do not add or remove tools merely to reach that count.

## Why not consolidate immediately

The proposed `list_vault`, `read_note(mode=...)`, `edit_note(operation=...)`,
and `update_metadata` names are attractive, but only the name count is known
to improve. Their schemas introduce costs that must be evaluated:

- A flat `edit_note` would need many parameters that are invalid for most
  operation values.
- A discriminated union gives stronger validation, but some MCP clients and
  models handle `oneOf`/`anyOf` schemas poorly.
- A combined read tool would return raw-note dictionaries, rendered strings,
  or outline dictionaries depending on a mode, weakening its output contract
  unless all results are wrapped in a new stable envelope.
- A combined list tool would mix recursive tree output, lists of paths, and
  parsed note metadata.
- Combining tag changes with frontmatter updates can hide removal of inline
  tags, a meaningful content mutation.

The profile approach provides most of the selection benefit with little
behavioural risk. Consolidation can then be tested one family at a time.

## Implementation plan

### Phase 1: Centralize registration metadata

1. Add a small immutable profile definition containing the focused tool-name
   set. Keep the policy close to registration code, with a comment explaining
   that it controls visibility, not authorization.
2. Parse and validate `TOOL_PROFILE` without requiring `VAULT_PATH`, because
   registration currently occurs at module import time.
3. Introduce one registration predicate/helper so profile checks are not
   repeated as ad hoc `if` blocks around dozens of functions.
4. Preserve direct Python wrappers for tests and internal callers; the profile
   should affect FastMCP registration only.
5. Ensure existing optional-format and high-impact mutation flags compose with
   the profile in one predictable order: base profile first, explicitly enabled
   capability groups added second.
6. Do not use profile selection as a security boundary. `READ_ONLY`, path
   policy, and authentication remain authoritative.

Implementation note: decorators currently register tools while defining the
functions. Before choosing a helper, make a minimal spike against the pinned
FastMCP version to confirm that conditional registration preserves function
metadata and schemas cleanly. Prefer an explicit registration table if it is
clearer than a clever decorator wrapper.

### Phase 2: Improve decision-oriented descriptions

Update descriptions for tools visible in the focused profile. Each should
state, where relevant:

- the user intention it represents;
- when to prefer it over the nearest neighbouring tool;
- whether it reads or returns a full note body;
- whether it can create, overwrite, or affect multiple notes;
- whether a prior read is needed;
- its `dry_run`, diff, and partial-success behaviour.

Important examples:

- Describe `append_to_note_tool` as the preferred operation for adding an
  independent memory/finding without replacing existing content.
- Distinguish heading/block-reference edits (`patch_note_tool`) from literal
  or regex replacement (`patch_note_text_tool`).
- Distinguish vault structure (`list_folder_tool`) from Markdown-note
  discovery and metadata (`list_notes_tool`).
- Explain that `query_notes_tool` is preferred over the tag-only convenience
  wrapper and can combine filters.
- Explain the server-side cost of full-text search and graph-wide diagnostics
  without presenting unstable performance promises.

Update `_DEFAULT_INSTRUCTIONS`, prompts, README examples, and `.env.example`
so they never instruct a focused client to call a hidden tool. Ideally build
the tool-reference portion of the instructions from the selected profile, or
maintain clearly tested profile-specific blocks; do not leave a static list
that contradicts the advertised tool list.

### Phase 3: Tests and compatibility checks

Add tests that verify:

1. The default is `full` and has the exact documented compatibility tool set.
2. Explicit `focused` has the documented curated tool set.
3. The three true aliases are absent from `focused` and present in `full`.
4. Unknown profiles fail clearly at startup/import.
5. Each optional format flag adds only its own group under both profiles.
6. Each high-impact mutation flag adds only its own group under both profiles.
7. Multi-vault middleware still supplies and strips the `vault` argument for
   every registered tool.
8. Read-only and write-path behaviour is identical under both profiles.
9. Profile-specific server instructions mention only registered tools.
10. Prompts do not prescribe unavailable tools.
11. Reloading the server under different environment settings does not leak
    registrations between tests.

Retain the existing tool-function tests: a hidden tool in `focused` still has
working implementation coverage. Add a tool-surface snapshot containing
names and input schemas for each profile so accidental public API changes are
visible in review.

Run:

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest -q
uv build --offline
```

Also perform one scripted MCP initialization and `tools/list` call for each
profile, then invoke representative read, append, patch, query, and template
tools against a temporary vault.

### Phase 4: Agent-facing evaluation

Before consolidating schemas, compare `full` and `focused` with a small,
repeatable prompt set:

- locate a note by topic;
- inspect a large note's structure;
- append a memory entry under a heading;
- change one exact value in a long note;
- update tags without replacing the body;
- find related notes before creating a new one;
- inspect backlinks and broken links;
- create a daily/weekly note from a template;
- attempt a task requiring a deliberately omitted tool.

Record tool-selection errors, unnecessary calls, retries, total tool calls,
and failures caused by missing tools. The last case is important: a smaller
list that forces fragile reconstruction is not an improvement.

## Later v2 candidates

Only after the focused profile has been used successfully should a follow-up
branch prototype actual consolidation:

1. Remove `get_daily_note_tool`, `get_note_history_tool`, and
   `get_notes_by_tag_tool` from the focused public surface.
2. Prototype `read_note(mode="parsed"|"rendered"|"outline")` with a stable
   discriminated response envelope. Compare its generated JSON schema and
   agent selection against the three current tools.
3. Consider a tag-reporting mode that unifies tree and counted-list output.
4. Keep append dedicated. Only combine section and text patching if a nested
   discriminated edit schema validates reliably across supported clients.
5. Do not combine listing tools unless the result contract can remain stable
   and the input schema is simpler than the current choices.
6. Consider a read-only `plan_note_move`/`analyze_note_placement` operation as
   a separate feature. It should report collisions, backlinks, aliases, and
   likely broken-link consequences without mutating the vault.

## Documentation and migration

- Document the current compatibility default explicitly: `TOOL_PROFILE=full`.
- Show `TOOL_PROFILE=focused` as the opt-in curated surface.
- Include the exact focused tool list and explain how optional format flags
  add to it.
- State that profiles reduce model-visible tools but do not grant or revoke
  filesystem permissions.
- Add a short troubleshooting note: if a documented capability is missing,
  inspect `TOOL_PROFILE` and the relevant optional-format flag, then reconnect
  the MCP client so it refreshes `tools/list`.
- Explain that selecting focused is a public tool-discovery migration.

## Acceptance criteria

- A clean install exposes exactly the documented compatibility base tool set.
- `TOOL_PROFILE=focused` exposes the documented curated base tool set.
- No underlying capability or existing full-profile tool schema changes in
  the first implementation.
- Focused instructions and prompts reference only tools the client can see.
- Optional format groups remain opt-in and compose correctly with both
  profiles.
- High-impact mutation groups remain opt-in and compose correctly with both
  profiles.
- Authorization and mutation semantics are unchanged.
- Full tests, lint, package build, and scripted MCP smoke tests pass.
- The final PR reports before/after tool counts and tool-definition sizes.

## Expected merge characteristics

This work is correctly based on upstream `main`, not on the filesystem-policy
or sync-hardening branches: tool visibility is an independent concern.
However, `server.py`, `config.py`, `.env.example`, and README are natural
conflict points with those branches. Keep the implementation concentrated in
registration/profile code and avoid refactoring unrelated server logic. If
the branches are later combined, merge the filesystem-policy branch first,
then reapply the small profile-selection layer and rerun the profile,
authorization, and multi-vault tests together.
