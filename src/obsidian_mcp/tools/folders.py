"""Folder management tools: create, delete, list, and rename vault folders."""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from ..config import get_config
from ..domain.index import VaultIndex
from ..storage.filesystem import validate_path, write_file_atomic
from .write import _check_write_permission


def create_folder(path: str) -> dict:
    """Create a folder (and any missing parent directories) inside the vault."""
    cfg = get_config()
    target = validate_path(cfg.vault_path, path)
    if target.exists() and not target.is_dir():
        raise ValueError(f"A file already exists at: {path!r}")
    _check_write_permission(path.rstrip("/") + "/.keep")
    target.mkdir(parents=True, exist_ok=True)
    return {"path": path, "status": "created"}


def delete_folder(path: str, trash: bool = True) -> dict:
    """Delete or trash a vault folder."""
    cfg = get_config()
    target = validate_path(cfg.vault_path, path)
    _check_write_permission(path.rstrip("/") + "/.keep")
    if not target.exists():
        raise FileNotFoundError(f"Folder not found: {path!r}")
    if not target.is_dir():
        raise ValueError(f"Not a folder: {path!r}")

    if trash:
        trash_dir = cfg.vault_path / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / Path(path).name
        if dest.exists():
            dest = trash_dir / f"{Path(path).name}-{uuid.uuid4().hex[:8]}"
        shutil.move(str(target), str(dest))
    else:
        shutil.rmtree(target)

    return {"path": path, "status": "deleted", "trash": trash}


def list_trash() -> dict:
    """List top-level items sitting in .trash/ (from delete_note/delete_folder
    with trash=True). Names here are what restore_note_tool/restore_folder_tool
    expect as trashed_name — they may differ from the original name if a
    collision at delete time appended a random suffix."""
    cfg = get_config()
    trash_dir = cfg.vault_path / ".trash"
    if not trash_dir.exists():
        return {"items": []}

    items = []
    for item in sorted(trash_dir.iterdir()):
        items.append(
            {
                "name": item.name,
                "type": "folder" if item.is_dir() else "file",
                "size_bytes": item.stat().st_size if item.is_file() else None,
                "mtime": item.stat().st_mtime,
            }
        )
    return {"items": items}


def restore_folder(trashed_name: str, to_path: str, index: VaultIndex | None = None) -> dict:
    """Restore a folder previously moved to .trash/ (via delete_folder trash=True).

    trashed_name: the folder name as it sits under .trash/ (see list_trash_tool).
    to_path: where to put it back (you choose it; the original parent path
    isn't recoverable from the trash entry alone).
    """
    if "/" in trashed_name or "\\" in trashed_name or trashed_name in (".", ".."):
        raise ValueError(f"trashed_name must be a bare name, not a path: {trashed_name!r}")

    cfg = get_config()
    to_dir = validate_path(cfg.vault_path, to_path)
    _check_write_permission(to_path.rstrip("/") + "/.keep")

    trash_src = cfg.vault_path / ".trash" / trashed_name
    if not trash_src.exists() or not trash_src.is_dir():
        raise FileNotFoundError(f"No trashed folder named {trashed_name!r} in .trash/")
    if to_dir.exists():
        raise FileExistsError(f"Target already exists: {to_path!r}")

    to_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trash_src), str(to_dir))

    notes_restored = 0
    if index is not None:
        for p in to_dir.rglob("*.md"):
            index.update(str(p.relative_to(cfg.vault_path)))
            notes_restored += 1

    return {"path": to_path, "status": "restored", "notes_restored": notes_restored}


def list_folder(path: str = "") -> dict:
    """List the immediate contents of a vault folder (non-hidden files and subfolders)."""
    cfg = get_config()
    target = validate_path(cfg.vault_path, path) if path else cfg.vault_path

    if not target.exists():
        raise FileNotFoundError(f"Folder not found: {path!r}")
    if not target.is_dir():
        raise ValueError(f"Not a folder: {path!r}")

    folders: list[str] = []
    files: list[str] = []
    for item in sorted(target.iterdir()):
        if item.name.startswith("."):
            continue
        rel = str(item.relative_to(cfg.vault_path))
        if item.is_dir():
            folders.append(rel)
        else:
            files.append(rel)

    return {"path": path or "/", "folders": folders, "files": files}


def rename_folder(
    from_path: str,
    to_path: str,
    index: VaultIndex | None = None,
) -> dict:
    """Rename or move a vault folder.

    Rewrites path-based wikilinks ([[from_path/note]]) in all vault notes.
    Stem-based links ([[note]]) are unaffected — they resolve by filename.
    """
    cfg = get_config()
    from_dir = validate_path(cfg.vault_path, from_path)
    to_dir = validate_path(cfg.vault_path, to_path)
    _check_write_permission(from_path.rstrip("/") + "/.keep")
    _check_write_permission(to_path.rstrip("/") + "/.keep")

    if not from_dir.exists():
        raise FileNotFoundError(f"Folder not found: {from_path!r}")
    if not from_dir.is_dir():
        raise ValueError(f"Not a folder: {from_path!r}")
    if to_dir.exists():
        raise FileExistsError(f"Target already exists: {to_path!r}")

    # Notes inside the folder (paths before rename)
    notes_inside = [
        str(p.relative_to(cfg.vault_path))
        for p in from_dir.rglob("*.md")
    ]

    from_prefix = from_path.replace("\\", "/").rstrip("/")
    to_prefix = to_path.replace("\\", "/").rstrip("/")

    # Match [[from_path/anything]] with optional |alias or #heading suffix
    link_re = re.compile(
        r"\[\[(" + re.escape(from_prefix) + r"/)([^\]|#]*)((?:[|#][^\]]*)?)\]\]",
        re.IGNORECASE,
    )

    # Rewrite path-based links in ALL vault notes (including those being moved)
    updated_files: list[str] = []
    for md_file in sorted(cfg.vault_path.rglob("*.md")):
        try:
            raw = md_file.read_text(encoding="utf-8", errors="replace")
            if not link_re.search(raw):
                continue
            rewritten = link_re.sub(
                lambda m, _tp=to_prefix: f"[[{_tp}/{m.group(2)}{m.group(3)}]]", raw
            )
            rel = str(md_file.relative_to(cfg.vault_path))
            write_file_atomic(cfg.vault_path, rel, rewritten)
            updated_files.append(rel)
        except Exception:
            pass

    # Move the folder
    to_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(from_dir), str(to_dir))

    # Update index
    if index is not None:
        for rel in notes_inside:
            index.remove(rel)
        for p in to_dir.rglob("*.md"):
            index.update(str(p.relative_to(cfg.vault_path)))
        external_updates = [
            f for f in updated_files
            if not f.startswith(from_prefix + "/") and f != from_prefix
        ]
        for rel in external_updates:
            index.update(rel)

    external_changes = [
        f for f in updated_files
        if not f.startswith(from_prefix + "/") and f != from_prefix
    ]
    return {
        "from": from_path,
        "to": to_path,
        "notes_moved": len(notes_inside),
        "updated_links_in": external_changes,
    }
