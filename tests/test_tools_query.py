from __future__ import annotations

from second_brain_mcp.tools.query import (
    get_broken_links,
    get_link_graph,
    get_orphans,
    get_tag_tree,
    get_tasks,
    get_vault_stats,
)
from second_brain_mcp.tools.read import list_notes, search_notes

# --- broken links ---

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


# --- orphans ---

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


# --- link graph ---

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


# --- vault stats ---

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


# --- tasks ---

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


# --- tag tree ---

def test_tag_tree_structure(vault_factory):
    idx = vault_factory({
        "a.md": "---\ntags: [konzept/python]\n---\n",
        "b.md": "---\ntags: [konzept/ki]\n---\n",
        "c.md": "---\ntags: [projekt]\n---\n",
    })
    tree = get_tag_tree(idx)
    assert "konzept" in tree
    assert "projekt" in tree


# --- search with snippets ---

def test_search_returns_snippets(vault_factory):
    vault_factory({"note.md": "Line 1\nThis has the keyword here\nLine 3\n"})
    results = search_notes("keyword")
    assert len(results) == 1
    assert results[0]["path"] == "note.md"
    assert results[0]["score"] > 0
    assert len(results[0]["snippets"]) > 0
    assert results[0]["snippets"][0]["line"] == 2


def test_search_ranking(vault_factory):
    vault_factory({
        "exact.md": "python",
        "phrase.md": "I love python programming",
        "substring.md": "pythonista",
    })
    results = search_notes("python")
    scores = {r["path"]: r["score"] for r in results}
    assert scores.get("exact.md", 0) >= scores.get("substring.md", 0)


def test_search_regex_mode(vault_factory):
    vault_factory({"note.md": "foo123bar\nbaz456qux\n"})
    results = search_notes(r"foo\d+bar", mode="regex")
    assert len(results) == 1


def test_search_tag_filter(vault_factory):
    vault_factory({
        "tagged.md": "---\ntags: [python]\n---\nsome python content",
        "untagged.md": "some python content",
    })
    results = search_notes("python", tag="python")
    assert len(results) == 1
    assert results[0]["path"] == "tagged.md"


# --- list_notes with meta ---

def test_list_notes_with_meta(vault_factory):
    vault_factory({"note.md": "---\ntitle: My Note\ntags: [foo]\nstatus: active\n---\nContent"})
    results = list_notes(include_meta=True)
    assert len(results) == 1
    assert results[0]["path"] == "note.md"
    assert results[0]["title"] == "My Note"
    assert "foo" in results[0]["tags"]
    assert results[0]["status"] == "active"
