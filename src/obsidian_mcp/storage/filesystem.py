from __future__ import annotations

import os
import uuid
from pathlib import Path


class PathTraversalError(Exception):
    pass


def validate_path(vault_root: str | Path, relative_path: str) -> Path:
    root = Path(vault_root).resolve()
    target = (root / relative_path).resolve()
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise PathTraversalError(f"Path escapes vault root: {relative_path!r}")
    return target


def read_file(vault_root: str | Path, relative_path: str) -> str:
    target = validate_path(vault_root, relative_path)
    return target.read_text(encoding="utf-8", errors="replace")


def write_file_atomic(vault_root: str | Path, relative_path: str, content: str) -> None:
    target = validate_path(vault_root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".tmp-{uuid.uuid4().hex}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def read_file_bytes(vault_root: str | Path, relative_path: str) -> bytes:
    target = validate_path(vault_root, relative_path)
    return target.read_bytes()


def write_file_atomic_bytes(vault_root: str | Path, relative_path: str, content: bytes) -> None:
    target = validate_path(vault_root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".tmp-{uuid.uuid4().hex}{target.suffix}"
    try:
        tmp.write_bytes(content)
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
