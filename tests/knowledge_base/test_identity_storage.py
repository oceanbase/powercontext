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

"""Canonical callers survive plugin storage initialization and native column widening."""

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"))
from pc_knowledge_base.models import identity
from pc_knowledge_base.service import KnowledgeBase
from pc_knowledge_base.store import Store, defaults, spaces

from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig
from powercontext.builtin.persistence.seekdb.profile import SeekDBConfig, SeekDBProfile
from powercontext.builtin.plugins.storage import SQLitePluginStorage, open_plugin_resources
from powercontext.plugins import Caller


@pytest.mark.parametrize(
    ("backend", "layout"),
    [("sqlite", "fresh"), ("native", "fresh"), ("native", "legacy"), ("native", "partial-upgrade")],
)
def test_canonical_callers_keep_distinct_personal_spaces_after_restart(tmp_path, backend, layout):
    if backend == "native" and not os.environ.get("POWERCONTEXT_TEST_KB_SQL"):
        pytest.skip("requires dedicated native SQL backend")

    async def scenario():
        async with AsyncExitStack() as stack:
            if backend == "sqlite":
                storage = SQLitePluginStorage(tmp_path / "identities.db")
                stack.push_async_callback(storage.aclose)
            else:
                config = json.loads(os.environ["POWERCONTEXT_TEST_KB_SQL"])
                database = (
                    OceanBaseConfig.model_validate(config)
                    if config["kind"] == "oceanbase"
                    else SeekDBConfig.model_validate(config)
                )
                options = None
                if isinstance(database, SeekDBConfig):
                    profile = await stack.enter_async_context(SeekDBProfile.open(database, tables=()))
                    options = profile.connection_options
                storage, _ = await open_plugin_resources(
                    database,
                    str(tmp_path),
                    "kb-identity-" + identity(str(tmp_path))[:12],
                    None,
                    stack,
                    connection_options=options,
                )
            store = Store(storage, 2)
            await store.initialize()

            def service(principal):
                return KnowledgeBase(SimpleNamespace(caller=Caller(principal_id=principal)), store, None, None)

            original = await service("user:existing").default()
            if layout != "fresh":
                async with store.engine.begin() as connection:
                    # Recreate the prior installation, or one interrupted after widening its first column.
                    owner_length = 263 if layout == "partial-upgrade" else 256
                    await connection.execute(
                        text(
                            "ALTER TABLE pc_knowledge_base_spaces "
                            f"MODIFY COLUMN personal_owner_id VARCHAR({owner_length}) COLLATE utf8mb4_bin NULL, "
                            "MODIFY COLUMN created_by VARCHAR(256) NOT NULL"
                        )
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE pc_knowledge_base_space_defaults "
                            "MODIFY COLUMN principal_id VARCHAR(256) COLLATE utf8mb4_bin NOT NULL"
                        )
                    )
                await asyncio.gather(store.initialize(), store.initialize())
            assert await service("user:existing").default() == original
            principals = [f"{kind}:" + "x" * 254 + suffix for kind in ("user", "service") for suffix in ("A", "a")]
            saved = {}
            for principal in principals:
                current = await service(principal).default()
                assert current["principal_id"] == principal
                owner = await store.one(spaces, space_scope_id=current["space_scope_id"])
                assert owner["personal_owner_id"] == owner["created_by"] == principal
                saved[principal] = current
            assert len({row["space_scope_id"] for row in saved.values()}) == len(principals)
            await store.engine.dispose()
            await store.initialize()
            for principal, current in saved.items():
                assert await service(principal).default() == current
                assert await store.one(defaults, principal_id=principal) == current
            assert await service("user:existing").default() == original

    asyncio.run(scenario())
