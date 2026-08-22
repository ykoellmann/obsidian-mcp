"""Cross-tool authorization matrix for Phase 1's single policy boundary."""

from __future__ import annotations

import base64

import httpx
import pytest

import obsidian_mcp.config as cfg_mod
from obsidian_mcp import server
from obsidian_mcp.storage.policy import ReadPermissionError, WritePermissionError
from obsidian_mcp.tools.attachments import (
    add_attachment,
    create_attachment_token,
    list_attachments,
    read_attachment,
)
from obsidian_mcp.tools.bases import read_base, write_base
from obsidian_mcp.tools.canvas import read_canvas, write_canvas
from obsidian_mcp.tools.excalidraw import read_excalidraw, write_excalidraw
from obsidian_mcp.tools.folders import (
    create_folder,
    delete_folder,
    list_folder,
    rename_folder,
    restore_folder,
)
from obsidian_mcp.tools.kanban import create_kanban_board, read_kanban
from obsidian_mcp.tools.read import get_note_outline, list_notes, read_note
from obsidian_mcp.tools.templates import create_from_template
from obsidian_mcp.tools.write import (
    append_to_note,
    delete_note,
    find_replace_in_vault,
    manage_tags,
    move_note,
    patch_frontmatter,
    patch_note,
    restore_note,
    write_note,
)


def _configure_scoped_policy(monkeypatch, lock_path=None):
    monkeypatch.setenv("WRITE_PATHS", "allowed")
    monkeypatch.setenv("DENY_READ_PATHS", "denied,.trash")
    monkeypatch.setenv("API_KEY", "matrix-key")
    if lock_path is not None:
        monkeypatch.setenv("LOCK_PATH", str(lock_path))
    cfg_mod._config = None


@pytest.mark.parametrize(
    ("name", "operation"),
    [
        ("write_note", lambda: write_note("outside.md", "# changed")),
        ("patch_note", lambda: patch_note("outside.md", "Heading", "changed")),
        ("append_to_note", lambda: append_to_note("outside.md", "changed")),
        ("patch_frontmatter", lambda: patch_frontmatter("outside.md", {"x": 1})),
        ("manage_tags", lambda: manage_tags("outside.md", add=["private"])),
        ("delete_note", lambda: delete_note("outside.md")),
        ("restore_note", lambda: restore_note("missing.md", "outside.md")),
        ("move_note", lambda: move_note("outside.md", "allowed/moved.md")),
        ("create_from_template", lambda: create_from_template("allowed/template.md", "outside.md")),
        ("create_folder", lambda: create_folder("outside-folder")),
        ("delete_folder", lambda: delete_folder("outside-folder")),
        ("rename_folder", lambda: rename_folder("outside-folder", "allowed/moved-folder")),
        ("restore_folder", lambda: restore_folder("missing-folder", "outside-folder")),
        ("add_attachment", lambda: add_attachment("outside.pdf", base64.b64encode(b"x").decode())),
        ("create_attachment_token", lambda: create_attachment_token("outside.pdf", method="PUT")),
        ("write_canvas", lambda: write_canvas("outside.canvas")),
        ("write_excalidraw", lambda: write_excalidraw("outside.excalidraw.md")),
        ("write_base", lambda: write_base("outside.base")),
        ("create_kanban_board", lambda: create_kanban_board("outside.md", ["Todo"])),
    ],
)
def test_every_mutation_group_rejects_out_of_scope_path(
    name, operation, tmp_path, vault_factory, monkeypatch
):
    vault_factory(
        {
            "outside.md": "# Heading\n",
            "allowed/template.md": "template",
        }
    )
    _configure_scoped_policy(monkeypatch, tmp_path.parent / f"{tmp_path.name}-locks")

    with pytest.raises(WritePermissionError, match="denied"):
        operation()


def test_find_replace_reports_out_of_scope_files_without_writing(tmp_path, vault_factory, monkeypatch):
    vault_factory({"outside.md": "needle\n", "allowed/inside.md": "needle\n"})
    _configure_scoped_policy(monkeypatch, tmp_path.parent / f"{tmp_path.name}-locks")

    result = find_replace_in_vault("needle", "changed", dry_run=False)

    assert result["replaced_in"] == ["allowed/inside.md"]
    assert result["skipped_write_protected"] == ["outside.md"]


@pytest.mark.parametrize(
    ("name", "operation"),
    [
        ("read_note", lambda: read_note("denied/secret.md")),
        ("get_note_outline", lambda: get_note_outline("denied/secret.md")),
        ("read_attachment", lambda: read_attachment("denied/secret.pdf")),
        ("read_canvas", lambda: read_canvas("denied/secret.canvas")),
        ("read_excalidraw", lambda: read_excalidraw("denied/secret.excalidraw.md")),
        ("read_base", lambda: read_base("denied/secret.base")),
        ("read_kanban", lambda: read_kanban("denied/secret.md")),
        ("list_folder", lambda: list_folder("denied")),
        ("list_attachments", lambda: list_attachments("denied")),
    ],
)
def test_every_read_group_rejects_denied_path(name, operation, tmp_path, vault_factory, monkeypatch):
    vault_factory(
        {
            "denied/secret.md": "# secret\n",
            "denied/secret.pdf": "not really a pdf",
            "denied/secret.canvas": "{}",
            "denied/secret.excalidraw.md": "# secret",
            "denied/secret.base": "views: []",
        }
    )
    _configure_scoped_policy(monkeypatch, tmp_path.parent / f"{tmp_path.name}-locks")

    with pytest.raises(ReadPermissionError, match="denied"):
        operation()


def test_list_notes_and_resources_never_expose_denied_content(tmp_path, vault_factory, monkeypatch):
    vault_factory({"denied/secret.md": "secret", "allowed/public.md": "public"})
    _configure_scoped_policy(monkeypatch, tmp_path.parent / f"{tmp_path.name}-locks")

    assert list_notes() == ["allowed/public.md"]
    assert server.vault_note_resource("denied/secret.md") == ""


@pytest.mark.asyncio
async def test_attachment_http_auth_precedes_filesystem_policy(tmp_path, vault_factory, monkeypatch):
    vault_factory({"denied/secret.pdf": "secret"})
    _configure_scoped_policy(monkeypatch, tmp_path.parent / f"{tmp_path.name}-locks")
    transport = httpx.ASGITransport(app=server.mcp.http_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        unauthenticated = await client.get(
            "/attachments/denied/secret.pdf",
            headers={"Authorization": "Bearer wrong-key"},
        )
        authenticated = await client.get(
            "/attachments/denied/secret.pdf",
            headers={"Authorization": "Bearer matrix-key"},
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 403
