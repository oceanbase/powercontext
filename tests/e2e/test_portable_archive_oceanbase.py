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

"""Live SQLite-to-OceanBase portable archive acceptance."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import insert, select
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    SCOPES_TABLE,
)
from powercontext.builtin.portability import PortableBundleService

LIVE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")


async def _authorize(_scopes: tuple[str, ...], /) -> None:
    pass


@pytest.mark.skipif(
    not LIVE_URL,
    reason="set POWERCONTEXT_TEST_OCEANBASE_URL with test database creation and deletion privileges",
)
def test_sqlite_bundle_restores_exact_revision_into_oceanbase(tmp_path: Path) -> None:
    async def scenario() -> None:
        assert LIVE_URL is not None
        scope_id = "project:portable-oceanbase"
        archive = tmp_path / "sqlite-to-oceanbase.pcb"
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as sqlite:
            async with sqlite.database.transaction() as connection:
                await connection.execute(
                    insert(SCOPES_TABLE).values(
                        scope_id=scope_id,
                        title="Portable migration",
                        summary="SQLite to OceanBase",
                        scope_id_search=scope_id,
                        title_search="Portable migration",
                        summary_search="SQLite to OceanBase",
                        parent_scope_id=None,
                        version=1,
                    )
                )
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id=scope_id,
                        family="handoff",
                        artifact_id="handoff",
                        revision=1,
                        content=b'{"content":{"objective":"portable"}}',
                    )
                )
                await connection.execute(
                    insert(ARTIFACT_HEADS_TABLE).values(
                        scope_id=scope_id,
                        family="handoff",
                        artifact_id="handoff",
                        revision=1,
                    )
                )
            exported = await PortableBundleService(sqlite.database).export([scope_id], archive, authorize=_authorize)

        database_name = f"pc_portable_{uuid4().hex}"
        base_config = OceanBaseConfig(url=SecretStr(LIVE_URL))
        created = False
        async with OceanBaseProfile.open(base_config, tables=()) as server:
            try:
                async with server.database.transaction() as connection:
                    await connection.exec_driver_sql(f"CREATE DATABASE `{database_name}`")
                    created = True
                target_url = make_url(LIVE_URL).set(database=database_name).render_as_string(hide_password=False)
                async with OceanBaseProfile.open(
                    OceanBaseConfig(url=SecretStr(target_url)), tables=BUILTIN_TABLES
                ) as oceanbase:
                    restored = await PortableBundleService(oceanbase.database).restore(archive)
                    async with oceanbase.database.transaction() as connection:
                        row = (
                            (
                                await connection.execute(
                                    select(ARTIFACTS_TABLE).where(
                                        ARTIFACTS_TABLE.c.scope_id == scope_id,
                                        ARTIFACTS_TABLE.c.family == "handoff",
                                        ARTIFACTS_TABLE.c.artifact_id == "handoff",
                                        ARTIFACTS_TABLE.c.revision == 1,
                                    )
                                )
                            )
                            .mappings()
                            .one()
                        )
                    assert restored.record_count == exported.record_count
                    assert bytes(row["content"]) == b'{"content":{"objective":"portable"}}'
            finally:
                if created:
                    async with server.database.transaction() as connection:
                        await connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")

    asyncio.run(scenario())
