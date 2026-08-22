"""Tests for the unauthenticated /health liveness/readiness route."""
from __future__ import annotations

import httpx
import pytest

from obsidian_mcp import server


def _client():
    transport = httpx.ASGITransport(app=server.mcp.http_app())
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_health_before_startup_returns_503(monkeypatch):
    monkeypatch.setattr(server, "_cfg", None)
    monkeypatch.setattr(server, "_indices", {})

    async with _client() as client:
        resp = await client.get("/health")

    assert resp.status_code == 503
    assert resp.json() == {"status": "starting"}


@pytest.mark.asyncio
async def test_health_ready_returns_ok(tmp_path, vault_factory, monkeypatch):
    idx = vault_factory({"note.md": "# Hello"})
    cfg = server.get_config()
    monkeypatch.setattr(server, "_cfg", cfg)
    monkeypatch.setattr(server, "_indices", {cfg.default_vault_name: idx})

    async with _client() as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["vault_path"] == str(tmp_path)
    assert body["index_ready"] is True


@pytest.mark.asyncio
async def test_health_requires_no_auth(tmp_path, vault_factory, monkeypatch):
    """/health must stay reachable even when API_KEY/OAuth are configured —
    it exposes no vault content, only process liveness."""
    idx = vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")
    cfg = server.get_config()
    monkeypatch.setattr(server, "_cfg", cfg)
    monkeypatch.setattr(server, "_indices", {cfg.default_vault_name: idx})

    async with _client() as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
