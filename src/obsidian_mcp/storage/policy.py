"""Canonical vault paths and the central filesystem authorization policy.

The MCP tools intentionally deal in vault-relative strings.  This module is
the only place where those strings become filesystem paths.  Keeping that
conversion here prevents a tool from validating one path and subsequently
constructing a different (or symlinked) path for the actual operation.
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


class VaultPathError(ValueError):
    """The supplied path is not a safe vault-relative path."""


class ReadPermissionError(PermissionError):
    """A path is outside the readable portion of the vault."""


class WritePermissionError(PermissionError):
    """A path is outside the writable portion of the vault."""


class ProtectedPathError(WritePermissionError):
    """A security-sensitive path cannot be modified by MCP."""


class PermanentDeleteDisabledError(WritePermissionError):
    """Permanent deletion is disabled by configuration."""


class InvalidFileTypeError(ValueError):
    """A tool-specific writer was given the wrong file extension."""


@dataclass(frozen=True)
class VaultPath:
    """An authorized, immutable vault-relative path and its absolute target."""

    relative: str
    absolute: Path
    stat_result: os.stat_result | None = None


def matches_path_rule(path: str, rule: str, *, casefold: bool = False) -> bool:
    """Match one canonical path against an exact or recursive rooted rule."""
    recursive = rule.endswith("/")
    candidate = path.casefold() if casefold else path
    scope = rule.rstrip("/")
    scope = scope.casefold() if casefold else scope
    return candidate == scope or (
        recursive and candidate.startswith(scope + "/")
    )


def _normalise_relative(value: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise VaultPathError("Vault path must be a string")
    if "\x00" in value:
        raise VaultPathError("Vault path contains a NUL byte")
    # MCP clients on Windows commonly send backslash paths even when the
    # server runs on Linux.  Treat both separators as path separators.
    value = value.replace("\\", "/")
    if value.startswith("/") or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise VaultPathError("Absolute vault paths are not allowed")
    normalised = posixpath.normpath(value)
    if normalised == ".":
        normalised = ""
    if not allow_empty and not normalised:
        raise VaultPathError("A vault file or child directory path is required")
    if normalised == ".." or normalised.startswith("../"):
        raise VaultPathError(f"Path escapes vault root: {value!r}")
    return normalised


def _normalise_rules(values: Iterable[str], *, name: str) -> tuple[str, ...]:
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise VaultPathError(f"{name} entries must be strings")
        recursive = raw.endswith(("/", "\\"))
        item = _normalise_relative(raw.rstrip("/\\"), allow_empty=False)
        if recursive:
            item += "/"
        if item not in result:
            result.append(item)
    return tuple(result)


class VaultAccessPolicy:
    """Resolve and authorize all MCP paths against one vault root."""

    def __init__(
        self,
        vault_root: str | Path,
        *,
        read_only: bool = False,
        write_paths: Iterable[str] = (),
        deny_read_paths: Iterable[str] = (),
        deny_write_paths: Iterable[str] = (),
        allow_permanent_delete: bool = False,
    ) -> None:
        root = Path(vault_root)
        if not root.exists() or not root.is_dir():
            raise VaultPathError(f"Vault root does not exist or is not a directory: {root}")
        self.root = root.resolve()
        self.read_only = bool(read_only)
        self.write_paths = _normalise_rules(write_paths, name="WRITE_PATHS")
        self.deny_read_paths = _normalise_rules(deny_read_paths, name="DENY_READ_PATHS")
        self.deny_write_paths = _normalise_rules(deny_write_paths, name="DENY_WRITE_PATHS")
        self.allow_permanent_delete = bool(allow_permanent_delete)

    @classmethod
    def from_config(cls, config) -> VaultAccessPolicy:
        return cls(
            config.vault_path,
            read_only=config.read_only,
            write_paths=config.write_paths,
            deny_read_paths=config.deny_read_paths,
            deny_write_paths=config.deny_write_paths,
            allow_permanent_delete=config.allow_permanent_delete,
        )

    def canonicalize(self, path: str, *, allow_empty: bool = True) -> VaultPath:
        relative = _normalise_relative(path, allow_empty=allow_empty)
        lexical = self.root / relative if relative else self.root

        # Do not follow a symlink supplied by an MCP caller.  This checks every
        # existing component, including a parent of a not-yet-created file.
        current = self.root
        for component in Path(relative).parts:
            current = current / component
            if current.is_symlink():
                raise VaultPathError(f"Symlink path components are not allowed: {path!r}")

        try:
            absolute = lexical.resolve(strict=False)
        except OSError as exc:
            raise VaultPathError(f"Unable to resolve vault path: {path!r}") from exc
        if not absolute.is_relative_to(self.root):
            raise VaultPathError(f"Path escapes vault root: {path!r}")
        # ``relative`` is derived from the normalized lexical path, not from a
        # potentially symlink-resolved target, so callers get a stable key.
        return VaultPath(relative=relative, absolute=absolute)

    @staticmethod
    def rule_path(rule: str) -> str:
        """Return the canonical path portion of a configured rule."""
        return rule.rstrip("/")

    @staticmethod
    def _matches(path: str, rule: str, *, casefold: bool = False) -> bool:
        """Match exact file rules and slash-suffixed recursive directory rules."""
        return matches_path_rule(path, rule, casefold=casefold)

    def authorize_discovered_read(
        self, path: str, info: os.stat_result
    ) -> VaultPath:
        """Authorize a no-follow descriptor discovery without rewalking it.

        Callers must obtain ``path`` and ``info`` from the descriptor-relative
        scanner. Unlike ``resolve_read``, this method deliberately performs no
        second path lookup that could race or duplicate O(depth) syscalls.
        """
        relative = _normalise_relative(path, allow_empty=False)
        if relative != path:
            raise VaultPathError(f"Non-canonical discovered path: {path!r}")
        if self._denied(relative, self.deny_read_paths):
            raise ReadPermissionError(f"Read access denied for path {relative!r}")
        return VaultPath(
            relative=relative,
            absolute=self.root / relative,
            stat_result=info,
        )

    def _denied(self, path: str, rules: tuple[str, ...]) -> str | None:
        # The longest matching rule gives a useful deterministic reason in logs.
        # Case-fold deny rules even on a case-sensitive host. This can only
        # deny additional paths and prevents case aliases bypassing policy on
        # the common case-insensitive macOS/Windows filesystems.
        matches = [rule for rule in rules if self._matches(path, rule, casefold=True)]
        return max(matches, key=len) if matches else None

    def resolve_read(self, path: str, *, allow_empty: bool = False) -> VaultPath:
        result = self.canonicalize(path, allow_empty=allow_empty)
        if self._denied(result.relative, self.deny_read_paths):
            raise ReadPermissionError(f"Read access denied for path {result.relative!r}")
        return result

    def resolve_write(self, path: str, *, allow_empty: bool = False) -> VaultPath:
        result = self.canonicalize(path, allow_empty=allow_empty)
        if self.read_only:
            raise WritePermissionError("Server is in read-only mode")
        if self.write_paths and not any(self._matches(result.relative, rule) for rule in self.write_paths):
            raise WritePermissionError(f"Write access denied for path {result.relative!r}")
        if self._denied(result.relative, self.deny_write_paths):
            raise ProtectedPathError(f"Write access denied for protected path {result.relative!r}")
        return result

    def resolve_delete(self, path: str, *, permanent: bool = False) -> VaultPath:
        result = self.resolve_write(path, allow_empty=True)
        if result.relative == "":
            raise ProtectedPathError("The vault root cannot be deleted")
        if permanent and not self.allow_permanent_delete:
            raise PermanentDeleteDisabledError("Permanent deletion is disabled")
        return result

    def can_read(self, path: str) -> bool:
        try:
            self.resolve_read(path)
            return True
        except (VaultPathError, ReadPermissionError):
            return False

    def can_write(self, path: str) -> bool:
        try:
            self.resolve_write(path)
            return True
        except (VaultPathError, WritePermissionError):
            return False


def path_rules_from_env(raw: str, *, name: str) -> list[str]:
    """Parse a comma-separated path setting and validate it immediately."""
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return list(_normalise_rules(values, name=name))
