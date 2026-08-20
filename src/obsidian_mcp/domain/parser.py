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

# Tasks-plugin emoji markers, extracted out of the task text separately.
_TASK_DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
_TASK_DONE_DATE_RE = re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})")
_TASK_PRIORITY_RE = re.compile(r"[⏫🔼🔽]")
_TASK_PRIORITY_MAP = {"⏫": "high", "🔼": "medium", "🔽": "low"}
_TASK_RECURRENCE_RE = re.compile(r"🔁\s*([^\n]*?)(?=\s*[📅✅⏫🔼🔽]|$)")

# key:: value  (Dataview inline fields — must start at line beginning)
_INLINE_FIELD_RE = re.compile(r"^([\w][\w /-]*)::[ \t]*(.+)$", re.MULTILINE)

# Fenced code blocks (```...```) and inline code spans (`...`) — inline tags
# inside either don't count as real tags (mirrors Obsidian's own behavior,
# e.g. a sentence explaining "`#tag`-Syntax" shouldn't itself become a tag).
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

# A tag must contain at least one non-numeric character — Obsidian doesn't
# treat purely-numeric strings like "#1" as valid tags, since that syntax is
# commonly used for cross-references ("see point #9") rather than tagging.
_PURELY_NUMERIC_RE = re.compile(r"^\d+$")


def _strip_code(body: str) -> str:
    """Blank out fenced code blocks and inline code spans, preserving length/
    line numbers (replaced with spaces/newlines-preserved) so other regexes
    that rely on positions in the original body still line up."""
    body = _CODE_BLOCK_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), body)
    body = _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), body)
    return body


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

    for match in re.finditer(r"(?<!\[)#([\w/-]+)", _strip_code(body)):
        tag = match.group(1)
        if _PURELY_NUMERIC_RE.match(tag):
            continue
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


def _extract_task_markers(
    text: str,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Pull Tasks-plugin emoji markers (due/done-date/priority/recurrence) out
    of a task line, returning (clean_text, due, recurrence, priority, done_date)."""
    due = None
    if m := _TASK_DUE_RE.search(text):
        due = m.group(1)
        text = text[: m.start()] + text[m.end():]

    done_date = None
    if m := _TASK_DONE_DATE_RE.search(text):
        done_date = m.group(1)
        text = text[: m.start()] + text[m.end():]

    priority = None
    if m := _TASK_PRIORITY_RE.search(text):
        priority = _TASK_PRIORITY_MAP[m.group(0)]
        text = text[: m.start()] + text[m.end():]

    recurrence = None
    if m := _TASK_RECURRENCE_RE.search(text):
        recurrence = m.group(1).strip() or None
        text = text[: m.start()] + text[m.end():]

    return re.sub(r"[ \t]{2,}", " ", text).strip(), due, recurrence, priority, done_date


def extract_tasks(body: str) -> list[Task]:
    tasks: list[Task] = []
    for m in _TASK_RE.finditer(body):
        line_num = body[: m.start()].count("\n") + 1
        done = m.group(1).lower() == "x"
        text, due, recurrence, priority, done_date = _extract_task_markers(m.group(2).strip())
        tasks.append(
            Task(
                text=text,
                done=done,
                line=line_num,
                due=due,
                recurrence=recurrence,
                priority=priority,
                done_date=done_date,
            )
        )
    return tasks


def extract_inline_fields(body: str) -> dict[str, str]:
    """Extract Dataview-style inline fields (key:: value) from the note body."""
    return {
        m.group(1).strip(): m.group(2).strip()
        for m in _INLINE_FIELD_RE.finditer(body)
    }
