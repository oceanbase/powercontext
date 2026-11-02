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

"""Memory extraction window reductions retained until the backlog is consumed."""

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import MEMORY_SOURCE_WINDOWS_TABLE, SOURCES_TABLE
from powercontext.limits import MAX_SCOPE_ID_LENGTH


class MemorySourceWindowRepository:
    """Keep one bounded recovery hint per Scope, serialized by the Memory cursor CAS."""

    async def signal(
        self, connection: AsyncConnection, scope_id: str, after: int, configured_limit: int
    ) -> tuple[int, int]:
        """Read the journal watermark and recovery limit for one window decision."""

        if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
            raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
        if len(scope_id) > MAX_SCOPE_ID_LENGTH:
            raise InvalidRepositoryArgumentError("scope_id", f"must not exceed {MAX_SCOPE_ID_LENGTH} characters")
        table = MEMORY_SOURCE_WINDOWS_TABLE
        reduction = (
            select(table.c.window_limit)
            .where(table.c.scope_id == scope_id, table.c.source_through > after)
            .scalar_subquery()
        )
        position, limit = (
            await connection.execute(
                select(func.coalesce(func.max(SOURCES_TABLE.c.journal_position), 0), reduction).where(
                    SOURCES_TABLE.c.scope_id == scope_id
                )
            )
        ).one()
        return int(position), configured_limit if limit is None else min(configured_limit, int(limit))

    async def reduce(
        self, connection: AsyncConnection, scope_id: str, *, source_through: int, window_limit: int
    ) -> None:
        """Replace a hint only after acquiring the cursor CAS in this transaction."""

        table = MEMORY_SOURCE_WINDOWS_TABLE
        await connection.execute(delete(table).where(table.c.scope_id == scope_id))
        await connection.execute(
            insert(table).values(scope_id=scope_id, source_through=source_through, window_limit=window_limit)
        )

    async def clear_consumed(self, connection: AsyncConnection, scope_id: str, through: int) -> None:
        table = MEMORY_SOURCE_WINDOWS_TABLE
        await connection.execute(delete(table).where(table.c.scope_id == scope_id, table.c.source_through <= through))
