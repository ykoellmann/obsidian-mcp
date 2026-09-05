"""Raw Markdown operations for the common LiveSync-shaped MCP surface.

Parsing is reserved for outlines, property operations and indexed extras.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import re
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

import yaml
from markdown_it import MarkdownIt
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from ..config import get_config
from ..domain.models import RevisionConflictError, normalize_revision_token
from ..storage.filesystem import VaultStorage
from ..storage.locking import acquire_lock
from ..storage.policy import matches_path_rule
from .attachments import _ALLOWED_ATTACHMENT_SUFFIXES, validate_attachment_path
from .audit import log_write

MAX_READ = 512_000
MAX_WRITE = 512_000
MAX_PATCH = 64_000
MAX_BATCH = 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
MAX_SEARCH_RESPONSE = 128 * 1024
PathArg = Annotated[str, Field(min_length=1, max_length=1024)]
RevisionArg = Annotated[str, Field(min_length=1, max_length=256)]
PrefixArg = Annotated[str, Field(max_length=1024)]
CursorArg = Annotated[str, Field(min_length=1, max_length=2048)]
PropertyArg = Annotated[str, Field(min_length=1, max_length=256)]
LineArg = Annotated[int, Field(strict=True, ge=1)]
ListLimit = Annotated[int, Field(strict=True, ge=1, le=100)]
SearchLimit = Annotated[int, Field(strict=True, ge=1, le=50)]
Scalar = StrictStr | StrictBool | StrictInt | StrictFloat | None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReadRequest(Input):
    path: PathArg
    startLine: LineArg | None = None
    endLine: LineArg | None = None
    expectedRevision: RevisionArg | None = None

    @model_validator(mode="after")
    def ordered_range(self):
        if self.endLine is not None and self.endLine < (self.startLine or 1):
            raise ValueError("endLine must not precede startLine")
        return self


class ValueFilter(Input):
    property: PropertyArg
    operator: Literal["eq", "ne", "contains"]
    value: Scalar

    @model_validator(mode="after")
    def bounded(self):
        if isinstance(self.value, str) and len(self.value) > 2048:
            raise ValueError("Filter value exceeds 2048 characters")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("Filter numbers must be finite")
        return self


class ExistsFilter(Input):
    property: PropertyArg
    operator: Literal["exists"]
    value: StrictBool


class OrderedFilter(Input):
    property: PropertyArg
    operator: Literal["lt", "lte", "gt", "gte"]
    type: Literal["number", "date"]
    value: StrictStr | StrictInt | StrictFloat

    @model_validator(mode="after")
    def typed(self):
        if self.type == "number":
            if not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
                raise ValueError("Ordered numeric filters require a finite number")
        elif not isinstance(self.value, str) or iso_time(self.value) is None:
            raise ValueError("Date filters require an ISO date or timezone-qualified timestamp")
        return self


Filter = ValueFilter | ExistsFilter | OrderedFilter
Filters = Annotated[list[Filter], Field(min_length=1, max_length=16)]
Properties = Annotated[list[PropertyArg], Field(max_length=20)]
Batch = Annotated[list[ReadRequest], Field(min_length=1, max_length=10)]
Updates = dict[PropertyArg, JsonValue]


class Problem(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def wire_size(data) -> int:
    return len(
        dumps(
            {"content": [{"type": "text", "text": dumps(data)}], "structuredContent": data}
        ).encode("utf-8")
    )


def bounded_text(content: str, maximum: int, *, nonempty: bool = False) -> bytes:
    try:
        encoded = content.encode("utf-8")
    except UnicodeError as exc:
        raise Problem("invalid_input", "Text must be valid UTF-8") from exc
    if nonempty and not encoded:
        raise Problem("invalid_input", "Text must not be empty")
    if len(encoded) > maximum:
        raise Problem("too_large", f"Content exceeds {maximum} UTF-8 bytes")
    return encoded


def note_path(path: str) -> str:
    if not path.lower().endswith(".md"):
        raise Problem("invalid_input", "Markdown paths must end in .md")
    return path


def raw_read(path: str, expected: str | None = None) -> tuple[str, str]:
    storage = VaultStorage.from_config()
    path = storage.resolve_read(note_path(path)).relative
    try:
        data, revision = storage.read_bytes_with_revision(path, max_bytes=MAX_READ)
    except OverflowError as exc:
        raise Problem("too_large", str(exc)) from exc
    if expected is not None and revision.token != normalize_revision_token(expected):
        raise RevisionConflictError(path, expected, revision)
    try:
        return data.decode("utf-8"), revision.token
    except UnicodeError as exc:
        raise Problem("unsupported", "Markdown must contain valid UTF-8") from exc


def file_lines(content: str) -> list[str]:
    # Unlike str.splitlines(), only CR/LF sequences are Markdown line endings.
    lines = re.findall(r"[^\r\n]*(?:\r\n|\n|\r|$)", content)
    return lines[:-1] if lines and lines[-1] == "" else lines


def read_file(path: str, startLine=None, endLine=None, expectedRevision=None) -> dict:
    request = ReadRequest(
        path=path, startLine=startLine, endLine=endLine, expectedRevision=expectedRevision
    )
    raw, revision = raw_read(path, expectedRevision)
    result = {
        "path": VaultStorage.from_config().resolve_read(path).relative,
        "revision": revision,
        "content": raw,
    }
    if startLine is not None or endLine is not None:
        lines = file_lines(raw)
        start = request.startLine or 1
        if start > len(lines):
            raise Problem("invalid_input", "startLine exceeds the file line count")
        end = min(request.endLine or len(lines), len(lines))
        result.update(
            content="".join(lines[start - 1 : end]),
            startLine=start,
            endLine=end,
            totalLines=len(lines),
            partial=start > 1 or end < len(lines),
        )
    return result


def error_data(exc: Exception) -> dict:
    if isinstance(exc, RevisionConflictError):
        return {
            "code": "revision_conflict",
            "message": str(exc),
            "path": exc.path,
            "resolution": "reread_and_reassess",
        }
    if isinstance(exc, Problem):
        code = exc.code
    elif isinstance(exc, PermissionError):
        code = "permission_denied"
    elif isinstance(exc, FileNotFoundError):
        code = "not_found"
    elif isinstance(exc, (ValueError, TypeError)):
        code = "invalid_input"
    elif isinstance(exc, OSError):
        return {"code": "unavailable", "message": "Filesystem operation unavailable"}
    else:
        return {"code": "internal", "message": "Operation failed"}
    return {"code": code, "message": str(exc)}


def read_files(files: list[ReadRequest]) -> dict:
    if not 1 <= len(files) <= 10:
        raise Problem("invalid_input", "Supply 1-10 files")
    data = {
        "files": [
            {
                "index": i,
                "path": request.path,
                "result": {"omitted": True, "reason": "response_budget"},
            }
            for i, request in enumerate(files)
        ]
    }
    for item, request in zip(data["files"], files, strict=True):
        reserved = item["result"]
        try:
            item["result"] = {"ok": True, "data": read_file(**request.model_dump())}
        except Exception as exc:
            item["result"] = {"ok": False, "error": error_data(exc)}
        if wire_size(data) > MAX_BATCH:
            item["result"] = reserved
    return data


class StrictLoader(yaml.SafeLoader):
    """Safe YAML with bounded expansion and no duplicate/non-string keys."""

    def __init__(self, stream):
        super().__init__(stream)
        self.nodes = 0
        self.depth = 0

    def compose_node(self, parent, index):
        self.nodes += 1
        self.depth += 1
        try:
            if self.nodes > 10_000 or self.depth > 32 or self.check_event(yaml.AliasEvent):
                raise ValueError("YAML exceeds complexity limits or uses aliases")
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or not 1 <= len(key) <= 256 or key in result:
                raise ValueError(
                    "YAML keys must be unique nonempty strings, at most 256 characters"
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("Unsupported YAML value")


def frontmatter(raw: str) -> tuple[dict, str, str, str]:
    bom = "\ufeff" if raw.startswith("\ufeff") else ""
    text = raw[len(bom) :]
    newline = "\r\n" if "\r\n" in text else "\n"
    if not re.match(r"^---\r?\n", text):
        return {}, text, newline, bom
    match = re.match(r"^---\r?\n(.*?)^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)", text, re.S | re.M)
    if not match:
        raise Problem("invalid_input", "Unterminated YAML frontmatter")
    try:
        fm = yaml.load(match[1], Loader=StrictLoader)
        fm = {} if fm is None else fm
        if not isinstance(fm, dict):
            raise ValueError("YAML frontmatter must be a mapping")
        # Validate normalization, while preserving YAML scalar types for mutation.
        json_value(fm)
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        raise Problem("invalid_input", f"Invalid YAML frontmatter: {exc}") from exc
    return fm, text[match.end() :], newline, bom


def read_frontmatter(path: str) -> dict:
    raw, revision = raw_read(path)
    fm, _, _, _ = frontmatter(raw)
    return {"path": path, "revision": revision, "frontmatter": json_value(fm)}


def get_file_outline(path: str) -> dict:
    raw, revision = raw_read(path)
    lines = file_lines(raw)
    if len(lines) > 8192:
        raise Problem("too_large", "Outline exceeds 8192 lines; use ranged read_file")
    markdown = list(lines)
    if markdown and markdown[0].lstrip("\ufeff").strip() == "---":
        end = next(
            (i for i in range(1, len(markdown)) if markdown[i].strip() in {"---", "..."}), None
        )
        if end is not None:
            markdown[: end + 1] = ["\n"] * (end + 1)
    parser = MarkdownIt("commonmark", {"html": True})
    headings = []
    heading_bytes = 0
    tokens = parser.parse("".join(markdown))
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        inline = tokens[i + 1]
        heading_bytes += len(inline.content.encode("utf-8"))
        if len(headings) >= 1024 or heading_bytes > 32768:
            raise Problem("too_large", "Outline exceeds heading limits; use ranged read_file")
        text = "".join(
            child.content
            if child.type in {"text", "code_inline", "image"}
            else " "
            if child.type in {"softbreak", "hardbreak"}
            else ""
            for child in (inline.children or [])
        )
        headings.append(
            {
                "text": text,
                "level": int(token.tag[1:]),
                "startLine": token.map[0] + 1,
                "endLine": len(lines),
            }
        )
    opened = []
    for heading in headings:
        while opened and opened[-1]["level"] >= heading["level"]:
            opened.pop()["endLine"] = heading["startLine"] - 1
        opened.append(heading)
    return {"path": path, "revision": revision, "totalLines": len(lines), "headings": headings}


def mutate(
    path: str, transform, expected: str | None, tool: str, index=None, *, create=False
) -> dict:
    storage = VaultStorage.from_config()
    path = storage.resolve_write(note_path(path)).relative
    # All canonical mutations also require read access, including creation.
    storage.resolve_read(path)
    with acquire_lock(path, lock_path=get_config().lock_path):
        if create:
            content = transform("")
            revision = storage.write_bytes_atomic(
                path, bounded_text(content, MAX_WRITE), create_only=True
            )
        else:
            raw, current = raw_read(path, expected)
            content = transform(raw)
            encoded = bounded_text(content, MAX_WRITE)
            revision = storage.write_bytes_atomic(path, encoded, expected_revision=current)
    log_write(tool, path, "updated Markdown")
    if index is not None:
        index.update(path)
    return {"path": path, "revision": revision.token}


def create_file(path: str, content: str, index=None) -> dict:
    return mutate(path, lambda _: content, None, "create_file", index, create=True)


def edit_file(path: str, content: str, expectedRevision: str, index=None) -> dict:
    if not expectedRevision:
        raise Problem("invalid_input", "expectedRevision is required for full replacement")
    return mutate(path, lambda _: content, expectedRevision, "edit_file", index)


def append_file(path: str, content: str, expectedRevision=None, index=None) -> dict:
    bounded_text(content, MAX_WRITE, nonempty=True)
    return mutate(path, lambda raw: raw + content, expectedRevision, "append_file", index)


def patch_file(
    path: str, oldText: str, newText: str, replaceAll=False, expectedRevision=None, index=None
) -> dict:
    bounded_text(oldText, MAX_PATCH, nonempty=True)
    bounded_text(newText, MAX_PATCH)
    replacements = 0

    def transform(raw):
        nonlocal replacements
        replacements = raw.count(oldText)
        if replacements == 0 or (not replaceAll and replacements != 1):
            raise Problem(
                "invalid_input", "oldText must match exactly once unless replaceAll is true"
            )
        return raw.replace(oldText, newText)

    result = mutate(path, transform, expectedRevision, "patch_file", index)
    return {**result, "replacements": replacements}


def patch_frontmatter(
    path: str, updates: dict, remove=None, expectedRevision=None, index=None
) -> dict:
    remove = list(dict.fromkeys(remove or []))
    if not updates and not remove:
        raise Problem("invalid_input", "Supply at least one update or removal")
    if set(updates) & set(remove) or len(updates) + len(remove) > 100:
        raise Problem(
            "invalid_input", "At most 100 distinct, non-overlapping updates/removals allowed"
        )
    # SafeLoader bounds depth/node count and mapping keys without a second validator.
    bounded_text(dumps(updates), MAX_READ)
    yaml.load(dumps(updates), Loader=StrictLoader)
    updated, removed = [], []

    def transform(raw):
        fm, body, newline, bom = frontmatter(raw)
        for key, value in updates.items():
            if key not in fm or dumps(json_value(fm[key])) != dumps(value):
                updated.append(key)
                fm[key] = value
        for key in remove:
            if key in fm:
                removed.append(key)
                del fm[key]
        if not updated and not removed:
            return raw
        rendered = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).replace("\n", newline)
        return f"{bom}---{newline}{rendered}---{newline}{body}"

    result = mutate(path, transform, expectedRevision, "patch_frontmatter", index)
    return {**result, "updated": updated, "removed": removed}


def read_attachment(path: str) -> dict:
    if path.lower().endswith(".md"):
        raise Problem("invalid_input", "Use read_file for Markdown")
    path = validate_attachment_path(path)
    try:
        data, revision = VaultStorage.from_config().read_bytes_with_revision(
            path, max_bytes=MAX_READ
        )
    except OverflowError as exc:
        raise Problem("too_large", str(exc)) from exc
    return {
        "path": path,
        "revision": revision.token,
        "mimeType": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "sizeBytes": len(data),
        "contentBase64": base64.b64encode(data).decode("ascii"),
    }


def add_attachment(path: str, contentBase64: str) -> dict:
    cfg = get_config()
    path = validate_attachment_path(path, write=True)
    storage = VaultStorage.from_config()
    storage.resolve_read(path)
    if len(contentBase64) > ((cfg.max_attachment_bytes + 2) // 3) * 4:
        raise Problem("too_large", "Attachment exceeds MAX_ATTACHMENT_BYTES")
    try:
        data = base64.b64decode(contentBase64, validate=True)
    except ValueError as exc:
        raise Problem("invalid_input", "Invalid base64 content") from exc
    if len(data) > cfg.max_attachment_bytes:
        raise Problem("too_large", "Attachment exceeds MAX_ATTACHMENT_BYTES")
    revision = storage.write_bytes_atomic(path, data, create_only=True)
    log_write("add_attachment", path, "created attachment")
    return {
        "path": path,
        "revision": revision.token,
        "sizeBytes": len(data),
        "mimeType": mimetypes.guess_type(path)[0] or "application/octet-stream",
    }


def prefix_value(prefix: str) -> str:
    if (
        len(prefix) > 1024
        or "\\" in prefix
        or "\x00" in prefix
        or prefix.startswith("/")
        or ":" in prefix
        or any(p in {".", ".."} for p in prefix.split("/"))
    ):
        raise Problem("invalid_input", "Prefix must be a safe vault-relative path prefix")
    return prefix


def scope_id(kind: str, arguments: dict) -> str:
    cfg = get_config()
    policy = VaultStorage.from_config().policy
    # Only hashes go into cursors; no root paths or policy details are disclosed.
    values = {
        "kind": kind,
        "vault": cfg.resolve_vault_name(),
        "root": str(policy.root),
        "read": policy.read_paths,
        "deny": policy.deny_read_paths,
        "exclude": cfg.exclude_paths,
        "arguments": arguments,
    }
    return hashlib.sha256(dumps(values).encode()).hexdigest()


def cursor_encode(scope: str, **fields) -> str:
    return (
        base64.urlsafe_b64encode(dumps({"v": 1, "scope": scope, **fields}).encode())
        .decode()
        .rstrip("=")
    )


def cursor_decode(cursor: str | None, scope: str) -> dict | None:
    if cursor is None:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError()
        data = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("scope") != scope:
            raise ValueError()
        return data
    except (ValueError, UnicodeError) as exc:
        raise Problem(
            "invalid_input", "Invalid cursor or changed query; restart without a cursor"
        ) from exc


def candidates(prefix: str, *, attachment=False):
    storage = VaultStorage.from_config()
    cfg = get_config()
    # Enumeration is always policy filtered; prefixes need not be real directories.
    for entry in sorted(storage.list_files(), key=lambda e: e.relative):
        path = entry.relative
        if not path.startswith(prefix) or any(
            matches_path_rule(path, rule) for rule in cfg.exclude_paths
        ):
            continue
        if attachment:
            if PurePosixPath(path).suffix.lower() not in _ALLOWED_ATTACHMENT_SUFFIXES:
                continue
            try:
                validate_attachment_path(path)
            except ValueError:
                continue
        elif not path.lower().endswith(".md"):
            continue
        yield entry


def list_page(prefix="", limit=50, cursor=None, *, attachment=False) -> dict:
    prefix_value(prefix)
    if not 1 <= limit <= 100:
        raise Problem("invalid_input", "limit must be 1-100")
    scope = scope_id("attachments" if attachment else "files", {"prefix": prefix})
    continuation = cursor_decode(cursor, scope)
    if continuation is not None and (
        set(continuation) != {"v", "scope", "after"} or not isinstance(continuation["after"], str)
    ):
        raise Problem("invalid_input", "Invalid listing cursor")
    after = continuation["after"] if continuation else ""
    items = []
    more = False
    storage = VaultStorage.from_config()
    for entry in candidates(prefix, attachment=attachment):
        if entry.relative <= after:
            continue
        if len(items) == limit:
            more = True
            break
        try:
            revision = storage.revision(entry.relative)
        except FileNotFoundError:
            continue
        item = {
            "path": entry.relative,
            "revision": revision.token,
            "sizeBytes": revision.size,
            "modifiedAt": revision.mtime_ns // 1_000_000,
        }
        if attachment:
            item["mimeType"] = mimetypes.guess_type(entry.relative)[0] or "application/octet-stream"
        items.append(item)
    return {
        "attachments" if attachment else "files": items,
        **({"cursor": cursor_encode(scope, after=items[-1]["path"])} if more else {}),
    }


def iso_time(value):
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC).timestamp()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})", value):
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def scalar_equal(left, right):
    if isinstance(left, (dict, list)):
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def matches(fm: dict, condition: Filter) -> bool:
    key, op, value = condition.property, condition.operator, condition.value
    if op == "exists":
        return (key in fm) == value
    if key not in fm:
        return False
    actual = fm[key]
    if key == "tags" and isinstance(value, str):
        value = value.removeprefix("#")
    if op == "contains":
        return isinstance(actual, list) and any(scalar_equal(item, value) for item in actual)
    if op in {"eq", "ne"}:
        if isinstance(actual, (dict, list)):
            return False
        equal = scalar_equal(actual, value)
        return equal if op == "eq" else not equal
    if condition.type == "date":
        actual, value = iso_time(actual), iso_time(value)
        if actual is None or value is None:
            return False
    elif type(actual) not in (int, float):
        return False
    return {
        "lt": actual < value,
        "lte": actual <= value,
        "gt": actual > value,
        "gte": actual >= value,
    }[op]


def truncate_utf8(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def search_files(
    query=None, filters=None, properties=None, pathPrefix="", limit=20, cursor=None
) -> dict:
    prefix_value(pathPrefix)
    query = query.strip() if query is not None else None
    if query is not None:
        bounded_text(query, 256, nonempty=True)
        if len(query.split()) > 16:
            raise Problem("invalid_input", "Query permits at most 16 terms")
    if query is None and not filters:
        raise Problem("invalid_input", "Supply query text or nonempty filters")
    if not 1 <= limit <= 50:
        raise Problem("invalid_input", "limit must be 1-50")
    filters = filters or []
    scope = scope_id(
        "search",
        {
            "query": query,
            "filters": [f.model_dump() for f in filters],
            "properties": properties,
            "pathPrefix": pathPrefix,
        },
    )
    continuation = cursor_decode(cursor, scope)
    offset = 0
    if continuation is not None:
        if (
            set(continuation) != {"v", "scope", "fingerprint", "offset"}
            or type(continuation["offset"]) is not int
            or continuation["offset"] < 0
            or not isinstance(continuation["fingerprint"], str)
        ):
            raise Problem("invalid_input", "Invalid search cursor")
        offset = continuation["offset"]
    terms = query.casefold().split() if query else []
    fingerprint = hashlib.sha256()
    results = []
    unindexed = unqueryable = 0
    # ponytail: re-scan/hash per page, O(vault size); add an index only after measured latency warrants it.
    for entry in candidates(pathPrefix):
        path = entry.relative
        try:
            raw, revision = raw_read(path)
            fingerprint.update(dumps([path, revision]).encode())
        except (OSError, Problem) as exc:
            if isinstance(exc, PermissionError):
                continue
            unindexed += 1
            fingerprint.update(dumps([path, error_data(exc)["code"]]).encode())
            continue
        fm = {}
        if filters or properties is not None:
            try:
                fm = json_value(frontmatter(raw)[0])
                tags = fm.get("tags")
                if isinstance(tags, (str, list)):
                    tags = re.split(r"[,\s]+", tags) if isinstance(tags, str) else tags
                    fm["tags"] = [
                        tag.removeprefix("#")
                        for tag in tags
                        if isinstance(tag, str) and tag.removeprefix("#")
                    ]
            except Problem:
                unqueryable += 1
                continue
        haystack = (path + "\n" + raw).casefold()
        if not all(term in haystack for term in terms) or not all(matches(fm, f) for f in filters):
            continue
        snippet = ""
        if terms:
            snippet = next(
                (
                    line
                    for line in file_lines(raw)
                    if any(term in line.casefold() for term in terms)
                ),
                path,
            )
        result = {"path": path, "revision": revision, "snippet": truncate_utf8(snippet, 1024)}
        if properties is not None:
            result["properties"] = {key: fm[key] for key in properties if key in fm}
        score = sum(term in PurePosixPath(path).name.casefold() for term in terms)
        results.append((score, result))
    generation = fingerprint.hexdigest()
    if continuation and continuation["fingerprint"] != generation:
        raise Problem(
            "cursor_expired", "Search results changed; restart search_files without a cursor"
        )
    results.sort(key=lambda pair: (-pair[0], pair[1]["path"]))
    if offset > len(results):
        raise Problem("invalid_input", "Invalid search offset")
    data = {
        "results": [],
        "truncated": False,
        "incomplete": bool(unindexed or unqueryable),
        "unindexedFiles": unindexed,
    }
    if filters or properties is not None:
        data["unqueryableFiles"] = unqueryable

    def continuation_fields():
        more = offset + len(data["results"]) < len(results)
        data["truncated"] = more
        if more:
            data["cursor"] = cursor_encode(
                scope, fingerprint=generation, offset=offset + len(data["results"])
            )
        else:
            data.pop("cursor", None)

    for _, result in results[offset : offset + limit]:
        data["results"].append(result)
        continuation_fields()
        if wire_size(data) > MAX_SEARCH_RESPONSE:
            data["results"].pop()
            if not data["results"]:
                raise Problem(
                    "too_large",
                    "Selected properties exceed response budget; request fewer properties",
                )
            break
    continuation_fields()
    return data
