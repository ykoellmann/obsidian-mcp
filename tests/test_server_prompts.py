"""Tests for the MCP Prompts registered on the server (weekly_review, daily_note)."""
from __future__ import annotations

import pytest
from fastmcp import Client

from obsidian_mcp import server


@pytest.mark.asyncio
async def test_prompts_are_listed():
    async with Client(server.mcp) as client:
        prompts = await client.list_prompts()

    names = {p.name for p in prompts}
    assert {"weekly_review", "daily_note"} <= names


@pytest.mark.asyncio
async def test_weekly_review_prompt_mentions_get_tasks_tool():
    async with Client(server.mcp) as client:
        result = await client.get_prompt("weekly_review")

    text = result.messages[0].content.text
    assert "get_tasks_tool" in text


@pytest.mark.asyncio
async def test_daily_note_prompt_uses_date_arg():
    async with Client(server.mcp) as client:
        result = await client.get_prompt("daily_note", {"date": "2026-01-01"})

    text = result.messages[0].content.text
    assert "2026-01-01" in text
    assert "get_periodic_note_tool" in text
    assert "get_daily_note_tool" not in text
    assert "get_tasks_tool" not in text
    assert "done=false" in text


@pytest.mark.asyncio
async def test_daily_note_prompt_defaults_to_today():
    async with Client(server.mcp) as client:
        result = await client.get_prompt("daily_note")

    text = result.messages[0].content.text
    assert "'today'" in text
