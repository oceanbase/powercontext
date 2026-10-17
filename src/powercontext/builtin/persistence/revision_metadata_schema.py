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

"""Add the nullable Artifact revision metadata columns to databases created before them."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateColumn

from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE

_REVISION_METADATA_COLUMNS = ("created_at", "created_by", "restored_from_revision", "rollback_reason")


async def ensure_artifact_revision_metadata_schema(connection: AsyncConnection, /) -> None:
    """Add missing revision metadata columns. Existing rows keep NULL, which reads as unknown history."""

    dialect = connection.dialect.name
    if dialect not in {"sqlite", "mysql"}:
        raise ValueError(f"unsupported Artifact revision metadata migration dialect: {dialect}")  # noqa: TRY003
    existing = await _column_names(connection)
    for name in _REVISION_METADATA_COLUMNS:
        if name in existing:
            continue
        declaration = CreateColumn(ARTIFACTS_TABLE.c[name]).compile(dialect=connection.dialect)
        await connection.exec_driver_sql(f"ALTER TABLE {ARTIFACTS_TABLE.name} ADD COLUMN {declaration}")


async def _column_names(connection: AsyncConnection) -> set[str]:
    if connection.dialect.name == "sqlite":
        rows = (await connection.exec_driver_sql(f"PRAGMA table_info('{ARTIFACTS_TABLE.name}')")).mappings()
        return {str(row["name"]) for row in rows}
    rows = (
        await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :table_name"
            ),
            {"table_name": ARTIFACTS_TABLE.name},
        )
    ).mappings()
    return {str(row["column_name"]) for row in rows}
