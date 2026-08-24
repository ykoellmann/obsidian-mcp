from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FileRevision:
    """A content revision. Only the SHA-256 token is part of the public API."""

    sha256: str
    size: int
    mtime_ns: int

    @property
    def token(self) -> str:
        return f"sha256:{self.sha256}"

    @classmethod
    def from_bytes(cls, content: bytes, *, mtime_ns: int) -> FileRevision:
        return cls(hashlib.sha256(content).hexdigest(), len(content), mtime_ns)


def normalize_revision_token(value: str) -> str:
    """Validate and normalize the opaque revision accepted from MCP clients."""
    digest = value.strip().removeprefix("sha256:").lower()
    if len(digest) != 64:
        raise ValueError("expected_revision must be a sha256:<64 hex characters> token")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError("expected_revision must be a sha256:<64 hex characters> token") from exc
    return f"sha256:{digest}"


class RevisionConflictError(RuntimeError):
    """A conditional mutation observed a different current file revision."""

    def __init__(self, path: str, expected: str | None, actual: FileRevision | None) -> None:
        self.path = path
        self.expected = normalize_revision_token(expected) if expected is not None else None
        self.actual = actual
        super().__init__(f"Revision conflict for {path!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "revision_conflict",
            "path": self.path,
            "expected_revision": self.expected,
            "actual_revision": self.actual.token if self.actual else None,
        }


class PreconditionRequiredError(PermissionError):
    """Strict mode requires the caller to pin replacement to a prior read."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"expected_revision is required to overwrite {path!r}")

    def to_dict(self) -> dict[str, str]:
        return {"error": "precondition_required", "path": self.path}


@dataclass
class WikiLink:
    target: str
    alias: str | None = None
    heading: str | None = None


@dataclass
class BlockRef:
    block_id: str
    line: int
    text: str


@dataclass
class Callout:
    type: str   # NOTE | WARNING | TIP | IMPORTANT | QUESTION | ...
    title: str
    body: str


@dataclass
class Task:
    text: str
    done: bool
    line: int
    due: str | None = None          # 📅 YYYY-MM-DD
    recurrence: str | None = None   # 🔁 freeform, e.g. "every week"
    priority: str | None = None     # ⏫ high | 🔼 medium | 🔽 low
    done_date: str | None = None    # ✅ YYYY-MM-DD


@dataclass
class Note:
    path: str
    frontmatter: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    wikilinks: list[WikiLink] = field(default_factory=list)
    block_refs: list[BlockRef] = field(default_factory=list)
    block_links: list[str] = field(default_factory=list)
    callouts: list[Callout] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    inline_fields: dict[str, str] = field(default_factory=dict)
    content: str = ""
    mtime: float = 0.0
