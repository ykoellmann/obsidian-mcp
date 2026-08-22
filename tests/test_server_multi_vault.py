"""Tests for multi-vault wiring in server.py: multi-key API auth,
identity resolution, and VaultResolutionMiddleware."""
from __future__ import annotations

import json

import pytest
from fastmcp.server.auth import AccessToken
from fastmcp.server.middleware import MiddlewareContext

from obsidian_mcp import server as server_mod
from obsidian_mcp.config import Config
from obsidian_mcp.server import (
    VaultResolutionMiddleware,
    _APIKeyAuthProvider,
    _identities_from_env,
    _resolve_identity,
)


def _write_vaults_config(tmp_path, extra_identities=None):
    vault_a, vault_b = tmp_path / "a", tmp_path / "b"
    vault_a.mkdir()
    vault_b.mkdir()
    data = {
        "vaults": {
            "private": {"path": str(vault_a)},
            "monari": {"path": str(vault_b)},
        },
        "identities": [
            {"type": "api_key", "value": "sk-private", "vaults": ["private"]},
            {"type": "github_login", "value": "yannikkoellmann", "vaults": ["monari"]},
            *(extra_identities or []),
        ],
    }
    config_path = tmp_path / "vaults.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return config_path


# ── _identities_from_env ─────────────────────────────────────────────────

def test_identities_from_env_legacy_single_key(monkeypatch):
    monkeypatch.delenv("VAULTS_CONFIG", raising=False)
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("OAUTH_GITHUB_ALLOWED_LOGINS", "OctoCat")

    api_keys, allowed_logins = _identities_from_env()

    assert api_keys == ["test-key"]
    assert allowed_logins == ["octocat"]


def test_identities_from_env_vaults_config(tmp_path, monkeypatch):
    config_path = _write_vaults_config(tmp_path)
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))

    api_keys, allowed_logins = _identities_from_env()

    assert api_keys == ["sk-private"]
    assert allowed_logins == ["yannikkoellmann"]


def test_identities_from_env_bad_vaults_config_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULTS_CONFIG", str(tmp_path / "nonexistent.json"))

    assert _identities_from_env() == ([], [])


# ── _APIKeyAuthProvider (multi-key) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_key_provider_accepts_any_configured_key():
    provider = _APIKeyAuthProvider(["key-a", "key-b"])

    token_a = await provider.verify_token("key-a")
    token_b = await provider.verify_token("key-b")

    assert token_a is not None and token_a.client_id == "key-a"
    assert token_b is not None and token_b.client_id == "key-b"


@pytest.mark.asyncio
async def test_api_key_provider_rejects_unknown_key():
    provider = _APIKeyAuthProvider(["key-a"])
    assert await provider.verify_token("wrong") is None


# ── _resolve_identity ────────────────────────────────────────────────────

def _cfg_with_vaults(tmp_path, monkeypatch, extra_identities=None):
    config_path = _write_vaults_config(tmp_path, extra_identities=extra_identities)
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")
    monkeypatch.delenv("VAULT_PATH", raising=False)
    return Config()


def test_resolve_identity_by_api_key(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="sk-private", client_id="sk-private", scopes=[]),
    )

    identity = _resolve_identity(cfg)
    assert identity.type == "api_key"
    assert identity.default == "private"


def test_resolve_identity_by_github_login(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="x", client_id="oauth", scopes=[], claims={"login": "YannikKoellmann"}),
    )

    identity = _resolve_identity(cfg)
    assert identity.type == "github_login"
    assert identity.default == "monari"


def test_resolve_identity_no_token_raises(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(tmp_path, monkeypatch)
    monkeypatch.setattr(server_mod, "get_access_token", lambda: None)

    with pytest.raises(PermissionError, match="No authenticated identity"):
        _resolve_identity(cfg)


def test_resolve_identity_unknown_identity_raises(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="x", client_id="not-a-configured-key", scopes=[]),
    )

    with pytest.raises(PermissionError, match="no vault access configured"):
        _resolve_identity(cfg)


# ── VaultResolutionMiddleware ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_middleware_passthrough_in_single_vault_mode(vault_factory):
    vault_factory({})
    seen_vault = {}

    async def call_next(context):
        from obsidian_mcp.config import get_config
        seen_vault["name"] = get_config().resolve_vault_name()
        return "ok"

    middleware = VaultResolutionMiddleware()
    result = await middleware.on_call_tool(MiddlewareContext(message=object()), call_next)

    assert result == "ok"
    assert seen_vault["name"] == "default"


@pytest.mark.asyncio
async def test_middleware_sets_vault_context_for_matched_identity(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(tmp_path, monkeypatch)
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = cfg
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="sk-private", client_id="sk-private", scopes=[]),
    )

    seen_vault = {}

    async def call_next(context):
        seen_vault["name"] = cfg.resolve_vault_name()
        return "ok"

    middleware = VaultResolutionMiddleware()
    result = await middleware.on_call_tool(MiddlewareContext(message=object()), call_next)

    assert result == "ok"
    assert seen_vault["name"] == "private"
    # Context must not leak past the call.
    assert cfg.resolve_vault_name() == cfg.default_vault_name


@pytest.mark.asyncio
async def test_middleware_rejects_unmatched_identity(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(tmp_path, monkeypatch)
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = cfg
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="x", client_id="unknown-key", scopes=[]),
    )

    async def call_next(context):
        return "should not be reached"

    middleware = VaultResolutionMiddleware()
    with pytest.raises(PermissionError):
        await middleware.on_call_tool(MiddlewareContext(message=object()), call_next)
