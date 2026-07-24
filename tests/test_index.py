from pathlib import Path

import pytest

from obsidian_mcp.domain.index import IndexBuildingError, VaultIndex

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"


def test_index_build_and_backlinks():
    idx = VaultIndex(FIXTURES)
    idx.build()
    assert idx.is_ready()


def test_index_not_ready_raises():
    idx = VaultIndex(FIXTURES)
    with pytest.raises(IndexBuildingError):
        idx.get_backlinks("simple.md")


def test_backlinks_detected(tmp_path):
    (tmp_path / "a.md").write_text("links to [[b]]")
    (tmp_path / "b.md").write_text("# B\nno links")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert "a.md" in idx.get_backlinks("b")


def test_tags_indexed(tmp_path):
    (tmp_path / "tagged.md").write_text("---\ntags: [project]\n---\nBody")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert "tagged.md" in idx.get_notes_by_tag("project")


def test_incremental_update(tmp_path):
    note_a = tmp_path / "a.md"
    note_a.write_text("links to [[b]]")
    (tmp_path / "b.md").write_text("# B")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert "a.md" in idx.get_backlinks("b")

    # Remove the link
    note_a.write_text("no links anymore")
    idx.update("a.md")
    assert "a.md" not in idx.get_backlinks("b")


def test_remove_cleans_up(tmp_path):
    (tmp_path / "a.md").write_text("links to [[target]]")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert "a.md" in idx.get_backlinks("target")
    idx.remove("a.md")
    assert "a.md" not in idx.get_backlinks("target")


def test_exclude_paths(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.md").write_text("secret links to [[other]]")
    (tmp_path / "other.md").write_text("other")
    idx = VaultIndex(tmp_path, exclude_paths=["private"])
    idx.build()
    # secret.md should not appear as a backlink because it was excluded
    assert "private/secret.md" not in idx.get_backlinks("other")


# ── alias resolution ──────────────────────────────────────────────────────

def test_alias_resolution(tmp_path):
    (tmp_path / "Real Note.md").write_text("---\naliases: [Kurzname, KN]\n---\nContent")
    (tmp_path / "linker.md").write_text("Links to [[Kurzname]]")
    idx = VaultIndex(tmp_path)
    idx.build()
    # Backlink via alias should resolve to the real file
    assert "linker.md" in idx.get_backlinks("Real Note.md")


def test_resolve_alias_returns_real_path(tmp_path):
    (tmp_path / "Python Tips.md").write_text("---\naliases: [Python]\n---\n")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.resolve_alias("Python") == "Python Tips.md"


def test_resolve_alias_case_insensitive(tmp_path):
    (tmp_path / "MyNote.md").write_text("---\naliases: [MyAlias]\n---\n")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.resolve_alias("myalias") == "MyNote.md"
    assert idx.resolve_alias("MYALIAS") == "MyNote.md"


def test_resolve_unknown_returns_none(tmp_path):
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.resolve_alias("NonExistent") is None


def test_remove_cleans_alias(tmp_path):
    (tmp_path / "note.md").write_text("---\naliases: [MyAlias]\n---\n")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.resolve_alias("MyAlias") == "note.md"
    idx.remove("note.md")
    assert idx.resolve_alias("MyAlias") is None


# ── get_all_notes / has_note ──────────────────────────────────────────────

def test_get_all_notes(tmp_path):
    (tmp_path / "a.md").write_text("A")
    (tmp_path / "b.md").write_text("B")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.get_all_notes() == {"a.md", "b.md"}


def test_get_all_notes_excludes_excluded(tmp_path):
    (tmp_path / "a.md").write_text("A")
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.md").write_text("S")
    idx = VaultIndex(tmp_path, exclude_paths=["private"])
    idx.build()
    assert "a.md" in idx.get_all_notes()
    assert "private/secret.md" not in idx.get_all_notes()


def test_has_note_by_stem(vault_factory):
    idx = vault_factory({"My Note.md": "content"})
    assert idx.has_note("My Note")


def test_has_note_by_path(vault_factory):
    idx = vault_factory({"Folder/Note.md": "content"})
    assert idx.has_note("Folder/Note.md")


def test_has_note_by_path_without_ext(vault_factory):
    idx = vault_factory({"Folder/Note.md": "content"})
    assert idx.has_note("Folder/Note")


def test_has_note_not_found(vault_factory):
    idx = vault_factory({"real.md": "content"})
    assert not idx.has_note("ghost")


# ── block index ───────────────────────────────────────────────────────────

def test_block_index(tmp_path):
    (tmp_path / "note.md").write_text("Some text here. ^my-block\nOther line.")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.get_block("note.md", "my-block") == 1


def test_block_index_unknown_returns_none(tmp_path):
    (tmp_path / "note.md").write_text("No blocks.")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.get_block("note.md", "nonexistent") is None


# ── tag tree ──────────────────────────────────────────────────────────────

def test_tag_tree_flat(tmp_path):
    (tmp_path / "a.md").write_text("---\ntags: [foo]\n---\n")
    (tmp_path / "b.md").write_text("---\ntags: [bar]\n---\n")
    idx = VaultIndex(tmp_path)
    idx.build()
    tree = idx.get_tag_tree()
    assert "foo" in tree
    assert "bar" in tree


def test_tag_tree_nested(tmp_path):
    (tmp_path / "a.md").write_text("---\ntags: [konzept/python]\n---\n")
    (tmp_path / "b.md").write_text("---\ntags: [konzept/ki/llm]\n---\n")
    idx = VaultIndex(tmp_path)
    idx.build()
    tree = idx.get_tag_tree()
    assert "konzept" in tree
    assert "python" in tree["konzept"]
    assert "ki" in tree["konzept"]


# ── outlinks ──────────────────────────────────────────────────────────────

def test_get_outlinks(tmp_path):
    (tmp_path / "a.md").write_text("Links to [[b]] and [[c]]")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.get_outlinks("a.md") == {"b", "c"}
