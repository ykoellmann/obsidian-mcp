"""MCP Prompts: reusable instruction templates for common vault workflows.

Unlike tools, these don't touch the vault themselves — they return an
instruction string that tells the calling LLM which existing tools to call
and in what order, so a client can offer them as one-click starting points
instead of the user having to spell the workflow out each time.
"""
from __future__ import annotations


def weekly_review_prompt() -> str:
    return """Review this vault: call list_vaults, then read_file for _AI_INSTRUCTIONS.md if present.
Use get_tasks(status="open") for overdue/due-soon work. Find the last seven days of daily notes using the documented date/path conventions, list_files and read_files. If conventions are absent, ask for the daily-note path/timezone rather than inventing one.
Summarize completed work, open work and themes. Write a summary with create_file only if requested.
"""


def daily_note_prompt(date: str = "today") -> str:
    return f"""Prepare the daily note for date={date!r}.
Call list_vaults and read_file for _AI_INSTRUCTIONS.md if present. Establish the daily-note path, timezone and template from user conventions; ask if absent. Read any specified template with read_file.
Read the existing daily note if present; otherwise use create_file without overwriting. Use read_files for today/yesterday when paths are known, and consider carrying forward open tasks (done=false) without duplicating existing entries. Use revision-bound patch_file or append_file for authorized additions. Preserve raw Markdown; do not invent a title H1.
"""
