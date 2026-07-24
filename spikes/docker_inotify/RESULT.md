# Spike 2 – Docker + inotify

**Status:** DONE – 2026-07-23

## Setup

OrbStack als Docker-Runtime auf macOS (Intel/ARM), Bind-Mount auf `testdir/`.

## Observations

- [x] inotify-Event kommt im Container an
- [x] Latenz: <1ms (praktisch sofort)
- Events: `created` + `modified` auf Datei und Parent-Dir

```
2026-07-23 07:12:32,379 watchdog observer started (inotify)
2026-07-23 07:12:54,728 EVENT: created /watch/probe.md
2026-07-23 07:12:54,728 EVENT: modified /watch
2026-07-23 07:12:54,728 EVENT: modified /watch/probe.md
```

## Decision

`WATCH_MODE=auto` bleibt Default in `storage/watcher.py` — watchdog/inotify
funktioniert mit OrbStack auf macOS zuverlässig. Polling-Fallback bleibt als
Absicherung für andere Environments erhalten.
