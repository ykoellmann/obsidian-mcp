from __future__ import annotations

import pytest

import obsidian_mcp.config as cfg_mod
import obsidian_mcp.tools.folders as folders_module
from obsidian_mcp.tools.folders import (
    create_folder,
    delete_folder,
    list_files,
    list_folder,
    list_trash,
    rename_folder,
    restore_folder,
)

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
    with pytest.raises(Exception, match="Permanent deletion is disabled"):
        delete_folder("Temp", trash=False)
    assert (tmp_path / "Temp" / "note.md").exists()
    assert not (tmp_path / ".trash" / "Temp").exists()


def test_delete_folder_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        delete_folder("NonExistent")


def test_delete_folder_not_a_dir_raises(vault_factory):
    vault_factory({"note.md": "content"})
    with pytest.raises(ValueError, match="Not a folder"):
        delete_folder("note.md")


# ── list_trash / restore_folder ──────────────────────────────────────────

def test_list_trash_empty_when_no_trash_dir(vault_factory):
    vault_factory({})
    assert list_trash() == {"items": []}


def test_list_trash_lists_files_and_folders(tmp_path, vault_factory):
    vault_factory({"a.md": "A", "Temp/note.md": "content"})
    from obsidian_mcp.tools.write import delete_note

    delete_note("a.md", trash=True)
    delete_folder("Temp", trash=True)
    items = list_trash()["items"]
    names = {i["name"]: i["type"] for i in items}
    assert names == {"a.md": "file", "Temp": "folder"}


def test_trash_metadata_is_narrow_exception_to_denied_read(tmp_path, vault_factory, monkeypatch):
    vault_factory({"a.md": "secret"})
    monkeypatch.setenv("DENY_READ_PATHS", ".obsidian/,.trash/")
    monkeypatch.setenv("LOCK_PATH", str(tmp_path.parent / f"{tmp_path.name}-locks"))
    import obsidian_mcp.config as cfg_mod

    cfg_mod._config = None
    from obsidian_mcp.storage.policy import ReadPermissionError
    from obsidian_mcp.tools.read import read_note
    from obsidian_mcp.tools.write import delete_note

    delete_note("a.md", trash=True)
    assert list_trash()["items"][0]["name"] == "a.md"
    with pytest.raises(ReadPermissionError):
        read_note(".trash/a.md")


def test_restore_folder_puts_it_back(tmp_path, vault_factory):
    vault_factory({"Temp/note.md": "content"})
    delete_folder("Temp", trash=True)
    result = restore_folder("Temp", "Temp")
    assert result["status"] == "restored"
    assert (tmp_path / "Temp" / "note.md").read_text() == "content"
    assert not (tmp_path / ".trash" / "Temp").exists()


def test_restore_folder_to_different_path(tmp_path, vault_factory):
    vault_factory({"Temp/note.md": "content"})
    delete_folder("Temp", trash=True)
    restore_folder("Temp", "Restored")
    assert (tmp_path / "Restored" / "note.md").read_text() == "content"


def test_restore_folder_updates_index(vault_factory):
    idx = vault_factory({"Temp/note.md": "content"})
    delete_folder("Temp", trash=True)
    result = restore_folder("Temp", "Temp", index=idx)
    assert result["notes_restored"] == 1
    assert "Temp/note.md" in idx.get_all_notes()


def test_restore_folder_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        restore_folder("NonExistent", "Somewhere")


def test_restore_folder_existing_target_raises(tmp_path, vault_factory):
    vault_factory({"Temp/note.md": "content"})
    delete_folder("Temp", trash=True)
    (tmp_path / "Temp").mkdir()
    with pytest.raises(FileExistsError):
        restore_folder("Temp", "Temp")


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


def test_list_folder_recursive_full_tree(vault_factory):
    vault_factory({
        "a.md": "A",
        "Projekte/active.md": "active",
        "Projekte/Sub/deep.md": "deep",
    })
    result = list_folder(recursive=True)
    assert result["path"] == "/"
    assert "a.md" in result["tree"]["files"]
    assert "Projekte/active.md" in result["tree"]["folders"]["Projekte"]["files"]
    assert "Projekte/Sub/deep.md" in result["tree"]["folders"]["Projekte"]["folders"]["Sub"]["files"]


def test_list_folder_recursive_max_depth(vault_factory):
    vault_factory({
        "Projekte/active.md": "active",
        "Projekte/Sub/deep.md": "deep",
    })
    result = list_folder(recursive=True, max_depth=1)
    # depth 1 = immediate children only; "Sub" itself doesn't get descended into
    assert "Projekte" in result["tree"]["folders"]
    assert result["tree"]["folders"]["Projekte"]["folders"] == {}


def test_list_folder_recursive_hides_dotfiles(tmp_path, vault_factory):
    vault_factory({"a.md": "A"})
    (tmp_path / ".obsidian").mkdir()
    result = list_folder(recursive=True)
    assert ".obsidian" not in result["tree"]["folders"]


# ── list_files ────────────────────────────────────────────────────────────

def test_list_files_returns_all_types(vault_factory):
    vault_factory({"note.md": "A", "note.md.lock": "lock"})
    results = list_files()
    assert "note.md" in results
    assert "note.md.lock" in results


def test_list_files_extension_filter(vault_factory):
    vault_factory({"note.md": "A", "note.md.lock": "lock", "other.canvas": "{}"})
    results = list_files(extension="lock")
    assert results == ["note.md.lock"]


def test_list_files_extension_filter_dot_optional(vault_factory):
    vault_factory({"a.canvas": "{}", "b.md": "content"})
    assert list_files(extension=".canvas") == ["a.canvas"]


def test_list_files_folder_scope(vault_factory):
    vault_factory({"a.md": "A", "Projekte/b.md": "B"})
    results = list_files(folder="Projekte")
    assert results == ["Projekte/b.md"]


def test_list_files_skips_hidden(tmp_path, vault_factory):
    vault_factory({"a.md": "A"})
    (tmp_path / ".trash").mkdir()
    (tmp_path / ".trash" / "x.md").write_text("x")
    results = list_files()
    assert all(not r.startswith(".trash") for r in results)


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


def test_delete_folder_preauthorizes_tree_before_lock(vault_factory, monkeypatch):
    vault_factory({"folder/protected.md": "secret"})
    monkeypatch.setenv("WRITE_PATHS", "folder/")
    monkeypatch.setenv("DENY_WRITE_PATHS", "folder/protected.md")
    cfg_mod._config = None
    lock_attempted = False

    def unexpected_lock(*args, **kwargs):
        nonlocal lock_attempted
        lock_attempted = True
        raise AssertionError("lock must not be created before complete authorization")

    monkeypatch.setattr(folders_module, "acquire_lock", unexpected_lock)
    with pytest.raises(PermissionError):
        delete_folder("folder")
    assert lock_attempted is False


def test_rename_folder_releases_partial_lock_set(vault_factory, monkeypatch):
    vault_factory(
        {
            "Old/note.md": "note",
            "one.md": "[[Old/note]]",
            "two.md": "[[Old/note]]",
        }
    )

    class FakeLock:
        released = False

        def release(self):
            self.released = True

    first = FakeLock()
    calls = 0

    def acquire_then_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        raise RuntimeError("injected lock failure")

    monkeypatch.setattr(folders_module, "acquire_lock", acquire_then_fail)
    with pytest.raises(RuntimeError, match="injected"):
        rename_folder("Old", "New")
    assert first.released is True
