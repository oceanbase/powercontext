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

"""Persist exact Memory budget rejections and conditional recovery receipts."""

from datetime import UTC
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.atomic_memory.processing import (
    AtomicMemoryBlockNotFoundError,
    AtomicMemoryWorkBlock,
)
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.supervision import database_utc_now
from powercontext.builtin.persistence.tables import MEMORY_WORK_BLOCKS_TABLE as BLOCKS
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE as HEADS
from powercontext.builtin.processing_diagnostics import ProcessingDiagnostics, ProcessingFailureReason, ProcessingStage


async def lock_memory_frontier(connection: AsyncConnection, scope_id: str) -> None:
    # Match Atomic publication and capture: journal head before intent/cursor locks.
    await connection.execute(update(HEADS).where(HEADS.c.scope_id == scope_id).values(position=HEADS.c.position))


async def current_memory_block(connection: AsyncConnection, scope_id: str, binding_name: str):
    cursor = await SourceCursorRepository().load(connection, scope_id, binding_name)
    after = 0 if cursor is None else cursor.cursor.sequence
    row = (
        (
            await connection.execute(
                select(BLOCKS).where(
                    BLOCKS.c.scope_id == scope_id,
                    BLOCKS.c.binding_name == binding_name,
                    BLOCKS.c.source_after == after,
                    BLOCKS.c.active == 1,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else _block(row)


async def get_memory_block(connection: AsyncConnection, scope_id: str, binding_name: str, block_id: str):
    row = (
        (
            await connection.execute(
                select(BLOCKS).where(
                    BLOCKS.c.scope_id == scope_id,
                    BLOCKS.c.binding_name == binding_name,
                    BLOCKS.c.block_id == block_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AtomicMemoryBlockNotFoundError
    return _block(row)


async def record_memory_block(
    connection: AsyncConnection,
    scope_id: str,
    binding_name: str,
    *,
    source_after: int,
    source_through: int,
    input_fingerprint: str,
    diagnostics: ProcessingDiagnostics,
) -> AtomicMemoryWorkBlock:
    """Caller holds the frontier lock and has fenced its selected cursor with CAS."""
    existing = await current_memory_block(connection, scope_id, binding_name)
    if existing is not None:
        return existing
    block_id = str(uuid4())
    await connection.execute(
        insert(BLOCKS).values(
            block_id=block_id,
            scope_id=scope_id,
            binding_name=binding_name,
            source_after=source_after,
            source_through=source_through,
            active=1,
            input_fingerprint=input_fingerprint,
            estimated_input_tokens=diagnostics.estimated_input_tokens,
            effective_budget=diagnostics.effective_budget,
            token_estimator_id=diagnostics.token_estimator_id,
            failure_location=diagnostics.failure_location,
            blocked_at=await database_utc_now(connection),
        )
    )
    return await get_memory_block(connection, scope_id, binding_name, block_id)


async def accept_memory_retry(connection: AsyncConnection, block: AtomicMemoryWorkBlock, generation: int):
    await connection.execute(
        update(BLOCKS)
        .where(BLOCKS.c.block_id == block.block_id, BLOCKS.c.active == 1)
        .values(
            active=None,
            retried_at=await database_utc_now(connection),
            retry_request_generation=generation,
        )
    )
    return await get_memory_block(connection, block.scope_id, block.binding_name, block.block_id)


def memory_block_diagnostics(block: AtomicMemoryWorkBlock) -> ProcessingDiagnostics:
    return ProcessingDiagnostics(
        stage=ProcessingStage.BUSINESS_VALIDATION,
        reason=ProcessingFailureReason.CONTEXT_OVERFLOW,
        input_item_count=1,
        estimated_input_tokens=block.estimated_input_tokens,
        effective_budget=block.effective_budget,
        token_estimator_id=block.token_estimator_id,
        failure_location=block.failure_location,
    )


def _block(row) -> AtomicMemoryWorkBlock:
    values = {name: row[name] for name in AtomicMemoryWorkBlock.model_fields if name in row}
    for name in ("blocked_at", "retried_at"):
        if values.get(name) is not None:
            values[name] = values[name].replace(tzinfo=UTC)
    return AtomicMemoryWorkBlock.model_validate(values)
