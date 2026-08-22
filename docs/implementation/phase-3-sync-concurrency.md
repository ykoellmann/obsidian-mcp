# Phase 3: Sync-aware concurrency and conflict handling

## Status

Proposed implementation plan. Depends on Phase 1 and uses Phase 2's revision and
mutation-plan concepts.

## Objective

Prevent silent lost updates when obsidian-mcp and a non-cooperating sync process
modify the same vault. Make conflicts visible, recoverable and safe for an MCP
client to retry.

The target deployment runs obsidian-mcp beside Obsidian Headless Sync. Both
processes observe the same host directory, but only the MCP process participates
in MCP file locks.

## Constraints

- Obsidian Headless Sync does not acquire obsidian-mcp lock files.
- Atomic replacement prevents partial files but does not prevent last-writer-wins
  data loss.
- No portable filesystem primitive atomically compares a content hash and
  replaces a file.
- Multi-file mutations cannot be made fully atomic with ordinary filesystem
  operations.
- Filesystem watcher events may be duplicated, coalesced, reordered or missed
  across container bind mounts.
- Obsidian Sync conflict handling is useful recovery behavior, not an
  application-level concurrency contract.

The design therefore combines optimistic revisions, narrow write windows,
idempotency, conflict copies, watcher reconciliation and operational limits.

## Revision model

Define a strong content revision:

```python
@dataclass(frozen=True)
class FileRevision:
    sha256: str
    size: int
    mtime_ns: int
```

The SHA-256 content hash is authoritative. Size and nanosecond mtime are fast
change detectors and diagnostics, not security decisions.

All reads that may precede a write should return a revision:

- `read_note_tool`;
- `get_note_outline_tool`;
- `read_attachment_tool`;
- Canvas, Bases, Excalidraw and Kanban reads;
- mutation dry runs.

Example:

```json
{
  "path": "AI-Memory/session.md",
  "revision": {
    "sha256": "...",
    "size": 1234,
    "mtime_ns": 1787390000000000000
  }
}
```

## Write preconditions

Mutations of existing files accept `expected_revision`:

```python
write_note(path, content, expected_revision=None, create_only=False)
patch_note(path, ..., expected_revision=None)
delete_note(path, expected_revision=None)
move_note(path, ..., expected_revision=None)
```

Modes:

- `create_only=true`: fail if the destination exists;
- `expected_revision=<sha256>`: fail unless current content matches;
- no precondition: allowed only when policy explicitly permits blind writes.

Add configuration:

```env
REQUIRE_WRITE_PRECONDITIONS=true
ALLOW_BLIND_CREATE=true
ALLOW_BLIND_OVERWRITE=false
```

Recommended behavior:

- creating a unique new file does not require a prior revision;
- overwriting, patching, deleting or moving an existing file requires a revision;
- append can be revisionless only when it is idempotent and rereads the latest
  content while holding the MCP lock;
- HTTP attachment PUT uses `If-Match` and `If-None-Match: *` semantics.

On mismatch, return a structured conflict:

```json
{
  "error": "revision_conflict",
  "path": "AI-Memory/session.md",
  "expected": "sha256:...",
  "actual": "sha256:...",
  "current_mtime_ns": 1787390000000000000
}
```

Do not include current file content automatically; the caller must issue a new
authorized read.

## Safe single-file write algorithm

For an existing file:

1. Resolve and authorize the path.
2. Acquire the external MCP path lock.
3. Open and hash the current file.
4. Compare it with `expected_revision`.
5. Produce the new content from that exact version.
6. Write and flush a temporary file beside the destination.
7. Re-hash the destination immediately before replacement; metadata is only an
   optimization and never substitutes for content validation.
8. If it changed, retain neither the staged file nor a misleading success result;
   return a conflict.
9. Atomically replace the destination.
10. Hash the committed file and return its new revision.

Step 7 cannot eliminate the final race with a non-cooperating writer. The
completion guarantee is therefore conflict detection and recovery evidence,
not proof that no external writer raced the final replacement. Use a
platform-specific compare-and-swap/no-clobber primitive where available, detect
post-write divergence, and retain filesystem snapshots and Sync history as
recovery layers.

## Idempotent append

Retries are common with remote MCP clients. Add an optional `operation_id` to
append-like tools:

```python
append_to_note(path, content, operation_id="uuid")
```

Maintain a small SQLite operation ledger at the configured external
operation-ledger path, never inside the vault:

```text
principal_id
operation_id
tool_name
target_path
request_digest
status                    # pending | complete
initial_revision
expected_result_revision
result_revision
result_json
created_at
PRIMARY KEY (principal_id, operation_id)
```

Rules:

- the same principal, operation ID and request digest returns the stored result;
- reuse with different content is rejected;
- records expire after a configurable retention period;
- use `BEGIN IMMEDIATE` plus the composite primary key to atomically reserve a
  unique pending operation before replacement; a concurrent retry must observe
  that reservation rather than execute the append again;
- persist `initial_revision` and `expected_result_revision` in the pending row,
  then atomically transition it to `complete` with `result_revision` and
  `result_json` after replacement;
- on retry, reconcile a pending row against its expected post-state: finalize
  the result when the replacement is proven, retry only when the original state
  is proven, and otherwise return outcome-unknown without appending again;
- do not store note content in the ledger.

For append-heavy memory, prefer one event per uniquely named file over repeatedly
appending to a shared note. This is naturally idempotent and minimizes conflicts.

## Conflict-copy policy

Do not silently merge arbitrary Markdown in the MCP server.

When a revision conflict occurs:

- leave the current vault file untouched;
- return a conflict to the client;
- optionally save the proposed content outside the vault under
  the configured conflict directory under an opaque identifier derived from
  principal and operation ID (never join caller input directly as a path);
- expose an operator command to inspect or discard staged conflict content;
- never put secrets or denied source content into a conflict record.

For multi-file operations, use Phase 2 transaction recovery and disable them by
default when continuous external sync is active.

## Watcher and index consistency

### Event handling

- Debounce repeated events per canonical path for a short interval.
- Treat create, modify, move and delete explicitly.
- Ignore lock, transaction and temporary-file patterns.
- Never index denied paths.
- Record the revision most recently indexed.
- If an event revision already matches the index, skip reparsing.

### Startup ordering

Current startup launches the initial index build and watcher concurrently. Make
the sequence deterministic:

1. start watcher event capture into a queue;
2. build an initial snapshot;
3. replay queued events against the snapshot;
4. mark the index ready;
5. process live events normally.

This avoids losing a sync change that occurs during the initial scan.

### Reconciliation

Add periodic reconciliation even when watchdog/inotify is active:

```env
INDEX_RECONCILE_INTERVAL=300
```

Reconciliation hashes every in-scope file on a bounded periodic schedule;
size and mtime may prioritize work but cannot be the sole trigger. It repairs
missed events and removes deleted entries even when metadata was preserved.

Expose health information without sensitive paths:

```json
{
  "status": "ok",
  "index_ready": true,
  "last_event_at": "...",
  "last_reconcile_at": "...",
  "pending_events": 0,
  "conflicts_total": 0
}
```

## Deployment conventions for Obsidian Headless Sync

Use one host vault directory with asymmetric mounts:

| Container | Mount | Mode |
|---|---|---|
| Headless Sync | `/srv/obsidian/vault:/vault` | read-write |
| MCP | `/srv/obsidian/vault:/vault` | read-only |
| MCP | `/srv/obsidian/vault/AI-Memory:/vault/AI-Memory` | read-write nested mount |
| MCP | `/srv/obsidian/vault/AI-Output:/vault/AI-Output` | read-write nested mount |

Run both containers with compatible UID/GID values. The sync container must be
able to upload MCP-created files, while MCP must be unable to write elsewhere at
the kernel/filesystem layer.

Recommended vault conventions:

```text
AI-Memory/Events/<date>/<uuid>.md     # create-only, preferred
AI-Memory/Summaries/<period>.md       # conditional overwrite
AI-Output/Drafts/<uuid>.md            # create-only
AI-Output/Published/                  # promoted by a human workflow
```

Avoid having a human client and the MCP service repeatedly edit the same
long-lived note.

Set a distinct Sync device name. Start with the sync implementation's documented
merge conflict strategy, but retain independent host snapshots.

## Backup and recovery

Obsidian Sync history is useful but is not the sole backup for an automated
writer. Before enabling writes:

- configure ZFS/Btrfs snapshots, filesystem snapshots, or a versioned backup;
- retain enough history to cover delayed discovery of an AI mistake;
- test restoration of one file and the whole vault;
- monitor free space, sync health and conflict files;
- alert when the MCP conflict rate or pending watcher queue is non-zero for a
  sustained period.

## File-level changes

### `src/obsidian_mcp/domain/models.py`

- Add `FileRevision` and structured conflict result models.

### `src/obsidian_mcp/storage/filesystem.py`

- Add revision calculation, precondition validation, staged conditional writes
  and HTTP ETag helpers.

### `src/obsidian_mcp/storage/locking.py`

- Integrate revision checks with external path locks.

### `src/obsidian_mcp/storage/operations.py` (new)

- Implement the SQLite idempotency ledger and retention cleanup.

### `src/obsidian_mcp/storage/watcher.py`

- Add event queueing, debouncing, ignore patterns and health metrics.

### `src/obsidian_mcp/domain/index.py`

- Track indexed revisions.
- Implement queued startup replay and periodic reconciliation.

### Tool modules and `server.py`

- Return revisions from reads and successful writes.
- Accept revision preconditions and operation IDs.
- Map HTTP attachment conditions to `ETag`, `If-Match`,
  `If-None-Match` and HTTP 412.
- Return stable MCP conflict errors.

### `config.py`, `.env.example`, README and Compose example

- Add precondition, operation-ledger and reconciliation settings.
- Document the nested mount pattern and backup requirement.

## Test plan

### Revision tests

- Revision is stable for unchanged bytes.
- Revision changes for any content change.
- Metadata-only changes do not change the authoritative hash.
- Expected revision success returns the new revision.
- Stale expected revision leaves the file untouched.
- `create_only` fails if a sync writer creates the destination first.

### Concurrency tests

- Two MCP writers with the same expected revision: exactly one succeeds.
- External write before lock acquisition: MCP detects conflict.
- External write after staging but before replacement: MCP detects conflict at
  the second check.
- Append retry with the same operation ID is applied once.
- Operation ID reuse with different content is rejected.
- Simulated watcher events during initial index build are replayed.
- Dropped watcher event is repaired by reconciliation.

### Container integration test

Run a fake or test Sync writer beside MCP against bind mounts:

1. Sync writes a note; MCP indexes it.
2. MCP creates a unique AI event; Sync observes it.
3. Sync changes a note after MCP reads it; MCP conditional write conflicts.
4. MCP attempts a write outside nested mounts; both policy and OS deny it.
5. Restart both containers; revisions and idempotency records still behave
   correctly.

### Fault injection

- process termination after staging;
- process termination immediately after replacement;
- disk full;
- permission change;
- watcher overflow;
- corrupt operation ledger;
- temporary loss of the bind mount or sync process.

## Rollout

1. Add revisions to responses without requiring them.
2. Update instructions so clients read before mutating.
3. Enable `REQUIRE_WRITE_PRECONDITIONS` in a disposable vault.
4. Deploy create-only `AI-Output` writes.
5. Verify files round-trip through Obsidian Sync and another client.
6. Enable idempotent memory events.
7. Enable conditional updates only after conflict and restore drills.
8. Keep multi-file operations disabled under continuous sync unless there is a
   specific, tested need.

## Completion criteria

- Existing-file mutations validate stale versions and use the strongest
  available no-clobber primitive; unavoidable final races with non-cooperating
  writers are detected after the write and surfaced with recovery evidence.
- Retried appends do not duplicate content.
- Watcher startup and reconciliation tolerate concurrent sync activity.
- Conflict handling never overwrites the remote/local winner automatically.
- The recommended nested-mount deployment passes an end-to-end sync test.
- A documented and tested backup restoration procedure exists before production
  writes are enabled.
