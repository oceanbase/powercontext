# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Map extracted HTML trees into blocks and protect the shared packing contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
import pc_knowledge_base.content as content
from pc_knowledge_base.content import ContentError, html_blocks, parse, parser_profile, relative_path
from pc_knowledge_base.service import reuse_parsed_result


def test_headings_build_paths_and_do_not_rewrite_earlier_blocks():
    xml = """<doc><main>
<head rend="h1">Top</head><p>one</p>
<head rend="h2">Second</head><p>two</p>
<head rend="h3">Third</head><p>three</p>
<head rend="2">Sibling</head><p>four</p>
</main></doc>"""
    blocks = html_blocks(xml)
    assert [block["heading_path"] for block in blocks] == [
        ["Top"],
        ["Top"],
        ["Top", "Second"],
        ["Top", "Second"],
        ["Top", "Second", "Third"],
        ["Top", "Second", "Third"],
        ["Top", "Sibling"],
        ["Top", "Sibling"],
    ]
    assert blocks[1]["heading_path"] == ["Top"]


def test_blank_heading_is_ignored_and_missing_headings_leave_an_empty_path():
    blocks = html_blocks('<doc><main><head rend="h2">  </head><p>body</p></main></doc>')
    assert [(block["text"], block["heading_path"]) for block in blocks] == [("body", [])]


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        (
            "<doc><main><table><row><cell>a</cell><cell>b</cell></row><row><cell>c</cell><cell>d</cell></row></table></main></doc>",
            {"block_type": "table", "rows": 2, "cols": 2},
        ),
        ("<doc><main><list><item>one</item><item>two</item></list></main></doc>", {"block_type": "list"}),
        ("<doc><main><quote><p>first</p><p>second</p></quote></main></doc>", {"block_type": "quote"}),
        ('<doc><main><code rend="python">print(1)\nprint(2)</code></main></doc>', {"block_type": "code"}),
    ],
)
def test_structured_elements_produce_single_blocks(xml, expected):
    blocks = html_blocks(xml)
    assert len(blocks) == 1
    for key, value in expected.items():
        assert blocks[0]["locator"][key] == value


def test_table_lists_quotes_and_line_breaks_render_text():
    table = html_blocks(
        "<doc><main><table><row><cell>Name</cell><cell>Value</cell></row>"
        "<row><cell>Timeout</cell><cell>30s</cell></row></table></main></doc>"
    )
    assert table[0]["text"] == "Name | Value\nTimeout | 30s"
    lists = html_blocks("<doc><main><list><item>one</item><item>two</item></list></main></doc>")
    assert lists[0]["text"] == "one\ntwo"
    quotes = html_blocks("<doc><main><quote><p>first</p><p>second</p></quote></main></doc>")
    assert quotes[0]["text"] == "first\n\nsecond"
    codes = html_blocks('<doc><main><code rend="python">def run():\n    return 1</code></main></doc>')
    assert codes[0]["text"] == "def run():\n    return 1"
    assert codes[0]["locator"]["language"] == "python"
    broken = html_blocks("<doc><main><p>a<lb/>b</p></main></doc>")
    assert broken[0]["text"] == "a\nb"


def test_inline_elements_keep_their_separating_space():
    blocks = html_blocks("<doc><main><p>Run <code>make test</code> <code>make lint</code> now.</p></main></doc>")
    assert blocks[0]["text"] == "Run make test make lint now."


def test_nested_lists_keep_item_boundaries():
    blocks = html_blocks("<doc><main><list><item>one<list><item>nested</item></list></item></list></main></doc>")
    assert blocks[0]["text"] == "one\nnested"


def test_embedded_tables_are_separated_from_surrounding_text():
    blocks = html_blocks(
        "<doc><main><list><item>before<table><row><cell>a</cell><cell>b</cell></row></table>after"
        "</item></list></main></doc>"
    )
    assert blocks[0]["text"] == "before\na | b\nafter"


def test_empty_table_cells_and_rows_do_not_add_text():
    blocks = html_blocks(
        "<doc><main><table><row><cell>a</cell><cell/></row><row/><row><cell>b</cell><cell>c</cell></row>"
        "</table></main></doc>"
    )
    assert blocks[0]["text"] == "a\nb | c"
    assert (blocks[0]["locator"]["rows"], blocks[0]["locator"]["cols"]) == (2, 2)


def test_table_columns_count_empty_cells():
    blocks = html_blocks("<doc><main><table><row><cell>a</cell><cell/><cell>c</cell></row></table></main></doc>")
    assert blocks[0]["text"] == "a | c"
    assert blocks[0]["locator"]["cols"] == 3


def test_list_items_separate_preformatted_code_but_keep_inline_code():
    blocks = html_blocks(
        "<doc><main><list><item>Use <code>make build</code> locally.</item>"
        "<item>Run this:<code>make test\nmake lint</code></item></list></main></doc>"
    )
    assert blocks[0]["text"] == "Use make build locally.\nRun this:\nmake test\nmake lint"


def test_preformatted_code_keeps_indentation_inside_a_quote():
    blocks = html_blocks("<doc><main><quote><p>Example</p><code>def f():\n    return 1</code></quote></main></doc>")
    assert blocks[0]["text"] == "Example\ndef f():\n    return 1"


def test_preformatted_code_keeps_indentation_through_enclosing_containers():
    blocks = html_blocks(
        "<doc><main><list><item>Run this:<code>def f():\n    return 1</code></item></list></main></doc>"
    )
    assert blocks[0]["text"] == "Run this:\ndef f():\n    return 1"


def test_html_list_code_keeps_indentation_after_extraction():
    source = (
        b"<html><body><article><h1>Guide</h1><ul><li>Run this:<pre><code>def f():\n    return 1</code></pre>"
        b"</li></ul></article></body></html>"
    )
    chunks = parse(source, "guide.html", {"max_chars": 12000, "overlap": 0})
    assert "def f():\n    return 1" in chunks[0]["text"]


def test_preformatted_code_does_not_leave_a_blank_line_before_the_next_item():
    blocks = html_blocks(
        "<doc><main><list><item><code>make test\nmake lint\n</code></item><item>Then verify.</item></list></main></doc>"
    )
    assert blocks[0]["text"] == "make test\nmake lint\nThen verify."


def test_unknown_heading_rend_is_a_top_level_heading():
    blocks = html_blocks('<doc><main><head rend="bold">Top</head><head rend="h2">Child</head><p>body</p></main></doc>')
    assert blocks[-1]["heading_path"] == ["Top", "Child"]


def test_parser_profile_snapshot_only_tracks_output_settings():
    snapshot = content.extract_config_snapshot()
    assert set(snapshot) == {"max_tree_size", "min_extracted_size", "min_output_comm_size", "min_output_size"}
    for unrelated in ("download_timeout", "max_redirects", "min_duplcheck_size", "max_repetitions"):
        assert unrelated not in snapshot


def _clone(blocks):
    return [
        {"text": block["text"], "heading_path": list(block["heading_path"]), "locator": dict(block["locator"])}
        for block in blocks
    ]


def _table(start, end, rows, cols):
    return {
        "text": f"table{rows}",
        "heading_path": ["H"],
        "locator": {
            "span_start": start,
            "span_end": end,
            "span_unit": "char",
            "block_type": "table",
            "rows": rows,
            "cols": cols,
        },
    }


def test_packing_same_type_clears_source_specific_fields(monkeypatch):
    blocks = [_table(0, 6, 2, 2), _table(8, 14, 3, 4)]
    monkeypatch.setitem(content.PARSERS, ".html", lambda data: _clone(blocks))
    chunks = parse(b"", "pack.html", {"max_chars": 12000, "overlap": 0})
    assert len(chunks) == 1
    locator = chunks[0]["locator"]
    assert (locator["span_start"], locator["span_end"]) == (0, 14)
    assert locator["block_type"] == "table"
    assert "rows" not in locator and "cols" not in locator


def test_packing_mixed_types_clears_the_block_type(monkeypatch):
    blocks = [_table(0, 6, 2, 2), {"text": "note", "heading_path": ["H"], "locator": {"span_start": 8, "span_end": 12}}]
    monkeypatch.setitem(content.PARSERS, ".html", lambda data: _clone(blocks))
    chunks = parse(b"", "pack.html", {"max_chars": 12000, "overlap": 0})
    assert len(chunks) == 1
    assert "block_type" not in chunks[0]["locator"]
    assert "rows" not in chunks[0]["locator"]


def test_html_parsing_merges_adjacent_blocks_without_reference_definitions():
    source = (
        b"<html><body><article><h1>Deployment Guide</h1>"
        b"<p>This guide explains how to deploy the service in a production environment with verification.</p>"
        b"<p>Run the installer, then verify the health endpoint before moving traffic to the new target.</p>"
        b"</article></body></html>"
    )
    chunks = parse(source, "guide.html", {"max_chars": 12000, "overlap": 0})
    assert len(chunks) == 1
    assert chunks[0]["heading_path"] == ["Deployment Guide"]
    assert chunks[0]["locator"]["span_unit"] == "char"


def test_short_html_still_produces_a_block():
    source = b"<html><body><p>Hello world, this is a short page.</p></body></html>"
    chunks = parse(source, "short.html", {"max_chars": 360, "overlap": 0})
    assert [chunk["text"] for chunk in chunks] == ["Hello world, this is a short page."]


def test_repeated_paragraphs_are_kept():
    source = (
        b"<html><body><article><p>"
        + b"same paragraph. " * 12
        + b"</p><p>"
        + b"different paragraph. " * 12
        + b"</p><p>"
        + b"same paragraph. " * 12
        + b"</p></article></body></html>"
    )
    chunks = parse(source, "repeat.html", {"max_chars": 12000, "overlap": 0})
    assert sum(chunk["text"].count("same paragraph.") for chunk in chunks) == 24


def test_repeated_parsing_is_deterministic():
    source = (
        "<html><body><article><h1>Guide</h1><p>"
        + "This sentence explains the deployment carefully. " * 20
        + "</p></article></body></html>"
    ).encode()
    first = parse(source, "guide.html", {"max_chars": 12000, "overlap": 0})
    assert parse(source, "guide.html", {"max_chars": 12000, "overlap": 0}) == first
    assert parse(source, "guide.html", {"max_chars": 12000, "overlap": 0}) == first
    assert parse(source, "guide.htm", {"max_chars": 12000, "overlap": 0}) == first


def test_extraction_failures_become_content_errors(monkeypatch):
    monkeypatch.setattr(content, "extract", lambda *args, **kwargs: None)
    with pytest.raises(ContentError, match="no extractable main content"):
        content.parse_html(b"<html></html>")
    monkeypatch.setattr(content, "extract", lambda *args, **kwargs: "<doc><main/></doc>")
    with pytest.raises(ContentError, match="no extractable main content"):
        content.parse_html(b"<html></html>")
    monkeypatch.setattr(content, "extract", lambda *args, **kwargs: "<doc><main><p>broken</main></doc>")
    with pytest.raises(ContentError, match="mapping failed"):
        content.parse_html(b"<html></html>")


def test_html_paths_and_profiles_are_registered():
    assert relative_path("docs/guide.html") == "docs/guide.html"
    assert relative_path("docs/guide.htm") == "docs/guide.htm"
    with pytest.raises(ContentError, match="HTML"):
        relative_path("docs/guide.txt")
    assert parser_profile("guide.html") == parser_profile("guide.htm")
    assert parser_profile("guide.html") != parser_profile("guide.md")


def test_markdown_line_locators_are_unchanged():
    chunks = parse(b"# Start\n\nalpha\n\nbeta\n", "steps.md", {"max_chars": 360, "overlap": 0})
    assert all("line_start" in chunk["locator"] for chunk in chunks)
    assert all("span_unit" not in chunk["locator"] for chunk in chunks)


@pytest.mark.parametrize(
    ("pinned", "current", "expected"),
    [
        ({"parser_profile": "html-parser-v1"}, {"parser_profile": "html-parser-v1"}, True),
        ({"parser_profile": "html-parser-v1"}, {"parser_profile": "html-parser-v2"}, False),
        ({}, {"parser_profile": "html-parser-v1"}, True),
        ({"parser_profile": "html-parser-v1"}, {}, True),
    ],
)
def test_parser_profile_comparison_skips_missing_versions(pinned, current, expected):
    assert reuse_parsed_result(pinned, current) is expected
