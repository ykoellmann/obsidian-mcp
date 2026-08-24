"""Kanban board tools for Obsidian Kanban plugin format.

A Kanban board is a Markdown note with:
  - frontmatter: kanban-plugin: basic  (or 'board')
  - ## Column Name  sections
  - - [ ] Card text  /  - [x] Card text  items
  - optional  %% kanban:settings ... %%  block at the end
"""
from __future__ import annotations

import re

from ..config import get_config
from ..domain.index import VaultIndex
from ..domain.parser import parse_note
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock
from ..storage.policy import InvalidFileTypeError
from ..storage.revisions import prepare_full_write, read_text_for_update, revision_result

# Matches - [ ] text  or  - [x] / - [X] text
_CARD_RE = re.compile(r"^- \[([ xX])\] (.+)$", re.MULTILINE)
# Matches ## Column Name
_H2_RE = re.compile(r"^## (.+?)[ \t]*$", re.MULTILINE)
# Kanban settings block at end of file
_SETTINGS_RE = re.compile(r"\n?%%.*?%%[ \t]*$", re.DOTALL)


# ── Public API ────────────────────────────────────────────────────────────────

def read_kanban(path: str) -> dict:
    """Parse a Kanban board note.
    Returns {path, plugin, columns: [{name, cards: [{text, done}]}], total_cards}.
    """
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".md"):
        raise InvalidFileTypeError("Kanban paths must end in .md")
    target = storage.resolve_read(path)
    path = target.relative
    if not storage.exists(path, read=True):
        raise FileNotFoundError(f"Kanban board not found: {path!r}")

    raw, revision = storage.read_text_with_revision(path)
    note = parse_note(raw, path=path)

    if "kanban-plugin" not in note.frontmatter:
        raise ValueError(f"{path!r} is not a Kanban board (kanban-plugin key missing in frontmatter)")

    body = _strip_settings(note.content)
    columns = _parse_columns(body)
    return {
        "path": path,
        "plugin": note.frontmatter.get("kanban-plugin", "basic"),
        "columns": columns,
        "total_cards": sum(len(c["cards"]) for c in columns),
        "revision": revision.token,
    }


def create_kanban_board(
    path: str,
    columns: list[str],
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
    create_only: bool = False,
) -> dict:
    """Create a new Kanban board with the given column names."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".md"):
        raise InvalidFileTypeError("Kanban paths must end in .md")
    target = storage.resolve_write(path)
    path = target.relative
    expected, effective_create_only = prepare_full_write(
        storage, path, expected_revision, create_only
    )

    lines = ["---", "kanban-plugin: basic", "---", ""]
    for col in columns:
        lines += [f"## {col}", ""]
    content = "\n".join(lines)

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        revision = storage.write_text_atomic(
            path,
            content,
            expected_revision=expected,
            create_only=effective_create_only,
        )
    finally:
        lock.release()
    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "created", "columns": columns}, revision
    )


def add_kanban_card(
    path: str,
    column: str,
    text: str,
    done: bool = False,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Add a new card to a column. New cards are inserted at the top of the column."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".md"):
        raise InvalidFileTypeError("Kanban paths must end in .md")
    target = storage.resolve_write(path)
    path = target.relative

    if not storage.exists(path, read=False):
        raise FileNotFoundError(f"Kanban board not found: {path!r}")

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        patched = _add_card(raw, column, text, done)
        revision = storage.write_text_atomic(
            path, patched, expected_revision=current_revision
        )
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "added", "column": column, "card": text, "done": done},
        revision,
    )


def move_kanban_card(
    path: str,
    card_text: str,
    from_column: str,
    to_column: str,
    done: bool | None = None,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Move a card between columns. done=True/False overwrites the card's tick state."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".md"):
        raise InvalidFileTypeError("Kanban paths must end in .md")
    target = storage.resolve_write(path)
    path = target.relative

    if not storage.exists(path, read=False):
        raise FileNotFoundError(f"Kanban board not found: {path!r}")

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        patched, moved = _move_card(raw, card_text, from_column, to_column, done)
        if not moved:
            raise ValueError(f"Card {card_text!r} not found in column {from_column!r}")
        revision = storage.write_text_atomic(
            path, patched, expected_revision=current_revision
        )
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result({
        "path": path,
        "status": "moved",
        "card": card_text,
        "from": from_column,
        "to": to_column,
    }, revision)


def delete_kanban_card(
    path: str,
    card_text: str,
    column: str | None = None,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Remove a card from the board. If column is given, only search there."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".md"):
        raise InvalidFileTypeError("Kanban paths must end in .md")
    target = storage.resolve_write(path)
    path = target.relative

    if not storage.exists(path, read=False):
        raise FileNotFoundError(f"Kanban board not found: {path!r}")

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        patched, deleted = _delete_card(raw, card_text, column)
        if not deleted:
            col_info = f" in column {column!r}" if column else ""
            raise ValueError(f"Card {card_text!r} not found{col_info}")
        revision = storage.write_text_atomic(
            path, patched, expected_revision=current_revision
        )
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "deleted", "card": card_text}, revision
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _strip_settings(body: str) -> str:
    return _SETTINGS_RE.sub("", body).strip()


def _parse_columns(body: str) -> list[dict]:
    headings = list(_H2_RE.finditer(body))
    if not headings:
        return []
    columns = []
    for i, m in enumerate(headings):
        name = m.group(1)
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        section = body[start:end]
        cards = [
            {"text": cm.group(2).strip(), "done": cm.group(1).lower() == "x"}
            for cm in _CARD_RE.finditer(section)
        ]
        columns.append({"name": name, "cards": cards})
    return columns


def _section_bounds(raw: str, column: str) -> tuple[int, int] | None:
    """Return (section_start, section_end) for a column heading's content."""
    heading_re = re.compile(r"^## " + re.escape(column) + r"[ \t]*$", re.MULTILINE)
    m = heading_re.search(raw)
    if not m:
        return None
    start = raw.find("\n", m.start()) + 1  # after heading line
    after = raw[start:]
    # End = next ## heading or settings block or EOF
    next_h2 = re.search(r"^## ", after, re.MULTILINE)
    settings = _SETTINGS_RE.search(after)
    candidates = [c.start() for c in [next_h2, settings] if c is not None]
    end = start + min(candidates) if candidates else len(raw)
    return start, end


def _add_card(raw: str, column: str, text: str, done: bool) -> str:
    bounds = _section_bounds(raw, column)
    if bounds is None:
        raise ValueError(f"Column {column!r} not found in Kanban board")
    start, _ = bounds
    marker = "[x]" if done else "[ ]"
    return raw[:start] + f"- {marker} {text}\n" + raw[start:]


def _move_card(
    raw: str,
    card_text: str,
    from_col: str,
    to_col: str,
    done: bool | None,
) -> tuple[str, bool]:
    from_bounds = _section_bounds(raw, from_col)
    if from_bounds is None:
        return raw, False

    f_start, f_end = from_bounds
    section = raw[f_start:f_end]
    escaped = re.escape(card_text)
    card_re = re.compile(r"^- \[([ xX])\] " + escaped + r"[ \t]*$", re.MULTILINE)
    cm = card_re.search(section)
    if not cm:
        return raw, False

    original_done = cm.group(1).lower() == "x"
    new_done = done if done is not None else original_done
    marker = "[x]" if new_done else "[ ]"

    # Absolute positions
    abs_start = f_start + cm.start()
    abs_end = f_start + cm.end()
    if abs_end < len(raw) and raw[abs_end] == "\n":
        abs_end += 1

    # Remove card
    without_card = raw[:abs_start] + raw[abs_end:]

    # Insert into to_col
    to_bounds = _section_bounds(without_card, to_col)
    if to_bounds is None:
        raise ValueError(f"Column {to_col!r} not found in Kanban board")
    insert_at = to_bounds[0]
    result = without_card[:insert_at] + f"- {marker} {card_text}\n" + without_card[insert_at:]
    return result, True


def _delete_card(raw: str, card_text: str, column: str | None) -> tuple[str, bool]:
    escaped = re.escape(card_text)
    card_re = re.compile(r"^- \[([ xX])\] " + escaped + r"[ \t]*\n?", re.MULTILINE)

    if column is None:
        new_raw, count = card_re.subn("", raw, count=1)
        return new_raw, count > 0

    bounds = _section_bounds(raw, column)
    if bounds is None:
        return raw, False

    f_start, f_end = bounds
    section = raw[f_start:f_end]
    new_section, count = card_re.subn("", section, count=1)
    if count == 0:
        return raw, False
    return raw[:f_start] + new_section + raw[f_end:], True
