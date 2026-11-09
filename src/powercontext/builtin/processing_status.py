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

"""Read-only processing status inspection that reuses existing durable data.

Every query is bounded and passive: no model calls, no writes, no triggers.
Missing observations are explicitly reported as unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

from powercontext.builtin.dream.bindings import DREAM_OPERATIONS, FAMILY_DREAM_BINDINGS
from powercontext.builtin.dream.models import ListDreamRunsRequest
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.processing_diagnostics import ProcessingDiagnostics, classify_block_code
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS


class ProcessingStatusState(StrEnum):
    """A bounded processing state observed from durable data."""

    IDLE = "idle"
    PENDING = "pending"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ObservationOrigin(StrEnum):
    """Where the evidence for a displayed fact originates."""

    DURABLE = "durable"
    LOCAL = "local"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class ProcessingStatusProgress:
    """Source-consumption progress from durable processing intents."""

    source_cursor: int = 0
    source_head: int = 0
    dirty_generation: int = 0
    clean_generation: int = 0
    requested_generation: int = 0
    handled_generation: int = 0

    @property
    def pending_inputs(self) -> bool:
        return self.source_head > self.source_cursor

    @property
    def outstanding_work(self) -> bool:
        return self.dirty_generation > self.clean_generation


@dataclass(frozen=True, slots=True)
class ProcessingStatusBlock:
    """A durable processing block with safe diagnostic details."""

    block_id: str
    stage: str | None = None
    error_code: str | None = None
    diagnostics: ProcessingDiagnostics | None = None


@dataclass(frozen=True, slots=True)
class DreamOperationStatus:
    """One Dream operation's durable status within the selected Scope."""

    run_id: str
    operation: str
    status: str
    outcome: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactProcessingStatus:
    """One Scope + Family processing snapshot from existing durable evidence."""

    family: str
    scope_id: str
    state: ProcessingStatusState = ProcessingStatusState.UNKNOWN
    progress: ProcessingStatusProgress | None = None
    block: ProcessingStatusBlock | None = None
    dream_operations: tuple[DreamOperationStatus, ...] = ()
    origin: ObservationOrigin = ObservationOrigin.DURABLE
    observed_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)


# Families backed by durable Source-window progress (processing intents).
_SOURCE_CONSUMPTION_FAMILIES = frozenset({"memory", "experience", "profile"})


class ProcessingStatusInspector:
    """Read-only inspection that reuses existing repositories and observations."""

    def __init__(
        self,
        *,
        intents: ArtifactProcessingIntentRepository | None = None,
        dream: DreamRepository | None = None,
        topic_memory_status: Any = None,
    ) -> None:
        self._intents = intents or ArtifactProcessingIntentRepository()
        self._dream = dream or DreamRepository()
        self._topic_memory_status = topic_memory_status

    async def status(
        self,
        connection: Any,
        scope_id: str,
        family: str,
        /,
    ) -> ArtifactProcessingStatus:
        """Return a bounded, read-only processing snapshot for one Scope + Family."""

        now = _utc_now()
        if family == "topic-memory":
            return await self._topic_memory_snapshot(connection, scope_id, now)
        if family in _SOURCE_CONSUMPTION_FAMILIES:
            result = await self._source_consumption_snapshot(connection, scope_id, family, now)
            # Source-consumption families may also have Dream operations (e.g. refine_experience).
            # The "memory" family's Dream key is "atomic-memory" internally.
            dream_family = "atomic-memory" if family == "memory" else family
            dream_binding = FAMILY_DREAM_BINDINGS.get(dream_family)
            if dream_binding is not None:
                dream_result = await self._dream_snapshot(connection, scope_id, family, dream_binding, now)
                if dream_result.dream_operations:
                    result = ArtifactProcessingStatus(
                        family=result.family,
                        scope_id=result.scope_id,
                        state=result.state,
                        progress=result.progress,
                        dream_operations=dream_result.dream_operations,
                        origin=result.origin,
                        observed_at=now,
                        details=result.details,
                    )
            return result
        # Families backed by Dream explicit requests.
        binding = FAMILY_DREAM_BINDINGS.get(family)
        if binding is not None:
            return await self._dream_snapshot(connection, scope_id, family, binding, now)
        return ArtifactProcessingStatus(
            family=family,
            scope_id=scope_id,
            state=ProcessingStatusState.UNKNOWN,
            origin=ObservationOrigin.DURABLE,
            observed_at=now,
            details={"reason": "family_not_supported"},
        )

    async def _source_consumption_snapshot(
        self, connection: Any, scope_id: str, family: str, now: datetime
    ) -> ArtifactProcessingStatus:
        binding = FAMILY_BINDINGS.get(family)
        if binding is None:
            return _unsupported_snapshot(family, scope_id, now, "missing_binding")
        intent = await self._intents.load(connection, scope_id, binding)
        if intent is None:
            return ArtifactProcessingStatus(
                family=family,
                scope_id=scope_id,
                state=ProcessingStatusState.IDLE,
                origin=ObservationOrigin.DURABLE,
                observed_at=now,
                details={"binding": binding},
            )
        progress = ProcessingStatusProgress(
            source_cursor=intent.clean_generation,
            source_head=intent.dirty_generation,
            dirty_generation=intent.dirty_generation,
            clean_generation=intent.clean_generation,
            requested_generation=intent.requested_generation,
            handled_generation=intent.handled_generation,
        )
        if progress.pending_inputs or progress.outstanding_work:
            state = ProcessingStatusState.PENDING
        else:
            state = ProcessingStatusState.IDLE
        return ArtifactProcessingStatus(
            family=family,
            scope_id=scope_id,
            state=state,
            progress=progress,
            origin=ObservationOrigin.DURABLE,
            observed_at=now,
            details={"binding": binding},
        )

    async def _topic_memory_snapshot(
        self, connection: Any, scope_id: str, now: datetime
    ) -> ArtifactProcessingStatus:
        if self._topic_memory_status is None:
            return _unsupported_snapshot("topic-memory", scope_id, now, "recovery_not_configured")
        try:
            raw = await self._topic_memory_status(scope_id)
        except Exception:
            return ArtifactProcessingStatus(
                family="topic-memory",
                scope_id=scope_id,
                state=ProcessingStatusState.UNKNOWN,
                origin=ObservationOrigin.DURABLE,
                observed_at=now,
                details={"reason": "status_query_failed"},
            )
        tm_state = getattr(raw, "state", None)
        state_str = tm_state.value if hasattr(tm_state, "value") else str(tm_state or "")
        if state_str == "idle":
            state = ProcessingStatusState.IDLE
        elif state_str == "blocked":
            state = ProcessingStatusState.BLOCKED
        elif state_str == "pending":
            state = ProcessingStatusState.PENDING
        else:
            state = ProcessingStatusState.UNKNOWN

        block: ProcessingStatusBlock | None = None
        raw_block = getattr(raw, "block", None)
        if raw_block is not None:
            block_id = getattr(raw_block, "block_id", None) or ""
            stage = getattr(raw_block, "stage", None) or None
            error_code = getattr(raw_block, "error_code", None) or None
            diagnostics = classify_block_code(stage, error_code)
            block = ProcessingStatusBlock(
                block_id=str(block_id),
                stage=stage,
                error_code=error_code,
                diagnostics=diagnostics,
            )

        progress = ProcessingStatusProgress(
            source_cursor=getattr(raw, "source_cursor", 0) or 0,
            source_head=getattr(raw, "source_head", 0) or 0,
        )

        return ArtifactProcessingStatus(
            family="topic-memory",
            scope_id=scope_id,
            state=state,
            progress=progress,
            block=block,
            origin=ObservationOrigin.DURABLE,
            observed_at=now,
            details={
                "target_source_through": getattr(raw, "target_source_through", None),
            },
        )

    async def _dream_snapshot(
        self, connection: Any, scope_id: str, family: str, binding: str, now: datetime
    ) -> ArtifactProcessingStatus:
        operations_for_family = [
            spec.operation for spec in DREAM_OPERATIONS if spec.binding == binding
        ]
        if not operations_for_family:
            return _unsupported_snapshot(family, scope_id, now, "no_dream_operations")

        try:
            page = await self._dream.list(
                connection,
                scope_id,
                ListDreamRunsRequest(limit=10),
            )
        except Exception:
            return ArtifactProcessingStatus(
                family=family,
                scope_id=scope_id,
                state=ProcessingStatusState.UNKNOWN,
                origin=ObservationOrigin.DURABLE,
                observed_at=now,
                details={"reason": "dream_query_failed"},
            )

        if not page.runs:
            return ArtifactProcessingStatus(
                family=family,
                scope_id=scope_id,
                state=ProcessingStatusState.IDLE,
                origin=ObservationOrigin.DURABLE,
                observed_at=now,
            )

        # Filter to runs that belong to this family's operations.
        family_op_set = frozenset(operations_for_family)
        matched = [r for r in page.runs if r.operation in family_op_set]
        if not matched:
            return ArtifactProcessingStatus(
                family=family,
                scope_id=scope_id,
                state=ProcessingStatusState.IDLE,
                origin=ObservationOrigin.DURABLE,
                observed_at=now,
            )

        dream_ops: list[DreamOperationStatus] = []
        for run in matched:
            dream_ops.append(
                DreamOperationStatus(
                    run_id=run.run_id,
                    operation=run.operation,
                    status=run.status,
                    outcome=run.outcome,
                    error=run.error,
                )
            )

        latest = dream_ops[0]
        if latest.status == "failed":
            state = ProcessingStatusState.BLOCKED
        elif latest.status in ("queued", "running"):
            state = ProcessingStatusState.PENDING
        else:
            state = ProcessingStatusState.IDLE

        return ArtifactProcessingStatus(
            family=family,
            scope_id=scope_id,
            state=state,
            dream_operations=tuple(dream_ops),
            origin=ObservationOrigin.DURABLE,
            observed_at=now,
        )


def _unsupported_snapshot(family: str, scope_id: str, now: datetime, reason: str) -> ArtifactProcessingStatus:
    return ArtifactProcessingStatus(
        family=family,
        scope_id=scope_id,
        state=ProcessingStatusState.UNKNOWN,
        origin=ObservationOrigin.DURABLE,
        observed_at=now,
        details={"reason": reason},
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ArtifactProcessingStatus",
    "DreamOperationStatus",
    "ObservationOrigin",
    "ProcessingStatusApplication",
    "ProcessingStatusBlock",
    "ProcessingStatusInspector",
    "ProcessingStatusProgress",
    "ProcessingStatusState",
    "ScopedProcessingStatusApplication",
]


class ScopedProcessingStatusApplication:
    """Read-only processing status inspection for one Scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        from powercontext.limits import validate_scope_id
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def status(self, family: str, /) -> ArtifactProcessingStatus:
        recovery = getattr(self._runtime, "_topic_memory_recovery", None)
        topic_status = None if recovery is None else recovery.status
        inspector = ProcessingStatusInspector(topic_memory_status=topic_status)
        async with self._runtime._scoped_operation(self.scope_id), self._runtime.primary_database.transaction() as connection:
            return await inspector.status(connection, self.scope_id, family)


class ProcessingStatusApplication:
    """Select the scoped processing status inspection application."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedProcessingStatusApplication:
        return ScopedProcessingStatusApplication(self._runtime, scope_id)
