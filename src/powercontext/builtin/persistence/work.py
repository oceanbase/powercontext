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

"""Durable work ledger with lane serialization, leases, and fencing."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from sqlalchemy import and_, delete, false, func, insert, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import stored_bytes
from powercontext.builtin.persistence.database import database_now, insert_if_absent
from powercontext.builtin.persistence.errors import (
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    InvalidStoredPayloadError,
    RepositoryError,
    RepositoryNotFoundError,
)
from powercontext.builtin.persistence.tables import (
    WORK_ATTEMPTS_TABLE,
    WORK_ITEMS_TABLE,
    WORK_KEYS_TABLE,
    WORK_LANES_TABLE,
)
from powercontext.limits import MAX_SCOPE_ID_LENGTH

if TYPE_CHECKING:
    from powercontext.builtin.runtime.work_observability import WorkObserver

_MAX_PAYLOAD_BYTES = 16 * 1024
_TERMINAL_STATUSES = frozenset({"succeeded", "cancelled"})


class WorkStatus(StrEnum):
    """Persisted lifecycle states for one logical work window."""

    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkSpec(BaseModel):
    """Safe, versioned references needed to execute one logical window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    payload_version: int = Field(ge=1)
    scope_id: str
    lane_key: str
    logical_key: str
    payload: dict[str, JsonValue]
    max_attempts: int = Field(default=5, ge=1, le=100)
    available_at: datetime | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        return _validated_text("kind", value, 128)

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: str) -> str:
        return _validated_text("scope_id", value, MAX_SCOPE_ID_LENGTH)

    @field_validator("lane_key", "logical_key")
    @classmethod
    def validate_digest(cls, value: str, info: Any) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"{info.field_name} must be a lowercase SHA-256 digest")  # noqa: TRY003
        return value


class WorkResult(BaseModel):
    """Sanitized result metadata persisted after a successful commit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _validated_text("code", value, 128)


WorkCommit = Callable[[AsyncConnection], Awaitable[WorkResult | None]]


class WorkFailure(BaseModel):
    """Bounded failure classification; raw exceptions are never persisted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    code: str
    retryable: bool

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return _validated_text("category", value, 64)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return _validated_text("code", value, 128)


class StoredWork(BaseModel):
    """Decoded work item returned by repository operations."""

    model_config = ConfigDict(frozen=True)

    work_id: str
    logical_key: str
    lane_key: str
    lane_sequence: int
    kind: str
    payload_version: int
    scope_id: str
    payload: dict[str, JsonValue]
    status: WorkStatus
    available_at: datetime
    lease_owner: str | None
    lease_fence: int
    lease_expires_at: datetime | None
    attempt_count: int
    generation_attempt_count: int
    recovery_generation: int
    max_attempts: int
    cancel_requested: bool
    result_code: str | None
    result_payload: dict[str, JsonValue] | None
    error_category: str | None
    error_code: str | None
    state_version: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class EnqueueResult(BaseModel):
    """Whether enqueue created a row or joined an existing logical window."""

    model_config = ConfigDict(frozen=True)

    created: bool
    work: StoredWork


class WorkClaim(BaseModel):
    """A fenced lease plus the safe payload needed by a Worker handler."""

    model_config = ConfigDict(frozen=True)

    work_id: str
    logical_key: str
    lane_key: str
    lane_sequence: int
    kind: str
    payload_version: int
    scope_id: str
    payload: dict[str, JsonValue]
    owner_id: str
    fence: int
    attempt_no: int
    generation_attempt_no: int
    recovery_generation: int
    created_at: datetime
    claimed_at: datetime
    previous_trace_id: str | None = None
    previous_span_id: str | None = None
    lease_expires_at: datetime


class WorkQueueStatistic(BaseModel):
    """One bounded queue gauge sample grouped by kind and state."""

    model_config = ConfigDict(frozen=True)

    kind: str
    status: WorkStatus
    depth: int
    oldest_age_seconds: float


class WorkStateConflictError(RepositoryError):
    """Raised when an optimistic operation observes a different state."""

    def __init__(self, work_id: str, expected_version: int, status: WorkStatus, actual_version: int) -> None:
        self.work_id = work_id
        self.expected_version = expected_version
        self.status = status
        self.actual_version = actual_version
        super().__init__(
            f"work {work_id!r} changed: expected version {expected_version}, "
            f"found {status.value} at version {actual_version}"
        )


class StaleWorkClaimError(RepositoryError):
    """Raised when a lease owner or fence can no longer mutate a work item."""

    def __init__(self, work_id: str) -> None:
        self.work_id = work_id
        super().__init__(f"work claim for {work_id!r} is no longer current")


class WorkRepository:
    """Persist and transition work using short caller-owned transactions."""

    def __init__(self, *, observer: WorkObserver | None = None) -> None:
        self._observer = observer

    async def enqueue(self, connection: AsyncConnection, spec: WorkSpec, /) -> EnqueueResult:
        payload = _dump_payload(spec.payload, kind="work", name=spec.logical_key)
        now = await database_now(connection)
        lane = await _lock_or_create_lane(connection, spec.lane_key)
        existing = await _work_for_logical_key(connection, spec.logical_key, for_update=True)
        if existing is not None:
            _verify_logical_identity(existing, spec)
            self._observe_enqueue(spec.kind, created=False)
            return EnqueueResult(created=False, work=existing)

        work_id = str(uuid4())
        created = await insert_if_absent(
            connection,
            WORK_KEYS_TABLE,
            {
                "logical_key": spec.logical_key,
                "work_id": work_id,
                "lane_key": spec.lane_key,
                "created_at": now,
            },
        )
        if not created:
            existing = await _work_for_logical_key(connection, spec.logical_key, for_update=True)
            if existing is None:
                raise InvalidStoredColumnError("logical_key", "a concurrently persisted work key")
            _verify_logical_identity(existing, spec)
            self._observe_enqueue(spec.kind, created=False)
            return EnqueueResult(created=False, work=existing)

        sequence = _positive_integer(lane["next_sequence"], "next_sequence")
        available_at = _normalized_datetime(spec.available_at) if spec.available_at is not None else now
        await connection.execute(
            update(WORK_LANES_TABLE)
            .where(WORK_LANES_TABLE.c.lane_key == spec.lane_key)
            .values(
                next_sequence=sequence + 1,
                head_sequence=sequence if lane["head_sequence"] is None else lane["head_sequence"],
            )
        )
        await connection.execute(
            insert(WORK_ITEMS_TABLE).values(
                work_id=work_id,
                logical_key=spec.logical_key,
                lane_key=spec.lane_key,
                lane_sequence=sequence,
                kind=spec.kind,
                payload_version=spec.payload_version,
                scope_id=spec.scope_id,
                payload=payload,
                status=WorkStatus.QUEUED.value,
                available_at=available_at,
                lease_owner=None,
                lease_fence=0,
                lease_expires_at=None,
                attempt_count=0,
                generation_attempt_count=0,
                recovery_generation=0,
                max_attempts=spec.max_attempts,
                cancel_requested=False,
                result_code=None,
                result_payload=None,
                error_category=None,
                error_code=None,
                state_version=1,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
        )
        self._observe_enqueue(spec.kind, created=True)
        return EnqueueResult(created=True, work=await self.get(connection, work_id))

    async def claim(
        self,
        connection: AsyncConnection,
        *,
        worker_id: str,
        supported: Mapping[str, frozenset[int]],
        lease_seconds: int,
        limit: int,
        expired_retry_delay_seconds: int = 0,
    ) -> tuple[WorkClaim, ...]:
        _validated_text("worker_id", worker_id, 128)
        _require_positive("lease_seconds", lease_seconds)
        _require_positive("limit", limit)
        _require_nonnegative("expired_retry_delay_seconds", expired_retry_delay_seconds)
        if not supported:
            return ()
        _validate_supported(supported)

        now = await database_now(connection)
        supported_pairs = [
            and_(WORK_ITEMS_TABLE.c.kind == kind, WORK_ITEMS_TABLE.c.payload_version.in_(versions))
            for kind, versions in supported.items()
        ]
        supported_due = and_(
            or_(*supported_pairs),
            WORK_ITEMS_TABLE.c.status.in_((WorkStatus.QUEUED.value, WorkStatus.RETRY_WAIT.value)),
            WORK_ITEMS_TABLE.c.available_at <= now,
        )
        expired = and_(
            WORK_ITEMS_TABLE.c.status.in_((WorkStatus.RUNNING.value, WorkStatus.CANCELLING.value)),
            WORK_ITEMS_TABLE.c.lease_expires_at <= now,
        )
        candidate_ids = (
            await connection.scalars(
                select(WORK_ITEMS_TABLE.c.work_id)
                .join(
                    WORK_LANES_TABLE,
                    and_(
                        WORK_LANES_TABLE.c.lane_key == WORK_ITEMS_TABLE.c.lane_key,
                        WORK_LANES_TABLE.c.head_sequence == WORK_ITEMS_TABLE.c.lane_sequence,
                    ),
                )
                .where(or_(supported_due, expired))
                .order_by(WORK_ITEMS_TABLE.c.available_at, WORK_ITEMS_TABLE.c.created_at)
                .limit(max(32, limit * 4))
            )
        ).all()

        claims: list[WorkClaim] = []
        for work_id_value in candidate_ids:
            if len(claims) >= limit:
                break
            claim = await self._claim_candidate(
                connection,
                str(work_id_value),
                worker_id=worker_id,
                supported=supported,
                lease_seconds=lease_seconds,
                expired_retry_delay_seconds=expired_retry_delay_seconds,
            )
            if claim is not None:
                claims.append(claim)
        return tuple(claims)

    async def heartbeat(
        self,
        connection: AsyncConnection,
        claim: WorkClaim,
        /,
        *,
        lease_seconds: int,
    ) -> StoredWork:
        _require_positive("lease_seconds", lease_seconds)
        await _lock_lane(connection, claim.lane_key)
        await _lock_logical_key(connection, claim.logical_key, claim.work_id)
        work = await _lock_work(connection, claim.work_id)
        now = await database_now(connection)
        _verify_claim(work, claim, now)
        if work.cancel_requested or work.status is WorkStatus.CANCELLING:
            raise StaleWorkClaimError(claim.work_id)
        expires_at = now + timedelta(seconds=lease_seconds)
        await connection.execute(
            update(WORK_ITEMS_TABLE)
            .where(WORK_ITEMS_TABLE.c.work_id == claim.work_id)
            .values(lease_expires_at=expires_at, updated_at=now, state_version=work.state_version + 1)
        )
        await connection.execute(
            update(WORK_ATTEMPTS_TABLE)
            .where(
                WORK_ATTEMPTS_TABLE.c.work_id == claim.work_id,
                WORK_ATTEMPTS_TABLE.c.attempt_no == claim.attempt_no,
                WORK_ATTEMPTS_TABLE.c.owner_id == claim.owner_id,
                WORK_ATTEMPTS_TABLE.c.fence == claim.fence,
            )
            .values(heartbeat_at=now)
        )
        return await self.get(connection, claim.work_id)

    async def record_attempt_trace(
        self,
        connection: AsyncConnection,
        claim: WorkClaim,
        /,
        *,
        trace_id: str,
        span_id: str,
    ) -> None:
        """Bind one non-sensitive span identity to the current fenced attempt."""

        _require_hex("trace_id", trace_id, 32)
        _require_hex("span_id", span_id, 16)
        await _lock_lane(connection, claim.lane_key)
        await _lock_logical_key(connection, claim.logical_key, claim.work_id)
        work = await _lock_work(connection, claim.work_id)
        _verify_claim(work, claim, await database_now(connection))
        result = await connection.execute(
            update(WORK_ATTEMPTS_TABLE)
            .where(
                WORK_ATTEMPTS_TABLE.c.work_id == claim.work_id,
                WORK_ATTEMPTS_TABLE.c.attempt_no == claim.attempt_no,
                WORK_ATTEMPTS_TABLE.c.owner_id == claim.owner_id,
                WORK_ATTEMPTS_TABLE.c.fence == claim.fence,
                WORK_ATTEMPTS_TABLE.c.finished_at.is_(None),
            )
            .values(trace_id=trace_id, span_id=span_id)
        )
        if result.rowcount != 1:
            raise StaleWorkClaimError(claim.work_id)

    async def complete(
        self,
        connection: AsyncConnection,
        claim: WorkClaim,
        result: WorkResult | None,
        /,
        *,
        commit: WorkCommit | None = None,
    ) -> StoredWork:
        if result is None and commit is None:
            raise InvalidRepositoryArgumentError("result", "result or commit is required")
        await _lock_lane(connection, claim.lane_key)
        await _lock_logical_key(connection, claim.logical_key, claim.work_id)
        work = await _lock_work(connection, claim.work_id)
        now = await database_now(connection)
        _verify_claim(work, claim, now)
        if work.cancel_requested or work.status is WorkStatus.CANCELLING:
            raise StaleWorkClaimError(claim.work_id)
        if commit is not None:
            committed_result = await commit(connection)
            if committed_result is not None:
                result = committed_result
        if result is None:
            raise InvalidRepositoryArgumentError("result", "commit did not produce a result")
        result_payload = _dump_payload(result.payload, kind="work-result", name=claim.work_id)
        await connection.execute(
            update(WORK_ITEMS_TABLE)
            .where(WORK_ITEMS_TABLE.c.work_id == claim.work_id)
            .values(
                status=WorkStatus.SUCCEEDED.value,
                lease_owner=None,
                lease_expires_at=None,
                result_code=result.code,
                result_payload=result_payload,
                error_category=None,
                error_code=None,
                state_version=work.state_version + 1,
                updated_at=now,
                completed_at=now,
            )
        )
        await self._finish_attempt(connection, claim, now=now, outcome="succeeded")
        self._observe_attempt(claim, now=now, outcome="succeeded")
        await _release_logical_key(connection, work)
        await _advance_lane(connection, work)
        return await self.get(connection, claim.work_id)

    async def fail(
        self,
        connection: AsyncConnection,
        claim: WorkClaim,
        failure: WorkFailure,
        /,
        *,
        retry_delay_seconds: int,
    ) -> StoredWork:
        _require_nonnegative("retry_delay_seconds", retry_delay_seconds)
        await _lock_lane(connection, claim.lane_key)
        await _lock_logical_key(connection, claim.logical_key, claim.work_id)
        work = await _lock_work(connection, claim.work_id)
        now = await database_now(connection)
        _verify_claim(work, claim, now)
        if work.cancel_requested or work.status is WorkStatus.CANCELLING:
            return await self._cancel_running(connection, work, claim=claim, now=now)
        retry = failure.retryable and work.generation_attempt_count < work.max_attempts
        status = WorkStatus.RETRY_WAIT if retry else WorkStatus.FAILED
        await connection.execute(
            update(WORK_ITEMS_TABLE)
            .where(WORK_ITEMS_TABLE.c.work_id == claim.work_id)
            .values(
                status=status.value,
                available_at=now + timedelta(seconds=retry_delay_seconds) if retry else work.available_at,
                lease_owner=None,
                lease_expires_at=None,
                error_category=failure.category,
                error_code=failure.code,
                state_version=work.state_version + 1,
                updated_at=now,
            )
        )
        await self._finish_attempt(
            connection,
            claim,
            now=now,
            outcome="retry_wait" if retry else "failed",
            failure=failure,
        )
        self._observe_attempt(
            claim,
            now=now,
            outcome="retry_wait" if retry else "failed",
            error_category=failure.category,
        )
        return await self.get(connection, claim.work_id)

    async def retry(self, connection: AsyncConnection, work_id: str, /, *, expected_version: int) -> StoredWork:
        _require_work_id(work_id)
        _require_positive("expected_version", expected_version)
        unlocked = await self.get(connection, work_id)
        await _lock_lane(connection, unlocked.lane_key)
        await _lock_logical_key(connection, unlocked.logical_key, work_id)
        work = await _lock_work(connection, work_id)
        _verify_version(work, expected_version)
        if work.status is not WorkStatus.FAILED:
            raise WorkStateConflictError(work_id, expected_version, work.status, work.state_version)
        now = await database_now(connection)
        await connection.execute(
            update(WORK_ITEMS_TABLE)
            .where(WORK_ITEMS_TABLE.c.work_id == work_id)
            .values(
                status=WorkStatus.QUEUED.value,
                available_at=now,
                generation_attempt_count=0,
                recovery_generation=work.recovery_generation + 1,
                cancel_requested=False,
                error_category=None,
                error_code=None,
                state_version=work.state_version + 1,
                updated_at=now,
            )
        )
        return await self.get(connection, work_id)

    async def cancel(self, connection: AsyncConnection, work_id: str, /, *, expected_version: int) -> StoredWork:
        _require_work_id(work_id)
        _require_positive("expected_version", expected_version)
        unlocked = await self.get(connection, work_id)
        await _lock_lane(connection, unlocked.lane_key)
        logical_key_locked = await _lock_logical_key_if_present(connection, unlocked.logical_key)
        work = await _lock_work(connection, work_id)
        _verify_version(work, expected_version)
        if work.status in {WorkStatus.CANCELLING, WorkStatus.SUCCEEDED, WorkStatus.CANCELLED}:
            raise WorkStateConflictError(work_id, expected_version, work.status, work.state_version)
        if not logical_key_locked:
            raise StaleWorkClaimError(work_id)
        now = await database_now(connection)
        if work.status is WorkStatus.RUNNING:
            await connection.execute(
                update(WORK_ITEMS_TABLE)
                .where(WORK_ITEMS_TABLE.c.work_id == work_id)
                .values(
                    status=WorkStatus.CANCELLING.value,
                    cancel_requested=True,
                    state_version=work.state_version + 1,
                    updated_at=now,
                )
            )
        else:
            await connection.execute(
                update(WORK_ITEMS_TABLE)
                .where(WORK_ITEMS_TABLE.c.work_id == work_id)
                .values(
                    status=WorkStatus.CANCELLED.value,
                    cancel_requested=True,
                    lease_owner=None,
                    lease_expires_at=None,
                    state_version=work.state_version + 1,
                    updated_at=now,
                    completed_at=now,
                )
            )
            await _release_logical_key(connection, work)
            await _advance_lane(connection, work)
        return await self.get(connection, work_id)

    async def get(self, connection: AsyncConnection, work_id: str, /) -> StoredWork:
        _require_work_id(work_id)
        row = (
            (await connection.execute(select(WORK_ITEMS_TABLE).where(WORK_ITEMS_TABLE.c.work_id == work_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RepositoryNotFoundError("work", work_id)
        return _decode_work(row)

    async def list(
        self,
        connection: AsyncConnection,
        /,
        *,
        scope_id: str | None = None,
        kind: str | None = None,
        allowed_kinds: frozenset[str] | None = None,
        status: WorkStatus | None = None,
        limit: int = 100,
        before: tuple[datetime, str] | None = None,
    ) -> tuple[StoredWork, ...]:
        statement = _work_list_statement(
            scope_id=scope_id,
            kind=kind,
            allowed_kinds=allowed_kinds,
            status=status,
            before=before,
            limit=limit,
        )
        if statement is None:
            return ()
        rows = (
            (
                await connection.execute(
                    statement.order_by(WORK_ITEMS_TABLE.c.created_at.desc(), WORK_ITEMS_TABLE.c.work_id.desc()).limit(
                        limit
                    )
                )
            )
            .mappings()
            .all()
        )
        return tuple(_decode_work(row) for row in rows)

    async def purge_terminal(
        self,
        connection: AsyncConnection,
        /,
        *,
        completed_before: datetime,
        limit: int = 500,
    ) -> int:
        """Delete one bounded batch of successful or cancelled operation history."""

        if limit < 1 or limit > 500:
            raise InvalidRepositoryArgumentError("limit", "must be between 1 and 500")
        cutoff = _normalized_datetime(completed_before)
        work_ids = tuple(
            str(value)
            for value in (
                await connection.scalars(
                    select(WORK_ITEMS_TABLE.c.work_id)
                    .where(
                        WORK_ITEMS_TABLE.c.status.in_(_TERMINAL_STATUSES),
                        WORK_ITEMS_TABLE.c.completed_at.is_not(None),
                        WORK_ITEMS_TABLE.c.completed_at < cutoff,
                    )
                    .order_by(WORK_ITEMS_TABLE.c.completed_at, WORK_ITEMS_TABLE.c.work_id)
                    .limit(limit)
                )
            ).all()
        )
        if not work_ids:
            return 0
        await connection.execute(delete(WORK_ATTEMPTS_TABLE).where(WORK_ATTEMPTS_TABLE.c.work_id.in_(work_ids)))
        result = await connection.execute(
            delete(WORK_ITEMS_TABLE).where(
                WORK_ITEMS_TABLE.c.work_id.in_(work_ids),
                WORK_ITEMS_TABLE.c.status.in_(_TERMINAL_STATUSES),
                WORK_ITEMS_TABLE.c.completed_at < cutoff,
            )
        )
        return result.rowcount

    async def unsupported_head_count(
        self,
        connection: AsyncConnection,
        supported: Mapping[str, frozenset[int]],
        /,
    ) -> int:
        """Count visible lane heads that no registered handler can decode."""

        _validate_supported(supported)
        supported_pairs = [
            and_(WORK_ITEMS_TABLE.c.kind == kind, WORK_ITEMS_TABLE.c.payload_version.in_(versions))
            for kind, versions in supported.items()
        ]
        compatible = or_(*supported_pairs) if supported_pairs else false()
        value = await connection.scalar(
            select(func.count())
            .select_from(WORK_ITEMS_TABLE)
            .join(
                WORK_LANES_TABLE,
                and_(
                    WORK_LANES_TABLE.c.lane_key == WORK_ITEMS_TABLE.c.lane_key,
                    WORK_LANES_TABLE.c.head_sequence == WORK_ITEMS_TABLE.c.lane_sequence,
                ),
            )
            .where(
                WORK_ITEMS_TABLE.c.status.not_in(_TERMINAL_STATUSES),
                not_(compatible),
            )
        )
        return int(value or 0)

    async def queue_statistics(self, connection: AsyncConnection, /) -> tuple[WorkQueueStatistic, ...]:
        """Aggregate non-terminal queue state without high-cardinality labels."""

        now = await database_now(connection)
        rows = (
            await connection.execute(
                select(
                    WORK_ITEMS_TABLE.c.kind,
                    WORK_ITEMS_TABLE.c.status,
                    func.count().label("depth"),
                    func.min(WORK_ITEMS_TABLE.c.created_at).label("oldest_created_at"),
                )
                .where(WORK_ITEMS_TABLE.c.status.not_in(_TERMINAL_STATUSES))
                .group_by(WORK_ITEMS_TABLE.c.kind, WORK_ITEMS_TABLE.c.status)
            )
        ).mappings()
        return tuple(
            WorkQueueStatistic(
                kind=str(row["kind"]),
                status=WorkStatus(str(row["status"])),
                depth=int(row["depth"]),
                oldest_age_seconds=max(
                    0.0,
                    (now - _stored_datetime(row["oldest_created_at"], "oldest_created_at")).total_seconds(),
                ),
            )
            for row in rows
        )

    async def _claim_locked(
        self,
        connection: AsyncConnection,
        work: StoredWork,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> WorkClaim:
        previous_trace_id, previous_span_id = await _previous_attempt_trace(connection, work.work_id)
        fence = work.lease_fence + 1
        attempt_no = work.attempt_count + 1
        expires_at = now + timedelta(seconds=lease_seconds)
        result = await connection.execute(
            update(WORK_ITEMS_TABLE)
            .where(
                WORK_ITEMS_TABLE.c.work_id == work.work_id,
                WORK_ITEMS_TABLE.c.state_version == work.state_version,
                WORK_ITEMS_TABLE.c.status == work.status.value,
            )
            .values(
                status=WorkStatus.RUNNING.value,
                lease_owner=worker_id,
                lease_fence=fence,
                lease_expires_at=expires_at,
                attempt_count=attempt_no,
                generation_attempt_count=work.generation_attempt_count + 1,
                state_version=work.state_version + 1,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise StaleWorkClaimError(work.work_id)
        await connection.execute(
            insert(WORK_ATTEMPTS_TABLE).values(
                work_id=work.work_id,
                attempt_no=attempt_no,
                recovery_generation=work.recovery_generation,
                owner_id=worker_id,
                fence=fence,
                started_at=now,
                heartbeat_at=now,
                finished_at=None,
                outcome=None,
                error_category=None,
                error_code=None,
                trace_id=None,
                span_id=None,
            )
        )
        self._observe_claim(work, now=now)
        return WorkClaim(
            work_id=work.work_id,
            logical_key=work.logical_key,
            lane_key=work.lane_key,
            lane_sequence=work.lane_sequence,
            kind=work.kind,
            payload_version=work.payload_version,
            scope_id=work.scope_id,
            payload=work.payload,
            owner_id=worker_id,
            fence=fence,
            attempt_no=attempt_no,
            generation_attempt_no=work.generation_attempt_count + 1,
            recovery_generation=work.recovery_generation,
            created_at=work.created_at,
            claimed_at=now,
            previous_trace_id=previous_trace_id,
            previous_span_id=previous_span_id,
            lease_expires_at=expires_at,
        )

    async def _claim_candidate(
        self,
        connection: AsyncConnection,
        work_id: str,
        *,
        worker_id: str,
        supported: Mapping[str, frozenset[int]],
        lease_seconds: int,
        expired_retry_delay_seconds: int,
    ) -> WorkClaim | None:
        unlocked = await self.get(connection, work_id)
        await _lock_lane(connection, unlocked.lane_key)
        await _lock_logical_key(connection, unlocked.logical_key, work_id)
        work = await _lock_work(connection, work_id)
        if not await _is_lane_head(connection, work):
            return None
        now = await database_now(connection)
        if work.status in {WorkStatus.RUNNING, WorkStatus.CANCELLING}:
            if work.lease_expires_at is None or work.lease_expires_at > now:
                return None
            await self._expire_claim(
                connection,
                work,
                now=now,
                retry_delay_seconds=expired_retry_delay_seconds,
            )
            return None
        if work.status not in {WorkStatus.QUEUED, WorkStatus.RETRY_WAIT} or work.available_at > now:
            return None
        if work.payload_version not in supported.get(work.kind, frozenset()):
            return None
        return await self._claim_locked(
            connection,
            work,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
        )

    async def _expire_claim(
        self,
        connection: AsyncConnection,
        work: StoredWork,
        *,
        now: datetime,
        retry_delay_seconds: int,
    ) -> None:
        claim = _claim_from_work(work)
        if work.status is WorkStatus.CANCELLING or work.cancel_requested:
            await self._cancel_running(connection, work, claim=claim, now=now)
            return
        retry = work.generation_attempt_count < work.max_attempts
        await connection.execute(
            update(WORK_ITEMS_TABLE)
            .where(WORK_ITEMS_TABLE.c.work_id == work.work_id)
            .values(
                status=WorkStatus.RETRY_WAIT.value if retry else WorkStatus.FAILED.value,
                available_at=now + timedelta(seconds=retry_delay_seconds) if retry else work.available_at,
                lease_owner=None,
                lease_expires_at=None,
                error_category="lease",
                error_code="lease_expired",
                state_version=work.state_version + 1,
                updated_at=now,
            )
        )
        await self._finish_attempt(
            connection,
            claim,
            now=now,
            outcome="lease_expired_retry" if retry else "lease_expired_failed",
            failure=WorkFailure(category="lease", code="lease_expired", retryable=retry),
        )
        self._observe_attempt(
            claim,
            now=now,
            outcome="lease_expired_retry" if retry else "lease_expired_failed",
            error_category="lease",
        )
        if self._observer is not None:
            with suppress(Exception):
                self._observer.observe_work_lease_expiry(
                    work.kind,
                    outcome="retry_wait" if retry else "failed",
                )

    async def _cancel_running(
        self,
        connection: AsyncConnection,
        work: StoredWork,
        *,
        claim: WorkClaim,
        now: datetime,
    ) -> StoredWork:
        await connection.execute(
            update(WORK_ITEMS_TABLE)
            .where(WORK_ITEMS_TABLE.c.work_id == work.work_id)
            .values(
                status=WorkStatus.CANCELLED.value,
                lease_owner=None,
                lease_expires_at=None,
                cancel_requested=True,
                state_version=work.state_version + 1,
                updated_at=now,
                completed_at=now,
            )
        )
        await self._finish_attempt(connection, claim, now=now, outcome="cancelled")
        self._observe_attempt(claim, now=now, outcome="cancelled")
        await _release_logical_key(connection, work)
        await _advance_lane(connection, work)
        return await self.get(connection, work.work_id)

    async def _finish_attempt(
        self,
        connection: AsyncConnection,
        claim: WorkClaim,
        *,
        now: datetime,
        outcome: str,
        failure: WorkFailure | None = None,
    ) -> None:
        result = await connection.execute(
            update(WORK_ATTEMPTS_TABLE)
            .where(
                WORK_ATTEMPTS_TABLE.c.work_id == claim.work_id,
                WORK_ATTEMPTS_TABLE.c.attempt_no == claim.attempt_no,
                WORK_ATTEMPTS_TABLE.c.owner_id == claim.owner_id,
                WORK_ATTEMPTS_TABLE.c.fence == claim.fence,
                WORK_ATTEMPTS_TABLE.c.finished_at.is_(None),
            )
            .values(
                heartbeat_at=now,
                finished_at=now,
                outcome=outcome,
                error_category=None if failure is None else failure.category,
                error_code=None if failure is None else failure.code,
            )
        )
        if result.rowcount != 1:
            raise StaleWorkClaimError(claim.work_id)

    def _observe_enqueue(self, kind: str, *, created: bool) -> None:
        if self._observer is not None:
            with suppress(Exception):
                self._observer.observe_work_enqueue(kind, created=created)

    def _observe_claim(self, work: StoredWork, *, now: datetime) -> None:
        if self._observer is not None:
            with suppress(Exception):
                self._observer.observe_work_claim(
                    work.kind,
                    latency_seconds=max(0.0, (now - work.created_at).total_seconds()),
                )

    def _observe_attempt(
        self,
        claim: WorkClaim,
        *,
        now: datetime,
        outcome: str,
        error_category: str = "none",
    ) -> None:
        if self._observer is not None:
            with suppress(Exception):
                self._observer.observe_work_attempt(
                    claim.kind,
                    outcome=outcome,
                    error_category=error_category,
                    duration_seconds=max(0.0, (now - claim.claimed_at).total_seconds()),
                )


async def _lock_or_create_lane(connection: AsyncConnection, lane_key: str) -> Mapping[Any, Any]:
    await connection.execute(
        update(WORK_LANES_TABLE)
        .where(WORK_LANES_TABLE.c.lane_key == lane_key)
        .values(next_sequence=WORK_LANES_TABLE.c.next_sequence)
    )
    row = await _lane_row(connection, lane_key)
    if row is not None:
        return row
    await insert_if_absent(
        connection,
        WORK_LANES_TABLE,
        {"lane_key": lane_key, "head_sequence": None, "next_sequence": 1},
    )
    row = await _lane_row(connection, lane_key)
    if row is None:
        raise InvalidStoredColumnError("lane_key", "an initialized work lane")
    return row


async def _previous_attempt_trace(connection: AsyncConnection, work_id: str) -> tuple[str | None, str | None]:
    row = (
        await connection.execute(
            select(WORK_ATTEMPTS_TABLE.c.trace_id, WORK_ATTEMPTS_TABLE.c.span_id)
            .where(WORK_ATTEMPTS_TABLE.c.work_id == work_id)
            .order_by(WORK_ATTEMPTS_TABLE.c.attempt_no.desc())
            .limit(1)
        )
    ).one_or_none()
    if row is None or (row.trace_id is None and row.span_id is None):
        return None, None
    if row.trace_id is None or row.span_id is None:
        raise InvalidStoredColumnError("attempt trace", "a complete trace/span pair")  # noqa: TRY003
    trace_id = str(row.trace_id)
    span_id = str(row.span_id)
    _require_hex("trace_id", trace_id, 32)
    _require_hex("span_id", span_id, 16)
    return trace_id, span_id


async def _lock_lane(connection: AsyncConnection, lane_key: str) -> None:
    await _lock_or_create_lane(connection, lane_key)


async def _lane_row(connection: AsyncConnection, lane_key: str) -> Mapping[Any, Any] | None:
    return (
        (
            await connection.execute(
                select(WORK_LANES_TABLE).where(WORK_LANES_TABLE.c.lane_key == lane_key).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )


async def _lock_logical_key(connection: AsyncConnection, logical_key: str, work_id: str) -> None:
    if not await _lock_logical_key_if_present(connection, logical_key):
        raise StaleWorkClaimError(work_id)


async def _lock_logical_key_if_present(connection: AsyncConnection, logical_key: str) -> bool:
    row = (
        await connection.execute(
            select(WORK_KEYS_TABLE.c.work_id).where(WORK_KEYS_TABLE.c.logical_key == logical_key).with_for_update()
        )
    ).one_or_none()
    return row is not None


async def _lock_work(connection: AsyncConnection, work_id: str) -> StoredWork:
    row = (
        (
            await connection.execute(
                select(WORK_ITEMS_TABLE).where(WORK_ITEMS_TABLE.c.work_id == work_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RepositoryNotFoundError("work", work_id)
    return _decode_work(row)


async def _work_for_logical_key(
    connection: AsyncConnection,
    logical_key: str,
    *,
    for_update: bool,
) -> StoredWork | None:
    statement = (
        select(WORK_ITEMS_TABLE)
        .join(WORK_KEYS_TABLE, WORK_KEYS_TABLE.c.work_id == WORK_ITEMS_TABLE.c.work_id)
        .where(WORK_KEYS_TABLE.c.logical_key == logical_key)
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    return None if row is None else _decode_work(row)


async def _is_lane_head(connection: AsyncConnection, work: StoredWork) -> bool:
    head = await connection.scalar(
        select(WORK_LANES_TABLE.c.head_sequence).where(WORK_LANES_TABLE.c.lane_key == work.lane_key)
    )
    return head == work.lane_sequence


async def _release_logical_key(connection: AsyncConnection, work: StoredWork) -> None:
    result = await connection.execute(
        delete(WORK_KEYS_TABLE).where(
            WORK_KEYS_TABLE.c.logical_key == work.logical_key,
            WORK_KEYS_TABLE.c.work_id == work.work_id,
        )
    )
    if result.rowcount != 1:
        raise StaleWorkClaimError(work.work_id)


async def _advance_lane(connection: AsyncConnection, work: StoredWork) -> None:
    next_sequence = await connection.scalar(
        select(WORK_ITEMS_TABLE.c.lane_sequence)
        .where(
            WORK_ITEMS_TABLE.c.lane_key == work.lane_key,
            WORK_ITEMS_TABLE.c.lane_sequence > work.lane_sequence,
            WORK_ITEMS_TABLE.c.status.not_in(_TERMINAL_STATUSES),
        )
        .order_by(WORK_ITEMS_TABLE.c.lane_sequence)
        .limit(1)
    )
    await connection.execute(
        update(WORK_LANES_TABLE)
        .where(
            WORK_LANES_TABLE.c.lane_key == work.lane_key,
            WORK_LANES_TABLE.c.head_sequence == work.lane_sequence,
        )
        .values(head_sequence=next_sequence)
    )


def _work_list_statement(
    *,
    scope_id: str | None,
    kind: str | None,
    allowed_kinds: frozenset[str] | None,
    status: WorkStatus | None,
    before: tuple[datetime, str] | None,
    limit: int,
) -> Any | None:
    if not _validate_work_list(scope_id=scope_id, kind=kind, allowed_kinds=allowed_kinds, limit=limit):
        return None

    statement = select(WORK_ITEMS_TABLE)
    if scope_id is not None:
        statement = statement.where(WORK_ITEMS_TABLE.c.scope_id == scope_id)
    if kind is not None:
        statement = statement.where(WORK_ITEMS_TABLE.c.kind == kind)
    if allowed_kinds is not None:
        statement = statement.where(WORK_ITEMS_TABLE.c.kind.in_(allowed_kinds))
    if status is not None:
        statement = statement.where(WORK_ITEMS_TABLE.c.status == status.value)
    if before is not None:
        created_at, work_id = before
        created_at = _normalized_datetime(created_at)
        _require_work_id(work_id)
        statement = statement.where(
            or_(
                WORK_ITEMS_TABLE.c.created_at < created_at,
                and_(WORK_ITEMS_TABLE.c.created_at == created_at, WORK_ITEMS_TABLE.c.work_id < work_id),
            )
        )
    return statement


def _validate_work_list(
    *,
    scope_id: str | None,
    kind: str | None,
    allowed_kinds: frozenset[str] | None,
    limit: int,
) -> bool:
    if scope_id is not None:
        _validated_text("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
    if kind is not None:
        _validated_text("kind", kind, 128)
    if allowed_kinds is not None:
        if not allowed_kinds:
            return False
        for allowed_kind in allowed_kinds:
            _validated_text("allowed_kind", allowed_kind, 128)
    if limit < 1 or limit > 100:
        raise InvalidRepositoryArgumentError("limit", "must be between 1 and 100")
    return True


def _decode_work(row: Mapping[Any, Any]) -> StoredWork:
    work_id = str(row["work_id"])
    return StoredWork(
        work_id=work_id,
        logical_key=str(row["logical_key"]),
        lane_key=str(row["lane_key"]),
        lane_sequence=_positive_integer(row["lane_sequence"], "lane_sequence"),
        kind=str(row["kind"]),
        payload_version=_positive_integer(row["payload_version"], "payload_version"),
        scope_id=str(row["scope_id"]),
        payload=_load_payload(row["payload"], kind="work", name=work_id),
        status=WorkStatus(str(row["status"])),
        available_at=_stored_datetime(row["available_at"], "available_at"),
        lease_owner=None if row["lease_owner"] is None else str(row["lease_owner"]),
        lease_fence=_nonnegative_integer(row["lease_fence"], "lease_fence"),
        lease_expires_at=_optional_datetime(row["lease_expires_at"], "lease_expires_at"),
        attempt_count=_nonnegative_integer(row["attempt_count"], "attempt_count"),
        generation_attempt_count=_nonnegative_integer(row["generation_attempt_count"], "generation_attempt_count"),
        recovery_generation=_nonnegative_integer(row["recovery_generation"], "recovery_generation"),
        max_attempts=_positive_integer(row["max_attempts"], "max_attempts"),
        cancel_requested=bool(row["cancel_requested"]),
        result_code=None if row["result_code"] is None else str(row["result_code"]),
        result_payload=(
            None
            if row["result_payload"] is None
            else _load_payload(row["result_payload"], kind="work-result", name=work_id)
        ),
        error_category=None if row["error_category"] is None else str(row["error_category"]),
        error_code=None if row["error_code"] is None else str(row["error_code"]),
        state_version=_positive_integer(row["state_version"], "state_version"),
        created_at=_stored_datetime(row["created_at"], "created_at"),
        updated_at=_stored_datetime(row["updated_at"], "updated_at"),
        completed_at=_optional_datetime(row["completed_at"], "completed_at"),
    )


def _verify_logical_identity(work: StoredWork, spec: WorkSpec) -> None:
    if work.kind != spec.kind or work.scope_id != spec.scope_id or work.lane_key != spec.lane_key:
        raise RepositoryError(  # noqa: TRY003
            f"logical work key {spec.logical_key!r} is bound to a different work identity"
        )


def _verify_claim(work: StoredWork, claim: WorkClaim, now: datetime) -> None:
    if (
        work.status not in {WorkStatus.RUNNING, WorkStatus.CANCELLING}
        or work.lease_owner != claim.owner_id
        or work.lease_fence != claim.fence
        or work.attempt_count != claim.attempt_no
        or work.lease_expires_at is None
        or work.lease_expires_at <= now
    ):
        raise StaleWorkClaimError(claim.work_id)


def _verify_version(work: StoredWork, expected_version: int) -> None:
    if work.state_version != expected_version:
        raise WorkStateConflictError(work.work_id, expected_version, work.status, work.state_version)


def _claim_from_work(work: StoredWork) -> WorkClaim:
    if work.lease_owner is None or work.lease_expires_at is None or work.attempt_count < 1:
        raise InvalidStoredColumnError("work lease", "a complete active lease")  # noqa: TRY003
    return WorkClaim(
        work_id=work.work_id,
        logical_key=work.logical_key,
        lane_key=work.lane_key,
        lane_sequence=work.lane_sequence,
        kind=work.kind,
        payload_version=work.payload_version,
        scope_id=work.scope_id,
        payload=work.payload,
        owner_id=work.lease_owner,
        fence=work.lease_fence,
        attempt_no=work.attempt_count,
        generation_attempt_no=work.generation_attempt_count,
        recovery_generation=work.recovery_generation,
        created_at=work.created_at,
        claimed_at=work.updated_at,
        previous_trace_id=None,
        previous_span_id=None,
        lease_expires_at=work.lease_expires_at,
    )


def _dump_payload(payload: dict[str, JsonValue], *, kind: str, name: str) -> bytes:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as error:
        raise InvalidStoredPayloadError(kind, name, "value is not JSON serializable") from error
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise InvalidRepositoryArgumentError("payload", f"must not exceed {_MAX_PAYLOAD_BYTES} encoded bytes")
    return encoded


def _load_payload(payload: object, *, kind: str, name: str) -> dict[str, JsonValue]:
    try:
        decoded = json.loads(stored_bytes(payload, column="payload"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidStoredPayloadError(kind, name, "payload is not valid JSON") from error
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise InvalidStoredPayloadError(kind, name, "payload is not an object")
    return decoded


def _stored_datetime(value: object, column: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidStoredColumnError(column, "a datetime")
    return _normalized_datetime(value)


def _optional_datetime(value: object, column: str) -> datetime | None:
    return None if value is None else _stored_datetime(value, column)


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _positive_integer(value: object, column: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidStoredColumnError(column, "a positive integer")
    return value


def _nonnegative_integer(value: object, column: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidStoredColumnError(column, "a non-negative integer")
    return value


def _require_positive(field: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidRepositoryArgumentError(field, "must be a positive integer")


def _require_nonnegative(field: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidRepositoryArgumentError(field, "must be a non-negative integer")


def _require_work_id(value: str) -> None:
    _validated_text("work_id", value, 36)


def _require_hex(field: str, value: str, length: int) -> None:
    if len(value) != length or any(character not in "0123456789abcdef" for character in value):
        raise InvalidRepositoryArgumentError(field, f"must be {length} lowercase hexadecimal characters")


def _validate_supported(supported: Mapping[str, frozenset[int]]) -> None:
    for kind, versions in supported.items():
        _validated_text("supported kind", kind, 128)
        if not versions or any(version < 1 for version in versions):
            raise InvalidRepositoryArgumentError("supported", "each kind must have positive payload versions")


def _validated_text(field: str, value: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed string")  # noqa: TRY003
    if len(value) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters")  # noqa: TRY003
    return value


__all__ = [
    "EnqueueResult",
    "StaleWorkClaimError",
    "StoredWork",
    "WorkClaim",
    "WorkCommit",
    "WorkFailure",
    "WorkQueueStatistic",
    "WorkRepository",
    "WorkResult",
    "WorkSpec",
    "WorkStateConflictError",
    "WorkStatus",
]
