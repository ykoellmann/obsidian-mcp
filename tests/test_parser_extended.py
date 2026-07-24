from pathlib import Path

from obsidian_mcp.domain.parser import (
    parse_note,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"


# --- Aliases ---

def test_aliases_from_frontmatter_list():
    note = parse_note("---\naliases: [Foo, Bar]\n---\nBody")
    assert note.aliases == ["Foo", "Bar"]


def test_aliases_from_frontmatter_string():
    note = parse_note("---\naliases: Foo, Bar\n---\nBody")
    assert "Foo" in note.aliases
    assert "Bar" in note.aliases


def test_aliases_empty_when_missing():
    note = parse_note("---\ntitle: X\n---\nBody")
    assert note.aliases == []


def test_aliases_in_fixture():
    raw = (FIXTURES / "with_blocks.md").read_text()
    note = parse_note(raw, path="with_blocks.md")
    assert "BlockTest" in note.aliases
    assert "block-test-alias" in note.aliases


# --- Block References ---

def test_block_refs_detected():
    raw = (FIXTURES / "with_blocks.md").read_text()
    note = parse_note(raw, path="with_blocks.md")
    ids = [r.block_id for r in note.block_refs]
    assert "important-block" in ids
    assert "section-ref" in ids


def test_block_ref_has_text_and_line():
    raw = (FIXTURES / "with_blocks.md").read_text()
    note = parse_note(raw, path="with_blocks.md")
    ref = next(r for r in note.block_refs if r.block_id == "important-block")
    assert "block ref" in ref.text
    assert ref.line > 0


def test_block_refs_empty_when_none():
    note = parse_note("---\ntitle: X\n---\nNo refs here.")
    assert note.block_refs == []


# --- Block Links ---

def test_block_links_detected():
    raw = (FIXTURES / "with_blocks.md").read_text()
    note = parse_note(raw, path="with_blocks.md")
    assert any("task-block" in bl for bl in note.block_links)
    assert any("another" in bl for bl in note.block_links)


def test_block_links_format():
    note = parse_note("See [[MyNote^my-block]] for details.")
    assert "MyNote^my-block" in note.block_links


# --- Callouts ---

def test_callouts_detected():
    raw = (FIXTURES / "with_callouts.md").read_text()
    note = parse_note(raw, path="with_callouts.md")
    types = [c.type for c in note.callouts]
    assert "NOTE" in types
    assert "WARNING" in types
    assert "TIP" in types
    assert "IMPORTANT" in types


def test_callout_title_and_body():
    raw = (FIXTURES / "with_callouts.md").read_text()
    note = parse_note(raw)
    note_callout = next(c for c in note.callouts if c.type == "NOTE")
    assert note_callout.title == "Eine Anmerkung"
    assert "Inhalt" in note_callout.body


def test_callout_multiline_body():
    raw = (FIXTURES / "with_callouts.md").read_text()
    note = parse_note(raw)
    note_callout = next(c for c in note.callouts if c.type == "NOTE")
    assert "Zweite Zeile" in note_callout.body


def test_callouts_empty_when_none():
    note = parse_note("Normal text, no callouts.")
    assert note.callouts == []


# --- Tasks ---

def test_tasks_detected():
    raw = (FIXTURES / "with_tasks.md").read_text()
    note = parse_note(raw, path="with_tasks.md")
    assert len(note.tasks) == 5


def test_task_done_status():
    raw = (FIXTURES / "with_tasks.md").read_text()
    note = parse_note(raw)
    open_tasks = [t for t in note.tasks if not t.done]
    done_tasks = [t for t in note.tasks if t.done]
    assert len(open_tasks) == 3
    assert len(done_tasks) == 2


def test_task_uppercase_x_is_done():
    note = parse_note("- [X] Done with caps")
    assert note.tasks[0].done is True


def test_task_has_line_number():
    note = parse_note("Line 1\n- [ ] My task\nLine 3")
    assert note.tasks[0].line == 2


def test_task_text_stripped():
    note = parse_note("- [ ] Buy milk")
    assert note.tasks[0].text == "Buy milk"


def test_tasks_empty_when_none():
    note = parse_note("No tasks here, just text.")
    assert note.tasks == []


# --- Wikilinks not broken by new regexes ---

def test_wikilinks_unaffected_by_block_syntax():
    body = "[[Normal]] [[Note#Heading]] [[Note|Alias]] [[Note^block]]"
    note = parse_note(body)
    wl_targets = [wl.target for wl in note.wikilinks]
    assert "Normal" in wl_targets
    assert "Note" in wl_targets
    # block link should NOT appear as wikilink target
    assert not any("^" in t for t in wl_targets)
