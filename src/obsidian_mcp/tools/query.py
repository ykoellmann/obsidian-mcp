from __future__ import annotations

from collections import deque
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..config import get_config
from ..domain.index import VaultIndex
from ..domain.parser import parse_note
from ..storage.filesystem import VaultStorage


def _load_note(vault_path, note_path: str):
    """Read and parse a single note. Thin helper to avoid repetition."""
    return parse_note(VaultStorage.from_config().read_text(note_path), path=note_path)


def get_backlinks(path: str, index: VaultIndex) -> list[str]:
    return index.get_backlinks(path)


def get_notes_by_tag(tag: str, index: VaultIndex) -> list[str]:
    return index.get_notes_by_tag(tag)


def get_vault_conventions() -> str:
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    try:
        raw = storage.read_text("_AI_INSTRUCTIONS.md")
    except (FileNotFoundError, PermissionError):
        return ""
    return raw


def get_broken_links(index: VaultIndex) -> list[dict]:
    cfg = get_config()
    results: list[dict] = []
    for note_path in sorted(index.get_all_notes()):
        try:
            note = _load_note(cfg.vault_path, note_path)
            for wl in note.wikilinks:
                if not index.has_note(wl.target):
                    results.append({"source": note_path, "link": wl.target})
        except Exception:
            pass
    return results


def get_orphans(index: VaultIndex, exclude_folders: list[str] | None = None) -> list[str]:
    exclude_folders = exclude_folders or []
    all_notes = index.get_all_notes()
    orphans = []
    for note_path in sorted(all_notes):
        parts = Path(note_path).parts
        if any(part in exclude_folders for part in parts):
            continue
        if not index.get_backlinks(note_path):
            orphans.append(note_path)
    return orphans


def get_link_graph(
    root: str,
    index: VaultIndex,
    depth: int = 2,
    direction: str = "both",
) -> dict:
    cfg = get_config()
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(root, 0)])

    def _meta(path: str) -> dict:
        try:
            note = _load_note(cfg.vault_path, path)
            return {
                "path": path,
                "title": note.frontmatter.get("title", Path(path).stem),
                "tags": note.tags,
            }
        except Exception:
            return {"path": path, "title": Path(path).stem, "tags": []}

    while queue:
        current, level = queue.popleft()
        if current in visited or level > depth:
            continue
        visited.add(current)
        nodes[current] = _meta(current)

        if direction in ("outgoing", "both"):
            for target in sorted(index.get_outlinks(current)):
                resolved = index.resolve_alias(target) or target
                edges.append({"from": current, "to": resolved, "type": "outgoing"})
                if resolved not in visited:
                    queue.append((resolved, level + 1))

        if direction in ("incoming", "both"):
            for source in index.get_backlinks(current):
                edges.append({"from": source, "to": current, "type": "incoming"})
                if source not in visited:
                    queue.append((source, level + 1))

    # Deduplicate edges
    seen_edges: set[tuple] = set()
    unique_edges = []
    for e in edges:
        key = (e["from"], e["to"])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(e)

    return {"root": root, "nodes": list(nodes.values()), "edges": unique_edges}


def get_vault_stats(index: VaultIndex) -> dict:
    all_notes = index.get_all_notes()
    total_links = sum(len(index.get_outlinks(p)) for p in all_notes)
    orphans = get_orphans(index)
    broken = get_broken_links(index)

    # Most linked = notes with most backlinks
    by_backlinks = sorted(all_notes, key=lambda p: len(index.get_backlinks(p)), reverse=True)

    return {
        "total_notes": len(all_notes),
        "total_links": total_links,
        "orphans_count": len(orphans),
        "broken_links_count": len(broken),
        "most_linked": by_backlinks[:5],
        "index_ready": index.is_ready(),
    }


def get_tag_tree(index: VaultIndex) -> dict:
    return index.get_tag_tree()


def get_tasks(
    index: VaultIndex,
    status: str = "open",
    folder: str = "",
    tag: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
) -> list[dict]:
    """due_before/due_after: 'YYYY-MM-DD', inclusive, compared against each
    task's 📅 due date (tasks without a due date never match either filter)."""
    cfg = get_config()
    all_notes = index.get_all_notes()
    results: list[dict] = []

    for note_path in sorted(all_notes):
        if folder and not note_path.startswith(folder.rstrip("/") + "/"):
            continue
        try:
            note = _load_note(cfg.vault_path, note_path)
            if tag and tag not in note.tags:
                continue
            for task in note.tasks:
                if status == "open" and task.done:
                    continue
                if status == "done" and not task.done:
                    continue
                if (due_before or due_after) and not task.due:
                    continue
                if due_before and task.due > due_before:
                    continue
                if due_after and task.due < due_after:
                    continue
                results.append({
                    "text": task.text,
                    "done": task.done,
                    "source": note_path,
                    "line": task.line,
                    "due": task.due,
                    "recurrence": task.recurrence,
                    "priority": task.priority,
                    "done_date": task.done_date,
                })
        except Exception:
            pass
    return results


def get_daily_note(index: VaultIndex, date_str: str = "today") -> dict:
    return get_periodic_note(index, period="daily", date_str=date_str)


def resolve_alias(name: str, index: VaultIndex) -> str | None:
    return index.resolve_alias(name)


def list_all_tags(index: VaultIndex, sort_by: str = "count") -> list[dict]:
    """Return all tags in the vault with note counts.
    sort_by: 'count' (descending) | 'name' (ascending)."""
    counts = index.get_all_tags_with_counts()
    tags = [{"tag": tag, "count": count} for tag, count in counts.items()]
    if sort_by == "name":
        tags.sort(key=lambda x: x["tag"])
    else:
        tags.sort(key=lambda x: (-x["count"], x["tag"]))
    return tags


def get_periodic_note(index: VaultIndex, period: str = "daily", date_str: str = "today") -> dict:
    """Read or preview a periodic note (daily/weekly/monthly/quarterly/yearly).
    date_str: 'today' | 'yesterday' | ISO date string (YYYY-MM-DD)."""
    cfg = get_config()

    if date_str == "today":
        target = date.today()
    elif date_str == "yesterday":
        target = date.today() - timedelta(days=1)
    else:
        target = date.fromisoformat(date_str)

    iso_cal = target.isocalendar()

    if period == "daily":
        note_id = target.isoformat()
        rel_path = f"Journal/{note_id}.md"
        template_name = "Daily-Note-Template.md"
    elif period == "weekly":
        note_id = f"{iso_cal[0]}-W{iso_cal[1]:02d}"
        rel_path = f"Journal/Weekly/{note_id}.md"
        template_name = "Weekly-Note-Template.md"
    elif period == "monthly":
        note_id = target.strftime("%Y-%m")
        rel_path = f"Journal/Monthly/{note_id}.md"
        template_name = "Monthly-Note-Template.md"
    elif period == "quarterly":
        q = (target.month - 1) // 3 + 1
        note_id = f"{target.year}-Q{q}"
        rel_path = f"Journal/Quarterly/{note_id}.md"
        template_name = "Quarterly-Note-Template.md"
    elif period == "yearly":
        note_id = str(target.year)
        rel_path = f"Journal/Yearly/{note_id}.md"
        template_name = "Yearly-Note-Template.md"
    else:
        raise ValueError(f"Unknown period {period!r}. Use: daily|weekly|monthly|quarterly|yearly")

    storage = VaultStorage.from_config(cfg)
    target = storage.resolve_read(rel_path)
    if storage.exists(target.relative):
        raw = storage.read_text(target.relative)
        note = parse_note(raw, path=rel_path)
        return {
            "path": rel_path,
            "period": period,
            "date": note_id,
            "exists": True,
            "content": note.content,
            "frontmatter": note.frontmatter,
            "tasks": [{"text": t.text, "done": t.done, "line": t.line} for t in note.tasks],
        }

    # Preview from template if available
    content = ""
    template_rel = f"Templates/{template_name}"
    try:
        raw_tpl = storage.read_text(template_rel)
        content = raw_tpl.replace("{{date}}", note_id).replace("{{title}}", note_id)
    except (FileNotFoundError, PermissionError):
        pass

    return {"path": rel_path, "period": period, "date": note_id, "exists": False,
            "content": content, "frontmatter": {}, "tasks": []}


def query_notes(
    index: VaultIndex,
    tags: list[str] | None = None,
    status: str | None = None,
    frontmatter_filter: dict | None = None,
    inline_field_filter: dict | None = None,
    sort_by: str = "path",
    sort_desc: bool = False,
    limit: int = 50,
    folder: str = "",
) -> list[dict]:
    """Filter notes by tags, status, or arbitrary frontmatter fields.
    sort_by: 'path' | 'title' | 'created' | 'mtime'."""
    cfg = get_config()
    storage = VaultStorage.from_config(cfg)
    all_notes = index.get_all_notes()
    results: list[dict] = []

    for note_path in sorted(all_notes):
        if folder and not note_path.startswith(folder.rstrip("/") + "/"):
            continue
        try:
            note = _load_note(cfg.vault_path, note_path)

            if tags and not all(t in note.tags for t in tags):
                continue

            note_status = note.frontmatter.get("status")
            if status is not None and note_status != status:
                continue

            if frontmatter_filter and not all(
                note.frontmatter.get(k) == v for k, v in frontmatter_filter.items()
            ):
                continue

            if inline_field_filter and not all(
                note.inline_fields.get(k) == str(v) for k, v in inline_field_filter.items()
            ):
                continue

            full = storage.resolve_read(note_path)
            results.append({
                "path": note_path,
                "title": note.frontmatter.get("title", Path(note_path).stem),
                "tags": note.tags,
                "status": note_status,
                "created": str(note.frontmatter.get("created", "")),
                "mtime": storage.stat(full.relative).st_mtime if storage.exists(full.relative) else 0.0,
                "frontmatter": note.frontmatter,
                "inline_fields": note.inline_fields,
            })
        except Exception:
            pass

    _sort_keys: dict[str, Any] = {
        "path": lambda x: x["path"],
        "title": lambda x: x["title"].lower(),
        "created": lambda x: x["created"],
        "mtime": lambda x: x["mtime"],
    }
    key_fn = _sort_keys.get(sort_by, _sort_keys["path"])
    results.sort(key=key_fn, reverse=sort_desc)
    return results[:limit]
