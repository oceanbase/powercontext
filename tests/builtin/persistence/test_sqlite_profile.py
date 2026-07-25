from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    SHARED_TABLES,
)


def test_sqlite_config_requires_the_async_dialect() -> None:
    with pytest.raises(ValidationError, match=r"sqlite\+aiosqlite"):
        SQLiteConfig(url="sqlite:///:memory:")


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
