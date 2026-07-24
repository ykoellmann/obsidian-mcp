from __future__ import annotations

from filelock import FileLock, Timeout


class LockTimeoutError(Exception):
    pass


def acquire_lock(path: str, timeout: float = 5.0) -> FileLock:
    lock = FileLock(f"{path}.lock", timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:
        raise LockTimeoutError(f"Could not acquire lock for {path!r} within {timeout}s") from exc
    return lock
