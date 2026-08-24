"""Excalidraw file tools for the Obsidian Excalidraw plugin format.

An Excalidraw file (`*.excalidraw.md`) is a Markdown file with:
  - frontmatter: excalidraw-plugin: parsed
  - a "# Excalidraw Data" heading
  - a "## Drawing" section containing a fenced ```json code block with
    {type, version, source, elements, appState, files} — the actual scene

Only the drawing JSON is round-tripped in full; the human-readable filler
text Obsidian's own Excalidraw plugin wraps around it (warning banner,
"## Text Elements" section) is written fresh on every write and ignored on
read — same tradeoff kanban.py makes for the Kanban plugin's settings block.
"""
from __future__ import annotations

import json
import re
import uuid

from ..config import get_config
from ..domain.index import VaultIndex
from ..domain.parser import parse_note
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock
from ..storage.policy import InvalidFileTypeError
from ..storage.revisions import prepare_full_write, read_text_for_update, revision_result

_DRAWING_BLOCK_RE = re.compile(r"## Drawing\s*\n```json\n(.*?)\n```", re.DOTALL)

_FILE_TEMPLATE = """\
---
excalidraw-plugin: parsed
---
==⚠  Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu of this document. ⚠==


# Excalidraw Data

## Text Elements
%%

## Drawing
```json
{drawing_json}
```
%%
"""


# ── Public API ────────────────────────────────────────────────────────────────

def list_excalidraw() -> list[str]:
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    return sorted(
        p.relative for p in storage.list_files() if p.relative.lower().endswith(".excalidraw.md")
    )


def read_excalidraw(path: str) -> dict:
    """Parse an Excalidraw file.
    Returns {path, elements, app_state, files}."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".excalidraw.md"):
        raise InvalidFileTypeError("Excalidraw paths must end in .excalidraw.md")
    target = storage.resolve_read(path)
    path = target.relative
    if not storage.exists(path, read=True):
        raise FileNotFoundError(f"Excalidraw file not found: {path!r}")

    raw, revision = storage.read_text_with_revision(path)
    note = parse_note(raw, path=path)
    if "excalidraw-plugin" not in note.frontmatter:
        raise ValueError(
            f"{path!r} is not an Excalidraw file (excalidraw-plugin key missing in frontmatter)"
        )

    data = _extract_scene(raw, path)
    return {
        "path": path,
        "elements": data.get("elements", []),
        "app_state": data.get("appState", {}),
        "files": data.get("files", {}),
        "revision": revision.token,
    }


def write_excalidraw(
    path: str,
    elements: list[dict] | None = None,
    app_state: dict | None = None,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
    create_only: bool = False,
) -> dict:
    """Create or fully overwrite an Excalidraw file.

    Each element must have: type (e.g. 'rectangle'|'ellipse'|'text'|'arrow'|'freedraw'),
    x, y, width, height. Element 'id' is auto-generated if omitted.
    """
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".excalidraw.md"):
        raise InvalidFileTypeError("Excalidraw paths must end in .excalidraw.md")
    target = storage.resolve_write(path)
    path = target.relative
    expected, effective_create_only = prepare_full_write(
        storage, path, expected_revision, create_only
    )

    built_elements = [_normalize_element(e) for e in (elements or [])]
    data = _build_scene(built_elements, app_state or {})

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        revision = storage.write_text_atomic(
            path,
            _build_excalidraw_content(data),
            expected_revision=expected,
            create_only=effective_create_only,
        )
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "written", "elements": len(built_elements)}, revision
    )


def patch_excalidraw(
    path: str,
    add_elements: list[dict] | None = None,
    update_elements: list[dict] | None = None,
    delete_element_ids: list[str] | None = None,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Atomically update an existing Excalidraw file: add/update/delete elements.

    update_elements: each dict must include 'id'; other fields are merged in.
    """
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".excalidraw.md"):
        raise InvalidFileTypeError("Excalidraw paths must end in .excalidraw.md")
    target = storage.resolve_write(path)
    path = target.relative
    if not storage.exists(path, read=False):
        raise FileNotFoundError(f"Excalidraw file not found: {path!r}")

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        data = _extract_scene(raw, path)
        elements: list[dict] = data.get("elements", [])

        if delete_element_ids:
            del_set = set(delete_element_ids)
            elements = [e for e in elements if e.get("id") not in del_set]

        if update_elements:
            el_by_id = {e["id"]: e for e in elements if "id" in e}
            for upd in update_elements:
                eid = upd.get("id")
                if eid and eid in el_by_id:
                    el_by_id[eid].update(upd)
            elements = list(el_by_id.values())

        if add_elements:
            elements.extend(_normalize_element(e) for e in add_elements)

        new_data = _build_scene(elements, data.get("appState", {}))
        revision = storage.write_text_atomic(
            path,
            _build_excalidraw_content(new_data),
            expected_revision=current_revision,
        )
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "patched", "elements": len(elements)}, revision
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_scene(raw: str, path: str) -> dict:
    m = _DRAWING_BLOCK_RE.search(raw)
    if not m:
        raise ValueError(f"No Drawing JSON block found in {path!r}")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Excalidraw JSON in {path!r}: {exc}") from exc


def _normalize_element(e: dict) -> dict:
    result = dict(e)
    if "id" not in result:
        result["id"] = uuid.uuid4().hex[:8]
    result.setdefault("type", "rectangle")
    result.setdefault("x", 0)
    result.setdefault("y", 0)
    result.setdefault("width", 100)
    result.setdefault("height", 100)
    return result


def _build_scene(elements: list[dict], app_state: dict) -> dict:
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff", **app_state},
        "files": {},
    }


def _build_excalidraw_content(data: dict) -> str:
    drawing_json = json.dumps(data, indent=2, ensure_ascii=False)
    return _FILE_TEMPLATE.format(drawing_json=drawing_json)
