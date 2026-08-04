from __future__ import annotations

from datetime import date

import pytest

from obsidian_mcp.tools.query import (
    get_broken_links,
    get_link_graph,
    get_orphans,
    get_periodic_note,
    get_tag_tree,
    get_tasks,
    get_vault_stats,
    list_all_tags,
    query_notes,
    resolve_alias,
)

# ── get_broken_links ──────────────────────────────────────────────────────

def test_broken_links_detected(vault_factory):
    idx = vault_factory({
        "a.md": "Links to [[exists]] and [[missing]]",
        "exists.md": "I exist.",
    })
    broken = get_broken_links(idx)
    assert any(b["link"] == "missing" for b in broken)
    assert not any(b["link"] == "exists" for b in broken)


def test_broken_links_empty_when_all_ok(vault_factory):
    idx = vault_factory({
        "a.md": "Links to [[b]]",
        "b.md": "I exist.",
    })
    assert get_broken_links(idx) == []


# ── get_orphans ───────────────────────────────────────────────────────────

def test_orphans_detected(vault_factory):
    idx = vault_factory({
        "linked.md": "Has a backlink.",
        "orphan.md": "Nobody links here.",
        "linker.md": "Links to [[linked]]",
    })
    orphans = get_orphans(idx)
    assert "orphan.md" in orphans
    assert "linked.md" not in orphans


def test_orphans_exclude_folders(vault_factory):
    idx = vault_factory({
        "Journal/2026-01-01.md": "Daily note, no backlinks.",
        "orphan.md": "Also no backlinks.",
    })
    orphans = get_orphans(idx, exclude_folders=["Journal"])
    assert "orphan.md" in orphans
    assert not any("Journal" in o for o in orphans)


# ── get_link_graph ────────────────────────────────────────────────────────

def test_link_graph_basic(vault_factory):
    idx = vault_factory({
        "root.md": "Links to [[child]]",
        "child.md": "Links to [[grandchild]]",
        "grandchild.md": "End node.",
    })
    graph = get_link_graph("root.md", idx, depth=2, direction="outgoing")
    node_paths = [n["path"] for n in graph["nodes"]]
    assert "root.md" in node_paths
    assert "child.md" in node_paths
    assert "grandchild.md" in node_paths


def test_link_graph_depth_limit(vault_factory):
    idx = vault_factory({
        "root.md": "Links to [[child]]",
        "child.md": "Links to [[grandchild]]",
        "grandchild.md": "End.",
    })
    graph = get_link_graph("root.md", idx, depth=1, direction="outgoing")
    node_paths = [n["path"] for n in graph["nodes"]]
    assert "child.md" in node_paths
    assert "grandchild.md" not in node_paths


# ── get_vault_stats ───────────────────────────────────────────────────────

def test_vault_stats(vault_factory):
    idx = vault_factory({
        "a.md": "Links to [[b]] and [[missing]]",
        "b.md": "Links to [[a]]",
        "orphan.md": "No links.",
    })
    stats = get_vault_stats(idx)
    assert stats["total_notes"] == 3
    assert stats["total_links"] >= 2
    assert stats["broken_links_count"] >= 1
    assert stats["orphans_count"] >= 1
    assert stats["index_ready"] is True


# ── get_tasks ─────────────────────────────────────────────────────────────

def test_get_tasks_open(vault_factory):
    idx = vault_factory({"tasks.md": "- [ ] Open task\n- [x] Done task\n"})
    open_tasks = get_tasks(idx, status="open")
    assert len(open_tasks) == 1
    assert open_tasks[0]["text"] == "Open task"
    assert open_tasks[0]["done"] is False


def test_get_tasks_done(vault_factory):
    idx = vault_factory({"tasks.md": "- [ ] Open\n- [x] Done\n"})
    done_tasks = get_tasks(idx, status="done")
    assert len(done_tasks) == 1
    assert done_tasks[0]["done"] is True


def test_get_tasks_all(vault_factory):
    idx = vault_factory({"tasks.md": "- [ ] Open\n- [x] Done\n"})
    all_tasks = get_tasks(idx, status="all")
    assert len(all_tasks) == 2


def test_get_tasks_includes_marker_fields(vault_factory):
    idx = vault_factory({"tasks.md": "- [ ] Ship it ⏫ 📅 2026-08-10 🔁 every week\n"})
    task = get_tasks(idx, status="open")[0]
    assert task["due"] == "2026-08-10"
    assert task["priority"] == "high"
    assert task["recurrence"] == "every week"
    assert task["done_date"] is None


def test_get_tasks_due_before_filters_out_later_and_unset(vault_factory):
    idx = vault_factory({
        "tasks.md": (
            "- [ ] Due soon 📅 2026-08-05\n"
            "- [ ] Due later 📅 2026-09-01\n"
            "- [ ] No due date\n"
        )
    })
    results = get_tasks(idx, status="open", due_before="2026-08-10")
    assert [t["text"] for t in results] == ["Due soon"]


def test_get_tasks_due_after_filters_out_earlier_and_unset(vault_factory):
    idx = vault_factory({
        "tasks.md": (
            "- [ ] Overdue 📅 2026-07-01\n"
            "- [ ] Upcoming 📅 2026-09-01\n"
            "- [ ] No due date\n"
        )
    })
    results = get_tasks(idx, status="open", due_after="2026-08-01")
    assert [t["text"] for t in results] == ["Upcoming"]


def test_get_tasks_due_range_is_inclusive(vault_factory):
    idx = vault_factory({"tasks.md": "- [ ] Exactly on date 📅 2026-08-10\n"})
    results = get_tasks(idx, status="open", due_before="2026-08-10", due_after="2026-08-10")
    assert len(results) == 1


# ── get_tag_tree ──────────────────────────────────────────────────────────

def test_tag_tree_structure(vault_factory):
    idx = vault_factory({
        "a.md": "---\ntags: [konzept/python]\n---\n",
        "b.md": "---\ntags: [konzept/ki]\n---\n",
        "c.md": "---\ntags: [projekt]\n---\n",
    })
    tree = get_tag_tree(idx)
    assert "konzept" in tree
    assert "projekt" in tree


# ── list_all_tags ─────────────────────────────────────────────────────────

def test_list_all_tags_counts(vault_factory):
    idx = vault_factory({
        "a.md": "---\ntags: [python, tech]\n---\n",
        "b.md": "---\ntags: [python]\n---\n",
        "c.md": "---\ntags: [tech]\n---\n",
    })
    result = list_all_tags(idx)
    by_tag = {r["tag"]: r["count"] for r in result}
    assert by_tag["python"] == 2
    assert by_tag["tech"] == 2


def test_list_all_tags_sort_by_name(vault_factory):
    idx = vault_factory({"a.md": "---\ntags: [zebra, apple]\n---\n"})
    result = list_all_tags(idx, sort_by="name")
    tags = [r["tag"] for r in result]
    assert tags == sorted(tags)


def test_list_all_tags_sort_by_count(vault_factory):
    idx = vault_factory({
        "a.md": "---\ntags: [popular]\n---\n",
        "b.md": "---\ntags: [popular]\n---\n",
        "c.md": "---\ntags: [rare]\n---\n",
    })
    result = list_all_tags(idx, sort_by="count")
    assert result[0]["tag"] == "popular"


# ── resolve_alias ─────────────────────────────────────────────────────────

def test_resolve_alias_found(vault_factory):
    idx = vault_factory({"Python Tips.md": "---\naliases: [Python]\n---\n"})
    assert resolve_alias("Python", idx) == "Python Tips.md"


def test_resolve_alias_not_found_returns_none(vault_factory):
    idx = vault_factory({})
    assert resolve_alias("DoesNotExist", idx) is None


# ── get_periodic_note ─────────────────────────────────────────────────────

def test_periodic_daily_exists(vault_factory):
    today = date.today().isoformat()
    idx = vault_factory({f"Journal/{today}.md": "---\ntags: [journal]\n---\nToday's note"})
    result = get_periodic_note(idx, period="daily", date_str="today")
    assert result["exists"] is True
    assert "Today's note" in result["content"]


def test_periodic_daily_not_exists(vault_factory):
    idx = vault_factory({})
    result = get_periodic_note(idx, period="daily", date_str="today")
    assert result["exists"] is False
    assert result["path"].startswith("Journal/")


def test_periodic_weekly_path(vault_factory):
    idx = vault_factory({})
    result = get_periodic_note(idx, period="weekly", date_str="today")
    assert result["path"].startswith("Journal/Weekly/")
    assert "-W" in result["date"]


def test_periodic_monthly_path(vault_factory):
    idx = vault_factory({})
    result = get_periodic_note(idx, period="monthly", date_str="2026-07-23")
    assert result["date"] == "2026-07"
    assert "Monthly" in result["path"]


def test_periodic_quarterly_path(vault_factory):
    idx = vault_factory({})
    result = get_periodic_note(idx, period="quarterly", date_str="2026-07-23")
    assert result["date"] == "2026-Q3"
    assert "Quarterly" in result["path"]


def test_periodic_yearly_path(vault_factory):
    idx = vault_factory({})
    result = get_periodic_note(idx, period="yearly", date_str="2026-07-23")
    assert result["date"] == "2026"
    assert "Yearly" in result["path"]


def test_periodic_invalid_period(vault_factory):
    idx = vault_factory({})
    with pytest.raises(ValueError):
        get_periodic_note(idx, period="hourly")


def test_periodic_uses_template(vault_factory):
    idx = vault_factory({
        "Templates/Weekly-Note-Template.md": "---\ntags: [journal]\n---\n## Week {{date}}\n"
    })
    result = get_periodic_note(idx, period="weekly", date_str="2026-07-23")
    assert result["exists"] is False
    assert "Week" in result["content"]
    assert "{{date}}" not in result["content"]


# ── query_notes ───────────────────────────────────────────────────────────

def test_query_notes_by_tag(vault_factory):
    idx = vault_factory({
        "a.md": "---\ntags: [projekt/aktiv]\nstatus: active\n---\n",
        "b.md": "---\ntags: [notiz]\n---\n",
    })
    results = query_notes(idx, tags=["projekt/aktiv"])
    assert len(results) == 1
    assert results[0]["path"] == "a.md"


def test_query_notes_by_status(vault_factory):
    idx = vault_factory({
        "a.md": "---\nstatus: active\n---\n",
        "b.md": "---\nstatus: done\n---\n",
        "c.md": "No frontmatter\n",
    })
    results = query_notes(idx, status="active")
    assert len(results) == 1
    assert results[0]["path"] == "a.md"


def test_query_notes_frontmatter_filter(vault_factory):
    idx = vault_factory({
        "a.md": "---\nprioritaet: 1\n---\n",
        "b.md": "---\nprioritaet: 5\n---\n",
    })
    results = query_notes(idx, frontmatter_filter={"prioritaet": 1})
    assert len(results) == 1
    assert results[0]["path"] == "a.md"


def test_query_notes_sort_by_title(vault_factory):
    idx = vault_factory({
        "z.md": "---\ntitle: Zebra\n---\n",
        "a.md": "---\ntitle: Apple\n---\n",
    })
    results = query_notes(idx, sort_by="title")
    assert results[0]["title"] == "Apple"
    assert results[1]["title"] == "Zebra"


def test_query_notes_sort_desc(vault_factory):
    idx = vault_factory({
        "z.md": "---\ntitle: Zebra\n---\n",
        "a.md": "---\ntitle: Apple\n---\n",
    })
    results = query_notes(idx, sort_by="title", sort_desc=True)
    assert results[0]["title"] == "Zebra"


def test_query_notes_limit(vault_factory):
    idx = vault_factory({f"{i}.md": f"Note {i}" for i in range(10)})
    results = query_notes(idx, limit=3)
    assert len(results) == 3


def test_query_notes_folder_filter(vault_factory):
    idx = vault_factory({
        "Projekte/p.md": "---\nstatus: active\n---\n",
        "Notizen/n.md": "---\nstatus: active\n---\n",
    })
    results = query_notes(idx, folder="Projekte")
    assert len(results) == 1
    assert "Projekte" in results[0]["path"]


def test_query_notes_inline_field_filter(vault_factory):
    idx = vault_factory({
        "a.md": "rating:: 8\nOther text",
        "b.md": "rating:: 5\nOther text",
        "c.md": "No inline fields",
    })
    results = query_notes(idx, inline_field_filter={"rating": "8"})
    assert len(results) == 1
    assert results[0]["path"] == "a.md"


def test_query_notes_inline_fields_in_result(vault_factory):
    idx = vault_factory({"note.md": "priority:: high\ncategory:: work\n"})
    results = query_notes(idx)
    assert len(results) == 1
    fields = results[0]["inline_fields"]
    assert fields["priority"] == "high"
    assert fields["category"] == "work"


def test_query_notes_combined_filter(vault_factory):
    idx = vault_factory({
        "a.md": "---\ntags: [project]\n---\npriority:: high\n",
        "b.md": "---\ntags: [project]\n---\npriority:: low\n",
        "c.md": "priority:: high\n",
    })
    results = query_notes(idx, tags=["project"], inline_field_filter={"priority": "high"})
    assert len(results) == 1
    assert results[0]["path"] == "a.md"
