from __future__ import annotations

from obsidian_mcp.tools.lint import lint_schema, parse_frontmatter_schema

_INSTRUCTIONS = """\
## Frontmatter Schema
```yaml
status: inbox | active | done | archived
type: project | area | resource | journal
tags: []
created: YYYY-MM-DD
```
"""


# ── parse_frontmatter_schema ────────────────────────────────────────────────

def test_parse_schema_extracts_enum_fields():
    schema = parse_frontmatter_schema(_INSTRUCTIONS)
    assert schema["status"] == ["inbox", "active", "done", "archived"]
    assert schema["type"] == ["project", "area", "resource", "journal"]


def test_parse_schema_skips_non_enum_lines():
    schema = parse_frontmatter_schema(_INSTRUCTIONS)
    assert "tags" not in schema
    assert "created" not in schema


def test_parse_schema_no_heading_falls_back_to_first_block():
    raw = "Some prose.\n```yaml\nstatus: a | b\n```\n"
    schema = parse_frontmatter_schema(raw)
    assert schema["status"] == ["a", "b"]


def test_parse_schema_no_block_returns_empty():
    assert parse_frontmatter_schema("Just prose, no code block.") == {}


def test_parse_schema_empty_string_returns_empty():
    assert parse_frontmatter_schema("") == {}


# ── lint_schema ──────────────────────────────────────────────────────────

def test_lint_schema_finds_violation(vault_factory):
    idx = vault_factory({
        "_AI_INSTRUCTIONS.md": _INSTRUCTIONS,
        "a.md": "---\nstatus: in-progress\n---\nBody",
        "b.md": "---\nstatus: active\n---\nBody",
    })
    result = lint_schema(idx)
    assert result["schema"]["status"] == ["inbox", "active", "done", "archived"]
    paths = [v["path"] for v in result["violations"]]
    assert paths == ["a.md"]
    assert result["violations"][0]["found"] == "in-progress"
    assert result["violations"][0]["field"] == "status"


def test_lint_schema_missing_field_not_a_violation(vault_factory):
    idx = vault_factory({
        "_AI_INSTRUCTIONS.md": _INSTRUCTIONS,
        "a.md": "---\ntitle: No status here\n---\nBody",
    })
    result = lint_schema(idx)
    assert result["violations"] == []


def test_lint_schema_no_instructions_file_returns_empty(vault_factory):
    idx = vault_factory({"a.md": "---\nstatus: whatever\n---\nBody"})
    result = lint_schema(idx)
    assert result["schema"] == {}
    assert result["violations"] == []


def test_lint_schema_clean_vault_no_violations(vault_factory):
    idx = vault_factory({
        "_AI_INSTRUCTIONS.md": _INSTRUCTIONS,
        "a.md": "---\nstatus: active\ntype: project\n---\nBody",
    })
    result = lint_schema(idx)
    assert result["violations"] == []
