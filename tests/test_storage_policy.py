from __future__ import annotations

import os

import pytest

import obsidian_mcp.storage.filesystem as filesystem
from obsidian_mcp.storage.filesystem import VaultStorage
from obsidian_mcp.storage.policy import (
    PermanentDeleteDisabledError,
    ProtectedPathError,
    ReadPermissionError,
    VaultAccessPolicy,
    VaultPathError,
    WritePermissionError,
    matches_path_rule,
)


def test_component_aware_write_allowlist(tmp_path):
    policy = VaultAccessPolicy(tmp_path, write_paths=["AI/"])
    assert policy.resolve_write("AI/note.md").relative == "AI/note.md"
    with pytest.raises(WritePermissionError):
        policy.resolve_write("AI-old/note.md")


def test_file_rule_is_exact_and_directory_rule_is_recursive(tmp_path):
    exact = VaultAccessPolicy(tmp_path, write_paths=["AI/note.md"])
    assert exact.resolve_write("AI/note.md").relative == "AI/note.md"
    with pytest.raises(WritePermissionError):
        exact.resolve_write("AI/note.md/child.md")

    recursive = VaultAccessPolicy(tmp_path, write_paths=["AI/note.md/"])
    assert recursive.resolve_write("AI/note.md/child.md").relative == "AI/note.md/child.md"


def test_path_rules_are_rooted_and_support_multiple_components():
    assert matches_path_rule("Archive/Old", "Archive/Old")
    assert not matches_path_rule("Archive/Old/note.md", "Archive/Old")
    assert matches_path_rule("Archive/Old/note.md", "Archive/Old/")
    assert not matches_path_rule("Projects/Archive/Old/note.md", "Archive/Old/")


def test_deny_rules_cannot_be_bypassed_by_case_alias(tmp_path):
    policy = VaultAccessPolicy(
        tmp_path,
        deny_read_paths=["private/"],
        deny_write_paths=["protected.md"],
    )
    with pytest.raises(ReadPermissionError):
        policy.resolve_read("Private/secret.md")
    with pytest.raises(ProtectedPathError):
        policy.resolve_write("PROTECTED.MD")


def test_deny_read_is_not_bypassable_by_direct_name(tmp_path):
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "secret.md").write_text("secret")
    policy = VaultAccessPolicy(tmp_path, deny_read_paths=["private/"])
    storage = VaultStorage(policy)
    with pytest.raises(ReadPermissionError):
        storage.read_text("private/secret.md")
    assert storage.list_dir("") == []


def test_tree_paths_filters_denied_descendants(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "private").mkdir()
    (tmp_path / "public" / "note.md").write_text("public")
    (tmp_path / "private" / "secret.md").write_text("secret")
    storage = VaultStorage(VaultAccessPolicy(tmp_path, deny_read_paths=["private/"]))

    assert [path.relative for path in storage.tree_paths("")] == ["public", "public/note.md"]


def test_list_files_authorizes_once_and_carries_scandir_stat(tmp_path, monkeypatch):
    (tmp_path / "deep" / "folder").mkdir(parents=True)
    note = tmp_path / "deep" / "folder" / "note.md"
    note.write_text("note")
    policy = VaultAccessPolicy(tmp_path)
    storage = VaultStorage(policy)
    real_resolve_read = policy.resolve_read
    calls = []

    def counted_resolve_read(path, *, allow_empty=False):
        calls.append(path)
        return real_resolve_read(path, allow_empty=allow_empty)

    monkeypatch.setattr(policy, "resolve_read", counted_resolve_read)
    discovered = storage.list_files()

    assert calls == [""]
    assert [path.relative for path in discovered] == ["deep/folder/note.md"]
    assert discovered[0].stat_result is not None
    assert discovered[0].stat_result.st_ino == note.stat().st_ino


def test_read_only_applies_to_binary_gateway(tmp_path):
    storage = VaultStorage(VaultAccessPolicy(tmp_path, read_only=True))
    with pytest.raises(WritePermissionError):
        storage.write_bytes_atomic("image.png", b"data")
    assert not (tmp_path / "image.png").exists()


def test_write_authorization_happens_before_parent_or_temp_creation(tmp_path):
    storage = VaultStorage(VaultAccessPolicy(tmp_path, write_paths=["allowed/"], read_only=False))
    with pytest.raises(WritePermissionError):
        storage.write_text_atomic("denied/new.md", "must not be written")
    assert not (tmp_path / "denied").exists()
    assert not list(tmp_path.rglob(".obsidian-mcp-tmp-*"))


def test_exists_returns_false_when_parent_is_a_file(tmp_path):
    (tmp_path / "note.md").write_text("not a directory")
    storage = VaultStorage(VaultAccessPolicy(tmp_path))
    assert storage.exists("note.md/child.base") is False


def test_symlinked_parent_and_target_are_rejected(tmp_path):
    outside = tmp_path.parent / "outside-policy-test"
    outside.mkdir(exist_ok=True)
    (outside / "secret.md").write_text("secret")
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    policy = VaultAccessPolicy(tmp_path)
    with pytest.raises(VaultPathError):
        policy.resolve_read("linked/secret.md")


def test_path_normalization_and_traversal(tmp_path):
    policy = VaultAccessPolicy(tmp_path)
    assert policy.resolve_read("notes/./nested/../note.md").relative == "notes/note.md"
    assert policy.resolve_read("notes\\note.md").relative == "notes/note.md"
    with pytest.raises(VaultPathError):
        policy.resolve_read("../outside.md")
    with pytest.raises(VaultPathError):
        policy.resolve_read(os.path.abspath("outside.md"))


def test_root_cannot_be_deleted_and_permanent_delete_is_opt_in(tmp_path):
    policy = VaultAccessPolicy(tmp_path)
    with pytest.raises(ProtectedPathError):
        policy.resolve_delete("")
    with pytest.raises(PermanentDeleteDisabledError):
        policy.resolve_delete("note.md", permanent=True)
    enabled = VaultAccessPolicy(tmp_path, allow_permanent_delete=True)
    assert enabled.resolve_delete("note.md", permanent=True).relative == "note.md"


def test_directory_move_preauthorizes_denied_descendants_before_mutation(tmp_path):
    (tmp_path / "source" / "nested").mkdir(parents=True)
    (tmp_path / "source" / "note.md").write_text("note")
    (tmp_path / "source" / "nested" / "secret.md").write_text("secret")
    policy = VaultAccessPolicy(
        tmp_path,
        write_paths=["source/", "destination/"],
        deny_write_paths=["source/nested/"],
    )
    storage = VaultStorage(policy)

    with pytest.raises(WritePermissionError):
        storage.move("source", "destination")

    assert (tmp_path / "source" / "nested" / "secret.md").exists()
    assert not (tmp_path / "destination").exists()


def test_directory_move_preauthorizes_mapped_destination_descendants(tmp_path):
    (tmp_path / "source" / "nested").mkdir(parents=True)
    (tmp_path / "source" / "nested" / "secret.md").write_text("secret")
    policy = VaultAccessPolicy(
        tmp_path,
        write_paths=["source/", "destination/"],
        deny_write_paths=["destination/nested/"],
    )
    storage = VaultStorage(policy)

    with pytest.raises(WritePermissionError):
        storage.move("source", "destination")

    assert (tmp_path / "source" / "nested" / "secret.md").exists()
    assert not (tmp_path / "destination").exists()


def test_directory_mutation_rejects_structural_protected_descendant(tmp_path):
    (tmp_path / "source").mkdir()
    policy = VaultAccessPolicy(
        tmp_path,
        write_paths=["source/", "destination/"],
        deny_write_paths=["destination/future-private/"],
    )
    storage = VaultStorage(policy)

    with pytest.raises(ProtectedPathError):
        storage.move("source", "destination")

    assert (tmp_path / "source").is_dir()
    assert not (tmp_path / "destination").exists()


def test_directory_mutation_rejects_structural_source_descendant(tmp_path):
    (tmp_path / "source").mkdir()
    policy = VaultAccessPolicy(
        tmp_path,
        write_paths=["source/", "destination/"],
        deny_write_paths=["source/future-private/"],
    )
    storage = VaultStorage(policy)

    with pytest.raises(ProtectedPathError):
        storage.move("source", "destination")

    assert (tmp_path / "source").is_dir()
    assert not (tmp_path / "destination").exists()


def test_directory_trash_and_delete_reject_symlink_descendant_before_mutation(tmp_path):
    (tmp_path / "source").mkdir()
    outside = tmp_path.parent / "outside-symlink-test"
    outside.mkdir(exist_ok=True)
    (outside / "secret.md").write_text("secret")
    (tmp_path / "source" / "linked.md").symlink_to(outside / "secret.md")
    storage = VaultStorage(VaultAccessPolicy(tmp_path, allow_permanent_delete=True))

    with pytest.raises(VaultPathError):
        storage.trash("source")
    with pytest.raises(VaultPathError):
        storage.delete("source")

    assert (tmp_path / "source" / "linked.md").is_symlink()
    assert (outside / "secret.md").read_text() == "secret"


def test_descriptor_relative_write_survives_destination_symlink_swap(tmp_path, monkeypatch):
    """A swap to a symlink cannot redirect an atomic write outside the vault."""
    outside = tmp_path.parent / "outside-write-swap-test.txt"
    outside.write_text("original")
    storage = VaultStorage(VaultAccessPolicy(tmp_path))
    real_rename = os.rename
    swapped = False

    def swap_then_rename(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and destination == "note.txt":
            (tmp_path / "note.txt").symlink_to(outside)
            swapped = True
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(filesystem.os, "rename", swap_then_rename)
    monkeypatch.setattr(filesystem.os, "supports_dir_fd", set(os.supports_dir_fd) | {swap_then_rename})
    storage.write_text_atomic("note.txt", "new content")

    assert outside.read_text() == "original"
    assert (tmp_path / "note.txt").read_text() == "new content"
