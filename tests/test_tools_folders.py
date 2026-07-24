from __future__ import annotations

import pytest

from obsidian_mcp.tools.folders import create_folder, delete_folder, list_folder, rename_folder

# ── create_folder ─────────────────────────────────────────────────────────

def test_create_folder_basic(tmp_path, vault_factory):
    vault_factory({})
    result = create_folder("Projekte")
    assert result["status"] == "created"
    assert (tmp_path / "Projekte").is_dir()


def test_create_folder_nested(tmp_path, vault_factory):
    vault_factory({})
    create_folder("Projekte/Aktiv/2026")
    assert (tmp_path / "Projekte" / "Aktiv" / "2026").is_dir()


def test_create_folder_idempotent(tmp_path, vault_factory):
    vault_factory({})
    create_folder("Ordner")
    result = create_folder("Ordner")
    assert result["status"] == "created"


def test_create_folder_over_file_raises(tmp_path, vault_factory):
    vault_factory({"note.md": "content"})
    with pytest.raises(ValueError, match="file already exists"):
        create_folder("note.md")


# ── delete_folder ─────────────────────────────────────────────────────────

def test_delete_folder_to_trash(tmp_path, vault_factory):
    vault_factory({"Temp/note.md": "content"})
    result = delete_folder("Temp")
    assert result["status"] == "deleted"
    assert result["trash"] is True
    assert not (tmp_path / "Temp").exists()
    assert (tmp_path / ".trash" / "Temp").is_dir()


def test_delete_folder_permanent(tmp_path, vault_factory):
    vault_factory({"Temp/note.md": "content"})
    delete_folder("Temp", trash=False)
    assert not (tmp_path / "Temp").exists()
    assert not (tmp_path / ".trash" / "Temp").exists()


def test_delete_folder_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        delete_folder("NonExistent")


def test_delete_folder_not_a_dir_raises(vault_factory):
    vault_factory({"note.md": "content"})
    with pytest.raises(ValueError, match="Not a folder"):
        delete_folder("note.md")


# ── list_folder ───────────────────────────────────────────────────────────

def test_list_folder_root(vault_factory):
    vault_factory({
        "a.md": "A",
        "b.md": "B",
        "Projekte/c.md": "C",
    })
    result = list_folder()
    assert "Projekte" in result["folders"]
    assert "a.md" in result["files"]
    assert "b.md" in result["files"]
    assert result["path"] == "/"


def test_list_folder_subfolder(vault_factory):
    vault_factory({
        "Projekte/active.md": "active",
        "Projekte/done.md": "done",
    })
    result = list_folder("Projekte")
    assert result["path"] == "Projekte"
    assert "Projekte/active.md" in result["files"]
    assert "Projekte/done.md" in result["files"]
    assert result["folders"] == []


def test_list_folder_hides_dotfiles(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".trash").mkdir()
    result = list_folder()
    assert ".obsidian" not in result["folders"]
    assert ".trash" not in result["folders"]


def test_list_folder_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        list_folder("NoSuchFolder")


def test_list_folder_non_dir_raises(vault_factory):
    vault_factory({"note.md": "content"})
    with pytest.raises(ValueError, match="Not a folder"):
        list_folder("note.md")


# ── rename_folder ─────────────────────────────────────────────────────────

def test_rename_folder_basic(tmp_path, vault_factory):
    vault_factory({"Projekte/note.md": "content"})
    result = rename_folder("Projekte", "Projects")
    assert result["from"] == "Projekte"
    assert result["to"] == "Projects"
    assert result["notes_moved"] == 1
    assert not (tmp_path / "Projekte").exists()
    assert (tmp_path / "Projects" / "note.md").exists()


def test_rename_folder_rewrites_path_based_links(tmp_path, vault_factory):
    vault_factory({
        "Projekte/note.md": "content",
        "index.md": "See [[Projekte/note]] for details",
    })
    rename_folder("Projekte", "Projects")
    content = (tmp_path / "index.md").read_text()
    assert "[[Projects/note]]" in content
    assert "[[Projekte/note]]" not in content


def test_rename_folder_preserves_alias_in_link(tmp_path, vault_factory):
    vault_factory({
        "Projekte/note.md": "content",
        "index.md": "[[Projekte/note|My Project]]",
    })
    rename_folder("Projekte", "Projects")
    content = (tmp_path / "index.md").read_text()
    assert "[[Projects/note|My Project]]" in content


def test_rename_folder_preserves_heading_in_link(tmp_path, vault_factory):
    vault_factory({
        "Projekte/note.md": "content",
        "index.md": "[[Projekte/note#Introduction]]",
    })
    rename_folder("Projekte", "Projects")
    content = (tmp_path / "index.md").read_text()
    assert "[[Projects/note#Introduction]]" in content


def test_rename_folder_does_not_touch_stem_links(tmp_path, vault_factory):
    vault_factory({
        "Projekte/note.md": "content",
        "index.md": "See [[note]] here",
    })
    rename_folder("Projekte", "Projects")
    content = (tmp_path / "index.md").read_text()
    assert "[[note]]" in content


def test_rename_folder_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        rename_folder("NonExistent", "Other")


def test_rename_folder_target_exists_raises(vault_factory):
    vault_factory({
        "Projekte/a.md": "A",
        "Projects/b.md": "B",
    })
    with pytest.raises(FileExistsError):
        rename_folder("Projekte", "Projects")


def test_rename_folder_nested_move(tmp_path, vault_factory):
    vault_factory({"Projekte/sub/deep.md": "deep content"})
    rename_folder("Projekte", "Projects")
    assert (tmp_path / "Projects" / "sub" / "deep.md").exists()


def test_rename_folder_updates_index(vault_factory):
    idx = vault_factory({"Projekte/note.md": "content"})
    rename_folder("Projekte", "Projects", index=idx)
    all_notes = idx.get_all_notes()
    assert "Projects/note.md" in all_notes
    assert "Projekte/note.md" not in all_notes
