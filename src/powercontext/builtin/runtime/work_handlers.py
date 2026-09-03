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

"""Built-in Memory and Experience work discovery and exact-window handlers."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.persistence.cursors import StoredSourceCursor
from powercontext.builtin.persistence.database import database_now
from powercontext.builtin.persistence.rate_limit import RateLimitRepository
from powercontext.builtin.persistence.work import (
    EnqueueResult,
    WorkClaim,
    WorkRepository,
    WorkResult,
    WorkSpec,
)
from powercontext.builtin.runtime.durable_scheduler import DiscoveryPage
from powercontext.builtin.runtime.models import ExperienceIncubationResult, MemoryFlushResult
from powercontext.builtin.runtime.relational import RelationalContexts, _validate_experience_plans
from powercontext.builtin.runtime.worker import PreparedWork, WorkExecutionError
from powercontext.builtin.sources import SourceCursor, validate_scope_id
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME, SourceHighWatermark, SourceWindowTrigger
from powercontext.errors import ArtifactNotFoundError

MEMORY_WORK_KIND = "powercontext.memory.source-window"
EXPERIENCE_WORK_KIND = "powercontext.experience.incubation"
MAINTENANCE_WORK_KIND = "powercontext.maintenance.operations"
CURRENT_WORK_PAYLOAD_VERSION = 1

logger = logging.getLogger(__name__)


class SourceWindowPayload(BaseModel):
    """Reference-only snapshot of one bounded Source cursor transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor_name: str
    cursor_generation: int = Field(ge=0)
    after: int = Field(ge=0)
    through: int = Field(ge=1)
    high_watermark: int = Field(ge=1)


class MemoryWorkDiscoverer:
    """Scan Source scopes and describe pending Memory windows."""

    name = MEMORY_WORK_KIND

    def __init__(
        self,
        contexts: RelationalContexts,
        *,
        interval_seconds: float,
        window_limit: int,
        max_attempts: int,
        payload_version: int = CURRENT_WORK_PAYLOAD_VERSION,
    ) -> None:
        self._contexts = contexts
        self.interval_seconds = interval_seconds
        self._window_limit = window_limit
        self._max_attempts = max_attempts
        self._payload_version = payload_version

    async def page(self, continuation: str | None, limit: int, /) -> DiscoveryPage:
        scopes = await self._contexts.scope_ids_page(continuation, limit)
        specs: list[WorkSpec] = []
        for scope_id in scopes:
            spec = await memory_work_spec(
                self._contexts,
                scope_id,
                limit=self._window_limit,
                max_attempts=self._max_attempts,
                payload_version=self._payload_version,
            )
            if spec is not None:
                specs.append(spec)
        return DiscoveryPage(
            specs=tuple(specs),
            continuation=scopes[-1] if len(scopes) == limit else None,
        )


class ExperienceWorkDiscoverer:
    """Scan Source scopes and describe pending Experience windows."""

    name = EXPERIENCE_WORK_KIND

    def __init__(
        self,
        contexts: RelationalContexts,
        *,
        interval_seconds: float,
        window_limit: int,
        max_attempts: int,
        payload_version: int = CURRENT_WORK_PAYLOAD_VERSION,
    ) -> None:
        self._contexts = contexts
        self.interval_seconds = interval_seconds
        self._window_limit = window_limit
        self._max_attempts = max_attempts
        self._payload_version = payload_version

    async def page(self, continuation: str | None, limit: int, /) -> DiscoveryPage:
        scopes = await self._contexts.scope_ids_page(continuation, limit)
        specs: list[WorkSpec] = []
        for scope_id in scopes:
            spec = await experience_work_spec(
                self._contexts,
                scope_id,
                limit=self._window_limit,
                max_attempts=self._max_attempts,
                payload_version=self._payload_version,
            )
            if spec is not None:
                specs.append(spec)
        return DiscoveryPage(
            specs=tuple(specs),
            continuation=scopes[-1] if len(scopes) == limit else None,
        )


class OperationMaintenanceDiscoverer:
    """Schedule one globally serialized, bounded history-retention batch."""

    name = MAINTENANCE_WORK_KIND

    def __init__(self, *, interval_seconds: float, max_attempts: int) -> None:
        self.interval_seconds = interval_seconds
        self._max_attempts = max_attempts

    async def page(self, continuation: str | None, _limit: int, /) -> DiscoveryPage:
        if continuation is not None:
            return DiscoveryPage(specs=(), continuation=None)
        return DiscoveryPage(
            specs=(
                WorkSpec(
                    kind=MAINTENANCE_WORK_KIND,
                    payload_version=CURRENT_WORK_PAYLOAD_VERSION,
                    scope_id="system:operations",
                    lane_key=_digest("maintenance:operations"),
                    logical_key=_digest(MAINTENANCE_WORK_KIND),
                    payload={},
                    max_attempts=self._max_attempts,
                ),
            ),
            continuation=None,
        )


class MemoryWorkHandler:
    """Prepare Memory outside locks and atomically commit it with Work success."""

    kind = MEMORY_WORK_KIND
    supported_versions = frozenset({CURRENT_WORK_PAYLOAD_VERSION})

    def __init__(self, contexts: RelationalContexts) -> None:
        self._contexts = contexts

    async def prepare(self, claim: WorkClaim, /) -> PreparedWork:
        payload = _payload(claim)
        services = self._contexts._services_for(claim.scope_id)
        async with self._contexts.database.transaction() as connection:
            state_row = await services.repositories.cursors.load(
                connection,
                claim.scope_id,
                SOURCE_WINDOW_TRIGGER_NAME,
            )
            current_sequence, current_generation = _cursor_position(state_row)
            if current_sequence >= payload.through:
                return PreparedWork(
                    result=_memory_result(
                        previous=payload.after,
                        current=payload.through,
                        high_watermark=payload.high_watermark,
                        source_count=payload.through - payload.after,
                        memory_ref=None,
                        code="already_committed",
                    )
                )
            _require_exact_cursor(payload, current_sequence, current_generation)
            rows = await services.repositories.sources.list(
                connection,
                claim.scope_id,
                after=payload.after,
                limit=payload.through - payload.after,
            )
        _require_complete_window(rows, payload)
        _, source_catalog = services.sources()
        memory = services.memory(source_catalog)
        try:
            current = await memory.head(services.memory_artifact_id)
        except ArtifactNotFoundError:
            current = None
        with bind_usage_reporter(
            _usage_reporter(self._contexts, claim.scope_id),
            generation_purpose=ModelUsagePurpose.MEMORY_EXTRACTION,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ):
            plan = await memory.plan_remember(
                memory=current,
                sources=tuple(row.value for row in rows),
                mode="extract",
            )

        async def commit(connection: AsyncConnection) -> WorkResult:
            locked = await services.repositories.cursors.load(
                connection,
                claim.scope_id,
                SOURCE_WINDOW_TRIGGER_NAME,
                for_update=True,
            )
            sequence, generation = _cursor_position(locked)
            _require_exact_cursor(payload, sequence, generation)
            _, bound_catalog = services.sources(connection)
            updated = await services.memory(bound_catalog, connection).apply(plan)
            await services.repositories.cursors.save(
                connection,
                claim.scope_id,
                SOURCE_WINDOW_TRIGGER_NAME,
                SourceCursor(sequence=payload.through),
                expected_generation=None if payload.cursor_generation == 0 else payload.cursor_generation,
            )
            return _memory_result(
                previous=payload.after,
                current=payload.through,
                high_watermark=payload.high_watermark,
                source_count=len(rows),
                memory_ref=None if updated is None else updated.as_ref().model_dump(mode="json"),
                code="processed",
            )

        return PreparedWork(result=None, commit=commit)


class ExperienceWorkHandler:
    """Prepare Experience proposals and commit Candidates with cursor and Work."""

    kind = EXPERIENCE_WORK_KIND
    supported_versions = frozenset({CURRENT_WORK_PAYLOAD_VERSION})

    def __init__(self, contexts: RelationalContexts) -> None:
        self._contexts = contexts

    async def prepare(self, claim: WorkClaim, /) -> PreparedWork:
        payload = _payload(claim)
        services = self._contexts._services_for(claim.scope_id)
        pipeline = services.experience_pipeline
        if pipeline is None:
            raise WorkExecutionError(category="configuration", code="experience_pipeline_unavailable", retryable=False)
        async with self._contexts.database.transaction() as connection:
            state_row = await services.repositories.cursors.load(
                connection,
                claim.scope_id,
                EXPERIENCE_INCUBATION_CURSOR_NAME,
            )
            current_sequence, current_generation = _cursor_position(state_row)
            if current_sequence >= payload.through:
                return PreparedWork(result=_experience_result(payload, candidate_count=0, code="already_committed"))
            _require_exact_cursor(payload, current_sequence, current_generation)
            rows = await services.repositories.sources.list(
                connection,
                claim.scope_id,
                after=payload.after,
                limit=payload.through - payload.after,
            )
        _require_complete_window(rows, payload)
        with bind_usage_reporter(
            _usage_reporter(self._contexts, claim.scope_id),
            generation_purpose=ModelUsagePurpose.EXPERIENCE_GENERATION,
        ):
            plans = await pipeline.incubate(tuple(row.value for row in rows))
        _validate_experience_plans(plans, rows)

        async def commit(connection: AsyncConnection) -> WorkResult:
            locked = await services.repositories.cursors.load(
                connection,
                claim.scope_id,
                EXPERIENCE_INCUBATION_CURSOR_NAME,
                for_update=True,
            )
            sequence, generation = _cursor_position(locked)
            _require_exact_cursor(payload, sequence, generation)
            review = services.review(connection)
            for plan in plans:
                await review.propose_experience(
                    plan.proposal,
                    sources=plan.sources,
                    artifacts=(),
                    target=None,
                    reason=plan.reason,
                )
            await services.repositories.cursors.save(
                connection,
                claim.scope_id,
                EXPERIENCE_INCUBATION_CURSOR_NAME,
                SourceCursor(sequence=payload.through),
                expected_generation=None if payload.cursor_generation == 0 else payload.cursor_generation,
            )
            return _experience_result(payload, candidate_count=len(plans), code="processed")

        return PreparedWork(result=None, commit=commit)


def _usage_reporter(
    contexts: RelationalContexts,
    scope_id: str,
) -> Callable[[ModelUsagePurpose, ModelUsageOperation, InferenceUsage], Awaitable[None]]:
    async def report(
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
    ) -> None:
        try:
            await contexts.statistics(scope_id).record(
                purpose,
                operation,
                usage,
                datetime.now(UTC).date(),
            )
        except Exception as error:
            # Statistics are best effort and raw exception text may contain
            # provider payloads. Record only a bounded event and type name.
            logger.warning(
                "Work model usage recording failed",
                extra={
                    "event": "statistics.model_usage.failed",
                    "operation": operation.value,
                    "outcome": "failure",
                    "error_type": type(error).__name__,
                },
            )

    return report


class OperationMaintenanceHandler:
    """Delete one bounded batch of expired successful/cancelled operations."""

    kind = MAINTENANCE_WORK_KIND
    supported_versions = frozenset({CURRENT_WORK_PAYLOAD_VERSION})

    def __init__(self, *, retention_days: int, batch_size: int) -> None:
        self._retention_days = retention_days
        self._batch_size = batch_size
        self._repository = WorkRepository()
        self._rate_limits = RateLimitRepository()

    async def prepare(self, claim: WorkClaim, /) -> PreparedWork:
        if claim.payload:
            raise WorkExecutionError(category="payload", code="invalid_payload", retryable=False)

        async def commit(connection: AsyncConnection) -> WorkResult:
            now = await database_now(connection)
            deleted = await self._repository.purge_terminal(
                connection,
                completed_before=now - timedelta(days=self._retention_days),
                limit=self._batch_size,
            )
            counters_deleted = await self._rate_limits.purge_expired(connection, limit=self._batch_size)
            return WorkResult(
                code="cleaned",
                payload={
                    "operations_deleted": deleted,
                    "rate_limit_windows_deleted": counters_deleted,
                },
            )

        return PreparedWork(result=None, commit=commit)


async def enqueue_memory_work(
    contexts: RelationalContexts,
    scope_id: str,
    /,
    *,
    limit: int,
    max_attempts: int,
    payload_version: int = CURRENT_WORK_PAYLOAD_VERSION,
    repository: WorkRepository | None = None,
) -> MemoryFlushResult | EnqueueResult:
    """Determine and enqueue one manual Memory window in a short transaction."""

    scope = validate_scope_id(scope_id)
    async with contexts.database.transaction() as connection:
        state_row, high_watermark = await _window_state(
            contexts,
            connection,
            scope,
            SOURCE_WINDOW_TRIGGER_NAME,
        )
        payload = _window_payload(state_row, high_watermark, SOURCE_WINDOW_TRIGGER_NAME, limit)
        if payload is None:
            position, _ = _cursor_position(state_row)
            return MemoryFlushResult(
                previous_cursor=position,
                high_watermark=high_watermark,
                current_cursor=position,
                source_count=0,
                memory_ref=None,
            )
        spec = _work_spec(
            kind=MEMORY_WORK_KIND,
            scope_id=scope,
            payload=payload,
            max_attempts=max_attempts,
            payload_version=payload_version,
        )
        return await (WorkRepository() if repository is None else repository).enqueue(connection, spec)


async def memory_work_spec(
    contexts: RelationalContexts,
    scope_id: str,
    /,
    *,
    limit: int,
    max_attempts: int,
    payload_version: int,
) -> WorkSpec | None:
    return await _discover_spec(
        contexts,
        scope_id,
        cursor_name=SOURCE_WINDOW_TRIGGER_NAME,
        kind=MEMORY_WORK_KIND,
        limit=limit,
        max_attempts=max_attempts,
        payload_version=payload_version,
    )


async def experience_work_spec(
    contexts: RelationalContexts,
    scope_id: str,
    /,
    *,
    limit: int,
    max_attempts: int,
    payload_version: int,
) -> WorkSpec | None:
    return await _discover_spec(
        contexts,
        scope_id,
        cursor_name=EXPERIENCE_INCUBATION_CURSOR_NAME,
        kind=EXPERIENCE_WORK_KIND,
        limit=limit,
        max_attempts=max_attempts,
        payload_version=payload_version,
    )


async def _discover_spec(
    contexts: RelationalContexts,
    scope_id: str,
    *,
    cursor_name: str,
    kind: str,
    limit: int,
    max_attempts: int,
    payload_version: int,
) -> WorkSpec | None:
    scope = validate_scope_id(scope_id)
    async with contexts.database.transaction() as connection:
        state_row, high_watermark = await _window_state(contexts, connection, scope, cursor_name)
    payload = _window_payload(state_row, high_watermark, cursor_name, limit)
    return (
        None
        if payload is None
        else _work_spec(
            kind=kind,
            scope_id=scope,
            payload=payload,
            max_attempts=max_attempts,
            payload_version=payload_version,
        )
    )


async def _window_state(
    contexts: RelationalContexts,
    connection: AsyncConnection,
    scope_id: str,
    cursor_name: str,
) -> tuple[StoredSourceCursor | None, int]:
    services = contexts._services_for(scope_id)
    state_row = await services.repositories.cursors.load(connection, scope_id, cursor_name)
    high_watermark = await services.repositories.sources.journal_position(connection, scope_id)
    return state_row, high_watermark


def _window_payload(
    state_row: StoredSourceCursor | None,
    high_watermark: int,
    cursor_name: str,
    limit: int,
) -> SourceWindowPayload | None:
    state = SourceCursor() if state_row is None else state_row.cursor
    transition = SourceWindowTrigger().activate(
        SourceHighWatermark(sequence=high_watermark, limit=limit),
        state,
    )
    if not transition.actions:
        return None
    action = transition.actions[0]
    return SourceWindowPayload(
        cursor_name=cursor_name,
        cursor_generation=0 if state_row is None else state_row.generation,
        after=action.after,
        through=action.through,
        high_watermark=high_watermark,
    )


def _work_spec(
    *,
    kind: str,
    scope_id: str,
    payload: SourceWindowPayload,
    max_attempts: int,
    payload_version: int,
) -> WorkSpec:
    lane_key = _digest(f"{kind.split('.')[1]}:{scope_id}")
    logical_key = _digest(
        json.dumps(
            [kind, scope_id, payload.cursor_name, payload.cursor_generation, payload.after],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return WorkSpec(
        kind=kind,
        payload_version=payload_version,
        scope_id=scope_id,
        lane_key=lane_key,
        logical_key=logical_key,
        payload=payload.model_dump(mode="json"),
        max_attempts=max_attempts,
    )


def _payload(claim: WorkClaim) -> SourceWindowPayload:
    try:
        return SourceWindowPayload.model_validate(claim.payload)
    except ValidationError:
        raise WorkExecutionError(category="payload", code="invalid_payload", retryable=False) from None


def _cursor_position(state_row: StoredSourceCursor | None) -> tuple[int, int]:
    return (0, 0) if state_row is None else (state_row.cursor.sequence, state_row.generation)


def _require_exact_cursor(payload: SourceWindowPayload, sequence: int, generation: int) -> None:
    if sequence != payload.after or generation != payload.cursor_generation:
        raise WorkExecutionError(category="conflict", code="cursor_changed", retryable=False)


def _require_complete_window(rows, payload: SourceWindowPayload) -> None:
    expected = payload.through - payload.after
    if len(rows) != expected or any(
        row.journal_position != payload.after + offset for offset, row in enumerate(rows, start=1)
    ):
        raise WorkExecutionError(category="retention", code="source_window_unavailable", retryable=False)


def _memory_result(
    *,
    previous: int,
    current: int,
    high_watermark: int,
    source_count: int,
    memory_ref,
    code: str,
) -> WorkResult:
    result = MemoryFlushResult(
        previous_cursor=previous,
        current_cursor=current,
        high_watermark=high_watermark,
        source_count=source_count,
        memory_ref=memory_ref,
    )
    return WorkResult(code=code, payload=result.model_dump(mode="json"))


def _experience_result(payload: SourceWindowPayload, *, candidate_count: int, code: str) -> WorkResult:
    result = ExperienceIncubationResult(
        previous_cursor=payload.after,
        current_cursor=payload.through,
        high_watermark=payload.high_watermark,
        source_count=payload.through - payload.after,
        candidate_count=candidate_count,
    )
    return WorkResult(code=code, payload=result.model_dump(mode="json"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = [
    "CURRENT_WORK_PAYLOAD_VERSION",
    "EXPERIENCE_WORK_KIND",
    "MEMORY_WORK_KIND",
    "ExperienceWorkDiscoverer",
    "ExperienceWorkHandler",
    "MemoryWorkDiscoverer",
    "MemoryWorkHandler",
    "SourceWindowPayload",
    "enqueue_memory_work",
    "experience_work_spec",
    "memory_work_spec",
]
