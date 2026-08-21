"""Append-only JSONL audit log of write-tool activity, stored as a dotfile
inside the vault (`.mcp-audit.jsonl`) so it rides along with the vault's own
persistence (e.g. the Docker volume) without needing separate storage, while
staying invisible to note-facing tools the same way `.trash/`/`.obsidian` do."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .locking import acquire_lock

_AUDIT_FILENAME = ".mcp-audit.jsonl"


def _audit_path(vault_root: Path) -> Path:
    return vault_root / _AUDIT_FILENAME


def append_entry(vault_root: Path, tool: str, path: str | None, summary: str) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "tool": tool,
        "path": path,
        "summary": summary,
    }
    target = _audit_path(vault_root)
    lock = acquire_lock(str(target))
    try:
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    finally:
        lock.release()


def read_entries(
    vault_root: Path,
    path: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Most-recent-first. `since` is an ISO timestamp, inclusive; entries are
    compared as strings, which works because timestamps are always written
    in the same ISO-8601 (sortable) format."""
    target = _audit_path(vault_root)
    if not target.exists():
        return []

    entries: list[dict] = []
    with target.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if path is not None and entry.get("path") != path:
                continue
            if tool is not None and entry.get("tool") != tool:
                continue
            if since is not None and entry.get("timestamp", "") < since:
                continue
            entries.append(entry)

    # Reverse to file (append) order first so a stable sort breaks any
    # same-timestamp tie in favor of whichever entry was appended last.
    entries.reverse()
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:limit]
