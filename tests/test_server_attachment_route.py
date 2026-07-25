"""End-to-end tests for the /attachments/{path} HTTP upload/download route.

Verifies the raw-bytes path documented in server.py works against the real
ASGI app: PUT/GET with an Authorization header write/read the file directly,
bypassing the MCP tool-call/base64 channel entirely.
"""
from __future__ import annotations

import time

import httpx
import pytest

from obsidian_mcp import server
from obsidian_mcp.tools.attachments import create_attachment_token


def _client():
    transport = httpx.ASGITransport(app=server.mcp.http_app())
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ── PUT (upload) ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_route_writes_file(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")

    async with _client() as client:
        resp = await client.put(
            "/attachments/docs/file.pdf",
            content=b"PDF-CONTENT-\x00\x01\x02",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "written"
    assert (tmp_path / "docs" / "file.pdf").read_bytes() == b"PDF-CONTENT-\x00\x01\x02"


@pytest.mark.asyncio
async def test_upload_route_rejects_wrong_key(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("API_KEY", "correct-key")

    async with _client() as client:
        resp = await client.put(
            "/attachments/file.png",
            content=b"data",
            headers={"Authorization": "Bearer wrong-key"},
        )

    assert resp.status_code == 401
    assert not (tmp_path / "file.png").exists()


@pytest.mark.asyncio
async def test_upload_route_rejects_markdown(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")

    async with _client() as client:
        resp = await client.put(
            "/attachments/note.md",
            content=b"# not allowed here",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 400


# ── GET (download) ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_route_reads_file(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "file.pdf").write_bytes(b"PDF-CONTENT-\x00\x01\x02")
    monkeypatch.setenv("API_KEY", "test-key")

    async with _client() as client:
        resp = await client.get(
            "/attachments/docs/file.pdf",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 200
    assert resp.content == b"PDF-CONTENT-\x00\x01\x02"
    assert resp.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_download_route_missing_file(vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")

    async with _client() as client:
        resp = await client.get(
            "/attachments/ghost.png",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_route_rejects_wrong_key(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    (tmp_path / "file.png").write_bytes(b"data")
    monkeypatch.setenv("API_KEY", "correct-key")

    async with _client() as client:
        resp = await client.get(
            "/attachments/file.png",
            headers={"Authorization": "Bearer wrong-key"},
        )

    assert resp.status_code == 401


# ── scoped tokens (no master key exposed) ────────────────────────────────────

@pytest.mark.asyncio
async def test_scoped_token_authorizes_upload(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    vault_factory({})
    token = create_attachment_token("docs/file.pdf", method="PUT", expires_in=60)

    async with _client() as client:
        resp = await client.put(
            f"/attachments/docs/file.pdf?exp={token['expires_at']}&sig={token['sig']}",
            content=b"PDF-CONTENT",
        )

    assert resp.status_code == 200
    assert (tmp_path / "docs" / "file.pdf").read_bytes() == b"PDF-CONTENT"


@pytest.mark.asyncio
async def test_scoped_token_authorizes_download(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    vault_factory({})
    (tmp_path / "file.png").write_bytes(b"image-bytes")
    token = create_attachment_token("file.png", method="GET", expires_in=60)

    async with _client() as client:
        resp = await client.get(f"/attachments/file.png?exp={token['expires_at']}&sig={token['sig']}")

    assert resp.status_code == 200
    assert resp.content == b"image-bytes"


@pytest.mark.asyncio
async def test_scoped_token_rejected_for_wrong_path(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    vault_factory({})
    token = create_attachment_token("allowed.png", method="PUT", expires_in=60)

    async with _client() as client:
        resp = await client.put(
            f"/attachments/other.png?exp={token['expires_at']}&sig={token['sig']}",
            content=b"data",
        )

    assert resp.status_code == 401
    assert not (tmp_path / "other.png").exists()


@pytest.mark.asyncio
async def test_scoped_token_rejected_for_wrong_method(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    vault_factory({})
    # token minted for GET must not authorize a PUT (would let a read-only
    # token overwrite the file it was meant only to expose for reading)
    token = create_attachment_token("file.png", method="GET", expires_in=60)

    async with _client() as client:
        resp = await client.put(
            f"/attachments/file.png?exp={token['expires_at']}&sig={token['sig']}",
            content=b"data",
        )

    assert resp.status_code == 401
    assert not (tmp_path / "file.png").exists()


@pytest.mark.asyncio
async def test_scoped_token_rejected_when_expired(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    vault_factory({})
    token = create_attachment_token("file.png", method="PUT", expires_in=1)

    async with _client() as client:
        resp = await client.put(
            f"/attachments/file.png?exp={int(time.time()) - 10}&sig={token['sig']}",
            content=b"data",
        )

    assert resp.status_code == 401
