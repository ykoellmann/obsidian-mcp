"""Bases tools for the Obsidian Bases core plugin format (Obsidian 1.9.0+).

A `.base` file is a plain YAML document (no frontmatter wrapper, no JSON) with
up to four top-level keys:
  - filters: a boolean tree (and/or/not) of comparison statements or function
    calls (e.g. 'status != "done"', 'file.hasTag("book")"), applied vault-wide.
  - formulas: name -> string expression, computed columns available to views.
  - properties: per-property display config, mainly {displayName: ...}.
  - views: a list of {type, name, limit, filters, order, groupBy, summaries}
    objects, each rendering the (filtered) notes as a table/cards/list/etc.

Bases don't change notes themselves — they only define how existing
frontmatter properties are displayed, filtered, and grouped. See
https://obsidian.md/help/bases/syntax for the full grammar.
"""
from __future__ import annotations

import yaml

from ..config import get_config
from ..domain.index import VaultIndex
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock
from ..storage.policy import InvalidFileTypeError

# ── Public API ────────────────────────────────────────────────────────────────

def list_bases() -> list[str]:
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    return sorted(
        p.relative for p in storage.list_files() if p.relative.lower().endswith(".base")
    )


def read_base(path: str) -> dict:
    """Parse a .base file.
    Returns {path, filters, formulas, properties, views}."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".base"):
        raise InvalidFileTypeError("Bases paths must end in .base")
    target = storage.resolve_read(path)
    path = target.relative
    if not storage.exists(path, read=True):
        raise FileNotFoundError(f"Base not found: {path!r}")

    data = _load_yaml(storage, path)
    return {
        "path": path,
        "filters": data.get("filters", {}),
        "formulas": data.get("formulas", {}),
        "properties": data.get("properties", {}),
        "views": data.get("views", []),
    }


def write_base(
    path: str,
    filters: dict | None = None,
    formulas: dict | None = None,
    properties: dict | None = None,
    views: list[dict] | None = None,
    index: VaultIndex | None = None,
) -> dict:
    """Create or fully overwrite a .base file.

    filters: boolean tree ({and: [...]}, {or: [...]}, {not: ...}) or a single
    string statement. formulas: name -> expression string. properties:
    name -> {displayName: ...}. views: list of {type, name, ...}; 'type' is
    required per view (e.g. 'table', 'cards', 'list').

    Existing .base files in the vault are scanned first and their property
    names/displayNames are returned as 'known_properties', so callers can
    keep new bases consistent with established conventions.
    """
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".base"):
        raise InvalidFileTypeError("Bases paths must end in .base")
    target = storage.resolve_write(path)
    path = target.relative

    data = {
        k: v
        for k, v in (
            ("filters", filters),
            ("formulas", formulas),
            ("properties", properties),
            ("views", views),
        )
        if v is not None
    }
    _validate_base_structure(data, path)

    known_properties = _known_properties(exclude_path=path)

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        _write_base_atomic(storage, path, data)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {
        "path": path,
        "status": "written",
        "views": len(data.get("views", [])),
        "known_properties": known_properties,
    }


def patch_base(
    path: str,
    update_formulas: dict | None = None,
    delete_formula_keys: list[str] | None = None,
    update_properties: dict | None = None,
    delete_property_keys: list[str] | None = None,
    set_filters: dict | None = None,
    add_views: list[dict] | None = None,
    update_views: list[dict] | None = None,
    delete_view_names: list[str] | None = None,
    index: VaultIndex | None = None,
) -> dict:
    """Atomically update an existing .base file without rewriting it wholesale.

    update_formulas/update_properties are merged in (per-key). set_filters
    replaces the whole filters block (partial-patching a boolean tree isn't
    meaningful). update_views: each dict must include 'name'; other fields
    are merged into the matching view. delete_view_names removes views by name.
    """
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    if not path.lower().endswith(".base"):
        raise InvalidFileTypeError("Bases paths must end in .base")
    target = storage.resolve_write(path)
    path = target.relative
    if not storage.exists(path, read=False):
        raise FileNotFoundError(f"Base not found: {path!r}")

    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        data = _load_yaml(storage, path)

        formulas: dict = dict(data.get("formulas", {}))
        if delete_formula_keys:
            for key in delete_formula_keys:
                formulas.pop(key, None)
        if update_formulas:
            formulas.update(update_formulas)
        if formulas:
            data["formulas"] = formulas
        elif "formulas" in data:
            del data["formulas"]

        properties: dict = dict(data.get("properties", {}))
        if delete_property_keys:
            for key in delete_property_keys:
                properties.pop(key, None)
        if update_properties:
            properties.update(update_properties)
        if properties:
            data["properties"] = properties
        elif "properties" in data:
            del data["properties"]

        if set_filters is not None:
            data["filters"] = set_filters

        views: list[dict] = list(data.get("views", []))
        if delete_view_names:
            del_set = set(delete_view_names)
            views = [v for v in views if v.get("name") not in del_set]
        if update_views:
            view_by_name = {v["name"]: v for v in views if "name" in v}
            for upd in update_views:
                name = upd.get("name")
                if name and name in view_by_name:
                    view_by_name[name].update(upd)
            views = list(view_by_name.values())
        if add_views:
            views.extend(add_views)
        if views:
            data["views"] = views
        elif "views" in data:
            del data["views"]

        _validate_base_structure(data, path)
        _write_base_atomic(storage, path, data)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "patched", "views": len(data.get("views", []))}


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_yaml(storage: VaultStorage, path: str) -> dict:
    raw = storage.read_text(path)
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid base YAML in {path!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid base structure in {path!r}: top level must be a mapping")
    return data


def _validate_base_structure(data: dict, path: str) -> None:
    """Light structural validation — does not parse Obsidian's filter/formula
    expression grammar, only checks the .base file's overall shape."""
    for key in ("filters", "properties"):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f"Invalid base structure in {path!r}: {key!r} must be a mapping")
    if "formulas" in data and not isinstance(data["formulas"], dict):
        raise ValueError(f"Invalid base structure in {path!r}: 'formulas' must be a mapping of name -> expression")
    if "views" in data:
        views = data["views"]
        if not isinstance(views, list):
            raise ValueError(f"Invalid base structure in {path!r}: 'views' must be a list")
        for i, view in enumerate(views):
            if not isinstance(view, dict):
                raise ValueError(f"Invalid base structure in {path!r}: views[{i}] must be a mapping")
            if not view.get("type"):
                raise ValueError(f"Invalid base structure in {path!r}: views[{i}] is missing required 'type'")


def _known_properties(exclude_path: str | None = None) -> dict:
    """Scan existing .base files in the vault and collect known property
    names/displayNames, so new bases can stay consistent. Unreadable/invalid
    .base files are skipped rather than failing the caller's write."""
    cfg = get_config()
    known: dict = {}
    for rel in list_bases():
        if rel == exclude_path:
            continue
        try:
            data = _load_yaml(VaultStorage.from_config(cfg), rel)
        except (ValueError, OSError):
            continue
        for name, conf in (data.get("properties") or {}).items():
            entry = known.setdefault(name, {})
            if isinstance(conf, dict) and conf.get("displayName"):
                entry["displayName"] = conf["displayName"]
    return known


def _write_base_atomic(storage: VaultStorage, path: str, data: dict) -> None:
    content = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    storage.write_text_atomic(path, content)
