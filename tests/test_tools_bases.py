from __future__ import annotations

import pytest
import yaml

from obsidian_mcp.tools.bases import list_bases, patch_base, read_base, write_base

# ── list_bases / read_base ──────────────────────────────────────────────────

def test_list_bases_finds_base(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "projects.base").write_text("views:\n  - type: table\n    name: All\n")
    bases = list_bases()
    assert "projects.base" in bases


def test_read_base_basic(tmp_path, vault_factory):
    vault_factory({})
    data = {
        "filters": {"and": ['status != "done"']},
        "views": [{"type": "table", "name": "Open"}],
    }
    (tmp_path / "projects.base").write_text(yaml.safe_dump(data))
    result = read_base("projects.base")
    assert result["path"] == "projects.base"
    assert result["views"][0]["name"] == "Open"
    assert result["formulas"] == {}
    assert result["properties"] == {}


def test_read_base_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        read_base("ghost.base")


def test_read_base_invalid_yaml_raises(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "bad.base").write_text("views: [unterminated")
    with pytest.raises(ValueError, match="Invalid base YAML"):
        read_base("bad.base")


def test_read_base_non_mapping_raises(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "bad.base").write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        read_base("bad.base")


# ── write_base ───────────────────────────────────────────────────────────────

def test_write_base_creates_file(tmp_path, vault_factory):
    vault_factory({})
    result = write_base("projects.base", views=[{"type": "table", "name": "All"}])
    assert result["status"] == "written"
    assert result["views"] == 1
    assert (tmp_path / "projects.base").exists()


def test_write_base_is_valid_yaml(tmp_path, vault_factory):
    vault_factory({})
    write_base(
        "projects.base",
        filters={"and": ['status != "done"']},
        formulas={"ppu": "(price / age).toFixed(2)"},
        properties={"status": {"displayName": "Status"}},
        views=[{"type": "table", "name": "Open"}],
    )
    raw = (tmp_path / "projects.base").read_text()
    data = yaml.safe_load(raw)
    assert data["filters"] == {"and": ['status != "done"']}
    assert data["formulas"]["ppu"] == "(price / age).toFixed(2)"
    assert data["views"][0]["type"] == "table"


def test_write_base_creates_subdirectory(tmp_path, vault_factory):
    vault_factory({})
    write_base("Bases/projects.base", views=[{"type": "table"}])
    assert (tmp_path / "Bases" / "projects.base").exists()


def test_write_base_empty(tmp_path, vault_factory):
    vault_factory({})
    result = write_base("empty.base")
    assert result["views"] == 0
    data = yaml.safe_load((tmp_path / "empty.base").read_text())
    assert data == {}


def test_write_base_overwrites_existing(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", views=[{"type": "table", "name": "Old"}])
    write_base("projects.base", views=[{"type": "table", "name": "New"}])
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    assert len(data["views"]) == 1
    assert data["views"][0]["name"] == "New"


def test_write_base_rejects_view_without_type(tmp_path, vault_factory):
    vault_factory({})
    with pytest.raises(ValueError, match="missing required 'type'"):
        write_base("projects.base", views=[{"name": "Open"}])


def test_write_base_rejects_non_list_views(tmp_path, vault_factory):
    vault_factory({})
    with pytest.raises(ValueError, match="'views' must be a list"):
        write_base("projects.base", views={"type": "table"})


def test_write_base_returns_known_properties_from_existing_bases(tmp_path, vault_factory):
    vault_factory({})
    write_base("first.base", properties={"status": {"displayName": "Status"}})
    result = write_base("second.base", views=[{"type": "table"}])
    assert result["known_properties"]["status"]["displayName"] == "Status"


# ── patch_base ───────────────────────────────────────────────────────────────

def test_patch_base_update_formulas(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", formulas={"a": "1"})
    result = patch_base("projects.base", update_formulas={"b": "2"})
    assert result["status"] == "patched"
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    assert data["formulas"] == {"a": "1", "b": "2"}


def test_patch_base_delete_formula_key(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", formulas={"a": "1", "b": "2"})
    patch_base("projects.base", delete_formula_keys=["a"])
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    assert data["formulas"] == {"b": "2"}


def test_patch_base_update_properties(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", properties={"status": {"displayName": "Status"}})
    patch_base("projects.base", update_properties={"price": {"displayName": "Price"}})
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    assert data["properties"]["status"]["displayName"] == "Status"
    assert data["properties"]["price"]["displayName"] == "Price"


def test_patch_base_set_filters_replaces_block(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", filters={"and": ["a"]})
    patch_base("projects.base", set_filters={"or": ["b", "c"]})
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    assert data["filters"] == {"or": ["b", "c"]}


def test_patch_base_add_view(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", views=[{"type": "table", "name": "Original"}])
    result = patch_base("projects.base", add_views=[{"type": "cards", "name": "Added"}])
    assert result["views"] == 2
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    names = [v["name"] for v in data["views"]]
    assert "Original" in names
    assert "Added" in names


def test_patch_base_update_view_by_name(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", views=[{"type": "table", "name": "Open", "limit": 5}])
    patch_base("projects.base", update_views=[{"name": "Open", "limit": 10}])
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    assert data["views"][0]["limit"] == 10
    assert data["views"][0]["type"] == "table"


def test_patch_base_delete_view_by_name(tmp_path, vault_factory):
    vault_factory({})
    write_base("projects.base", views=[
        {"type": "table", "name": "Keep"},
        {"type": "table", "name": "Drop"},
    ])
    result = patch_base("projects.base", delete_view_names=["Drop"])
    assert result["views"] == 1
    data = yaml.safe_load((tmp_path / "projects.base").read_text())
    assert [v["name"] for v in data["views"]] == ["Keep"]


def test_patch_base_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        patch_base("ghost.base", update_formulas={"a": "1"})
