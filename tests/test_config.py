"""Tests for GitHub OAuth config validation (obsidian_mcp/config.py)."""
from __future__ import annotations

import pytest

from obsidian_mcp.config import Config, ConfigError


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
