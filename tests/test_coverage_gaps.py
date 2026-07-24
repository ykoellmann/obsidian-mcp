"""Tests covering previously missing scenarios: B1, B2, B3, B4, B5, B6."""
from __future__ import annotations

import json

import pytest

from second_brain_mcp.tools.write import WritePermissionError

# ── write_note + read_note round-trip (B1) ────────────────────────────────────

def test_write_note_creates_file(tmp_path, vault_factory):
    vault_factory({})
    from second_brain_mcp.tools.write import write_note
    result = write_note("new.md", "# Hello\nWorld")
    assert result["status"] == "written"
    assert (tmp_path / "new.md").exists()
    assert (tmp_path / "new.md").read_text() == "# Hello\nWorld"


def test_write_note_overwrites_existing(tmp_path, vault_factory):
    vault_factory({"old.md": "old content"})
    from second_brain_mcp.tools.write import write_note
    write_note("old.md", "new content")
    assert (tmp_path / "old.md").read_text() == "new content"


def test_write_note_creates_subdirectories(tmp_path, vault_factory):
    vault_factory({})
    from second_brain_mcp.tools.write import write_note
    write_note("Deep/Nested/note.md", "content")
    assert (tmp_path / "Deep" / "Nested" / "note.md").exists()


def test_write_note_updates_index(vault_factory):
    idx = vault_factory({})
    from second_brain_mcp.tools.write import write_note
    write_note("note.md", "---\ntags: [test]\n---\nBody", index=idx)
    assert "note.md" in idx.get_all_notes()
    assert "note.md" in idx.get_notes_by_tag("test")


def test_read_note_returns_structured_data(vault_factory):
    vault_factory({
        "note.md": (
            "---\ntitle: My Note\ntags: [foo, bar]\naliases: [MN]\n---\n"
            "# My Note\n\nSome body text.\n\n- [ ] A task\n\nLinks to [[other]].\n"
        )
    })
    from second_brain_mcp.tools.read import read_note
    result = read_note("note.md")
    assert result["frontmatter"]["title"] == "My Note"
    assert "foo" in result["tags"]
    assert "bar" in result["tags"]
    assert any(wl["target"] == "other" for wl in result["wikilinks"])
    assert any(t["text"] == "A task" for t in result["tasks"])


def test_read_note_missing_raises(vault_factory):
    vault_factory({})
    from second_brain_mcp.tools.read import read_note
    with pytest.raises(FileNotFoundError):
        read_note("nonexistent.md")


# ── write_note permission checks (B2) ────────────────────────────────────────

def test_write_note_read_only_raises(vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("READ_ONLY", "true")
    import second_brain_mcp.config as cfg_mod
    cfg_mod._config = None
    from second_brain_mcp.tools.write import write_note
    with pytest.raises(WritePermissionError, match="read-only"):
        write_note("note.md", "content")


def test_write_note_write_paths_allowed(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("WRITE_PATHS", "Allowed/")
    import second_brain_mcp.config as cfg_mod
    cfg_mod._config = None
    from second_brain_mcp.tools.write import write_note
    write_note("Allowed/note.md", "content")
    assert (tmp_path / "Allowed" / "note.md").exists()


def test_write_note_write_paths_blocked(vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("WRITE_PATHS", "Allowed/")
    import second_brain_mcp.config as cfg_mod
    cfg_mod._config = None
    from second_brain_mcp.tools.write import write_note
    with pytest.raises(WritePermissionError):
        write_note("Blocked/note.md", "content")


# ── canvas error handling (B3) ────────────────────────────────────────────────

def test_list_canvases_finds_canvas(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "board.canvas").write_text('{"nodes":[],"edges":[]}')
    from second_brain_mcp.tools.canvas import list_canvases
    canvases = list_canvases()
    assert "board.canvas" in canvases


def test_read_canvas_basic(tmp_path, vault_factory):
    vault_factory({})
    data = {
        "nodes": [{"id": "1", "type": "text", "text": "Hello", "x": 0, "y": 0}],
        "edges": [],
    }
    (tmp_path / "board.canvas").write_text(json.dumps(data))
    from second_brain_mcp.tools.canvas import read_canvas
    result = read_canvas("board.canvas")
    assert result["path"] == "board.canvas"
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["text"] == "Hello"


def test_read_canvas_missing_raises(vault_factory):
    vault_factory({})
    from second_brain_mcp.tools.canvas import read_canvas
    with pytest.raises(FileNotFoundError):
        read_canvas("ghost.canvas")


def test_read_canvas_invalid_json_raises(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "bad.canvas").write_text("NOT JSON {{{")
    from second_brain_mcp.tools.canvas import read_canvas
    with pytest.raises(ValueError, match="Invalid canvas JSON"):
        read_canvas("bad.canvas")


# ── Write → Index → Query integration (B4) ───────────────────────────────────

def test_write_then_search(vault_factory):
    vault_factory({})
    from second_brain_mcp.tools.read import search_notes
    from second_brain_mcp.tools.write import write_note
    write_note("note.md", "Unique phrase XYZ123 here")
    results = search_notes("XYZ123")
    assert any(r["path"] == "note.md" for r in results)


def test_write_creates_backlink(vault_factory):
    idx = vault_factory({"target.md": "I am the target."})
    from second_brain_mcp.tools.write import write_note
    write_note("source.md", "Links to [[target]].", index=idx)
    backlinks = idx.get_backlinks("target.md")
    assert "source.md" in backlinks


def test_delete_removes_backlinks(vault_factory):
    idx = vault_factory({
        "source.md": "Links to [[target]].",
        "target.md": "I am target.",
    })
    assert "source.md" in idx.get_backlinks("target.md")
    from second_brain_mcp.tools.write import delete_note
    delete_note("source.md", trash=False, index=idx)
    assert "source.md" not in idx.get_backlinks("target.md")


def test_manage_tags_then_query(vault_factory):
    idx = vault_factory({"note.md": "---\ntags: []\n---\n"})
    from second_brain_mcp.tools.query import query_notes
    from second_brain_mcp.tools.write import manage_tags
    manage_tags("note.md", add=["integration-test"], index=idx)
    results = query_notes(idx, tags=["integration-test"])
    assert any(r["path"] == "note.md" for r in results)


# ── block_ref patch modes (B5) ────────────────────────────────────────────────

def test_patch_block_ref_insert_after(tmp_path, vault_factory):
    vault_factory({"note.md": "Important line. ^key\nNext line."})
    from second_brain_mcp.tools.write import patch_note
    patch_note("note.md", "key", "APPENDED", target_type="block_ref", mode="insert_after")
    content = (tmp_path / "note.md").read_text()
    assert content.index("^key") < content.index("APPENDED")


def test_patch_block_ref_append(tmp_path, vault_factory):
    vault_factory({"note.md": "Line with block. ^myblock\nOther."})
    from second_brain_mcp.tools.write import patch_note
    patch_note("note.md", "myblock", "AFTER", target_type="block_ref", mode="append")
    content = (tmp_path / "note.md").read_text()
    assert "AFTER" in content
    assert content.index("^myblock") < content.index("AFTER")


# ── resolve_alias contract (D6) ───────────────────────────────────────────────

def test_resolve_alias_found(vault_factory):
    idx = vault_factory({"Python Tips.md": "---\naliases: [Python]\n---\n"})
    from second_brain_mcp.tools.query import resolve_alias
    assert resolve_alias("Python", idx) == "Python Tips.md"


def test_resolve_alias_not_found_returns_none(vault_factory):
    idx = vault_factory({})
    from second_brain_mcp.tools.query import resolve_alias
    assert resolve_alias("DoesNotExist", idx) is None


# ── has_note (C4) ─────────────────────────────────────────────────────────────

def test_has_note_by_stem(vault_factory):
    idx = vault_factory({"My Note.md": "content"})
    assert idx.has_note("My Note")


def test_has_note_by_path(vault_factory):
    idx = vault_factory({"Folder/Note.md": "content"})
    assert idx.has_note("Folder/Note.md")


def test_has_note_by_path_without_ext(vault_factory):
    idx = vault_factory({"Folder/Note.md": "content"})
    assert idx.has_note("Folder/Note")


def test_has_note_not_found(vault_factory):
    idx = vault_factory({"real.md": "content"})
    assert not idx.has_note("ghost")
