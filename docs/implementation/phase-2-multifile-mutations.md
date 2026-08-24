# Phase 2: Authorization-safe multi-file mutations

## Status

Parked. The transaction-based implementation developed from this plan was
closed after review found that its complexity and operational costs outweighed
the underlying consistency problem. Do not implement this plan as written.

See
[`multi-file-mutations-reassessment.md`](multi-file-mutations-reassessment.md)
for the findings, current decision, and the much smaller roll-forward design to
consider if this work is resumed.

## Objective

Make note moves, folder renames, backlink rewrites and vault-wide replacement
predictable and authorization-safe. No operation may start and then discover
that one of its later writes is forbidden.

This phase addresses the current behavior where a move inside `WRITE_PATHS` can
rewrite backlinks outside `WRITE_PATHS`.

## Scope

The following operations are multi-file mutations:

- `move_note`;
- `rename_folder`;
- `find_replace_in_vault` with `dry_run=false`;
- any future operation that rewrites backlinks, aliases or embedded paths;
- folder deletion or restore when it contains multiple indexed notes.

Single-file note patches remain covered by Phase 1 and Phase 3.

## Product decision

Register high-impact mutation tools only when explicitly enabled:

```env
ENABLE_MOVE=false
ENABLE_FOLDER_RENAME=false
ENABLE_BULK_REPLACE=false
ENABLE_DELETE=false
```

For the intended AI memory/output deployment, the recommended defaults are all
`false`. The server can still create and append notes in the allowed AI folders.

When enabled, multi-file operations are fail-closed and all-or-nothing from an
authorization perspective. A backlink outside `WRITE_PATHS` causes the move to
be rejected; silently leaving stale backlinks is not the default.

## Mutation-plan model

Add `src/obsidian_mcp/storage/mutations.py` with explicit plan types:

```python
@dataclass(frozen=True)
class PlannedWrite:
    path: VaultPath
    original_revision: str | None
    content: bytes

@dataclass(frozen=True)
class PlannedMove:
    source: VaultPath
    destination: VaultPath
    original_revision: str

@dataclass(frozen=True)
class PlannedDelete:
    path: VaultPath
    original_revision: str

@dataclass(frozen=True)
class PlannedDirectoryCreate:
    path: VaultPath

@dataclass(frozen=True)
class MutationPlan:
    operation: str
    writes: tuple[PlannedWrite, ...]
    moves: tuple[PlannedMove, ...]
    deletes: tuple[PlannedDelete, ...]
    directory_creates: tuple[PlannedDirectoryCreate, ...]
    index_changes: tuple[IndexChange, ...]
```

Planning is read-only. It must produce the complete intended change set before
the first directory, lock or temporary file is created.

Each plan should have a stable digest derived from the complete deterministic
mutation payload: operation type and parameters, canonical paths, original
revisions, proposed-content hashes, every directory creation and every other
semantic field. Different committed bytes, paths or parameters must always
produce a different digest. Return it from dry runs so a caller can approve one
specific plan.

## Execution sequence

### 1. Resolve

- Resolve source and destination through Phase 1's path policy.
- Resolve every candidate backlink or bulk-replacement file.
- Reject ambiguous path/stem resolution before planning changes.
- Build a complete authorized inventory of candidate backlink and
  bulk-replacement files. If policy hides any possible candidate, including a
  file beneath `DENY_READ_PATHS`, reject rather than operate on an incomplete
  set.

### 2. Build the complete plan

- Read source content and revisions.
- Calculate all rewritten file contents in memory.
- Record every missing destination directory, including missing parents, as a
  `PlannedDirectoryCreate`; do not derive or create directories during commit
  that were absent from the approved plan.
- Record index removals and updates.
- Do not mutate disk.

### 3. Authorize the complete plan

- Require write authorization for every `PlannedWrite`.
- Require source and destination authorization for every `PlannedMove`.
- Require delete authorization for every `PlannedDelete`.
- Require write authorization for every `PlannedDirectoryCreate`.
- Reject the entire plan if any affected path is protected or outside
  `WRITE_PATHS`.
- Return a structured list of blocked paths without exposing denied content.

### 4. Validate preconditions

- Re-read and SHA-256 hash every planned existing path; metadata may avoid
  unnecessary work only when a content hash is still validated before commit.
- Confirm its revision matches the planned revision.
- Confirm each `PlannedDelete` still has its `original_revision`.
- Confirm every planned directory destination is still absent and every
  unplanned parent required by the commit already exists as a real directory.
- Confirm destinations still do not exist unless overwrite was explicitly
  planned.
- Abort with a conflict before staging if anything changed.

### 5. Lock

- Sort canonical paths lexicographically.
- Acquire locks in that stable order to avoid deadlock.
- Use the external lock directory introduced in Phase 1.
- Apply a total operation timeout and release all acquired locks on failure.

### 6. Revalidate under lock

- Recheck write, move and delete revisions plus directory destination
  non-existence.
- This protects against concurrent MCP operations.
- Phase 3 addresses non-cooperating sync writers.

### 7. Stage

- Write every `PlannedWrite` payload into the configured external transaction
  staging directory; staging must not require its vault destination or parent
  directory to exist.
- Record and verify each staged artifact's SHA-256 against the content hash
  bound into the approved plan.
- `fsync` staged files when durable mode is enabled.
- Do not create planned vault directories or replace originals yet.
- If staging fails, remove temporary files and leave originals untouched.

### 8. Commit

There is no portable atomic transaction across multiple files. Minimize the
partial-commit window and keep a recoverable journal:

1. write a transaction journal beneath the configured external transaction
   directory;
2. snapshot original file contents or create recovery copies outside the vault;
3. journal an intent and create each approved directory in parent-first order;
4. for every `PlannedWrite` in stable path order, journal its replacement
   intent, read and hash its staged artifact once, reject any mismatch with the
   planned content hash, install those same verified in-memory bytes with the
   planned revision precondition, and
   record the post-revision for recovery;
5. delete only files whose content still matches `original_revision`;
6. move the source to its destination;
7. mark the journal committed;
8. update the in-memory index;
9. remove recovery data after a retention period.

If a commit step fails, attempt rollback from the recovery copies. If rollback
is incomplete, retain the journal and return a recovery-required error. Never
claim the operation succeeded partially without listing its state.
Rollback removes a transaction-created directory only when the journal proves
this transaction created it and it is still empty; an unexpected child makes
recovery fail closed rather than deleting another writer's data.

### 9. Report

Return:

```json
{
  "operation_id": "...",
  "plan_digest": "...",
  "status": "committed",
  "moved": [{"from": "...", "to": "..."}],
  "rewritten": ["..."],
  "revisions": {"path.md": "sha256:..."}
}
```

Do not return full original or rewritten note contents.

## Move semantics

### Preferred backend interface

Define a semantic vault backend rather than embedding backlink rewriting in MCP
tool functions:

```python
class VaultSemantics(Protocol):
    def backlinks(self, path: str) -> list[str]: ...
    def plan_move(self, source: str, destination: str) -> MutationPlan: ...
```

Initially implement this with the existing parser/index. This leaves room for
an Obsidian CLI-backed implementation without coupling the authorization and
transaction layers to either engine.

### Link rewriting rules

- Preserve aliases, headings and block references.
- Preserve embeds versus normal links.
- Distinguish path-qualified and stem-only links.
- Do not rewrite a stem-only link when the stem is unchanged.
- Detect duplicate stems and alias ambiguity.
- Respect case-only renames on case-insensitive filesystems.
- Do not rewrite code blocks or frontmatter values unless explicitly supported.
- Add corpus tests covering Obsidian link syntax before enabling moves remotely.

## Bulk replacement

- Keep `dry_run=true` as the default.
- Dry run returns a plan digest, per-file revision and bounded previews.
- Commit accepts the plan digest or expected revisions.
- Set maximum files, total bytes and replacements per operation.
- Disable arbitrary regular expressions by default for remote deployments.
- If regex mode is retained, enforce pattern length and execution limits.
- Reject an empty search string.

## Folder operations

- Reject the vault root and protected directories.
- Plan every descendant filesystem entry, including notes, attachments,
  Canvas/Bases/Excalidraw files, hidden entries and directories, plus external
  backlink rewrites.
- Authorize every descendant source, mapped destination and external rewrite;
  explicitly reject unsupported or protected entry types.
- Do not follow symlinked directories.
- Preserve relative layout beneath the new folder.
- Treat cross-filesystem moves as unsupported unless an explicit copy-and-delete
  transaction is implemented.

## Recovery and observability

- Store transaction journals outside the vault.
- Log operation ID, principal, plan digest, affected path count and outcome.
- Do not log note bodies, API keys or OAuth tokens.
- On startup, inspect incomplete journals and either roll them back safely or
  mark the service unhealthy until an operator resolves them.
- Add an operator-only command to inspect and recover incomplete transactions.

## File-level changes

### `src/obsidian_mcp/storage/mutations.py` (new)

- Define plan, validation, execution, rollback and journal types.

### `src/obsidian_mcp/domain/semantics.py` (new)

- Define the semantic backend protocol.
- Implement the existing parser/index-backed move planner.

### `src/obsidian_mcp/tools/write.py`

- Replace inline move and bulk-replace mutation logic with plan creation and
  execution.
- Require explicit enable flags.
- Add plan digest/revision parameters for commits.

### `src/obsidian_mcp/tools/folders.py`

- Replace inline rename and delete logic with planned operations.
- Remove broad exception swallowing during backlink rewrites.

### `src/obsidian_mcp/server.py`

- Register destructive tools only when enabled.
- Return structured plan, conflict and recovery errors.

### `src/obsidian_mcp/config.py`

- Add enable flags and mutation limits.
- Validate that transaction and lock directories are outside the vault.

## Test plan

### Authorization regressions

- Move source and destination inside `WRITE_PATHS` with a backlink outside it:
  reject before mutation.
- Folder rename with one protected backlink: reject before mutation.
- Bulk replace with allowed and denied matches: reject the complete atomic
  operation; any separate omit mode must be explicitly non-atomic and visible
  to the client.
- Source inside allowed path and destination outside: reject.
- Root or `.obsidian` folder rename/delete: reject.

### Planning tests

- Planning causes no filesystem or index mutation.
- Identical state produces the same plan digest.
- A content change changes the digest/revision.
- Duplicate stems and aliases produce deterministic ambiguity errors.
- Link syntax is preserved for headings, blocks, aliases and embeds.

### Execution tests

- Stable lock ordering prevents deadlocks between overlapping operations.
- Destination appearing between plan and execution produces a conflict.
- Source changing between plan and execution produces a conflict.
- Failure during staging leaves originals unchanged.
- Injected failure during each commit step exercises rollback.
- Incomplete journal is detected at startup.
- Successful execution updates the index exactly once per affected note.

## Rollout

1. Ship all multi-file mutation flags disabled.
2. Enable move operations only in a disposable test vault.
3. Run corpus and fault-injection tests.
4. Enable in production only if the container's writable mounts include every
   path that the operation may legitimately rewrite.
5. Keep move, folder rename and bulk replace disabled in the recommended
   read-mostly plus `AI-*` nested-mount deployment.

## Completion criteria

- No multi-file tool mutates before complete planning and authorization.
- A write-protected backlink cannot be modified indirectly.
- Authorization or precondition failure leaves the vault byte-for-byte
  unchanged.
- Commit failures are recoverable from a journal.
- Destructive tools are absent from the MCP tool list unless explicitly
  enabled.
