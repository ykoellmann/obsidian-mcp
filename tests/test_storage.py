
import pytest

from obsidian_mcp.storage.filesystem import (
    PathTraversalError,
    read_file,
    validate_path,
    write_file_atomic,
)
from obsidian_mcp.storage.locking import LockTimeoutError, acquire_lock


def test_validate_path_ok(tmp_path):
    target = validate_path(tmp_path, "subdir/note.md")
    assert str(target).startswith(str(tmp_path))


def test_validate_path_traversal(tmp_path):
    with pytest.raises(PathTraversalError):
        validate_path(tmp_path, "../../etc/passwd")


def test_validate_path_traversal_encoded(tmp_path):
    with pytest.raises(PathTraversalError):
        validate_path(tmp_path, "../outside.md")


def test_write_and_read(tmp_path):
    write_file_atomic(tmp_path, "note.md", "hello world")
    assert read_file(tmp_path, "note.md") == "hello world"


def test_write_is_atomic(tmp_path):
    write_file_atomic(tmp_path, "note.md", "first")
    write_file_atomic(tmp_path, "note.md", "second")
    assert read_file(tmp_path, "note.md") == "second"
    # no leftover tmp files
    assert not list(tmp_path.glob("*.tmp-*"))


def test_write_creates_directories(tmp_path):
    write_file_atomic(tmp_path, "subdir/nested/note.md", "content")
    assert (tmp_path / "subdir" / "nested" / "note.md").exists()


def test_lock_acquire_and_release(tmp_path):
    lock = acquire_lock(str(tmp_path / "note.md"))
    lock.release()


def test_lock_timeout(tmp_path):
    lock1 = acquire_lock(str(tmp_path / "note.md"))
    with pytest.raises(LockTimeoutError):
        acquire_lock(str(tmp_path / "note.md"), timeout=0.1)
    lock1.release()
