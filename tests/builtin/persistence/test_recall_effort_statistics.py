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

"""Recall-effort storage contracts, including isolated live OceanBase coverage."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import BigInteger, insert, select
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.statistics import StatisticsRepository
from powercontext.builtin.persistence.tables import (
    MODEL_USAGE_DAILY_TABLE,
    RECALL_EFFORT_DAILY_TABLE,
    RECALL_TOKEN_DAILY_TABLE,
    STATISTICS_TABLES,
)
from powercontext.builtin.statistics import RecallEffortMeasurement

DAY = date(2026, 10, 9)


def _measurement() -> RecallEffortMeasurement:
    return RecallEffortMeasurement(
        policy_id="powercontext.recall-gate.v1",
        assessment="at-max-rounds",
        rounds=3,
        expanded_preparations=1,
        admission_expansions=1,
        policy_floor_expansions=1,
        candidate_round_samples=3,
        candidates_assessed=18,
        final_candidate_pool=9,
        added_embeddings=2,
        added_generation_calls=4,
        truncated_items=2,
        dropped_items=5,
        dropped_below_min_bytes=2,
        dropped_no_fitting_truncation=3,
    )


@asynccontextmanager
async def _isolated_config(backend: str, path: Path) -> AsyncIterator[SQLiteConfig | OceanBaseConfig]:
    if backend == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")
        return
    value = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not value:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL for the isolated live OceanBase statistics contract")
    database_name = f"pc_effort_{uuid4().hex}"
    created = False
    async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(value)), tables=()) as server:
        try:
            async with server.database.transaction() as connection:
                await connection.exec_driver_sql(
                    f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                )
                created = True
            url = make_url(value).set(database=database_name).render_as_string(hide_password=False)
            yield OceanBaseConfig(url=SecretStr(url))
        finally:
            if created:
                async with server.database.transaction() as connection:
                    await connection.exec_driver_sql(f"DROP DATABASE `{database_name}`")


@pytest.mark.parametrize("backend", ["sqlite", "oceanbase"])
def test_recall_effort_schema_upgrade_and_atomic_daily_scope_accounting(backend: str, tmp_path) -> None:
    async def scenario() -> None:
        repository = StatisticsRepository()
        observation = _measurement()
        async with _isolated_config(backend, tmp_path / "statistics.db") as config:
            if isinstance(config, SQLiteConfig):
                legacy = SQLiteProfile.open(config, tables=(MODEL_USAGE_DAILY_TABLE, RECALL_TOKEN_DAILY_TABLE))
                upgraded = SQLiteProfile.open(config, tables=STATISTICS_TABLES)
                reopened = SQLiteProfile.open(config, tables=STATISTICS_TABLES)
            else:
                legacy = OceanBaseProfile.open(config, tables=(MODEL_USAGE_DAILY_TABLE, RECALL_TOKEN_DAILY_TABLE))
                upgraded = OceanBaseProfile.open(config, tables=STATISTICS_TABLES)
                reopened = OceanBaseProfile.open(config, tables=STATISTICS_TABLES)
            async with legacy as profile, profile.database.transaction() as connection:
                await connection.execute(
                    insert(RECALL_TOKEN_DAILY_TABLE).values(
                        scope_id="scope-a",
                        usage_date=DAY,
                        estimator_id="fixture",
                        estimator_version="v1",
                        preparations=1,
                        ready_preparations=1,
                        comparable_preparations=1,
                        baseline_tokens=12,
                        recalled_tokens=4,
                    )
                )

            async with upgraded as profile:

                async def record(scope="scope-a", day=DAY, measurement=observation) -> None:
                    async with profile.database.transaction() as connection:
                        await repository.record_recall_effort(connection, scope, day, measurement)

                await record()
                await asyncio.gather(*(record() for _ in range(8)))
                await record("Scope-a")
                await record(day=DAY + timedelta(days=1))
                await record(measurement=observation.model_copy(update={"policy_id": "powercontext.recall-gate.v2"}))
                await record(measurement=observation.model_copy(update={"assessment": "sufficient"}))

            async with reopened as profile, profile.database.transaction() as connection:
                rows = (await connection.execute(select(RECALL_EFFORT_DAILY_TABLE))).mappings().all()
                assert len(rows) == 5
                dimensions = {(row["scope_id"], row["usage_date"], row["policy_id"], row["assessment"]) for row in rows}
                assert dimensions == {
                    ("scope-a", DAY, observation.policy_id, observation.assessment),
                    ("Scope-a", DAY, observation.policy_id, observation.assessment),
                    ("scope-a", DAY + timedelta(days=1), observation.policy_id, observation.assessment),
                    ("scope-a", DAY, "powercontext.recall-gate.v2", observation.assessment),
                    ("scope-a", DAY, observation.policy_id, "sufficient"),
                }
                for row in rows:
                    multiplier = (
                        9
                        if (row["scope_id"], row["usage_date"], row["policy_id"], row["assessment"])
                        == ("scope-a", DAY, observation.policy_id, observation.assessment)
                        else 1
                    )
                    counters = observation.model_dump(exclude={"policy_id", "assessment"})
                    assert {name: row[name] for name in counters} == {
                        name: value * multiplier for name, value in counters.items()
                    }
                    assert row["dropped_items"] == row["dropped_below_min_bytes"] + row["dropped_no_fitting_truncation"]
                token_row = (await connection.execute(select(RECALL_TOKEN_DAILY_TABLE))).mappings().one()
                assert token_row["preparations"] == 1
                assert token_row["baseline_tokens"] == 12

    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [{"dropped_items": 0}, {"added_embeddings": -1}])
def test_recall_effort_invalid_observation_is_rejected_before_sql(changes) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=STATISTICS_TABLES) as profile:
            with pytest.raises(ValidationError):
                async with profile.database.transaction() as connection:
                    await StatisticsRepository().record_recall_effort(
                        connection, "scope-a", DAY, _measurement().model_copy(update=changes)
                    )
            async with profile.database.transaction() as connection:
                assert (await connection.execute(select(RECALL_EFFORT_DAILY_TABLE))).all() == []

    asyncio.run(scenario())


@pytest.mark.parametrize("changes", [{"dropped_items": 0}, {"added_embeddings": -1}])
def test_recall_effort_schema_rejects_invalid_counters(changes) -> None:
    async def scenario() -> None:
        values = _measurement().model_dump() | {"scope_id": "scope-a", "usage_date": DAY} | changes
        async with SQLiteProfile.open(SQLiteConfig(), tables=STATISTICS_TABLES) as profile:
            with pytest.raises(IntegrityError):
                async with profile.database.transaction() as connection:
                    await connection.execute(insert(RECALL_EFFORT_DAILY_TABLE).values(**values))

    asyncio.run(scenario())


def test_recall_effort_schema_contains_only_bounded_keys_and_numeric_counters() -> None:
    table = RECALL_EFFORT_DAILY_TABLE
    keys = ("scope_id", "usage_date", "policy_id", "assessment")
    assert tuple(column.name for column in table.primary_key.columns) == keys
    assert set(table.columns.keys()) == {*keys, *_measurement().model_dump()}
    assert all(isinstance(column.type, BigInteger) for column in table.columns if column.name not in keys)
    ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
    assert "PRIMARY KEY (scope_id, usage_date, policy_id, assessment)" in ddl
    for name, length in (("scope_id", 256), ("policy_id", 128), ("assessment", 64)):
        assert f"{name} VARCHAR({length}) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin" in ddl


def test_recall_effort_mysql_uses_one_additive_upsert() -> None:
    async def scenario() -> None:
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = mysql.dialect()
        await StatisticsRepository().record_recall_effort(
            cast(AsyncConnection, connection), "scope-a", DAY, _measurement()
        )
        connection.execute.assert_awaited_once()
        statement = connection.execute.await_args.args[0]
        sql = str(statement.compile(dialect=mysql.dialect()))
        assert "ON DUPLICATE KEY UPDATE" in sql
        for name in _measurement().model_dump(exclude={"policy_id", "assessment"}):
            assert f"pc_recall_effort_daily.{name} + VALUES({name})" in sql

    asyncio.run(scenario())
