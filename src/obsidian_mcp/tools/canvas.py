from __future__ import annotations

import json
import uuid

from ..config import get_config
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock
from ..storage.policy import InvalidFileTypeError


def list_canvases() -> list[str]:
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    return sorted(
        p.relative
        for p in storage.list_files()
        if p.relative.lower().endswith(".canvas")
    )


def read_canvas(path: str) -> dict:
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".canvas"):
        raise InvalidFileTypeError("Canvas paths must end in .canvas")
    target = storage.resolve_read(path)
    path = target.relative
    if not storage.exists(path, read=True):
        raise FileNotFoundError(f"Canvas not found: {path!r}")
    try:
        raw = storage.read_text(path)
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid canvas JSON in {path!r}: {exc}") from exc

    nodes = []
    for node in data.get("nodes", []):
        nodes.append({
            "id": node.get("id"),
            "type": node.get("type"),
            "text": node.get("text"),
            "file": node.get("file"),
            "x": node.get("x"),
            "y": node.get("y"),
            "width": node.get("width"),
            "height": node.get("height"),
        })

    edges = []
    for edge in data.get("edges", []):
        edges.append({
            "id": edge.get("id"),
            "from": edge.get("fromNode"),
            "to": edge.get("toNode"),
            "label": edge.get("label"),
        })

    return {"path": path, "nodes": nodes, "edges": edges}


def write_canvas(
    path: str,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> dict:
    """Create or fully overwrite a canvas file.

    Each node must have: type ('text'|'file'|'group'|'link'), x, y, width, height.
    Text nodes need 'text'. File nodes need 'file' (vault path). Group nodes can
    have 'label'. Link nodes need 'url'. Node 'id' is auto-generated if omitted.
    Each edge must have fromNode and toNode. Edge 'id' is auto-generated if omitted.
    """
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".canvas"):
        raise InvalidFileTypeError("Canvas paths must end in .canvas")
    target = storage.resolve_write(path)
    path = target.relative

    built_nodes = [_normalize_node(n) for n in (nodes or [])]
    built_edges = [_normalize_edge(e) for e in (edges or [])]
    data = {"nodes": built_nodes, "edges": built_edges}

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        storage.write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        lock.release()

    return {
        "path": path,
        "status": "written",
        "nodes": len(built_nodes),
        "edges": len(built_edges),
    }


def patch_canvas(
    path: str,
    add_nodes: list[dict] | None = None,
    update_nodes: list[dict] | None = None,
    delete_node_ids: list[str] | None = None,
    add_edges: list[dict] | None = None,
    delete_edge_ids: list[str] | None = None,
) -> dict:
    """Atomically update an existing canvas: add/update/delete nodes and edges.

    update_nodes: each dict must include 'id'; other fields are merged in.
    delete_node_ids: also removes all edges connected to those nodes.
    """
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".canvas"):
        raise InvalidFileTypeError("Canvas paths must end in .canvas")
    target = storage.resolve_write(path)
    path = target.relative
    if not storage.exists(path, read=False):
        raise FileNotFoundError(f"Canvas not found: {path!r}")

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        try:
            data = json.loads(storage.read_text(path))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid canvas JSON in {path!r}: {exc}") from exc

        nodes: list[dict] = data.get("nodes", [])
        edges: list[dict] = data.get("edges", [])

        # Delete nodes (and their edges)
        if delete_node_ids:
            del_set = set(delete_node_ids)
            nodes = [n for n in nodes if n.get("id") not in del_set]
            edges = [e for e in edges
                     if e.get("fromNode") not in del_set and e.get("toNode") not in del_set]

        # Update nodes (merge fields by id)
        if update_nodes:
            node_by_id = {n["id"]: n for n in nodes if "id" in n}
            for upd in update_nodes:
                nid = upd.get("id")
                if nid and nid in node_by_id:
                    node_by_id[nid].update(upd)
            nodes = list(node_by_id.values())

        # Add nodes
        if add_nodes:
            nodes.extend(_normalize_node(n) for n in add_nodes)

        # Delete edges
        if delete_edge_ids:
            del_edge_set = set(delete_edge_ids)
            edges = [e for e in edges if e.get("id") not in del_edge_set]

        # Add edges
        if add_edges:
            edges.extend(_normalize_edge(e) for e in add_edges)

        storage.write_text_atomic(path, json.dumps({"nodes": nodes, "edges": edges}, indent=2, ensure_ascii=False))
    finally:
        lock.release()

    return {
        "path": path,
        "status": "patched",
        "nodes": len(nodes),
        "edges": len(edges),
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize_node(n: dict) -> dict:
    result = dict(n)
    if "id" not in result:
        result["id"] = uuid.uuid4().hex[:8]
    # Ensure required layout fields have defaults
    result.setdefault("x", 0)
    result.setdefault("y", 0)
    result.setdefault("width", 250)
    result.setdefault("height", 60)
    return result


def _normalize_edge(e: dict) -> dict:
    result = dict(e)
    if "id" not in result:
        result["id"] = uuid.uuid4().hex[:8]
    # Canvas format uses fromNode/toNode internally
    if "from" in result and "fromNode" not in result:
        result["fromNode"] = result.pop("from")
    if "to" in result and "toNode" not in result:
        result["toNode"] = result.pop("to")
    return result
