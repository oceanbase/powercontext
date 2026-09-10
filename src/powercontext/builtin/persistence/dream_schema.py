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

from powercontext.builtin.persistence.tables import ARTIFACT_CANDIDATE_VERSIONS_TABLE, ARTIFACTS_TABLE


async def ensure_dream_schema(connection: AsyncConnection, /) -> None:
    for table in (ARTIFACTS_TABLE, ARTIFACT_CANDIDATE_VERSIONS_TABLE):
        column = table.c.memory_citations
        if await _has_column(connection, table.name, column.name):
            continue
        declaration = str(CreateColumn(column).compile(dialect=connection.dialect))
        try:
            await connection.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {declaration}")
        except DBAPIError:
            # Concurrent startup may have applied the same additive migration.
            if not await _has_column(connection, table.name, column.name):
                raise


async def _has_column(connection: AsyncConnection, table_name: str, column_name: str) -> bool:
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns(table_name))
    return any(column["name"] == column_name for column in columns)
