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

"""Relational table creation over caller-selected SQLAlchemy metadata."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import MetaData, Table
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateIndex, CreateTable


async def create_tables(connection: AsyncConnection, tables: Sequence[Table], /) -> None:
    """Create missing tables and indexes using SQLAlchemy's native metadata support."""

    groups: dict[MetaData, list[Table]] = {}
    for table in tables:
        groups.setdefault(table.metadata, []).append(table)

    for metadata, selected in groups.items():
        if connection.dialect.name == "mysql":
            # OceanBase/seekdb support native idempotent DDL. A separate
            # checkfirst read can race with another replica's startup.
            for table in metadata.sorted_tables:
                if table not in selected:
                    continue
                await connection.execute(CreateTable(table, if_not_exists=True))
                for index in sorted(table.indexes, key=lambda item: item.name or ""):
                    # Native vector indexes have their own schema visitor and
                    # owner. Compiling them as CreateIndex would emit ordinary
                    # INDEX SQL and discard their VECTOR type and parameters.
                    if index.__visit_name__ == "index":
                        await connection.execute(CreateIndex(index, if_not_exists=True))
            continue
        await connection.run_sync(
            lambda sync_connection, current=metadata, owned=selected: current.create_all(
                sync_connection,
                tables=owned,
                checkfirst=True,
            )
        )
