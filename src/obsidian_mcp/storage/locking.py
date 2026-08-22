"""Process locks stored outside the synced vault."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from filelock import FileLock, Timeout


class LockTimeoutError(Exception):
    pass


def _default_lock_path() -> tuple[Path | None, bool]:
    try:
        from ..config import ConfigError, get_config

        return get_config().lock_path, True
    except (ImportError, ModuleNotFoundError):
        return None, False
    except ConfigError:
        # No configured vault means a standalone compatibility caller. Any
        # configured deployment error must remain visible to the operator.
        if "VAULT_PATH" in os.environ:
            raise
        # Explicit-root storage tests can run without a Config.  Keep this
        # compatibility fallback outside any vault directory.
        return None, False


def acquire_lock(path: str, timeout: float = 5.0, lock_path: str | Path | None = None) -> FileLock:
    """Acquire a stable hashed lock outside the vault.

    ``path`` is hashed rather than embedded in the filename, avoiding path
    disclosure and preventing creation of ``*.lock`` files in synced content.
    """
    if lock_path is not None:
        root, configured = Path(lock_path), True
    else:
        root, configured = _default_lock_path()
        if root is None:
            root = Path(tempfile.gettempdir()) / "obsidian-mcp-locks"
    lock_name = hashlib.sha256(str(path).encode("utf-8", "surrogatepass")).hexdigest() + ".lock"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        if configured:
            raise
        # Local development may not have /data mounted.  Production Docker
        # deployments provide LOCK_PATH=/data/locks and therefore never use
        # this fallback.
        root = Path(tempfile.gettempdir()) / "obsidian-mcp-locks"
        root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(root / lock_name), timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:
        raise LockTimeoutError(f"Could not acquire lock for {path!r} within {timeout}s") from exc
    return lock
