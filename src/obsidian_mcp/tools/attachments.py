from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from ..config import get_config
from ..storage.filesystem import validate_path, write_file_atomic_bytes

_TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".js", ".ts"}
_MAX_TOKEN_TTL = 3600


def list_attachments(folder: str = "") -> list[dict]:
    """List all non-Markdown files in the vault (images, PDFs, audio, etc.)."""
    cfg = get_config()
    root = cfg.vault_path
    base = validate_path(root, folder) if folder else root

    results = []
    for p in base.rglob("*"):
        if p.is_dir() or p.suffix.lower() == ".md":
            continue
        rel = str(p.relative_to(root))
        parts = Path(rel).parts
        if any(part in cfg.exclude_paths for part in parts):
            continue
        stat = p.stat()
        mime, _ = mimetypes.guess_type(str(p))
        results.append({
            "path": rel,
            "size_bytes": stat.st_size,
            "mime_type": mime or "application/octet-stream",
            "mtime": stat.st_mtime,
        })

    return sorted(results, key=lambda x: x["path"])


def read_attachment(path: str) -> dict:
    """Read an attachment file. Text files returned as UTF-8 string; binary files as base64."""
    cfg = get_config()
    target = validate_path(cfg.vault_path, path)

    if not target.exists():
        raise FileNotFoundError(f"Attachment not found: {path!r}")
    if target.is_dir():
        raise IsADirectoryError(f"Path is a directory: {path!r}")

    mime, _ = mimetypes.guess_type(str(target))
    mime = mime or "application/octet-stream"
    is_text = mime.startswith("text/") or target.suffix.lower() in _TEXT_SUFFIXES

    if is_text:
        return {
            "path": path,
            "mime_type": mime,
            "encoding": "utf-8",
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }

    data = target.read_bytes()
    return {
        "path": path,
        "mime_type": mime,
        "encoding": "base64",
        "content": base64.b64encode(data).decode("ascii"),
        "size_bytes": len(data),
    }


def write_attachment_bytes(path: str, data: bytes) -> dict:
    """Write raw bytes as a binary attachment.

    Shared by add_attachment (decodes base64 from an MCP tool call) and the
    server's direct HTTP upload route (raw bytes, no MCP tool-call/base64
    round trip needed).
    """
    cfg = get_config()
    validate_path(cfg.vault_path, path)

    # Refuse .md through this path to keep write_note as the canonical text entrypoint
    if Path(path).suffix.lower() == ".md":
        raise ValueError("Use write_note_tool for Markdown files, not add_attachment_tool")

    write_file_atomic_bytes(cfg.vault_path, path, data)

    mime, _ = mimetypes.guess_type(path)
    return {
        "path": path,
        "status": "written",
        "size_bytes": len(data),
        "mime_type": mime or "application/octet-stream",
    }


def add_attachment(path: str, content_base64: str) -> dict:
    """Write a binary attachment from a base64-encoded string.
    Use this to add images, PDFs, or other binary files to the vault."""
    try:
        data = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 content: {exc}") from exc

    return write_attachment_bytes(path, data)


def _sign_attachment_token(signing_key: str, method: str, path: str, vault: str, expires_at: int) -> str:
    # vault is part of the signed message (not just a side channel) so a
    # token minted for one vault can't be replayed against another by
    # tampering with an unsigned query param.
    msg = f"{method}:{path}:{vault}:{expires_at}".encode()
    return hmac.new(signing_key.encode(), msg, hashlib.sha256).hexdigest()


def create_attachment_token(
    path: str,
    signing_key: str,
    vault: str,
    method: str = "PUT",
    expires_in: int = 300,
) -> dict:
    """Create a short-lived, single-file, single-method signed token for the
    server's GET/PUT /attachments/{path} HTTP route.

    Lets a client fetch or upload a file's raw bytes directly over HTTP
    without ever being handed the server's long-lived master API_KEY — the
    token is scoped to this exact path, method, and vault, and expires on
    its own. If PUBLIC_BASE_URL is configured, the ready-to-use request URL
    is included so the caller never has to guess host/port/scheme itself.

    signing_key/vault are resolved by the caller (server.py), not here —
    which key is valid to sign with depends on the calling identity
    (single global API_KEY in single-vault mode, that identity's own key in
    multi-vault mode), which this module deliberately stays unaware of.
    """
    cfg = get_config()
    method = method.upper()
    if method not in ("GET", "PUT"):
        raise ValueError("method must be 'GET' or 'PUT'")

    expires_in = max(1, min(int(expires_in), _MAX_TOKEN_TTL))
    expires_at = int(time.time()) + expires_in
    sig = _sign_attachment_token(signing_key, method, path, vault, expires_at)
    result = {"path": path, "method": method, "vault": vault, "expires_at": expires_at, "sig": sig}
    if cfg.public_base_url:
        quoted_path = quote(path, safe="/")
        query_params = {"exp": expires_at, "sig": sig}
        if vault != cfg.default_vault_name:
            # Keep single-vault-mode URLs exactly as before — only add
            # ?vault= when it actually says something the server can't
            # already assume.
            query_params["vault"] = vault
        query = urlencode(query_params)
        result["url"] = f"{cfg.public_base_url}/attachments/{quoted_path}?{query}"
    return result


def verify_attachment_token(
    signing_key: str, method: str, path: str, vault: str, expires_at: str | int, sig: str
) -> bool:
    """Verify a token minted by create_attachment_token. Constant-time, expiry-checked."""
    try:
        expires_at_int = int(expires_at)
    except (TypeError, ValueError):
        return False
    if time.time() > expires_at_int:
        return False
    expected = _sign_attachment_token(signing_key, method, path, vault, expires_at_int)
    return hmac.compare_digest(sig, expected)
