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

"""Durable block observation and conditional, idempotent Topic recovery."""

from datetime import UTC
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.topic_memory.processing import (
    TopicMemoryBlockConflictError,
    TopicMemoryBlockNotFoundError,
    TopicMemoryWorkBlock,
)
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository, database_utc_now
from powercontext.builtin.persistence.tables import (
    SOURCE_JOURNAL_HEADS_TABLE as HEADS,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE as TARGETS,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_WORK_BLOCKS_TABLE as BLOCKS,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_WORK_BUDGETS_TABLE as BUDGETS,
)
from powercontext.builtin.persistence.topic_memory_budget import (
    exhausted_reason,
    load_topic_memory_work_budget,
)


async def observe_topic_memory_block(
    connection: AsyncConnection, scope_id: str, binding_name: str, *, worker_stopped: bool = False
) -> TopicMemoryWorkBlock | None:
    """Record the first observation; an active final attempt may still publish.

    Only the dispatcher/failed Worker can attest that the Worker has stopped.
    Status readers instead require a finished attempt or an obsolete lease.
    Legacy rows carry an observation timestamp, never an invented failure time.
    """
    cursor = await SourceCursorRepository().load(connection, scope_id, binding_name)
    after = 0 if cursor is None else cursor.cursor.sequence
    row = await load_topic_memory_work_budget(connection, scope_id, binding_name, after)
    if row is None or not exhausted_reason(row):
        return None
    observed_attempt = row
    lease = None
    if not worker_stopped and row["started_at"] is not None and row["finished_at"] is None:
        # A current locking read avoids a stale expired lease in MySQL's
        # repeatable-read snapshot. Match publication's lease -> head order.
        lease = await ArtifactProcessingLeaseRepository().load(connection, row["supervisor_group"], for_update=True)
    # Serialize observation with capture, begin, publication and explicit retry.
    await connection.execute(update(HEADS).where(HEADS.c.scope_id == scope_id).values(position=HEADS.c.position))
    cursor = await SourceCursorRepository().load(connection, scope_id, binding_name, for_update=True)
    after = 0 if cursor is None else cursor.cursor.sequence
    row = await load_topic_memory_work_budget(connection, scope_id, binding_name, after, for_update=True)
    if row is None or not (reason := exhausted_reason(row)):
        return None
    target = await connection.scalar(
        select(TARGETS.c.source_through)
        .where(TARGETS.c.scope_id == scope_id, TARGETS.c.binding_name == binding_name)
        .with_for_update()
    )
    head = await connection.scalar(select(HEADS.c.position).where(HEADS.c.scope_id == scope_id).with_for_update())
    through = head if target is None else target
    if through is None or through <= after:
        return None
    existing = (
        (
            await connection.execute(
                select(BLOCKS)
                .where(
                    BLOCKS.c.scope_id == scope_id,
                    BLOCKS.c.binding_name == binding_name,
                    BLOCKS.c.source_after == after,
                    BLOCKS.c.attempt_id == row["attempt_id"],
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        return _block(existing)
    if not worker_stopped and row["started_at"] is not None and row["finished_at"] is None:
        if observed_attempt["attempt_id"] != row["attempt_id"]:
            # A replacement attempt appeared while acquiring the locks. Its
            # owner was not inspected, so it must remain pending on this read.
            return None
        now = await database_utc_now(connection)
        if (
            lease is not None
            and lease.holder_id == row["holder_id"]
            and lease.supervisor_generation == row["supervisor_generation"]
            and (lease.lease_expires_at is None or lease.lease_expires_at > now)
        ):
            return None
    now = await database_utc_now(connection)
    block_id = str(uuid4())
    await connection.execute(
        insert(BLOCKS).values(
            block_id=block_id,
            **{
                name: row[name]
                for name in (
                    "scope_id",
                    "binding_name",
                    "source_after",
                    "attempt_id",
                    "attempts",
                    "requests",
                    "tokens",
                    "last_error_code",
                )
            },
            source_through=row["attempt_source_through"],
            budget_source_through=row["source_through"],
            target_source_through=through,
            error_code=reason,
            blocked_at=now,
        )
    )
    return await get_topic_memory_block(connection, scope_id, binding_name, block_id)


async def get_topic_memory_block(
    connection: AsyncConnection, scope_id: str, binding_name: str, block_id: str, *, for_update: bool = False
) -> TopicMemoryWorkBlock:
    statement = select(BLOCKS).where(
        BLOCKS.c.block_id == block_id,
        BLOCKS.c.scope_id == scope_id,
        BLOCKS.c.binding_name == binding_name,
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise TopicMemoryBlockNotFoundError
    return _block(row)


async def retry_topic_memory_block(
    connection: AsyncConnection, scope_id: str, binding_name: str, block_id: str
) -> TopicMemoryWorkBlock:
    """Grant one new allowance for an observed block, retaining its audit record."""
    block = await get_topic_memory_block(connection, scope_id, binding_name, block_id)
    if block.retried_at is not None:
        return block
    await connection.execute(update(HEADS).where(HEADS.c.scope_id == scope_id).values(position=HEADS.c.position))
    # The first read may have pinned an older MySQL repeatable-read snapshot.
    block = await get_topic_memory_block(connection, scope_id, binding_name, block_id, for_update=True)
    if block.retried_at is not None:
        return block
    cursor = await SourceCursorRepository().load(connection, scope_id, binding_name, for_update=True)
    after = 0 if cursor is None else cursor.cursor.sequence
    row = await load_topic_memory_work_budget(connection, scope_id, binding_name, after, for_update=True)
    if (
        row is None
        or after != block.source_after
        or row["attempt_id"] != block.attempt_id
        or exhausted_reason(row) != block.error_code
    ):
        raise TopicMemoryBlockConflictError
    await connection.execute(
        delete(BUDGETS).where(
            BUDGETS.c.scope_id == scope_id,
            BUDGETS.c.binding_name == binding_name,
            BUDGETS.c.source_after == after,
            BUDGETS.c.attempt_id == block.attempt_id,
        )
    )
    pending = await ArtifactProcessingPendingRepository().request_flush(connection, scope_id, binding_name)
    if pending is None:
        raise TopicMemoryBlockConflictError
    intent = await ArtifactProcessingIntentRepository().request(connection, scope_id, binding_name)
    await connection.execute(
        update(BLOCKS)
        .where(BLOCKS.c.block_id == block_id)
        .values(
            retried_at=await database_utc_now(connection),
            retry_request_generation=intent.requested_generation,
        )
    )
    return await get_topic_memory_block(connection, scope_id, binding_name, block_id)


def _block(row) -> TopicMemoryWorkBlock:
    return TopicMemoryWorkBlock(
        block_id=row["block_id"],
        **{
            name: row[name]
            for name in (
                "attempt_id",
                "scope_id",
                "binding_name",
                "source_after",
                "source_through",
                "budget_source_through",
                "legacy_source_through",
                "target_source_through",
                "attempts",
                "requests",
                "tokens",
                "last_error_code",
                "retry_request_generation",
            )
        },
        error_code=row["error_code"],
        blocked_at=row["blocked_at"].replace(tzinfo=UTC),
        retried_at=None if row["retried_at"] is None else row["retried_at"].replace(tzinfo=UTC),
    )
