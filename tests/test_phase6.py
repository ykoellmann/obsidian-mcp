from __future__ import annotations

import base64

import pytest

from second_brain_mcp.tools.attachments import add_attachment, list_attachments, read_attachment
from second_brain_mcp.tools.write import append_to_note, delete_note, patch_frontmatter, patch_note

# ── delete_note ───────────────────────────────────────────────────────────────

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


# ── append_to_note ────────────────────────────────────────────────────────────

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


# ── patch_frontmatter ─────────────────────────────────────────────────────────

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


# ── enhanced patch_note ───────────────────────────────────────────────────────

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


# ── attachments ───────────────────────────────────────────────────────────────

def test_list_attachments_finds_image(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    results = list_attachments()
    assert any(r["path"] == "image.png" for r in results)
    assert any(r["mime_type"] == "image/png" for r in results)


def test_list_attachments_excludes_md(tmp_path, vault_factory):
    vault_factory({"note.md": "content"})
    results = list_attachments()
    assert not any(r["path"].endswith(".md") for r in results)


def test_read_attachment_text(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "data.csv").write_text("a,b,c\n1,2,3")
    result = read_attachment("data.csv")
    assert result["encoding"] == "utf-8"
    assert "a,b,c" in result["content"]


def test_read_attachment_binary_base64(tmp_path, vault_factory):
    vault_factory({})
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    (tmp_path / "image.png").write_bytes(raw)
    result = read_attachment("image.png")
    assert result["encoding"] == "base64"
    assert base64.b64decode(result["content"]) == raw


def test_add_attachment_round_trip(tmp_path, vault_factory):
    vault_factory({})
    raw = b"PDF-CONTENT-\x00\x01\x02"
    encoded = base64.b64encode(raw).decode()
    result = add_attachment("docs/file.pdf", encoded)
    assert result["status"] == "written"
    assert (tmp_path / "docs" / "file.pdf").exists()
    read_back = read_attachment("docs/file.pdf")
    assert base64.b64decode(read_back["content"]) == raw


def test_add_attachment_rejects_md(vault_factory):
    vault_factory({})
    with pytest.raises(ValueError):
        add_attachment("note.md", base64.b64encode(b"text").decode())


def test_add_attachment_invalid_base64(vault_factory):
    vault_factory({})
    with pytest.raises(ValueError):
        add_attachment("file.png", "not-valid-base64!!!")
