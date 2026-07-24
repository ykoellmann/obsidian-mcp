from __future__ import annotations

import logging
import re

import frontmatter

from .models import BlockRef, Callout, Note, Task, WikiLink

logger = logging.getLogger(__name__)

# [[Target]], [[Target|Alias]], [[Target#Heading]], [[Target#Heading|Alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|#^]+)(?:#([^\]|^]+))?(?:\|([^\]]+))?\]\]")

# [[Note^block-id]] or [[Note^block-id|Alias]]
_BLOCK_LINK_RE = re.compile(r"\[\[([^\]^]+)\^([\w-]+)(?:\|[^\]]*)?\]\]")

# Paragraph ending with ^block-id
_BLOCK_REF_RE = re.compile(r"^(.+?)\s+\^([\w-]+)\s*$", re.MULTILINE)

# > [!TYPE] Title\n> body lines
_CALLOUT_RE = re.compile(
    r"^> \[!([\w]+)\][ \t]*(.*?)\n((?:>[ \t]*.*\n?)*)",
    re.MULTILINE,
)

# - [ ] text  or  - [x] text
_TASK_RE = re.compile(r"^- \[([ xX])\] (.+)$", re.MULTILINE)

# key:: value  (Dataview inline fields — must start at line beginning)
_INLINE_FIELD_RE = re.compile(r"^([\w][\w /-]*)::[ \t]*(.+)$", re.MULTILINE)


def parse_note(raw_content: str, path: str = "") -> Note:
    try:
        post = frontmatter.loads(raw_content)
        fm = dict(post.metadata)
        body = post.content
    except Exception:
        logger.warning("Failed to parse frontmatter in %s – treating as plain content", path)
        fm = {}
        body = raw_content

    return Note(
        path=path,
        frontmatter=fm,
        tags=extract_tags(fm, body),
        aliases=extract_aliases(fm),
        wikilinks=extract_wikilinks(body),
        block_refs=extract_block_refs(body),
        block_links=extract_block_links(body),
        callouts=extract_callouts(body),
        tasks=extract_tasks(body),
        inline_fields=extract_inline_fields(body),
        content=body,
    )


def extract_aliases(fm: dict) -> list[str]:
    raw = fm.get("aliases", [])
    if isinstance(raw, str):
        return [a.strip() for a in raw.split(",") if a.strip()]
    if isinstance(raw, list):
        return [str(a).strip() for a in raw if a]
    return []


def extract_tags(fm: dict, body: str) -> list[str]:
    tags: list[str] = []

    fm_tags = fm.get("tags", [])
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(",")]
    if isinstance(fm_tags, list):
        tags.extend(str(t).strip().lstrip("#") for t in fm_tags if t)

    for match in re.finditer(r"(?<!\[)#([\w/-]+)", body):
        tag = match.group(1)
        if tag not in tags:
            tags.append(tag)

    return tags


def extract_wikilinks(body: str) -> list[WikiLink]:
    links: list[WikiLink] = []
    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        heading = m.group(2).strip() if m.group(2) else None
        alias = m.group(3).strip() if m.group(3) else None
        links.append(WikiLink(target=target, heading=heading, alias=alias))
    return links


def extract_block_refs(body: str) -> list[BlockRef]:
    refs: list[BlockRef] = []
    for m in _BLOCK_REF_RE.finditer(body):
        line_num = body[: m.start()].count("\n") + 1
        refs.append(BlockRef(block_id=m.group(2), line=line_num, text=m.group(1).strip()))
    return refs


def extract_block_links(body: str) -> list[str]:
    return [f"{m.group(1)}^{m.group(2)}" for m in _BLOCK_LINK_RE.finditer(body)]


def extract_callouts(body: str) -> list[Callout]:
    callouts: list[Callout] = []
    for m in _CALLOUT_RE.finditer(body):
        callout_type = m.group(1).upper()
        title = m.group(2).strip()
        raw_body = m.group(3)
        clean_body = re.sub(r"^>[ \t]?", "", raw_body, flags=re.MULTILINE).strip()
        callouts.append(Callout(type=callout_type, title=title, body=clean_body))
    return callouts


def extract_tasks(body: str) -> list[Task]:
    tasks: list[Task] = []
    for m in _TASK_RE.finditer(body):
        line_num = body[: m.start()].count("\n") + 1
        done = m.group(1).lower() == "x"
        tasks.append(Task(text=m.group(2).strip(), done=done, line=line_num))
    return tasks


def extract_inline_fields(body: str) -> dict[str, str]:
    """Extract Dataview-style inline fields (key:: value) from the note body."""
    return {
        m.group(1).strip(): m.group(2).strip()
        for m in _INLINE_FIELD_RE.finditer(body)
    }
