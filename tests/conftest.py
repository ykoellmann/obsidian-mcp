"""Shared pytest fixtures for all test modules."""
from __future__ import annotations

from pathlib import Path

import pytest

import second_brain_mcp.config as cfg_mod
from second_brain_mcp.domain.index import VaultIndex


@pytest.fixture
def vault_factory(tmp_path: Path, monkeypatch):
    """Factory fixture that creates a temp vault, sets VAULT_PATH, and returns an index.

    Usage::

        def test_something(tmp_path, vault_factory):
            idx = vault_factory({"note.md": "# Hello"})
            assert (tmp_path / "note.md").exists()
    """
    def _make(files: dict[str, str] | None = None) -> VaultIndex:
        for rel, content in (files or {}).items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        monkeypatch.setenv("VAULT_PATH", str(tmp_path))
        cfg_mod._config = None
        idx = VaultIndex(tmp_path)
        idx.build()
        return idx

    return _make
