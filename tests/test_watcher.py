from __future__ import annotations

import time

from obsidian_mcp.storage.watcher import VaultWatcher


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("watcher condition was not observed")


def test_polling_invalidates_note_replaced_by_symlink(tmp_path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("indexed")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("outside")
    changes: list[str] = []
    monkeypatch.setenv("WATCH_MODE", "poll")
    watcher = VaultWatcher(tmp_path, poll_interval=0.02)
    try:
        watcher.start(changes.append)
        _wait_until(lambda: "note.md" in changes)
        changes.clear()
        note.unlink()
        note.symlink_to(outside)
        _wait_until(lambda: "note.md" in changes)
    finally:
        watcher.stop()


def test_polling_skips_file_that_disappears_during_stat(tmp_path, monkeypatch):
    (tmp_path / "first.md").write_text("first")
    (tmp_path / "second.md").write_text("second")
    changes: list[str] = []
    monkeypatch.setenv("WATCH_MODE", "poll")
    watcher = VaultWatcher(tmp_path, poll_interval=0.02)
    original_stat = watcher._storage.stat
    removed = False

    def disappearing_stat(path, *, read=True):
        nonlocal removed
        if path == "first.md" and not removed:
            (tmp_path / "first.md").unlink()
            removed = True
        return original_stat(path, read=read)

    monkeypatch.setattr(watcher._storage, "stat", disappearing_stat)
    try:
        watcher.start(changes.append)
        _wait_until(lambda: "second.md" in changes)
    finally:
        watcher.stop()
