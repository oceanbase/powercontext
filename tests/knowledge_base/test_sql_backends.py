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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.models import identity
from pc_knowledge_base.store import Store

from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig
from powercontext.builtin.persistence.seekdb.profile import SeekDBConfig
from powercontext.builtin.plugins.storage import open_plugin_resources


@pytest.mark.skipif(not os.environ.get("POWERCONTEXT_TEST_KB_SQL"), reason="requires dedicated native SQL backend")
def test_native_indexes_and_plugin_storage_isolation(tmp_path):
    config = json.loads(os.environ["POWERCONTEXT_TEST_KB_SQL"])
    database = (
        OceanBaseConfig.model_validate(config) if config["kind"] == "oceanbase" else SeekDBConfig.model_validate(config)
    )

    async def scenario():
        async with AsyncExitStack() as stack:
            storage, _ = await open_plugin_resources(
                database, str(tmp_path), "kb-test-" + identity(str(tmp_path))[:12], None, stack
            )
            store = Store(storage, 2)
            await store.initialize()
            block = {
                "chunk_id": "a",
                "text": "database backups restart",
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

    asyncio.run(scenario())
