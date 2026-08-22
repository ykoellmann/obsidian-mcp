"""Cross-module integration tests: write -> index -> query round-trips."""
from __future__ import annotations

from obsidian_mcp.tools.query import query_notes
from obsidian_mcp.tools.read import search_notes
from obsidian_mcp.tools.write import delete_note, manage_tags, write_note


def test_write_then_search(vault_factory):
    vault_factory({})
    write_note("note.md", "Unique phrase XYZ123 here")
    results = search_notes("XYZ123")
    assert any(r["path"] == "note.md" for r in results)


def test_write_creates_backlink(vault_factory):
    idx = vault_factory({"target.md": "I am the target."})
    write_note("source.md", "Links to [[target]].", index=idx)
    backlinks = idx.get_backlinks("target.md")
    assert "source.md" in backlinks


def test_delete_removes_backlinks(vault_factory):
    idx = vault_factory({
        "source.md": "Links to [[target]].",
        "target.md": "I am target.",
    })
    assert "source.md" in idx.get_backlinks("target.md")
    delete_note("source.md", trash=True, index=idx)
    assert "source.md" not in idx.get_backlinks("target.md")


def test_manage_tags_then_query(vault_factory):
    idx = vault_factory({"note.md": "---\ntags: []\n---\n"})
    manage_tags("note.md", add=["integration-test"], index=idx)
    results = query_notes(idx, tags=["integration-test"])
    assert any(r["path"] == "note.md" for r in results)
