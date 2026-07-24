from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class VaultWatcher:
    """Watches for .md file changes in a vault directory.

    Tries watchdog (inotify on Linux) first; falls back to polling if
    watchdog is unavailable or the platform doesn't support it.
    Set WATCH_MODE=poll to force polling.
    """

    def __init__(self, vault_root: Path, poll_interval: float = 2.0) -> None:
        self._vault_root = vault_root
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

            class _Handler(FileSystemEventHandler):
                def on_modified(self, event):
                    if not event.is_directory and event.src_path.endswith(".md"):
                        rel = str(Path(event.src_path).relative_to(vault_root))
                        on_change(rel)

                def on_created(self, event):
                    self.on_modified(event)

                def on_deleted(self, event):
                    if not event.is_directory and event.src_path.endswith(".md"):
                        rel = str(Path(event.src_path).relative_to(vault_root))
                        on_change(rel)

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
                    for p in self._vault_root.rglob("*.md"):
                        rel = str(p.relative_to(self._vault_root))
                        current[rel] = p.stat().st_mtime

                    for rel, mtime in current.items():
                        if mtimes.get(rel) != mtime:
                            on_change(rel)

                    for rel in set(mtimes) - set(current):
                        on_change(rel)

                    mtimes.clear()
                    mtimes.update(current)
                except Exception:
                    logger.exception("Polling error")
                self._stop_event.wait(self._poll_interval)

        self._poll_thread = threading.Thread(target=_poll, daemon=True)
        self._poll_thread.start()
