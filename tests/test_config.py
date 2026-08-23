"""Tests for GitHub OAuth config validation (obsidian_mcp/config.py)."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest

from obsidian_mcp.config import Config, ConfigError
from obsidian_mcp.storage.locking import acquire_lock


def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("OBSIDIAN_MCP_API_KEY", raising=False)
    monkeypatch.delenv("OAUTH_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("OAUTH_GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OAUTH_GITHUB_ALLOWED_LOGINS", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)


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
    assert cfg.oauth_github_allowed_logins == ("octocat", "some-other-user")
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


def test_security_path_defaults_and_lock_outside_vault(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    cfg = Config()
    assert cfg.deny_read_paths == (".obsidian/", ".trash/")
    assert cfg.deny_write_paths == (".obsidian/", ".trash/", "_AI_INSTRUCTIONS.md")
    assert cfg.allow_permanent_delete is False
    assert tmp_path not in cfg.lock_path.parents
    assert cfg.audit_log_path == cfg.lock_path / "audit.jsonl"
    assert tmp_path not in cfg.audit_log_path.parents
    assert cfg.enable_move is False
    assert cfg.enable_folder_rename is False
    assert cfg.enable_bulk_replace is False
    assert cfg.enable_delete is False
    assert cfg.require_write_preconditions is False
    assert cfg.index_reconcile_interval == 900
    assert cfg.watcher_debounce_ms == 100


def test_native_default_lock_path_is_external_and_usable(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("LOCK_PATH", raising=False)
    monkeypatch.delenv("FASTMCP_HOME", raising=False)
    cfg = Config()

    assert cfg.lock_path == (Path(tempfile.gettempdir()) / "obsidian-mcp-locks").resolve()
    assert cfg.lock_path != cfg.vault_path
    lock = acquire_lock("native-test", lock_path=cfg.lock_path)
    lock.release()


def test_security_path_lists_normalize_separators(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("WRITE_PATHS", "AI\\Memory/")
    monkeypatch.setenv("READ_PATHS", "AI\\Memory/")
    monkeypatch.setenv("DENY_READ_PATHS", "private/")
    cfg = Config()
    assert cfg.write_paths == ("AI/Memory/",)
    assert cfg.read_paths == ("AI/Memory/",)
    assert cfg.deny_read_paths == ("private/",)
    with pytest.raises(AttributeError):
        cfg.write_paths.append("other")
    with pytest.raises(AttributeError):
        cfg.read_only = True
    with pytest.raises(AttributeError):
        cfg.write_paths += ("other",)
    with pytest.raises(AttributeError):
        cfg.write_paths *= 2
    assert cfg.write_paths == ("AI/Memory/",)


def test_config_supports_copy_without_losing_immutability(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    cfg = Config()

    copied = copy.copy(cfg)

    assert copied == cfg
    with pytest.raises(AttributeError):
        copied.read_only = True


@pytest.mark.parametrize("value", ["0", "-1", "not-an-int"])
def test_max_attachment_bytes_must_be_positive(tmp_path, monkeypatch, value):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_ATTACHMENT_BYTES", value)
    with pytest.raises(ConfigError, match="MAX_ATTACHMENT_BYTES"):
        Config()


def test_max_attachment_bytes_defaults_to_25_mib(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    assert Config().max_attachment_bytes == 25 * 1024 * 1024


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("INDEX_RECONCILE_INTERVAL", "0"),
        ("INDEX_RECONCILE_INTERVAL", "invalid"),
        ("INDEX_RECONCILE_INTERVAL", "nan"),
        ("INDEX_RECONCILE_INTERVAL", "inf"),
        ("WATCHER_DEBOUNCE_MS", "-1"),
        ("WATCHER_DEBOUNCE_MS", "invalid"),
    ],
)
def test_sync_timing_configuration_is_validated(tmp_path, monkeypatch, name, value):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        Config()


@pytest.mark.parametrize(
    "setting",
    ["READ_PATHS", "WRITE_PATHS", "DENY_READ_PATHS", "DENY_WRITE_PATHS", "EXCLUDE_PATHS"],
)
def test_security_path_lists_reject_escape(tmp_path, monkeypatch, setting):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(setting, "../outside")
    with pytest.raises(ConfigError):
        Config()


def test_audit_log_path_must_be_outside_vault(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    with pytest.raises(ConfigError, match="AUDIT_LOG_PATH"):
        Config()
