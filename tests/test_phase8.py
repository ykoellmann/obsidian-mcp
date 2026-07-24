"""Phase 8: Canvas write tools + Dataview inline fields."""
from __future__ import annotations

import json

import pytest

from obsidian_mcp.tools.canvas import patch_canvas, write_canvas
from obsidian_mcp.tools.query import query_notes
from obsidian_mcp.tools.read import read_note

# ── write_canvas ──────────────────────────────────────────────────────────────

def test_write_canvas_creates_file(tmp_path, vault_factory):
    vault_factory({})
    result = write_canvas("board.canvas", nodes=[
        {"type": "text", "text": "Hello", "x": 0, "y": 0},
    ])
    assert result["status"] == "written"
    assert result["nodes"] == 1
    assert (tmp_path / "board.canvas").exists()


def test_write_canvas_is_valid_json(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas", nodes=[
        {"type": "text", "text": "Node 1", "x": 0, "y": 0},
        {"type": "text", "text": "Node 2", "x": 300, "y": 0},
    ], edges=[
        {"fromNode": "n1", "toNode": "n2"},
    ])
    raw = (tmp_path / "board.canvas").read_text()
    data = json.loads(raw)
    assert "nodes" in data
    assert "edges" in data


def test_write_canvas_autogenerates_ids(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas", nodes=[{"type": "text", "text": "Hello", "x": 0, "y": 0}])
    data = json.loads((tmp_path / "board.canvas").read_text())
    assert "id" in data["nodes"][0]


def test_write_canvas_with_file_node(tmp_path, vault_factory):
    vault_factory({"note.md": "content"})
    write_canvas("board.canvas", nodes=[
        {"type": "file", "file": "note.md", "x": 0, "y": 0, "width": 400, "height": 400},
    ])
    data = json.loads((tmp_path / "board.canvas").read_text())
    assert data["nodes"][0]["file"] == "note.md"


def test_write_canvas_creates_subdirectory(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("Canvas/board.canvas")
    assert (tmp_path / "Canvas" / "board.canvas").exists()


def test_write_canvas_empty(tmp_path, vault_factory):
    vault_factory({})
    result = write_canvas("empty.canvas")
    assert result["nodes"] == 0
    assert result["edges"] == 0
    data = json.loads((tmp_path / "empty.canvas").read_text())
    assert data == {"nodes": [], "edges": []}


def test_write_canvas_overwrites_existing(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas", nodes=[{"type": "text", "text": "Old", "x": 0, "y": 0}])
    write_canvas("board.canvas", nodes=[{"type": "text", "text": "New", "x": 0, "y": 0}])
    data = json.loads((tmp_path / "board.canvas").read_text())
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["text"] == "New"


# ── patch_canvas ──────────────────────────────────────────────────────────────

def test_patch_canvas_add_node(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas", nodes=[{"id": "a1", "type": "text", "text": "Original", "x": 0, "y": 0}])
    result = patch_canvas("board.canvas", add_nodes=[
        {"type": "text", "text": "Added", "x": 300, "y": 0},
    ])
    assert result["nodes"] == 2
    data = json.loads((tmp_path / "board.canvas").read_text())
    texts = [n["text"] for n in data["nodes"]]
    assert "Original" in texts
    assert "Added" in texts


def test_patch_canvas_update_node(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas", nodes=[{"id": "n1", "type": "text", "text": "Old text", "x": 0, "y": 0}])
    patch_canvas("board.canvas", update_nodes=[{"id": "n1", "text": "New text"}])
    data = json.loads((tmp_path / "board.canvas").read_text())
    assert data["nodes"][0]["text"] == "New text"
    assert data["nodes"][0]["id"] == "n1"


def test_patch_canvas_delete_node_removes_edges(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas",
        nodes=[
            {"id": "a", "type": "text", "text": "A", "x": 0, "y": 0},
            {"id": "b", "type": "text", "text": "B", "x": 300, "y": 0},
        ],
        edges=[{"id": "e1", "fromNode": "a", "toNode": "b"}],
    )
    result = patch_canvas("board.canvas", delete_node_ids=["a"])
    assert result["nodes"] == 1
    assert result["edges"] == 0
    data = json.loads((tmp_path / "board.canvas").read_text())
    assert len(data["edges"]) == 0


def test_patch_canvas_add_edge(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas", nodes=[
        {"id": "a", "type": "text", "text": "A", "x": 0, "y": 0},
        {"id": "b", "type": "text", "text": "B", "x": 300, "y": 0},
    ])
    result = patch_canvas("board.canvas", add_edges=[
        {"from": "a", "to": "b", "label": "connects"},
    ])
    assert result["edges"] == 1
    data = json.loads((tmp_path / "board.canvas").read_text())
    assert data["edges"][0]["fromNode"] == "a"
    assert data["edges"][0]["label"] == "connects"


def test_patch_canvas_delete_edge(tmp_path, vault_factory):
    vault_factory({})
    write_canvas("board.canvas",
        nodes=[
            {"id": "a", "type": "text", "text": "A", "x": 0, "y": 0},
            {"id": "b", "type": "text", "text": "B", "x": 300, "y": 0},
        ],
        edges=[{"id": "e1", "fromNode": "a", "toNode": "b"}],
    )
    result = patch_canvas("board.canvas", delete_edge_ids=["e1"])
    assert result["edges"] == 0


def test_patch_canvas_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        patch_canvas("ghost.canvas", add_nodes=[])


# ── inline fields — parser ────────────────────────────────────────────────────

def test_read_note_includes_inline_fields(vault_factory):
    vault_factory({"note.md": "# Title\n\nrating:: 8\nauthor:: Jane\n\nOther text."})
    result = read_note("note.md")
    assert result["inline_fields"]["rating"] == "8"
    assert result["inline_fields"]["author"] == "Jane"


def test_inline_fields_not_in_frontmatter(vault_factory):
    vault_factory({"note.md": "---\ntitle: Test\n---\nrating:: 9\n"})
    result = read_note("note.md")
    assert "rating" not in result["frontmatter"]
    assert result["inline_fields"]["rating"] == "9"


def test_inline_fields_empty_when_none(vault_factory):
    vault_factory({"note.md": "# Just a title\nNo inline fields here."})
    result = read_note("note.md")
    assert result["inline_fields"] == {}


def test_inline_fields_multi_word_key(vault_factory):
    vault_factory({"note.md": "due date:: 2026-07-30\n"})
    result = read_note("note.md")
    assert result["inline_fields"]["due date"] == "2026-07-30"


# ── inline fields — query ─────────────────────────────────────────────────────

def test_query_notes_inline_field_filter(vault_factory):
    idx = vault_factory({
        "a.md": "rating:: 8\nOther text",
        "b.md": "rating:: 5\nOther text",
        "c.md": "No inline fields",
    })
    results = query_notes(idx, inline_field_filter={"rating": "8"})
    assert len(results) == 1
    assert results[0]["path"] == "a.md"


def test_query_notes_inline_fields_in_result(vault_factory):
    idx = vault_factory({"note.md": "priority:: high\ncategory:: work\n"})
    results = query_notes(idx)
    assert len(results) == 1
    fields = results[0]["inline_fields"]
    assert fields["priority"] == "high"
    assert fields["category"] == "work"


def test_query_notes_combined_filter(vault_factory):
    idx = vault_factory({
        "a.md": "---\ntags: [project]\n---\npriority:: high\n",
        "b.md": "---\ntags: [project]\n---\npriority:: low\n",
        "c.md": "priority:: high\n",
    })
    results = query_notes(idx, tags=["project"], inline_field_filter={"priority": "high"})
    assert len(results) == 1
    assert results[0]["path"] == "a.md"
