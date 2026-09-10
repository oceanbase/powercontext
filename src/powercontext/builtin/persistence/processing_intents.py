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

"""Coalesced Scope invocation intents, independent of domain input progress.

Every mutation joins the caller's transaction. Domain callers publish input and
dirty/requested together; Supervisor callers must first validate their fence.
Rows are retained after completion so neither generations nor sequence IDs are
reused. Queue state and retry history are deliberately absent.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    ARTIFACT_PROCESSING_SEQUENCES_TABLE,
)
from powercontext.limits import MAX_BINDING_NAME_LENGTH, MAX_SCOPE_ID_LENGTH


class StoredArtifactProcessingIntent(BaseModel):
    """One durable invocation counter and independent domain dirty counter."""

    binding_name: str
    scope_id: str
    pending_sequence: int
    dirty_generation: int
    clean_generation: int
    requested_generation: int
    handled_generation: int
    last_auto_scan_generation: int


class ArtifactProcessingIntentRepository:
    """Persist intent counters using caller-owned short transactions."""

    async def load(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        /,
        *,
        for_update: bool = False,
    ) -> StoredArtifactProcessingIntent | None:
        _require_key(scope_id, binding_name)
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        condition = (table.c.scope_id == scope_id) & (table.c.binding_name == binding_name)
        if for_update and connection.dialect.name == "sqlite":
            # SQLite ignores FOR UPDATE. Even an unmatched UPDATE opens the
            # real write transaction before the read, protecting first inserts.
            await connection.execute(update(table).where(condition).values(dirty_generation=table.c.dirty_generation))
        statement = select(table).where(condition)
        if for_update:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else StoredArtifactProcessingIntent.model_validate(dict(row))

    async def ensure(
        self, connection: AsyncConnection, scope_id: str, binding_name: str, /
    ) -> StoredArtifactProcessingIntent:
        """Register a stable sequence without accepting or marking any work."""

        current = await self.load(connection, scope_id, binding_name)
        if current is not None:
            return current
        sequence = ARTIFACT_PROCESSING_SEQUENCES_TABLE
        if connection.dialect.name == "sqlite":
            seed = sqlite_insert(sequence).values(singleton=1, sequence=0).on_conflict_do_nothing()
        elif connection.dialect.name == "mysql":
            seed = mysql_insert(sequence).values(singleton=1, sequence=0)
            seed = seed.on_duplicate_key_update(sequence=sequence.c.sequence)
        else:
            raise InvalidRepositoryArgumentError("dialect", "processing intents require SQLite or MySQL")
        await connection.execute(seed)
        # Serialize first registrations across bindings. Recheck using a current
        # locked read after waiting, including under MySQL repeatable read.
        await connection.execute(update(sequence).where(sequence.c.singleton == 1).values(sequence=sequence.c.sequence))
        current = await self.load(connection, scope_id, binding_name, for_update=True)
        if current is not None:
            return current
        await connection.execute(
            update(sequence).where(sequence.c.singleton == 1).values(sequence=sequence.c.sequence + 1)
        )
        allocated = int(
            cast(int, await connection.scalar(select(sequence.c.sequence).where(sequence.c.singleton == 1)))
        )
        await connection.execute(
            insert(ARTIFACT_PROCESSING_INTENTS_TABLE).values(
                binding_name=binding_name, scope_id=scope_id, pending_sequence=allocated
            )
        )
        return cast(StoredArtifactProcessingIntent, await self.load(connection, scope_id, binding_name))

    async def mark_dirty(
        self, connection: AsyncConnection, scope_id: str, binding_name: str, /
    ) -> StoredArtifactProcessingIntent:
        await self.ensure(connection, scope_id, binding_name)
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        await connection.execute(
            update(table)
            .where(table.c.scope_id == scope_id, table.c.binding_name == binding_name)
            .values(dirty_generation=table.c.dirty_generation + 1)
        )
        return cast(
            StoredArtifactProcessingIntent, await self.load(connection, scope_id, binding_name, for_update=True)
        )

    async def request(
        self, connection: AsyncConnection, scope_id: str, binding_name: str, /
    ) -> StoredArtifactProcessingIntent:
        await self.ensure(connection, scope_id, binding_name)
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        await connection.execute(
            update(table)
            .where(table.c.scope_id == scope_id, table.c.binding_name == binding_name)
            .values(requested_generation=table.c.requested_generation + 1)
        )
        return cast(
            StoredArtifactProcessingIntent, await self.load(connection, scope_id, binding_name, for_update=True)
        )

    async def acknowledge(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        generation: int,
        /,
        *,
        clean_generation: int | None = None,
    ) -> StoredArtifactProcessingIntent:
        _require_nonnegative("generation", generation)
        current = await self.load(connection, scope_id, binding_name, for_update=True)
        if current is None or generation > current.requested_generation:
            raise InvalidRepositoryArgumentError("generation", "cannot acknowledge an unaccepted invocation")
        if generation <= current.handled_generation:
            return current
        clean = current.clean_generation
        if clean_generation is not None:
            _require_nonnegative("clean_generation", clean_generation)
            if clean_generation > current.dirty_generation:
                raise InvalidRepositoryArgumentError("clean_generation", "cannot acknowledge unseen domain work")
            clean = max(clean, clean_generation)
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        await connection.execute(
            update(table)
            .where(table.c.scope_id == scope_id, table.c.binding_name == binding_name)
            .values(handled_generation=max(current.handled_generation, generation), clean_generation=clean)
        )
        return cast(StoredArtifactProcessingIntent, await self.load(connection, scope_id, binding_name))

    async def scan(
        self,
        connection: AsyncConnection,
        binding_name: str,
        /,
        *,
        after_sequence: int = 0,
        limit: int = 100,
        requested_only: bool = False,
        dirty_only: bool = False,
        upper_sequence: int | None = None,
    ) -> tuple[StoredArtifactProcessingIntent, ...]:
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        _require_nonnegative("after_sequence", after_sequence)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise InvalidRepositoryArgumentError("limit", "must be a positive integer")
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        statement = select(table).where(table.c.binding_name == binding_name, table.c.pending_sequence > after_sequence)
        if upper_sequence is not None:
            _require_nonnegative("upper_sequence", upper_sequence)
            statement = statement.where(table.c.pending_sequence <= upper_sequence)
        if requested_only:
            statement = statement.where(table.c.requested_generation > table.c.handled_generation)
        if dirty_only:
            statement = statement.where(table.c.dirty_generation > table.c.clean_generation)
        rows = (await connection.execute(statement.order_by(table.c.pending_sequence).limit(limit))).mappings()
        return tuple(StoredArtifactProcessingIntent.model_validate(dict(row)) for row in rows)

    async def max_sequence(self, connection: AsyncConnection, binding_name: str, /) -> int:
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        return int(
            await connection.scalar(
                select(func.max(table.c.pending_sequence)).where(table.c.binding_name == binding_name)
            )
            or 0
        )

    async def admit(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        scan_generation: int,
        /,
    ) -> StoredArtifactProcessingIntent:
        """Idempotently accept one member of the current frozen scan.

        The caller checks ready/running/retry exclusion and validates its fence
        in this transaction before calling. Existing accepted work is reused.
        """

        _require_nonnegative("scan_generation", scan_generation)
        state_table = ARTIFACT_PROCESSING_BINDING_STATES_TABLE
        state = (
            (
                await connection.execute(
                    select(state_table).where(state_table.c.binding_name == binding_name).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if state is None or not state["scan_in_progress"] or state["scan_generation"] != scan_generation:
            raise InvalidRepositoryArgumentError("scan_generation", "automatic scan is not current")
        current = await self.load(connection, scope_id, binding_name, for_update=True)
        if current is None:
            raise InvalidRepositoryArgumentError("scope_id", "automatic member is not registered")
        if current.pending_sequence > int(state["scan_upper_pending_sequence"] or 0):
            raise InvalidRepositoryArgumentError("scope_id", "automatic member is outside the frozen scan")
        if current.last_auto_scan_generation >= scan_generation:
            return current
        requested = current.requested_generation
        if current.dirty_generation > current.clean_generation and requested == current.handled_generation:
            requested += 1
        table = ARTIFACT_PROCESSING_INTENTS_TABLE
        await connection.execute(
            update(table)
            .where(table.c.scope_id == scope_id, table.c.binding_name == binding_name)
            .values(requested_generation=requested, last_auto_scan_generation=scan_generation)
        )
        return cast(StoredArtifactProcessingIntent, await self.load(connection, scope_id, binding_name))


def _require_key(scope_id: str, binding_name: str) -> None:
    _require_identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
    _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)


def _require_identifier(field: str, value: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > limit:
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed identifier within the length limit")


def _require_nonnegative(field: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidRepositoryArgumentError(field, "must be a non-negative integer")


StoredIntent = StoredArtifactProcessingIntent

__all__ = ["ArtifactProcessingIntentRepository", "StoredArtifactProcessingIntent", "StoredIntent"]
