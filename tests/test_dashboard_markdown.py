# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Preserve complete exact references without activating untrusted Markdown."""

import json
from html import unescape
from typing import Any

import pytest
from markdown_it import MarkdownIt

from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.markdown import handoff_markdown


def _record(text: str) -> dict[str, Any]:
    citation = {
        "kind": "memory",
        "memory_citation": {
            "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 9},
            "entry_id": "entry-1",
            "entry_version_id": "version-3",
        },
    }
    return {
        "scope_id": "scope-中文",
        "family": "handoff",
        "artifact_id": "h1",
        "revision": 3,
        "content_digest": "sha256:" + "a" * 64,
        "sources": [{"source_type": "content", "source_id": "source-1"}],
        "artifacts": [{"family": "experience", "artifact_id": "upstream", "revision": 5}],
        "content": {
            "schema": "powercontext.handoff.v1",
            "objective": "Exact handoff",
            "state": [{"text": text, "citations": [citation]}],
            "disposition": "blocked",
            "next_action": None,
            "omissions": [{"text": "Unknown result", "citation": citation}],
        },
    }


def test_markdown_preserves_reference_versions_and_blocks_structure_injection():
    content = "# Forged heading\n<script>alert(1)</script>\n![pixel](https://evil.example/pixel)\n```\n中文正文\n```"
    result = handoff_markdown(_record(content), "en").decode()
    rendered = MarkdownIt("commonmark", {"html": False}).render(result)
    assert content in unescape(rendered)
    blocks = [token.content for token in MarkdownIt().parse(result) if token.type == "fence"]
    values = [json.loads(block) for block in blocks]
    citation = values[1][0]["memory_citation"]
    assert citation["memory_ref"]["revision"] == 9
    assert citation["entry_version_id"] == "version-3"
    html = MarkdownIt("commonmark", {"html": True}).render(result)
    assert "<script>" not in html
    assert "<img" not in html
    assert "<h1>Forged heading" not in html
    assert "Unknown result" in html
    assert values[-1]["artifacts"][0]["revision"] == 5


def test_markdown_preserves_ordinary_punctuation_after_rendering():
    original = "Don't change the user's scope: keep `literal` #1."
    result = handoff_markdown(_record(original), "en").decode()
    rendered = MarkdownIt("commonmark", {"html": False}).render(result)
    assert "Don't change the user's scope: keep `literal` #1." in rendered
    assert "&#x27;" not in result


@pytest.mark.parametrize("field", ["objective", "state", "next_action", "omissions"])
def test_markdown_keeps_equals_lines_as_literal_text(field):
    original = "记录正文\n===\nx=1"
    record = _record("Plain state")
    if field == "objective":
        record["content"][field] = original
    elif field == "next_action":
        record["content"][field] = {**record["content"]["state"][0], "text": original}
    else:
        record["content"][field][0]["text"] = original
    result = handoff_markdown(record, "en").decode()
    html = MarkdownIt("commonmark", {"html": True}).render(result)
    assert "<h1>记录正文</h1>" not in html
    assert original in unescape(html)


def test_markdown_does_not_escape_punctuation_inside_indented_code():
    original = '    print("literal")\n    if enabled: # keep this branch\n<script>alert(1)</script>'
    result = handoff_markdown(_record(original), "en").decode()
    assert 'print("literal")' in result
    assert r'print\("literal"\)' not in result
    rendered = unescape(MarkdownIt("commonmark", {"html": False}).render(result))
    assert 'print("literal")' in rendered
    assert r'print\("literal"\)' not in rendered
    assert "<script>" not in MarkdownIt("commonmark", {"html": True}).render(result)


@pytest.mark.parametrize(
    "original",
    [
        "说明文字\n\n    return user.profile_id",
        "\n    return user.profile_id",
        "说明文字\n\n\treturn user.profile_id",
        "说明文字\n\n  \treturn user.profile_id",
        "    def read():\n\treturn user.profile_id\n````\n<script>alert(1)</script>",
    ],
)
def test_markdown_preserves_multiline_indented_content(original):
    result = handoff_markdown(_record(original), "en").decode()
    parser = MarkdownIt("commonmark", {"html": True})
    blocks = [token.content for token in parser.parse(result) if token.type == "fence" and not token.info]
    assert original + "\n" in blocks
    html = parser.render(result)
    assert "<script>" not in html
    assert "<img" not in html


def test_oversized_export_fails_without_truncation(monkeypatch):
    monkeypatch.setattr("powercontext.server.dashboard.markdown.MAX_DOWNLOAD_BYTES", 100)
    with pytest.raises(ReadError) as caught:
        handoff_markdown(_record("Complete statement"), "en")
    assert caught.value.status == 413
