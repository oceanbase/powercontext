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

"""Verify locator projection through plugin search with persisted legacy chunks."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.parser_contract import CONTEXT_LOCATOR_MAX_BYTES
from pc_knowledge_base.service import KnowledgeBase
from pc_knowledge_base.store import Store

from powercontext.artifacts.models import ArtifactRef
from powercontext.builtin.plugins.storage import SQLitePluginStorage
from powercontext.plugins import SearchRequest


def test_prepared_context_projects_legacy_chunk_locators(tmp_path):
    offsets = {"block_char_start": 0, "block_char_end": 7}
    lines = {"line_start": 2, "line_end": 4, **offsets}
    image = {
        "ocr_block": 3,
        "image_width": 100,
        "image_height": 200,
        "polygon": [[10, 20], [90, 20], [90, 100], [10, 100]],
        "confidence": 0.99,
        "internal": "excluded",
        **offsets,
    }
    invalid = {**image, "polygon": [[0, 0], [101, 0], [0, 20]], "block_char_end": -1}
    oversized = {**image, "polygon": [[0, 0]] * 17}
    invalid_confidence = {**image, "confidence": 1.1}
    old = {
        "notes": lines,
        "image": image,
        "invalid": invalid,
        "oversized": oversized,
        "invalid_score": invalid_confidence,
    }
    image_locator = {
        "kind": "image",
        "index": 3,
        "polygon": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.5], [0.1, 0.5]],
    }
    expected = {
        "notes": {"source_locator": {"kind": "lines", "lines": {"start": 1, "end": 4}}, **offsets},
        "image": {"source_locator": {**image_locator, "confidence": 0.99}, **offsets},
        "invalid_score": {"source_locator": image_locator, **offsets},
        "invalid": {},
        "oversized": offsets,
    }
    source = {"source_type": "knowledge-base-file", "source_id": "old-source"}
    published = SimpleNamespace(
        ref=ArtifactRef(family="document", artifact_id="existing", revision=5),
        lifecycle_state="active",
        content={"result_id": "historical", "embedding_profile": "fixture", "original_source": source},
    )

    async def get(_document):
        return published

    async def scenario():
        storage = SQLitePluginStorage(tmp_path / "plugin.db")
        store = Store(storage, 2)
        try:
            await store.initialize()
            await store.add_resource("scope", "scope", "settings", {"embedding_profile": "fixture"})
            blocks = [
                {
                    "chunk_id": name,
                    "text": "backups",
                    "heading_path": ["Operations"],
                    "locator": locator,
                    "manually_modified": False,
                }
                for name, locator in old.items()
            ]
            await store.prepare("scope", "existing", "historical", blocks, [[1.0, 0.0]] * len(blocks))
            kb = KnowledgeBase(
                SimpleNamespace(reads=lambda _scope: SimpleNamespace(get=get)),
                store,
                None,
                SimpleNamespace(profile="fixture"),
            )
            request = SearchRequest(scope_id="scope", search={"query": "backups", "mode": "fts"})
            general = await kb.search_artifacts(request)
            context = await kb.search_artifacts(
                SearchRequest(scope_id=request.scope_id, search=request.search, max_bytes=4096)
            )
            (general_hit,) = general.hits
            (context_hit,) = context.hits
            assert general_hit.ref == context_hit.ref == published.ref
            envelope = {"heading_path": ["Operations"], "source": source, "manually_modified": False}
            assert general_hit.locator is not None
            assert context_hit.locator is not None
            assert {row["chunk_id"]: row for row in general_hit.locator["chunks"]} == {
                name: {"chunk_id": name, **envelope, **locator} for name, locator in old.items()
            }
            projected = context_hit.locator["chunks"]
            assert {row["chunk_id"]: row for row in projected} == {
                name: {"chunk_id": name, **envelope, **locator} for name, locator in expected.items()
            }
            assert [
                json.loads(line.removeprefix("Chunk: "))
                for line in context_hit.text.splitlines()
                if line.startswith("Chunk: ")
            ] == projected
            for row in projected:
                if "source_locator" in row:
                    assert (
                        len(json.dumps(row["source_locator"], separators=(",", ":")).encode())
                        <= CONTEXT_LOCATOR_MAX_BYTES
                    )
            assert {
                block["chunk_id"]: block["locator"]
                for block in await store.blocks(
                    "scope",
                    "existing",
                    "historical",
                )
            } == old
        finally:
            await storage.aclose()

    asyncio.run(scenario())
