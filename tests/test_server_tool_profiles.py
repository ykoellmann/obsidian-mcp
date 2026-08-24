"""Exact public tool surfaces for the full and focused profiles."""

from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path

import pytest
from fastmcp import Client

import obsidian_mcp.config as cfg_mod
import obsidian_mcp.server as server_mod
from obsidian_mcp.config import ConfigError
from obsidian_mcp.tool_profiles import FOCUSED_TOOL_NAMES

BASE_FULL_TOOL_NAMES = {
    "list_notes_tool", "read_note_tool", "search_notes_tool", "render_note_tool",
    "get_note_outline_tool", "find_similar_notes_tool", "write_note_tool",
    "patch_note_tool", "patch_note_text_tool", "delete_note_tool",
    "restore_note_tool", "find_replace_in_vault_tool", "append_to_note_tool",
    "patch_frontmatter_tool", "patch_frontmatter_batch_tool", "manage_tags_tool",
    "move_note_tool", "get_backlinks_tool", "get_notes_by_tag_tool",
    "get_vault_conventions_tool", "get_audit_log_tool", "get_note_history_tool",
    "list_vaults_tool", "lint_schema_tool", "get_broken_links_tool",
    "get_orphans_tool", "get_link_graph_tool", "get_vault_stats_tool",
    "get_tag_tree_tool", "list_all_tags_tool", "get_tasks_tool",
    "get_daily_note_tool", "get_periodic_note_tool", "resolve_alias_tool",
    "query_notes_tool", "list_attachments_tool", "read_attachment_tool",
    "add_attachment_tool", "create_attachment_token_tool", "list_templates_tool",
    "create_from_template_tool", "list_folder_tool", "list_files_tool",
    "create_folder_tool", "delete_folder_tool", "rename_folder_tool",
    "list_trash_tool", "restore_folder_tool",
}

OPTIONAL_GROUPS = {
    "canvas": {"list_canvases_tool", "read_canvas_tool", "write_canvas_tool", "patch_canvas_tool"},
    "excalidraw": {
        "list_excalidraw_tool", "read_excalidraw_tool", "write_excalidraw_tool",
        "patch_excalidraw_tool",
    },
    "kanban": {
        "read_kanban_tool", "create_kanban_board_tool", "add_kanban_card_tool",
        "move_kanban_card_tool", "delete_kanban_card_tool",
    },
    "bases": {"list_bases_tool", "read_base_tool", "write_base_tool", "patch_base_tool"},
}

_PROFILE_ENV = ("TOOL_PROFILE", "ENABLE_CANVAS", "ENABLE_EXCALIDRAW", "ENABLE_KANBAN", "ENABLE_BASES")
_SCHEMA_SNAPSHOT = Path(__file__).parent / "snapshots" / "tool_profile_input_schemas.json"


def _reload_server(monkeypatch, profile: str = "full", **flags: bool):
    for name in _PROFILE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TOOL_PROFILE", profile)
    for group, enabled in flags.items():
        monkeypatch.setenv(f"ENABLE_{group.upper()}", "true" if enabled else "false")
    cfg_mod._config = None
    return importlib.reload(server_mod)


async def _tool_names(server) -> set[str]:
    return {tool.name for tool in await server.mcp.list_tools()}


@pytest.mark.asyncio
async def test_default_full_profile_has_exact_legacy_surface(monkeypatch):
    monkeypatch.delenv("TOOL_PROFILE", raising=False)
    for group in OPTIONAL_GROUPS:
        monkeypatch.delenv(f"ENABLE_{group.upper()}", raising=False)
    server = importlib.reload(server_mod)
    assert await _tool_names(server) == BASE_FULL_TOOL_NAMES


@pytest.mark.asyncio
async def test_focused_profile_has_exact_documented_surface(monkeypatch):
    server = _reload_server(monkeypatch, "focused")
    assert await _tool_names(server) == FOCUSED_TOOL_NAMES


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "focused"])
async def test_profile_input_schema_snapshot(monkeypatch, profile):
    server = _reload_server(monkeypatch, profile)
    actual = {
        tool.name: tool.parameters
        for tool in sorted(await server.mcp.list_tools(), key=lambda item: item.name)
    }
    expected = json.loads(_SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))[profile]
    assert actual == expected


@pytest.mark.asyncio
async def test_aliases_are_hidden_only_in_focused(monkeypatch):
    aliases = {"get_daily_note_tool", "get_note_history_tool", "get_notes_by_tag_tool"}
    focused = await _tool_names(_reload_server(monkeypatch, "focused"))
    full = await _tool_names(_reload_server(monkeypatch, "full"))
    assert focused.isdisjoint(aliases)
    assert aliases <= full


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "focused"])
@pytest.mark.parametrize("group", sorted(OPTIONAL_GROUPS))
async def test_optional_groups_compose_with_both_profiles(monkeypatch, profile, group):
    server = _reload_server(monkeypatch, profile, **{group: True})
    expected_base = BASE_FULL_TOOL_NAMES if profile == "full" else FOCUSED_TOOL_NAMES
    assert await _tool_names(server) == expected_base | OPTIONAL_GROUPS[group]


def test_unknown_profile_fails_during_server_import(monkeypatch):
    monkeypatch.setenv("TOOL_PROFILE", "unknown")
    with pytest.raises(ConfigError, match="Invalid TOOL_PROFILE"):
        importlib.reload(server_mod)


@pytest.mark.asyncio
async def test_focused_instructions_only_reference_visible_tools(monkeypatch):
    server = _reload_server(monkeypatch, "focused")
    names = await _tool_names(server)
    referenced = set(re.findall(r"\b[a-z][a-z0-9_]*_tool\b", server.mcp.instructions))
    assert referenced <= names


@pytest.mark.asyncio
async def test_focused_tool_descriptions_only_reference_visible_tools(monkeypatch):
    server = _reload_server(monkeypatch, "focused")
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}
    referenced = set()
    for tool in tools:
        referenced.update(re.findall(r"\b[a-z][a-z0-9_]*_tool\b", tool.description or ""))
    assert referenced <= names


@pytest.mark.asyncio
async def test_focused_prompts_only_reference_visible_tools(monkeypatch):
    server = _reload_server(monkeypatch, "focused")
    names = await _tool_names(server)
    async with Client(server.mcp) as client:
        weekly = await client.get_prompt("weekly_review")
        daily = await client.get_prompt("daily_note")
    text = weekly.messages[0].content.text + daily.messages[0].content.text
    referenced = set(re.findall(r"\b[a-z][a-z0-9_]*_tool\b", text))
    assert referenced <= names


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "focused"])
async def test_every_routed_tool_advertises_vault_argument(monkeypatch, profile):
    server = _reload_server(monkeypatch, profile)
    tools = await server.mcp.list_tools()
    for tool in tools:
        if tool.name != "list_vaults_tool":
            assert "vault" in tool.parameters["properties"], tool.name


def test_hidden_tools_remain_directly_callable(monkeypatch):
    server = _reload_server(monkeypatch, "focused")
    assert callable(server.get_daily_note_tool)
    assert callable(server.get_note_history_tool)
    assert callable(server.get_notes_by_tag_tool)


@pytest.fixture(autouse=True)
def _restore_default_server():
    yield
    for name in _PROFILE_ENV:
        os.environ.pop(name, None)
    cfg_mod._config = None
    importlib.reload(server_mod)
