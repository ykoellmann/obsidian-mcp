from __future__ import annotations

from datetime import date

import yaml

from obsidian_mcp.tools.query import get_periodic_note, list_all_tags
from obsidian_mcp.tools.templates import create_from_template, list_templates
from obsidian_mcp.tools.write import manage_tags

# ── manage_tags ───────────────────────────────────────────────────────────────

def test_manage_tags_add(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [existing]\n---\nBody"})
    manage_tags("note.md", add=["new-tag"])
    content = (tmp_path / "note.md").read_text()
    assert "existing" in content
    assert "new-tag" in content


def test_manage_tags_remove_from_frontmatter(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [foo, bar]\n---\nBody"})
    manage_tags("note.md", remove=["foo"])
    content = (tmp_path / "note.md").read_text()
    assert "foo" not in content
    assert "bar" in content


def test_manage_tags_remove_inline(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [foo]\n---\nSome text #foo in body"})
    manage_tags("note.md", remove=["foo"])
    content = (tmp_path / "note.md").read_text()
    assert "#foo" not in content
    assert "Some text" in content


def test_manage_tags_no_duplicates(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntags: [existing]\n---\n"})
    manage_tags("note.md", add=["existing"])
    fm_text = (tmp_path / "note.md").read_text().split("---")[1]
    fm = yaml.safe_load(fm_text)
    assert fm["tags"].count("existing") == 1


def test_manage_tags_creates_tags_key(tmp_path, vault_factory):
    vault_factory({"note.md": "---\ntitle: Test\n---\nBody"})
    manage_tags("note.md", add=["new"])
    content = (tmp_path / "note.md").read_text()
    assert "new" in content


def test_manage_tags_updates_index(vault_factory):
    idx = vault_factory({"note.md": "---\ntags: []\n---\n"})
    manage_tags("note.md", add=["mytag"], index=idx)
    assert "note.md" in idx.get_notes_by_tag("mytag")


# ── list_all_tags ─────────────────────────────────────────────────────────────

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


# ── get_periodic_note ─────────────────────────────────────────────────────────

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
    import pytest
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


# ── create_from_template ──────────────────────────────────────────────────────

def test_template_basic(tmp_path, vault_factory):
    vault_factory({"Templates/basic.md": "---\ntitle: {{title}}\ndate: {{date}}\n---\n# {{title}}\n"})
    result = create_from_template("Templates/basic.md", "Notes/MyNote.md")
    assert result["status"] == "created"
    content = (tmp_path / "Notes" / "MyNote.md").read_text()
    assert "MyNote" in content
    assert "{{title}}" not in content
    assert "{{date}}" not in content


def test_template_custom_variables(tmp_path, vault_factory):
    vault_factory({"Templates/proj.md": "# {{project_name}}\nOwner: {{owner}}\n"})
    create_from_template(
        "Templates/proj.md", "Projects/Alpha.md",
        variables={"project_name": "Alpha", "owner": "Yannik"},
    )
    content = (tmp_path / "Projects" / "Alpha.md").read_text()
    assert "Alpha" in content
    assert "Yannik" in content


def test_template_unknown_var_preserved(tmp_path, vault_factory):
    vault_factory({"Templates/t.md": "Hello {{unknown_var}}"})
    create_from_template("Templates/t.md", "out.md")
    content = (tmp_path / "out.md").read_text()
    assert "{{unknown_var}}" in content


def test_template_date_format(tmp_path, vault_factory):
    vault_factory({"Templates/t.md": "Month: {{date:YYYY-MM}}"})
    create_from_template("Templates/t.md", "out.md")
    content = (tmp_path / "out.md").read_text()
    assert "{{date:YYYY-MM}}" not in content
    assert "Month:" in content


def test_template_creates_parent_dirs(tmp_path, vault_factory):
    vault_factory({"Templates/t.md": "Content"})
    create_from_template("Templates/t.md", "Deep/Nested/Folder/note.md")
    assert (tmp_path / "Deep" / "Nested" / "Folder" / "note.md").exists()


def test_list_templates(vault_factory):
    vault_factory({
        "Templates/daily.md": "Daily",
        "Templates/weekly.md": "Weekly",
        "Notes/not-a-template.md": "Note",
    })
    templates = list_templates()
    assert len(templates) == 2
    assert all("Templates/" in t for t in templates)
