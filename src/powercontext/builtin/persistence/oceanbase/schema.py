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

"""Native idempotent OceanBase vector-index DDL."""

import asyncio
import re
import time

from pyobvector import VectorIndex
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection


def _parameters(value: str) -> dict[str, str]:
    return {
        key.strip().lower(): setting.strip().lower()
        for part in value.split(",")
        if "=" in part
        for key, setting in [part.split("=", 1)]
    }


def _matching_definition(definition: str, quoted_name: str, quoted_column: str, parameters: str) -> bool:
    match = re.search(
        r"^\s*VECTOR\s+(?:KEY|INDEX)\s+" + quoted_name + r"\s*\(\s*" + quoted_column + r"\s*\)\s+WITH\s*\(([^)]*)\)",
        definition,
        re.IGNORECASE | re.MULTILINE,
    )
    if match and all(_parameters(match[1]).get(key) == value for key, value in _parameters(parameters).items()):
        return True
    if re.search(
        r"^\s*(?:(?:UNIQUE|FULLTEXT|VECTOR|SPATIAL)\s+)?(?:KEY|INDEX)\s+" + quoted_name + r"\s*\(",
        definition,
        re.IGNORECASE | re.MULTILINE,
    ):
        message = "An existing OceanBase vector index does not match its native column or parameters"
        raise SQLAlchemyError(message)
    return False


async def create_vector_index(connection: AsyncConnection, index: VectorIndex, /) -> None:
    table, name = index.table, index.name
    if table is None or name is None:
        message = "A native vector index must have a name and bound table"
        raise ValueError(message)
    preparer = connection.dialect.identifier_preparer
    columns = tuple(index.columns)
    column = columns[0]
    quoted_name = re.escape(preparer.quote_identifier(name))
    quoted_column = re.escape(preparer.quote_identifier(column.name))

    async def existing_matches() -> bool:
        result = await connection.exec_driver_sql(f"SHOW CREATE TABLE {preparer.format_table(table)}")
        row = result.first()
        definition = "" if row is None else str(row[1])
        # STATISTICS exposes hidden BTREE auxiliary columns for VECTOR indexes.
        return _matching_definition(definition, quoted_name, quoted_column, index.params or "")

    # 4.4.2.1 rejects another VECTOR index on the column before honoring
    # IF NOT EXISTS. Revalidate only that exact duplicate-column race.
    if await existing_matches():
        return
    parameters = "" if index.params is None else f" WITH ({index.params})"
    names = ", ".join(preparer.format_column(value) for value in columns)
    try:
        await connection.exec_driver_sql(
            f"CREATE VECTOR INDEX IF NOT EXISTS {preparer.format_index(index)} "
            f"ON {preparer.format_table(table)} ({names}){parameters}"
        )
    except DBAPIError as error:
        original = error.orig
        if (
            original is None
            or not original.args
            or original.args[0] != 1235
            or not str(original.args[1] if len(original.args) > 1 else "")
            .lower()
            .startswith("create vector index on column has vector index is not supported")
        ):
            raise
        # The native column reservation becomes visible before its logical
        # index DDL. Wait for the competing creation's metadata to settle,
        # using the tenant's existing query timeout; never replay CREATE.
        result = await connection.exec_driver_sql("SELECT @@ob_query_timeout")
        deadline = time.monotonic() + max(0, int(result.scalar_one())) / 1_000_000
        while True:
            if await existing_matches():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            await asyncio.sleep(min(0.1, remaining))
