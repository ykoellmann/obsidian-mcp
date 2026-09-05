# Canonical MCP validation

Validated locally on 2026-09-05. No production or personal vault was modified.
The implementation follows the final user decision: a clean break with a single
17-tool base surface, no profiles and no legacy aliases.

## Automated and HTTP checks

- Complete Python suite: 650 passing tests, covering existing storage/auth/index
  protection and the new API, raw content, writes, search, pagination and batching.
- Ruff checks pass for source, tests and the smoke/benchmark scripts.
- Authenticated localhost HTTP smoke test passes against a disposable vault:
  create, duplicate-create rejection, raw read, outline/range read, batch read,
  append, stale append rejection, exact patch, frontmatter mutation, typed
  property search, listing, complete read/replacement and denied-path rejection.
  The advertised base surface contains exactly 17 tools.
- Actual cross-vault MCP calls verify discovery without a default, batch selection,
  missing selection rejection and cursor rejection across vaults.
- LiveSync's Node MCP suite: 19 tests passing. Only its conventions instruction
  changed; no storage contract or tool changes.

The HTTP exercise is a scripted protocol workflow, not a measured autonomous-agent
benchmark. A real user's model/client may still make different tool choices or
incur different latency. No new promise about Obsidian Sync's final-check/rename
race or exactly-once appends is made.

## Search measurements

Run with `python scripts/benchmark_canonical_search.py` in the project environment.
The script generates disposable approximately 1 KB notes. Results below are warm
local filesystem measurements on macOS; each query returns 20 results.

| Notes | Initial page | Continuation page | Previous scan implementation | Process peak RSS |
|---|---:|---:|---:|---:|
| 1,000 | 0.257 s | 0.248 s | 0.227 s | 42.4 MiB |
| 10,000 | 2.575 s | 2.589 s | 2.326 s | 87.6 MiB |

Peak RSS is process-wide and cumulative, not the isolated allocation of a search.
The continuation repeats the scan/hash to detect changed content. Exhausting a
large matching result set therefore multiplies this scan cost by the page count;
use the maximum page size when exhaustive retrieval is necessary. These numbers
do not represent cold disks, NAS mounts or YAML-filter-heavy workloads. No new
index/cache is justified by these measurements alone.

## Intentional limits

- Note reads/writes and attachment reads are capped at 512000 bytes, including
  whole-file validation for ranged reads. Attachment uploads retain the configured
  MAX_ATTACHMENT_BYTES cap and are create-only through MCP.
- YAML patching may normalize comments/formatting; body text is preserved. Invalid,
  duplicate-key, non-mapping, excessive or alias-bearing YAML fails explicitly.
- Search matches literal substrings with AND and filesystem-specific ranking;
  it does not reproduce LiveSync FTS tokenization or ranking.
- Listings are live keyset traversals. Search changes invalidate continuation;
  neither operation promises an atomic vault snapshot.
- Existing optional format tools remain behind their flags. Removed high-impact
  and profile environment settings do not restore the old catalogue.
