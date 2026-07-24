from __future__ import annotations

from dataclasses import dataclass, field


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
