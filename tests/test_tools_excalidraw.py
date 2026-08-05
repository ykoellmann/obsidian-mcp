from __future__ import annotations

import pytest

from obsidian_mcp.tools.excalidraw import (
    list_excalidraw,
    patch_excalidraw,
    read_excalidraw,
    write_excalidraw,
)

# ── list_excalidraw / read_excalidraw ────────────────────────────────────

def test_list_excalidraw_finds_files(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md")
    files = list_excalidraw()
    assert "drawing.excalidraw.md" in files


def test_read_excalidraw_basic(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md", elements=[
        {"id": "1", "type": "text", "x": 10, "y": 20, "text": "Hello"},
    ])
    result = read_excalidraw("drawing.excalidraw.md")
    assert result["path"] == "drawing.excalidraw.md"
    assert len(result["elements"]) == 1
    assert result["elements"][0]["text"] == "Hello"


def test_read_excalidraw_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        read_excalidraw("ghost.excalidraw.md")


def test_read_excalidraw_rejects_non_excalidraw_note(tmp_path, vault_factory):
    vault_factory({"plain.excalidraw.md": "# Not actually an Excalidraw file"})
    with pytest.raises(ValueError, match="not an Excalidraw file"):
        read_excalidraw("plain.excalidraw.md")


def test_read_excalidraw_invalid_json_raises(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "bad.excalidraw.md").write_text(
        "---\nexcalidraw-plugin: parsed\n---\n## Drawing\n```json\nNOT JSON {{{\n```\n"
    )
    with pytest.raises(ValueError, match="Invalid Excalidraw JSON"):
        read_excalidraw("bad.excalidraw.md")


# ── write_excalidraw ──────────────────────────────────────────────────────

def test_write_excalidraw_creates_file(tmp_path, vault_factory):
    vault_factory({})
    result = write_excalidraw("drawing.excalidraw.md", elements=[
        {"type": "rectangle", "x": 0, "y": 0},
    ])
    assert result["status"] == "written"
    assert result["elements"] == 1
    assert (tmp_path / "drawing.excalidraw.md").exists()


def test_write_excalidraw_has_plugin_frontmatter(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md")
    content = (tmp_path / "drawing.excalidraw.md").read_text()
    assert "excalidraw-plugin: parsed" in content


def test_write_excalidraw_autogenerates_ids(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md", elements=[{"type": "rectangle"}])
    result = read_excalidraw("drawing.excalidraw.md")
    assert "id" in result["elements"][0]


def test_write_excalidraw_creates_subdirectory(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("Drawings/board.excalidraw.md")
    assert (tmp_path / "Drawings" / "board.excalidraw.md").exists()


def test_write_excalidraw_empty(tmp_path, vault_factory):
    vault_factory({})
    result = write_excalidraw("empty.excalidraw.md")
    assert result["elements"] == 0
    assert read_excalidraw("empty.excalidraw.md")["elements"] == []


def test_write_excalidraw_overwrites_existing(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md", elements=[{"type": "text", "text": "Old"}])
    write_excalidraw("drawing.excalidraw.md", elements=[{"type": "text", "text": "New"}])
    result = read_excalidraw("drawing.excalidraw.md")
    assert len(result["elements"]) == 1
    assert result["elements"][0]["text"] == "New"


def test_write_excalidraw_custom_app_state(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md", app_state={"viewBackgroundColor": "#000000"})
    result = read_excalidraw("drawing.excalidraw.md")
    assert result["app_state"]["viewBackgroundColor"] == "#000000"


def test_write_excalidraw_does_not_pollute_index_tags(vault_factory):
    idx = vault_factory({})
    write_excalidraw(
        "drawing.excalidraw.md",
        elements=[{"type": "rectangle", "backgroundColor": "#ffffff"}],
        index=idx,
    )
    assert "ffffff" not in idx.get_all_tags_with_counts()
    assert "drawing.excalidraw.md" not in idx.get_all_notes()


# ── patch_excalidraw ──────────────────────────────────────────────────────

def test_patch_excalidraw_add_element(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md", elements=[
        {"id": "a1", "type": "text", "text": "Original"},
    ])
    result = patch_excalidraw("drawing.excalidraw.md", add_elements=[
        {"type": "text", "text": "Added"},
    ])
    assert result["elements"] == 2
    texts = [e["text"] for e in read_excalidraw("drawing.excalidraw.md")["elements"]]
    assert "Original" in texts
    assert "Added" in texts


def test_patch_excalidraw_update_element(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md", elements=[
        {"id": "e1", "type": "text", "text": "Old text"},
    ])
    patch_excalidraw("drawing.excalidraw.md", update_elements=[{"id": "e1", "text": "New text"}])
    result = read_excalidraw("drawing.excalidraw.md")
    assert result["elements"][0]["text"] == "New text"
    assert result["elements"][0]["id"] == "e1"


def test_patch_excalidraw_delete_element(tmp_path, vault_factory):
    vault_factory({})
    write_excalidraw("drawing.excalidraw.md", elements=[
        {"id": "a", "type": "text", "text": "A"},
        {"id": "b", "type": "text", "text": "B"},
    ])
    result = patch_excalidraw("drawing.excalidraw.md", delete_element_ids=["a"])
    assert result["elements"] == 1
    remaining = read_excalidraw("drawing.excalidraw.md")["elements"]
    assert [e["id"] for e in remaining] == ["b"]


def test_patch_excalidraw_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        patch_excalidraw("ghost.excalidraw.md", add_elements=[])
