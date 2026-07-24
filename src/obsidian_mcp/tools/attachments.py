from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from ..config import get_config
from ..storage.filesystem import validate_path, write_file_atomic_bytes

_TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".js", ".ts"}


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


def add_attachment(path: str, content_base64: str) -> dict:
    """Write a binary attachment from a base64-encoded string.
    Use this to add images, PDFs, or other binary files to the vault."""
    cfg = get_config()
    validate_path(cfg.vault_path, path)

    # Refuse .md through this path to keep write_note as the canonical text entrypoint
    if Path(path).suffix.lower() == ".md":
        raise ValueError("Use write_note_tool for Markdown files, not add_attachment_tool")

    try:
        data = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 content: {exc}") from exc

    write_file_atomic_bytes(cfg.vault_path, path, data)

    mime, _ = mimetypes.guess_type(path)
    return {
        "path": path,
        "status": "written",
        "size_bytes": len(data),
        "mime_type": mime or "application/octet-stream",
    }
