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

from __future__ import annotations

import asyncio

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.work import EnqueueResult, WorkRepository, WorkStatus
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import WorkerConfig
from powercontext.builtin.runtime.models import MemoryFlushResult
from powercontext.builtin.runtime.work_handlers import MemoryWorkHandler, enqueue_memory_work
from powercontext.builtin.runtime.worker import DurableWorker
from powercontext.builtin.sources import ContentCapture, ContentSource


class _ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_manual_and_scheduled_discovery_join_one_memory_window_and_commit_once(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'durable.db'}"),
        )
        async with open_builtin_contexts(config, candidate_pipeline=_ContentCandidatePipeline()) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="First durable fact."))
            await context.sources.capture(ContentCapture(source_id="two", content="Second durable fact."))

            manual = await enqueue_memory_work(contexts, "project", limit=100, max_attempts=5)
            scheduled = await enqueue_memory_work(contexts, "project", limit=100, max_attempts=5)
            assert isinstance(manual, EnqueueResult)
            assert isinstance(scheduled, EnqueueResult)
            assert manual.created is True
            assert scheduled.created is False
            assert manual.work.work_id == scheduled.work.work_id

            worker = DurableWorker(
                database=contexts.database,
                worker_id="worker-a",
                handlers=(MemoryWorkHandler(contexts),),
                config=WorkerConfig(concurrency=1),
            )
            assert await worker.run_once() == 1

            async with contexts.database.transaction() as connection:
                stored = await WorkRepository().get(connection, manual.work.work_id)
            assert stored.status is WorkStatus.SUCCEEDED
            assert stored.result_payload is not None
            assert stored.result_payload["current_cursor"] == 2

            memory = await context.artifacts.memory.head("memory")
            assert memory.revision == 1
            assert {entry.text for entry in await context.artifacts.memory.entries(memory)} == {
                "First durable fact.",
                "Second durable fact.",
            }
            idle = await enqueue_memory_work(contexts, "project", limit=100, max_attempts=5)
            assert isinstance(idle, MemoryFlushResult)
            assert idle.current_cursor == 2

    asyncio.run(scenario())
