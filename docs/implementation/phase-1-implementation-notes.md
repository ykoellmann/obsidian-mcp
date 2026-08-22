# Phase 1 implementation notes

Phase 1 is implemented on the local `codex/phase-1-filesystem-authorization`
branch. The implementation adds one canonical `VaultAccessPolicy` and routes
MCP tool, resource, and attachment HTTP filesystem operations through
`VaultStorage`.

Verification completed:

- `PYTHONPATH=src .venv/bin/pytest -q` — 450 tests passing.
- `PYTHONPATH=src .venv/bin/ruff check src tests` — clean.
- `docker compose -f docker-compose.home-server.yml config --quiet` — valid
  with representative required variables (runtime mount/ownership testing is
  still target-host specific).
- Policy tests cover traversal, Windows separators, component-aware allowlists,
  denied reads, symlinks, descriptor-relative I/O, recursive move/trash/delete
  preauthorization, deterministic symlink-swap behavior, read-only writes,
  root deletion, permanent-delete opt-in, and authorization before
  temporary/parent/destination creation.
- A parameterized matrix covers note, folder, attachment, template, Canvas,
  Excalidraw, Kanban, and Bases mutations; read tools; resources; and the
  attachment HTTP route. Invalid HTTP credentials are checked before path
  authorization (401 rather than a protected-path oracle).
- Attachment writes use a positive extension allowlist and reject hidden
  components, extensionless names, scripts, configuration files, and Markdown
  for MCP add, token minting, and HTTP PUT. Reads intentionally have a separate
  compatibility rule: hidden components and policy-denied paths are rejected,
  while a caller that already has read access may retrieve a visible existing
  file of another type. Attachment listing is limited to the positive allowlist.
- `MAX_ATTACHMENT_BYTES` defaults to 25 MiB, must be positive, and is enforced
  before MCP writes and during both declared-length and streamed HTTP uploads.
- Index updates purge stale entries before applying read policy, including
  readable-to-denied renames; watchdog move events handle both file and
  directory source/destination paths.
- High-impact mutation registrations are default-off: `ENABLE_MOVE`,
  `ENABLE_FOLDER_RENAME`, `ENABLE_BULK_REPLACE`, and `ENABLE_DELETE`. The
  underlying functions remain available for tests and Phase 2.
- Native lock defaults use the external temp directory
  `<system-temp>/obsidian-mcp-locks`; Docker explicitly supplies `/data/locks`.
  Public `tree_paths()` filters every descendant through read policy, while
  internal mutation preflight retains an unfiltered descriptor-relative scan.
- All lock paths are hashed and stored under `LOCK_PATH`; no tool creates lock
  files in the vault. A configured lock-path creation failure is fatal; the
  temporary-directory fallback exists only for standalone callers with no
  configuration.
- `/health` no longer discloses the absolute vault path.
- `.trash/` is a dedicated internal capability: `trash`, `restore`, and
  `list_trash` may inspect/mutate only the trash metadata and item named by the
  operation after source/destination authorization. It is not a general
  read/write bypass, and direct `.trash/...` reads remain denied by default.
- `docker-compose.home-server.yml` provides the Cloudflare Tunnel deployment
  shape: full vault `:ro`, nested AI directories `:rw`, matching restricted
  `WRITE_PATHS`, a non-root UID/GID, a private internal MCP network plus
  Cloudflared egress, explicit host-owned `/data`, digest-pinned image
  configuration, and no host port on the MCP service. Static Compose checks
  are automated. Cloudflare Access Managed OAuth remains edge authentication;
  the origin still requires `API_KEY` until a future trusted-proxy phase.

Known limitations carried into later phases:

1. The compatibility helpers in `storage.filesystem` accept arbitrary explicit
   temporary roots for standalone callers. When pointed at the configured
   vault, they construct the configured policy and cannot bypass it. Tool code
   uses `VaultStorage` directly.
2. The OS-boundary test with a read-only full-vault mount and nested read-write
   mount is deployment-specific and remains to be run in the target container
   environment. The same applies to verifying host UID/GID permissions and the
   Cloudflare Tunnel sidecar route with a real Docker daemon.
3. Multi-file note/folder moves preflight affected files and use the gateway,
   but they do not yet have Phase 2's transactional mutation plan or rollback
   journal. They should remain disabled for untrusted network callers until
   Phase 2 is complete.
