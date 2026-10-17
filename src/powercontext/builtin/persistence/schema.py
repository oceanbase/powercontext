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

from sqlalchemy import MetaData, Table, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def create_tables(connection: AsyncConnection, tables: Sequence[Table], /) -> None:
    """Create missing tables and indexes using SQLAlchemy's native metadata support."""

    groups: dict[MetaData, list[Table]] = {}
    for table in tables:
        groups.setdefault(table.metadata, []).append(table)

    for metadata, selected in groups.items():
        await connection.run_sync(
            lambda sync_connection, current=metadata, owned=selected: current.create_all(
                sync_connection,
                tables=owned,
                checkfirst=True,
            )
        )


async def ensure_portable_timestamp_precision(connection: AsyncConnection, tables: Sequence[Table]) -> None:
    """Explicit runtime schema upgrade preserving portable microsecond timestamps."""

    if connection.dialect.name != "mysql":
        return

    portable_columns = {
        "pc_profile_policies": "updated_at",
        "pc_topic_memory_revision_publications": "published_at",
        "pc_artifact_tags": "assigned_at",
    }
    for table in tables:
        column = portable_columns.get(table.name)
        if column is None:
            continue
        precision = await connection.scalar(
            text(
                "SELECT DATETIME_PRECISION FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name AND COLUMN_NAME = :column_name"
            ),
            {"table_name": table.name, "column_name": column},
        )
        if precision is not None and int(precision) < 6:
            await connection.exec_driver_sql(
                f"ALTER TABLE `{table.name}` MODIFY COLUMN `{column}` DATETIME(6) NOT NULL"
            )
