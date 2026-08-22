"""Folder management tools backed by the central vault storage gateway."""

from __future__ import annotations

import re
import stat

from ..config import get_config
from ..domain.index import VaultIndex
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock


def _storage() -> VaultStorage:
    return VaultStorage.from_config()


def create_folder(path: str) -> dict:
    storage = _storage()
    if not path or path in (".", "/", "\\"):
        raise ValueError("A child folder path is required")
    target = storage.resolve_write(path)
    if storage.exists(target.relative, read=False) and not stat.S_ISDIR(
        storage.stat(target.relative, read=False).st_mode
    ):
        raise ValueError(f"A file already exists at: {path!r}")
    storage.make_dir(target.relative)
    return {"path": target.relative, "status": "created"}


def delete_folder(path: str, trash: bool = True) -> dict:
    storage = _storage()
    target = storage.resolve_delete(path, permanent=not trash)
    if not storage.exists(target.relative, read=False):
        raise FileNotFoundError(f"Folder not found: {path!r}")
    if not stat.S_ISDIR(storage.stat(target.relative, read=False).st_mode):
        raise ValueError(f"Not a folder: {path!r}")
    cfg = get_config()
    lock = acquire_lock(target.relative, lock_path=cfg.lock_path)
    try:
        if trash:
            storage.trash(target.relative)
        else:
            storage.delete(target.relative, permanent=True)
    finally:
        lock.release()
    return {"path": target.relative, "status": "deleted", "trash": trash}


def list_trash() -> dict:
    items = []
    for item in _storage().list_trash():
        items.append(
            {
                "name": item.name,
                "type": "folder" if item.is_dir else "file",
                "size_bytes": item.size_bytes,
                "mtime": item.mtime,
            }
        )
    return {"items": items}


def restore_folder(trashed_name: str, to_path: str, index: VaultIndex | None = None) -> dict:
    if "/" in trashed_name or "\\" in trashed_name or trashed_name in (".", ".."):
        raise ValueError(f"trashed_name must be a bare name, not a path: {trashed_name!r}")
    storage = _storage()
    destination = storage.resolve_write(to_path)
    info = storage.trash_info(trashed_name)
    if not info.is_dir:
        raise FileNotFoundError(f"No trashed folder named {trashed_name!r} in .trash/")
    restored = storage.restore(trashed_name, destination.relative)

    notes_restored = 0
    if index is not None:
        for p in storage.tree_paths(restored.relative):
            rel = p.relative
            if not rel.lower().endswith(".md"):
                continue
            if storage.policy.can_read(rel):
                index.update(rel)
                notes_restored += 1
    return {"path": restored.relative, "status": "restored", "notes_restored": notes_restored}


def list_folder(path: str = "") -> dict:
    storage = _storage()
    target = storage.resolve_read(path, allow_empty=True)
    folders: list[str] = []
    files: list[str] = []
    try:
        entries = storage.list_dir(target.relative)
    except NotADirectoryError as exc:
        raise ValueError(f"Not a folder: {path!r}") from exc
    for entry in entries:
        if entry.name.startswith("."):
            continue
        (folders if entry.is_dir else files).append(entry.relative)
    return {"path": target.relative or "/", "folders": folders, "files": files}


def rename_folder(
    from_path: str,
    to_path: str,
    index: VaultIndex | None = None,
) -> dict:
    """Move a folder after preauthorizing every note rewritten for links."""
    storage = _storage()
    source = storage.resolve_delete(from_path)
    destination = storage.resolve_write(to_path)
    from_path, to_path = source.relative, destination.relative
    if to_path.startswith(from_path + "/"):
        raise ValueError("Destination cannot be inside the source folder")
    if not storage.exists(source.relative, read=False):
        raise FileNotFoundError(f"Folder not found: {from_path!r}")
    if not stat.S_ISDIR(storage.stat(source.relative, read=False).st_mode):
        raise ValueError(f"Not a folder: {from_path!r}")
    if storage.exists(destination.relative, read=False):
        raise FileExistsError(f"Target already exists: {to_path!r}")

    source_paths = storage.authorize_tree(from_path, destination=to_path)
    notes_inside = [rel for rel in source_paths if rel.lower().endswith(".md")]
    from_prefix = from_path.rstrip("/")
    to_prefix = to_path.rstrip("/")
    link_re = re.compile(
        r"\[\[(" + re.escape(from_prefix) + r"/)([^\]|#]*)((?:[|#][^\]]*)?)\]\]",
        re.IGNORECASE,
    )

    updated_files: list[str] = []
    files_to_rewrite: list[tuple[str, str]] = []
    for candidate in storage.tree_paths(""):
        rel = candidate.relative
        if not rel.lower().endswith(".md"):
            continue
        try:
            raw = storage.read_text(rel)
        except Exception:
            continue
        if link_re.search(raw):
            storage.resolve_write(rel)  # preflight before any mutation
            files_to_rewrite.append((rel, raw))

    cfg = get_config()
    locks = [acquire_lock(rel, lock_path=cfg.lock_path) for rel, _ in files_to_rewrite]
    locks.append(acquire_lock(from_path, lock_path=cfg.lock_path))
    try:
        for rel, raw in files_to_rewrite:
            rewritten = link_re.sub(
                lambda m: f"[[{to_prefix}/{m.group(2)}{m.group(3)}]]", raw
            )
            storage.write_text_atomic(rel, rewritten)
            updated_files.append(rel)
        storage.move(from_path, to_path)
    finally:
        for lock in reversed(locks):
            lock.release()

    if index is not None:
        for rel in notes_inside:
            index.remove(rel)
        for candidate in storage.tree_paths(to_path):
            rel = candidate.relative
            if not rel.lower().endswith(".md"):
                continue
            if storage.policy.can_read(rel):
                index.update(rel)
        for rel in updated_files:
            if not rel.startswith(from_prefix + "/"):
                index.update(rel)

    external_changes = [
        rel for rel in updated_files
        if not rel.startswith(from_prefix + "/") and rel != from_prefix
    ]
    return {
        "from": from_path,
        "to": to_path,
        "notes_moved": len(notes_inside),
        "updated_links_in": external_changes,
    }
