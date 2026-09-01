"""MCP Prompts: reusable instruction templates for common vault workflows.

Unlike tools, these don't touch the vault themselves — they return an
instruction string that tells the calling LLM which existing tools to call
and in what order, so a client can offer them as one-click starting points
instead of the user having to spell the workflow out each time.
"""
from __future__ import annotations


def weekly_review_prompt() -> str:
    return """\
Do a weekly review of this Obsidian vault:

1. Call get_tasks_tool(status="open") and pick out anything with a due date
   (`due`) in the last 7 days, plus anything overdue.
2. Find this week's daily notes: call get_periodic_note_tool(period="daily", date=...)
   for each
   of the last 7 days (today and the 6 before it); skip days with no note.
3. Summarize: what got done (completed tasks/notable journal entries), what's
   still open and overdue, and any recurring themes across the week's notes.
4. Ask the user whether to write the summary into a new note (e.g. under a
   Journal/Weekly/ path if the vault uses one — check get_vault_conventions_tool
   first) before creating anything.
"""


def daily_note_prompt(date: str = "today") -> str:
    return f"""\
Prepare the daily note for date={date!r}:

1. Call get_periodic_note_tool(period="daily", date={date!r}). If it already
   exists, just show
   its content and open tasks.
2. If it doesn't exist, check get_vault_conventions_tool for this vault's
   daily-note template/path conventions, then call
   create_from_template_tool with the daily template if one exists —
   otherwise write_note_tool with a minimal structure (heading + empty task
   list) at the conventional path.
3. Call get_periodic_note_tool with period="daily" and the prior date. Copy
   the entries from its tasks result where done=false into today's note, so
   nothing open silently falls off.
"""
