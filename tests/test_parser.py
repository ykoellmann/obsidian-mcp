from pathlib import Path

from obsidian_mcp.domain.parser import extract_wikilinks, parse_note

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"

# ── basic parsing ─────────────────────────────────────────────────────────


def test_parse_simple_note():
    raw = (FIXTURES / "simple.md").read_text()
    note = parse_note(raw, path="simple.md")
    assert note.path == "simple.md"
    assert note.frontmatter["title"] == "Simple Note"
    assert "foo" in note.tags
    assert "bar" in note.tags
    assert "inline-tag" in note.tags


def test_parse_broken_yaml_does_not_crash():
    raw = (FIXTURES / "broken_yaml.md").read_text()
    note = parse_note(raw, path="broken_yaml.md")
    assert note.frontmatter == {}
    assert any(wl.target == "Wikilink" for wl in note.wikilinks)


def test_parse_no_frontmatter():
    raw = (FIXTURES / "no_frontmatter.md").read_text()
    note = parse_note(raw, path="no_frontmatter.md")
    assert note.frontmatter == {}
    assert "tag-only" in note.tags


def test_wikilinks_all_forms():
    body = "[[Note]] [[Note|Alias]] [[Note#Heading]] [[Note#Heading|Alias]]"
    links = extract_wikilinks(body)
    assert len(links) == 4
    targets = [wl.target for wl in links]
    assert all(t == "Note" for t in targets)
    assert links[1].alias == "Alias"
    assert links[2].heading == "Heading"
    assert links[3].heading == "Heading"
    assert links[3].alias == "Alias"


def test_wikilinks_heading_note():
    raw = (FIXTURES / "headings.md").read_text()
    note = parse_note(raw, path="headings.md")
    heading_link = next((wl for wl in note.wikilinks if wl.heading == "Bar"), None)
    assert heading_link is not None
    assert heading_link.target == "Foo"

    alias_link = next((wl for wl in note.wikilinks if wl.alias == "Alias"), None)
    assert alias_link is not None
    assert alias_link.target == "Baz"


def test_tags_from_frontmatter_list():
    raw = "---\ntags: [alpha, beta]\n---\nBody"
    note = parse_note(raw)
    assert "alpha" in note.tags
    assert "beta" in note.tags


def test_tags_from_frontmatter_string():
    raw = "---\ntags: alpha, beta\n---\nBody"
    note = parse_note(raw)
    assert "alpha" in note.tags
    assert "beta" in note.tags


# ── aliases ───────────────────────────────────────────────────────────────

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


# ── block references ──────────────────────────────────────────────────────

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


# ── block links ───────────────────────────────────────────────────────────

def test_block_links_detected():
    raw = (FIXTURES / "with_blocks.md").read_text()
    note = parse_note(raw, path="with_blocks.md")
    assert any("task-block" in bl for bl in note.block_links)
    assert any("another" in bl for bl in note.block_links)


def test_block_links_format():
    note = parse_note("See [[MyNote^my-block]] for details.")
    assert "MyNote^my-block" in note.block_links


# ── callouts ──────────────────────────────────────────────────────────────

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


# ── tasks ─────────────────────────────────────────────────────────────────

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


# ── Tasks-plugin emoji markers ───────────────────────────────────────────

def test_task_due_date_extracted():
    note = parse_note("- [ ] Buy milk 📅 2026-08-10")
    task = note.tasks[0]
    assert task.text == "Buy milk"
    assert task.due == "2026-08-10"


def test_task_done_date_extracted():
    note = parse_note("- [x] Pay rent ✅ 2026-08-01")
    task = note.tasks[0]
    assert task.text == "Pay rent"
    assert task.done_date == "2026-08-01"


def test_task_priority_high():
    note = parse_note("- [ ] Urgent fix ⏫")
    assert note.tasks[0].text == "Urgent fix"
    assert note.tasks[0].priority == "high"


def test_task_priority_medium():
    note = parse_note("- [ ] Somewhat urgent 🔼")
    assert note.tasks[0].priority == "medium"


def test_task_priority_low():
    note = parse_note("- [ ] Whenever 🔽")
    assert note.tasks[0].priority == "low"


def test_task_recurrence_extracted():
    note = parse_note("- [ ] Weekly sync 🔁 every week")
    task = note.tasks[0]
    assert task.text == "Weekly sync"
    assert task.recurrence == "every week"


def test_task_recurrence_stops_before_next_marker():
    note = parse_note("- [ ] Weekly sync 🔁 every week 📅 2026-08-15")
    task = note.tasks[0]
    assert task.recurrence == "every week"
    assert task.due == "2026-08-15"


def test_task_combined_markers():
    note = parse_note("- [ ] Ship release ⏫ 📅 2026-08-05 🔁 every month")
    task = note.tasks[0]
    assert task.text == "Ship release"
    assert task.priority == "high"
    assert task.due == "2026-08-05"
    assert task.recurrence == "every month"


def test_task_without_markers_has_none_fields():
    note = parse_note("- [ ] Plain task")
    task = note.tasks[0]
    assert task.due is None
    assert task.recurrence is None
    assert task.priority is None
    assert task.done_date is None


# ── wikilinks not broken by new regexes ──────────────────────────────────

def test_wikilinks_unaffected_by_block_syntax():
    body = "[[Normal]] [[Note#Heading]] [[Note|Alias]] [[Note^block]]"
    note = parse_note(body)
    wl_targets = [wl.target for wl in note.wikilinks]
    assert "Normal" in wl_targets
    assert "Note" in wl_targets
    # block link should NOT appear as wikilink target
    assert not any("^" in t for t in wl_targets)
