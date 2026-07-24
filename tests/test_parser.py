from pathlib import Path

from second_brain_mcp.domain.parser import extract_wikilinks, parse_note

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"


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
