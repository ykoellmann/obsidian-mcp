"""Spike 2: Test whether watchdog receives inotify events inside a Docker container."""

import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

WATCH_DIR = Path("/watch")


def main():
    logger.info("Starting inotify spike, watching %s", WATCH_DIR)
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                logger.info("EVENT: %s %s", event.event_type, event.src_path)

        observer = Observer()
        observer.schedule(_Handler(), str(WATCH_DIR), recursive=True)
        observer.start()
        logger.info("watchdog observer started (inotify)")
        while True:
            time.sleep(1)
    except Exception as exc:
        logger.error("watchdog failed: %s – falling back to polling", exc)
        _poll()


def _poll():
    mtimes: dict[str, float] = {}
    while True:
        current = {str(p): p.stat().st_mtime for p in WATCH_DIR.rglob("*") if p.is_file()}
        for path, mtime in current.items():
            if mtimes.get(path) != mtime:
                logger.info("POLL CHANGE: %s", path)
        mtimes.clear()
        mtimes.update(current)
        time.sleep(2)


if __name__ == "__main__":
    main()
