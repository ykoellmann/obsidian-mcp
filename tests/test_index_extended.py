

from second_brain_mcp.domain.index import VaultIndex


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


def test_remove_cleans_alias(tmp_path):
    (tmp_path / "note.md").write_text("---\naliases: [MyAlias]\n---\n")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.resolve_alias("MyAlias") == "note.md"
    idx.remove("note.md")
    assert idx.resolve_alias("MyAlias") is None


def test_get_outlinks(tmp_path):
    (tmp_path / "a.md").write_text("Links to [[b]] and [[c]]")
    idx = VaultIndex(tmp_path)
    idx.build()
    assert idx.get_outlinks("a.md") == {"b", "c"}
