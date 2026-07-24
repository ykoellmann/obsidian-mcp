from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import yaml

from ..config import get_config
from ..domain.index import VaultIndex
from ..storage.filesystem import validate_path, write_file_atomic
from ..storage.locking import acquire_lock


class WritePermissionError(Exception):
    pass


def _check_write_permission(relative_path: str) -> None:
    cfg = get_config()
    if cfg.read_only:
        raise WritePermissionError("Server is in read-only mode")
    if cfg.write_paths:
        allowed = any(
            relative_path.startswith(wp.rstrip("/") + "/") or relative_path == wp
            for wp in cfg.write_paths
        )
        if not allowed:
            raise WritePermissionError(
                f"Path {relative_path!r} is not in WRITE_PATHS: {cfg.write_paths}"
            )


def write_note(path: str, content: str, index: VaultIndex | None = None) -> dict:
    cfg = get_config()
    validate_path(cfg.vault_path, path)
    _check_write_permission(path)

    full_path = str(cfg.vault_path / path)
    lock = acquire_lock(full_path)
    try:
        write_file_atomic(cfg.vault_path, path, content)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "written"}


def patch_note(
    path: str,
    section: str,
    new_content: str,
    mode: str = "replace",
    target_type: str = "heading",
    index: VaultIndex | None = None,
) -> dict:
    """Edit a note section or block reference.

    mode: 'replace' | 'insert_before' | 'insert_after' | 'append'
    target_type: 'heading' | 'block_ref'
    """
    cfg = get_config()
    validate_path(cfg.vault_path, path)
    _check_write_permission(path)

    full_path = str(cfg.vault_path / path)
    lock = acquire_lock(full_path)
    try:
        raw = (cfg.vault_path / path).read_text(encoding="utf-8")
        if target_type == "block_ref":
            patched = _patch_block_ref(raw, section, new_content, mode)
        else:
            patched = _patch_section(raw, section, new_content, mode)
        write_file_atomic(cfg.vault_path, path, patched)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "patched", "mode": mode, "target_type": target_type}


def _find_section_bounds(content: str, heading: str) -> tuple[int, int, int, int]:
    """Return (heading_start, heading_end, body_end, level) for a heading."""
    escaped = re.escape(heading)
    m = re.search(r"^(#{1,6})\s+" + escaped + r"[ \t]*$", content, re.MULTILINE)
    if not m:
        raise ValueError(f"Section {heading!r} not found in note")
    level = len(m.group(1))
    end_m = re.search(r"^#{1," + str(level) + r"}\s", content[m.end():], re.MULTILINE)
    body_end = m.end() + end_m.start() if end_m else len(content)
    return m.start(), m.end(), body_end, level


def _patch_section(content: str, heading: str, new_content: str, mode: str) -> str:
    h_start, h_end, body_end, _ = _find_section_bounds(content, heading)
    heading_line = content[h_start:h_end].rstrip("\n")

    if mode == "replace":
        return content[:h_start] + heading_line + "\n\n" + new_content.strip() + "\n" + content[body_end:]
    if mode == "insert_before":
        return content[:h_start] + new_content.strip() + "\n\n" + content[h_start:]
    if mode == "insert_after":
        return content[:h_end] + "\n" + new_content.strip() + "\n\n" + content[h_end:]
    if mode == "append":
        before = content[:body_end].rstrip("\n")
        return before + "\n\n" + new_content.strip() + "\n" + content[body_end:]
    raise ValueError(f"Unknown mode: {mode!r}")


def _patch_block_ref(content: str, block_id: str, new_content: str, mode: str) -> str:
    pattern = re.compile(rf"^(.+?)\s+\^{re.escape(block_id)}\s*$", re.MULTILINE)
    m = pattern.search(content)
    if not m:
        raise ValueError(f"Block reference ^{block_id} not found in note")

    if mode == "replace":
        return content[:m.start()] + new_content.strip() + " ^" + block_id + content[m.end():]
    if mode == "insert_before":
        return content[:m.start()] + new_content.strip() + "\n" + content[m.start():]
    if mode in ("insert_after", "append"):
        return content[:m.end()] + "\n" + new_content.strip() + content[m.end():]
    raise ValueError(f"Unknown mode: {mode!r}")


def delete_note(path: str, trash: bool = True, index: VaultIndex | None = None) -> dict:
    """Delete a note. trash=True moves it to .trash/ in the vault root."""
    cfg = get_config()
    validate_path(cfg.vault_path, path)
    _check_write_permission(path)

    full_path = cfg.vault_path / path
    if not full_path.exists():
        raise FileNotFoundError(f"Note not found: {path!r}")

    lock = acquire_lock(str(full_path))
    try:
        if trash:
            trash_dir = cfg.vault_path / ".trash"
            trash_dir.mkdir(exist_ok=True)
            dest = trash_dir / Path(path).name
            if dest.exists():
                dest = trash_dir / f"{dest.stem}-{uuid.uuid4().hex[:8]}{dest.suffix}"
            os.rename(full_path, dest)
        else:
            full_path.unlink()
    finally:
        lock.release()

    if index is not None:
        index.remove(path)

    return {"path": path, "status": "deleted", "trash": trash}


def append_to_note(
    path: str,
    content: str,
    section: str | None = None,
    create: bool = True,
    index: VaultIndex | None = None,
) -> dict:
    """Append content to a note (or a specific section). Creates the note if it doesn't exist."""
    cfg = get_config()
    validate_path(cfg.vault_path, path)
    _check_write_permission(path)

    full_path = cfg.vault_path / path
    lock = acquire_lock(str(full_path))
    try:
        if full_path.exists():
            raw = full_path.read_text(encoding="utf-8")
            if section:
                patched = _patch_section(raw, section, content, mode="append")
            else:
                patched = raw.rstrip("\n") + "\n\n" + content.strip() + "\n"
        elif create:
            patched = content
        else:
            raise FileNotFoundError(f"Note not found: {path!r}")
        write_file_atomic(cfg.vault_path, path, patched)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "appended"}


def patch_frontmatter(
    path: str,
    updates: dict,
    merge_arrays: bool = True,
    index: VaultIndex | None = None,
) -> dict:
    """Update specific YAML frontmatter keys. Arrays are merged by default."""
    cfg = get_config()
    validate_path(cfg.vault_path, path)
    _check_write_permission(path)

    full_path = cfg.vault_path / path
    if not full_path.exists():
        raise FileNotFoundError(f"Note not found: {path!r}")

    lock = acquire_lock(str(full_path))
    try:
        raw = full_path.read_text(encoding="utf-8")
        patched = _apply_frontmatter_updates(raw, updates, merge_arrays)
        write_file_atomic(cfg.vault_path, path, patched)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "frontmatter_patched", "updated_keys": list(updates.keys())}


_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
_INLINE_TAG_RE = re.compile(r"(?<!\S)#([\w/]+)")


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse YAML frontmatter block. Returns (fm_dict, body_text)."""
    m = _FM_RE.match(raw)
    if m:
        try:
            fm: dict = yaml.safe_load(m.group(1)) or {}
        except Exception:
            fm = {}
        return fm, raw[m.end():]
    return {}, raw


def _serialize_frontmatter(fm: dict, body: str) -> str:
    """Serialize frontmatter dict and body back to a note string."""
    new_fm = yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{new_fm}\n---\n{body}"


def manage_tags(
    path: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    index: VaultIndex | None = None,
) -> dict:
    """Add or remove tags on a note. Updates frontmatter tags array and strips inline #tags."""
    cfg = get_config()
    validate_path(cfg.vault_path, path)
    _check_write_permission(path)

    full_path = cfg.vault_path / path
    if not full_path.exists():
        raise FileNotFoundError(f"Note not found: {path!r}")

    add = list(add or [])
    remove = list(remove or [])

    lock = acquire_lock(str(full_path))
    try:
        raw = full_path.read_text(encoding="utf-8")
        patched = _apply_tag_changes(raw, add, remove)
        write_file_atomic(cfg.vault_path, path, patched)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return {"path": path, "status": "tags_updated", "added": add, "removed": remove}


def _apply_tag_changes(raw: str, add: list[str], remove: list[str]) -> str:
    fm, body = _parse_frontmatter(raw)

    current: list = fm.get("tags", [])
    if not isinstance(current, list):
        current = [current] if current else []

    for tag in add:
        if tag not in current:
            current.append(tag)
    for tag in remove:
        current = [t for t in current if t != tag]
    fm["tags"] = current

    # Strip removed tags from body inline (#tag syntax)
    for tag in remove:
        body = re.sub(r"(?<!\S)#" + re.escape(tag) + r"(?=[\s,.]|$)", "", body)
    # Collapse multiple spaces created by removal
    body = re.sub(r"[ \t]{2,}", " ", body)

    return _serialize_frontmatter(fm, body)


def _apply_frontmatter_updates(raw: str, updates: dict, merge_arrays: bool) -> str:
    fm, body = _parse_frontmatter(raw)

    for key, value in updates.items():
        if merge_arrays and isinstance(value, list) and isinstance(fm.get(key), list):
            existing: list = fm[key]
            fm[key] = existing + [v for v in value if v not in existing]
        else:
            fm[key] = value

    return _serialize_frontmatter(fm, body)


def move_note(from_path: str, to_path: str, index: VaultIndex | None = None) -> dict:
    cfg = get_config()
    validate_path(cfg.vault_path, from_path)
    validate_path(cfg.vault_path, to_path)
    _check_write_permission(from_path)
    _check_write_permission(to_path)

    from_full = cfg.vault_path / from_path
    to_full = cfg.vault_path / to_path

    if not from_full.exists():
        raise FileNotFoundError(f"Source note not found: {from_path!r}")
    if to_full.exists():
        raise FileExistsError(f"Target already exists: {to_path!r}")

    from_stem = Path(from_path).stem
    to_stem = Path(to_path).stem

    # Patterns that refer to the source note in wikilinks
    # Match [[from_stem]], [[from_path]], [[from_stem|Alias]], [[from_stem#Heading]]
    _patterns = [
        re.escape(from_stem),
        re.escape(from_path.replace("\\", "/")),
    ]
    combined = "|".join(_patterns)
    link_re = re.compile(rf"\[\[({combined})((?:[|#][^\]]*)?)\]\]", re.IGNORECASE)

    updated_files: list[str] = []
    all_md = list(cfg.vault_path.rglob("*.md"))

    # Collect and lock all files we'll touch
    locks = []
    files_to_rewrite: list[tuple[Path, str]] = []

    for md_file in all_md:
        rel = str(md_file.relative_to(cfg.vault_path))
        if rel == from_path:
            continue
        try:
            raw = md_file.read_text(encoding="utf-8", errors="replace")
            if link_re.search(raw):
                files_to_rewrite.append((md_file, raw))
        except Exception:
            pass

    try:
        for md_file, _ in files_to_rewrite:
            locks.append(acquire_lock(str(md_file)))
        locks.append(acquire_lock(str(from_full)))

        # Rewrite links
        for md_file, raw in files_to_rewrite:
            rewritten = link_re.sub(lambda m: f"[[{to_stem}{m.group(2)}]]", raw)
            write_file_atomic(cfg.vault_path, str(md_file.relative_to(cfg.vault_path)), rewritten)
            updated_files.append(str(md_file.relative_to(cfg.vault_path)))

        # Move the file
        to_full.parent.mkdir(parents=True, exist_ok=True)
        os.rename(from_full, to_full)
    finally:
        for lock in reversed(locks):
            lock.release()

    if index is not None:
        index.remove(from_path)
        index.update(to_path)
        for rel in updated_files:
            index.update(rel)

    return {"from": from_path, "to": to_path, "updated_links_in": updated_files}
