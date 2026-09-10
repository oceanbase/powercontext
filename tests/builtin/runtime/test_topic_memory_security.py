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

"""Topic publication, Server ownership, and accepted request completion are atomic."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING, TopicMemoryContent
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryGlobalOutput,
    TopicMemoryProbe,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
)
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    BUILTIN_TABLES,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE,
    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
    TOPIC_MEMORY_WORK_BUDGETS_TABLE,
)
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import CompositeTopicMemoryIndex
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.topic_memory_processing import (
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryWindowSelector,
)
from powercontext.builtin.runtime.topic_memory_scope import TopicMemoryScopeProcessor
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.repository import ACCESS_AUDIT_EVENTS_TABLE, ACCESS_OWNERS_TABLE, ACCESS_TABLES
from powercontext.server.processing_security import WorkerSecuritySpec, open_worker_security
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource
from tests.builtin.runtime.test_topic_memory_processing import _stages

BINDING = TOPIC_MEMORY_SOURCE_WINDOW_BINDING
SCOPE = "topic-security-scope"
TOPIC_ID = "owned-topic"


@pytest.mark.parametrize("failure_boundary", ["owner", "acknowledgement"])
def test_real_topic_owner_and_publication_roll_back_then_same_generation_retry_succeeds(tmp_path, failure_boundary):
    async def scenario():
        index = CompositeTopicMemoryIndex(SQLiteTopicMemoryFTSIndex())
        topics = TopicMemoryRepository(index=index)
        sources = SourceRepository(SOURCE_ADAPTERS)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic-security.db'}")
        async with SQLiteProfile.open(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES, *index.tables)) as profile:
            async with profile.database.transaction() as connection:
                await topics.initialize(connection)
                source = await sources.add(
                    connection,
                    SCOPE,
                    NoteSource(
                        name="ownership-source", materialization=SourceMaterialization.CAPTURED, body="Verified topic"
                    ),
                )
                pending = ArtifactProcessingPendingRepository()
                await pending.raise_source(connection, SCOPE, BINDING, source.journal_position)
                await pending.request_flush(connection, SCOPE, BINDING)
                intent = await ArtifactProcessingIntentRepository().request(connection, SCOPE, BINDING)
                term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "topic-owner")
            assignment = ArtifactProcessingWorkAssignment(
                BINDING, SCOPE, "topic-memory", intent.requested_generation, term.fence("single-process"), "worker-1"
            )

            def processor(commit_authorizer):
                stages = _stages(
                    probe=TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="verified", evidence_ids=("evidence-0001",)),)
                    ),
                    global_output=TopicMemoryGlobalOutput(
                        proposals=(
                            TopicMemoryProposal(
                                content=TopicMemoryContent(
                                    title="Verified", summary="Durable topic", detail="Evidence"
                                ),
                                evidence_ids=("evidence-0001",),
                            ),
                        )
                    ),
                )
                return TopicMemoryScopeProcessor(
                    profile.database,
                    TopicMemoryProcessor(
                        database=profile.database,
                        sources=sources,
                        topics=topics,
                        stages=stages,
                        publisher=TopicMemoryAtomicPublisher(
                            profile.database, sources, topics, commit_authorizer=commit_authorizer
                        ),
                        id_factory=lambda: TOPIC_ID,
                    ),
                    TopicMemoryWindowSelector(
                        profile.database, sources, character_token_estimator(), context_window_tokens=100_000
                    ),
                    commit_authorizer=commit_authorizer,
                )

            spec = WorkerSecuritySpec(
                principal=PrincipalRef(type="service", id="topic-worker"), deployment_id="test", static_preset=True
            ).model_dump(mode="json")
            async with open_worker_security(spec, profile.database) as security:
                assert security is not None
                owner_hook_calls = 0

                async def owner_commit(connection, scope_id, operations):
                    nonlocal owner_hook_calls
                    await security.topic_commit(connection, scope_id, operations)
                    owner_hook_calls += 1
                    assert len(operations) == 1 and operations[0].artifact_id == TOPIC_ID
                    assert await connection.scalar(select(func.count()).select_from(ACCESS_OWNERS_TABLE)) == 1
                    assert (
                        await connection.scalar(
                            select(func.count())
                            .select_from(ACCESS_AUDIT_EVENTS_TABLE)
                            .where(ACCESS_AUDIT_EVENTS_TABLE.c.reason_code == "artifact-owner-established")
                        )
                        == 1
                    )
                    if failure_boundary == "owner":
                        raise OSError("injected after real Topic owner hook")  # noqa: TRY003

                first = processor(owner_commit)
                finish_target = first._finish_target

                async def fail_after_ack(connection, work, target):
                    await finish_target(connection, work, target)
                    acknowledged = await ArtifactProcessingIntentRepository().load(connection, SCOPE, BINDING)
                    cursor = await SourceCursorRepository().load(connection, SCOPE, BINDING)
                    assert acknowledged is not None and cursor is not None
                    assert acknowledged.handled_generation == assignment.claimed_request_generation
                    assert cursor.cursor.sequence == source.journal_position
                    assert await connection.scalar(select(func.count()).select_from(ARTIFACT_HEADS_TABLE)) == 1
                    assert await connection.scalar(select(func.count()).select_from(ACCESS_OWNERS_TABLE)) == 1
                    raise OSError("injected after real Topic owner hook and acknowledgement")  # noqa: TRY003

                if failure_boundary == "acknowledgement":
                    first._finish_target = fail_after_ack
                with pytest.raises(OSError, match="after real Topic owner hook"):
                    await first.process(assignment)
                assert owner_hook_calls == 1
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, SCOPE, BINDING)
                    intent = await ArtifactProcessingIntentRepository().load(connection, SCOPE, BINDING)
                    pending = await ArtifactProcessingPendingRepository().load(connection, SCOPE, BINDING)
                    assert cursor is None
                    assert intent is not None and pending is not None
                    assert intent.handled_generation == 0 and intent.clean_generation == 0
                    assert pending.handled_flush_generation == 0 and pending.flush_generation == 1
                    target = (await connection.execute(select(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE))).mappings().one()
                    assert target["target_request_generation"] == assignment.claimed_request_generation
                    assert target["source_through"] == source.journal_position
                    for table in (
                        ARTIFACT_HEADS_TABLE,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
                        TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
                        ACCESS_OWNERS_TABLE,
                        ACCESS_AUDIT_EVENTS_TABLE,
                    ):
                        assert await connection.scalar(select(func.count()).select_from(table)) == 0
                    budget_before = (await connection.execute(select(TOPIC_MEMORY_WORK_BUDGETS_TABLE))).mappings().one()
                    assert budget_before["attempts"] == 1 and budget_before["requests"] > 0

                async def retry_commit(connection, scope_id, operations):
                    await security.topic_commit(connection, scope_id, operations)
                    budget = (await connection.execute(select(TOPIC_MEMORY_WORK_BUDGETS_TABLE))).mappings().one()
                    assert budget["attempts"] == 2 and budget["requests"] > budget_before["requests"]

                retry = processor(retry_commit)
                assert (await retry.process(assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
                # Replaying the same acknowledged G must not invoke the exhausted deterministic generators.
                assert (await retry.process(assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, SCOPE, BINDING)
                    intent = await ArtifactProcessingIntentRepository().load(connection, SCOPE, BINDING)
                    assert cursor is not None and intent is not None
                    assert cursor.cursor.sequence == source.journal_position
                    assert intent.handled_generation == assignment.claimed_request_generation
                    assert intent.clean_generation == intent.dirty_generation
                    assert await ArtifactProcessingPendingRepository().load(connection, SCOPE, BINDING) is None
                    assert (
                        await connection.scalar(select(func.count()).select_from(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE))
                        == 0
                    )
                    owner = (await connection.execute(select(ACCESS_OWNERS_TABLE))).mappings().one()
                    assert (owner["owner_kind"], owner["family"], owner["artifact_id"]) == (
                        "artifact",
                        "topic-memory",
                        TOPIC_ID,
                    )
                    assert (owner["owner_type"], owner["owner_id"]) == ("service", "topic-worker")
                    assert await connection.scalar(select(func.count()).select_from(ARTIFACT_HEADS_TABLE)) == 1
                    assert (
                        await connection.scalar(select(func.count()).select_from(TOPIC_MEMORY_WORK_BUDGETS_TABLE)) == 0
                    )

    asyncio.run(scenario())
