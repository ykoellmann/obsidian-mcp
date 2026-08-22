from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from .filesystem import VaultStorage
from .policy import VaultAccessPolicy

logger = logging.getLogger(__name__)


class VaultWatcher:
    """Watches for .md file changes in a vault directory.

    Tries watchdog (inotify on Linux) first; falls back to polling if
    watchdog is unavailable or the platform doesn't support it.
    Set WATCH_MODE=poll to force polling.
    """

    def __init__(
        self,
        vault_root: Path,
        poll_interval: float = 2.0,
        policy: VaultAccessPolicy | None = None,
    ) -> None:
        self._vault_root = vault_root
        self._policy = policy or VaultAccessPolicy(vault_root)
        self._storage = VaultStorage(self._policy)
        self._poll_interval = poll_interval
        self._observer = None
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, on_change: Callable[[str], None]) -> None:
        watch_mode = os.environ.get("WATCH_MODE", "auto").lower()
        if watch_mode == "poll" or not self._try_watchdog(on_change):
            self._start_polling(on_change)

    def stop(self) -> None:
        self._stop_event.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=self._poll_interval + 1)

    def _try_watchdog(self, on_change: Callable[[str], None]) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            vault_root = self._vault_root
            policy = self._policy

            class _Handler(FileSystemEventHandler):
                @staticmethod
                def _relative(path: str) -> str | None:
                    try:
                        return Path(path).relative_to(vault_root).as_posix()
                    except (ValueError, OSError):
                        return None

                def on_modified(self, event):
                    if event.is_directory:
                        return
                    rel = self._relative(event.src_path)
                    if rel and rel.lower().endswith(".md") and policy.can_read(rel):
                        on_change(rel)

                def on_created(self, event):
                    self.on_modified(event)

                def on_deleted(self, event):
                    rel = self._relative(event.src_path)
                    if not rel:
                        return
                    # Directory deletion/move events are passed through too;
                    # VaultIndex.update removes all indexed descendants when
                    # the path no longer exists.
                    if event.is_directory or rel.lower().endswith(".md"):
                        on_change(rel)

                def on_moved(self, event):
                    old_rel = self._relative(event.src_path)
                    new_rel = self._relative(event.dest_path)
                    if old_rel and (event.is_directory or old_rel.lower().endswith(".md")):
                        on_change(old_rel)
                    if not new_rel:
                        return
                    if event.is_directory:
                        if policy.can_read(new_rel):
                            on_change(new_rel)
                    elif new_rel.lower().endswith(".md") and policy.can_read(new_rel):
                        on_change(new_rel)

            self._observer = Observer()
            self._observer.schedule(_Handler(), str(self._vault_root), recursive=True)
            self._observer.start()
            logger.info("VaultWatcher: using watchdog observer")
            return True
        except Exception as exc:
            logger.warning("watchdog unavailable (%s), falling back to polling", exc)
            return False

    def _start_polling(self, on_change: Callable[[str], None]) -> None:
        logger.info("VaultWatcher: using polling fallback (interval=%ss)", self._poll_interval)
        mtimes: dict[str, float] = {}

        def _poll() -> None:
            while not self._stop_event.is_set():
                try:
                    current: dict[str, float] = {}
                    for path in self._storage.list_files():
                        if path.relative.lower().endswith(".md"):
                            current[path.relative] = self._storage.stat(path.relative).st_mtime

                    for rel, mtime in current.items():
                        if self._policy.can_read(rel) and mtimes.get(rel) != mtime:
                            on_change(rel)

                    for rel in set(mtimes) - set(current):
                        if self._policy.can_read(rel):
                            on_change(rel)

                    mtimes.clear()
                    mtimes.update(current)
                except Exception:
                    logger.exception("Polling error")
                self._stop_event.wait(self._poll_interval)

        self._poll_thread = threading.Thread(target=_poll, daemon=True)
        self._poll_thread.start()
