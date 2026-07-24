from pathlib import Path

import pytest

from second_brain_mcp.domain.index import IndexBuildingError, VaultIndex

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
