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

"""Passive, Scope-bound projections of existing processing evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.dream.bindings import DREAM_OPERATIONS, FAMILY_DREAM_BINDINGS
from powercontext.builtin.dream.models import DreamCandidateRef, DreamOperation, DreamRecord, DreamRun, DreamStatus
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import InvalidStoredColumnError, InvalidStoredPayloadError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository, database_utc_now
from powercontext.builtin.persistence.tables import (
    DREAM_RUNS_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE,
    TOPIC_MEMORY_WORK_BLOCKS_TABLE,
)
from powercontext.builtin.persistence.topic_memory_budget import exhausted_reason, load_topic_memory_work_budget
from powercontext.builtin.processing_diagnostics import ProcessingDiagnostics, classify_block_code
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.runtime.processing_observation import project_processing_observation
from powercontext.builtin.scope.errors import ScopeNotFoundError
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics.models import ProcessingObservation

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors


class ProcessingStatusState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProcessingStatusProgress:
    source_cursor: int
    source_head: int
    target_source_through: int | None = None

    @property
    def pending_inputs(self) -> bool:
        return self.source_head > self.source_cursor


@dataclass(frozen=True, slots=True)
class ProcessingStatusBlock:
    error_code: str
    source_after: int
    target_source_through: int
    attempts: int
    requests: int
    tokens: int
    block_id: str | None = None
    blocked_at: datetime | None = None
    source_through: int | None = None
    budget_source_through: int | None = None
    diagnostics: ProcessingDiagnostics | None = None

    @property
    def persisted(self) -> bool:
        return self.block_id is not None


@dataclass(frozen=True, slots=True)
class DreamOperationStatus:
    run_id: str
    operation: DreamOperation
    status: DreamStatus
    outcome: Literal["proposed", "no_change", "needs_evidence"] | None
    accepted_at: datetime
    completed_at: datetime | None
    candidate: DreamCandidateRef | None
    diagnostics: ProcessingDiagnostics | None = None


@dataclass(frozen=True, slots=True)
class ArtifactProcessingStatus:
    family: str
    scope_id: str
    state: ProcessingStatusState
    observed_at: datetime
    source_consumption_applicable: bool
    progress: ProcessingStatusProgress | None = None
    invocation: ProcessingObservation | None = None
    executor: ProcessingObservation | None = None
    block: ProcessingStatusBlock | None = None
    dream_operations: tuple[DreamOperationStatus, ...] = ()
    dream_pending: bool | None = None
    unavailable_reason: Literal["family_not_supported", "dream_query_failed"] | None = None
    evidence_origin: Literal["durable"] = "durable"


_SOURCE_FAMILIES = frozenset({"memory", "experience", "profile", "topic-memory"})


class ProcessingStatusInspector:
    async def status(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        /,
        *,
        supervisors: ArtifactProcessingSupervisors | None = None,
        lease_time_floor: datetime | None = None,
    ) -> ArtifactProcessingStatus:
        scope_id = validate_scope_id(scope_id)
        now = datetime.now(UTC)
        if await ScopeRepository().get(connection, scope_id) is None:
            raise ScopeNotFoundError(scope_id)
        processing_family = "memory" if family == "atomic-memory" else family
        source_family = processing_family in _SOURCE_FAMILIES
        binding = (
            TOPIC_MEMORY_SOURCE_WINDOW_BINDING
            if processing_family == "topic-memory"
            else FAMILY_BINDINGS.get(processing_family)
        )
        if binding is None:
            return ArtifactProcessingStatus(
                family,
                scope_id,
                ProcessingStatusState.UNKNOWN,
                now,
                False,
                unavailable_reason="family_not_supported",
            )
        intent = await ArtifactProcessingIntentRepository().load(connection, scope_id, binding)
        invocation = (
            None if intent is None else project_processing_observation(intent.model_dump(), processing_family, now)
        )
        progress = await self._source_progress(connection, scope_id, binding) if source_family else None
        block = (
            await self._topic_block(connection, scope_id, binding, progress, lease_time_floor)
            if processing_family == "topic-memory" and progress is not None
            else None
        )
        runs: tuple[DreamOperationStatus, ...] = ()
        pending: bool | None = None
        unavailable = None
        try:
            runs, pending = await self._dream_status(connection, scope_id, processing_family)
        except (SQLAlchemyError, InvalidStoredColumnError, InvalidStoredPayloadError):
            unavailable = "dream_query_failed"
        executor = None
        if supervisors is not None:
            executor = next((row for row in supervisors.observe_scope(scope_id) if row.binding == binding), None)
        has_work = (
            (progress is not None and (progress.pending_inputs or progress.target_source_through is not None))
            or (invocation is not None and invocation.state in {"pending", "dirty"})
            or pending is True
        )
        state = ProcessingStatusState.PENDING if has_work else ProcessingStatusState.IDLE
        if unavailable is not None and not has_work:
            state = ProcessingStatusState.UNKNOWN
        if block is not None and pending is not True:
            state = ProcessingStatusState.BLOCKED
        return ArtifactProcessingStatus(
            family=family,
            scope_id=scope_id,
            state=state,
            observed_at=now,
            source_consumption_applicable=source_family,
            progress=progress,
            invocation=invocation,
            executor=executor,
            block=block,
            dream_operations=runs,
            dream_pending=pending,
            unavailable_reason=unavailable,
        )

    async def _source_progress(
        self, connection: AsyncConnection, scope_id: str, binding: str
    ) -> ProcessingStatusProgress:
        cursor = await SourceCursorRepository().load(connection, scope_id, binding)
        head = await connection.scalar(
            select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        )
        target = None
        if binding == TOPIC_MEMORY_SOURCE_WINDOW_BINDING:
            target = await connection.scalar(
                select(TOPIC_MEMORY_PROCESSING_TARGETS_TABLE.c.source_through).where(
                    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE.c.scope_id == scope_id,
                    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE.c.binding_name == binding,
                )
            )
        return ProcessingStatusProgress(0 if cursor is None else cursor.cursor.sequence, head or 0, target)

    async def _topic_block(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding: str,
        progress: ProcessingStatusProgress,
        lease_time_floor: datetime | None,
    ) -> ProcessingStatusBlock | None:
        after = progress.source_cursor
        through = progress.source_head if progress.target_source_through is None else progress.target_source_through
        budget = await load_topic_memory_work_budget(connection, scope_id, binding, after)
        if through <= after or budget is None or not (reason := exhausted_reason(budget)):
            return None
        table = TOPIC_MEMORY_WORK_BLOCKS_TABLE
        row = (
            (
                await connection.execute(
                    select(table)
                    .where(
                        table.c.scope_id == scope_id,
                        table.c.binding_name == binding,
                        table.c.source_after == after,
                        table.c.attempt_id == budget["attempt_id"],
                        table.c.retried_at.is_(None),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return ProcessingStatusBlock(
                error_code=row["error_code"],
                source_after=after,
                target_source_through=row["target_source_through"],
                attempts=row["attempts"],
                requests=row["requests"],
                tokens=row["tokens"],
                block_id=row["block_id"],
                blocked_at=row["blocked_at"].replace(tzinfo=UTC),
                source_through=row["source_through"],
                budget_source_through=row["budget_source_through"],
                diagnostics=classify_block_code("topic_memory", row["error_code"]),
            )
        if budget["started_at"] is not None and budget["finished_at"] is None:
            group = budget["supervisor_group"]
            if group is None:
                return None
            lease = await ArtifactProcessingLeaseRepository().load(connection, group)
            if (
                lease is not None
                and lease.holder_id == budget["holder_id"]
                and lease.supervisor_generation == budget["supervisor_generation"]
                and (
                    lease.lease_expires_at is None
                    or lease_time_floor is None
                    or lease.lease_expires_at > lease_time_floor
                )
            ):
                return None
        return ProcessingStatusBlock(
            error_code=reason,
            source_after=after,
            target_source_through=through,
            attempts=budget["attempts"],
            requests=budget["requests"],
            tokens=budget["tokens"],
            source_through=budget["attempt_source_through"],
            budget_source_through=budget["source_through"],
            diagnostics=classify_block_code("topic_memory", reason),
        )

    async def _dream_status(
        self, connection: AsyncConnection, scope_id: str, family: str
    ) -> tuple[tuple[DreamOperationStatus, ...], bool]:
        dream_family = "atomic-memory" if family == "memory" else family
        binding = FAMILY_DREAM_BINDINGS[dream_family]
        operations = tuple(spec.operation for spec in DREAM_OPERATIONS if spec.binding == binding)
        table = DREAM_RUNS_TABLE
        target = func.json_extract(cast(table.c.payload, Text), "$.request.tag_target.target.family")
        if connection.dialect.name == "mysql":
            target = func.json_unquote(target)
        condition = and_(
            table.c.scope_id == scope_id,
            or_(table.c.operation.in_(operations), and_(table.c.operation == "revise_tags", target == dream_family)),
        )
        payloads = await connection.scalars(
            select(table.c.payload)
            .where(condition)
            .order_by(table.c.accepted_at.desc(), table.c.run_id.desc())
            .limit(10)
        )
        runs: list[DreamOperationStatus] = []
        for payload in payloads:
            run: DreamRun = load_model(
                DreamRecord, stored_bytes(payload, column="dream"), kind="dream", name="record"
            ).run
            runs.append(
                DreamOperationStatus(
                    run_id=run.run_id,
                    operation=run.operation,
                    status=run.status,
                    outcome=run.outcome,
                    accepted_at=run.accepted_at,
                    completed_at=run.completed_at,
                    candidate=run.candidate,
                    diagnostics=classify_block_code("dream", run.error),
                )
            )
        pending = await connection.scalar(
            select(table.c.run_id).where(condition, table.c.status.in_(("queued", "running"))).limit(1)
        )
        return tuple(runs), pending is not None


class ScopedProcessingStatusApplication:
    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def status(self, family: str, /) -> ArtifactProcessingStatus:
        async with self._runtime._operation():
            lease_time_floor = None
            if family == "topic-memory":
                # A snapshot's old lease must not be compared with a later clock after a concurrent renewal.
                async with self._runtime.primary_database.transaction() as connection:
                    lease_time_floor = await database_utc_now(connection)
            async with self._runtime.primary_database.transaction(consistent_snapshot=True) as connection:
                return await ProcessingStatusInspector().status(
                    connection,
                    self.scope_id,
                    family,
                    supervisors=self._runtime.artifact_processing_supervisor,
                    lease_time_floor=lease_time_floor,
                )


class ProcessingStatusApplication:
    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedProcessingStatusApplication:
        return ScopedProcessingStatusApplication(self._runtime, scope_id)


__all__ = [
    "ArtifactProcessingStatus",
    "DreamOperationStatus",
    "ProcessingStatusApplication",
    "ProcessingStatusBlock",
    "ProcessingStatusInspector",
    "ProcessingStatusProgress",
    "ProcessingStatusState",
    "ScopedProcessingStatusApplication",
]
