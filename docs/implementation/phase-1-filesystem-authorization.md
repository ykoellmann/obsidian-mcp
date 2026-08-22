# Phase 1: Central filesystem authorization

## Status

Proposed implementation plan.

## Objective

Make the configured vault access policy apply consistently to every read and
mutation, regardless of which MCP tool or custom HTTP route performs the
operation.

This phase closes the current gaps where attachment and Canvas writes bypass
`READ_ONLY` and `WRITE_PATHS`, and where excluded paths can still be accessed by
naming them directly.

## Security invariants

After this phase, the following invariants must hold:

1. Every filesystem path is canonicalized before it is authorized.
2. Every mutation passes through one write-authorization function.
3. `READ_ONLY=true` prevents all mutations, including custom HTTP routes and
   optional plugin-format tools.
4. `WRITE_PATHS` is an allowlist based on complete path components, not string
   prefix coincidence.
5. A direct read cannot bypass a configured read-deny boundary.
6. Security-sensitive paths are unwritable by default.
7. Tool-specific writers cannot be used to write arbitrary file formats.
8. Destructive operations cannot target the vault root.
9. Symlinks cannot be used to escape or redirect an authorized operation.
10. Authorization failure occurs before a temporary file, lock file, directory,
    or destination file is created.

## Configuration model

Retain `EXCLUDE_PATHS` as a discovery/indexing filter for compatibility, but do
not describe it as a security boundary. Add explicit security settings:

```env
# Paths that no MCP operation may read. A denied directory denies descendants.
DENY_READ_PATHS=private/,.obsidian/,.trash/

# Paths in which MCP-originated writes are allowed. Empty means no path-based
# restriction, unless READ_ONLY is enabled.
WRITE_PATHS=AI-Memory/,AI-Output/

# Paths that cannot be written even when they are beneath WRITE_PATHS.
DENY_WRITE_PATHS=.obsidian/,.trash/,_AI_INSTRUCTIONS.md

# Disable permanent deletion unless explicitly enabled by the operator.
ALLOW_PERMANENT_DELETE=false
```

Defaults should prioritize safety for a network deployment:

- `DENY_READ_PATHS`: `.obsidian/,.trash/`
- `DENY_WRITE_PATHS`: `.obsidian/,.trash/,_AI_INSTRUCTIONS.md`
- `ALLOW_PERMANENT_DELETE`: `false`

`EXCLUDE_PATHS` may continue to hide content from listing, indexing, search and
graph tools without necessarily forbidding an intentional direct read. This
separation prevents a compatibility-oriented display option from being confused
with access control.

Document that OS/container mounts remain the final enforcement boundary.

## Core design

### Canonical path type

Add `src/obsidian_mcp/storage/policy.py` with an immutable result type:

```python
@dataclass(frozen=True)
class VaultPath:
    relative: str       # normalized POSIX path relative to vault root
    absolute: Path      # resolved absolute path
```

Only the policy module creates `VaultPath` values. Callers should not authorize a
raw string and then reconstruct a path separately.

Canonicalization must:

- reject NUL bytes;
- reject absolute paths;
- normalize `.` and separators;
- reject an empty path when the operation requires a file or child directory;
- resolve the vault root and target;
- verify the resolved target remains beneath the resolved vault root;
- reject existing symlink components by default;
- for a new target, inspect every existing parent component for symlinks;
- return a normalized `/`-separated relative path.

Use `Path.is_relative_to()` rather than string-prefix comparison.

### Policy object

Add a process-wide `VaultAccessPolicy` constructed from `Config`:

```python
class VaultAccessPolicy:
    def resolve_read(self, path: str) -> VaultPath: ...
    def resolve_write(self, path: str) -> VaultPath: ...
    def resolve_delete(self, path: str, *, permanent: bool) -> VaultPath: ...
    def can_read(self, path: str) -> bool: ...
    def can_write(self, path: str) -> bool: ...
```

Path-list matching must be component-aware:

- `AI/` matches `AI/note.md`;
- `AI/` does not match `AI-old/note.md`;
- a file entry matches only that exact file;
- normalize configuration entries once at startup;
- reject configuration entries that are absolute or escape the vault.

Add specific exception types:

```python
VaultPathError
ReadPermissionError
WritePermissionError
ProtectedPathError
PermanentDeleteDisabledError
InvalidFileTypeError
```

MCP tools should expose a concise error. Server logs may include the operation
and authenticated principal, but must not include file contents or credentials.

### Storage gateway

Move authorization into the lowest shared filesystem layer. Replace generic
helpers that accept arbitrary roots with a vault-scoped gateway:

```python
class VaultStorage:
    def read_text(self, path: str) -> str: ...
    def read_bytes(self, path: str) -> bytes: ...
    def write_text_atomic(self, path: str, content: str) -> None: ...
    def write_bytes_atomic(self, path: str, content: bytes) -> None: ...
    def list_dir(self, path: str) -> list[VaultEntry]: ...
```

Every public method performs its own authorization. Tool modules must not call
`Path.read_text`, `Path.read_bytes`, `Path.write_text`, `os.replace`, `unlink`,
`rename`, `shutil.move`, or `shutil.rmtree` on vault paths directly.

Temporary files should be created beside the destination only after write
authorization succeeds. Their names must not escape the authorized parent.

### Protected internal operations

Trash and lock storage need narrowly scoped internal APIs rather than a general
authorization bypass.

- Move lock files outside the vault to `LOCK_PATH`, defaulting to
  `${FASTMCP_HOME}/locks` or `/data/locks` in Docker.
- A trash operation may write beneath `.trash/` only through a dedicated
  `VaultStorage.trash()` method after authorizing deletion of the source.
- A restore operation may read from `.trash/` only through a dedicated
  `VaultStorage.restore()` method and must authorize the destination.
- No MCP-supplied path may directly invoke an "internal" bypass.

## File-level changes

### `src/obsidian_mcp/config.py`

- Parse and validate `DENY_READ_PATHS`, `DENY_WRITE_PATHS`, `LOCK_PATH`, and
  `ALLOW_PERMANENT_DELETE`.
- Normalize all path-list entries at startup.
- Reject contradictory or escaping entries with `ConfigError`.
- Keep configuration immutable after startup.

### `src/obsidian_mcp/storage/policy.py` (new)

- Implement canonicalization, path-list matching and the policy object.
- Contain all policy exception types.
- Unit test this module independently of MCP and tool registration.

### `src/obsidian_mcp/storage/filesystem.py`

- Introduce `VaultStorage` or refactor existing helpers to require a
  `VaultAccessPolicy`.
- Remove authorization-free vault writes from the public API.
- Preserve atomic same-directory replacement.
- Add safe list, move, delete, trash and restore primitives.

### `src/obsidian_mcp/storage/locking.py`

- Store locks under `LOCK_PATH` using a hash of the canonical vault-relative
  path.
- Do not create `*.lock` files inside the synced vault.

### Tool modules

Replace direct filesystem access in:

- `tools/read.py`
- `tools/write.py`
- `tools/attachments.py`
- `tools/canvas.py`
- `tools/excalidraw.py`
- `tools/kanban.py`
- `tools/bases.py`
- `tools/templates.py`
- `tools/folders.py`
- `tools/query.py`

Specific requirements:

- attachment writes must call the same write policy as note writes;
- direct attachment GET and PUT must enforce read/write policy respectively;
- Canvas paths must end in `.canvas`;
- Bases paths must end in `.base`;
- Kanban paths must end in `.md`;
- Excalidraw paths must end in `.excalidraw.md`;
- note mutation paths must end in `.md`;
- attachment writes must reject Markdown and protected executable/configuration
  destinations;
- `list_folder` must not reveal denied children;
- templates and transclusions must not read denied paths;
- `_AI_INSTRUCTIONS.md` remains readable only if allowed and is unwritable by
  default.

### `src/obsidian_mcp/domain/index.py`

- Build and update the index through the read policy.
- Ensure denied notes cannot enter alias, tag, block, backlink or outlink indexes.
- If policy settings change, require restart and full index rebuild.

### `src/obsidian_mcp/server.py`

- Apply the storage gateway to MCP resources and custom routes.
- Map policy errors to stable MCP errors and HTTP 403 responses.
- Do not leak denied paths through health or error responses.

## Test plan

### Policy unit tests

Cover:

- normal nested paths;
- absolute paths;
- `..` traversal;
- sibling-prefix confusion (`AI` versus `AI-old`);
- Windows separators on all supported platforms;
- empty/root path handling;
- existing file symlinks;
- symlinked parent directories;
- non-existing descendants beneath a symlink;
- Unicode and case behavior without assuming a case-insensitive filesystem;
- invalid configuration entries.

### Authorization matrix

Parameterize every mutating tool against:

| Configuration | Inside allowed path | Outside allowed path |
|---|---:|---:|
| `READ_ONLY=true` | denied | denied |
| `WRITE_PATHS=` | allowed | allowed |
| `WRITE_PATHS=AI-Output/` | allowed | denied |
| target in `DENY_WRITE_PATHS` | denied | denied |

Parameterize every reading tool against allowed and `DENY_READ_PATHS` targets.

At minimum, explicitly regress:

- attachment MCP upload;
- attachment HTTP PUT and GET;
- Canvas write and patch;
- Bases, Kanban and Excalidraw reads and writes;
- MCP `vault://notes/{path}` resources;
- direct read of a known denied filename;
- folder listing containing a denied child;
- write to `.obsidian/plugins/example/main.js`;
- permanent note and folder deletion;
- deletion with an empty path.

### OS-boundary integration test

Run the server with `/vault` mounted read-only and one nested read-write mount.
Verify that:

1. reads work throughout the permitted read tree;
2. writes work inside the nested mount;
3. writes outside it are denied by policy;
4. bypassing the policy in a deliberate test fixture is denied by the OS;
5. lock files appear under `/data/locks`, not the vault.

## Migration and compatibility

1. Add new configuration with warnings when operators rely only on
   `EXCLUDE_PATHS`.
2. Update `.env.example`, README and Compose examples.
3. Default permanent deletion to disabled.
4. Treat new read-deny behavior as a documented security feature and potential
   breaking change.
5. Release as a minor version only if defaults preserve existing direct reads;
   otherwise release as a major version.

## Completion criteria

- All vault filesystem calls are routed through the storage gateway or a
  documented internal primitive.
- No tool module contains an unaudited direct vault write.
- The authorization matrix passes for every registered tool and custom route.
- Existing functional tests pass.
- A repository search for direct filesystem mutation has no unexplained result.
- Documentation states clearly which settings are discovery filters and which
  are security boundaries.

