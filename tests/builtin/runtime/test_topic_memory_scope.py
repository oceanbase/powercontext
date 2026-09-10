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
from dataclasses import replace

import pytest
from sqlalchemy import select, update

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryProbeOutput
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE,
    TOPIC_MEMORY_WORK_BUDGETS_TABLE,
)
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.topic_memory_processing import (
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowSelector,
)
from powercontext.builtin.runtime.topic_memory_scope import TopicMemoryScopeProcessor
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import NoteSource
from tests.builtin.runtime.test_topic_memory_processing import _repositories

BINDING = TOPIC_MEMORY_SOURCE_WINDOW_BINDING


class _Probe:
    def __init__(self, before=None):
        self.before = before
        self.inputs = []

    async def generate(self, value, /):
        self.inputs.append(value)
        if self.before is not None:
            await self.before(len(self.inputs))
        return GenerationResult(output=TopicMemoryProbeOutput(probes=()))


class _OneSourceSelector(TopicMemoryWindowSelector):
    async def select(self, scope_id, source_after, source_ceiling, /):
        return await super().select(scope_id, source_after, min(source_after + 1, source_ceiling))


def _processor(profile, sources, topics, probe, *, commit_authorizer=None, intents=None):
    stages = TopicMemoryStageSet(
        probe=probe,
        global_evolver=probe,
        planner=probe,
        evolver=probe,
        temporary=probe,
        reconciler=probe,
        estimator=character_token_estimator(),
        input_tokens_limit=100_000,
    )
    domain = TopicMemoryProcessor(
        database=profile.database,
        sources=sources,
        topics=topics,
        stages=stages,
        publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, commit_authorizer=commit_authorizer),
    )
    return TopicMemoryScopeProcessor(
        profile.database,
        domain,
        _OneSourceSelector(profile.database, sources, character_token_estimator(), context_window_tokens=100_000),
        commit_authorizer=commit_authorizer,
        intents=intents,
    )


async def _capture(profile, sources, number, *, flush=True):
    intents = ArtifactProcessingIntentRepository()
    async with profile.database.transaction() as connection:
        source = await sources.add(
            connection,
            "scope-a",
            NoteSource(name=f"note-{number}", materialization=SourceMaterialization.CAPTURED, body=f"source {number}"),
        )
        pending = ArtifactProcessingPendingRepository()
        await pending.raise_source(connection, "scope-a", BINDING, source.journal_position)
        if flush:
            await pending.request_flush(connection, "scope-a", BINDING)
            return await intents.request(connection, "scope-a", BINDING)
        return await intents.load(connection, "scope-a", BINDING)


async def _assignment(profile, generation=1):
    async with profile.database.transaction() as connection:
        term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "holder")
    return ArtifactProcessingWorkAssignment(
        binding_name=BINDING,
        scope_id="scope-a",
        artifact_family="topic-memory",
        claimed_request_generation=generation,
        fence=term.fence("single-process"),
        worker_id="worker-a",
    )


async def _state(profile):
    async with profile.database.transaction() as connection:
        intent = await ArtifactProcessingIntentRepository().load(connection, "scope-a", BINDING)
        cursor = await SourceCursorRepository().load(connection, "scope-a", BINDING)
        pending = await ArtifactProcessingPendingRepository().load(connection, "scope-a", BINDING)
        targets = (await connection.execute(select(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE))).mappings().all()
    return intent, 0 if cursor is None else cursor.cursor.sequence, pending, targets


def test_snapshot_reads_current_input_but_acknowledges_only_claimed_generation():
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            await _capture(profile, sources, 1)
            assignment = await _assignment(profile)
            await _capture(profile, sources, 2)
            probe = _Probe()
            processor = _processor(profile, sources, topics, probe)
            result = await processor.process(assignment)
            assert result.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            intent, cursor, pending, targets = await _state(profile)
            assert intent.requested_generation == 2
            assert intent.handled_generation == 1
            assert intent.clean_generation == intent.dirty_generation == 2
            assert cursor == 2 and pending is None and not targets

            await _capture(profile, sources, 3)
            await processor.process(assignment)
            assert len(probe.inputs) == 2
            assert (await _state(profile))[1] == 2
            await processor.process(replace(assignment, claimed_request_generation=2))
            intent, cursor, _, targets = await _state(profile)
            assert cursor == 3 and intent.handled_generation == 2 and intent.requested_generation == 3
            assert not targets
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_crash_after_partial_commit_retains_old_target_before_new_invocation():
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            await _capture(profile, sources, 1, flush=False)
            await _capture(profile, sources, 2)
            assignment = await _assignment(profile)

            async def crash_second(count):
                if count == 2:
                    raise RuntimeError("worker crashed")  # noqa: TRY003

            with pytest.raises(RuntimeError, match="worker crashed"):
                await _processor(profile, sources, topics, _Probe(crash_second)).process(assignment)
            intent, cursor, pending, targets = await _state(profile)
            assert cursor == 1 and intent.handled_generation == 0
            assert pending.handled_flush_generation == 0
            assert targets[0]["source_through"] == 2 and targets[0]["target_request_generation"] == 1
            await _capture(profile, sources, 3)
            observed = []

            async def observe(_count):
                observed.append(await _state(profile))

            result = await _processor(profile, sources, topics, _Probe(observe)).process(
                replace(assignment, claimed_request_generation=2)
            )
            assert result.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            assert observed[0][3][0]["source_through"] == 2
            assert observed[1][0].handled_generation == 1
            assert observed[1][3][0]["source_through"] == 3
            assert observed[1][3][0]["target_request_generation"] == 2
            intent, cursor, pending, targets = await _state(profile)
            assert cursor == 3 and intent.handled_generation == intent.requested_generation == 2
            assert pending is None and not targets
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_late_source_and_flush_are_not_consumed_by_active_target():
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            await _capture(profile, sources, 1)
            assignment = await _assignment(profile)

            async def append_during_generation(_count):
                await _capture(profile, sources, 2)

            probe = _Probe(append_during_generation)
            result = await _processor(profile, sources, topics, probe).process(assignment)
            assert result.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            intent, cursor, pending, targets = await _state(profile)
            assert cursor == 1 and intent.handled_generation == 1 and intent.requested_generation == 2
            assert pending.flush_generation == 2 and pending.handled_flush_generation == 1
            assert intent.dirty_generation > intent.clean_generation and not targets
            replay = _Probe()
            await _processor(profile, sources, topics, replay).process(assignment)
            assert not replay.inputs
            await _processor(profile, sources, topics, replay).process(
                replace(assignment, claimed_request_generation=2)
            )
            assert (await _state(profile))[1] == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_target_compare_and_swap_failure_rolls_back_cursor_and_acknowledgement():
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            await _capture(profile, sources, 1)
            assignment = await _assignment(profile)

            async def replace_target(_count):
                async with profile.database.transaction() as connection:
                    await ArtifactProcessingIntentRepository().request(connection, "scope-a", BINDING)
                    await connection.execute(
                        update(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE).values(target_request_generation=2)
                    )

            result = await _processor(profile, sources, topics, _Probe(replace_target)).process(assignment)
            assert result.outcome is ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT
            intent, cursor, pending, targets = await _state(profile)
            assert cursor == 0 and intent.handled_generation == 0 and pending.handled_flush_generation == 0
            assert targets[0]["target_request_generation"] == 2
            async with profile.database.transaction() as connection:
                budgets = (await connection.execute(select(TOPIC_MEMORY_WORK_BUDGETS_TABLE))).mappings().all()
            # Provider reservations remain charged after publication rollback.
            assert len(budgets) == 1 and budgets[0]["requests"] > 0
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_source_capture_waits_for_frozen_worker_snapshot(tmp_path):
    async def scenario():
        manager, profile, sources, topics = await _repositories(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'freeze-race.db'}")
        )
        freeze_started = asyncio.Event()
        finish_freeze = asyncio.Event()
        capture_started = asyncio.Event()

        class PausedIntents(ArtifactProcessingIntentRepository):
            async def load(self, connection, scope_id, binding_name, /, *, for_update=False):
                result = await super().load(connection, scope_id, binding_name, for_update=for_update)
                if for_update and not freeze_started.is_set():
                    freeze_started.set()
                    await finish_freeze.wait()
                return result

        try:
            await _capture(profile, sources, 1)
            assignment = await _assignment(profile)

            async def capture():
                capture_started.set()
                await _capture(profile, sources, 2)

            async def wait_for_capture(_count):
                await capture_task

            probe = _Probe(wait_for_capture)
            processor = _processor(profile, sources, topics, probe, intents=PausedIntents())
            worker = asyncio.create_task(processor.process(assignment))
            await asyncio.wait_for(freeze_started.wait(), 5)
            capture_task = asyncio.create_task(capture())
            await asyncio.wait_for(capture_started.wait(), 5)
            finish_freeze.set()
            result = await asyncio.wait_for(worker, 10)
            await capture_task
            assert result.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            intent, cursor, pending, targets = await _state(profile)
            assert len(probe.inputs) == 1 and cursor == 1
            assert intent.handled_generation == 1 and intent.requested_generation == 2
            assert pending.handled_flush_generation == 1 and pending.flush_generation == 2 and not targets
        finally:
            finish_freeze.set()
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [False, True])
def test_authorization_failure_preserves_target_and_unacknowledged_work(empty):
    async def scenario():
        manager, profile, sources, topics = await _repositories()
        try:
            if empty:
                async with profile.database.transaction() as connection:
                    await ArtifactProcessingIntentRepository().request(connection, "scope-a", BINDING)
            else:
                await _capture(profile, sources, 1)
            assignment = await _assignment(profile)

            async def deny(_connection, _scope_id, _operations):
                raise PermissionError("access revoked")  # noqa: TRY003

            with pytest.raises(PermissionError, match="access revoked"):
                await _processor(profile, sources, topics, _Probe(), commit_authorizer=deny).process(assignment)
            intent, cursor, _, targets = await _state(profile)
            assert cursor == 0 and intent.handled_generation == 0 and targets
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
