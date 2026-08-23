"""Small helpers for optimistic single-file mutations."""

from __future__ import annotations

from typing import Any

from ..config import get_config
from ..domain.models import (
    PreconditionRequiredError,
    RevisionConflictError,
    normalize_revision_token,
)
from .filesystem import VaultStorage


def read_text_for_update(
    storage: VaultStorage, path: str, expected_revision: str | None = None
) -> tuple[str, str]:
    """Read content and return the exact revision to pass to the write."""
    raw, current = storage.read_text_with_revision(path)
    if expected_revision is not None:
        expected = normalize_revision_token(expected_revision)
        if current.token != expected:
            raise RevisionConflictError(path, expected, current)
    return raw, current.token


def prepare_full_write(
    storage: VaultStorage,
    path: str,
    expected_revision: str | None = None,
    create_only: bool = False,
) -> tuple[str | None, bool]:
    """Resolve strict replacement and race-safe creation semantics."""
    try:
        current = storage.revision(path, read=False)
    except FileNotFoundError:
        if expected_revision is not None:
            raise RevisionConflictError(path, expected_revision, None) from None
        return None, True
    if create_only:
        raise RevisionConflictError(path, expected_revision, current)
    if expected_revision is None:
        if get_config().require_write_preconditions:
            raise PreconditionRequiredError(path)
        return current.token, False
    expected = normalize_revision_token(expected_revision)
    if current.token != expected:
        raise RevisionConflictError(path, expected, current)
    return expected, False


def revision_result(result: dict[str, Any], revision) -> dict[str, Any]:
    return {**result, "revision": revision.token}
