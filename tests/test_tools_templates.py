from __future__ import annotations

from obsidian_mcp.tools.templates import create_from_template, list_templates

# ── create_from_template ──────────────────────────────────────────────────

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


# ── list_templates ────────────────────────────────────────────────────────

def test_list_templates(vault_factory):
    vault_factory({
        "Templates/daily.md": "Daily",
        "Templates/weekly.md": "Weekly",
        "Notes/not-a-template.md": "Note",
    })
    templates = list_templates()
    assert len(templates) == 2
    assert all("Templates/" in t for t in templates)


def test_list_templates_returns_empty_when_directory_is_absent(vault_factory):
    vault_factory({"Notes/note.md": "Note"})
    assert list_templates() == []
