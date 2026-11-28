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

"""Memory-owned recovery and metadata-only scheduler admission."""

from sqlalchemy import select

from powercontext.builtin.artifacts.atomic_memory.processing import AtomicMemoryProcessingStatus
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.memory_recovery import (
    current_memory_block,
    get_memory_block,
    memory_block_diagnostics,
)
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingBlock
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME


def processing_block(block) -> ArtifactProcessingBlock:
    return ArtifactProcessingBlock(
        key=block.block_id,
        stage="atomic_memory",
        error_code=block.error_code,
        details=(("source_after", block.source_after), ("source_through", block.source_through)),
        diagnostics=memory_block_diagnostics(block),
    )


async def atomic_memory_processing_block(connection, scope_id, binding_name, *, dream_enabled=False):
    if dream_enabled:
        from powercontext.builtin.dream.bindings import operations_for_binding
        from powercontext.builtin.persistence.dream import DreamRepository

        if (
            await DreamRepository().next_pending(
                connection, scope_id, operations_for_binding(binding_name), binding=binding_name
            )
            is not None
        ):
            return None
    block = await current_memory_block(connection, scope_id, binding_name)
    return None if block is None else processing_block(block)


class AtomicMemoryRecoveryService:
    def __init__(self, processor) -> None:
        self.processor = processor
        self.database = processor.application.database

    async def status(self, scope_id: str) -> AtomicMemoryProcessingStatus:
        async with self.database.transaction(consistent_snapshot=True) as connection:
            cursor = await SourceCursorRepository().load(connection, scope_id, SOURCE_WINDOW_TRIGGER_NAME)
            after = 0 if cursor is None else cursor.cursor.sequence
            head = (
                await connection.scalar(
                    select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(
                        SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id
                    )
                )
                or 0
            )
            block = await current_memory_block(connection, scope_id, SOURCE_WINDOW_TRIGGER_NAME)
            return AtomicMemoryProcessingStatus(
                state="blocked" if block is not None else "idle" if after >= head else "pending",
                source_cursor=after,
                source_head=head,
                block=block,
            )

    async def get_block(self, scope_id: str, block_id: str):
        async with self.database.transaction(consistent_snapshot=True) as connection:
            return await get_memory_block(connection, scope_id, SOURCE_WINDOW_TRIGGER_NAME, block_id)

    async def retry(self, scope_id: str, block_id: str, *, context=None):
        return await self.processor.retry_block(scope_id, block_id, context=context)
