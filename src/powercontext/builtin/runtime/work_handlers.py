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

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError
from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.artifacts.profile.models import (
    PROFILE_SOURCE_WINDOW_BINDING,
    ProfileFlushResult,
)
from powercontext.builtin.artifacts.profile.service import PreparedProfileFlush, RelationalProfileService
from powercontext.builtin.artifacts.prompt.service import current_prompt
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.persistence.cursors import StoredSourceCursor
from powercontext.builtin.persistence.database import database_now, is_transaction_contention
from powercontext.builtin.persistence.errors import GenerationConflictError
from powercontext.builtin.persistence.rate_limit import RateLimitRepository
from powercontext.builtin.persistence.tables import PROFILE_POLICIES_TABLE
from powercontext.builtin.persistence.work import (
    WorkClaim,
    WorkRepository,
    WorkResult,
    WorkSpec,
)
from powercontext.builtin.records import BaseValueConflictError
from powercontext.builtin.runtime.cron import CronSchedule
from powercontext.builtin.runtime.durable_scheduler import DiscoveryPage
from powercontext.builtin.runtime.models import ExperienceIncubationResult, MemoryFlushResult
from powercontext.builtin.runtime.relational import RelationalContexts, _validate_experience_plans
from powercontext.builtin.runtime.worker import PreparedWork, WorkExecutionError
from powercontext.builtin.sources import SourceCursor, validate_scope_id
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME, SourceHighWatermark, SourceWindowTrigger
from powercontext.errors import RevisionConflictError

MEMORY_WORK_KIND = "powercontext.memory.source-window"
EXPERIENCE_WORK_KIND = "powercontext.experience.incubation"
PROFILE_WORK_KIND = "powercontext.profile.source-window"
MAINTENANCE_WORK_KIND = "powercontext.maintenance.operations"
CURRENT_WORK_PAYLOAD_VERSION = 1

ProfileWorkRunner = Callable[[str, int, RelationalProfileService], Awaitable[PreparedProfileFlush]]


class WorkRequester(BaseModel):
    """Non-sensitive identity reference carried across async execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["user", "service"]
    id: str = Field(min_length=1, max_length=255)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("requester id must be trimmed")  # noqa: TRY003
        return value


class SourceWindowPayload(BaseModel):
    """Reference-only snapshot of one bounded Source cursor transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor_name: str
    cursor_generation: int = Field(ge=0)
    after: int = Field(ge=0)
    through: int = Field(ge=1)
    high_watermark: int = Field(ge=1)
    requester: WorkRequester | None = None


class ProfileWorkPayload(BaseModel):
    """Reference-only boundary for one scheduled Profile catch-up."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor_generation: int = Field(ge=0)
    after: int = Field(ge=0)
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


class ProfileWorkDiscoverer:
    """Scan enabled Profile policies on a persistent cron schedule."""

    name = PROFILE_WORK_KIND
    interval_seconds = 60.0

    def __init__(
        self,
        contexts: RelationalContexts,
        *,
        cron: str,
        timezone: str,
        max_attempts: int,
        payload_version: int = CURRENT_WORK_PAYLOAD_VERSION,
    ) -> None:
        self._contexts = contexts
        self._schedule = CronSchedule.parse(cron, timezone)
        self._max_attempts = max_attempts
        self._payload_version = payload_version

    def next_run_at(self, now: datetime, /) -> datetime:
        return self._schedule.next_after(now)

    async def page(self, continuation: str | None, limit: int, /) -> DiscoveryPage:
        async with self._contexts.database.transaction() as connection:
            query = select(PROFILE_POLICIES_TABLE.c.scope_id).where(
                PROFILE_POLICIES_TABLE.c.generation_enabled.is_(True),
                PROFILE_POLICIES_TABLE.c.pending_candidate_id.is_(None),
            )
            if continuation is not None:
                query = query.where(PROFILE_POLICIES_TABLE.c.scope_id > validate_scope_id(continuation))
            values = (
                await connection.execute(query.order_by(PROFILE_POLICIES_TABLE.c.scope_id).limit(limit))
            ).scalars()
            scopes = tuple(str(value) for value in values)
        specs: list[WorkSpec] = []
        for scope_id in scopes:
            spec = await profile_work_spec(
                self._contexts,
                scope_id,
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
    """Run one claimed Memory window through the Atomic Memory processor.

    Preparation selects the window and runs the model calls without any
    domain write. The publication (Memory artifacts plus cursor advance) is
    returned as a ``PreparedWork.commit`` closure and runs inside the fenced
    Work completion transaction, after ``WorkRepository.complete`` verified
    the live claim and cancellation: a cancel accepted during preparation
    discards the prepared result with the rolled-back transaction. A retried
    attempt re-reads the cursor, so a window that already committed retires
    instead of repeating the domain write.
    """

    kind = MEMORY_WORK_KIND
    supported_versions = frozenset({CURRENT_WORK_PAYLOAD_VERSION})

    def __init__(self, contexts: RelationalContexts, *, window_limit: int) -> None:
        self._contexts = contexts
        self._window_limit = window_limit

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
                    payload,
                    current=current_sequence,
                    source_count=0,
                    code="already_committed",
                )
            )
        if _is_superseded_window(payload, current_sequence, current_generation):
            # A committed racing window consumed this snapshot's prefix; retire
            # the key so the lane keeps moving and discovery re-enqueues the rest.
            return PreparedWork(
                result=_memory_result(
                    payload,
                    current=current_sequence,
                    source_count=0,
                    code="superseded",
                )
            )
        _require_exact_cursor(payload, current_sequence, current_generation)
        prepared = await self._contexts.prepare_memory(claim.scope_id, self._window_limit)
        if isinstance(prepared, MemoryFlushResult):
            # The trigger selected no window; there is nothing to publish and
            # nothing the ledger fence could discard.
            return PreparedWork(
                result=WorkResult(
                    code="processed" if prepared.processed else "idle",
                    payload=prepared.model_dump(mode="json", exclude_none=True),
                )
            )

        async def commit(connection: AsyncConnection) -> WorkResult:
            try:
                # Runs inside the Work completion transaction after
                # WorkRepository.complete has verified the live claim and
                # cancellation: a cancel accepted before publication rolls this
                # domain write back with the fenced completion.
                flushed = await prepared.publish(connection, processing=None)
            except (AtomicMemoryConflictError, GenerationConflictError, RevisionConflictError) as error:
                raise WorkExecutionError(category="conflict", code="memory_conflict", retryable=True) from error
            prepared.emit_finalized()
            return WorkResult(
                code="processed" if flushed.processed else "idle",
                payload=flushed.model_dump(mode="json", exclude_none=True),
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
                return PreparedWork(
                    result=_experience_result(
                        payload,
                        source_count=payload.through - payload.after,
                        candidate_count=0,
                        code="already_committed",
                    )
                )
            if _is_superseded_window(payload, current_sequence, current_generation):
                # A committed racing window consumed this snapshot's prefix.
                # Retire instead of failing so the key is released and the lane
                # keeps moving; discovery re-enqueues the remaining Sources.
                return PreparedWork(
                    result=_experience_result(
                        payload,
                        source_count=0,
                        candidate_count=0,
                        code="superseded",
                        current_cursor=current_sequence,
                    )
                )
            _require_exact_cursor(payload, current_sequence, current_generation)
            rows = await services.repositories.sources.list_window(
                connection,
                claim.scope_id,
                after=payload.after,
                through=payload.through,
            )
            eligible_rows = await services.generation_sources().list_window_for_generation(
                connection,
                claim.scope_id,
                after=payload.after,
                through=payload.through,
            )
        _require_complete_window(rows, payload)
        try:
            async with services.prompts.bind(claim.scope_id, "experience.incubate"):
                with bind_usage_reporter(
                    self._contexts.model_usage_reporter(claim.scope_id),
                    generation_purpose=ModelUsagePurpose.EXPERIENCE_GENERATION,
                ):
                    plans = (
                        () if not eligible_rows else await pipeline.incubate(tuple(row.value for row in eligible_rows))
                    )
                selection = current_prompt("experience.incubate")
                prompt_refs = () if selection is None or selection.artifact is None else (selection.artifact,)
        finally:
            await self._contexts.flush_model_usage()
        _validate_experience_plans(plans, eligible_rows)

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
            candidate_ids: list[str] = []
            for plan in plans:
                candidate = await review.propose_experience(
                    plan.proposal,
                    sources=plan.sources,
                    artifacts=prompt_refs,
                    target=None,
                    reason=plan.reason,
                )
                candidate_ids.append(candidate.candidate_id)
            for proposal in await services.recurrence_ledger().record_window(connection, eligible_rows):
                target_content = await services.repositories.artifacts.get(
                    connection,
                    claim.scope_id,
                    proposal.target,
                )
                candidate = await review.propose_experience(
                    proposal.proposal,
                    sources=proposal.sources,
                    artifacts=(target_content.as_ref(),),
                    target=proposal.target,
                    reason=proposal.reason,
                )
                candidate_ids.append(candidate.candidate_id)
            await services.repositories.cursors.save(
                connection,
                claim.scope_id,
                EXPERIENCE_INCUBATION_CURSOR_NAME,
                SourceCursor(sequence=payload.through),
                expected_generation=None if payload.cursor_generation == 0 else payload.cursor_generation,
            )
            return _experience_result(
                payload,
                source_count=len(eligible_rows),
                candidate_count=len(candidate_ids),
                candidate_ids=tuple(candidate_ids),
                code="processed",
            )

        return PreparedWork(result=None, commit=commit)


class ProfileWorkHandler:
    """Prepare one Profile window outside locks and commit it with Work success.

    One attempt prepares and commits exactly one window: the model call runs
    outside any transaction during ``prepare``, and the Profile, its cursor
    and the Work success commit inside the single fenced completion
    transaction. Remaining windows are re-discovered on the next scheduled
    scan, so an expired or superseded Worker can never publish a Profile.
    """

    kind = PROFILE_WORK_KIND
    supported_versions = frozenset({CURRENT_WORK_PAYLOAD_VERSION})

    def __init__(
        self,
        profiles: RelationalProfileService,
        *,
        max_concurrency: int,
        runner: ProfileWorkRunner | None = None,
    ) -> None:
        self._profiles = profiles
        self._runner = runner
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def prepare(self, claim: WorkClaim, /) -> PreparedWork:
        payload = _profile_payload(claim)
        async with self._semaphore:
            prepared = (
                await self._runner(claim.scope_id, payload.high_watermark, self._profiles)
                if self._runner is not None
                else await self._profiles.prepare_flush(claim.scope_id, high_watermark=payload.high_watermark)
            )
        if prepared.result is not None:
            return PreparedWork(result=_profile_result(prepared.result))

        async def commit(connection: AsyncConnection) -> WorkResult:
            try:
                flushed = await self._profiles.commit_flush(connection, prepared)
            except (
                BaseValueConflictError,
                GenerationConflictError,
                RevisionConflictError,
                IntegrityError,
                OperationalError,
            ) as error:
                if isinstance(error, OperationalError) and not is_transaction_contention(error):
                    raise
                raise WorkExecutionError(category="conflict", code="profile_cursor_changed", retryable=True) from error
            return _profile_result(flushed)

        return PreparedWork(result=None, commit=commit)


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


async def profile_work_spec(
    contexts: RelationalContexts,
    scope_id: str,
    /,
    *,
    max_attempts: int,
    payload_version: int,
) -> WorkSpec | None:
    scope = validate_scope_id(scope_id)
    async with contexts.database.transaction() as connection:
        services = contexts._services_for(scope)
        cursor = await services.repositories.cursors.load(connection, scope, PROFILE_SOURCE_WINDOW_BINDING)
        high_watermark = await services.repositories.sources.journal_position(connection, scope)
    after, generation = _cursor_position(cursor)
    if after >= high_watermark:
        return None
    payload = ProfileWorkPayload(
        cursor_generation=generation,
        after=after,
        high_watermark=high_watermark,
    )
    return WorkSpec(
        kind=PROFILE_WORK_KIND,
        payload_version=payload_version,
        scope_id=scope,
        lane_key=_digest(f"profile:{scope}"),
        logical_key=_digest(
            json.dumps(
                [PROFILE_WORK_KIND, scope, PROFILE_SOURCE_WINDOW_BINDING, generation, after],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        payload=payload.model_dump(mode="json"),
        max_attempts=max_attempts,
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
    *,
    requester: WorkRequester | None = None,
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
        requester=requester,
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
        payload=payload.model_dump(mode="json", exclude_none=True),
        max_attempts=max_attempts,
    )


def _payload(claim: WorkClaim) -> SourceWindowPayload:
    try:
        return SourceWindowPayload.model_validate(claim.payload)
    except ValidationError:
        raise WorkExecutionError(category="payload", code="invalid_payload", retryable=False) from None


def _profile_payload(claim: WorkClaim) -> ProfileWorkPayload:
    try:
        return ProfileWorkPayload.model_validate(claim.payload)
    except ValidationError:
        raise WorkExecutionError(category="payload", code="invalid_payload", retryable=False) from None


def _cursor_position(state_row: StoredSourceCursor | None) -> tuple[int, int]:
    return (0, 0) if state_row is None else (state_row.cursor.sequence, state_row.generation)


def _require_exact_cursor(payload: SourceWindowPayload, sequence: int, generation: int) -> None:
    if sequence != payload.after or generation != payload.cursor_generation:
        raise WorkExecutionError(category="conflict", code="cursor_changed", retryable=False)


def _is_superseded_window(payload: SourceWindowPayload, sequence: int, generation: int) -> bool:
    """True when a committed racing window already consumed this snapshot's prefix.

    A generation mismatch means the Source cursor was regenerated and the
    window identity no longer holds, which stays a terminal failure — except
    for snapshots taken before the cursor row existed at all: the racing
    window's first commit creates that row at generation 1, which is an
    ordinary advance, not a reset.
    """

    if payload.cursor_generation == 0:
        return generation == 1 and payload.after < sequence < payload.through
    return generation == payload.cursor_generation and payload.after < sequence < payload.through


def _require_complete_window(rows, payload: SourceWindowPayload) -> None:
    expected = payload.through - payload.after
    if len(rows) != expected or any(
        row.journal_position != payload.after + offset for offset, row in enumerate(rows, start=1)
    ):
        raise WorkExecutionError(category="retention", code="source_window_unavailable", retryable=False)


def _memory_result(
    payload: SourceWindowPayload,
    *,
    current: int,
    source_count: int,
    code: str,
) -> WorkResult:
    result = MemoryFlushResult(
        previous_cursor=payload.after,
        current_cursor=current,
        high_watermark=payload.high_watermark,
        source_count=source_count,
        memory_ref=None,
    )
    return WorkResult(code=code, payload=result.model_dump(mode="json", exclude_none=True))


def _experience_result(
    payload: SourceWindowPayload,
    *,
    source_count: int,
    candidate_count: int,
    candidate_ids: tuple[str, ...] = (),
    code: str,
    current_cursor: int | None = None,
) -> WorkResult:
    result = ExperienceIncubationResult(
        previous_cursor=payload.after,
        current_cursor=payload.through if current_cursor is None else current_cursor,
        high_watermark=payload.high_watermark,
        source_count=source_count,
        candidate_count=candidate_count,
        candidate_ids=candidate_ids,
    )
    return WorkResult(code=code, payload=result.model_dump(mode="json"))


def _profile_result(result: ProfileFlushResult) -> WorkResult:
    return WorkResult(code=result.status, payload=result.model_dump(mode="json", exclude_none=True))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = [
    "CURRENT_WORK_PAYLOAD_VERSION",
    "EXPERIENCE_WORK_KIND",
    "MEMORY_WORK_KIND",
    "PROFILE_WORK_KIND",
    "ExperienceWorkDiscoverer",
    "ExperienceWorkHandler",
    "MemoryWorkDiscoverer",
    "MemoryWorkHandler",
    "ProfileWorkDiscoverer",
    "ProfileWorkHandler",
    "ProfileWorkPayload",
    "ProfileWorkRunner",
    "SourceWindowPayload",
    "WorkRequester",
    "experience_work_spec",
    "memory_work_spec",
]
