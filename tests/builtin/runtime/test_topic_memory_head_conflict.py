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

"""A Topic head moved by a concurrent writer is reread and recomputed, not counted as a failed attempt."""

from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy import select

from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryGenerationError,
    TopicMemoryGlobalOutput,
    TopicMemoryProbe,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BLOCKS_TABLE, TOPIC_MEMORY_WORK_BUDGETS_TABLE
from powercontext.builtin.persistence.topic_memory_budget import (
    MAX_TOPIC_MEMORY_WORK_ATTEMPTS,
    MAX_TOPIC_MEMORY_WORK_TOKENS,
    exhausted_reason,
)
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
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
from tests.builtin.runtime.test_topic_memory_processing import (
    _assignment,
    _content,
    _ProcessorLauncher,
    _QueueGenerator,
    _RepeatingGenerator,
    _repositories,
)

_UPDATED = _content("updated", "zircon")


class _ConcurrentWriterStage:
    """The global stage; each scripted step either returns, fails, or lets a concurrent writer move the head first."""

    def __init__(self, database, topics, steps) -> None:
        self.database = database
        self.topics = topics
        self.steps = list(steps)
        self.calls = 0
        self.output = TopicMemoryGlobalOutput(
            proposals=(
                TopicMemoryProposal(candidate_id="candidate-0001", content=_UPDATED, evidence_ids=("evidence-0001",)),
            )
        )

    async def generate(self, _value, /):
        self.calls += 1
        step = self.steps.pop(0) if self.steps else "publish"
        if step == "fail":
            raise RuntimeError("provider unavailable")  # noqa: TRY003
        if step == "move-head":
            # A manual replace or Dream lands while the Worker is generating.
            async with self.database.transaction() as connection:
                (current,) = await self.topics.browse_current(connection, "scope-a", limit=10)
                head = await self.topics.get_exact(connection, "scope-a", current.artifact_ref)
                content = _content(f"replaced {self.calls}", "zircon")
                await self.topics.publish_revision(
                    connection,
                    "scope-a",
                    head.topic,
                    TopicMemoryDraft(content=content, artifacts=(head.topic.as_ref(),)),
                    prepare_topic_memory_projection(content),
                )
        return GenerationResult(output=self.output)


async def _setup(steps, *, config=None, limit=100_000):
    manager, profile, sources, topics = await _repositories(config)
    async with profile.database.transaction() as connection:
        await topics.publish_create(
            connection,
            "scope-a",
            "topic-history",
            TopicMemoryDraft(content=_content("legacy", "zircon")),
            prepare_topic_memory_projection(_content("legacy", "zircon")),
        )
        source = await sources.add(
            connection,
            "scope-a",
            NoteSource(name="note-1", materialization=SourceMaterialization.CAPTURED, body="updated zircon"),
        )
    stage = _ConcurrentWriterStage(profile.database, topics, steps)
    processor = TopicMemoryProcessor(
        database=profile.database,
        sources=sources,
        topics=topics,
        stages=TopicMemoryStageSet(
            probe=_RepeatingGenerator(
                TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),))
            ),
            global_evolver=stage,
            planner=_QueueGenerator(),
            evolver=_QueueGenerator(),
            temporary=_QueueGenerator(),
            reconciler=_QueueGenerator(),
            estimator=character_token_estimator(),
            input_tokens_limit=limit,
        ),
        publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
    )
    return manager, profile, sources, topics, stage, processor, source


async def _budget(profile):
    async with profile.database.transaction() as connection:
        row = (await connection.execute(select(TOPIC_MEMORY_WORK_BUDGETS_TABLE))).mappings().one_or_none()
    return None if row is None else dict(row)


async def _fence(profile):
    async with profile.database.transaction() as connection:
        term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "holder")
    return term.fence("single-process")


async def _assert_published_on_moved_head(profile, topics, moves: int) -> None:
    async with profile.database.transaction() as connection:
        (current,) = await topics.browse_current(connection, "scope-a", limit=10)
        published = await topics.get_exact(connection, "scope-a", current.artifact_ref)
        cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
    # Revision 1 is the original, then one revision per concurrent write, then the Worker's.
    assert current.artifact_ref.revision == moves + 2
    assert published.topic.content == _UPDATED
    assert published.topic.lineage.artifacts == (current.artifact_ref.model_copy(update={"revision": moves + 1}),)
    assert cursor is not None and cursor.cursor.sequence == 1


def test_repeated_head_conflicts_recompute_on_the_new_head_without_spending_attempts() -> None:
    moves = MAX_TOPIC_MEMORY_WORK_ATTEMPTS + 2

    async def scenario() -> None:
        manager, profile, _, topics, stage, processor, _ = await _setup(["move-head"] * moves)
        try:
            assignment = _assignment(await _fence(profile))
            charged = []
            for _ in range(moves):
                completion = await processor.process(assignment)
                assert completion.outcome is ArtifactProcessingWorkerOutcome.HEAD_CONFLICT
                row = await _budget(profile)
                assert row is not None
                # The cause is kept on the frontier, and it does not count toward the attempt ceiling.
                assert row["last_error_code"] == "head_conflict"
                assert row["attempts"] == 1
                assert exhausted_reason(row) == ""
                charged.append(row["requests"])
            # Every recomputation is charged to the durable provider ceiling; nothing is refunded.
            assert charged == sorted(set(charged))

            assert (await processor.process(assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            assert await _budget(profile) is None
            assert stage.calls == moves + 1
            await _assert_published_on_moved_head(profile, topics, moves)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("steps", "exhausted"),
    [
        # Three real failures exhaust the frontier, however many head conflicts separate them.
        (["move-head", "fail", "move-head", "fail", "move-head", "fail"], True),
        # A head conflict on the last remaining attempt hands that attempt back.
        (["fail", "fail", "move-head"], False),
    ],
)
def test_real_failures_still_spend_attempts_between_head_conflicts(steps, exhausted) -> None:
    async def scenario() -> None:
        manager, profile, _, topics, stage, processor, _ = await _setup(steps)
        try:
            assignment = _assignment(await _fence(profile))
            for step in steps:
                if step == "fail":
                    with pytest.raises(RuntimeError, match="provider unavailable"):
                        await processor.process(assignment)
                else:
                    completion = await processor.process(assignment)
                    assert completion.outcome is ArtifactProcessingWorkerOutcome.HEAD_CONFLICT
            row = await _budget(profile)
            assert row is not None
            assert row["attempts"] == MAX_TOPIC_MEMORY_WORK_ATTEMPTS
            calls = stage.calls
            if exhausted:
                with pytest.raises(TopicMemoryGenerationError, match="window_attempt_limit"):
                    await processor.process(assignment)
                assert stage.calls == calls
            else:
                assert (await processor.process(assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
                await _assert_published_on_moved_head(profile, topics, steps.count("move-head"))
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_a_head_that_never_settles_is_stopped_by_the_frontier_provider_ceiling() -> None:
    # Large stage reservations make the durable token ceiling reachable in a few recomputations.
    limit = MAX_TOPIC_MEMORY_WORK_TOKENS // 10
    per_attempt = 2 * 2 * (limit + limit // 4)  # probe + global, each reserving two requests of capacity

    async def scenario() -> None:
        manager, profile, _, _, stage, processor, _ = await _setup(["move-head"] * 1_000, limit=limit)
        try:
            assignment = _assignment(await _fence(profile))
            conflicts = 0
            with pytest.raises(TopicMemoryGenerationError) as error:
                while True:
                    completion = await processor.process(assignment)
                    assert completion.outcome is ArtifactProcessingWorkerOutcome.HEAD_CONFLICT
                    conflicts += 1
            assert error.value.code == "window_provider_budget_exceeded"
            assert 0 < conflicts <= MAX_TOPIC_MEMORY_WORK_TOKENS // per_attempt
            assert stage.calls == conflicts
            row = await _budget(profile)
            assert row is not None and row["attempts"] == 1
            assert exhausted_reason(row) == "window_provider_budget_exceeded"
            # The ceiling stopped the next attempt before it began, so the conflict stays the visible cause.
            assert row["last_error_code"] == "head_conflict"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_supervised_scope_rereads_moved_heads_and_publishes_without_a_block(tmp_path, caplog) -> None:
    moves = MAX_TOPIC_MEMORY_WORK_ATTEMPTS + 1

    async def scenario() -> None:
        manager, profile, sources, topics, _, processor, source = await _setup(
            ["move-head"] * moves, config=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic.db'}")
        )
        try:
            pending = ArtifactProcessingPendingRepository()
            async with profile.database.transaction() as connection:
                await pending.raise_source(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING, source.journal_position
                )
                await pending.request_flush(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                await ArtifactProcessingIntentRepository().request(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                )
            binding = ArtifactProcessingBinding(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                artifact_family="topic-memory",
                max_workers=1,
                worker_timeout_seconds=5,
                launcher=_ProcessorLauncher(
                    TopicMemoryScopeProcessor(
                        profile.database,
                        processor,
                        TopicMemoryWindowSelector(
                            profile.database, sources, character_token_estimator(), context_window_tokens=10_000
                        ),
                    )
                ),
            )
            with caplog.at_level(logging.INFO, logger="powercontext.builtin.runtime.artifact_processing"):
                async with ArtifactProcessingSupervisor(
                    database=profile.database,
                    bindings=(binding,),
                    lease_mode="single-process",
                    retry_base_seconds=0.01,
                    retry_cap_seconds=0.01,
                    retry_jitter=lambda: 1.0,
                ):
                    async with asyncio.timeout(30):
                        while True:
                            async with profile.database.transaction() as connection:
                                row = await pending.load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                            if row is None:
                                break
                            await asyncio.sleep(0.01)
            await _assert_published_on_moved_head(profile, topics, moves)
            async with profile.database.transaction() as connection:
                assert (await connection.execute(select(TOPIC_MEMORY_WORK_BLOCKS_TABLE))).first() is None
            attempts = [
                record
                for record in caplog.records
                if record.__dict__.get("event") == "artifact_processing.attempt.completed"
            ]
            conflicts = [record for record in attempts if record.__dict__.get("outcome") == "conflict"]
            assert len(conflicts) == moves
            assert {record.__dict__.get("error_code") for record in conflicts} == {"head_conflict"}
            assert not [r for r in caplog.records if r.__dict__.get("event") == "artifact_processing.blocked"]
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
