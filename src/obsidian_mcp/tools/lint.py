"""Validate note frontmatter against the enum schema declared in the vault's
own _AI_INSTRUCTIONS.md, instead of a hardcoded/external schema config."""
from __future__ import annotations

import re

from ..config import get_config
from ..domain.index import VaultIndex
from .query import _load_note, get_vault_conventions

# A fenced code block, optionally tagged ```yaml.
_YAML_BLOCK_RE = re.compile(r"```(?:yaml)?\n(.*?)```", re.DOTALL)
# Heading whose text mentions "Frontmatter Schema" (any level, case-insensitive).
_SCHEMA_HEADING_RE = re.compile(r"^#{1,6}\s*.*Frontmatter Schema.*$", re.IGNORECASE | re.MULTILINE)
# `field: value` — only used once we already know the line is inside the schema block.
_FIELD_LINE_RE = re.compile(r"^([\w][\w-]*):\s*(.+)$")


def parse_frontmatter_schema(instructions: str) -> dict[str, list[str]]:
    """Extract enum fields from a vault's _AI_INSTRUCTIONS.md.

    Looks for a fenced code block after a heading mentioning "Frontmatter
    Schema"; falls back to the first fenced block in the document if no such
    heading exists. Only lines shaped like `field: v1 | v2 | v3` become
    enums — other lines (`tags: []`, `created: YYYY-MM-DD`, free-form
    prose) are intentionally skipped rather than treated as single-value
    enums, since they don't describe a closed set of allowed values.
    Returns {} (not an error) if nothing parseable is found — lint_schema
    then reports zero violations instead of guessing at a schema.
    """
    heading_m = _SCHEMA_HEADING_RE.search(instructions)
    block_m = _YAML_BLOCK_RE.search(instructions, heading_m.end()) if heading_m else None
    if not block_m:
        block_m = _YAML_BLOCK_RE.search(instructions)
    if not block_m:
        return {}

    schema: dict[str, list[str]] = {}
    for line in block_m.group(1).splitlines():
        m = _FIELD_LINE_RE.match(line.strip())
        if not m:
            continue
        field, value = m.group(1), m.group(2).strip()
        if "|" not in value:
            continue
        values = [v.strip() for v in value.split("|") if v.strip()]
        if values:
            schema[field] = values
    return schema


def lint_schema(index: VaultIndex) -> dict:
    """Validate every note's frontmatter against the enum fields declared in
    _AI_INSTRUCTIONS.md. Returns {schema, violations: [{path, field, found,
    expected_enum}]} — only the deviations, not a full vault dump.
    A note missing a schema field entirely is not a violation (that's a
    different concern); this only flags a *present* value outside the
    declared enum. If no schema section can be parsed, returns an empty
    schema/violations pair rather than guessing.
    """
    cfg = get_config()
    schema = parse_frontmatter_schema(get_vault_conventions())
    violations: list[dict] = []
    if not schema:
        return {"schema": schema, "violations": violations}

    for note_path in sorted(index.get_all_notes()):
        try:
            note = _load_note(cfg.vault_path, note_path)
        except Exception:
            continue
        for field, allowed in schema.items():
            if field not in note.frontmatter:
                continue
            found = note.frontmatter.get(field)
            if found is None:
                continue
            found_str = str(found)
            if found_str not in allowed:
                violations.append({
                    "path": note_path,
                    "field": field,
                    "found": found_str,
                    "expected_enum": allowed,
                })

    return {"schema": schema, "violations": violations}
