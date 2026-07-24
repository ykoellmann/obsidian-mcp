from __future__ import annotations

import pytest

from second_brain_mcp.tools.write import move_note


def test_move_note_renames_file(tmp_path, vault_factory):
    vault_factory({"old.md": "Content", "other.md": "No links"})
    move_note("old.md", "new.md")
    assert (tmp_path / "new.md").exists()
    assert not (tmp_path / "old.md").exists()


def test_move_note_rewrites_backlinks(tmp_path, vault_factory):
    idx = vault_factory({"old.md": "Content", "linker.md": "See [[old]] for details."})
    result = move_note("old.md", "new.md", idx)
    assert "linker.md" in result["updated_links_in"]
    rewritten = (tmp_path / "linker.md").read_text()
    assert "[[new]]" in rewritten
    assert "[[old]]" not in rewritten


def test_move_note_preserves_alias_in_link(tmp_path, vault_factory):
    vault_factory({"old.md": "Content", "linker.md": "See [[old|My Label]] here."})
    move_note("old.md", "new.md")
    rewritten = (tmp_path / "linker.md").read_text()
    assert "[[new|My Label]]" in rewritten


def test_move_note_updates_index(vault_factory):
    idx = vault_factory({"old.md": "Content", "linker.md": "[[old]]"})
    move_note("old.md", "new.md", idx)
    assert "new.md" in idx.get_all_notes()
    assert "old.md" not in idx.get_all_notes()
    assert "linker.md" in idx.get_backlinks("new.md")


def test_move_note_missing_source_raises(vault_factory):
    vault_factory({"other.md": "x"})
    with pytest.raises(FileNotFoundError):
        move_note("nonexistent.md", "new.md")


def test_move_note_existing_target_raises(vault_factory):
    vault_factory({"old.md": "Content", "new.md": "Already exists"})
    with pytest.raises(FileExistsError):
        move_note("old.md", "new.md")


def test_move_note_into_subfolder(tmp_path, vault_factory):
    vault_factory({"note.md": "Content", "linker.md": "[[note]]"})
    move_note("note.md", "Archiv/note.md")
    assert (tmp_path / "Archiv" / "note.md").exists()
    rewritten = (tmp_path / "linker.md").read_text()
    assert "[[note]]" in rewritten  # stem unchanged, link stays valid
