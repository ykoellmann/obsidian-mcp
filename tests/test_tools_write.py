from __future__ import annotations

import pytest
import yaml

from obsidian_mcp.tools.write import (
    WritePermissionError,
    append_to_note,
    delete_note,
    manage_tags,
    move_note,
    patch_frontmatter,
    patch_note,
    write_note,
)

# ── write_note ────────────────────────────────────────────────────────────

def test_write_note_creates_file(tmp_path, vault_factory):
    vault_factory({})
    result = write_note("new.md", "# Hello\nWorld")
    assert result["status"] == "written"
    assert (tmp_path / "new.md").exists()
    assert (tmp_path / "new.md").read_text() == "# Hello\nWorld"


def test_write_note_overwrites_existing(tmp_path, vault_factory):
    vault_factory({"old.md": "old content"})
    write_note("old.md", "new content")
    assert (tmp_path / "old.md").read_text() == "new content"


def test_write_note_creates_subdirectories(tmp_path, vault_factory):
    vault_factory({})
    write_note("Deep/Nested/note.md", "content")
    assert (tmp_path / "Deep" / "Nested" / "note.md").exists()


def test_write_note_updates_index(vault_factory):
    idx = vault_factory({})
    write_note("note.md", "---\ntags: [test]\n---\nBody", index=idx)
    assert "note.md" in idx.get_all_notes()
    assert "note.md" in idx.get_notes_by_tag("test")


def test_write_note_read_only_raises(vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("READ_ONLY", "true")
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = None
    with pytest.raises(WritePermissionError, match="read-only"):
        write_note("note.md", "content")


def test_write_note_write_paths_allowed(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("WRITE_PATHS", "Allowed/")
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = None
    write_note("Allowed/note.md", "content")
    assert (tmp_path / "Allowed" / "note.md").exists()


def test_write_note_write_paths_blocked(vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("WRITE_PATHS", "Allowed/")
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = None
    with pytest.raises(WritePermissionError):
        write_note("Blocked/note.md", "content")


# ── patch_note ────────────────────────────────────────────────────────────

def test_patch_note_replace(tmp_path, vault_factory):
    vault_factory({"note.md": "## Sec\nold body\n"})
    patch_note("note.md", "Sec", "new body", mode="replace")
    content = (tmp_path / "note.md").read_text()
    assert "new body" in content
    assert "old body" not in content


def test_patch_note_insert_before(tmp_path, vault_factory):
    vault_factory({"note.md": "## Sec\nbody\n"})
    patch_note("note.md", "Sec", "BEFORE", mode="insert_before")
    content = (tmp_path / "note.md").read_text()
    assert content.index("BEFORE") < content.index("## Sec")


def test_patch_note_insert_after(tmp_path, vault_factory):
    vault_factory({"note.md": "## Sec\nbody\n"})
    patch_note("note.md", "Sec", "AFTER_HEADING", mode="insert_after")
    content = (tmp_path / "note.md").read_text()
    assert content.index("## Sec") < content.index("AFTER_HEADING") < content.index("body")


def test_patch_note_append(tmp_path, vault_factory):
    vault_factory({"note.md": "## Sec\nbody\n\n## Next\nother"})
    patch_note("note.md", "Sec", "APPENDED", mode="append")
    content = (tmp_path / "note.md").read_text()
    assert content.index("body") < content.index("APPENDED") < content.index("Next")


def test_patch_note_block_ref_replace(tmp_path, vault_factory):
    vault_factory({"note.md": "Some text here. ^my-block\nOther line."})
    patch_note("note.md", "my-block", "Replaced text.", target_type="block_ref", mode="replace")
    content = (tmp_path / "note.md").read_text()
    assert "Replaced text." in content
    assert "Some text here" not in content


def test_patch_note_block_ref_insert_before(tmp_path, vault_factory):
    vault_factory({"note.md": "Important line. ^key\nNext"})
    patch_note("note.md", "key", "INSERTED", target_type="block_ref", mode="insert_before")
    content = (tmp_path / "note.md").read_text()
    assert content.index("INSERTED") < content.index("Important line")


def test_patch_note_block_ref_insert_after(tmp_path, vault_factory):
    vault_factory({"note.md": "Important line. ^key\nNext line."})
    patch_note("note.md", "key", "APPENDED", target_type="block_ref", mode="insert_after")
    content = (tmp_path / "note.md").read_text()
    assert content.index("^key") < content.index("APPENDED")


def test_patch_note_block_ref_append(tmp_path, vault_factory):
    vault_factory({"note.md": "Line with block. ^myblock\nOther."})
    patch_note("note.md", "myblock", "AFTER", target_type="block_ref", mode="append")
    content = (tmp_path / "note.md").read_text()
    assert "AFTER" in content
    assert content.index("^myblock") < content.index("AFTER")


# ── delete_note ───────────────────────────────────────────────────────────

def test_delete_note_to_trash(tmp_path, vault_factory):
    vault_factory({"note.md": "content"})
    result = delete_note("note.md", trash=True)
    assert result["status"] == "deleted"
    assert not (tmp_path / "note.md").exists()
    assert (tmp_path / ".trash" / "note.md").exists()


def test_delete_note_permanent(tmp_path, vault_factory):
    vault_factory({"note.md": "content"})
    delete_note("note.md", trash=False)
    assert not (tmp_path / "note.md").exists()
    assert not (tmp_path / ".trash").exists()


def test_delete_note_updates_index(vault_factory):
    idx = vault_factory({"note.md": "content"})
    delete_note("note.md", index=idx)
    assert "note.md" not in idx.get_all_notes()


def test_delete_note_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        delete_note("ghost.md")


def test_delete_note_trash_conflict(tmp_path, vault_factory):
    vault_factory({"a.md": "first", "b.md": "second"})
    (tmp_path / ".trash").mkdir()
    (tmp_path / ".trash" / "a.md").write_text("already there")
    delete_note("a.md", trash=True)
    trash_files = list((tmp_path / ".trash").iterdir())
    assert len(trash_files) == 2  # original + renamed


# ── append_to_note ────────────────────────────────────────────────────────

def test_append_creates_note(tmp_path, vault_factory):
    vault_factory({})
    append_to_note("new.md", "Hello World")
    assert (tmp_path / "new.md").read_text() == "Hello World"


def test_append_adds_content(tmp_path, vault_factory):
    vault_factory({"note.md": "Line 1"})
    append_to_note("note.md", "Line 2")
    content = (tmp_path / "note.md").read_text()
    assert "Line 1" in content
    assert "Line 2" in content


def test_append_to_section(tmp_path, vault_factory):
    vault_factory({"note.md": "## Tasks\n- old task\n\n## Notes\nother"})
    append_to_note("note.md", "- new task", section="Tasks")
    content = (tmp_path / "note.md").read_text()
    assert "old task" in content
    assert "new task" in content
    assert content.index("old task") < content.index("new task") < content.index("Notes")


def test_append_no_create_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        append_to_note("ghost.md", "content", create=False)


# ── patch_frontmatter ─────────────────────────────────────────────────────

def test_patch_frontmatter_scalar(tmp_path, vault_factory):
    vault_factory({"note.md": "---\nstatus: inbox\n---\nBody"})
    patch_frontmatter("note.md", {"status": "done"})
    content = (tmp_path / "note.md").read_text()
    assert "status: done" in content
    assert "Body" in content


def test_patch_frontmatter_merge_arrays(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags:\n- existing\n---\nBody"})
    patch_frontmatter("note.md", {"tags": ["new"]}, merge_arrays=True)
    content = (tmp_path / "note.md").read_text()
    assert "existing" in content
    assert "new" in content


def test_patch_frontmatter_replace_arrays(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags:\n- old\n---\nBody"})
    patch_frontmatter("note.md", {"tags": ["new"]}, merge_arrays=False)
    content = (tmp_path / "note.md").read_text()
    assert "old" not in content
    assert "new" in content


def test_patch_frontmatter_creates_fm_if_missing(tmp_path, vault_factory):
    vault_factory({"note.md": "Just body text"})
    patch_frontmatter("note.md", {"status": "active"})
    content = (tmp_path / "note.md").read_text()
    assert "---" in content
    assert "status: active" in content
    assert "Just body text" in content


# ── manage_tags ───────────────────────────────────────────────────────────

def test_manage_tags_add(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [existing]\n---\nBody"})
    manage_tags("note.md", add=["new-tag"])
    content = (tmp_path / "note.md").read_text()
    assert "existing" in content
    assert "new-tag" in content


def test_manage_tags_remove_from_frontmatter(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [foo, bar]\n---\nBody"})
    manage_tags("note.md", remove=["foo"])
    content = (tmp_path / "note.md").read_text()
    assert "foo" not in content
    assert "bar" in content


def test_manage_tags_remove_inline(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [foo]\n---\nSome text #foo in body"})
    manage_tags("note.md", remove=["foo"])
    content = (tmp_path / "note.md").read_text()
    assert "#foo" not in content
    assert "Some text" in content


def test_manage_tags_no_duplicates(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [existing]\n---\n"})
    manage_tags("note.md", add=["existing"])
    fm_text = (tmp_path / "note.md").read_text().split("---")[1]
    fm = yaml.safe_load(fm_text)
    assert fm["tags"].count("existing") == 1


def test_manage_tags_creates_tags_key(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntitle: Test\n---\nBody"})
    manage_tags("note.md", add=["new"])
    content = (tmp_path / "note.md").read_text()
    assert "new" in content


def test_manage_tags_updates_index(vault_factory):
    idx = vault_factory({"note.md": "---\ntags: []\n---\n"})
    manage_tags("note.md", add=["mytag"], index=idx)
    assert "note.md" in idx.get_notes_by_tag("mytag")


# ── move_note ─────────────────────────────────────────────────────────────

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
