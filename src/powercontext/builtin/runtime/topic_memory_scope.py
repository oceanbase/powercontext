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

"""Topic-owned finite input snapshots behind generic Scope invocations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryGenerationError
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError, GenerationConflictError
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE, TOPIC_MEMORY_PROCESSING_TARGETS_TABLE
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.topic_memory_processing import (
    MAX_TOPIC_MEMORY_WINDOW_SOURCES,
    ArtifactProcessingWaveKind,
    TopicMemoryCommitAuthorizer,
    TopicMemoryProcessor,
    TopicMemoryWindowAssignment,
    TopicMemoryWindowSelector,
)


@dataclass(frozen=True, slots=True)
class TopicMemoryProcessingTarget:
    """One immutable target retained through partial commits and crashes."""

    target_request_generation: int
    source_through: int
    captured_flush_generation: int
    observed_dirty_generation: int


class TopicMemoryScopeProcessor:
    """Finish an existing target, then at most one newly claimed snapshot.

    Request generation limits acknowledgment, not input reads. A snapshot is
    frozen in the Worker, so all sources committed before that transaction may
    be included even when a newer invocation was accepted meanwhile.
    """

    def __init__(
        self,
        database: AsyncDatabase,
        processor: TopicMemoryProcessor,
        selector: TopicMemoryWindowSelector,
        *,
        cursors: SourceCursorRepository | None = None,
        leases: ArtifactProcessingLeaseRepository | None = None,
        intents: ArtifactProcessingIntentRepository | None = None,
        pending: ArtifactProcessingPendingRepository | None = None,
        commit_authorizer: TopicMemoryCommitAuthorizer | None = None,
        source_window_limit: int = MAX_TOPIC_MEMORY_WINDOW_SOURCES,
    ) -> None:
        if type(source_window_limit) is not int or source_window_limit < 1:
            raise TopicMemoryGenerationError("invalid_window")
        self._database = database
        self._processor = processor
        self._selector = selector
        self._cursors = SourceCursorRepository() if cursors is None else cursors
        self._leases = ArtifactProcessingLeaseRepository() if leases is None else leases
        self._intents = ArtifactProcessingIntentRepository() if intents is None else intents
        self._pending = ArtifactProcessingPendingRepository() if pending is None else pending
        self._commit_authorizer = commit_authorizer
        self._source_window_limit = min(source_window_limit, MAX_TOPIC_MEMORY_WINDOW_SOURCES)

    async def process(self, assignment: ArtifactProcessingWorkAssignment, /) -> ArtifactProcessingWorkerCompletion:
        if (
            assignment.binding_name != TOPIC_MEMORY_SOURCE_WINDOW_BINDING
            or assignment.artifact_family != "topic-memory"
            or assignment.claimed_request_generation < 1
            or assignment.fence.supervisor_group not in {"global", "artifact:topic-memory"}
        ):
            raise TopicMemoryGenerationError("invalid_invocation")
        try:
            # An old G0 target may survive while the dispatcher now claims G1.
            # Retire G0 atomically before freezing G1; never extend an active H.
            for _ in range(2):
                target = await self._target(assignment)
                if target is None:
                    return ArtifactProcessingWorkerCompletion()
                result = await self._process_target(assignment, target)
                if result.outcome is not ArtifactProcessingWorkerOutcome.SUCCEEDED:
                    return result
                if target.target_request_generation == assignment.claimed_request_generation:
                    return result
        except ArtifactProcessingLeadershipLostError:
            return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST)
        except GenerationConflictError:
            return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
        raise TopicMemoryGenerationError("target_did_not_converge")

    async def _target(self, assignment: ArtifactProcessingWorkAssignment) -> TopicMemoryProcessingTarget | None:
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, assignment.fence)
            # Lock the Source head before the intent, matching capture's order.
            # SQLite's no-op UPDATE also establishes a real write transaction.
            await self._lock_source_head(connection, assignment.scope_id)
            intent = await self._intents.load(connection, assignment.scope_id, assignment.binding_name, for_update=True)
            if intent is None or assignment.claimed_request_generation > intent.requested_generation:
                raise TopicMemoryGenerationError("unaccepted_invocation")
            # A replay must not create a fresh target or read new input.
            if intent.handled_generation >= assignment.claimed_request_generation:
                return None
            table = TOPIC_MEMORY_PROCESSING_TARGETS_TABLE
            row = (
                (
                    await connection.execute(
                        select(table)
                        .where(table.c.scope_id == assignment.scope_id, table.c.binding_name == assignment.binding_name)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is not None:
                target = TopicMemoryProcessingTarget(
                    target_request_generation=int(row["target_request_generation"]),
                    source_through=int(row["source_through"]),
                    captured_flush_generation=int(row["captured_flush_generation"]),
                    observed_dirty_generation=int(row["observed_dirty_generation"]),
                )
                if target.target_request_generation > assignment.claimed_request_generation:
                    raise GenerationConflictError(
                        assignment.binding_name, assignment.claimed_request_generation, target.target_request_generation
                    )
                return target
            head = await self._head(connection, assignment.scope_id)
            pending = await self._pending.load(
                connection, assignment.scope_id, assignment.binding_name, for_update=True
            )
            target = TopicMemoryProcessingTarget(
                target_request_generation=assignment.claimed_request_generation,
                source_through=head,
                captured_flush_generation=0 if pending is None else pending.flush_generation,
                observed_dirty_generation=intent.dirty_generation,
            )
            await connection.execute(
                insert(table).values(
                    scope_id=assignment.scope_id,
                    binding_name=assignment.binding_name,
                    target_request_generation=target.target_request_generation,
                    source_through=target.source_through,
                    captured_flush_generation=target.captured_flush_generation,
                    observed_dirty_generation=target.observed_dirty_generation,
                )
            )
            return target

    async def _process_target(
        self, assignment: ArtifactProcessingWorkAssignment, target: TopicMemoryProcessingTarget
    ) -> ArtifactProcessingWorkerCompletion:
        while True:
            async with self._database.transaction() as connection:
                await self._leases.require_fence(connection, assignment.fence)
                cursor = await self._cursors.load(connection, assignment.scope_id, assignment.binding_name)
                source_after = 0 if cursor is None else cursor.cursor.sequence
                if source_after >= target.source_through:
                    await self._lock_source_head(connection, assignment.scope_id)
                    if self._commit_authorizer is not None:
                        await self._commit_authorizer(connection, assignment.scope_id, ())
                    await self._finish_target(connection, assignment, target)
                    await self._leases.require_fence(connection, assignment.fence)
                    return ArtifactProcessingWorkerCompletion()
            ceiling = min(target.source_through, source_after + self._source_window_limit)
            through = await self._selector.select(assignment.scope_id, source_after, ceiling)
            if not isinstance(through, int) or isinstance(through, bool) or not source_after < through <= ceiling:
                raise TopicMemoryGenerationError("invalid_window")
            window = TopicMemoryWindowAssignment(
                binding_name=assignment.binding_name,
                scope_id=assignment.scope_id,
                source_after=source_after,
                source_through=through,
                wave_target=target.source_through,
                claimed_flush_generation=target.captured_flush_generation,
                cursor_generation=None if cursor is None else cursor.generation,
                wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
                fence=assignment.fence,
                worker_id=assignment.worker_id,
            )

            async def commit(connection: AsyncConnection, final: bool = through == target.source_through) -> None:
                if final:
                    await self._finish_target(connection, assignment, target)
                else:
                    await self._require_target(connection, assignment, target)

            completion = await self._processor.process(window, commit_hook=commit)
            if completion.outcome is not ArtifactProcessingWorkerOutcome.SUCCEEDED or through == target.source_through:
                return completion

    async def _finish_target(
        self,
        connection: AsyncConnection,
        assignment: ArtifactProcessingWorkAssignment,
        target: TopicMemoryProcessingTarget,
    ) -> None:
        await self._require_target(connection, assignment, target)
        cursor = await self._cursors.load(connection, assignment.scope_id, assignment.binding_name, for_update=True)
        position = 0 if cursor is None else cursor.cursor.sequence
        if position < target.source_through:
            raise TopicMemoryGenerationError("incomplete_target")
        pending = await self._pending.mark_flush_handled(
            connection, assignment.scope_id, assignment.binding_name, target.captured_flush_generation
        )
        covered = pending is None or (
            pending.source_through <= target.source_through
            and pending.flush_generation <= target.captured_flush_generation
        )
        if target.source_through > 0:
            await self._pending.delete_if_covered(
                connection,
                assignment.scope_id,
                assignment.binding_name,
                cursor=position,
                source_through_limit=target.source_through,
            )
        await self._intents.acknowledge(
            connection,
            assignment.scope_id,
            assignment.binding_name,
            target.target_request_generation,
            clean_generation=target.observed_dirty_generation
            if covered and await self._head(connection, assignment.scope_id) <= target.source_through
            else None,
        )
        result = await connection.execute(
            delete(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE).where(*self._target_key(assignment, target))
        )
        if result.rowcount != 1:
            raise GenerationConflictError(assignment.binding_name, target.target_request_generation, None)

    async def _require_target(
        self,
        connection: AsyncConnection,
        assignment: ArtifactProcessingWorkAssignment,
        target: TopicMemoryProcessingTarget,
    ) -> None:
        current = await connection.scalar(
            select(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE.c.target_request_generation)
            .where(*self._target_key(assignment, target))
            .with_for_update()
        )
        if current is None:
            raise GenerationConflictError(assignment.binding_name, target.target_request_generation, None)

    @staticmethod
    def _target_key(assignment: ArtifactProcessingWorkAssignment, target: TopicMemoryProcessingTarget):
        table = TOPIC_MEMORY_PROCESSING_TARGETS_TABLE
        return (
            table.c.binding_name == assignment.binding_name,
            table.c.scope_id == assignment.scope_id,
            table.c.target_request_generation == target.target_request_generation,
            table.c.source_through == target.source_through,
            table.c.captured_flush_generation == target.captured_flush_generation,
            table.c.observed_dirty_generation == target.observed_dirty_generation,
        )

    @staticmethod
    async def _lock_source_head(connection: AsyncConnection, scope_id: str) -> None:
        await connection.execute(
            update(SOURCE_JOURNAL_HEADS_TABLE)
            .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
            .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
        )

    @staticmethod
    async def _head(connection: AsyncConnection, scope_id: str) -> int:
        # A locking read observes commits made before the lock under MySQL's
        # repeatable-read isolation as well as SQLite's transaction semantics.
        return int(
            await connection.scalar(
                select(SOURCE_JOURNAL_HEADS_TABLE.c.position)
                .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
                .with_for_update()
            )
            or 0
        )


__all__ = ["TopicMemoryProcessingTarget", "TopicMemoryScopeProcessor"]
