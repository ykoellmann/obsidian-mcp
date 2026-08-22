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


def list_folder(path: str = "", recursive: bool = False, max_depth: int | None = None) -> dict:
    """List the contents of a vault folder (non-hidden files and subfolders).

    recursive=False (default): only the immediate contents — unchanged
    behavior, `{path, folders, files}`.
    recursive=True: a full tree dump in one call instead of one
    list_folder_tool round-trip per level. max_depth limits how many levels
    deep to descend (None = unlimited); depth 1 is `path`'s immediate
    children, same as non-recursive. Returns `{path, tree}` where `tree` is
    a nested `{folders: {name: tree}, files: [...]}` structure.
    """
    cfg = get_config()
    target = validate_path(cfg.vault_path, path) if path else cfg.vault_path

    if not target.exists():
        raise FileNotFoundError(f"Folder not found: {path!r}")
    if not target.is_dir():
        raise ValueError(f"Not a folder: {path!r}")

    if not recursive:
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

    tree = _build_tree(target, cfg.vault_path, depth=1, max_depth=max_depth)
    return {"path": path or "/", "tree": tree}


def _build_tree(dir_path: Path, vault_root: Path, depth: int, max_depth: int | None) -> dict:
    """depth = the level of the subfolders about to be listed (1 = dir_path's
    immediate children). A subfolder is always listed; its own contents are
    only descended into if there's depth budget left (max_depth is None, or
    depth < max_depth) — otherwise it appears as an empty stub."""
    folders: dict[str, dict] = {}
    files: list[str] = []
    for item in sorted(dir_path.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            if max_depth is not None and depth >= max_depth:
                folders[item.name] = {"folders": {}, "files": []}
            else:
                folders[item.name] = _build_tree(item, vault_root, depth + 1, max_depth)
        else:
            files.append(str(item.relative_to(vault_root)))
    return {"folders": folders, "files": files}


def list_files(folder: str = "", extension: str | None = None) -> list[str]:
    """List every file in the vault (or a subfolder), any type — not just
    notes/attachments/bases/canvases. extension filters by suffix without
    the dot (e.g. "lock" to find .lock files, "canvas", "base"); omit for
    everything. Hidden files/folders (dotfiles, .trash/) are skipped, same
    as list_folder_tool."""
    cfg = get_config()
    base = validate_path(cfg.vault_path, folder) if folder else cfg.vault_path
    if not base.exists():
        raise FileNotFoundError(f"Folder not found: {folder!r}")

    suffix = f".{extension.lstrip('.')}" if extension else None
    results: list[str] = []
    for item in base.rglob("*"):
        if not item.is_file():
            continue
        if any(part.startswith(".") for part in item.relative_to(cfg.vault_path).parts):
            continue
        if suffix and item.suffix != suffix:
            continue
        results.append(str(item.relative_to(cfg.vault_path)))

    return sorted(results)


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
