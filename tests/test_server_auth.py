"""Tests for _build_auth() (server.py) — API key + GitHub OAuth composition."""
from __future__ import annotations

from obsidian_mcp.server import _build_auth


def test_api_key_only(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.delenv("OAUTH_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("OAUTH_GITHUB_CLIENT_SECRET", raising=False)

    auth = _build_auth()

    assert auth is not None
    assert type(auth).__name__ == "_APIKeyAuthProvider"


def test_api_key_and_oauth_combined_clears_required_scopes(monkeypatch):
    """Regression test: MultiAuth defaults required_scopes to the GitHub
    provider's (["user"]) and enforces it across every verifier — including
    _APIKeyAuthProvider, whose tokens carry scopes=[]. Without clearing
    required_scopes, every API-key request fails with 403 insufficient_scope
    even though the key itself is valid (this broke a real deployment)."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("OAUTH_GITHUB_ALLOWED_LOGINS", "octocat")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")

    auth = _build_auth()

    assert type(auth).__name__ == "MultiAuth"
    assert auth.required_scopes == []
