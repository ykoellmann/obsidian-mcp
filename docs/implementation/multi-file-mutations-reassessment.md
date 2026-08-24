# Multi-file mutations: reassessment and parked direction

## Status

Parked. No replacement implementation is currently planned.

The immediate priority is to ship and exercise the simpler single-file
operations, filesystem authorization, revision conflict detection, and index
reconciliation. High-impact multi-file tools should remain disabled by default.

This note records why the transaction-based implementation was closed and the
smallest design worth considering if the work is resumed.

## Decision

Do not revive the generic multi-file transaction manager.

The closed implementation attempted database-style crash recovery over ordinary
vault files: plan digests, staged post-images, pre-image backups, intent/applied
journalling, rollback, unknown-state classification, transaction management,
and health integration. That machinery was substantially more complex and
operationally risky than the failures it was intended to handle.

The underlying requirement should be reduced. Note and folder moves need safe,
recoverable link maintenance, not an ACID transaction spanning the vault.

## The actual consistency problem

Only three workflows are genuinely multi-file:

1. moving or renaming a note while updating links to it;
2. renaming a folder while updating path-qualified links;
3. replacing text across many independent notes.

Trash and restore are different. On one filesystem they each commit through a
single `rename(2)` and do not need a transaction manager.

| Operation | Atomic part | Failure if interrupted | Severity |
|---|---|---|---|
| Trash folder | One rename into `.trash` | Either moved or not moved | Low |
| Restore folder | One rename out of `.trash` | Either restored or not restored | Low |
| Move note | Rename plus link rewrites | Note moves, but some backlinks remain stale | Moderate and recoverable |
| Rename folder | Rename plus link rewrites | Folder moves, but some path links remain stale | Moderate and recoverable |
| Bulk replace | Many independent rewrites | Only some notes are changed | Potentially high |

POSIX does not provide a portable atomic commit across multiple file
replacements. Building that abstraction correctly requires a write-ahead log,
which is effectively what the closed implementation became.

## Findings from the closed implementation

### Planning performance

Move planning was measured as approximately quadratic in vault size and link
count. `SemanticFile.note` reparsed the same unchanged note every time a link
candidate was resolved. In a 100-note test this produced 100,000 parser calls,
with parsing accounting for most planning time.

Using `cached_property` would remove repeated parsing, but the proper fix is to
build canonical-path, stem, and alias lookup tables once. Candidate lookup then
becomes approximately linear in vault content plus the number of links.

### Journal performance

Every intent and applied step republished and fsynced the complete accumulated
JSON journal. A folder rename therefore rewrote a growing document twice per
file, producing quadratic I/O and several fsyncs per affected file. This is
especially unsuitable for NAS storage.

### Unbounded retained data

Successful transactions retained their staged post-images, recovery pre-images,
and journal indefinitely. Completed transactions were omitted from health
warnings, so this disk growth was invisible.

### Health semantics

Any prepared, running, interrupted, or partially-created transaction caused
`/health` to return HTTP 503. Operator attention is not the same as inability to
serve requests. Pending recovery information belongs in the health body while
liveness remains successful whenever the server and usable index are available.

Ordinary Docker Compose does not restart a container solely because a Docker
health check marks it unhealthy, although external supervisors may do so. The
503 behaviour was still the wrong service contract.

### Lock and descriptor scaling

The executor acquired locks for writes, moves, descendants, mapped
destinations, parents, and the graph, and held them simultaneously. At the
configured 1,000-file limit this could exceed a typical 1,024-descriptor process
limit.

### Approval churn

Plan digests included the revisions of the complete readable Markdown scan.
Unrelated Obsidian Sync activity could therefore invalidate an otherwise valid
move between preview and approval. The approval requirement was also coupled to
tool-registration feature flags instead of expressing an independent policy.

### Snapshot cost

Each operation retained both pre-images and staged post-images, requiring about
twice the touched content in scratch space before journal overhead. This was
disproportionate for recoverable stale-link failures.

## Minimal future design

If note move or folder rename support is revisited, implement the operations
directly rather than through a generic executor.

### Efficient semantic planning

Keep the useful semantic link scanner and its protections for frontmatter,
fenced code, inline code, aliases, embeds, headings, and ambiguity.

Build a lookup catalog once per operation:

```text
canonical path -> candidate notes
stem           -> candidate notes
alias          -> candidate notes
```

Precompute every affected note's proposed content and record the content
revision from the same read. Pre-authorize every affected write before changing
anything. If a complete readable/writable scan is impossible, either reject
automatic link rewriting or require an explicit move-without-rewrite mode.

### Rename first, then repair links

The safe order is:

1. validate and authorize the source, destination, and affected notes;
2. acquire one global semantic-operation lock;
3. revalidate the source and destination;
4. atomically rename the note or folder;
5. rewrite each affected note conditionally against its planned revision;
6. acquire and release each per-file lock individually;
7. report every completed, conflicted, and failed rewrite;
8. update or reconcile the index.

Renaming first makes the failure state a correctly moved object with stale
links. Rewriting first can leave links pointing to a destination that was never
created and must not be retained.

Revision conflicts must never trigger a blind overwrite. A result may therefore
be explicitly partial:

```json
{
  "status": "moved_with_incomplete_link_repairs",
  "from": "Old.md",
  "to": "New.md",
  "updated_links_in": ["A.md", "B.md"],
  "unresolved_links_in": ["C.md"]
}
```

This is roll-forward recovery. Do not roll the rename back after some link
rewrites have succeeded.

### Crash recovery is optional

The first replacement should not include automatic crash recovery. Once the
planner is efficient, the post-rename rewrite window should be short, and stale
links are visible and recoverable.

A later version may add either a repair operation accepting the old and new
paths, or one immutable roll-forward manifest. Such a manifest may record the
operation, paths, affected files, before/after revisions, and deterministic
rewrite instructions. It should be written once before the rename and deleted
after successful repair.

It must not:

- store full pre-image backups;
- be rewritten and fsynced after every file;
- attempt rollback;
- re-plan by resolving the now-missing source against the live post-move graph;
- make health return 503 merely because repair remains pending.

If a current file matches neither its recorded before nor after revision, leave
it unresolved for human/client review rather than overwriting it.

### Trash, restore, and permanent deletion

Folder trash and restore should call the existing secure storage rename
operations directly. If the source and `.trash` are on different mounts,
`rename(2)` returns `EXDEV`; report that limitation and do not fall back to a
non-atomic copy-and-delete sequence.

The hardened nested-bind home-server profile deliberately keeps delete and
restore disabled because its writable subfolders and vault-root `.trash` cross
mount boundaries.

Permanent recursive folder deletion should remain unavailable. It is destructive
and can stop halfway, while the intended deployment already has a safer trash or
backup/sync-history workflow.

### Bulk replacement

Keep bulk replacement disabled while this work is parked. If it is revived,
treat it explicitly as a bounded, non-transactional batch:

- require a dry-run preview;
- cap affected files and bytes;
- use a revision check for every note;
- hold only one per-file lock at a time;
- return separate succeeded, conflicted, and failed paths;
- never retry automatically;
- document that interruption can leave a partial result.

Do not bring back the generic transaction manager solely for bulk replacement.
Backups and Obsidian Sync history are the recovery layer unless real use shows a
need for a separately designed, retention-bounded snapshot feature.

## Features and concepts to remove

A future replacement should not carry forward:

- the generic `MutationExecutor` and transaction-plan hierarchy;
- pre-image and post-image transaction directories;
- intent/applied per-file journal steps;
- rollback and unknown-state classification;
- caller-facing transaction `operation_id` values;
- whole-vault approved digests;
- transaction recovery/discard CLI commands;
- transaction-derived health 503 responses;
- simultaneous acquisition of every affected path lock.

Dry-run previews may remain useful, but they should not require a digest-based
two-call approval protocol. The opt-in feature flags already keep these tools
out of the MCP schema by default.

## Possible future PR scope

If this work resumes, keep the replacement PR narrowly focused on safe semantic
moves:

1. efficient semantic catalog and tested link transformations;
2. rename-first ordering;
3. revision-checked link rewrites;
4. one-at-a-time locks;
5. explicit partial-success reporting;
6. direct atomic trash and restore;
7. no permanent recursive deletion;
8. no transactional bulk replacement.

Obsidian CLI could later be evaluated as an optional semantic backend when a
full local Obsidian instance is already available, but it is not required for
this smaller filesystem design and should not become a mandatory dependency.
