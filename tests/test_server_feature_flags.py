"""Tests that the optional plugin-format tool groups (Canvas/Excalidraw/Kanban/
Bases) are only registered on the FastMCP server when their ENABLE_* flag is
set — disabled tools must not appear in the client's tool list at all."""
from __future__ import annotations

import importlib

import pytest

import obsidian_mcp.config as cfg_mod
import obsidian_mcp.server as server_mod

_FLAG_ENV = (
    "ENABLE_CANVAS", "ENABLE_EXCALIDRAW", "ENABLE_KANBAN", "ENABLE_BASES",
    "ENABLE_MOVE", "ENABLE_FOLDER_RENAME", "ENABLE_BULK_REPLACE", "ENABLE_DELETE",
)

_GROUP_TOOLS = {
    "canvas": {"list_canvases_tool", "read_canvas_tool", "write_canvas_tool", "patch_canvas_tool"},
    "excalidraw": {
        "list_excalidraw_tool", "read_excalidraw_tool",
        "write_excalidraw_tool", "patch_excalidraw_tool",
    },
    "kanban": {
        "read_kanban_tool", "create_kanban_board_tool",
        "add_kanban_card_tool", "move_kanban_card_tool", "delete_kanban_card_tool",
    },
    "bases": {"list_bases_tool", "read_base_tool", "write_base_tool", "patch_base_tool"},
}

_HIGH_RISK_TOOLS = {
    "move": {"move_note_tool"},
    "folder_rename": {"rename_folder_tool"},
    "bulk_replace": {"find_replace_in_vault_tool"},
    "delete": {
        "delete_note_tool",
        "delete_folder_tool",
        "list_trash_tool",
        "restore_note_tool",
        "restore_folder_tool",
    },
}


def _reload_server(monkeypatch, **flags: bool):
    for name in _FLAG_ENV:
        monkeypatch.delenv(name, raising=False)
    for group, enabled in flags.items():
        monkeypatch.setenv(f"ENABLE_{group.upper()}", "true" if enabled else "false")
    cfg_mod._config = None
    return importlib.reload(server_mod)


@pytest.mark.asyncio
async def test_all_flags_disabled_by_default(monkeypatch):
    server = _reload_server(monkeypatch)
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    for group_tools in _GROUP_TOOLS.values():
        assert names.isdisjoint(group_tools)
    for group_tools in _HIGH_RISK_TOOLS.values():
        assert names.isdisjoint(group_tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag, tools_for_flag", _HIGH_RISK_TOOLS.items())
async def test_removed_high_risk_flags_do_not_register_tools(monkeypatch, flag, tools_for_flag):
    server = _reload_server(monkeypatch, **{flag: True})
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert names.isdisjoint(tools_for_flag)
    for other_flag, other_tools in _HIGH_RISK_TOOLS.items():
        if other_flag != flag:
            assert names.isdisjoint(other_tools)


@pytest.mark.asyncio
async def test_bases_flag_enables_only_bases_tools(monkeypatch):
    server = _reload_server(monkeypatch, bases=True)
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert _GROUP_TOOLS["bases"] <= names
    assert names.isdisjoint(_GROUP_TOOLS["canvas"])
    assert names.isdisjoint(_GROUP_TOOLS["excalidraw"])
    assert names.isdisjoint(_GROUP_TOOLS["kanban"])


@pytest.mark.asyncio
async def test_all_flags_enabled(monkeypatch):
    server = _reload_server(monkeypatch, canvas=True, excalidraw=True, kanban=True, bases=True)
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    for group_tools in _GROUP_TOOLS.values():
        assert group_tools <= names


@pytest.fixture(autouse=True)
def _restore_server_module():
    """Reload server.py once more after each test so later test modules
    (which import `obsidian_mcp.server` expecting default env) get a clean
    module state instead of whatever flags the last test here left behind."""
    yield
    for name in _FLAG_ENV:
        import os
        os.environ.pop(name, None)
    cfg_mod._config = None
    importlib.reload(server_mod)
