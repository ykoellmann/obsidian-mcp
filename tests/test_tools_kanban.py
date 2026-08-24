from __future__ import annotations

import pytest

import obsidian_mcp.tools.kanban as kanban_module
from obsidian_mcp.tools.kanban import (
    add_kanban_card,
    create_kanban_board,
    delete_kanban_card,
    move_kanban_card,
    read_kanban,
)

_BOARD = """\
---
kanban-plugin: basic
---

## Backlog

- [ ] Task A
- [ ] Task B

## In Progress

- [ ] Task C

## Done

- [x] Task D
"""


def test_create_kanban_board_uses_path_lock(vault_factory, monkeypatch):
    vault_factory({})
    events: list[str] = []

    class FakeLock:
        def release(self):
            events.append("release")

    def fake_acquire(path, **kwargs):
        events.append(f"acquire:{path}")
        return FakeLock()

    monkeypatch.setattr(kanban_module, "acquire_lock", fake_acquire)
    create_kanban_board("board.md", ["Todo"])
    assert events == ["acquire:board.md", "release"]


# ── create_kanban_board ───────────────────────────────────────────────────

def test_create_kanban_board(tmp_path, vault_factory):
    vault_factory({})
    result = create_kanban_board("board.md", ["Backlog", "In Progress", "Done"])
    assert result["status"] == "created"
    assert (tmp_path / "board.md").exists()
    content = (tmp_path / "board.md").read_text()
    assert "kanban-plugin: basic" in content
    assert "## Backlog" in content
    assert "## Done" in content


def test_create_kanban_board_readable(vault_factory):
    vault_factory({})
    create_kanban_board("board.md", ["Todo", "Done"])
    board = read_kanban("board.md")
    assert len(board["columns"]) == 2
    assert board["columns"][0]["name"] == "Todo"


# ── read_kanban ───────────────────────────────────────────────────────────

def test_read_kanban_columns(vault_factory):
    vault_factory({"board.md": _BOARD})
    board = read_kanban("board.md")
    names = [c["name"] for c in board["columns"]]
    assert "Backlog" in names
    assert "In Progress" in names
    assert "Done" in names


def test_read_kanban_cards(vault_factory):
    vault_factory({"board.md": _BOARD})
    board = read_kanban("board.md")
    backlog = next(c for c in board["columns"] if c["name"] == "Backlog")
    assert len(backlog["cards"]) == 2
    assert any(c["text"] == "Task A" for c in backlog["cards"])
    assert all(not c["done"] for c in backlog["cards"])


def test_read_kanban_done_flag(vault_factory):
    vault_factory({"board.md": _BOARD})
    board = read_kanban("board.md")
    done_col = next(c for c in board["columns"] if c["name"] == "Done")
    assert done_col["cards"][0]["done"] is True


def test_read_kanban_total_cards(vault_factory):
    vault_factory({"board.md": _BOARD})
    board = read_kanban("board.md")
    assert board["total_cards"] == 4


def test_read_kanban_missing_raises(vault_factory):
    vault_factory({})
    with pytest.raises(FileNotFoundError):
        read_kanban("ghost.md")


def test_read_kanban_not_a_board_raises(vault_factory):
    vault_factory({"note.md": "# Just a regular note\n"})
    with pytest.raises(ValueError, match="kanban-plugin"):
        read_kanban("note.md")


# ── add_kanban_card ───────────────────────────────────────────────────────

def test_add_kanban_card_open(vault_factory):
    vault_factory({"board.md": _BOARD})
    add_kanban_card("board.md", "Backlog", "New Task")
    board = read_kanban("board.md")
    backlog = next(c for c in board["columns"] if c["name"] == "Backlog")
    assert any(c["text"] == "New Task" for c in backlog["cards"])


def test_add_kanban_card_done(vault_factory):
    vault_factory({"board.md": _BOARD})
    add_kanban_card("board.md", "Done", "Already done", done=True)
    board = read_kanban("board.md")
    done_col = next(c for c in board["columns"] if c["name"] == "Done")
    new_card = next(c for c in done_col["cards"] if c["text"] == "Already done")
    assert new_card["done"] is True


def test_add_kanban_card_inserted_at_top(tmp_path, vault_factory):
    vault_factory({"board.md": _BOARD})
    add_kanban_card("board.md", "Backlog", "First card")
    content = (tmp_path / "board.md").read_text()
    assert content.index("First card") < content.index("Task A")


def test_add_kanban_card_wrong_column_raises(vault_factory):
    vault_factory({"board.md": _BOARD})
    with pytest.raises(ValueError, match="not found"):
        add_kanban_card("board.md", "Nonexistent", "Card")


# ── move_kanban_card ──────────────────────────────────────────────────────

def test_move_kanban_card_basic(vault_factory):
    vault_factory({"board.md": _BOARD})
    result = move_kanban_card("board.md", "Task A", "Backlog", "In Progress")
    assert result["status"] == "moved"
    board = read_kanban("board.md")
    backlog = next(c for c in board["columns"] if c["name"] == "Backlog")
    in_progress = next(c for c in board["columns"] if c["name"] == "In Progress")
    assert not any(c["text"] == "Task A" for c in backlog["cards"])
    assert any(c["text"] == "Task A" for c in in_progress["cards"])


def test_move_kanban_card_updates_done_state(vault_factory):
    vault_factory({"board.md": _BOARD})
    move_kanban_card("board.md", "Task A", "Backlog", "Done", done=True)
    board = read_kanban("board.md")
    done_col = next(c for c in board["columns"] if c["name"] == "Done")
    moved = next(c for c in done_col["cards"] if c["text"] == "Task A")
    assert moved["done"] is True


def test_move_kanban_card_preserves_done_state_by_default(vault_factory):
    vault_factory({"board.md": _BOARD})
    move_kanban_card("board.md", "Task D", "Done", "Backlog")
    board = read_kanban("board.md")
    backlog = next(c for c in board["columns"] if c["name"] == "Backlog")
    moved = next(c for c in backlog["cards"] if c["text"] == "Task D")
    assert moved["done"] is True  # original state preserved


def test_move_kanban_card_not_found_raises(vault_factory):
    vault_factory({"board.md": _BOARD})
    with pytest.raises(ValueError):
        move_kanban_card("board.md", "Ghost Task", "Backlog", "Done")


def test_move_kanban_card_wrong_column_raises(vault_factory):
    vault_factory({"board.md": _BOARD})
    with pytest.raises(ValueError):
        move_kanban_card("board.md", "Task A", "In Progress", "Done")  # Task A is in Backlog


# ── delete_kanban_card ────────────────────────────────────────────────────

def test_delete_kanban_card(vault_factory):
    vault_factory({"board.md": _BOARD})
    delete_kanban_card("board.md", "Task A")
    board = read_kanban("board.md")
    all_cards = [c["text"] for col in board["columns"] for c in col["cards"]]
    assert "Task A" not in all_cards
    assert "Task B" in all_cards


def test_delete_kanban_card_with_column(vault_factory):
    vault_factory({"board.md": _BOARD})
    delete_kanban_card("board.md", "Task A", column="Backlog")
    board = read_kanban("board.md")
    backlog = next(c for c in board["columns"] if c["name"] == "Backlog")
    assert not any(c["text"] == "Task A" for c in backlog["cards"])


def test_delete_kanban_card_wrong_column_raises(vault_factory):
    vault_factory({"board.md": _BOARD})
    with pytest.raises(ValueError):
        delete_kanban_card("board.md", "Task A", column="Done")  # not there


def test_delete_kanban_card_not_found_raises(vault_factory):
    vault_factory({"board.md": _BOARD})
    with pytest.raises(ValueError):
        delete_kanban_card("board.md", "Nonexistent Task")
