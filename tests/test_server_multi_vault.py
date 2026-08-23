"""Tests for multi-vault wiring in server.py: multi-key API auth,
identity resolution, and VaultResolutionMiddleware."""
from __future__ import annotations

import json

import pytest
from fastmcp.server.auth import AccessToken
from fastmcp.server.middleware import MiddlewareContext

import obsidian_mcp.config as cfg_mod
from obsidian_mcp import server as server_mod
from obsidian_mcp.config import Config
from obsidian_mcp.server import (
    _DEFAULT_INSTRUCTIONS,
    VaultResolutionMiddleware,
    _APIKeyAuthProvider,
    _identities_from_env,
    _load_instructions,
    _resolve_identity,
    _select_vault,
    list_vaults_tool,
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


def test_shared_server_instructions_do_not_expose_default_vault_conventions(
    tmp_path, monkeypatch
):
    config_path = _write_vaults_config(tmp_path)
    (tmp_path / "a" / "_AI_INSTRUCTIONS.md").write_text(
        "private default-vault instructions", encoding="utf-8"
    )
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")
    monkeypatch.delenv("VAULT_PATH", raising=False)
    monkeypatch.setattr(cfg_mod, "_config", None)

    instructions = _load_instructions()

    assert instructions == _DEFAULT_INSTRUCTIONS
    assert "private default-vault instructions" not in instructions


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


# ── _select_vault (Phase 2: explicit vault= switching) ──────────────────────

class _FakeIdentity:
    def __init__(self, vaults, default=None):
        self.vaults = vaults
        self.default = default


def test_select_vault_uses_default_when_none_requested():
    identity = _FakeIdentity(vaults=["private", "monari"], default="private")
    assert _select_vault(identity, None) == "private"


def test_select_vault_uses_requested_when_allowed():
    identity = _FakeIdentity(vaults=["private", "monari"], default="private")
    assert _select_vault(identity, "monari") == "monari"


def test_select_vault_rejects_disallowed_vault():
    identity = _FakeIdentity(vaults=["private"], default="private")
    with pytest.raises(PermissionError, match="does not have access"):
        _select_vault(identity, "monari")


def test_select_vault_requires_explicit_when_no_default():
    identity = _FakeIdentity(vaults=["private", "monari"], default=None)
    with pytest.raises(PermissionError, match="no default is configured"):
        _select_vault(identity, None)


# ── VaultResolutionMiddleware: explicit vault= override ─────────────────────

class _FakeMessage:
    def __init__(self, arguments):
        self.arguments = arguments


@pytest.mark.asyncio
async def test_middleware_honors_explicit_vault_argument(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(
        tmp_path, monkeypatch,
        extra_identities=[
            {"type": "api_key", "value": "sk-both", "vaults": ["private", "monari"], "default": "private"},
        ],
    )
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = cfg
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="sk-both", client_id="sk-both", scopes=[]),
    )

    seen_vault = {}

    async def call_next(context):
        seen_vault["name"] = cfg.resolve_vault_name()
        return "ok"

    middleware = VaultResolutionMiddleware()
    context = MiddlewareContext(message=_FakeMessage(arguments={"vault": "monari"}))
    result = await middleware.on_call_tool(context, call_next)

    assert result == "ok"
    assert seen_vault["name"] == "monari"


@pytest.mark.asyncio
async def test_middleware_falls_back_to_default_without_vault_argument(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(
        tmp_path, monkeypatch,
        extra_identities=[
            {"type": "api_key", "value": "sk-both", "vaults": ["private", "monari"], "default": "private"},
        ],
    )
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = cfg
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="sk-both", client_id="sk-both", scopes=[]),
    )

    seen_vault = {}

    async def call_next(context):
        seen_vault["name"] = cfg.resolve_vault_name()
        return "ok"

    middleware = VaultResolutionMiddleware()
    context = MiddlewareContext(message=_FakeMessage(arguments={"path": "note.md"}))
    await middleware.on_call_tool(context, call_next)

    assert seen_vault["name"] == "private"


@pytest.mark.asyncio
async def test_middleware_rejects_vault_argument_outside_allowed_set(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(tmp_path, monkeypatch)
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = cfg
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="sk-private", client_id="sk-private", scopes=[]),
    )

    async def call_next(context):
        return "should not be reached"

    middleware = VaultResolutionMiddleware()
    context = MiddlewareContext(message=_FakeMessage(arguments={"vault": "monari"}))
    with pytest.raises(PermissionError, match="does not have access"):
        await middleware.on_call_tool(context, call_next)


# ── list_vaults_tool ─────────────────────────────────────────────────────

def test_list_vaults_tool_single_vault_mode(vault_factory):
    vault_factory({})
    result = list_vaults_tool()
    assert len(result) == 1
    assert result[0]["is_default"] is True


def test_list_vaults_tool_multi_vault_mode(tmp_path, monkeypatch):
    cfg = _cfg_with_vaults(
        tmp_path, monkeypatch,
        extra_identities=[
            {"type": "api_key", "value": "sk-both", "vaults": ["private", "monari"], "default": "private"},
        ],
    )
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = cfg
    monkeypatch.setattr(
        server_mod, "get_access_token",
        lambda: AccessToken(token="sk-both", client_id="sk-both", scopes=[]),
    )

    result = list_vaults_tool()
    names = {v["name"]: v["is_default"] for v in result}
    assert names == {"private": True, "monari": False}


def test_main_builds_policy_aware_index_and_watcher_per_vault(tmp_path, monkeypatch):
    config_path = _write_vaults_config(tmp_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["vaults"]["private"]["read_paths"] = ["Memory/"]
    data["vaults"]["monari"]["write_paths"] = ["Output/"]
    config_path.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "b" / "Output").mkdir()
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "stdio")

    import obsidian_mcp.config as cfg_mod

    cfg_mod._config = None
    created_indices = {}
    created_watchers = {}

    class FakeIndex:
        def __init__(self, path, *, exclude_paths, policy):
            self.path = path
            self.exclude_paths = exclude_paths
            self.policy = policy
            created_indices[path.name] = self

        def build(self, *, publish_ready=True):
            self.publish_ready = publish_ready
            return None

        def update(self, _path):
            return None

        def reconcile(self):
            self.reconciled = True

        def mark_ready(self):
            self.ready = True

    class FakeWatcher:
        def __init__(self, path, *, debounce_ms, reconcile_interval, policy):
            self.path = path
            self.debounce_ms = debounce_ms
            self.reconcile_interval = reconcile_interval
            self.policy = policy
            self.callback = None
            self.reconcile_callback = None
            created_watchers[path.name] = self

        def start(self, *, on_change, on_reconcile):
            self.callback = on_change
            self.reconcile_callback = on_reconcile

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    monkeypatch.setattr(server_mod, "VaultIndex", FakeIndex)
    monkeypatch.setattr(server_mod, "VaultWatcher", FakeWatcher)
    monkeypatch.setattr(server_mod.threading, "Thread", FakeThread)
    monkeypatch.setattr(server_mod.mcp, "run", lambda **_kwargs: None)
    monkeypatch.setattr(server_mod, "_indices", {})
    monkeypatch.setattr(server_mod, "_watchers", {})

    server_mod.main()

    assert set(created_indices) == {"a", "b"}
    assert created_indices["a"].policy.root == (tmp_path / "a").resolve()
    assert created_indices["a"].policy.read_paths == ("Memory/",)
    assert created_indices["b"].policy.root == (tmp_path / "b").resolve()
    assert created_indices["b"].policy.write_paths == ("Output/",)
    assert created_watchers["a"].policy is created_indices["a"].policy
    assert created_watchers["b"].policy is created_indices["b"].policy
    assert created_indices["a"].publish_ready is False
    assert created_indices["a"].reconciled is True
    assert created_indices["a"].ready is True
    assert created_watchers["a"].reconcile_callback == created_indices["a"].reconcile
