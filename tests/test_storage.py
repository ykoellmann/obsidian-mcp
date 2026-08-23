
import os
import stat

import pytest

from obsidian_mcp.storage.filesystem import PathTraversalError, VaultStorage
from obsidian_mcp.storage.locking import LockTimeoutError, acquire_lock
from obsidian_mcp.storage.policy import VaultAccessPolicy


def _storage(root):
    return VaultStorage(VaultAccessPolicy(root))


def test_validate_path_ok(tmp_path):
    target = _storage(tmp_path).policy.canonicalize("subdir/note.md").absolute
    assert str(target).startswith(str(tmp_path))


def test_validate_path_traversal(tmp_path):
    with pytest.raises(PathTraversalError):
        _storage(tmp_path).policy.canonicalize("../../etc/passwd")


def test_validate_path_traversal_encoded(tmp_path):
    with pytest.raises(PathTraversalError):
        _storage(tmp_path).policy.canonicalize("../outside.md")


def test_write_and_read(tmp_path):
    storage = _storage(tmp_path)
    storage.write_text_atomic("note.md", "hello world")
    assert storage.read_text("note.md") == "hello world"


def test_write_is_atomic(tmp_path):
    storage = _storage(tmp_path)
    storage.write_text_atomic("note.md", "first")
    storage.write_text_atomic("note.md", "second")
    assert storage.read_text("note.md") == "second"
    # no leftover tmp files
    assert not list(tmp_path.glob("*.tmp-*"))


def test_write_creates_directories(tmp_path):
    _storage(tmp_path).write_text_atomic("subdir/nested/note.md", "content")
    assert (tmp_path / "subdir" / "nested" / "note.md").exists()


def test_write_preserves_existing_file_mode(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("old")
    note.chmod(0o664)

    _storage(tmp_path).write_text_atomic("note.md", "new")

    assert stat.S_IMODE(note.stat().st_mode) == 0o664


def test_new_files_and_directories_honour_umask(tmp_path):
    previous_umask = os.umask(0o027)
    try:
        _storage(tmp_path).write_text_atomic("sub/note.md", "new")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE((tmp_path / "sub").stat().st_mode) == 0o750
    assert stat.S_IMODE((tmp_path / "sub" / "note.md").stat().st_mode) == 0o640


def test_lock_acquire_and_release(tmp_path):
    lock = acquire_lock(str(tmp_path / "note.md"))
    lock.release()


def test_lock_timeout(tmp_path):
    lock1 = acquire_lock(str(tmp_path / "note.md"))
    with pytest.raises(LockTimeoutError):
        acquire_lock(str(tmp_path / "note.md"), timeout=0.1)
    lock1.release()


def test_configured_lock_creation_failure_does_not_fallback(tmp_path, monkeypatch):
    lock_root = tmp_path / "locks"
    real_mkdir = type(lock_root).mkdir

    def fail_configured_path(self, *args, **kwargs):
        if self == lock_root:
            raise PermissionError("configured lock path unavailable")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(type(lock_root), "mkdir", fail_configured_path)
    with pytest.raises(PermissionError, match="configured lock path unavailable"):
        acquire_lock("note.md", lock_path=lock_root)


def test_read_file_via_config_vault_path(vault_factory):
    vault_factory({"note.md": "# Hello\nWorld"})
    from obsidian_mcp.config import get_config
    cfg = get_config()
    content = VaultStorage.from_config(cfg).read_text("note.md")
    assert "Hello" in content
    assert "World" in content
