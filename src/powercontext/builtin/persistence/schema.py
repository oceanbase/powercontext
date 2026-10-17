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

from powercontext.builtin.persistence.atomic_memory_evidence_schema import ATOMIC_MEMORY_EVIDENCE_TABLES
from powercontext.builtin.persistence.migrations.atomic_memory_evidence import apply_atomic_memory_evidence_migration


async def create_tables(connection: AsyncConnection, tables: Sequence[Table], /) -> None:
    """Create selected tables, applying retained evidence through its frozen revision."""

    groups: dict[MetaData, list[Table]] = {}
    evidence_names = {table.name for table in ATOMIC_MEMORY_EVIDENCE_TABLES}
    if any(table.name in evidence_names or table.name == "pc_sources" for table in tables):
        await apply_atomic_memory_evidence_migration(connection)
    for table in tables:
        if table.name in evidence_names:
            continue
        groups.setdefault(table.metadata, []).append(table)

    for metadata, selected in groups.items():
        await connection.run_sync(
            lambda sync_connection, current=metadata, owned=selected: current.create_all(
                sync_connection,
                tables=owned,
                checkfirst=True,
            )
        )
