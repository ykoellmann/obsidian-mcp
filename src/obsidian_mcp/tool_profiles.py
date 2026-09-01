"""Public MCP tool profiles.

Profiles control which tools are advertised to clients.  They are a usability
feature, not an authorization boundary; the normal read-only and path-policy
checks continue to govern every underlying operation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final, Literal, TypeVar

ToolProfile = Literal["full", "focused"]

FOCUSED_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        # Orientation, reading, and discovery.
        "list_vaults_tool",
        "get_vault_conventions_tool",
        "list_notes_tool",
        "list_folder_tool",
        "read_note_tool",
        "get_note_outline_tool",
        "search_notes_tool",
        "find_similar_notes_tool",
        "query_notes_tool",
        # Single-note writing.
        "write_note_tool",
        "patch_note_tool",
        "patch_note_text_tool",
        "append_to_note_tool",
        "patch_frontmatter_tool",
        "manage_tags_tool",
        "create_folder_tool",
        # Semantic vault operations.
        "get_backlinks_tool",
        "get_broken_links_tool",
        "get_orphans_tool",
        "get_link_graph_tool",
        "get_tasks_tool",
        "get_periodic_note_tool",
        "lint_schema_tool",
        "get_vault_stats_tool",
        "list_all_tags_tool",
        # Templates.
        "list_templates_tool",
        "create_from_template_tool",
        # Attachments.
        "list_attachments_tool",
        "read_attachment_tool",
        "add_attachment_tool",
        "create_attachment_token_tool",
    }
)

# Optional format groups are independently and explicitly enabled.  Once a
# group is enabled, its tools are visible in either profile.
OPTIONAL_FORMAT_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "list_canvases_tool",
        "read_canvas_tool",
        "write_canvas_tool",
        "patch_canvas_tool",
        "list_excalidraw_tool",
        "read_excalidraw_tool",
        "write_excalidraw_tool",
        "patch_excalidraw_tool",
        "read_kanban_tool",
        "create_kanban_board_tool",
        "add_kanban_card_tool",
        "move_kanban_card_tool",
        "delete_kanban_card_tool",
        "list_bases_tool",
        "read_base_tool",
        "write_base_tool",
        "patch_base_tool",
    }
)

# High-impact mutations are also independently enabled. Their feature flags
# decide whether they are registered; profile selection must not silently
# override that explicit operator choice.
OPTIONAL_MUTATION_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "move_note_tool",
        "rename_folder_tool",
        "find_replace_in_vault_tool",
        "delete_note_tool",
        "restore_note_tool",
        "delete_folder_tool",
        "restore_folder_tool",
        "list_trash_tool",
    }
)


def is_tool_visible(name: str, profile: ToolProfile) -> bool:
    return (
        profile == "full"
        or name in FOCUSED_TOOL_NAMES
        or name in OPTIONAL_FORMAT_TOOL_NAMES
        or name in OPTIONAL_MUTATION_TOOL_NAMES
    )


_F = TypeVar("_F", bound=Callable[..., Any])


def profile_tool_decorator(mcp: Any, profile: ToolProfile) -> Callable[[], Callable[[_F], _F]]:
    """Return a drop-in ``@mcp.tool()`` decorator filtered by *profile*.

    Hidden functions are returned untouched so direct Python callers and
    their tests retain the same implementation and metadata.
    """

    def tool() -> Callable[[_F], _F]:
        def decorate(function: _F) -> _F:
            if is_tool_visible(function.__name__, profile):
                return mcp.tool()(function)
            return function

        return decorate

    return tool
