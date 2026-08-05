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
import os
import re
import uuid

from ..config import get_config
from ..domain.index import VaultIndex
from ..domain.parser import parse_note
from ..storage.filesystem import validate_path
from ..storage.locking import acquire_lock
from .write import _check_write_permission

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
    root = cfg.vault_path
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.excalidraw.md")
    )


def read_excalidraw(path: str) -> dict:
    """Parse an Excalidraw file.
    Returns {path, elements, app_state, files}."""
    cfg = get_config()
    target = validate_path(cfg.vault_path, path)
    if not target.exists():
        raise FileNotFoundError(f"Excalidraw file not found: {path!r}")

    raw = target.read_text(encoding="utf-8")
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
    }


def write_excalidraw(
    path: str,
    elements: list[dict] | None = None,
    app_state: dict | None = None,
    index: VaultIndex | None = None,
) -> dict:
    """Create or fully overwrite an Excalidraw file.

    Each element must have: type (e.g. 'rectangle'|'ellipse'|'text'|'arrow'|'freedraw'),
    x, y, width, height. Element 'id' is auto-generated if omitted.
    """
    cfg = get_config()
    target = validate_path(cfg.vault_path, path)
    _check_write_permission(path)

    built_elements = [_normalize_element(e) for e in (elements or [])]
    data = _build_scene(built_elements, app_state or {})

    target.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(str(target))
    try:
        _write_excalidraw_atomic(target, data)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "written", "elements": len(built_elements)}


def patch_excalidraw(
    path: str,
    add_elements: list[dict] | None = None,
    update_elements: list[dict] | None = None,
    delete_element_ids: list[str] | None = None,
    index: VaultIndex | None = None,
) -> dict:
    """Atomically update an existing Excalidraw file: add/update/delete elements.

    update_elements: each dict must include 'id'; other fields are merged in.
    """
    cfg = get_config()
    target = validate_path(cfg.vault_path, path)
    _check_write_permission(path)
    if not target.exists():
        raise FileNotFoundError(f"Excalidraw file not found: {path!r}")

    lock = acquire_lock(str(target))
    try:
        raw = target.read_text(encoding="utf-8")
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
        _write_excalidraw_atomic(target, new_data)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "patched", "elements": len(elements)}


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


def _write_excalidraw_atomic(target, data: dict) -> None:
    drawing_json = json.dumps(data, indent=2, ensure_ascii=False)
    content = _FILE_TEMPLATE.format(drawing_json=drawing_json)
    tmp = target.parent / f".tmp-{uuid.uuid4().hex}.md"
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
