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

from __future__ import annotations

import asyncio
import shutil
from contextlib import AsyncExitStack

import pytest
from sqlalchemy import event, insert
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.server.identity import (
    SERVER_IDENTITY_TABLE,
    ServerIdentityRepository,
    open_server_identity_repository,
)


def test_server_identity_singleton_key_is_not_auto_incremented_by_mysql_profiles() -> None:
    ddl = str(CreateTable(SERVER_IDENTITY_TABLE).compile(dialect=mysql.dialect()))

    assert "AUTO_INCREMENT" not in ddl
    assert "CHECK (singleton_key = 1)" in ddl


def test_server_identity_survives_reopen_restore_and_explicit_clone_rotation(tmp_path) -> None:
    async def scenario() -> None:
        primary_path = tmp_path / "primary.db"
        restored_path = tmp_path / "restored.db"
        primary = SQLiteConfig(url=f"sqlite+aiosqlite:///{primary_path}")

        async with open_server_identity_repository(primary) as repository:
            original = await repository.load_or_create()
            assert await repository.load_or_create() == original

        shutil.copy2(primary_path, restored_path)
        restored = SQLiteConfig(url=f"sqlite+aiosqlite:///{restored_path}")
        async with open_server_identity_repository(restored) as repository:
            assert await repository.load_or_create() == original
            rotated = await repository.rotate()
            assert rotated != original
            assert await repository.load_or_create() == rotated

        async with open_server_identity_repository(primary) as repository:
            assert await repository.load_or_create() == original

    asyncio.run(scenario())


def test_concurrent_server_initializers_converge_on_one_identity(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'deployment.db'}")

        async def initialize() -> str:
            async with open_server_identity_repository(config) as repository:
                return await repository.load_or_create()

        identities = await asyncio.gather(*(initialize() for _ in range(8)))
        assert len(set(identities)) == 1

    asyncio.run(scenario())


def test_shared_memory_initializers_converge_on_one_identity(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///file:{tmp_path / 'identity'}?mode=memory&cache=shared&uri=true")
        async with AsyncExitStack() as resources:
            repositories = [
                await resources.enter_async_context(open_server_identity_repository(config)) for _ in range(8)
            ]
            identities = await asyncio.gather(*(repository.load_or_create() for repository in repositories))
            assert len(set(identities)) == 1
            assert await repositories[0].load_or_create() == identities[0]

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["schema", "read"])
def test_identity_initialization_recovers_from_locks_but_exhaustion_still_fails(tmp_path, monkeypatch, phase) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(
            url=f"sqlite+aiosqlite:///file:{tmp_path / 'locked'}?mode=memory&cache=shared&uri=true",
            busy_timeout_ms=0,
        )
        async with (
            SQLiteProfile.open(config, tables=()) as owner,
            SQLiteProfile.open(config, tables=()) as contender,
        ):
            repository = ServerIdentityRepository(contender.database)
            if phase == "read":
                await repository.initialize()
            operation = repository.initialize if phase == "schema" else repository.load_or_create
            contended = asyncio.Event()

            @event.listens_for(contender.database.engine.sync_engine, "handle_error")
            def observed_lock(context) -> None:
                contended.set()

            async with owner.database.transaction() as connection:
                if phase == "schema":
                    await connection.exec_driver_sql("BEGIN IMMEDIATE")
                    await connection.exec_driver_sql("CREATE TABLE lock_probe (value INTEGER)")
                else:
                    await connection.execute(
                        insert(SERVER_IDENTITY_TABLE).values(singleton_key=1, server_id="committed")
                    )
                with monkeypatch.context() as budget:
                    budget.setattr("powercontext.server.identity._INITIALIZATION_TIMEOUT_SECONDS", 0)
                    with pytest.raises(OperationalError, match="locked"):
                        await operation()
                contended.clear()
                pending = asyncio.create_task(operation())
                try:
                    await asyncio.wait_for(contended.wait(), 2)
                except BaseException:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    raise
            result = await pending
            if phase == "read":
                assert result == "committed"
            else:
                assert await repository.load_or_create()

    asyncio.run(scenario())
