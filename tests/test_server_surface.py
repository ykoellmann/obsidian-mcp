"""The one public tool surface and its MCP wire contract."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastmcp import Client

from obsidian_mcp import server
from obsidian_mcp.tools import canonical as c

NAMES = {
    "list_vaults",
    "list_files",
    "search_files",
    "read_file",
    "read_files",
    "get_file_outline",
    "read_frontmatter",
    "list_attachments",
    "read_attachment",
    "create_file",
    "edit_file",
    "append_file",
    "patch_file",
    "patch_frontmatter",
    "get_backlinks",
    "get_tasks",
    "add_attachment",
}
SNAPSHOT = Path(__file__).parent / "snapshots/canonical_input_schemas.json"


@pytest.mark.asyncio
async def test_surface_and_schemas():
    tools = await server.mcp.list_tools()
    assert {tool.name for tool in tools} == NAMES
    assert {tool.name: tool.parameters for tool in tools} == json.loads(SNAPSHOT.read_text())
    for tool in tools:
        if tool.name != "list_vaults":
            assert "vault" in tool.parameters["properties"]
        assert "_tool" not in (tool.description or "")
    assert "_tool" not in server.mcp.instructions


@pytest.mark.asyncio
async def test_tools_raw_workflow_and_errors(vault_factory, monkeypatch):
    monkeypatch.setenv("READ_ONLY", "false")
    index = vault_factory({"n.md": "---\ntags: [a, b]\n---\nBudget meeting"})
    monkeypatch.setattr(server, "_indices", {"default": index})
    async with Client(server.mcp) as client:
        read = await client.call_tool("read_file", {"path": "n.md"})
        assert read.structured_content["content"].startswith("---\n")
        assert json.loads(read.content[0].text) == read.structured_content
        rev = read.structured_content["revision"]
        search = await client.call_tool(
            "search_files",
            {"filters": [{"property": "tags", "operator": "contains", "value": "a"}]},
        )
        assert search.structured_content["results"][0]["path"] == "n.md"
        patch = await client.call_tool(
            "patch_frontmatter",
            {"path": "n.md", "updates": {"tags": ["a"]}, "expectedRevision": rev},
        )
        assert patch.structured_content["updated"] == ["tags"]
        stale = await client.call_tool(
            "edit_file",
            {"path": "n.md", "content": "oops", "expectedRevision": rev},
            raise_on_error=False,
        )
        assert stale.is_error and stale.structured_content["error"]["code"] == "revision_conflict"
        invalid = await client.call_tool(
            "read_file", {"path": "n.md", "unexpected": True}, raise_on_error=False
        )
        assert invalid.is_error and invalid.structured_content["error"]["code"] == "invalid_input"
        batch = await client.call_tool(
            "read_files", {"files": [{"path": "n.md"}, {"path": "missing.md"}]}
        )
        assert batch.structured_content["files"][1]["result"]["error"]["code"] == "not_found"
        missing = await client.call_tool(
            "edit_file", {"path": "n.md", "content": "x"}, raise_on_error=False
        )
        assert missing.is_error
        readonly = await client.call_tool(
            "search_files", {"query": "x", "limit": 0}, raise_on_error=False
        )
        assert readonly.is_error


def test_conventions_are_not_embedded(vault_factory):
    vault_factory({"_AI_INSTRUCTIONS.md": "Private instructions"})
    assert "Private instructions" not in server._load_instructions()


@pytest.mark.asyncio
async def test_old_profile_setting_does_not_restore_legacy(monkeypatch):
    monkeypatch.setenv("TOOL_PROFILE", "full")
    module = importlib.reload(server)
    assert {tool.name for tool in await module.mcp.list_tools()} == NAMES


@pytest.mark.asyncio
async def test_wire_budget_uses_actual_representation():
    from obsidian_mcp.canonical_server import result

    data = {"content": '😀\n"'}
    encoded = result(data)
    assert c.wire_size(data) == len(
        c.dumps(
            {
                "content": [{"type": "text", "text": encoded.content[0].text}],
                "structuredContent": encoded.structured_content,
            }
        ).encode()
    )


@pytest.mark.asyncio
async def test_multi_vault_batch_and_cursor_isolation(tmp_path, monkeypatch):
    from fastmcp.server.auth import AccessToken

    import obsidian_mcp.config as cfg
    from obsidian_mcp.domain.index import VaultIndex

    vaults = {}
    indices = {}
    for name in ["a", "b"]:
        root = tmp_path / name
        root.mkdir()
        for note in ["one.md", "two.md"]:
            (root / note).write_text(name)
        vaults[name] = {"path": str(root)}
        indices[name] = VaultIndex(root)
        indices[name].build()
    path = tmp_path / "vaults.json"
    path.write_text(
        json.dumps(
            {
                "vaults": vaults,
                "identities": [{"type": "api_key", "value": "both", "vaults": ["a", "b"]}],
            }
        )
    )
    monkeypatch.setenv("VAULTS_CONFIG", str(path))
    monkeypatch.setenv("TRANSPORT", "http")
    monkeypatch.setenv("LOCK_PATH", str(tmp_path / "locks"))
    monkeypatch.setattr(cfg, "_config", None)
    monkeypatch.setattr(server, "_indices", indices)
    monkeypatch.setattr(
        server, "get_access_token", lambda: AccessToken(token="both", client_id="both", scopes=[])
    )
    async with Client(server.mcp) as client:
        listed = await client.call_tool("list_vaults", {})
        assert {v["name"] for v in listed.structured_content["vaults"]} == {"a", "b"}
        first = await client.call_tool("list_files", {"vault": "a", "limit": 1})
        cursor = first.structured_content["cursor"]
        mismatch = await client.call_tool(
            "list_files", {"vault": "b", "cursor": cursor}, raise_on_error=False
        )
        assert mismatch.is_error and mismatch.structured_content["error"]["code"] == "invalid_input"
        batch = await client.call_tool(
            "read_files", {"vault": "b", "files": [{"path": "one.md"}, {"path": "two.md"}]}
        )
        assert [r["result"]["data"]["content"] for r in batch.structured_content["files"]] == [
            "b",
            "b",
        ]
        missing_vault = await client.call_tool(
            "read_file", {"path": "one.md"}, raise_on_error=False
        )
        assert missing_vault.is_error
