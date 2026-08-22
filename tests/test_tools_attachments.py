from __future__ import annotations

import base64
import time

import pytest

from obsidian_mcp.tools.attachments import (
    AttachmentTooLargeError,
    _sign_attachment_token,
    add_attachment,
    create_attachment_token,
    list_attachments,
    read_attachment,
    verify_attachment_token,
)

# ── list_attachments ──────────────────────────────────────────────────────

def test_list_attachments_finds_image(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    results = list_attachments()
    assert any(r["path"] == "image.png" for r in results)
    assert any(r["mime_type"] == "image/png" for r in results)


def test_list_attachments_excludes_md(tmp_path, vault_factory):
    vault_factory({"note.md": "content"})
    results = list_attachments()
    assert not any(r["path"].endswith(".md") for r in results)


# ── read_attachment ───────────────────────────────────────────────────────

def test_read_attachment_text(tmp_path, vault_factory):
    vault_factory({})
    (tmp_path / "data.csv").write_text("a,b,c\n1,2,3")
    result = read_attachment("data.csv")
    assert result["encoding"] == "utf-8"
    assert "a,b,c" in result["content"]


def test_read_attachment_binary_base64(tmp_path, vault_factory):
    vault_factory({})
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    (tmp_path / "image.png").write_bytes(raw)
    result = read_attachment("image.png")
    assert result["encoding"] == "base64"
    assert base64.b64decode(result["content"]) == raw


# ── add_attachment ────────────────────────────────────────────────────────

def test_add_attachment_round_trip(tmp_path, vault_factory):
    vault_factory({})
    raw = b"PDF-CONTENT-\x00\x01\x02"
    encoded = base64.b64encode(raw).decode()
    result = add_attachment("docs/file.pdf", encoded)
    assert result["status"] == "written"
    assert (tmp_path / "docs" / "file.pdf").exists()
    read_back = read_attachment("docs/file.pdf")
    assert base64.b64decode(read_back["content"]) == raw


def test_add_attachment_rejects_md(vault_factory):
    vault_factory({})
    with pytest.raises(ValueError):
        add_attachment("note.md", base64.b64encode(b"text").decode())


@pytest.mark.parametrize(
    "path",
    [".env", ".hidden/file.png", "extensionless", "script.py", "script.sh", "config.yaml"],
)
def test_add_attachment_positive_policy_rejects_hidden_or_unsafe_names(path, vault_factory):
    vault_factory({})
    with pytest.raises(ValueError):
        add_attachment(path, base64.b64encode(b"not-an-attachment").decode())


def test_add_attachment_invalid_base64(vault_factory):
    vault_factory({})
    with pytest.raises(ValueError):
        add_attachment("file.png", "not-valid-base64!!!")


def test_add_attachment_enforces_configured_size_limit(vault_factory, monkeypatch):
    monkeypatch.setenv("MAX_ATTACHMENT_BYTES", "3")
    vault_factory({})
    with pytest.raises(AttachmentTooLargeError):
        add_attachment("file.png", base64.b64encode(b"1234").decode())


# ── attachment tokens ─────────────────────────────────────────────────────

def test_create_attachment_token_requires_api_key(vault_factory):
    vault_factory({})
    with pytest.raises(ValueError, match="API_KEY"):
        create_attachment_token("file.png")


def test_create_attachment_token_round_trip(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    token = create_attachment_token("file.png", method="PUT", expires_in=60)
    assert token["path"] == "file.png"
    assert token["method"] == "PUT"
    assert verify_attachment_token("secret", "PUT", "file.png", token["expires_at"], token["sig"])


def test_create_attachment_token_no_url_without_public_base_url(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    token = create_attachment_token("file.png")
    assert "url" not in token


def test_create_attachment_token_includes_url_when_configured(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://obsidian.example.com")
    vault_factory({})
    token = create_attachment_token("docs/file.pdf", method="PUT", expires_in=60)
    expected = (
        f"https://obsidian.example.com/attachments/docs/file.pdf"
        f"?exp={token['expires_at']}&sig={token['sig']}"
    )
    assert token["url"] == expected


def test_create_attachment_token_url_strips_trailing_slash(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://obsidian.example.com/")
    vault_factory({})
    token = create_attachment_token("file.png")
    assert token["url"].startswith("https://obsidian.example.com/attachments/file.png?")


def test_create_attachment_token_url_encodes_path(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://obsidian.example.com")
    vault_factory({})
    token = create_attachment_token("My Photos/image one.png")
    assert "My%20Photos/image%20one.png" in token["url"]


def test_verify_attachment_token_rejects_wrong_key(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    token = create_attachment_token("file.png")
    assert not verify_attachment_token("wrong-key", "PUT", "file.png", token["expires_at"], token["sig"])


def test_verify_attachment_token_rejects_wrong_path(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    token = create_attachment_token("file.png")
    assert not verify_attachment_token("secret", "PUT", "other.png", token["expires_at"], token["sig"])


def test_verify_attachment_token_rejects_wrong_method(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    token = create_attachment_token("file.png", method="PUT")
    assert not verify_attachment_token("secret", "GET", "file.png", token["expires_at"], token["sig"])


def test_verify_attachment_token_rejects_expired(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    expired_at = int(time.time()) - 10
    sig = _sign_attachment_token("secret", "PUT", "file.png", expired_at)
    assert not verify_attachment_token("secret", "PUT", "file.png", expired_at, sig)


def test_create_attachment_token_rejects_bad_method(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    with pytest.raises(ValueError, match="method"):
        create_attachment_token("file.png", method="DELETE")


def test_create_attachment_token_caps_ttl(vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    token = create_attachment_token("file.png", expires_in=999999)
    assert token["expires_at"] <= int(time.time()) + 3600 + 1


@pytest.mark.parametrize(
    "path",
    [".env", ".hidden/file.png", "extensionless", "script.py", "script.sh", "config.yaml"],
)
def test_put_attachment_token_positive_policy_rejects_unsafe_names(path, vault_factory, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    vault_factory({})
    with pytest.raises(ValueError):
        create_attachment_token(path, method="PUT")
