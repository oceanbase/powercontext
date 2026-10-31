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

"""Delete test-owned Scopes from the tables actually installed by a database profile."""

import re
from collections.abc import Sequence

from sqlalchemy import column, delete, inspect, select, table
from sqlalchemy.engine import Connection

from powercontext.builtin.persistence.database import AsyncDatabase


def _scope_tables(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    shadows = set()
    if connection.dialect.name == "sqlite":
        shadows = {row.name for row in connection.exec_driver_sql("PRAGMA table_list") if row.type == "shadow"}
    # Native vector tables are registered dynamically. Virtual indexes own their
    # shadow storage, so delete through the virtual table rather than its shadows.
    return tuple(
        name
        for name, _ in reversed(inspector.get_sorted_table_and_fkc_names())
        if name is not None
        and re.fullmatch(r"pc_[a-z0-9_]+", name)
        and name not in shadows
        and any(item["name"] == "scope_id" for item in inspector.get_columns(name))
    )


async def cleanup_scopes(database: AsyncDatabase, scopes: Sequence[str]) -> None:
    """Remove only the supplied test Scopes, including native and virtual projections."""
    if not scopes:
        return
    async with database.transaction() as connection:
        names = await connection.run_sync(_scope_tables)
        tables = tuple(table(name, column("scope_id")) for name in names)
        for scoped in tables:
            await connection.execute(delete(scoped).where(scoped.c.scope_id.in_(scopes)))
        for scoped in tables:
            remaining = await connection.scalar(select(scoped.c.scope_id).where(scoped.c.scope_id.in_(scopes)).limit(1))
            assert remaining is None, f"test Scope cleanup left rows in {scoped.name}"
