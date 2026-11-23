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

"""Opt-in native SQL backend acceptance; never points to a shared application database."""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.models import identity
from pc_knowledge_base.store import Store, chunks

from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig
from powercontext.builtin.persistence.seekdb.profile import SeekDBConfig, SeekDBProfile
from powercontext.builtin.plugins.storage import open_plugin_resources


@pytest.mark.parametrize("index_state", ["new", "legacy", "interrupted-upgrade"])
@pytest.mark.skipif(not os.environ.get("POWERCONTEXT_TEST_KB_SQL"), reason="requires dedicated native SQL backend")
def test_native_indexes_and_plugin_storage_isolation(tmp_path, index_state):
    config = json.loads(os.environ["POWERCONTEXT_TEST_KB_SQL"])
    database = (
        OceanBaseConfig.model_validate(config) if config["kind"] == "oceanbase" else SeekDBConfig.model_validate(config)
    )

    async def scenario():
        async with AsyncExitStack() as stack:
            connection_options = None
            if isinstance(database, SeekDBConfig):
                profile = await stack.enter_async_context(SeekDBProfile.open(database, tables=()))
                connection_options = profile.connection_options
            storage, _ = await open_plugin_resources(
                database,
                str(tmp_path),
                "kb-test-" + identity(str(tmp_path))[:12],
                None,
                stack,
                connection_options=connection_options,
            )
            store = Store(storage, 2)
            await store.initialize()
            block = {
                "chunk_id": "a",
                "text": "database backups restart 发布 备份 回滚 窗口 中",
                "heading_path": [],
                "locator": {},
                "manually_modified": False,
            }
            await store.prepare("scope", "document", "r1", [block], [[1.0, 0.0]])
            assert len(await store.blocks("scope", "document", "r1")) == 1
            await store.prepare("scope", "document", "r1", [block], [[1.0, 0.0]])
            hits = await store.candidates(["scope"], "backups", [1.0, 0.0], "hybrid", 10)
            assert [h["result_id"] for h in hits] == ["r1"]
            assert not await store.candidates(["other"], "backups", [1.0, 0.0], "hybrid", 10)
            async with store.engine.connect() as connection:
                original = (await connection.execute(select(chunks))).mappings().all()
            if index_state != "new":
                # Reproduce an installation created before short tokens were enabled.
                async with store.engine.begin() as connection:
                    await connection.execute(text("DROP INDEX kb_tokens_v2 ON kb_search"))
                    await connection.execute(text("CREATE FULLTEXT INDEX kb_tokens ON kb_search(tokens)"))
                assert not await store.candidates(["scope"], "发布", [], "fts", 10)
                if index_state == "interrupted-upgrade":
                    async with store.engine.begin() as connection:
                        await connection.execute(
                            text(
                                "CREATE FULLTEXT INDEX kb_tokens_v2 ON kb_search(tokens) "
                                "WITH PARSER SPACE PARSER_PROPERTIES=(min_token_size=1)"
                            )
                        )
                # Exercise concurrent Server/Worker startup on the persisted old projection.
                await asyncio.gather(store.initialize(), store.initialize())
            await store.initialize()
            for query in ("发布", "备份", "回滚窗口", "发布前必须检查什么", "中", "backups"):
                hits = await store.candidates(["scope"], query, [], "fts", 10)
                assert [hit["result_id"] for hit in hits] == ["r1"], query
                assert not await store.candidates(["other"], query, [], "fts", 10)
            hybrid = await store.candidates(["scope"], "备份", [1.0, 0.0], "hybrid", 10)
            assert hybrid[0]["score"] == pytest.approx(2 / 61)
            vector_hits = await store.candidates(["scope"], "", [1.0, 0.0], "vector", 10)
            assert [hit["result_id"] for hit in vector_hits] == ["r1"]
            async with store.engine.connect() as connection:
                assert (await connection.execute(select(chunks))).mappings().all() == original
            assert len(await store.blocks("scope", "document", "r1")) == 1

    asyncio.run(scenario())
