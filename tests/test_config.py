"""Tests for GitHub OAuth config validation (obsidian_mcp/config.py)."""
from __future__ import annotations

import json

import pytest

from obsidian_mcp.config import (
    Config,
    ConfigError,
    load_vaults_file,
    reset_current_vault,
    set_current_vault,
)


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("OBSIDIAN_MCP_API_KEY", raising=False)
    monkeypatch.delenv("OAUTH_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("OAUTH_GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OAUTH_GITHUB_ALLOWED_LOGINS", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("VAULTS_CONFIG", raising=False)


def test_oauth_requires_both_client_id_and_secret(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "abc")

    with pytest.raises(ConfigError, match="CLIENT_ID and OAUTH_GITHUB_CLIENT_SECRET"):
        Config()


def test_oauth_requires_allowed_logins(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "abc")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")

    with pytest.raises(ConfigError, match="OAUTH_GITHUB_ALLOWED_LOGINS"):
        Config()


def test_oauth_requires_public_base_url(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "abc")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OAUTH_GITHUB_ALLOWED_LOGINS", "octocat")

    with pytest.raises(ConfigError, match="PUBLIC_BASE_URL"):
        Config()


def test_oauth_only_is_valid_without_api_key(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRANSPORT", "sse")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "abc")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OAUTH_GITHUB_ALLOWED_LOGINS", "octocat, some-other-user")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com/")

    cfg = Config()
    assert cfg.oauth_github_allowed_logins == ["octocat", "some-other-user"]
    assert cfg.public_base_url == "https://example.com"


def test_api_key_and_oauth_can_both_be_configured(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRANSPORT", "sse")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "abc")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OAUTH_GITHUB_ALLOWED_LOGINS", "octocat")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")

    cfg = Config()
    assert cfg.api_key == "test-key"
    assert cfg.oauth_github_client_id == "abc"


def test_network_transport_requires_api_key_or_oauth(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRANSPORT", "sse")

    with pytest.raises(ConfigError, match="API_KEY or GitHub OAuth"):
        Config()


# ── Multi-vault (VAULTS_CONFIG) ─────────────────────────────────────────────

def _write_vaults_config(tmp_path, vault_a, vault_b, extra_identities=None):
    vault_a.mkdir(exist_ok=True)
    vault_b.mkdir(exist_ok=True)
    data = {
        "vaults": {
            "private": {"path": str(vault_a), "exclude_paths": ["private"]},
            "monari": {"path": str(vault_b), "write_paths": ["02-Areas/monari/"]},
        },
        "identities": [
            {"type": "api_key", "value": "sk-private", "vaults": ["private"]},
            {"type": "api_key", "value": "sk-monari", "vaults": ["monari"]},
            *(extra_identities or []),
        ],
    }
    config_path = tmp_path / "vaults.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return config_path


def test_load_vaults_file_missing_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_vaults_file(str(tmp_path / "nonexistent.json"))


def test_load_vaults_file_invalid_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_vaults_file(str(bad))


def test_load_vaults_file_no_vaults_raises(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"identities": []}), encoding="utf-8")
    with pytest.raises(ConfigError, match="at least one vault"):
        load_vaults_file(str(empty))


def test_load_vaults_file_no_identities_raises(tmp_path):
    vault_dir = tmp_path / "v"
    vault_dir.mkdir()
    cfg_file = tmp_path / "vaults.json"
    cfg_file.write_text(json.dumps({"vaults": {"a": {"path": str(vault_dir)}}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="at least one identity"):
        load_vaults_file(str(cfg_file))


def test_load_vaults_file_identity_unknown_vault_raises(tmp_path):
    vault_dir = tmp_path / "v"
    vault_dir.mkdir()
    cfg_file = tmp_path / "vaults.json"
    cfg_file.write_text(json.dumps({
        "vaults": {"a": {"path": str(vault_dir)}},
        "identities": [{"type": "api_key", "value": "k", "vaults": ["nonexistent"]}],
    }), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown vault"):
        load_vaults_file(str(cfg_file))


def test_load_vaults_file_single_vault_gets_implicit_default(tmp_path):
    vault_dir = tmp_path / "v"
    vault_dir.mkdir()
    cfg_file = tmp_path / "vaults.json"
    cfg_file.write_text(json.dumps({
        "vaults": {"a": {"path": str(vault_dir)}},
        "identities": [{"type": "api_key", "value": "k", "vaults": ["a"]}],
    }), encoding="utf-8")
    _vaults, identities = load_vaults_file(str(cfg_file))
    assert identities[0].default == "a"


def test_load_vaults_file_github_login_lowercased(tmp_path):
    vault_dir = tmp_path / "v"
    vault_dir.mkdir()
    cfg_file = tmp_path / "vaults.json"
    cfg_file.write_text(json.dumps({
        "vaults": {"a": {"path": str(vault_dir)}},
        "identities": [{"type": "github_login", "value": "SomeUser", "vaults": ["a"]}],
    }), encoding="utf-8")
    _vaults, identities = load_vaults_file(str(cfg_file))
    assert identities[0].value == "someuser"


def test_config_multi_vault_mode_loads_vaults(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    config_path = _write_vaults_config(tmp_path, tmp_path / "a", tmp_path / "b")
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")

    cfg = Config()
    assert cfg.multi_vault is True
    assert set(cfg.vaults) == {"private", "monari"}
    assert len(cfg.identities) == 2


def test_config_multi_vault_resolves_vault_path_from_context(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    vault_a, vault_b = tmp_path / "a", tmp_path / "b"
    config_path = _write_vaults_config(tmp_path, vault_a, vault_b)
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")

    cfg = Config()
    # No context set -> default vault (first one declared).
    assert cfg.vault_path == vault_a.resolve()

    token = set_current_vault("monari")
    try:
        assert cfg.vault_path == vault_b.resolve()
        assert cfg.write_paths == ["02-Areas/monari/"]
    finally:
        reset_current_vault(token)

    # Context reset -> back to default.
    assert cfg.vault_path == vault_a.resolve()


def test_config_multi_vault_unknown_vault_in_context_raises(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    config_path = _write_vaults_config(tmp_path, tmp_path / "a", tmp_path / "b")
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")

    cfg = Config()
    token = set_current_vault("nonexistent")
    try:
        with pytest.raises(ConfigError, match="Unknown vault"):
            _ = cfg.vault_path
    finally:
        reset_current_vault(token)


def test_config_multi_vault_read_only_per_vault_overrides_global(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    vault_a, vault_b = tmp_path / "a", tmp_path / "b"
    vault_a.mkdir()
    vault_b.mkdir()
    data = {
        "vaults": {
            "a": {"path": str(vault_a)},
            "b": {"path": str(vault_b), "read_only": True},
        },
        "identities": [{"type": "api_key", "value": "k", "vaults": ["a", "b"], "default": "a"}],
    }
    config_path = tmp_path / "vaults.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")

    cfg = Config()
    assert cfg.read_only is False  # global default, vault "a" has no override

    token = set_current_vault("b")
    try:
        assert cfg.read_only is True
    finally:
        reset_current_vault(token)


def test_config_multi_vault_oauth_allowed_logins_from_identities(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    config_path = _write_vaults_config(
        tmp_path, tmp_path / "a", tmp_path / "b",
        extra_identities=[{"type": "github_login", "value": "OctoCat", "vaults": ["private"]}],
    )
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "abc")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")

    cfg = Config()
    assert cfg.oauth_github_allowed_logins == ["octocat"]


def test_config_multi_vault_missing_file_raises(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("VAULTS_CONFIG", str(tmp_path / "nonexistent.json"))
    monkeypatch.setenv("TRANSPORT", "sse")
    monkeypatch.setenv("API_KEY", "x")

    with pytest.raises(ConfigError, match="not found"):
        Config()


@pytest.mark.parametrize("nested", [False, True])
def test_load_vaults_file_rejects_overlapping_roots(tmp_path, nested):
    vault_a = tmp_path / "a"
    vault_a.mkdir()
    vault_b = vault_a / "nested" if nested else vault_a
    if nested:
        vault_b.mkdir()
    cfg_file = tmp_path / "vaults.json"
    cfg_file.write_text(json.dumps({
        "vaults": {
            "a": {"path": str(vault_a)},
            "b": {"path": str(vault_b)},
        },
        "identities": [{"type": "api_key", "value": "k", "vaults": ["a"]}],
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match="Vault paths must not overlap"):
        load_vaults_file(str(cfg_file))


def test_config_multi_vault_rejects_stdio(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    config_path = _write_vaults_config(tmp_path, tmp_path / "a", tmp_path / "b")
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "stdio")

    with pytest.raises(ConfigError, match="TRANSPORT=stdio cannot identify"):
        Config()
