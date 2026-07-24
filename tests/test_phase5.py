from __future__ import annotations

from obsidian_mcp.tools.query import query_notes
from obsidian_mcp.tools.read import get_note_outline, render_note, search_notes

# ── query_notes ───────────────────────────────────────────────────────────────

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


# ── render_note ───────────────────────────────────────────────────────────────

def test_render_note_no_embeds(vault_factory):
    vault_factory({"note.md": "# Hello\nNo embeds here."})
    result = render_note("note.md")
    assert "Hello" in result
    assert "No embeds here" in result


def test_render_note_resolves_embed(vault_factory):
    vault_factory({
        "main.md": "Intro\n![[embed]]\nOutro",
        "embed.md": "## Embedded Content\nThis is embedded.",
    })
    result = render_note("main.md")
    assert "Intro" in result
    assert "Embedded Content" in result
    assert "This is embedded" in result
    assert "Outro" in result


def test_render_note_embed_with_heading(vault_factory):
    vault_factory({
        "main.md": "![[source#Wichtig]]",
        "source.md": "## Unwichtig\nIgnore this.\n## Wichtig\nThis matters.\n## More\nSkip.",
    })
    result = render_note("main.md")
    assert "This matters" in result
    assert "Ignore this" not in result


def test_render_note_missing_embed_kept(vault_factory):
    vault_factory({"main.md": "![[nonexistent]]"})
    result = render_note("main.md")
    assert "![[nonexistent]]" in result


def test_render_note_depth_zero_no_resolve(vault_factory):
    vault_factory({
        "main.md": "![[embed]]",
        "embed.md": "SHOULD NOT APPEAR",
    })
    result = render_note("main.md", depth=0)
    assert "SHOULD NOT APPEAR" not in result
    assert "![[embed]]" in result


# ── get_note_outline ──────────────────────────────────────────────────────────

def test_get_note_outline_headings(vault_factory):
    vault_factory({"note.md": "# Title\n## Section A\ntext\n### Subsection\n## Section B\n"})
    outline = get_note_outline("note.md")
    headings = outline["headings"]
    assert len(headings) == 4
    assert headings[0]["level"] == 1
    assert headings[1]["text"] == "Section A"
    assert headings[2]["level"] == 3


def test_get_note_outline_block_refs(vault_factory):
    vault_factory({"note.md": "Important insight. ^key-insight\nOther text."})
    outline = get_note_outline("note.md")
    assert any(b["block_id"] == "key-insight" for b in outline["block_refs"])


def test_get_note_outline_stats(vault_factory):
    vault_factory({"note.md": "---\ntitle: T\n---\nOne two three"})
    outline = get_note_outline("note.md")
    assert outline["word_count"] > 0
    assert outline["line_count"] > 0
    assert "title" in outline["frontmatter_keys"]


# ── fuzzy search ──────────────────────────────────────────────────────────────

def test_search_fuzzy_finds_typo(vault_factory):
    vault_factory({"note.md": "Python is a great programming language."})
    results = search_notes("Pythn", mode="fuzzy")
    assert len(results) == 1
    assert results[0]["path"] == "note.md"


def test_search_fuzzy_no_false_positive(vault_factory):
    vault_factory({"note.md": "Completely unrelated text."})
    results = search_notes("quantum", mode="fuzzy")
    assert len(results) == 0


def test_search_fuzzy_multi_word(vault_factory):
    vault_factory({"note.md": "machine learning is powerful."})
    results = search_notes("machne lerning", mode="fuzzy")
    assert len(results) == 1
