# Simplified sync hardening

## Goal and threat model

The MCP server and Obsidian Sync may write the same vault without sharing a
lock. The realistic lost-update window is the time between an MCP client
reading a note and later writing its answer, while a phone or desktop edit is
synced into the same file.

This design detects that conflict with content revisions and keeps the search
index self-healing. It does not attempt distributed transactions, automatic
Markdown merging, or exactly-once execution.

## Content revisions

Each direct note read returns an opaque revision token:

```text
sha256:<64 lowercase hexadecimal characters>
```

SHA-256 of the exact file bytes is authoritative. Size and nanosecond mtime are
kept internally for diagnostics, but never decide whether content is unchanged.
Direct single-file mutations return the new revision. List and search results do
not hash every result and do not include revisions.

Incremental read-modify-write tools read content and its revision together once,
derive their change from those bytes, and pass that revision to the atomic write.
A caller may also supply `expected_revision` to pin the mutation to an earlier
read. Full replacement tools accept `expected_revision` and `create_only`.

`REQUIRE_WRITE_PRECONDITIONS=true` makes an existing full-file replacement
require the revision returned by an earlier read. Creating a missing path remains
allowed and automatically uses no-replace semantics. The native default is
`false` for compatibility; network Compose examples default it to `true`.

Revision mismatches are returned as MCP error results (`isError: true`) with a
machine-readable `revision_conflict` payload. The current content is not included;
the client must read again before deciding whether to retry or merge.

## Atomic writes and their limit

For an existing file, the storage gateway:

1. authorizes and opens the target descriptor-relatively without following links;
2. reads and checks the expected content revision;
3. writes and flushes a temporary file beside the destination;
4. checks the destination revision again immediately before replacement;
5. atomically renames the staged file and flushes the directory;
6. returns the committed content revision.

For a path observed as missing, a hard-link commit provides portable no-replace
behavior: a concurrent creator wins and MCP returns a conflict instead of
overwriting it. Startup probes this primitive on every configured writable
filesystem and fails clearly if the mount does not support it.

The final check and rename are not one portable compare-and-swap operation. A
non-cooperating sync writer can still land in that very small interval. Revision
checks materially protect the much larger client think-time window, but snapshots
and Obsidian Sync history remain the final recovery layer.

New files and directories continue to use normal `0666`/`0777` creation modes
filtered by the process umask. Replacing a file preserves its existing permission
bits.

## Append and retry behavior

Appending to existing notes is supported. The safe client workflow is:

1. read the note and revision `R`;
2. append using `expected_revision=R`;
3. if the response is lost, retrying with `R` returns a revision conflict;
4. read again and verify whether the intended content is present.

This is optimistic concurrency, not exactly-once execution. An append without a
caller revision still protects the server's own read-to-write interval, but a
blind retry can append twice. There is deliberately no SQLite operation ledger,
pending-operation recovery protocol, or conflict-content store.

## Watcher and reconciliation

Filesystem events are debounced per path (`WATCHER_DEBOUNCE_MS`, default 100 ms)
because sync clients commonly produce short event storms.

Watcher delivery can be lost across bind mounts and network filesystems, so the
index also performs a full content-revision reconciliation every
`INDEX_RECONCILE_INTERVAL` seconds (default 900, or 15 minutes). Startup ordering
is:

1. build the initial index;
2. start the watcher;
3. run one full reconciliation;
4. publish the index as ready.

The startup sweep repairs changes that land between the build and watcher start.
There is no startup event queue or overflow state machine; a later missed event
self-heals at the next periodic sweep.

Reconciliation hashes only readable, indexable Markdown notes. Attachments,
excluded paths, denied paths, and `*.excalidraw.md` files are never part of the
sweep. This invariant is what keeps a vault containing large PDFs, images, or
audio inexpensive to reconcile. Each Markdown file is read at most once per
sweep, and changed content is passed directly into indexing rather than read
again.

The implementation intentionally does not use a size/mtime stat cache. Same-size
Obsidian edits are common, and coarse NAS timestamps can otherwise leave a
"racily clean" note stale indefinitely. Measurements on approximately 1 KB notes
showed a full-hash pass taking about 0.24 seconds for 1,000 notes, 1.1 seconds for
5,000, and 2.3 seconds for 10,000. At the default interval, the 10,000-note case
is roughly a 0.25% background duty cycle. Cold spinning-disk performance may be
slower, so health reports the observed duration.

## Readiness and health

The unauthenticated health response contains no vault paths or content and adds:

- `index_ready`;
- `last_reconcile_at`;
- `last_reconcile_duration_seconds`;
- `last_reconcile_error`.

Initial build or startup reconciliation failure leaves the index unready and
returns HTTP 503. A later periodic reconciliation failure is reported in health
but does not discard an already usable index or change readiness by itself.

## Deliberately excluded

- SQLite operation ledger and exactly-once claims;
- staged conflict copies and operator conflict CLI;
- HTTP attachment ETag/`If-Match` handling;
- blind-create/blind-overwrite configuration matrices;
- stat-cache and filesystem timestamp-granularity probing;
- multi-file transaction or sync guarantees.

High-impact multi-file move, folder rename, bulk replacement, and delete
workflows remain separately feature-gated. `list_trash_tool` is hidden with the
rest of the delete/restore workflow when `ENABLE_DELETE=false`.
