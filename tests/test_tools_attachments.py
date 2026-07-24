from __future__ import annotations

import base64

import pytest

from obsidian_mcp.tools.attachments import add_attachment, list_attachments, read_attachment

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


def test_add_attachment_invalid_base64(vault_factory):
    vault_factory({})
    with pytest.raises(ValueError):
        add_attachment("file.png", "not-valid-base64!!!")
