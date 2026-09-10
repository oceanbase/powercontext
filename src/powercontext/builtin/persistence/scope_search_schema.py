# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Startup migration for legacy Scope search columns."""

from __future__ import annotations

from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import (
    SCOPE_BINDINGS_TABLE,
    SCOPE_EXTERNAL_REFERENCES_TABLE,
    SCOPES_TABLE,
)

_SEARCH_COLUMNS = {
    "pc_scopes": ("scope_id_search", "title_search", "summary_search"),
    "pc_scope_external_references": ("value_search",),
    "pc_scope_bindings": ("external_id_search",),
}


async def ensure_scope_search_schema(connection: AsyncConnection, /) -> None:
    """Add and backfill compatibility columns without changing original text."""

    dialect = connection.dialect.name
    if dialect not in {"sqlite", "mysql"}:
        raise ValueError(f"unsupported Scope discovery migration dialect: {dialect}")  # noqa: TRY003

    added: list[tuple[str, str]] = []
    for table_name, columns in _SEARCH_COLUMNS.items():
        existing = await _column_names(connection, table_name)
        for column in columns:
            if column in existing:
                continue
            if dialect == "sqlite":
                await connection.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
            else:
                await connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column} MEDIUMTEXT NULL")
            added.append((table_name, column))

    await _backfill_scopes(connection)
    await _backfill_external_references(connection)
    await _backfill_bindings(connection)

    if dialect == "mysql":
        for table_name, column in added:
            await connection.exec_driver_sql(f"ALTER TABLE {table_name} MODIFY COLUMN {column} MEDIUMTEXT NOT NULL")


async def _column_names(connection: AsyncConnection, table_name: str) -> set[str]:
    if connection.dialect.name == "sqlite":
        rows = (await connection.exec_driver_sql(f"PRAGMA table_info('{table_name}')")).mappings()
        return {str(row["name"]) for row in rows}
    rows = (
        await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :table_name"
            ),
            {"table_name": table_name},
        )
    ).mappings()
    return {str(row["column_name"]) for row in rows}


async def _backfill_scopes(connection: AsyncConnection) -> None:
    rows = (
        await connection.execute(
            select(
                SCOPES_TABLE.c.scope_id,
                SCOPES_TABLE.c.title,
                SCOPES_TABLE.c.summary,
            ).where(
                or_(
                    SCOPES_TABLE.c.scope_id_search.is_(None),
                    SCOPES_TABLE.c.scope_id_search == "",
                    SCOPES_TABLE.c.title_search.is_(None),
                    SCOPES_TABLE.c.title_search == "",
                    SCOPES_TABLE.c.summary_search.is_(None),
                    SCOPES_TABLE.c.summary_search == "",
                )
            )
        )
    ).mappings()
    for row in rows:
        await connection.execute(
            update(SCOPES_TABLE)
            .where(SCOPES_TABLE.c.scope_id == row["scope_id"])
            .values(
                scope_id_search=str(row["scope_id"]),
                title_search=str(row["title"]),
                summary_search=str(row["summary"]),
            )
        )


async def _backfill_external_references(connection: AsyncConnection) -> None:
    rows = (
        await connection.execute(
            select(
                SCOPE_EXTERNAL_REFERENCES_TABLE.c.scope_id,
                SCOPE_EXTERNAL_REFERENCES_TABLE.c.ordinal,
                SCOPE_EXTERNAL_REFERENCES_TABLE.c.value,
            ).where(
                or_(
                    SCOPE_EXTERNAL_REFERENCES_TABLE.c.value_search.is_(None),
                    SCOPE_EXTERNAL_REFERENCES_TABLE.c.value_search == "",
                )
            )
        )
    ).mappings()
    for row in rows:
        await connection.execute(
            update(SCOPE_EXTERNAL_REFERENCES_TABLE)
            .where(
                SCOPE_EXTERNAL_REFERENCES_TABLE.c.scope_id == row["scope_id"],
                SCOPE_EXTERNAL_REFERENCES_TABLE.c.ordinal == row["ordinal"],
            )
            .values(value_search=str(row["value"]))
        )


async def _backfill_bindings(connection: AsyncConnection) -> None:
    rows = (
        await connection.execute(
            select(
                SCOPE_BINDINGS_TABLE.c.integration,
                SCOPE_BINDINGS_TABLE.c.kind,
                SCOPE_BINDINGS_TABLE.c.external_id,
            ).where(
                or_(
                    SCOPE_BINDINGS_TABLE.c.external_id_search.is_(None),
                    SCOPE_BINDINGS_TABLE.c.external_id_search == "",
                )
            )
        )
    ).mappings()
    for row in rows:
        await connection.execute(
            update(SCOPE_BINDINGS_TABLE)
            .where(
                SCOPE_BINDINGS_TABLE.c.integration == row["integration"],
                SCOPE_BINDINGS_TABLE.c.kind == row["kind"],
                SCOPE_BINDINGS_TABLE.c.external_id == row["external_id"],
            )
            .values(external_id_search=str(row["external_id"]))
        )


__all__ = ["ensure_scope_search_schema"]
