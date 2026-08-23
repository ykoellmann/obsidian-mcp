"""Append-only JSONL audit log stored outside the synced vault."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .locking import acquire_lock


def append_entry(
    audit_path: Path,
    lock_path: Path,
    tool: str,
    path: str | None,
    summary: str,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="microseconds"),
        "tool": tool,
        "path": path,
        "summary": summary,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(str(audit_path), lock_path=lock_path)
    try:
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        fd = os.open(audit_path, flags, 0o600)
        try:
            payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short audit-log write")
                view = view[written:]
        finally:
            os.close(fd)
    finally:
        lock.release()


def read_entries(
    audit_path: Path,
    path: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Most-recent-first. `since` is an ISO timestamp, inclusive; entries are
    compared as strings, which works because timestamps are always written
    in the same ISO-8601 (sortable) format."""
    entries: list[dict] = []
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(audit_path, flags)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, "r", encoding="utf-8") as f:
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
