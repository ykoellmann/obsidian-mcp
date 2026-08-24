"""Vault-scoped, descriptor-relative filesystem gateway.

Authorization is performed on canonical vault-relative names.  Actual I/O is
then performed relative to directory file descriptors opened with
``O_NOFOLLOW``.  This matters for a network-facing service: checking a
``Path`` and opening that path later leaves a symlink-swap window.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ..domain.models import FileRevision, RevisionConflictError, normalize_revision_token
from .policy import (
    ProtectedPathError,
    ReadPermissionError,
    VaultAccessPolicy,
    VaultPath,
    VaultPathError,
    WritePermissionError,
)

PathTraversalError = VaultPathError


class SecureStorageError(RuntimeError):
    """The platform cannot provide the no-follow descriptor guarantees."""


@dataclass(frozen=True)
class VaultEntry:
    name: str
    relative: str
    is_dir: bool
    size_bytes: int | None
    mtime: float


@dataclass(frozen=True)
class TrashEntry:
    """Metadata exposed by the narrow trash-management capability."""

    name: str
    is_dir: bool
    size_bytes: int | None
    mtime: float


@dataclass(frozen=True)
class _AuthorizedTree:
    """One storage instance's fully authorized tree-mutation input."""

    storage: object
    source: VaultPath
    destination: VaultPath | None
    paths: tuple[str, ...]
    permanent: bool


def _require_secure_platform() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "supports_dir_fd"):
        raise SecureStorageError("This platform lacks descriptor-relative no-follow filesystem APIs")
    if os.open not in os.supports_dir_fd:
        raise SecureStorageError("openat-style directory descriptors are unavailable")
    if os.rename not in os.supports_dir_fd:
        raise SecureStorageError("renameat-style directory descriptors are unavailable")
    if os.unlink not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd:
        raise SecureStorageError("unlinkat/mkdirat-style directory descriptors are unavailable")
    if not hasattr(os, "link"):
        raise SecureStorageError("linkat-style no-replace file creation is unavailable")


def _dir_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    # O_NONBLOCK prevents opening a FIFO from hanging before we can reject it
    # with fstat. It has no effect on ordinary regular-file reads.
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


@contextlib.contextmanager
def _opened_parent(
    root: Path, relative: str, *, create_from: int | None = None
) -> Iterator[tuple[int, str]]:
    """Yield ``(parent_fd, leaf)`` with every parent opened no-follow."""
    _require_secure_platform()
    parts = [part for part in relative.split("/") if part]
    if not parts:
        raise VaultPathError("A child path is required")
    root_fd = os.open(root, _dir_flags())
    fds = [root_fd]
    try:
        current = root_fd
        for depth, component in enumerate(parts[:-1]):
            try:
                child = os.open(component, _dir_flags(), dir_fd=current)
            except FileNotFoundError:
                if create_from is None or depth < create_from:
                    raise
                os.mkdir(component, 0o777, dir_fd=current)
                child = os.open(component, _dir_flags(), dir_fd=current)
            fds.append(child)
            current = child
        yield current, parts[-1]
    finally:
        for fd in reversed(fds):
            os.close(fd)


@contextlib.contextmanager
def _opened_dir(root: Path, relative: str = "") -> Iterator[int]:
    """Yield a no-follow fd for an existing vault directory."""
    _require_secure_platform()
    if not relative:
        fd = os.open(root, _dir_flags())
        try:
            yield fd
        finally:
            os.close(fd)
        return
    with _opened_parent(root, relative) as (parent_fd, leaf):
        fd = os.open(leaf, _dir_flags(), dir_fd=parent_fd)
        try:
            yield fd
        finally:
            os.close(fd)


def _stat_at(parent_fd: int, leaf: str) -> os.stat_result:
    return os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)


def _ensure_not_symlink(parent_fd: int, leaf: str) -> os.stat_result:
    info = _stat_at(parent_fd, leaf)
    if stat.S_ISLNK(info.st_mode):
        raise VaultPathError(f"Symlink path components are not allowed: {leaf!r}")
    return info


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _revision_at(parent_fd: int, leaf: str) -> FileRevision | None:
    try:
        fd = os.open(leaf, _file_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    try:
        initial = os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode):
            raise IsADirectoryError(f"Target is not a regular file: {leaf!r}")
        content = _read_all(fd)
        info = os.fstat(fd)
        return FileRevision.from_bytes(content, mtime_ns=info.st_mtime_ns)
    finally:
        os.close(fd)


def _scandir_tree(fd: int, prefix: str = "") -> Iterator[tuple[str, os.stat_result, bool]]:
    """Yield all descendants, rejecting symlinks and using stable dirfds."""
    with os.scandir(fd) as entries:
        for entry in entries:
            rel = f"{prefix}/{entry.name}" if prefix else entry.name
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise VaultPathError(f"Symlink path components are not allowed: {rel!r}")
            is_dir = stat.S_ISDIR(info.st_mode)
            yield rel, info, is_dir
            if is_dir:
                child_fd = os.open(entry.name, _dir_flags(), dir_fd=fd)
                try:
                    yield from _scandir_tree(child_fd, rel)
                finally:
                    os.close(child_fd)


def _remove_tree_fd(parent_fd: int, leaf: str) -> None:
    """Remove a directory tree relative to an already-open parent fd."""
    info = _ensure_not_symlink(parent_fd, leaf)
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(leaf, dir_fd=parent_fd)
        return
    child_fd = os.open(leaf, _dir_flags(), dir_fd=parent_fd)
    try:
        with os.scandir(child_fd) as entries:
            for entry in entries:
                child_info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(child_info.st_mode):
                    raise VaultPathError(f"Symlink path components are not allowed: {entry.name!r}")
                if stat.S_ISDIR(child_info.st_mode):
                    _remove_tree_fd(child_fd, entry.name)
                else:
                    os.unlink(entry.name, dir_fd=child_fd)
    finally:
        os.close(child_fd)
    os.rmdir(leaf, dir_fd=parent_fd)


class VaultStorage:
    """Authorize each operation before secure descriptor-relative I/O."""

    def __init__(self, policy: VaultAccessPolicy) -> None:
        self.policy = policy

    @classmethod
    def from_config(cls, config=None) -> VaultStorage:
        if config is None:
            from ..config import get_config

            config = get_config()
        return cls(VaultAccessPolicy.from_config(config))

    def resolve_read(self, path: str, *, allow_empty: bool = False) -> VaultPath:
        return self.policy.resolve_read(path, allow_empty=allow_empty)

    def resolve_write(self, path: str, *, allow_empty: bool = False) -> VaultPath:
        return self.policy.resolve_write(path, allow_empty=allow_empty)

    def resolve_delete(self, path: str, *, permanent: bool = False) -> VaultPath:
        return self.policy.resolve_delete(path, permanent=permanent)

    def _write_create_from(self, target: VaultPath) -> int:
        """First parent depth that a matching write scope permits creating."""
        if not self.policy.write_paths:
            return 0
        matching_scopes = (
            self.policy.rule_path(rule)
            for rule in self.policy.write_paths
            if self.policy._matches(target.relative, rule)
        )
        return min(max(len(scope.split("/")) - 1, 0) for scope in matching_scopes)

    @contextlib.contextmanager
    def _opened_write_parent(self, target: VaultPath) -> Iterator[tuple[int, str]]:
        try:
            with _opened_parent(
                self.policy.root,
                target.relative,
                create_from=self._write_create_from(target),
            ) as opened:
                yield opened
        except FileNotFoundError as exc:
            if self.policy.write_paths:
                raise WritePermissionError(
                    "A parent above the configured WRITE_PATHS scope does not exist"
                ) from exc
            raise

    def stat(self, path: str, *, read: bool = True) -> os.stat_result:
        target = self.resolve_read(path) if read else self.resolve_write(path)
        with _opened_parent(self.policy.root, target.relative) as (parent_fd, leaf):
            return _ensure_not_symlink(parent_fd, leaf)

    def exists(self, path: str, *, read: bool = True) -> bool:
        try:
            self.stat(path, read=read)
            return True
        except (FileNotFoundError, NotADirectoryError):
            return False

    def _tree_paths(self, target: VaultPath) -> list[str]:
        """List a target and descendants using no-follow descriptors."""
        if not target.relative:
            with _opened_dir(self.policy.root, "") as root_fd:
                return [rel for rel, _, _ in _scandir_tree(root_fd)]
        try:
            with _opened_parent(self.policy.root, target.relative) as (parent_fd, leaf):
                info = _ensure_not_symlink(parent_fd, leaf)
                paths = [target.relative]
                if stat.S_ISDIR(info.st_mode):
                    child_fd = os.open(leaf, _dir_flags(), dir_fd=parent_fd)
                    try:
                        paths.extend(f"{target.relative}/{rel}" for rel, _, _ in _scandir_tree(child_fd))
                    finally:
                        os.close(child_fd)
                return paths
        except FileNotFoundError:
            raise FileNotFoundError(f"Path not found: {target.relative!r}") from None

    def tree_paths(self, path: str) -> list[VaultPath]:
        target = self.resolve_read(path, allow_empty=not path)
        # Public discovery must apply read policy to every descendant. Internal
        # mutation preflight intentionally uses _tree_paths() directly and
        # authorizes each returned path with resolve_delete/resolve_write.
        result: list[VaultPath] = []
        for rel in self._tree_paths(target):
            try:
                result.append(self.policy.resolve_read(rel))
            except (VaultPathError, ReadPermissionError):
                continue
        return result

    def authorize_tree(
        self,
        path: str,
        *,
        destination: str | None = None,
        permanent: bool = False,
    ) -> _AuthorizedTree:
        """Preauthorize every source descendant and mapped destination.

        Delete policy is applied to every source descendant.
        ``destination`` additionally authorizes each mapped destination before
        any rename or directory creation occurs.
        """
        source = self.resolve_delete(path, permanent=permanent)
        destination_path = self.resolve_write(destination) if destination is not None else None
        return self._authorize_resolved_tree(
            source, destination=destination_path, permanent=permanent
        )

    def _authorize_resolved_tree(
        self,
        source: VaultPath,
        *,
        destination: VaultPath | None = None,
        permanent: bool = False,
    ) -> _AuthorizedTree:
        source_paths = self._tree_paths(source)
        source_is_dir = False
        with _opened_parent(self.policy.root, source.relative) as (source_parent, source_leaf):
            source_is_dir = stat.S_ISDIR(_ensure_not_symlink(source_parent, source_leaf).st_mode)
        if source_is_dir:
            source_prefix = source.relative.rstrip("/") + "/"
            if any(
                self.policy.rule_path(rule).startswith(source_prefix)
                for rule in self.policy.deny_write_paths
            ):
                raise ProtectedPathError(
                    f"Directory mutation crosses a protected descendant of {source.relative!r}"
                )
        for rel in source_paths:
            self.policy.resolve_delete(rel, permanent=permanent)
        if destination is not None:
            dest = destination
            if source_is_dir:
                dest_prefix = dest.relative.rstrip("/") + "/"
                if any(
                    self.policy.rule_path(rule).startswith(dest_prefix)
                    for rule in self.policy.deny_write_paths
                ):
                    raise ProtectedPathError(
                        f"Directory mutation crosses a protected destination descendant of {dest.relative!r}"
                    )
            prefix = source.relative + "/"
            for rel in source_paths:
                suffix = rel[len(prefix):] if rel.startswith(prefix) else ""
                mapped = f"{dest.relative}/{suffix}" if suffix else dest.relative
                self.policy.resolve_write(mapped)
        return _AuthorizedTree(
            storage=self,
            source=source,
            destination=destination,
            paths=tuple(source_paths),
            permanent=permanent,
        )

    def _validate_authorization(
        self,
        authorization: _AuthorizedTree,
        *,
        source: VaultPath,
        destination: VaultPath | None,
        permanent: bool,
    ) -> None:
        if (
            authorization.storage is not self
            or authorization.source.relative != source.relative
            or authorization.destination != destination
            or authorization.permanent != permanent
        ):
            raise VaultPathError("Tree authorization does not match this mutation")

    def read_text(self, path: str) -> str:
        content, _ = self.read_text_with_revision(path)
        return content

    def read_text_with_revision(self, path: str) -> tuple[str, FileRevision]:
        target = self.resolve_read(path)
        with _opened_parent(self.policy.root, target.relative) as (parent_fd, leaf):
            fd = os.open(leaf, _file_flags(), dir_fd=parent_fd)
            try:
                initial = os.fstat(fd)
                if not stat.S_ISREG(initial.st_mode):
                    raise IsADirectoryError(
                        f"Target is not a regular file: {target.relative!r}"
                    )
                content = _read_all(fd)
                info = os.fstat(fd)
                return (
                    content.decode("utf-8", errors="replace"),
                    FileRevision.from_bytes(content, mtime_ns=info.st_mtime_ns),
                )
            finally:
                os.close(fd)

    def read_bytes(self, path: str) -> bytes:
        content, _ = self.read_bytes_with_revision(path)
        return content

    def read_bytes_with_revision(self, path: str) -> tuple[bytes, FileRevision]:
        target = self.resolve_read(path)
        with _opened_parent(self.policy.root, target.relative) as (parent_fd, leaf):
            fd = os.open(leaf, _file_flags(), dir_fd=parent_fd)
            try:
                initial = os.fstat(fd)
                if not stat.S_ISREG(initial.st_mode):
                    raise IsADirectoryError(
                        f"Target is not a regular file: {target.relative!r}"
                    )
                content = _read_all(fd)
                info = os.fstat(fd)
                return content, FileRevision.from_bytes(content, mtime_ns=info.st_mtime_ns)
            finally:
                os.close(fd)

    def revision(self, path: str, *, read: bool = True) -> FileRevision:
        target = self.resolve_read(path) if read else self.resolve_write(path)
        with _opened_parent(self.policy.root, target.relative) as (parent_fd, leaf):
            revision = _revision_at(parent_fd, leaf)
        if revision is None:
            raise FileNotFoundError(f"Path not found: {target.relative!r}")
        return revision

    def _write_atomic(
        self,
        target: VaultPath,
        data: bytes,
        *,
        expected_revision: str | None = None,
        create_only: bool = False,
    ) -> FileRevision:
        expected = normalize_revision_token(expected_revision) if expected_revision is not None else None
        tmp_name = f".obsidian-mcp-tmp-{uuid.uuid4().hex}"
        with self._opened_write_parent(target) as (parent_fd, leaf):
            current = _revision_at(parent_fd, leaf)
            if create_only and current is not None:
                raise RevisionConflictError(target.relative, expected, current)
            if expected is not None and (current is None or current.token != expected):
                raise RevisionConflictError(target.relative, expected, current)
            effective_create_only = create_only or current is None
            existing_mode = None
            if current is not None:
                existing = _ensure_not_symlink(parent_fd, leaf)
                existing_mode = stat.S_IMODE(existing.st_mode) & 0o777
            # O_EXCL + dirfd ensures the temporary file is created in the
            # already-authorized parent and cannot be redirected by a symlink.
            tmp_fd = os.open(
                tmp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o666,
                dir_fd=parent_fd,
            )
            committed = False
            try:
                _write_all(tmp_fd, data)
                if existing_mode is not None:
                    os.fchmod(tmp_fd, existing_mode)
                os.fsync(tmp_fd)
                os.close(tmp_fd)
                tmp_fd = -1
                latest = _revision_at(parent_fd, leaf)
                if effective_create_only:
                    if latest is not None:
                        raise RevisionConflictError(target.relative, expected, latest)
                    try:
                        os.link(
                            tmp_name,
                            leaf,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        raise RevisionConflictError(
                            target.relative, expected, _revision_at(parent_fd, leaf)
                        ) from None
                    except OSError as exc:
                        if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EPERM}:
                            raise SecureStorageError(
                                "The vault filesystem does not support atomic no-replace file creation"
                            ) from exc
                        raise
                    committed = True
                    os.unlink(tmp_name, dir_fd=parent_fd)
                else:
                    if expected is not None and (latest is None or latest.token != expected):
                        raise RevisionConflictError(target.relative, expected, latest)
                    os.rename(tmp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    committed = True
                os.fsync(parent_fd)
                final = _revision_at(parent_fd, leaf)
                if final is None:
                    raise OSError(f"Committed file disappeared: {target.relative!r}")
                return final
            finally:
                if tmp_fd >= 0:
                    os.close(tmp_fd)
                if not committed:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(tmp_name, dir_fd=parent_fd)

    def write_text_atomic(
        self,
        path: str,
        content: str,
        *,
        expected_revision: str | None = None,
        create_only: bool = False,
    ) -> FileRevision:
        target = self.resolve_write(path)
        return self._write_atomic(
            target,
            content.encode("utf-8"),
            expected_revision=expected_revision,
            create_only=create_only,
        )

    def write_bytes_atomic(
        self,
        path: str,
        content: bytes,
        *,
        expected_revision: str | None = None,
        create_only: bool = False,
    ) -> FileRevision:
        target = self.resolve_write(path)
        return self._write_atomic(
            target,
            content,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    def probe_create_only_support(self) -> None:
        """Verify the mounted vault supports the hard-link no-replace primitive."""
        if self.policy.read_only:
            return
        probe_targets = {f".obsidian-mcp-link-probe-{uuid.uuid4().hex}"}
        if self.policy.write_paths:
            probe_targets = {
                (
                    f"{rule.rstrip('/')}/.obsidian-mcp-link-probe-{uuid.uuid4().hex}"
                    if rule.endswith("/")
                    else rule
                )
                for rule in self.policy.write_paths
            }
        for probe_target in sorted(probe_targets):
            self._probe_create_only_target(probe_target)

    def _probe_create_only_target(self, probe_target: str) -> None:
        probe_name = f".obsidian-mcp-link-probe-{uuid.uuid4().hex}"
        target = self.resolve_write(probe_target)
        try:
            with self._opened_write_parent(target) as (directory_fd, _target_leaf):
                self._run_create_only_probe(directory_fd, probe_name)
        except WritePermissionError as exc:
            raise SecureStorageError(
                f"Configured writable parent cannot be opened: {probe_target!r}"
            ) from exc

    @staticmethod
    def _run_create_only_probe(directory_fd: int, probe_name: str) -> None:
        source = f"{probe_name}.source"
        destination = f"{probe_name}.destination"
        fd = os.open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        os.close(fd)
        try:
            os.link(
                source,
                destination,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SecureStorageError(
                "The writable vault filesystem does not support atomic no-replace file creation"
            ) from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(destination, dir_fd=directory_fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(source, dir_fd=directory_fd)

    def make_dir(self, path: str) -> VaultPath:
        target = self.resolve_write(path)
        with self._opened_write_parent(target) as (parent_fd, leaf):
            try:
                os.mkdir(leaf, 0o777, dir_fd=parent_fd)
            except FileExistsError:
                info = _ensure_not_symlink(parent_fd, leaf)
                if not stat.S_ISDIR(info.st_mode):
                    raise
        return target

    def list_dir(self, path: str = "") -> list[VaultEntry]:
        target = self.resolve_read(path, allow_empty=True)
        with _opened_dir(self.policy.root, target.relative) as directory_fd:
            entries: list[VaultEntry] = []
            with os.scandir(directory_fd) as items:
                for item in sorted(items, key=lambda entry: entry.name):
                    rel = f"{target.relative}/{item.name}" if target.relative else item.name
                    try:
                        info = item.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            continue
                        authorized = self.policy.authorize_discovered_read(rel, info)
                    except (VaultPathError, ReadPermissionError, OSError):
                        continue
                    entries.append(
                        VaultEntry(
                            name=item.name,
                            relative=authorized.relative,
                            is_dir=stat.S_ISDIR(info.st_mode),
                            size_bytes=None if stat.S_ISDIR(info.st_mode) else info.st_size,
                            mtime=info.st_mtime,
                        )
                    )
            return entries

    def list_files(self, path: str = "") -> list[VaultPath]:
        target = self.resolve_read(path, allow_empty=True)
        with _opened_dir(self.policy.root, target.relative) as directory_fd:
            result: list[VaultPath] = []

            def walk(fd: int, prefix: str = "") -> Iterator[VaultPath]:
                with os.scandir(fd) as items:
                    for item in items:
                        rel = f"{prefix}/{item.name}" if prefix else item.name
                        full_rel = f"{target.relative}/{rel}" if target.relative else rel
                        try:
                            info = item.stat(follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode):
                                continue
                            authorized = self.policy.authorize_discovered_read(
                                full_rel, info
                            )
                        except (VaultPathError, ReadPermissionError, OSError):
                            # A denied directory is not descended into.  This
                            # keeps discovery from even enumerating protected
                            # subtrees, while a concurrent/symlink swap fails
                            # closed for this listing.
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            try:
                                child_fd = os.open(item.name, _dir_flags(), dir_fd=fd)
                            except OSError:
                                continue
                            try:
                                yield from walk(child_fd, rel)
                            finally:
                                os.close(child_fd)
                        else:
                            yield authorized

            result.extend(walk(directory_fd))
            return result

    def _rename_relative(self, source: VaultPath, destination: VaultPath) -> None:
        with _opened_parent(self.policy.root, source.relative) as (src_parent, src_leaf):
            _ensure_not_symlink(src_parent, src_leaf)
            with self._opened_write_parent(destination) as (dst_parent, dst_leaf):
                try:
                    _stat_at(dst_parent, dst_leaf)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError(f"Target already exists: {destination.relative!r}")
                os.rename(src_leaf, dst_leaf, src_dir_fd=src_parent, dst_dir_fd=dst_parent)

    def move(
        self,
        from_path: str,
        to_path: str,
        *,
        authorization: _AuthorizedTree | None = None,
    ) -> tuple[VaultPath, VaultPath]:
        source = self.resolve_delete(from_path)
        destination = self.resolve_write(to_path)
        if source.relative == destination.relative:
            raise VaultPathError("Source and destination must differ")
        if destination.relative.startswith(source.relative + "/"):
            raise VaultPathError("Destination cannot be inside the source tree")
        if authorization is None:
            self._authorize_resolved_tree(source, destination=destination)
        else:
            self._validate_authorization(
                authorization,
                source=source,
                destination=destination,
                permanent=False,
            )
        self._rename_relative(source, destination)
        return source, destination

    def delete(
        self, path: str, *, authorization: _AuthorizedTree | None = None
    ) -> VaultPath:
        target = self.resolve_delete(path, permanent=True)
        if authorization is None:
            self._authorize_resolved_tree(target, permanent=True)
        else:
            self._validate_authorization(
                authorization,
                source=target,
                destination=None,
                permanent=True,
            )
        with _opened_parent(self.policy.root, target.relative) as (parent_fd, leaf):
            _remove_tree_fd(parent_fd, leaf)
        return target

    def trash(
        self, path: str, *, authorization: _AuthorizedTree | None = None
    ) -> tuple[VaultPath, Path]:
        source = self.resolve_delete(path)
        if authorization is None:
            self._authorize_resolved_tree(source)
        else:
            self._validate_authorization(
                authorization,
                source=source,
                destination=None,
                permanent=False,
            )
        with _opened_dir(self.policy.root, "") as root_fd:
            try:
                trash_fd = os.open(".trash", _dir_flags(), dir_fd=root_fd)
            except FileNotFoundError:
                os.mkdir(".trash", 0o700, dir_fd=root_fd)
                trash_fd = os.open(".trash", _dir_flags(), dir_fd=root_fd)
            try:
                with _opened_parent(self.policy.root, source.relative) as (src_parent, src_leaf):
                    _ensure_not_symlink(src_parent, src_leaf)
                    destination_name = source.relative.rsplit("/", 1)[-1]
                    try:
                        _stat_at(trash_fd, destination_name)
                    except FileNotFoundError:
                        pass
                    else:
                        stem, dot, suffix = destination_name.rpartition(".")
                        if not dot:
                            stem, suffix = destination_name, ""
                        destination_name = f"{stem}-{uuid.uuid4().hex[:8]}{('.' + suffix) if suffix else ''}"
                    os.rename(src_leaf, destination_name, src_dir_fd=src_parent, dst_dir_fd=trash_fd)
                    return source, self.policy.root / ".trash" / destination_name
            finally:
                os.close(trash_fd)

    def list_trash(self) -> list[TrashEntry]:
        try:
            with _opened_dir(self.policy.root, ".trash") as trash_fd:
                result: list[TrashEntry] = []
                with os.scandir(trash_fd) as items:
                    for item in sorted(items, key=lambda entry: entry.name):
                        info = item.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            continue
                        result.append(
                            TrashEntry(
                                name=item.name,
                                is_dir=stat.S_ISDIR(info.st_mode),
                                size_bytes=None if stat.S_ISDIR(info.st_mode) else info.st_size,
                                mtime=info.st_mtime,
                            )
                        )
                return result
        except FileNotFoundError:
            return []

    def trash_info(self, trashed_name: str) -> TrashEntry:
        if not trashed_name or Path(trashed_name).name != trashed_name:
            raise VaultPathError("Trash item must be a bare filename")
        with _opened_dir(self.policy.root, ".trash") as trash_fd:
            info = _ensure_not_symlink(trash_fd, trashed_name)
            return TrashEntry(
                name=trashed_name,
                is_dir=stat.S_ISDIR(info.st_mode),
                size_bytes=None if stat.S_ISDIR(info.st_mode) else info.st_size,
                mtime=info.st_mtime,
            )

    def restore(self, trashed_name: str, to_path: str) -> VaultPath:
        info = self.trash_info(trashed_name)
        destination = self.resolve_write(to_path)
        if info.is_dir:
            dest_prefix = destination.relative.rstrip("/") + "/"
            if any(
                self.policy.rule_path(rule).startswith(dest_prefix)
                for rule in self.policy.deny_write_paths
            ):
                raise ProtectedPathError(
                    f"Directory restore crosses a protected destination descendant of {destination.relative!r}"
                )
        with _opened_dir(self.policy.root, ".trash") as trash_fd:
            source_fd = os.open(info.name, _dir_flags(), dir_fd=trash_fd) if info.is_dir else -1
            try:
                source_paths = [destination.relative]
                if source_fd >= 0:
                    source_paths.extend(
                        f"{destination.relative}/{rel}" for rel, _, _ in _scandir_tree(source_fd)
                    )
                for rel in source_paths:
                    self.policy.resolve_write(rel)
            finally:
                if source_fd >= 0:
                    os.close(source_fd)
        with (
            _opened_dir(self.policy.root, ".trash") as trash_fd,
            self._opened_write_parent(destination) as (dst_parent, dst_leaf),
        ):
            try:
                _stat_at(dst_parent, dst_leaf)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(f"Target already exists: {to_path!r}")
            _ensure_not_symlink(trash_fd, info.name)
            os.rename(info.name, dst_leaf, src_dir_fd=trash_fd, dst_dir_fd=dst_parent)
        return destination
