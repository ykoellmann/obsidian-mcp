from __future__ import annotations

import difflib
import re
from pathlib import Path

import yaml

from ..config import get_config
from ..domain.index import VaultIndex
from ..domain.models import (
    PreconditionRequiredError,
    RevisionConflictError,
    normalize_revision_token,
)
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock
from ..storage.policy import WritePermissionError as PolicyWritePermissionError
from ..storage.revisions import read_text_for_update, revision_result
from .read import _is_excluded

WritePermissionError = PolicyWritePermissionError


def _storage() -> VaultStorage:
    return VaultStorage.from_config()


def _check_write_permission(relative_path: str) -> None:
    _storage().resolve_write(relative_path)


def _require_note_path(path: str) -> str:
    if not path.lower().endswith(".md"):
        raise ValueError("Note paths must end in .md")
    return path


def _unified_diff(before: str, after: str, path: str) -> str:
    """Unified diff between two full note texts, git-diff-style — lets a
    caller see exactly what a write/patch changed without re-reading the
    note before and after."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def write_note(
    path: str,
    content: str,
    index: VaultIndex | None = None,
    dry_run: bool = False,
    expected_revision: str | None = None,
    create_only: bool = False,
) -> dict:
    """Write (create or overwrite) a note.

    If `content` has no YAML frontmatter of its own and a note already exists
    at `path` with frontmatter, that existing frontmatter is carried over
    onto the new body instead of being silently dropped — an overwrite that
    only changes the body shouldn't destroy status/tags/created/etc.

    dry_run=True previews the result (including the merged frontmatter and a
    unified diff against the current file) without writing anything —
    check the preview before setting dry_run=False.
    """
    _require_note_path(path)
    storage = _storage()
    target = storage.resolve_write(path)
    path = target.relative

    lock = acquire_lock(path, lock_path=get_config().lock_path)
    try:
        exists = storage.exists(path, read=True)
        if exists:
            before, current_revision = read_text_for_update(storage, path, expected_revision)
        else:
            if expected_revision is not None:
                raise RevisionConflictError(path, expected_revision, None)
            before, current_revision = "", None

        frontmatter_preserved = False
        if not _FM_RE.match(content) and before:
            existing_fm, _ = _parse_frontmatter(before)
            if existing_fm:
                content = _serialize_frontmatter(existing_fm, content)
                frontmatter_preserved = True

        diff = _unified_diff(before, content, path)

        if dry_run:
            return {
                "path": path,
                "status": "dry_run",
                "frontmatter_preserved": frontmatter_preserved,
                "preview": content,
                "diff": diff,
                "revision": current_revision,
            }

        if (
            exists
            and not create_only
            and get_config().require_write_preconditions
            and expected_revision is None
        ):
            raise PreconditionRequiredError(path)
        revision = storage.write_text_atomic(
            path,
            content,
            expected_revision=current_revision,
            create_only=create_only or not exists,
        )
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result({
        "path": path,
        "status": "written",
        "frontmatter_preserved": frontmatter_preserved,
        "diff": diff,
    }, revision)


def patch_note(
    path: str,
    section: str,
    new_content: str,
    mode: str = "replace",
    target_type: str = "heading",
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Edit a note section or block reference.

    mode: 'replace' | 'insert_before' | 'insert_after' | 'append'
    target_type: 'heading' | 'block_ref'
    """
    _require_note_path(path)
    target = _storage().resolve_write(path)
    path = target.relative

    lock = acquire_lock(path, lock_path=get_config().lock_path)
    try:
        storage = _storage()
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        if target_type == "block_ref":
            patched = _patch_block_ref(raw, section, new_content, mode)
        else:
            patched = _patch_section(raw, section, new_content, mode)
        revision = storage.write_text_atomic(path, patched, expected_revision=current_revision)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "patched", "mode": mode, "target_type": target_type},
        revision,
    )


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


def delete_note(
    path: str,
    trash: bool = True,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Delete a note. trash=True moves it to .trash/ in the vault root."""
    cfg = get_config()
    _require_note_path(path)
    target = _storage().resolve_delete(path, permanent=not trash)
    path = target.relative
    lock = acquire_lock(path, lock_path=cfg.lock_path)
    try:
        storage = _storage()
        current = storage.revision(path, read=False)
        if expected_revision is not None and current.token != normalize_revision_token(expected_revision):
            raise RevisionConflictError(path, expected_revision, current)
        if cfg.require_write_preconditions and expected_revision is None:
            raise PreconditionRequiredError(path)
        if trash:
            storage.trash(path)
        else:
            storage.delete(path)
    finally:
        lock.release()

    if index is not None:
        index.remove(path)

    return {"path": path, "status": "deleted", "trash": trash}


def restore_note(trashed_name: str, to_path: str, index: VaultIndex | None = None) -> dict:
    """Restore a note previously moved to .trash/ (via delete_note trash=True).

    trashed_name: the filename as it sits under .trash/ (see list_trash_tool) —
    a collision at delete time may have appended a random suffix, so this is
    often not the note's original filename.
    to_path: where to put it back (you choose it; the original folder isn't
    recoverable from the trash entry alone).
    """
    if "/" in trashed_name or "\\" in trashed_name or trashed_name in (".", ".."):
        raise ValueError(f"trashed_name must be a bare filename, not a path: {trashed_name!r}")

    cfg = get_config()
    if not to_path.lower().endswith(".md"):
        raise ValueError("Note paths must end in .md")
    storage = _storage()
    storage.resolve_write(to_path)
    info = storage.trash_info(trashed_name)
    if info.is_dir:
        raise ValueError("The trashed item is a folder, not a note")

    lock = acquire_lock(f".trash/{trashed_name}", lock_path=cfg.lock_path)
    try:
        destination = storage.restore(trashed_name, to_path)
        to_path = destination.relative
    finally:
        lock.release()

    if index is not None:
        index.update(to_path)

    revision = storage.revision(to_path, read=False)
    return revision_result(
        {"from": f".trash/{trashed_name}", "to": to_path, "status": "restored"},
        revision,
    )


def append_to_note(
    path: str,
    content: str,
    section: str | None = None,
    create: bool = True,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Append content to a note (or a specific section). Creates the note if it doesn't exist."""
    _require_note_path(path)
    target = _storage().resolve_write(path)
    path = target.relative
    lock = acquire_lock(path, lock_path=get_config().lock_path)
    try:
        storage = _storage()
        if storage.exists(path, read=True):
            raw, current_revision = read_text_for_update(storage, path, expected_revision)
            if section:
                patched = _patch_section(raw, section, content, mode="append")
            else:
                patched = raw.rstrip("\n") + "\n\n" + content.strip() + "\n"
        elif create:
            if expected_revision is not None:
                raise RevisionConflictError(path, expected_revision, None)
            patched = content
            current_revision = None
        else:
            raise FileNotFoundError(f"Note not found: {path!r}")
        revision = storage.write_text_atomic(
            path,
            patched,
            expected_revision=current_revision,
            create_only=current_revision is None,
        )
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result({"path": path, "status": "appended"}, revision)


def patch_frontmatter(
    path: str,
    updates: dict,
    merge_arrays: bool = True,
    index: VaultIndex | None = None,
    dry_run: bool = False,
    expected_revision: str | None = None,
) -> dict:
    """Update YAML frontmatter, optionally returning a write-free preview."""
    _require_note_path(path)
    target = _storage().resolve_write(path)
    path = target.relative

    if not _storage().exists(path, read=True):
        raise FileNotFoundError(f"Note not found: {path!r}")

    lock = acquire_lock(path, lock_path=get_config().lock_path)
    try:
        storage = _storage()
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        patched = _apply_frontmatter_updates(raw, updates, merge_arrays)
        diff = _unified_diff(raw, patched, path)

        if dry_run:
            return {
                "path": path,
                "status": "dry_run",
                "updated_keys": list(updates.keys()),
                "preview": patched,
                "diff": diff,
                "revision": current_revision,
            }

        revision = storage.write_text_atomic(path, patched, expected_revision=current_revision)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result({
        "path": path,
        "status": "frontmatter_patched",
        "updated_keys": list(updates.keys()),
        "diff": diff,
    }, revision)


def patch_frontmatter_batch(
    updates: list[dict],
    index: VaultIndex | None = None,
) -> dict:
    """Patch frontmatter on multiple notes in one call instead of one
    patch_frontmatter_tool call per note.

    updates: list of {"path": str, "updates": dict, "merge_arrays": bool}
    (merge_arrays defaults to True per entry, same as patch_frontmatter_tool).
    One entry failing (missing note, bad path, ...) doesn't abort the rest —
    each result lands in `succeeded` or `failed`.
    """
    succeeded: list[dict] = []
    failed: list[dict] = []
    for entry in updates:
        path = entry.get("path")
        try:
            result = patch_frontmatter(
                path,
                entry.get("updates", {}),
                merge_arrays=entry.get("merge_arrays", True),
                index=index,
                expected_revision=entry.get("expected_revision"),
            )
            succeeded.append(result)
        except Exception as exc:
            failed.append({"path": path, "error": str(exc)})

    return {"succeeded": succeeded, "failed": failed}


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
    expected_revision: str | None = None,
) -> dict:
    """Add or remove tags on a note. Updates frontmatter tags array and strips inline #tags."""
    _require_note_path(path)
    target = _storage().resolve_write(path)
    path = target.relative

    if not _storage().exists(path, read=True):
        raise FileNotFoundError(f"Note not found: {path!r}")

    add = list(add or [])
    remove = list(remove or [])

    lock = acquire_lock(path, lock_path=get_config().lock_path)
    try:
        storage = _storage()
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        patched = _apply_tag_changes(raw, add, remove)
        revision = storage.write_text_atomic(path, patched, expected_revision=current_revision)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "tags_updated", "added": add, "removed": remove}, revision
    )


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
    if not from_path.lower().endswith(".md") or not to_path.lower().endswith(".md"):
        raise ValueError("Note paths must end in .md")
    source = _storage().resolve_delete(from_path)
    destination = _storage().resolve_write(to_path)
    from_path, to_path = source.relative, destination.relative
    storage = _storage()

    if not storage.exists(from_path, read=False):
        raise FileNotFoundError(f"Source note not found: {from_path!r}")
    if storage.exists(to_path, read=False):
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
    all_md = [candidate for candidate in storage.list_files() if candidate.relative.lower().endswith(".md")]

    # Collect and lock all files we'll touch
    locks = []
    files_to_rewrite: list[tuple[str, str]] = []

    for candidate in all_md:
        rel = candidate.relative
        if rel == from_path:
            continue
        try:
            raw = _storage().read_text(rel)
        except Exception:
            continue
        if link_re.search(raw):
            # Link rewriting is a second mutation. Preflight every affected
            # path so a restricted move cannot partially mutate the vault
            # before discovering an out-of-scope note.
            _check_write_permission(rel)
            files_to_rewrite.append((rel, raw))

    try:
        for rel, _ in files_to_rewrite:
            locks.append(acquire_lock(rel, lock_path=cfg.lock_path))
        locks.append(acquire_lock(from_path, lock_path=cfg.lock_path))

        # Rewrite links
        for rel, raw in files_to_rewrite:
            rewritten = link_re.sub(lambda m: f"[[{to_stem}{m.group(2)}]]", raw)
            storage.write_text_atomic(rel, rewritten)
            updated_files.append(rel)

        # Move the file through the same policy gateway.
        _storage().move(from_path, to_path)
    finally:
        for lock in reversed(locks):
            lock.release()

    if index is not None:
        index.remove(from_path)
        index.update(to_path)
        for rel in updated_files:
            index.update(rel)

    return {"from": from_path, "to": to_path, "updated_links_in": updated_files}


def _is_writable(rel_path: str) -> bool:
    """Like _check_write_permission, but returns False instead of raising —
    used to silently skip out-of-scope files during a vault-wide sweep
    instead of aborting the whole operation for one file."""
    try:
        _check_write_permission(rel_path)
    except WritePermissionError:
        return False
    return True


def _match_snippets(raw: str, pattern: re.Pattern, limit: int = 3) -> list[dict]:
    """Up to `limit` example matches, each with its 1-indexed line number and
    the full matched line — mirrors search_notes' snippet shape for
    familiarity, but simpler since there's no relevance scoring here."""
    snippets = []
    for i, line in enumerate(raw.splitlines()):
        if pattern.search(line):
            snippets.append({"line": i + 1, "text": line})
            if len(snippets) >= limit:
                break
    return snippets


def patch_note_text(
    path: str,
    find: str,
    replace: str,
    mode: str = "exact",
    count: int = 1,
    dry_run: bool = False,
    index: VaultIndex | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Find and replace text anywhere in a note's body — no heading/block-ref
    anchor required, unlike patch_note_tool. For a handful of scattered
    single-note edits (e.g. "bump this one enum value" inside a long note)
    this is far cheaper than reading, rewriting, and re-sending the whole
    note through write_note_tool.

    mode: 'exact' (literal substring, default) | 'regex'.
    count: max replacements (default 1, first match only); 0 = replace all.
    dry_run=True (default False) previews matches and the resulting diff
    without writing anything.
    Raises ValueError if `find` doesn't match anything in the note.
    """
    _require_note_path(path)
    storage = _storage()
    target = storage.resolve_write(path)
    path = target.relative
    if not storage.exists(path, read=True):
        raise FileNotFoundError(f"Note not found: {path!r}")

    if mode == "regex":
        try:
            pattern = re.compile(find)
        except re.error as exc:
            raise ValueError(f"Invalid regex: {exc}") from exc
    else:
        pattern = re.compile(re.escape(find))

    lock = acquire_lock(path, lock_path=get_config().lock_path)
    try:
        raw, current_revision = read_text_for_update(storage, path, expected_revision)
        replace_count = count if count > 0 else 0
        patched, n = pattern.subn(replace, raw, count=replace_count)
        if n == 0:
            raise ValueError(f"{find!r} not found in {path!r}")
        diff = _unified_diff(raw, patched, path)

        if dry_run:
            return {
                "path": path,
                "status": "dry_run",
                "replacements": n,
                "preview": _match_snippets(raw, pattern, limit=5),
                "diff": diff,
                "revision": current_revision,
            }

        revision = storage.write_text_atomic(path, patched, expected_revision=current_revision)
    finally:
        lock.release()

    if index is not None:
        index.update(path)

    return revision_result(
        {"path": path, "status": "patched", "replacements": n, "diff": diff}, revision
    )


def find_replace_in_vault(
    search: str,
    replace: str,
    mode: str = "exact",
    folder: str = "",
    dry_run: bool = True,
    index: VaultIndex | None = None,
) -> dict:
    """Find and replace text across every note in the vault (or a subfolder).

    mode: 'exact' (literal substring) | 'regex'.
    dry_run=True (default) only previews matches, writes nothing — always
    run once with dry_run=True first to check what would change before
    setting dry_run=False. .trash/ and EXCLUDE_PATHS are always skipped;
    files outside WRITE_PATHS (or all files, if the server is READ_ONLY)
    are silently skipped rather than aborting the whole run, and reported
    under "skipped_write_protected".
    """
    cfg = get_config()
    storage = _storage()

    if mode == "regex":
        try:
            pattern = re.compile(search)
        except re.error as exc:
            raise ValueError(f"Invalid regex: {exc}") from exc
    else:
        pattern = re.compile(re.escape(search))

    candidates: list[tuple[str, str, int]] = []
    skipped_write_protected: list[str] = []
    for candidate in storage.list_files(folder):
        rel = candidate.relative
        if not rel.lower().endswith(".md"):
            continue
        if _is_excluded(rel, cfg.exclude_paths) or Path(rel).parts[0] == ".trash":
            continue
        try:
            raw = storage.read_text(rel)
        except Exception:
            continue
        count = len(pattern.findall(raw))
        if count == 0:
            continue
        if not _is_writable(rel):
            skipped_write_protected.append(rel)
            continue
        candidates.append((rel, raw, count))

    if dry_run:
        return {
            "dry_run": True,
            "matches": [
                {"path": rel, "match_count": count, "preview": _match_snippets(raw, pattern)}
                for rel, raw, count in candidates
            ],
            "total_matches": sum(count for *_, count in candidates),
            "skipped_write_protected": skipped_write_protected,
        }

    if not candidates:
        return {
            "dry_run": False,
            "replaced_in": [],
            "total_replacements": 0,
            "skipped_write_protected": skipped_write_protected,
        }

    locks = []
    try:
        for rel, _, _ in candidates:
            locks.append(acquire_lock(rel, lock_path=cfg.lock_path))
        replaced_in: list[str] = []
        total = 0
        for rel, raw, count in candidates:
            storage.write_text_atomic(rel, pattern.sub(replace, raw))
            replaced_in.append(rel)
            total += count
    finally:
        for lock in reversed(locks):
            lock.release()

    if index is not None:
        for rel in replaced_in:
            index.update(rel)

    return {
        "dry_run": False,
        "replaced_in": replaced_in,
        "total_replacements": total,
        "skipped_write_protected": skipped_write_protected,
    }
