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

from powercontext.builtin.artifacts.experience import (
    EXPERIENCE_INCUBATION_CURSOR_NAME,
    ExperienceCandidateInput,
    ExperienceContent,
)
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.work import EnqueueResult, WorkRepository, WorkStatus
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import WorkerConfig
from powercontext.builtin.runtime.work_handlers import ExperienceWorkHandler, experience_work_spec
from powercontext.builtin.runtime.worker import DurableWorker
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentCapture, ContentSource
from powercontext.sources import Source, SourceRef


class _TaskOutcomePipeline:
    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        return tuple(
            ExperienceCandidateInput(
                proposal=ExperienceContent(
                    situation="A durable worker processed one bounded Source window.",
                    action="Incubate every captured fact into a reviewed proposal.",
                    outcome=source.content,
                    lesson="Keep the window bounded so each commit stays fenced.",
                ),
                sources=(SourceRef(source_type="content", source_id=source.name),),
            )
            for source in sources
            if isinstance(source, ContentSource)
        )


def test_manual_and_scheduled_discovery_join_one_window_and_commit_once(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'durable.db'}"),
        )
        async with open_builtin_contexts(config, experience_pipeline=_TaskOutcomePipeline()) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Durable work", summary="Joined window commit", idempotency_key="join")
            )
            context = await contexts.get(scope.scope_id)
            await context.sources.capture(ContentCapture(source_id="one", content="First durable fact."))
            await context.sources.capture(ContentCapture(source_id="two", content="Second durable fact."))

            manual_spec = await experience_work_spec(
                contexts, scope.scope_id, limit=100, max_attempts=5, payload_version=1
            )
            scheduled_spec = await experience_work_spec(
                contexts, scope.scope_id, limit=100, max_attempts=5, payload_version=1
            )
            assert manual_spec is not None and scheduled_spec is not None
            assert manual_spec.logical_key == scheduled_spec.logical_key

            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                manual = await repository.enqueue(connection, manual_spec)
                scheduled = await repository.enqueue(connection, scheduled_spec)
            assert isinstance(manual, EnqueueResult) and isinstance(scheduled, EnqueueResult)
            assert manual.created is True
            assert scheduled.created is False
            assert manual.work.work_id == scheduled.work.work_id

            worker = DurableWorker(
                database=contexts.database,
                worker_id="worker-a",
                handlers=(ExperienceWorkHandler(contexts),),
                config=WorkerConfig(concurrency=1),
            )
            assert await worker.run_once() == 1

            async with contexts.database.transaction() as connection:
                stored = await repository.get(connection, manual.work.work_id)
            assert stored.status is WorkStatus.SUCCEEDED
            assert stored.result_payload is not None
            assert stored.result_payload["current_cursor"] == 2
            assert stored.result_payload["candidate_count"] == 2

    asyncio.run(scenario())


def test_stale_discovery_snapshot_retires_instead_of_blocking_the_lane(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'stale-window.db'}"),
        )
        async with open_builtin_contexts(config, experience_pipeline=_TaskOutcomePipeline()) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Durable work", summary="Stale snapshot retirement", idempotency_key="stale")
            )
            context = await contexts.get(scope.scope_id)
            await context.sources.capture(ContentCapture(source_id="one", content="First durable fact."))
            await context.sources.capture(ContentCapture(source_id="two", content="Second durable fact."))

            # A scheduled discovery snapshot taken before the manual window exists.
            stale = await experience_work_spec(contexts, scope.scope_id, limit=2, max_attempts=5, payload_version=1)
            assert stale is not None

            # The manual window 0 -> 1 wins the race and commits first.
            manual = await experience_work_spec(contexts, scope.scope_id, limit=1, max_attempts=5, payload_version=1)
            assert manual is not None
            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                await repository.enqueue(connection, manual)
            worker = DurableWorker(
                database=contexts.database,
                worker_id="worker-a",
                handlers=(ExperienceWorkHandler(contexts),),
                config=WorkerConfig(concurrency=1),
            )
            assert await worker.run_once() == 1

            # The stale snapshot is enqueued only after that commit released the key.
            async with contexts.database.transaction() as connection:
                stale_enqueued = await repository.enqueue(connection, stale)
            assert stale_enqueued.created is True

            assert await worker.run_once() == 1
            async with contexts.database.transaction() as connection:
                stored = await repository.get(connection, stale_enqueued.work.work_id)
            assert stored.status is WorkStatus.SUCCEEDED
            assert stored.result_code == "superseded"

            # The lane kept moving: discovery covers the remainder and both facts land.
            remainder = await experience_work_spec(contexts, scope.scope_id, limit=2, max_attempts=5, payload_version=1)
            assert remainder is not None
            async with contexts.database.transaction() as connection:
                remainder_enqueued = await repository.enqueue(connection, remainder)
            assert remainder_enqueued.created is True
            assert await worker.run_once() == 1
            async with contexts.database.transaction() as connection:
                stored = await repository.get(connection, remainder_enqueued.work.work_id)
                cursor_state = await SourceCursorRepository().load(
                    connection, scope.scope_id, EXPERIENCE_INCUBATION_CURSOR_NAME
                )
            assert stored.status is WorkStatus.SUCCEEDED
            assert stored.result_code == "processed"
            assert cursor_state is not None and cursor_state.cursor.sequence == 2

    asyncio.run(scenario())
