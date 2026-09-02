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

_RECORD_COLUMNS = {
    "pc_sources": {"created_at": "DATETIME NULL"},
    "pc_artifacts": {"created_at": "DATETIME NULL"},
    "pc_artifact_heads": {"deleted_at": "DATETIME NULL"},
}


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

    await ensure_record_columns(connection, {table.name for table in tables})


async def ensure_record_columns(connection: AsyncConnection, selected_tables: set[str], /) -> None:
    """Add nullable lifecycle timestamps to schemas created before base access existed."""

    dialect = connection.dialect.name
    for table_name, required in _RECORD_COLUMNS.items():
        if table_name not in selected_tables:
            continue
        if dialect == "sqlite":
            rows = (await connection.exec_driver_sql(f"PRAGMA table_info('{table_name}')")).mappings()
            existing = {str(row["name"]) for row in rows}
        elif dialect == "mysql":
            rows = (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = :table_name"
                    ),
                    {"table_name": table_name},
                )
            ).scalars()
            existing = {str(value) for value in rows}
        else:
            raise ValueError(f"unsupported record schema migration dialect: {dialect}")  # noqa: TRY003

        for column_name, column_type in required.items():
            if column_name not in existing:
                await connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
