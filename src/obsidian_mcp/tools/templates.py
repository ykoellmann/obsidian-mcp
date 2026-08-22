from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from ..config import get_config
from ..domain.index import VaultIndex
from ..storage.filesystem import VaultStorage

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
    storage = VaultStorage.from_config(cfg)
    if not output_path.lower().endswith(".md"):
        raise ValueError("Template output paths must end in .md")
    output = storage.resolve_write(output_path)
    output_path = output.relative

    raw_template = storage.read_text(template_path)

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
    storage.write_text_atomic(output_path, rendered)

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
    storage = VaultStorage.from_config(cfg)
    try:
        return sorted(
            p.relative
            for p in storage.list_files("Templates")
            if p.relative.lower().endswith(".md")
        )
    except (FileNotFoundError, NotADirectoryError):
        return []
