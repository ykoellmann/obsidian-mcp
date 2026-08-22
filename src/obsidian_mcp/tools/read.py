from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from ..config import get_config
from ..domain.parser import parse_note
from ..storage.filesystem import VaultStorage


def list_notes(folder: str = "", include_meta: bool = False) -> list:
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)

    results = []
    for resolved in storage.list_files(folder):
        p, rel = resolved.absolute, resolved.relative
        if p.suffix.lower() != ".md":
            continue
        if _is_excluded(rel, cfg.exclude_paths):
            continue
        if include_meta:
            try:
                raw = storage.read_text(rel)
                note = parse_note(raw, path=rel)
                results.append({
                    "path": rel,
                    "title": note.frontmatter.get("title", p.stem),
                    "tags": note.tags,
                    "status": note.frontmatter.get("status"),
                    "created": str(note.frontmatter.get("created", "")),
                    "mtime": storage.stat(rel).st_mtime,
                })
            except Exception:
                results.append({"path": rel, "title": p.stem, "tags": [], "status": None, "created": "", "mtime": 0.0})
        else:
            results.append(rel)

    return sorted(results, key=lambda x: x["path"] if isinstance(x, dict) else x)


def read_note(path: str) -> dict:
    cfg = get_config()
    raw = VaultStorage.from_config(cfg).read_text(path)
    note = parse_note(raw, path=path)
    return {
        "content": note.content,
        "frontmatter": note.frontmatter,
        "tags": note.tags,
        "aliases": note.aliases,
        "wikilinks": [
            {"target": wl.target, "alias": wl.alias, "heading": wl.heading}
            for wl in note.wikilinks
        ],
        "block_refs": [
            {"block_id": br.block_id, "line": br.line, "text": br.text}
            for br in note.block_refs
        ],
        "callouts": [
            {"type": c.type, "title": c.title, "body": c.body}
            for c in note.callouts
        ],
        "tasks": [
            {
                "text": t.text,
                "done": t.done,
                "line": t.line,
                "due": t.due,
                "recurrence": t.recurrence,
                "priority": t.priority,
                "done_date": t.done_date,
            }
            for t in note.tasks
        ],
        "inline_fields": note.inline_fields,
    }


def search_notes(
    query: str,
    tag: str | None = None,
    mode: str = "exact",
    limit: int = 20,
) -> list[dict]:
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    results: list[dict] = []

    try:
        pattern = re.compile(query, re.IGNORECASE) if mode == "regex" else None
    except re.error:
        pattern = None

    for resolved in storage.list_files():
        p, rel = resolved.absolute, resolved.relative
        if p.suffix.lower() != ".md":
            continue
        if _is_excluded(rel, cfg.exclude_paths):
            continue
        try:
            raw = storage.read_text(rel)
            note = parse_note(raw, path=rel)
            if tag and tag not in note.tags:
                continue

            lines = raw.splitlines()
            snippets, score = _find_snippets(lines, query, pattern, mode)
            if score == 0:
                continue

            results.append({"path": rel, "score": score, "snippets": snippets, "tags": note.tags})
        except Exception:
            pass

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


_EMBED_RE = re.compile(r"!\[\[([^\]#|^]+?)(?:#([^\]|^]+?))?(?:\|[^\]]*)?\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def get_note_outline(path: str) -> dict:
    """Return the structural map of a note: headings, block refs, frontmatter keys.
    Does not return body text — efficient for large notes."""
    cfg = get_config()
    raw = VaultStorage.from_config(cfg).read_text(path)
    note = parse_note(raw, path=path)

    headings = [
        {"level": len(m.group(1)), "text": m.group(2).strip(), "line": raw[:m.start()].count("\n") + 1}
        for m in _HEADING_RE.finditer(raw)
    ]
    return {
        "path": path,
        "headings": headings,
        "block_refs": [
            {"block_id": br.block_id, "line": br.line, "text": br.text[:80]}
            for br in note.block_refs
        ],
        "frontmatter_keys": list(note.frontmatter.keys()),
        "tags": note.tags,
        "aliases": note.aliases,
        "inline_fields": note.inline_fields,
        "word_count": len(raw.split()),
        "line_count": len(raw.splitlines()),
    }


def render_note(path: str, depth: int = 1) -> str:
    """Read a note and resolve all ![[embed]] transclusions inline.
    depth controls recursion (0 = raw content, 1 = one level, 2 = two levels)."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    raw = storage.read_text(path)
    if depth == 0:
        return raw

    def _replace(m: re.Match) -> str:
        target_stem = m.group(1).strip()
        heading = (m.group(2) or "").strip()
        target_rel = _find_note_by_stem(cfg.vault_path, target_stem, cfg.exclude_paths, storage=storage)
        if target_rel is None:
            return m.group(0)
        try:
            target_raw = storage.read_text(target_rel)
            if heading:
                target_raw = _extract_section(target_raw, heading)
            if depth > 1:
                target_raw = render_note(target_rel, depth=depth - 1)
            return f"\n---\n*from [[{target_stem}]]:*\n{target_raw.strip()}\n---\n"
        except Exception:
            return m.group(0)

    return _EMBED_RE.sub(_replace, raw)


def _find_note_by_stem(
    root: Path,
    stem: str,
    exclude_paths: list[str],
    *,
    storage: VaultStorage | None = None,
) -> str | None:
    stem_lower = stem.lower()
    # A missing gateway is a programming error/closed result, not permission
    # to fall back to unrestricted filesystem traversal.
    if storage is None:
        return None
    candidates = storage.list_files()
    for resolved in candidates:
        p, rel = resolved.absolute, resolved.relative
        if p.suffix.lower() != ".md":
            continue
        if _is_excluded(rel, exclude_paths):
            continue
        if p.stem.lower() == stem_lower:
            return rel
    return None


def _extract_section(content: str, heading: str) -> str:
    """Extract the content under a Markdown heading."""
    lines = content.splitlines(keepends=True)
    heading_lower = heading.lower()
    in_section = False
    section_level = 0
    result: list[str] = []
    for line in lines:
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            title = line.lstrip("#").strip()
            if not in_section:
                if title.lower() == heading_lower:
                    in_section = True
                    section_level = level
                    result.append(line)
            else:
                if level <= section_level:
                    break
                result.append(line)
        elif in_section:
            result.append(line)
    return "".join(result) if result else content


def _find_snippets(
    lines: list[str],
    query: str,
    pattern: re.Pattern | None,
    mode: str,
) -> tuple[list[dict], int]:
    snippets: list[dict] = []
    total_score = 0
    query_lower = query.lower()

    for i, line in enumerate(lines):
        line_lower = line.lower()
        matched = False

        if mode == "regex" and pattern:
            matched = bool(pattern.search(line))
            line_score = 2 if matched else 0
        elif mode == "fuzzy":
            line_score = _fuzzy_score(line_lower, query_lower)
            matched = line_score > 0
        else:
            if query_lower in line_lower:
                matched = True
                if re.search(rf"\b{re.escape(query_lower)}\b", line_lower):
                    line_score = 3 if line_lower == query_lower else 2
                else:
                    line_score = 1
            else:
                line_score = 0

        if matched:
            total_score += line_score
            context_start = max(0, i - 2)
            context_end = min(len(lines), i + 3)
            snippet_lines = [
                {"line": j + 1, "text": lines[j], "match": j == i}
                for j in range(context_start, context_end)
            ]
            snippets.append({"line": i + 1, "context": snippet_lines})
            if len(snippets) >= 3:
                break

    return snippets, total_score


def _fuzzy_score(line_lower: str, query_lower: str) -> int:
    """Fuzzy match: all query words must find a similar word in the line (ratio ≥ 0.8)."""
    if query_lower in line_lower:
        return 2
    query_words = query_lower.split()
    line_words = line_lower.split()
    matched_words = 0
    for qw in query_words:
        if any(SequenceMatcher(None, qw, lw).ratio() >= 0.8 for lw in line_words):
            matched_words += 1
    return matched_words if matched_words == len(query_words) and query_words else 0


def _is_excluded(rel_path: str, exclude_paths: list[str]) -> bool:
    normalized = rel_path.replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    return any(rule.rstrip("/") in parts for rule in exclude_paths)
