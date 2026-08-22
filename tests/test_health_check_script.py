from __future__ import annotations

import importlib.util
from pathlib import Path

import obsidian_mcp.config as cfg_mod

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "health_check.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("health_check_script", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INSTRUCTIONS = """\
## Frontmatter Schema
```yaml
status: inbox | active | done | archived
```
"""


def test_health_check_writes_report_on_violation(tmp_path, monkeypatch):
    (tmp_path / "_AI_INSTRUCTIONS.md").write_text(_INSTRUCTIONS, encoding="utf-8")
    (tmp_path / "note.md").write_text("---\nstatus: in-progress\n---\nBody", encoding="utf-8")

    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("HEALTH_CHECK_INBOX", "00-Inbox")
    cfg_mod._config = None

    module = _load_script()
    exit_code = module.main()

    assert exit_code == 0
    reports = list((tmp_path / "00-Inbox").glob("health-check-*.md"))
    assert len(reports) == 1
    content = reports[0].read_text(encoding="utf-8")
    assert "note.md" in content
    assert "in-progress" in content


def test_health_check_silent_when_clean(tmp_path, monkeypatch):
    (tmp_path / "_AI_INSTRUCTIONS.md").write_text(_INSTRUCTIONS, encoding="utf-8")
    (tmp_path / "note.md").write_text("---\nstatus: active\n---\nBody", encoding="utf-8")

    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("HEALTH_CHECK_INBOX", "00-Inbox")
    cfg_mod._config = None

    module = _load_script()
    exit_code = module.main()

    assert exit_code == 0
    assert not (tmp_path / "00-Inbox").exists()
