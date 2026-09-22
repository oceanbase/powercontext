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

"""Add exact entry provenance to existing immutable revision tables."""

from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateColumn

from powercontext.builtin.persistence.catalog_changes import ensure_catalog_change_schema
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    ARTIFACTS_TABLE,
    DREAM_RUNS_TABLE,
)


async def ensure_dream_schema(connection: AsyncConnection, /) -> None:
    for table, name in (
        (DREAM_RUNS_TABLE, "proposal_fingerprint"),
        (ARTIFACTS_TABLE, "memory_citations"),
        (ARTIFACT_CANDIDATE_VERSIONS_TABLE, "memory_citations"),
        (ARTIFACT_PROCESSING_INTENTS_TABLE, "consecutive_dream_attempts"),
    ):
        if not await connection.run_sync(lambda sync, table_name=table.name: inspect(sync).has_table(table_name)):
            continue
        column = table.c[name]
        if await _has_column(connection, table.name, column.name):
            continue
        declaration = str(CreateColumn(column).compile(dialect=connection.dialect))
        try:
            await connection.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {declaration}")
        except DBAPIError:
            # Concurrent startup may have applied the same additive migration.
            if not await _has_column(connection, table.name, column.name):
                raise

    for index in DREAM_RUNS_TABLE.indexes:
        if index.name == "ix_pc_dream_runs_proposal_fingerprint" and await connection.run_sync(
            lambda sync: inspect(sync).has_table(DREAM_RUNS_TABLE.name)
        ):
            await connection.run_sync(lambda sync, selected=index: selected.create(sync, checkfirst=True))
    await ensure_catalog_change_schema(connection)


async def _has_column(connection: AsyncConnection, table_name: str, column_name: str) -> bool:
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns(table_name))
    return any(column["name"] == column_name for column in columns)
