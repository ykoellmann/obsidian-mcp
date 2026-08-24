from __future__ import annotations

import os
import threading

import pytest
from fastmcp import Client
from fastmcp.tools.base import ToolResult

import obsidian_mcp.config as config_module
from obsidian_mcp import server
from obsidian_mcp.domain.index import VaultIndex
from obsidian_mcp.domain.models import PreconditionRequiredError, RevisionConflictError
from obsidian_mcp.storage.filesystem import SecureStorageError, VaultStorage
from obsidian_mcp.storage.policy import ReadPermissionError, VaultAccessPolicy
from obsidian_mcp.storage.watcher import VaultWatcher
from obsidian_mcp.tools.read import read_note
from obsidian_mcp.tools.write import append_to_note, write_note


def _configured_storage(tmp_path, monkeypatch, *, strict: bool = False) -> VaultStorage:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("LOCK_PATH", str(tmp_path.parent / f"{tmp_path.name}-locks"))
    monkeypatch.setenv("REQUIRE_WRITE_PRECONDITIONS", "true" if strict else "false")
    config_module._config = None
    return VaultStorage.from_config()


def test_revision_round_trip_and_stale_write_conflict(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("one")
    storage = _configured_storage(tmp_path, monkeypatch)

    raw, first = storage.read_text_with_revision("note.md")
    assert raw == "one"
    second = storage.write_text_atomic("note.md", "two", expected_revision=first.token)
    assert second.token != first.token

    with pytest.raises(RevisionConflictError):
        storage.write_text_atomic("note.md", "stale", expected_revision=first.token)
    assert (tmp_path / "note.md").read_text() == "two"


def test_new_write_never_replaces_a_concurrent_create(tmp_path, monkeypatch):
    storage = _configured_storage(tmp_path, monkeypatch)
    original_link = __import__("os").link

    def concurrent_create(source, destination, **kwargs):
        (tmp_path / destination).write_text("sync won")
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr("obsidian_mcp.storage.filesystem.os.link", concurrent_create)
    with pytest.raises(RevisionConflictError):
        storage.write_text_atomic("new.md", "mcp")
    assert (tmp_path / "new.md").read_text() == "sync won"


def test_strict_full_overwrite_requires_revision_but_create_does_not(tmp_path, monkeypatch):
    (tmp_path / "existing.md").write_text("old")
    _configured_storage(tmp_path, monkeypatch, strict=True)

    with pytest.raises(PreconditionRequiredError):
        write_note("existing.md", "new")
    created = write_note("created.md", "new")
    assert created["revision"].startswith("sha256:")


def test_append_retry_with_same_revision_conflicts(tmp_path, monkeypatch):
    (tmp_path / "memory.md").write_text("start\n")
    _configured_storage(tmp_path, monkeypatch)
    initial = read_note("memory.md")["revision"]

    result = append_to_note("memory.md", "finding", expected_revision=initial)
    assert result["revision"] != initial
    with pytest.raises(RevisionConflictError):
        append_to_note("memory.md", "finding", expected_revision=initial)
    assert (tmp_path / "memory.md").read_text().count("finding") == 1


def test_reconcile_hashes_only_markdown_and_repairs_missed_change(tmp_path, monkeypatch):
    (tmp_path / "source.md").write_text("links [[old]]")
    (tmp_path / "asset.pdf").write_bytes(b"not hashed")
    index = VaultIndex(tmp_path)
    index.build()
    (tmp_path / "source.md").write_text("links [[new]]")

    reads: list[str] = []
    original = index._storage.read_text_with_revision

    def observed(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(index._storage, "read_text_with_revision", observed)
    index.reconcile()

    assert reads == ["source.md"]
    assert "source.md" not in index.get_backlinks("old")
    assert "source.md" in index.get_backlinks("new")
    status = index.reconcile_status()
    assert status["last_reconcile_at"] is not None
    assert status["last_reconcile_duration_seconds"] is not None
    assert status["last_reconcile_error"] is None


def test_per_note_reconcile_failure_is_telemetry_not_unreadiness(tmp_path, monkeypatch):
    (tmp_path / "good.md").write_text("good")
    (tmp_path / "bad.md").write_text("bad")
    index = VaultIndex(tmp_path)
    index.build()
    original = index._storage.read_text_with_revision

    def fail_one(path):
        if path == "bad.md":
            raise OSError("temporarily unavailable")
        return original(path)

    monkeypatch.setattr(index._storage, "read_text_with_revision", fail_one)
    index.reconcile()

    assert index.is_ready() is True
    assert index.reconcile_status()["last_reconcile_error"] == (
        "Failed to reconcile 1 Markdown note(s)"
    )


def test_watcher_debounces_repeated_events(tmp_path):
    watcher = VaultWatcher(tmp_path, debounce_ms=30)
    changes: list[str] = []
    callback = None
    delivered = threading.Event()

    def fake_watchdog(on_change):
        nonlocal callback
        callback = on_change
        return True

    def record(path: str) -> None:
        changes.append(path)
        delivered.set()

    watcher._try_watchdog = fake_watchdog
    try:
        watcher.start(record)
        assert callback is not None
        callback("note.md")
        callback("note.md")
        callback("note.md")
        assert delivered.wait(5)
    finally:
        watcher.stop()
    assert changes == ["note.md"]


def test_revision_reads_reject_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "pipe.md"
    os.mkfifo(fifo)
    storage = VaultStorage(VaultAccessPolicy(tmp_path))

    with pytest.raises(IsADirectoryError, match="not a regular file"):
        storage.read_text_with_revision("pipe.md")
    with pytest.raises(IsADirectoryError, match="not a regular file"):
        storage.revision("pipe.md")


def test_write_note_explicitly_rejects_write_only_path(tmp_path, monkeypatch):
    (tmp_path / "drop").mkdir()
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("LOCK_PATH", str(tmp_path.parent / f"{tmp_path.name}-locks"))
    monkeypatch.setenv("WRITE_PATHS", "drop/")
    monkeypatch.setenv("DENY_READ_PATHS", "drop/")
    config_module._config = None

    with pytest.raises(ReadPermissionError, match="write_note requires read access"):
        write_note("drop/note.md", "content")
    assert not (tmp_path / "drop" / "note.md").exists()

    # The central policy still permits a deliberately write-only primitive;
    # only read-dependent note workflows reject the overlap.
    VaultStorage.from_config().write_text_atomic("drop/raw.txt", "content", create_only=True)
    assert (tmp_path / "drop" / "raw.txt").read_text() == "content"


def test_server_conflict_boundary_marks_tool_result_as_error():
    @server._mutation_boundary
    def conflict():
        raise RevisionConflictError("note.md", "sha256:" + "0" * 64, None)

    result = conflict()
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.structured_content["error"] == "revision_conflict"


@pytest.mark.asyncio
async def test_scripted_mcp_calls_return_revision_and_error_conflict(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("first")
    _configured_storage(tmp_path, monkeypatch)
    index = VaultIndex(tmp_path)
    index.build()
    monkeypatch.setattr(server, "_index", index)

    async with Client(server.mcp) as client:
        read_result = await client.call_tool("read_note_tool", {"path": "note.md"})
        revision = read_result.data["revision"]
        await client.call_tool(
            "append_to_note_tool",
            {"path": "note.md", "content": "second", "expected_revision": revision},
        )
        conflict = await client.call_tool(
            "append_to_note_tool",
            {"path": "note.md", "content": "second", "expected_revision": revision},
            raise_on_error=False,
        )

    assert conflict.is_error is True
    assert conflict.structured_content["error"] == "revision_conflict"


def test_create_only_probe_cleans_up(tmp_path):
    storage = VaultStorage(VaultAccessPolicy(tmp_path))
    storage.probe_create_only_support()
    assert list(tmp_path.iterdir()) == []


def test_create_only_probe_creates_terminal_recursive_write_scope(tmp_path):
    (tmp_path / "deep").mkdir()
    storage = VaultStorage(VaultAccessPolicy(tmp_path, write_paths=["deep/nested/"]))

    storage.probe_create_only_support()

    assert (tmp_path / "deep" / "nested").is_dir()
    assert list((tmp_path / "deep" / "nested").iterdir()) == []


def test_create_only_probe_rejects_missing_parent_above_write_scope(tmp_path):
    storage = VaultStorage(VaultAccessPolicy(tmp_path, write_paths=["deep/note.md"]))

    with pytest.raises(SecureStorageError, match="writable parent"):
        storage.probe_create_only_support()
    assert not (tmp_path / "deep").exists()
