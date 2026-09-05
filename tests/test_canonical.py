"""Behaviour contracts for raw Markdown and LiveSync-shaped discovery."""

from __future__ import annotations

import base64

import pytest

from obsidian_mcp.domain.models import RevisionConflictError
from obsidian_mcp.tools import canonical as c


@pytest.fixture
def writable(vault_factory, monkeypatch):
    monkeypatch.setenv("READ_ONLY", "false")
    monkeypatch.setenv("WRITE_PATHS", "")
    return vault_factory


def test_raw_roundtrip_and_literal_edits(writable, tmp_path):
    idx = writable({})
    raw = "\ufeff---\r\n# comment\r\ntags: [a, b]\r\n---\r\n\r\nBody 😀\r\n\r\n"
    created = c.create_file("new/n.md", raw, idx)
    assert c.read_file("new/n.md")["content"] == raw
    assert (tmp_path / "new/n.md").read_bytes() == raw.encode()
    with pytest.raises(RevisionConflictError):
        c.create_file("new/n.md", "overwrite", idx)
    c.edit_file("new/n.md", "bare", created["revision"], idx)
    assert c.read_file("new/n.md")["content"] == "bare"
    c.append_file("new/n.md", "\n  extra  \n\n", index=idx)
    assert c.read_file("new/n.md")["content"] == "bare\n  extra  \n\n"
    c.patch_file("new/n.md", "extra", r"C:\new\1", index=idx)
    assert r"C:\new\1" in c.read_file("new/n.md")["content"]


def test_preconditions_ambiguous_and_missing(writable):
    idx = writable({"n.md": "same same"})
    rev = c.read_file("n.md")["revision"]
    with pytest.raises(c.Problem, match="exactly once"):
        c.patch_file("n.md", "same", "other", index=idx)
    result = c.patch_file("n.md", "same", "other", replaceAll=True, index=idx)
    assert result["replacements"] == 2
    with pytest.raises(RevisionConflictError):
        c.append_file("n.md", "stale", expectedRevision=rev, index=idx)
    with pytest.raises(c.Problem):
        c.edit_file("n.md", "x", "", idx)
    for call in [
        lambda: c.edit_file("missing.md", "x", rev),
        lambda: c.append_file("missing.md", "x"),
    ]:
        with pytest.raises(FileNotFoundError):
            call()
    with pytest.raises(c.Problem):
        c.patch_file("n.md", "", "x")
    with pytest.raises(c.Problem):
        c.patch_file("n.md", "absent", "x")


def test_append_lost_response(writable):
    writable({"n.md": "first"})
    rev = c.read_file("n.md")["revision"]
    c.append_file("n.md", "\nsecond", rev)
    with pytest.raises(RevisionConflictError):
        c.append_file("n.md", "\nsecond", rev)
    assert c.read_file("n.md")["content"] == "first\nsecond"


def test_frontmatter_preserves_body_replaces_arrays_removes(writable):
    writable({"n.md": "---\ntags: [a, b]\nremove_me: yes\nkeep: 2026-01-01\n---\n\nBody\n\n"})
    result = c.patch_frontmatter("n.md", {"tags": ["a"], "nullable": None}, ["remove_me"])
    fm = c.read_frontmatter("n.md")["frontmatter"]
    assert fm == {"tags": ["a"], "keep": "2026-01-01", "nullable": None}
    assert result["removed"] == ["remove_me"]
    assert c.read_file("n.md")["content"].endswith("---\n\nBody\n\n")
    with pytest.raises(c.Problem):
        c.patch_frontmatter("n.md", {"tags": []}, ["tags"])


@pytest.mark.parametrize("yaml", ["tags: [unfinished", "a: 1\na: 2", "- a", "a: &x [*x]", "a: 1"])
def test_malformed_yaml_and_missing_delimiter(writable, yaml):
    raw = f"---\n{yaml}\n" + ("body" if yaml == "a: 1" else "---\nBody")
    writable({"n.md": raw})
    with pytest.raises(c.Problem):
        c.patch_frontmatter("n.md", {"status": "done"})
    assert c.read_file("n.md")["content"] == raw


def test_outline_and_ranges(writable):
    raw = "---\ntitle: example\n---\n# Top\nintro\n```\n# Fake\n```\n## Sub\nx\n# Top\nend\n"
    writable({"n.md": raw, "empty.md": ""})
    outline = c.get_file_outline("n.md")
    assert [(h["text"], h["startLine"], h["endLine"]) for h in outline["headings"]] == [
        ("Top", 4, 10),
        ("Sub", 9, 10),
        ("Top", 11, 12),
    ]
    read = c.read_file("n.md", 9, 10, outline["revision"])
    assert read["content"] == "## Sub\nx\n" and read["partial"]
    c.append_file("n.md", "new")
    with pytest.raises(RevisionConflictError):
        c.read_file("n.md", 9, 10, outline["revision"])
    assert c.read_file("empty.md")["content"] == ""
    assert c.get_file_outline("empty.md")["totalLines"] == 0
    with pytest.raises(c.Problem):
        c.read_file("empty.md", 1)
    with pytest.raises(ValueError):
        c.read_file("n.md", 4, 2)


def test_batch_budget_errors_and_later_small_file(writable, monkeypatch):
    writable({"large.md": "L" * 400, "small.md": "ok"})
    monkeypatch.setattr(c, "MAX_BATCH", 1100)
    batch = c.read_files([c.ReadRequest(path=p) for p in ["large.md", "small.md", "missing.md"]])
    assert batch["files"][0]["result"].get("omitted")
    assert batch["files"][1]["result"]["data"]["content"] == "ok"
    assert batch["files"][2]["result"]["error"]["code"] == "not_found"
    assert c.wire_size(batch) <= c.MAX_BATCH


def test_limits_and_invalid_encoding(writable, tmp_path):
    writable({})
    (tmp_path / "bad.md").write_bytes(b"bad\xff")
    (tmp_path / "large.md").write_bytes(b"x" * (c.MAX_READ + 1))
    for path, code in [("bad.md", "unsupported"), ("large.md", "too_large")]:
        with pytest.raises(c.Problem) as e:
            c.read_file(path)
        assert e.value.code == code
    with pytest.raises(c.Problem):
        c.create_file("new.md", "😀" * (c.MAX_WRITE // 4 + 1))


def exhaust_list(**kwargs):
    result = c.list_page(**kwargs)
    paths = [r["path"] for r in result["files"]]
    while "cursor" in result:
        result = c.list_page(**kwargs, cursor=result["cursor"])
        paths += [r["path"] for r in result["files"]]
    return paths


def test_listing_prefix_keyset_and_cursor_validation(writable):
    writable({f"Projects/{i:02}.md": "text" for i in range(7)} | {"Projectship.md": "x"})
    assert len(exhaust_list(prefix="Projects/", limit=2)) == 7
    assert len(exhaust_list(prefix="Project", limit=2)) == 8
    first = c.list_page("Projects/", 2)
    second = c.list_page("Projects/", 3, first["cursor"])
    assert second["files"][0]["path"] == "Projects/02.md"
    with pytest.raises(c.Problem) as exc:
        c.list_page("Other/", 2, first["cursor"])
    assert exc.value.code == "invalid_input"
    with pytest.raises(c.Problem):
        c.list_page(cursor="garbage")
    assert c.list_page(prefix="Absent/")["files"] == []


def test_search_filters_pagination_change_detection(writable):
    writable(
        {
            f"n{i}.md": f"---\nstatus: active\nscore: {i}\ntags: [one, two]\n---\nMeeting budget"
            for i in range(7)
        }
    )
    filters = [
        c.ValueFilter(property="status", operator="eq", value="active"),
        c.OrderedFilter(property="score", operator="gte", type="number", value=2),
    ]
    first = c.search_files("meeting budget", filters, ["score"], limit=2)
    second = c.search_files("meeting budget", filters, ["score"], limit=3, cursor=first["cursor"])
    assert len(first["results"]) == 2 and len(second["results"]) == 3 and not second["truncated"]
    assert first["results"][0]["properties"] == {"score": 2}
    with pytest.raises(c.Problem) as exc:
        c.search_files("changed", filters, ["score"], cursor=first["cursor"])
    assert exc.value.code == "invalid_input"
    c.append_file("n0.md", "changed non-result")
    with pytest.raises(c.Problem) as exc:
        c.search_files("meeting budget", filters, ["score"], cursor=first["cursor"])
    assert exc.value.code == "cursor_expired"
    assert c.search_files(
        filters=[c.ValueFilter(property="tags", operator="contains", value="#one")]
    )["results"]


@pytest.mark.parametrize(
    ("fm", "op", "value", "expected"),
    [
        ({}, "ne", None, False),
        ({"x": None}, "eq", None, True),
        ({"x": True}, "eq", 1, False),
        ({"x": 1}, "eq", 1.0, True),
        ({"x": [1]}, "contains", True, False),
        ({"x": [1]}, "contains", 1, True),
        ({"x": [1]}, "ne", 2, False),
        ({"x": "1"}, "eq", 1, False),
    ],
)
def test_typed_scalar_filters(fm, op, value, expected):
    assert c.matches(fm, c.ValueFilter(property="x", operator=op, value=value)) is expected


def test_search_coverage_and_budget(writable, tmp_path, monkeypatch):
    writable({"good.md": "---\nx: 1\n---\ntopic", "yaml.md": "---\nx: [\n---\ntopic"})
    (tmp_path / "bad.md").write_bytes(b"\xff")
    text = c.search_files("topic")
    assert len(text["results"]) == 2 and text["unindexedFiles"] == 1
    props = c.search_files("topic", properties=["x"])
    assert len(props["results"]) == 1 and props["unqueryableFiles"] == 1 and props["incomplete"]
    monkeypatch.setattr(c, "MAX_SEARCH_RESPONSE", 100)
    with pytest.raises(c.Problem) as exc:
        c.search_files("topic")
    assert exc.value.code == "too_large"


def test_attachment_create_only_and_base64(writable):
    writable({})
    encoded = base64.b64encode(b"hello").decode()
    c.add_attachment("files/a.txt", encoded)
    assert c.read_attachment("files/a.txt")["contentBase64"] == encoded
    with pytest.raises(RevisionConflictError):
        c.add_attachment("files/a.txt", encoded)
    assert c.list_page(attachment=True)["attachments"][0]["path"] == "files/a.txt"


def test_readonly_and_cursor_policy_binding(vault_factory, monkeypatch):
    monkeypatch.setenv("READ_ONLY", "true")
    vault_factory({"a.md": "x", "b.md": "x"})
    first = c.list_page(limit=1)
    with pytest.raises(PermissionError):
        c.create_file("new.md", "x")
    import obsidian_mcp.config as cfg

    monkeypatch.setenv("DENY_READ_PATHS", "b.md")
    cfg._config = None
    with pytest.raises(c.Problem):
        c.list_page(limit=1, cursor=first["cursor"])
    assert [r["path"] for r in c.search_files("x")["results"]] == ["a.md"]


def test_typed_dates_and_exists(writable):
    writable({"n.md": "---\nwhen: 2026-09-05\nnullable: null\n---\nbody"})
    conditions = [
        c.OrderedFilter(property="when", operator="gte", type="date", value="2026-09-05T00:00:00Z"),
        c.ExistsFilter(property="nullable", operator="exists", value=True),
        c.ExistsFilter(property="absent", operator="exists", value=False),
    ]
    assert len(c.search_files(filters=conditions)["results"]) == 1
    for value in ["yesterday", "2026-02-30", "2026-09-05T12:00:00"]:
        with pytest.raises(ValueError):
            c.OrderedFilter(property="when", operator="gte", type="date", value=value)


def test_search_pages_have_no_omissions_and_restart_after_insert(writable):
    writable({f"n{i:02}.md": "topic" for i in range(13)})
    page = c.search_files("topic", limit=3)
    first_cursor = page["cursor"]
    paths = [r["path"] for r in page["results"]]
    while page.get("cursor"):
        page = c.search_files("topic", limit=3, cursor=page["cursor"])
        paths.extend(r["path"] for r in page["results"])
    assert paths == [f"n{i:02}.md" for i in range(13)]
    c.create_file("added.md", "topic")
    with pytest.raises(c.Problem) as exc:
        c.search_files("topic", cursor=first_cursor)
    assert exc.value.code == "cursor_expired"


def test_symlinks_and_paths_are_not_readable(writable, tmp_path):
    writable({"n.md": "topic"})
    (tmp_path / "link.md").symlink_to(tmp_path / "n.md")
    with pytest.raises(ValueError):
        c.read_file("link.md")
    assert [item["path"] for item in c.list_page()["files"]] == ["n.md"]
    for path in ["../escape.md", "/absolute.md"]:
        with pytest.raises(ValueError):
            c.create_file(path, "x")


def test_strict_nested_requests_reject_unknown_fields():
    with pytest.raises(ValueError):
        c.ReadRequest(path="n.md", operation="delete")
    with pytest.raises(ValueError):
        c.ValueFilter(property="x", operator="regex", value=".*")


def test_unrelated_yaml_properties_and_noop_preserve_exact_text(writable):
    raw = "---\r\n# comment\r\nx: 1\r\ny: [a, b]\r\n---\r\n\r\nBody  \r\n"
    writable({"n.md": ""})
    c.create_file("crlf.md", raw)
    before = c.read_file("crlf.md")
    result = c.patch_frontmatter("crlf.md", {"x": 1})
    assert not result["updated"]
    assert c.read_file("crlf.md") == before
    c.patch_frontmatter("crlf.md", {"x": 2})
    assert c.read_file("crlf.md")["content"].endswith("---\r\n\r\nBody  \r\n")
    assert c.read_frontmatter("crlf.md")["frontmatter"]["y"] == ["a", "b"]
