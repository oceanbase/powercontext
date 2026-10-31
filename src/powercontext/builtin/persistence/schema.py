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

from sqlalchemy import Connection, MetaData, Table, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable, sort_tables

from powercontext.builtin.persistence.bootstrap import mysql_error_code


async def create_tables(connection: AsyncConnection, tables: Sequence[Table], /) -> None:
    """Create selected tables only for unmanaged storage; managed schemas are read-only."""

    names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    if "pc_schema_revision" in names:
        if not {table.name for table in tables} <= names:
            from powercontext.builtin.persistence.migrations.models import MigrationError

            raise MigrationError(
                "unsupported_configuration", "The versioned database does not cover the requested feature tables."
            )
        return
    if connection.dialect.name == "mysql":
        await connection.run_sync(lambda sync: _create_mysql_tables(sync, tables))
        return

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


def _create_mysql_tables(connection: Connection, tables: Sequence[Table]) -> None:
    """Use atomic table creation and normalize only verified index-name races.

    Existing tables are left untouched. This initializes missing tables; it does
    not reconcile existing schemas or replace the explicit migration workflow.
    """
    groups: dict[MetaData, list[Table]] = {}
    for table in dict.fromkeys(tables):
        groups.setdefault(table.metadata, []).append(table)
    for selected in groups.values():
        for table in sort_tables(selected):
            if inspect(connection).has_table(table.name, schema=table.schema):
                continue
            connection.execute(CreateTable(table, if_not_exists=True))
            for index in sorted(table.indexes, key=lambda item: str(item.name)):
                try:
                    index.create(connection, checkfirst=True)
                except DBAPIError as error:
                    if mysql_error_code(error) != 1061:
                        raise
                    actual = inspect(connection).get_indexes(table.name, schema=table.schema)
                    if not any(
                        item["name"] == index.name
                        and item["column_names"] == [column.name for column in index.columns]
                        and bool(item["unique"]) == bool(index.unique)
                        for item in actual
                    ):
                        raise


async def ensure_portable_timestamp_precision(connection: AsyncConnection, tables: Sequence[Table]) -> None:
    """Explicit runtime schema upgrade preserving portable microsecond timestamps."""

    if connection.dialect.name != "mysql":
        return

    portable_columns = {
        "pc_artifacts": "created_at",
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
            nullability = "NULL" if table.c[column].nullable else "NOT NULL"
            await connection.exec_driver_sql(
                f"ALTER TABLE `{table.name}` MODIFY COLUMN `{column}` DATETIME(6) {nullability}"
            )
