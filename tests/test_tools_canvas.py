from __future__ import annotations

import json

import pytest

from obsidian_mcp.tools.canvas import list_canvases, patch_canvas, read_canvas, write_canvas

# ── list_canvases / read_canvas ───────────────────────────────────────────

def test_list_canvases_finds_canvas(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "board.canvas").write_text('{"nodes":[],"edges":[]}')
    canvases = list_canvases()
    assert "board.canvas" in canvases


def test_read_canvas_basic(tmp_path, vault_factory):
    vault_factory({})
    data = {
        "nodes": [{"id": "1", "type": "text", "text": "Hello", "x": 0, "y": 0}],
        "edges": [],
    }
    (tmp_path / "board.canvas").write_text(json.dumps(data))
    result = read_canvas("board.canvas")
    assert result["path"] == "board.canvas"
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["text"] == "Hello"


def test_read_canvas_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        read_canvas("ghost.canvas")


def test_read_canvas_invalid_json_raises(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "bad.canvas").write_text("NOT JSON {{{")
    with pytest.raises(ValueError, match="Invalid canvas JSON"):
        read_canvas("bad.canvas")


# ── write_canvas ──────────────────────────────────────────────────────────

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


# ── patch_canvas ──────────────────────────────────────────────────────────

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
