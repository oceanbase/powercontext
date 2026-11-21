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

"""Protect parsing boundaries and immutable indexed revisions without downloading a model."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.content import ContentError, Snapshots, parse, relative_path
from pc_knowledge_base.store import Store

from powercontext.builtin.plugins.storage import SQLitePluginStorage


def test_markdown_blocks_keep_headings_and_cover_long_text():
    body = "# Operations\n\n## Restart\n\n" + "check backups. " * 50 + "\n\n```sh\nrestart --safe\n```\n"
    blocks = parse(body.encode(), "ops.md", {"max_chars": 100, "overlap": 10})
    assert all(len(block["text"]) <= 100 for block in blocks)
    assert any(
        "restart --safe" in block["text"] and block["heading_path"] == ["Operations", "Restart"] for block in blocks
    )
    assert all(block["locator"]["line_start"] >= 1 for block in blocks)
    with pytest.raises(ContentError):
        relative_path("../secret.md")
    with pytest.raises(ContentError):
        relative_path("/absolute.md")


@pytest.mark.parametrize("newline", ["\r", "\n", "\r\n"])
@pytest.mark.parametrize("separator", ["\u2028", "\f"])
def test_markdown_uses_commonmark_line_boundaries(newline, separator):
    steps = f"First{separator}second"
    source = newline.join(["# Start", "", steps, "", "# Finish", "", "Submit the request"])
    blocks = parse(source.encode(), "steps.md", {"max_chars": 360, "overlap": 0})
    assert [block["text"] for block in blocks] == ["# Start\n\n" + steps, "# Finish\n\nSubmit the request"]
    assert [(b["locator"]["line_start"], b["locator"]["line_end"]) for b in blocks] == [(1, 3), (5, 7)]


@pytest.mark.parametrize("replaced", ["allowed", "project"])
def test_directory_replacement_cannot_redirect_import_outside_allowed_root(tmp_path, monkeypatch, replaced):
    pytest.importorskip("opendal")
    allowed = tmp_path / "allowed"
    project = allowed / "project"
    project.mkdir(parents=True)
    (project / "file.md").write_bytes(b"Allowed content")
    outside = tmp_path / "outside"
    (outside / "project").mkdir(parents=True)
    (outside / "file.md").write_bytes(b"Outside content")
    (outside / "project" / "file.md").write_bytes(b"Outside content")
    snapshots = Snapshots(tmp_path / "snapshots", [allowed])
    original_open = os.open
    swapped = False
    target = allowed if replaced == "allowed" else project

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        # Interleave a real directory replacement after validation but before traversal.
        if not swapped and (str(path) == str(project) or str(path) == replaced):
            target.rename(tmp_path / "saved")
            target.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(OSError):
        snapshots.local(str(project), "file.md")
    assert swapped


def test_markdown_html_tables_keep_content_and_source_lines():
    body = "<table>\n<tr><td>Emergency shutdown procedure</td></tr>\n</table>\n"
    blocks = parse(body.encode(), "runbook.md", {"max_chars": 360, "overlap": 40})
    assert len(blocks) == 1
    assert blocks[0]["text"] == body.rstrip()
    assert blocks[0]["locator"]["line_start"] == 1
    assert blocks[0]["locator"]["line_end"] == 3


@pytest.mark.parametrize(
    ("body", "expected_paths"),
    [
        (
            "## Preconditions\n\nCheck backups.\n\n## Execution\n\nDrain connections.",
            [["Preconditions"], ["Execution"]],
        ),
        (
            "# Runbook\n\n### Preconditions\n\nCheck backups.\n\n### Execution\n\nDrain connections.",
            [["Runbook"], ["Runbook", "Preconditions"], ["Runbook", "Execution"]],
        ),
        (
            "# Runbook\n\n## Restart\n\n#### Checks\n\nVerify backups.\n\n## Stop\n\nDrain connections.\n\n# Appendix",
            [["Runbook"], ["Runbook", "Restart"], ["Runbook", "Restart", "Checks"], ["Runbook", "Stop"], ["Appendix"]],
        ),
        (
            "# Runbook\n\n- ## Embedded heading\n\nOutside the list.",
            [["Runbook"]],
        ),
        (
            "> ## Example\n\nOutside the quote.",
            [[]],
        ),
        (
            "# Runbook\n\n> ## Example\n>\n> Quoted paragraph.\n\nOutside the quote.\n\n## Execution",
            [["Runbook"], ["Runbook", "Execution"]],
        ),
        (
            "# Runbook\n\n> ## Example\n>\n> > # Nested heading\n> >\n> > Nested paragraph.\n\nOutside the quotes.",
            [["Runbook"]],
        ),
    ],
    ids=[
        "h2-siblings",
        "skipped-level-siblings",
        "return-to-ancestor",
        "atomic-list-heading",
        "quoted-heading-without-parent",
        "quoted-heading-with-parent",
        "nested-quoted-headings",
    ],
)
def test_markdown_heading_paths_follow_levels_and_block_boundaries(body, expected_paths):
    blocks = parse(body.encode(), "runbook.md", {"max_chars": 360, "overlap": 40})
    assert [block["heading_path"] for block in blocks] == expected_paths
    # Heading metadata must not remove or reorder the original Markdown blocks.
    assert "\n\n".join(block["text"] for block in blocks) == body


def test_sqlite_indexes_preserve_old_chunks_and_isolate_scopes(tmp_path):
    async def scenario():
        storage = SQLitePluginStorage(tmp_path / "plugin.db")
        store = Store(storage, 2)
        try:
            await store.initialize()
            block = {
                "chunk_id": "one",
                "text": "database restart backups",
                "heading_path": [],
                "locator": {},
                "manually_modified": False,
            }
            await store.prepare("scope-a", "doc", "old", [block], [[1.0, 0.0]])
            await store.prepare("scope-a", "doc", "old", [block], [[1.0, 0.0]])
            await store.prepare("scope-a", "doc", "new", [{**block, "text": "approval required"}], [[0.0, 1.0]])
            await store.prepare("scope-b", "doc", "other", [block], [[1.0, 0.0]])
            results = await store.candidates(["scope-a"], "backups", [1.0, 0.0], "fts", 10)
            assert [(r["scope_id"], r["result_id"]) for r in results] == [("scope-a", "old")]
            results = await store.candidates(["scope-a"], "approval", [0.0, 1.0], "hybrid", 10)
            assert results[0]["result_id"] == "new"
            assert (await store.blocks("scope-a", "doc", "old"))[0]["text"] == block["text"]
        finally:
            await storage.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("link", ["[guide][g]", "[g][]", "[g]", "![diagram][g]"])
def test_markdown_chunks_keep_reference_destinations_and_original_locations(link):
    from markdown_it import MarkdownIt

    body = f'# Guide\n\nRead {link}.\n\n# Other\n\nUnrelated text.\n\n[g]: https://example.com/guide "Guide title"\n'
    chunks = parse(body.encode(), "guide.md", {"max_chars": 360, "overlap": 40})
    linked = next(chunk for chunk in chunks if "Read" in chunk["text"])
    rendered = MarkdownIt().render(linked["text"])
    assert "https://example.com/guide" in rendered
    assert 'title="Guide title"' in rendered
    assert linked["locator"]["line_start"] == 1
    assert linked["locator"]["line_end"] == 3
    assert linked["length"] == len(linked["text"])
    assert all(len(chunk["text"]) <= 360 for chunk in chunks)
    unrelated = next(chunk for chunk in chunks if "Unrelated" in chunk["text"])
    assert "https://example.com/guide" not in unrelated["text"]


def test_reference_definitions_survive_chunk_splitting_without_affecting_code():
    from markdown_it import MarkdownIt

    body = "See [guide][g].\n\n" * 12 + "\n```md\n[not a link][g]\n```\n\n[g]: https://example.com\n"
    chunks = parse(body.encode(), "guide.md", {"max_chars": 140, "overlap": 0})
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk["text"]) <= 140
        if "[guide][g]" in chunk["text"]:
            assert 'href="https://example.com"' in MarkdownIt().render(chunk["text"])
    code = next(chunk for chunk in chunks if "[not a link]" in chunk["text"])
    assert "[not a link][g]" in MarkdownIt().render(code["text"])


def test_reference_destination_larger_than_chunk_budget_requires_larger_chunks():
    from markdown_it import MarkdownIt

    url = "https://example.com/" + "x" * 400
    body = f"[guide][g]\n\n[g]: {url}".encode()
    with pytest.raises(ContentError, match="increase max_chars"):
        parse(body, "guide.md", {"max_chars": 360, "overlap": 40})
    chunks = parse(body, "guide.md", {"max_chars": 800, "overlap": 40})
    assert len(chunks) == 1
    assert f'href="{url}"' in MarkdownIt().render(chunks[0]["text"])
