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
import sqlite3

import pytest
from pydantic import ValidationError
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncTransaction

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    SCOPES_TABLE,
    SHARED_TABLES,
)


def test_sqlite_config_requires_the_async_dialect() -> None:
    with pytest.raises(ValidationError, match=r"sqlite\+aiosqlite"):
        SQLiteConfig(url="sqlite:///:memory:")


def test_sqlite_profile_creates_a_missing_database_directory(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "nested" / "powercontext.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            tables=SHARED_TABLES,
        ):
            assert database.is_file()

    asyncio.run(scenario())


def test_repeated_cancellation_waits_for_transaction_rollback_before_releasing_writer(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "cancelled.db"
        body_started = asyncio.Event()
        rollback_started = asyncio.Event()
        finish_rollback = asyncio.Event()
        original_exit = AsyncTransaction.__aexit__

        async def delayed_exit(transaction, error_type, error, traceback):
            rollback_started.set()
            await finish_rollback.wait()
            return await original_exit(transaction, error_type, error, traceback)

        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"), tables=(SCOPES_TABLE,)
        ) as profile:

            async def cancelled_write() -> None:
                async with profile.database.transaction() as connection:
                    await connection.exec_driver_sql("UPDATE pc_scopes SET version = version")
                    body_started.set()
                    await asyncio.Event().wait()

            with monkeypatch.context() as injected:
                # Simulate a slow driver rollback and a second cancellation
                # while the transaction is returning its connection.
                injected.setattr(AsyncTransaction, "__aexit__", delayed_exit)
                task = asyncio.create_task(cancelled_write())
                try:
                    await asyncio.wait_for(body_started.wait(), 2)
                    task.cancel()
                    await asyncio.wait_for(rollback_started.wait(), 2)
                    task.cancel()
                    await asyncio.sleep(0)
                    assert not task.done()
                finally:
                    finish_rollback.set()
                    task.cancel()
                    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
                assert task.cancelled()

            writer = sqlite3.connect(database_path, timeout=0)
            try:
                writer.execute("BEGIN IMMEDIATE")
                writer.rollback()
            finally:
                writer.close()
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SCOPES_TABLE)) == 0

    asyncio.run(scenario())


def test_sqlite_pragmas_enforce_lineage_source_foreign_keys() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.sqlite_version())) is not None
                pragma = await connection.exec_driver_sql("PRAGMA foreign_keys")
                assert int(pragma.scalar() or 0) == 1
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id="scope",
                        family="memory",
                        artifact_id="memory-1",
                        revision=1,
                        content=b"{}",
                    )
                )

            with pytest.raises(IntegrityError):
                async with profile.database.transaction() as connection:
                    await connection.execute(
                        insert(ARTIFACT_LINEAGE_SOURCES_TABLE).values(
                            scope_id="scope",
                            family="memory",
                            artifact_id="memory-1",
                            revision=1,
                            ordinal=0,
                            source_type="content",
                            source_id="missing",
                        )
                    )

    asyncio.run(scenario())
