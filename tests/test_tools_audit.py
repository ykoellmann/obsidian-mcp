from __future__ import annotations

import time

from obsidian_mcp.tools.audit import get_audit_log, get_note_history, log_write


def test_log_write_then_get_audit_log(vault_factory):
    vault_factory({})
    log_write("write_note_tool", "a.md", "wrote note")
    entries = get_audit_log()
    assert len(entries) == 1
    assert entries[0]["tool"] == "write_note_tool"
    assert entries[0]["path"] == "a.md"
    assert entries[0]["summary"] == "wrote note"
    assert "timestamp" in entries[0]


def test_get_audit_log_filters_by_path(vault_factory):
    vault_factory({})
    log_write("write_note_tool", "a.md", "wrote a")
    log_write("write_note_tool", "b.md", "wrote b")
    entries = get_audit_log(path="a.md")
    assert len(entries) == 1
    assert entries[0]["path"] == "a.md"


def test_get_audit_log_filters_by_tool(vault_factory):
    vault_factory({})
    log_write("write_note_tool", "a.md", "wrote a")
    log_write("delete_note_tool", "a.md", "deleted a")
    entries = get_audit_log(tool="delete_note_tool")
    assert len(entries) == 1
    assert entries[0]["tool"] == "delete_note_tool"


def test_get_audit_log_most_recent_first(vault_factory):
    vault_factory({})
    log_write("write_note_tool", "a.md", "first")
    time.sleep(0.01)
    log_write("write_note_tool", "a.md", "second")
    entries = get_audit_log()
    assert entries[0]["summary"] == "second"
    assert entries[1]["summary"] == "first"


def test_get_audit_log_limit(vault_factory):
    vault_factory({})
    for i in range(5):
        log_write("write_note_tool", "a.md", f"write {i}")
    entries = get_audit_log(limit=2)
    assert len(entries) == 2


def test_get_audit_log_empty_when_no_entries(vault_factory):
    vault_factory({})
    assert get_audit_log() == []


def test_get_note_history_scoped_to_path(vault_factory):
    vault_factory({})
    log_write("write_note_tool", "a.md", "wrote a")
    log_write("write_note_tool", "b.md", "wrote b")
    log_write("patch_frontmatter_tool", "a.md", "patched a")
    history = get_note_history("a.md")
    assert len(history) == 2
    assert all(e["path"] == "a.md" for e in history)


def test_log_write_never_raises_on_bad_vault(monkeypatch):
    # log_write is best-effort — a config/IO problem must not raise up into
    # the caller of the write tool it's instrumenting.
    import obsidian_mcp.config as cfg_mod
    monkeypatch.delenv("VAULT_PATH", raising=False)
    cfg_mod._config = None
    log_write("write_note_tool", "a.md", "should not raise")
