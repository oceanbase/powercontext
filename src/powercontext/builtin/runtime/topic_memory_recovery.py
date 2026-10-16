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

"""Scope-bound inspection and explicit retry of Topic processing frontiers."""

from sqlalchemy import select, update

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.processing import (
    TopicMemoryProcessingStatus,
    TopicMemoryRetryResult,
    TopicMemoryWorkBlock,
)
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE, TOPIC_MEMORY_PROCESSING_TARGETS_TABLE
from powercontext.builtin.persistence.topic_memory_recovery import (
    get_topic_memory_block,
    observe_topic_memory_block,
    retry_topic_memory_block,
)


class TopicMemoryRecoveryService:
    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def status(self, scope_id: str) -> TopicMemoryProcessingStatus:
        async with self._database.transaction() as connection:
            block = await observe_topic_memory_block(connection, scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            # Current reads under the common head lock keep the response aligned
            # with the observed block, even when an earlier snapshot was pinned.
            await connection.execute(
                update(SOURCE_JOURNAL_HEADS_TABLE)
                .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
                .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
            )
            cursor = await SourceCursorRepository().load(
                connection, scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING, for_update=True
            )
            after = 0 if cursor is None else cursor.cursor.sequence
            head = (
                await connection.scalar(
                    select(SOURCE_JOURNAL_HEADS_TABLE.c.position)
                    .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
                    .with_for_update()
                )
                or 0
            )
            target = await connection.scalar(
                select(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE.c.source_through)
                .where(
                    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE.c.scope_id == scope_id,
                    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE.c.binding_name == TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                )
                .with_for_update()
            )
            return TopicMemoryProcessingStatus(
                state="blocked" if block is not None else "idle" if after >= head and target is None else "pending",
                source_cursor=after,
                source_head=head,
                target_source_through=target,
                block=block,
            )

    async def get_block(self, scope_id: str, block_id: str) -> TopicMemoryWorkBlock:
        async with self._database.transaction() as connection:
            return await get_topic_memory_block(connection, scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING, block_id)

    async def retry(self, scope_id: str, block_id: str) -> TopicMemoryRetryResult:
        async with self._database.transaction() as connection:
            block = await retry_topic_memory_block(connection, scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING, block_id)
        return TopicMemoryRetryResult(block=block)
