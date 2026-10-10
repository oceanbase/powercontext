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

"""Memory timeouts shrink durable windows without consuming failed evidence."""

import asyncio
from uuid import uuid4

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.inference import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.persistence.errors import GenerationConflictError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft


class Pipeline:
    def __init__(self, maximum=1, error=None):
        self.maximum = maximum
        self.error = error
        self.windows = []

    async def extract(self, request):
        self.windows.append(tuple(source.name for source in request.sources))
        if self.error is not None:
            raise self.error
        if len(request.sources) > self.maximum:
            raise InferenceTimeoutError("generate", 60)
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,)) for source in request.sources
        )


async def create_scope(contexts, count):
    scope = (
        await contexts.scopes.create(ScopeDraft(title="Recovery", summary="Recovery", idempotency_key=str(uuid4())))
    ).scope_id
    for index in range(count):
        await contexts.records.create_source(scope, "content", f"Fact {index}")
    return scope


def test_timeout_reduction_survives_reopen_and_resets_after_backlog(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
        pipeline = Pipeline()
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, candidate_pipeline=pipeline)
            scope = await create_scope(contexts, 4)
            context = await contexts.get(scope)
            with pytest.raises(InferenceTimeoutError):
                await context.triggers.flush(limit=100)
            assert (await context.triggers.cursor()).sequence == 0

        # Reopen the database and rebuild the processor, as a new Worker does.
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, candidate_pipeline=pipeline)
            context = await contexts.get(scope)
            with pytest.raises(InferenceTimeoutError):
                await context.triggers.flush(limit=100)
            assert (await context.triggers.cursor()).sequence == 0
            assert len(pipeline.windows[-1]) < len(pipeline.windows[0])
            for position in range(1, 5):
                result = await context.triggers.flush(limit=100)
                assert result.current_cursor == position
                assert result.memory_ref is not None
            assert tuple(item for window in pipeline.windows[2:] for item in window) == pipeline.windows[0]

            pipeline.maximum = 4
            for index in range(4):
                await contexts.records.create_source(scope, "content", f"New fact {index}")
            result = await context.triggers.flush(limit=100)
            assert result.source_count == 4
            assert result.current_cursor == 8

    asyncio.run(scenario())


def test_single_source_timeout_preserves_cursor_and_can_recover():
    async def scenario():
        pipeline = Pipeline(error=InferenceTimeoutError("generate", 60))
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, candidate_pipeline=pipeline)
            scope = await create_scope(contexts, 1)
            context = await contexts.get(scope)
            for _ in range(2):
                with pytest.raises(InferenceTimeoutError):
                    await context.triggers.flush(limit=100)
                assert (await context.triggers.cursor()).sequence == 0
            pipeline.error = None
            assert (await context.triggers.flush(limit=100)).current_cursor == 1
            assert pipeline.windows[0] == pipeline.windows[-1]

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [InferenceTimeoutError("embed", 60), InferenceUnavailableError("generate")])
def test_non_extraction_failures_do_not_reduce_source_window(error):
    async def scenario():
        pipeline = Pipeline(maximum=4, error=error)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, candidate_pipeline=pipeline)
            scope = await create_scope(contexts, 4)
            context = await contexts.get(scope)
            with pytest.raises(type(error)):
                await context.triggers.flush(limit=100)
            pipeline.error = None
            result = await context.triggers.flush(limit=100)
            assert result.current_cursor == 4
            assert pipeline.windows[0] == pipeline.windows[-1]

    asyncio.run(scenario())


def test_stale_timeout_cannot_rewind_concurrently_committed_cursor():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class DelayedTimeout:
            async def extract(self, request):
                entered.set()
                await release.wait()
                raise InferenceTimeoutError("generate", 60)

        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            first = RelationalContexts(database=profile.database, candidate_pipeline=DelayedTimeout())
            second = RelationalContexts(database=profile.database, candidate_pipeline=Pipeline(maximum=4))
            scope = await create_scope(first, 4)
            first_context, second_context = await first.get(scope), await second.get(scope)
            task = asyncio.create_task(first_context.triggers.flush(limit=4))
            await asyncio.wait_for(entered.wait(), timeout=5)
            try:
                assert (await second_context.triggers.flush(limit=2)).current_cursor == 2
            finally:
                release.set()
            with pytest.raises(GenerationConflictError):
                await task
            assert (await second_context.triggers.cursor()).sequence == 2
            assert (await second_context.triggers.flush(limit=4)).current_cursor == 4

    asyncio.run(scenario())


def test_failed_commit_retains_reduction_until_consumption_succeeds():
    async def fail_commit(*_args):
        raise OSError("ownership commit failed")  # noqa: TRY003 - injected transaction failure

    async def scenario():
        pipeline = Pipeline()
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, candidate_pipeline=pipeline)
            scope = await create_scope(contexts, 2)
            context = await contexts.get(scope)
            with pytest.raises(InferenceTimeoutError):
                await context.triggers.flush(limit=100)
            assert (await context.triggers.flush(limit=1)).current_cursor == 1
            with pytest.raises(OSError, match="ownership commit failed"):
                await contexts.process_memory(scope, 100, on_commit=fail_commit)
            assert (await context.triggers.cursor()).sequence == 1
            await contexts.records.create_source(scope, "content", "New input while retrying")
            # The failed final commit must roll back clearing the reduction too.
            assert (await context.triggers.flush(limit=100)).current_cursor == 2
            assert (await context.triggers.flush(limit=100)).current_cursor == 3

    asyncio.run(scenario())
