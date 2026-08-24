#!/usr/bin/env python3
"""End-to-end smoke test for a running obsidian-mcp HTTP server."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastmcp import Client

CREATED_CONTENT = "# MCP smoke test\n\ncreated over real HTTP\n"
UPDATED_CONTENT = "# MCP smoke test\n\nupdated over real HTTP\n"
EXPECTED_CREATED_CONTENT = "# MCP smoke test\n\ncreated over real HTTP"
EXPECTED_UPDATED_CONTENT = "# MCP smoke test\n\nupdated over real HTTP"


def _result_value(result: Any) -> Any:
    if result.data is not None:
        return result.data
    if result.structured_content is not None:
        value = result.structured_content.get("result", result.structured_content)
        return value
    if len(result.content) == 1 and hasattr(result.content[0], "text"):
        return json.loads(result.content[0].text)
    raise RuntimeError(f"Unexpected MCP response: {result!r}")


def _structured(result: Any) -> Any:
    if result.is_error:
        text = "\n".join(getattr(item, "text", repr(item)) for item in result.content)
        raise RuntimeError(text)
    return _result_value(result)


def _require_rejection(result: Any, description: str) -> str:
    text = "\n".join(getattr(item, "text", repr(item)) for item in result.content)
    if result.is_error:
        return text
    value = _result_value(result)
    if isinstance(value, dict) and value.get("error"):
        return json.dumps(value, sort_keys=True)
    raise RuntimeError(f"{description} unexpectedly succeeded: {result!r}")


async def run(args: argparse.Namespace) -> None:
    async with Client(args.url, auth=args.api_key) as client:
        tools = {tool.name for tool in await client.list_tools()}
        required = {"list_notes_tool", "read_note_tool", "write_note_tool"}
        missing = required - tools
        if missing:
            raise RuntimeError(f"Missing required tools: {sorted(missing)}")

        created = _structured(
            await client.call_tool(
                "write_note_tool",
                {"path": args.note, "content": CREATED_CONTENT},
            )
        )
        first_read = _structured(
            await client.call_tool("read_note_tool", {"path": args.note})
        )
        if first_read.get("content") != EXPECTED_CREATED_CONTENT:
            raise RuntimeError("Created note did not round-trip through read_note_tool")

        updated = _structured(
            await client.call_tool(
                "write_note_tool",
                {"path": args.note, "content": UPDATED_CONTENT},
            )
        )
        final_read = _structured(
            await client.call_tool("read_note_tool", {"path": args.note})
        )
        if final_read.get("content") != EXPECTED_UPDATED_CONTENT:
            raise RuntimeError("Updated note did not round-trip through read_note_tool")

        denied_write = "not_requested"
        if args.denied_note:
            denied_result = await client.call_tool(
                "write_note_tool",
                {
                    "path": args.denied_note,
                    "content": "this must not be written\n",
                },
                raise_on_error=False,
            )
            _require_rejection(denied_result, "Denied write")
            denied_write = "access_denied"

        if args.vault_path:
            disk_path = args.vault_path / args.note
            if disk_path.read_bytes() != UPDATED_CONTENT.encode("utf-8"):
                raise RuntimeError(f"On-disk content differs at {disk_path}")
            if args.denied_note and (args.vault_path / args.denied_note).exists():
                raise RuntimeError("Denied note was unexpectedly created on disk")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "tools_advertised": len(tools),
                    "create_status": created.get("status"),
                    "update_status": updated.get("status"),
                    "denied_write": denied_write,
                    "note": args.note,
                },
                indent=2,
                sort_keys=True,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--note")
    parser.add_argument(
        "--denied-note",
        help="Optional caller-supplied path known to be outside the server write scope",
    )
    parser.add_argument("--vault-path", type=Path)
    args = parser.parse_args()
    args.api_key = os.environ.get("OBSIDIAN_MCP_API_KEY")
    if not args.api_key:
        args.api_key = getpass.getpass("Obsidian MCP API key: ")
    args.note = args.note or f"AI-Memory/mcp-smoke-test-{uuid4().hex}.md"
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
