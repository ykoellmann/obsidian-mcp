#!/usr/bin/env python3
"""Standalone vault health-check: runs lint_schema_tool's logic directly
(no MCP client/network round-trip needed) and, only if it finds frontmatter
schema violations, drops a note into the vault so a human notices without
anyone needing to have asked. Silent (no note, no vault write) when the
vault is clean — that's the point, no noise on every run.

Meant to be invoked periodically by cron; see README.md's "Health-Check
Cron" section for wiring examples (Docker container cron, or host cron +
`docker exec`).

Usage:
    VAULT_PATH=/path/to/vault python scripts/health_check.py

Env vars (same ones the server itself reads, see README):
    VAULT_PATH        (required)
    EXCLUDE_PATHS      (optional, default: "private,.obsidian")
    HEALTH_CHECK_INBOX (optional, default: "Inbox" — folder the report note
                         is written into; point it at your vault's actual
                         inbox folder, e.g. "00-Inbox")
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from obsidian_mcp.config import get_config  # noqa: E402
from obsidian_mcp.domain.index import VaultIndex  # noqa: E402
from obsidian_mcp.storage.filesystem import VaultStorage  # noqa: E402
from obsidian_mcp.tools.lint import lint_schema  # noqa: E402


def _report_note(violations: list[dict], today: str) -> str:
    lines = [
        "---",
        "status: inbox",
        "type: resource",
        f"created: {today}",
        "tags: [health-check]",
        "---",
        f"## Schema violations found ({len(violations)})",
        "",
        f"Automated health-check run ({datetime.now(UTC).isoformat(timespec='seconds')}), "
        "checked against the enums declared in `_AI_INSTRUCTIONS.md`.",
        "",
    ]
    for v in violations:
        lines.append(f"- `{v['path']}` — field `{v['field']}`: `{v['found']}` not in {v['expected_enum']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    cfg = get_config()
    index = VaultIndex(cfg.vault_path, exclude_paths=cfg.exclude_paths)
    index.build()

    result = lint_schema(index)
    violations = result["violations"]

    if not violations:
        print("health-check: no schema violations found.")
        return 0

    inbox = os.environ.get("HEALTH_CHECK_INBOX", "Inbox").strip("/")
    today = date.today().isoformat()
    note_path = f"{inbox}/health-check-{today}.md" if inbox else f"health-check-{today}.md"
    VaultStorage.from_config(cfg).write_text_atomic(
        note_path, _report_note(violations, today)
    )
    print(f"health-check: {len(violations)} violation(s) found — wrote {note_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
