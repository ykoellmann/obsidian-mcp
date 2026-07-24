from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from ..config import get_config
from ..domain.index import VaultIndex
from ..storage.filesystem import read_file, write_file_atomic
from .write import _check_write_permission

# Matches {{variable}} and {{variable:format}}
_VAR_RE = re.compile(r"\{\{(\w+)(?::([^}]*))?\}\}")


def create_from_template(
    template_path: str,
    output_path: str,
    variables: dict | None = None,
    index: VaultIndex | None = None,
) -> dict:
    """Render a template and write the result as a new note.

    Built-in variables: date, time, title, week, month, month_num, year, weekday.
    Custom variables in the `variables` dict override built-ins.
    Supports date format specs: {{date:YYYY-MM}} → formatted date string.
    Preserves {{unknown_var}} as-is if not provided.
    """
    cfg = get_config()
    _check_write_permission(output_path)

    raw_template = read_file(cfg.vault_path, template_path)

    now = datetime.now()
    today = date.today()
    iso_cal = today.isocalendar()

    built_in: dict[str, str] = {
        "date": today.isoformat(),
        "time": now.strftime("%H:%M"),
        "title": Path(output_path).stem,
        "week": f"{iso_cal[0]}-W{iso_cal[1]:02d}",
        "month": today.strftime("%B"),
        "month_num": today.strftime("%m"),
        "year": str(today.year),
        "weekday": today.strftime("%A"),
    }

    merged = {**built_in, **(variables or {})}

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        fmt = m.group(2)
        if name not in merged:
            return m.group(0)  # keep unknown vars unchanged
        val = merged[name]
        if fmt:
            try:
                d = date.fromisoformat(val)
                # Convert strftime-style format (YYYY→%Y, MM→%m, DD→%d, etc.)
                fmt_mapped = (fmt
                    .replace("YYYY", "%Y").replace("YY", "%y")
                    .replace("MM", "%m").replace("DD", "%d")
                    .replace("HH", "%H").replace("mm", "%M")
                    .replace("ss", "%S"))
                val = d.strftime(fmt_mapped)
            except Exception:
                pass
        return val

    rendered = _VAR_RE.sub(_replace, raw_template)
    write_file_atomic(cfg.vault_path, output_path, rendered)

    if index is not None:
        index.update(output_path)

    return {
        "template": template_path,
        "output": output_path,
        "status": "created",
        "variables": merged,
    }


def list_templates() -> list[str]:
    """List all template files in the Templates/ folder."""
    cfg = get_config()
    templates_dir = cfg.vault_path / "Templates"
    if not templates_dir.exists():
        return []
    return sorted(
        str(p.relative_to(cfg.vault_path))
        for p in templates_dir.rglob("*.md")
    )
