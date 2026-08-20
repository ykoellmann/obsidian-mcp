from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from ..config import get_config
from ..domain.parser import parse_note
from ..storage.filesystem import read_file, validate_path
from .query import matches_frontmatter_filter


def list_notes(folder: str = "", include_meta: bool = False) -> list:
    cfg = get_config()
    root = cfg.vault_path
    base = validate_path(root, folder) if folder else root

    results = []
    for p in base.rglob("*.md"):
        rel = str(p.relative_to(root))
        if _is_excluded(rel, cfg.exclude_paths):
            continue
        if include_meta:
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
                note = parse_note(raw, path=rel)
                results.append({
                    "path": rel,
                    "title": note.frontmatter.get("title", p.stem),
                    "tags": note.tags,
                    "status": note.frontmatter.get("status"),
                    "created": str(note.frontmatter.get("created", "")),
                    "mtime": p.stat().st_mtime,
                })
            except Exception:
                results.append({"path": rel, "title": p.stem, "tags": [], "status": None, "created": "", "mtime": 0.0})
        else:
            results.append(rel)

    return sorted(results, key=lambda x: x["path"] if isinstance(x, dict) else x)


def read_note(path: str) -> dict:
    cfg = get_config()
    raw = read_file(cfg.vault_path, path)
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
    frontmatter_filter: dict | None = None,
    field: str | None = None,
    threshold: float = 0.8,
) -> list[dict]:
    """Full-text search across the vault.
    mode: 'exact' (default) | 'regex' | 'fuzzy'.
    frontmatter_filter: same shape as query_notes_tool's (plain value = exact
    match, or an operator dict like {"$ne": v}/{"$nin": [...]}/{"$exists": bool}) —
    combines with the text search in a single call instead of filtering first
    and grepping results after.
    field: None/'body' (default, search line-by-line in the note body) |
    'filename' (match only against the file name, not its content).
    threshold: fuzzy-match similarity cutoff (0-1, only used when mode='fuzzy')."""
    cfg = get_config()
    root = cfg.vault_path
    results: list[dict] = []

    if frontmatter_filter:
        matches_frontmatter_filter({}, frontmatter_filter)  # validate operators up front

    try:
        pattern = re.compile(query, re.IGNORECASE) if mode == "regex" else None
    except re.error:
        pattern = None

    for p in root.rglob("*.md"):
        rel = str(p.relative_to(root))
        if _is_excluded(rel, cfg.exclude_paths):
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            note = parse_note(raw, path=rel)
            if tag and tag not in note.tags:
                continue
            if frontmatter_filter and not matches_frontmatter_filter(note.frontmatter, frontmatter_filter):
                continue

            if field == "filename":
                snippets, score = _match_filename(p.name, query, pattern, mode, threshold)
            else:
                lines = raw.splitlines()
                snippets, score = _find_snippets(lines, query, pattern, mode, threshold)
            if score == 0:
                continue

            results.append({"path": rel, "score": score, "snippets": snippets, "tags": note.tags})
        except Exception:
            pass

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def _match_filename(
    name: str,
    query: str,
    pattern: re.Pattern | None,
    mode: str,
    threshold: float,
) -> tuple[list[dict], int]:
    """Match `query` against a bare filename instead of note body lines —
    reuses _find_snippets' line-scoring logic on a single-line input."""
    snippets, score = _find_snippets([name], query, pattern, mode, threshold)
    if snippets:
        snippets = [{"line": 0, "context": [{"line": 0, "text": name, "match": True}]}]
    return snippets, score


_EMBED_RE = re.compile(r"!\[\[([^\]#|^]+?)(?:#([^\]|^]+?))?(?:\|[^\]]*)?\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def get_note_outline(path: str) -> dict:
    """Return the structural map of a note: headings, block refs, frontmatter keys.
    Does not return body text — efficient for large notes."""
    cfg = get_config()
    raw = read_file(cfg.vault_path, path)
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
    raw = read_file(cfg.vault_path, path)
    if depth == 0:
        return raw

    def _replace(m: re.Match) -> str:
        target_stem = m.group(1).strip()
        heading = (m.group(2) or "").strip()
        target_rel = _find_note_by_stem(cfg.vault_path, target_stem, cfg.exclude_paths)
        if target_rel is None:
            return m.group(0)
        try:
            target_raw = read_file(cfg.vault_path, target_rel)
            if heading:
                target_raw = _extract_section(target_raw, heading)
            if depth > 1:
                target_raw = render_note(target_rel, depth=depth - 1)
            return f"\n---\n*from [[{target_stem}]]:*\n{target_raw.strip()}\n---\n"
        except Exception:
            return m.group(0)

    return _EMBED_RE.sub(_replace, raw)


def _find_note_by_stem(root: Path, stem: str, exclude_paths: list[str]) -> str | None:
    stem_lower = stem.lower()
    for p in root.rglob("*.md"):
        rel = str(p.relative_to(root))
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
    threshold: float = 0.8,
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
            line_score = _fuzzy_score(line_lower, query_lower, threshold)
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


def _fuzzy_score(line_lower: str, query_lower: str, threshold: float = 0.8) -> int:
    """Fuzzy match: all query words must find a similar word in the line
    (ratio >= threshold, default 0.8 — lower it for looser/more tolerant
    matches, raise it to cut down on unrelated noise)."""
    if query_lower in line_lower:
        return 2
    query_words = query_lower.split()
    line_words = line_lower.split()
    matched_words = 0
    for qw in query_words:
        if any(SequenceMatcher(None, qw, lw).ratio() >= threshold for lw in line_words):
            matched_words += 1
    return matched_words if matched_words == len(query_words) and query_words else 0


def _is_excluded(rel_path: str, exclude_paths: list[str]) -> bool:
    parts = Path(rel_path).parts
    return any(part in exclude_paths for part in parts)
