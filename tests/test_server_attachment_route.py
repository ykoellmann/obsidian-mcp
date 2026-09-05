"""End-to-end tests for the /attachments/{path} HTTP upload/download route.

Verifies the raw-bytes path documented in server.py works against the real
ASGI app: PUT/GET with an Authorization header write/read the file directly,
bypassing the MCP tool-call/base64 channel entirely.
"""
from __future__ import annotations

import time

import httpx
import pytest
from fastmcp.server.auth import AccessToken, TokenVerifier

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [".env", ".hidden/file.png", "extensionless", "script.py", "script.sh", "config.yaml"],
)
async def test_upload_route_rejects_unsafe_attachment_names(path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")

    async with _client() as client:
        resp = await client.put(
            f"/attachments/{path}",
            content=b"unsafe",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_route_rejects_declared_oversize(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("MAX_ATTACHMENT_BYTES", "3")

    async with _client() as client:
        resp = await client.put(
            "/attachments/file.png",
            content=b"1234",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 413
    assert not (tmp_path / "file.png").exists()


@pytest.mark.asyncio
async def test_upload_route_rejects_streamed_oversize(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("MAX_ATTACHMENT_BYTES", "3")

    async def body():
        yield b"12"
        yield b"34"

    async with _client() as client:
        resp = await client.put(
            "/attachments/file.png",
            content=body(),
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 413
    assert not (tmp_path / "file.png").exists()


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


# ── OAuth-style tokens (mcp.auth), independent of the static API_KEY ─────────

class _FakeOAuthVerifier(TokenVerifier):
    """Stands in for GitHubProvider/MultiAuth without hitting the network."""

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == "valid-oauth-token":
            return AccessToken(token=token, client_id="oauth-user", scopes=[])
        return None


@pytest.mark.asyncio
async def test_upload_route_accepts_oauth_token(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(server.mcp, "auth", _FakeOAuthVerifier())

    async with _client() as client:
        resp = await client.put(
            "/attachments/docs/file.pdf",
            content=b"PDF-CONTENT",
            headers={"Authorization": "Bearer valid-oauth-token"},
        )

    assert resp.status_code == 200
    assert (tmp_path / "docs" / "file.pdf").read_bytes() == b"PDF-CONTENT"


@pytest.mark.asyncio
async def test_upload_route_rejects_invalid_oauth_token(tmp_path, vault_factory, monkeypatch):
    vault_factory({})
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(server.mcp, "auth", _FakeOAuthVerifier())

    async with _client() as client:
        resp = await client.put(
            "/attachments/docs/file.pdf",
            content=b"PDF-CONTENT",
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    assert resp.status_code == 401
    assert not (tmp_path / "docs" / "file.pdf").exists()


@pytest.mark.asyncio
async def test_upload_route_accepts_api_key_alongside_oauth(tmp_path, vault_factory, monkeypatch):
    """Both auth variants must work at the same time, not either/or."""
    vault_factory({})
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setattr(server.mcp, "auth", _FakeOAuthVerifier())

    async with _client() as client:
        resp = await client.put(
            "/attachments/docs/file.pdf",
            content=b"PDF-CONTENT",
            headers={"Authorization": "Bearer test-key"},
        )

    assert resp.status_code == 200


# ── scoped tokens (no master key exposed) ────────────────────────────────────

@pytest.mark.asyncio
async def test_scoped_token_authorizes_upload(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    vault_factory({})
    token = create_attachment_token(
        "docs/file.pdf", signing_key="master-key", vault="default", method="PUT", expires_in=60
    )

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
    token = create_attachment_token(
        "file.png", signing_key="master-key", vault="default", method="GET", expires_in=60
    )

    async with _client() as client:
        resp = await client.get(f"/attachments/file.png?exp={token['expires_at']}&sig={token['sig']}")

    assert resp.status_code == 200
    assert resp.content == b"image-bytes"


@pytest.mark.asyncio
async def test_scoped_token_rejected_for_wrong_path(tmp_path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "master-key")
    vault_factory({})
    token = create_attachment_token(
        "allowed.png", signing_key="master-key", vault="default", method="PUT", expires_in=60
    )

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
    token = create_attachment_token(
        "file.png", signing_key="master-key", vault="default", method="GET", expires_in=60
    )

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
    token = create_attachment_token(
        "file.png", signing_key="master-key", vault="default", method="PUT", expires_in=1
    )

    async with _client() as client:
        resp = await client.put(
            f"/attachments/file.png?exp={int(time.time()) - 10}&sig={token['sig']}",
            content=b"data",
        )

    assert resp.status_code == 401


# ── multi-vault mode ─────────────────────────────────────────────────────

def _write_vaults_config(tmp_path):
    vault_a, vault_b = tmp_path / "a", tmp_path / "b"
    vault_a.mkdir()
    vault_b.mkdir()
    import json
    data = {
        "vaults": {"private": {"path": str(vault_a)}, "monari": {"path": str(vault_b)}},
        "identities": [
            {"type": "api_key", "value": "sk-private-only", "vaults": ["private"]},
            {"type": "api_key", "value": "sk-both", "vaults": ["private", "monari"], "default": "private"},
            {"type": "github_login", "value": "octocat", "vaults": ["monari"]},
        ],
    }
    config_path = tmp_path / "vaults.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return config_path, vault_a, vault_b


def _enable_multi_vault_http(monkeypatch, config_path):
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "http")


@pytest.mark.asyncio
async def test_bearer_token_route_scoped_to_identitys_default_vault(tmp_path, monkeypatch):
    config_path, vault_a, vault_b = _write_vaults_config(tmp_path)
    _enable_multi_vault_http(monkeypatch, config_path)
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = None
    # mcp.auth was built once at module-import time, before VAULTS_CONFIG
    # existed — rebuild it against the now-current env, same as production
    # startup does (VAULTS_CONFIG is already set before the process's very
    # first import there). Same reasoning as the _FakeOAuthVerifier tests
    # above, just with the real multi-key provider instead of a stand-in.
    monkeypatch.setattr(server.mcp, "auth", server._build_auth())

    async with _client() as client:
        resp = await client.put(
            "/attachments/file.png",
            content=b"data",
            headers={"Authorization": "Bearer sk-private-only"},
        )

    assert resp.status_code == 200
    assert (vault_a / "file.png").read_bytes() == b"data"
    assert not (vault_b / "file.png").exists()


@pytest.mark.asyncio
async def test_bearer_token_route_honors_vault_query_param(tmp_path, monkeypatch):
    config_path, vault_a, vault_b = _write_vaults_config(tmp_path)
    _enable_multi_vault_http(monkeypatch, config_path)
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = None
    monkeypatch.setattr(server.mcp, "auth", server._build_auth())

    async with _client() as client:
        resp = await client.put(
            "/attachments/file.png?vault=monari",
            content=b"data",
            headers={"Authorization": "Bearer sk-both"},
        )

    assert resp.status_code == 200
    assert (vault_b / "file.png").read_bytes() == b"data"
    assert not (vault_a / "file.png").exists()


@pytest.mark.asyncio
async def test_bearer_token_route_rejects_vault_outside_identity(tmp_path, monkeypatch):
    config_path, vault_a, vault_b = _write_vaults_config(tmp_path)
    _enable_multi_vault_http(monkeypatch, config_path)
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = None
    monkeypatch.setattr(server.mcp, "auth", server._build_auth())

    async with _client() as client:
        resp = await client.put(
            "/attachments/file.png?vault=monari",
            content=b"data",
            headers={"Authorization": "Bearer sk-private-only"},
        )

    assert resp.status_code == 403
    assert not (vault_a / "file.png").exists()
    assert not (vault_b / "file.png").exists()


@pytest.mark.asyncio
async def test_bearer_token_route_enforces_selected_vault_write_policy(tmp_path, monkeypatch):
    vault_a, vault_b = tmp_path / "a", tmp_path / "b"
    vault_a.mkdir()
    vault_b.mkdir()
    import json

    config_path = tmp_path / "vaults.json"
    config_path.write_text(
        json.dumps(
            {
                "vaults": {
                    "private": {"path": str(vault_a)},
                    "monari": {"path": str(vault_b), "write_paths": ["allowed/"]},
                },
                "identities": [
                    {
                        "type": "api_key",
                        "value": "sk-both",
                        "vaults": ["private", "monari"],
                        "default": "private",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VAULTS_CONFIG", str(config_path))
    monkeypatch.setenv("TRANSPORT", "sse")
    import obsidian_mcp.config as cfg_mod

    cfg_mod._config = None
    monkeypatch.setattr(server.mcp, "auth", server._build_auth())

    async with _client() as client:
        denied = await client.put(
            "/attachments/file.png?vault=monari",
            content=b"denied",
            headers={"Authorization": "Bearer sk-both"},
        )
        allowed = await client.put(
            "/attachments/allowed/file.png?vault=monari",
            content=b"allowed",
            headers={"Authorization": "Bearer sk-both"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert not (vault_b / "file.png").exists()
    assert (vault_b / "allowed" / "file.png").read_bytes() == b"allowed"


@pytest.mark.asyncio
async def test_scoped_token_route_multi_vault_isolates_correctly(tmp_path, monkeypatch):
    config_path, vault_a, vault_b = _write_vaults_config(tmp_path)
    _enable_multi_vault_http(monkeypatch, config_path)
    import obsidian_mcp.config as cfg_mod
    cfg_mod._config = None

    token = create_attachment_token("file.png", signing_key="sk-both", vault="monari", method="PUT", expires_in=60)

    async with _client() as client:
        # Correct vault query param -> succeeds, writes to the right vault.
        resp_ok = await client.put(
            f"/attachments/file.png?exp={token['expires_at']}&sig={token['sig']}&vault=monari",
            content=b"data",
        )
        # Tampering with the vault query param invalidates the signature.
        resp_tampered = await client.put(
            f"/attachments/file.png?exp={token['expires_at']}&sig={token['sig']}&vault=private",
            content=b"tampered",
        )

    assert resp_ok.status_code == 200
    assert (vault_b / "file.png").read_bytes() == b"data"
    assert resp_tampered.status_code == 401
    assert not (vault_a / "file.png").exists()
