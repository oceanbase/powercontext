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

"""Durable, pre-I/O reservations for one unadvanced Topic Memory frontier."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryGenerationError
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import GenerationConflictError
from powercontext.builtin.persistence.supervision import ArtifactProcessingFence, ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE, TOPIC_MEMORY_WORK_BUDGETS_TABLE

# Server-owned ceilings, shared by ALL attempts and stages at the same Cursor.
MAX_TOPIC_MEMORY_WORK_ATTEMPTS = 3
MAX_TOPIC_MEMORY_WORK_REQUESTS = 512
MAX_TOPIC_MEMORY_WORK_TOKENS = 64_000_000


def exhausted_reason(row: Mapping[Any, Any]) -> str:
    if row["failure_code"]:
        return str(row["failure_code"])
    if row["attempts"] >= MAX_TOPIC_MEMORY_WORK_ATTEMPTS:
        return "window_attempt_limit"
    if row["requests"] >= MAX_TOPIC_MEMORY_WORK_REQUESTS or row["tokens"] >= MAX_TOPIC_MEMORY_WORK_TOKENS:
        return "window_provider_budget_exceeded"
    return ""


async def require_topic_memory_work_available(
    connection: AsyncConnection, scope_id: str, binding_name: str, source_after: int
) -> None:
    """Cheap selector guard: terminal frontiers never spawn or reproject Sources."""
    table = TOPIC_MEMORY_WORK_BUDGETS_TABLE
    row = (
        (
            await connection.execute(
                select(table).where(
                    table.c.scope_id == scope_id,
                    table.c.binding_name == binding_name,
                    table.c.source_after == source_after,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is not None and (reason := exhausted_reason(row)):
        raise TopicMemoryGenerationError(reason)


class TopicMemoryWorkBudget:
    """Commit worst-case charges before I/O; never refund ambiguous/failed work.

    Identity excludes Worker, lease term, flush generation and window end: none
    of those can reset the allowance before the authoritative Cursor advances.
    A fresh attempt supersedes old same-term work, including its publication.
    """

    def __init__(
        self,
        database: AsyncDatabase,
        *,
        scope_id: str,
        binding_name: str,
        source_after: int,
        source_through: int,
        cursor_generation: int | None,
        fence: ArtifactProcessingFence,
    ) -> None:
        self.database = database
        self.scope_id = scope_id
        self.binding_name = binding_name
        self.source_after = source_after
        self.source_through = source_through
        self.cursor_generation = cursor_generation
        self.fence = fence
        self.attempt_id = str(uuid4())

    def _key(self):
        table = TOPIC_MEMORY_WORK_BUDGETS_TABLE
        return (
            table.c.scope_id == self.scope_id,
            table.c.binding_name == self.binding_name,
            table.c.source_after == self.source_after,
        )

    async def _lock(self, connection: AsyncConnection) -> None:
        await ArtifactProcessingLeaseRepository().require_fence(connection, self.fence)
        # Same lock order as publication. This also starts SQLite's write txn.
        await connection.execute(
            update(SOURCE_JOURNAL_HEADS_TABLE)
            .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == self.scope_id)
            .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
        )
        cursor = await SourceCursorRepository().load(connection, self.scope_id, self.binding_name, for_update=True)
        if (0 if cursor is None else cursor.cursor.sequence) != self.source_after or (
            None if cursor is None else cursor.generation
        ) != self.cursor_generation:
            raise GenerationConflictError(
                self.binding_name, self.cursor_generation, None if cursor is None else cursor.generation
            )

    async def begin(self) -> None:
        table = TOPIC_MEMORY_WORK_BUDGETS_TABLE
        reason = ""
        async with self.database.transaction() as connection:
            await self._lock(connection)
            row = (
                (await connection.execute(select(table).where(*self._key()).with_for_update())).mappings().one_or_none()
            )
            if row is None:
                await connection.execute(
                    insert(table).values(
                        scope_id=self.scope_id,
                        binding_name=self.binding_name,
                        source_after=self.source_after,
                        source_through=self.source_through,
                        attempt_id=self.attempt_id,
                        attempts=1,
                        requests=0,
                        tokens=0,
                        failure_code="",
                    )
                )
            elif reason := exhausted_reason(row):
                await connection.execute(update(table).where(*self._key()).values(failure_code=reason))
            else:
                await connection.execute(
                    update(table)
                    .where(*self._key())
                    .values(
                        attempts=row["attempts"] + 1,
                        attempt_id=self.attempt_id,
                        source_through=max(self.source_through, row["source_through"]),
                    )
                )
        if reason:
            raise TopicMemoryGenerationError(reason)

    async def _current(self, connection: AsyncConnection):
        table = TOPIC_MEMORY_WORK_BUDGETS_TABLE
        row = (await connection.execute(select(table).where(*self._key()).with_for_update())).mappings().one_or_none()
        if row is None or row["attempt_id"] != self.attempt_id:
            raise TopicMemoryGenerationError("window_attempt_superseded")
        return row

    async def reserve(self, *, requests: int, tokens: int) -> None:
        if requests < 1 or tokens < 1:
            raise TopicMemoryGenerationError("invalid_work_reservation")
        table = TOPIC_MEMORY_WORK_BUDGETS_TABLE
        reason = ""
        async with self.database.transaction() as connection:
            await self._lock(connection)
            row = await self._current(connection)
            reason = str(row["failure_code"])
            if not reason and (
                row["requests"] + requests > MAX_TOPIC_MEMORY_WORK_REQUESTS
                or row["tokens"] + tokens > MAX_TOPIC_MEMORY_WORK_TOKENS
            ):
                reason = "window_provider_budget_exceeded"
            values = (
                {"failure_code": reason}
                if reason
                else {"requests": row["requests"] + requests, "tokens": row["tokens"] + tokens}
            )
            await connection.execute(update(table).where(*self._key()).values(**values))
        # Raise AFTER committing the terminal marker, never roll it back.
        if reason:
            raise TopicMemoryGenerationError(reason)

    async def fail(self, reason: str) -> None:
        """Persist a deterministic input rejection without storing source text."""
        async with self.database.transaction() as connection:
            await self._lock(connection)
            await self._current(connection)
            await connection.execute(
                update(TOPIC_MEMORY_WORK_BUDGETS_TABLE)
                .where(*self._key())
                .values(
                    failure_code=reason,
                )
            )

    async def complete(self, connection: AsyncConnection) -> None:
        """Called only inside the atomic publisher, before its Cursor CAS."""
        row = await self._current(connection)
        if row["failure_code"]:
            raise TopicMemoryGenerationError(str(row["failure_code"]))
        await connection.execute(delete(TOPIC_MEMORY_WORK_BUDGETS_TABLE).where(*self._key()))
