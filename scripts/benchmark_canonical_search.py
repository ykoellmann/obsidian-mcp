#!/usr/bin/env python3
"""Measure the deliberately scan-based search on disposable 1 KB notes."""

from __future__ import annotations

import json
import os
import resource
import tempfile
import time
from pathlib import Path

from obsidian_mcp import config
from obsidian_mcp.tools.canonical import search_files
from obsidian_mcp.tools.read import search_notes


def measure(call):
    start = time.perf_counter()
    result = call()
    return result, round(time.perf_counter() - start, 3)


def main():
    with tempfile.TemporaryDirectory(prefix="mcp-benchmark-") as root:
        vault = Path(root) / "vault"
        vault.mkdir()
        os.environ.update(
            VAULT_PATH=str(vault), LOCK_PATH=str(Path(root) / "locks"), READ_ONLY="true"
        )
        os.environ.pop("VAULTS_CONFIG", None)
        config._config = None
        for target, previous in [(1000, 0), (10000, 1000)]:
            for number in range(previous, target):
                (vault / f"{number:05}.md").write_text(
                    "---\nstatus: active\n---\nBudget meeting\n" + "body text " * 100
                )
            first, initial = measure(lambda: search_files("budget", limit=20))
            _, continuation = measure(
                lambda cursor=first["cursor"]: search_files("budget", limit=20, cursor=cursor)
            )
            _, legacy = measure(lambda: search_notes("budget", limit=20))
            # ru_maxrss units differ between Linux and macOS; report the platform with the raw value.
            print(
                json.dumps(
                    {
                        "notes": target,
                        "initial_seconds": initial,
                        "continuation_seconds": continuation,
                        "legacy_scan_seconds": legacy,
                        "maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        "platform": os.sys.platform,
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
