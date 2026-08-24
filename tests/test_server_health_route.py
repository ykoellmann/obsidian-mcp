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
    assert "vault_path" not in body
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


@pytest.mark.asyncio
async def test_health_is_503_until_index_is_ready(tmp_path, monkeypatch):
    index = server.VaultIndex(tmp_path)
    cfg = server.get_config()
    monkeypatch.setattr(server, "_cfg", cfg)
    monkeypatch.setattr(server, "_indices", {cfg.default_vault_name: index})

    async with _client() as client:
        resp = await client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["index_ready"] is False


@pytest.mark.asyncio
async def test_periodic_reconcile_error_is_reported_without_discarding_ready_index(
    tmp_path, vault_factory, monkeypatch
):
    index = vault_factory({"note.md": "content"})
    monkeypatch.setattr(index._storage, "list_files", lambda: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        index.reconcile()
    cfg = server.get_config()
    monkeypatch.setattr(server, "_cfg", cfg)
    monkeypatch.setattr(server, "_indices", {cfg.default_vault_name: index})

    async with _client() as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["last_reconcile_error"] == "disk"
