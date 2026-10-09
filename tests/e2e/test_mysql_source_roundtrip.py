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

"""Real backend acceptance for Source bytes and idempotent readback."""

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource
from powercontext.sources import SourceMaterialization


@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
def test_mysql_source_write_read_and_replay(tmp_path, backend):
    if backend == "seekdb":
        pytest.importorskip("pylibseekdb")
        database = SeekDBConfig(path=tmp_path / "seekdb")
    else:
        url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
        if not url:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated test database")
        database = OceanBaseConfig(url=SecretStr(url))

    async def scenario():
        async with open_builtin_contexts(BuiltinConfig(database=database)) as contexts:
            scope = (
                await contexts.scopes.create(
                    ScopeDraft(title="Binary Source", summary="Driver acceptance", idempotency_key=str(uuid4()))
                )
            ).scope_id
            source = ContentSource(
                name="binary-roundtrip",
                materialization=SourceMaterialization.CAPTURED,
                content="中文 'quoted' \\ backslash\nline\0end",
            )
            await contexts.database.ping()
            async with contexts.database.transaction() as connection:
                stored, created = await contexts.repositories.sources.add_with_status(connection, scope, source)
                assert created
            async with contexts.database.transaction() as connection:
                loaded = await contexts.repositories.sources.get(connection, scope, stored.ref)
                replayed, created = await contexts.repositories.sources.add_with_status(connection, scope, source)
                assert loaded.value == source
                assert replayed == loaded == stored
                assert not created
                assert (
                    await contexts.repositories.sources.journal_position(connection, scope) == stored.journal_position
                )

    asyncio.run(scenario())
