#!/usr/bin/env python3
"""Start a disposable MCP server and run the HTTP smoke client against it."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = Path(sys.executable).with_name("obsidian-remote-mcp")
CLIENT = REPO_ROOT / "scripts" / "smoke_test_mcp.py"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MCP server exited during startup with status {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310
                payload = json.loads(response.read())
                if response.status == 200 and payload.get("status") == "ok":
                    return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"MCP server did not become healthy: {last_error}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run(args: argparse.Namespace) -> int:
    if not SERVER.is_file():
        raise RuntimeError("Missing installed entry point; run with `uv run python` after `uv sync`")

    root = Path(tempfile.mkdtemp(prefix="obsidian-mcp-smoke-"))
    vault = root / "vault"
    (vault / "AI-Memory").mkdir(parents=True)

    port = args.port or _free_port()
    api_key = args.api_key or secrets.token_urlsafe(32)
    base_url = f"http://127.0.0.1:{port}"
    log_path = root / "server.log"
    environment = os.environ.copy()
    environment.update(
        {
            "VAULT_PATH": str(vault),
            "TRANSPORT": "http",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "API_KEY": api_key,
            "OBSIDIAN_MCP_API_KEY": api_key,
            "WRITE_PATHS": "AI-Memory/",
        }
    )

    succeeded = False
    process: subprocess.Popen[bytes] | None = None
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(SERVER)],
                cwd=REPO_ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            _wait_for_health(f"{base_url}/health", process, args.startup_timeout)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLIENT),
                    "--url",
                    f"{base_url}/mcp",
                    "--denied-note",
                    "outside-write-scope.md",
                    "--vault-path",
                    str(vault),
                ],
                cwd=REPO_ROOT,
                env=environment,
                check=False,
            )
            succeeded = completed.returncode == 0
            return completed.returncode
    finally:
        if process is not None:
            _stop(process)
        if args.keep or not succeeded:
            print(f"Smoke-test files retained at: {root}", file=sys.stderr)
            if not succeeded and log_path.exists():
                print(f"Server log: {log_path}", file=sys.stderr)
        else:
            shutil.rmtree(root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="Local disposable server key; default is random")
    parser.add_argument("--port", type=int, help="Fixed port; default chooses a free port")
    parser.add_argument("--startup-timeout", type=float, default=15)
    parser.add_argument("--keep", action="store_true", help="Keep the disposable vault and log")
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
