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
EXPECTED_CREATED_CONTENT = CREATED_CONTENT
EXPECTED_UPDATED_CONTENT = UPDATED_CONTENT


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
        required = {"list_files", "read_file", "create_file"}
        missing = required - tools
        if missing:
            raise RuntimeError(f"Missing required tools: {sorted(missing)}")

        created = _structured(
            await client.call_tool(
                "create_file",
                {"path": args.note, "content": CREATED_CONTENT},
            )
        )
        first_read = _structured(await client.call_tool("read_file", {"path": args.note}))
        if first_read.get("content") != EXPECTED_CREATED_CONTENT:
            raise RuntimeError("Created note did not round-trip through read_file")

        duplicate = await client.call_tool(
            "create_file",
            {"path": args.note, "content": "must not overwrite"},
            raise_on_error=False,
        )
        _require_rejection(duplicate, "Duplicate create")
        outline = _structured(await client.call_tool("get_file_outline", {"path": args.note}))
        heading = outline["headings"][0]
        section = _structured(
            await client.call_tool(
                "read_file",
                {
                    "path": args.note,
                    "startLine": heading["startLine"],
                    "endLine": heading["endLine"],
                    "expectedRevision": outline["revision"],
                },
            )
        )
        if section["content"] != CREATED_CONTENT:
            raise RuntimeError("Outline/range round trip failed")
        batch = _structured(
            await client.call_tool(
                "read_files", {"files": [{"path": args.note}, {"path": args.note}]}
            )
        )
        if any(item["result"]["data"]["content"] != CREATED_CONTENT for item in batch["files"]):
            raise RuntimeError("Batch read failed")
        appended = _structured(
            await client.call_tool(
                "append_file",
                {
                    "path": args.note,
                    "content": "\nFinding\n",
                    "expectedRevision": first_read["revision"],
                },
            )
        )
        retry = await client.call_tool(
            "append_file",
            {
                "path": args.note,
                "content": "\nFinding\n",
                "expectedRevision": first_read["revision"],
            },
            raise_on_error=False,
        )
        _require_rejection(retry, "Stale append retry")
        patched = _structured(
            await client.call_tool(
                "patch_file",
                {
                    "path": args.note,
                    "oldText": "Finding",
                    "newText": "Checked finding",
                    "expectedRevision": appended["revision"],
                },
            )
        )
        _structured(
            await client.call_tool(
                "patch_frontmatter",
                {
                    "path": args.note,
                    "updates": {"smoke": True},
                    "expectedRevision": patched["revision"],
                },
            )
        )
        found = _structured(
            await client.call_tool(
                "search_files",
                {
                    "filters": [{"property": "smoke", "operator": "eq", "value": True}],
                    "pathPrefix": args.note,
                    "properties": ["smoke"],
                },
            )
        )
        if not found["results"] or found["results"][0]["properties"] != {"smoke": True}:
            raise RuntimeError("Property search failed")
        listing = _structured(await client.call_tool("list_files", {"prefix": args.note}))
        if listing["files"][0]["path"] != args.note:
            raise RuntimeError("Listing failed")
        complete = _structured(await client.call_tool("read_file", {"path": args.note}))
        if complete["content"].count("Checked finding") != 1:
            raise RuntimeError("Append retry duplicated content")

        updated = _structured(
            await client.call_tool(
                "edit_file",
                {
                    "path": args.note,
                    "content": UPDATED_CONTENT,
                    "expectedRevision": complete["revision"],
                },
            )
        )
        final_read = _structured(await client.call_tool("read_file", {"path": args.note}))
        if final_read.get("content") != EXPECTED_UPDATED_CONTENT:
            raise RuntimeError("Updated note did not round-trip through read_file")

        denied_write = "not_requested"
        if args.denied_note:
            denied_result = await client.call_tool(
                "create_file",
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
                    "created_revision": created["revision"],
                    "updated_revision": updated["revision"],
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
