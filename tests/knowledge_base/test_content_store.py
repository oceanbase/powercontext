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
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.content import ContentError, parse, relative_path
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
