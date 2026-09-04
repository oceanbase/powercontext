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

"""Database-time coordination primitives for schedulers and role members."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import database_now, insert_if_absent
from powercontext.builtin.persistence.errors import (
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    RepositoryError,
)
from powercontext.builtin.persistence.tables import (
    RUNTIME_MEMBERS_TABLE,
    SCHEDULER_LEASES_TABLE,
    SCHEDULER_SCANS_TABLE,
)
from powercontext.limits import MAX_SCOPE_ID_LENGTH


class CoordinatorLease(BaseModel):
    """Monotonic fencing token held until database time reaches its expiry."""

    model_config = ConfigDict(frozen=True)

    lease_name: str
    owner_id: str
    fence: int
    lease_expires_at: datetime


class SchedulerScan(BaseModel):
    """Durable keyset continuation for one bounded discoverer scan."""

    model_config = ConfigDict(frozen=True)

    discoverer: str
    next_run_at: datetime
    continuation: str | None
    state_version: int
    updated_at: datetime


class RuntimeMember(BaseModel):
    """Non-sensitive compatibility advertisement for one live process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    member_id: str
    role: Literal["all", "api", "scheduler", "worker"]
    build_version: str
    schema_min: int
    schema_max: int
    payload_min: int
    payload_max: int
    behavior_revision: str
    heartbeat_at: datetime
    expires_at: datetime


class RuntimeMemberSpec(BaseModel):
    """Compatibility data supplied on member registration and heartbeat."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    member_id: str
    role: Literal["all", "api", "scheduler", "worker"]
    build_version: str
    schema_min: int
    schema_max: int
    payload_min: int
    payload_max: int
    behavior_revision: str


class StaleCoordinatorLeaseError(RepositoryError):
    """Raised when an old owner or fence attempts a coordinated write."""

    def __init__(self, lease_name: str) -> None:
        self.lease_name = lease_name
        super().__init__(f"coordinator lease {lease_name!r} is no longer current")


class StaleScanStateError(RepositoryError):
    """Raised when a scheduler scan continuation loses its version race."""

    def __init__(self, discoverer: str) -> None:
        self.discoverer = discoverer
        super().__init__(f"scheduler scan {discoverer!r} changed concurrently")


class CoordinationRepository:
    """Coordinate leases, scans, and role discovery in caller transactions."""

    async def acquire_lease(
        self,
        connection: AsyncConnection,
        *,
        lease_name: str,
        owner_id: str,
        lease_seconds: int,
    ) -> CoordinatorLease | None:
        _require_text("lease_name", lease_name, 64)
        _require_text("owner_id", owner_id, 128)
        _require_positive("lease_seconds", lease_seconds)
        now = await database_now(connection)
        row = await _lock_or_create_lease(connection, lease_name, now)
        current_owner = None if row["owner_id"] is None else str(row["owner_id"])
        current_expiry = _optional_datetime(row["lease_expires_at"], "lease_expires_at")
        current_fence = _nonnegative_integer(row["fence"], "fence")
        if current_owner == owner_id and current_expiry is not None and current_expiry > now:
            fence = current_fence
        elif current_expiry is None or current_expiry <= now:
            fence = current_fence + 1
        else:
            return None
        expires_at = now + timedelta(seconds=lease_seconds)
        await connection.execute(
            update(SCHEDULER_LEASES_TABLE)
            .where(SCHEDULER_LEASES_TABLE.c.lease_name == lease_name)
            .values(owner_id=owner_id, fence=fence, lease_expires_at=expires_at, updated_at=now)
        )
        return CoordinatorLease(
            lease_name=lease_name,
            owner_id=owner_id,
            fence=fence,
            lease_expires_at=expires_at,
        )

    async def assert_lease(self, connection: AsyncConnection, lease: CoordinatorLease, /) -> None:
        row = await _lock_lease(connection, lease.lease_name)
        now = await database_now(connection)
        expires_at = None if row is None else _optional_datetime(row["lease_expires_at"], "lease_expires_at")
        if (
            row is None
            or row["owner_id"] != lease.owner_id
            or row["fence"] != lease.fence
            or expires_at is None
            or expires_at <= now
        ):
            raise StaleCoordinatorLeaseError(lease.lease_name)

    async def release_lease(self, connection: AsyncConnection, lease: CoordinatorLease, /) -> bool:
        now = await database_now(connection)
        result = await connection.execute(
            update(SCHEDULER_LEASES_TABLE)
            .where(
                SCHEDULER_LEASES_TABLE.c.lease_name == lease.lease_name,
                SCHEDULER_LEASES_TABLE.c.owner_id == lease.owner_id,
                SCHEDULER_LEASES_TABLE.c.fence == lease.fence,
            )
            .values(owner_id=None, lease_expires_at=now, updated_at=now)
        )
        return result.rowcount == 1

    async def load_scan(self, connection: AsyncConnection, discoverer: str, /) -> SchedulerScan | None:
        _require_text("discoverer", discoverer, 128)
        row = (
            (
                await connection.execute(
                    select(SCHEDULER_SCANS_TABLE).where(SCHEDULER_SCANS_TABLE.c.discoverer == discoverer)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_scan(row)

    async def save_scan(
        self,
        connection: AsyncConnection,
        discoverer: str,
        /,
        *,
        next_run_at: datetime,
        continuation: str | None,
        expected_version: int | None,
    ) -> SchedulerScan:
        _require_text("discoverer", discoverer, 128)
        if continuation is not None:
            _require_text("continuation", continuation, MAX_SCOPE_ID_LENGTH)
        next_run_at = _normalized_datetime(next_run_at)
        now = await database_now(connection)
        if expected_version is None:
            created = await insert_if_absent(
                connection,
                SCHEDULER_SCANS_TABLE,
                {
                    "discoverer": discoverer,
                    "next_run_at": next_run_at,
                    "continuation": continuation,
                    "state_version": 1,
                    "updated_at": now,
                },
            )
            if not created:
                raise StaleScanStateError(discoverer) from None
        else:
            _require_positive("expected_version", expected_version)
            result = await connection.execute(
                update(SCHEDULER_SCANS_TABLE)
                .where(
                    SCHEDULER_SCANS_TABLE.c.discoverer == discoverer,
                    SCHEDULER_SCANS_TABLE.c.state_version == expected_version,
                )
                .values(
                    next_run_at=next_run_at,
                    continuation=continuation,
                    state_version=expected_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise StaleScanStateError(discoverer)
        result = await self.load_scan(connection, discoverer)
        if result is None:
            raise InvalidStoredColumnError("discoverer", "a persisted scheduler scan")
        return result

    async def heartbeat_member(
        self,
        connection: AsyncConnection,
        spec: RuntimeMemberSpec,
        /,
        *,
        ttl_seconds: int,
    ) -> RuntimeMember:
        _validate_member(spec)
        _require_positive("ttl_seconds", ttl_seconds)
        now = await database_now(connection)
        expires_at = now + timedelta(seconds=ttl_seconds)
        values = {
            **spec.model_dump(mode="python"),
            "heartbeat_at": now,
            "expires_at": expires_at,
        }
        result = await connection.execute(
            update(RUNTIME_MEMBERS_TABLE).where(RUNTIME_MEMBERS_TABLE.c.member_id == spec.member_id).values(**values)
        )
        if result.rowcount != 1:
            created = await insert_if_absent(connection, RUNTIME_MEMBERS_TABLE, values)
            if not created:
                result = await connection.execute(
                    update(RUNTIME_MEMBERS_TABLE)
                    .where(RUNTIME_MEMBERS_TABLE.c.member_id == spec.member_id)
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise InvalidStoredColumnError("member_id", "a concurrently persisted runtime member")
        return RuntimeMember.model_validate(values)

    async def live_members(
        self,
        connection: AsyncConnection,
        /,
        *,
        role: Literal["all", "api", "scheduler", "worker"] | None = None,
    ) -> tuple[RuntimeMember, ...]:
        now = await database_now(connection)
        statement = select(RUNTIME_MEMBERS_TABLE).where(RUNTIME_MEMBERS_TABLE.c.expires_at > now)
        if role is not None:
            statement = statement.where(RUNTIME_MEMBERS_TABLE.c.role == role)
        rows = (await connection.execute(statement.order_by(RUNTIME_MEMBERS_TABLE.c.member_id))).mappings().all()
        return tuple(_decode_member(row) for row in rows)


async def _lock_or_create_lease(
    connection: AsyncConnection,
    lease_name: str,
    now: datetime,
) -> Mapping[Any, Any]:
    row = await _lock_lease(connection, lease_name)
    if row is not None:
        return row
    await insert_if_absent(
        connection,
        SCHEDULER_LEASES_TABLE,
        {
            "lease_name": lease_name,
            "owner_id": None,
            "fence": 0,
            "lease_expires_at": None,
            "updated_at": now,
        },
    )
    row = await _lock_lease(connection, lease_name)
    if row is None:
        raise InvalidStoredColumnError("lease_name", "an initialized coordinator lease")
    return row


async def _lock_lease(connection: AsyncConnection, lease_name: str) -> Mapping[Any, Any] | None:
    await connection.execute(
        update(SCHEDULER_LEASES_TABLE)
        .where(SCHEDULER_LEASES_TABLE.c.lease_name == lease_name)
        .values(fence=SCHEDULER_LEASES_TABLE.c.fence)
    )
    return (
        (
            await connection.execute(
                select(SCHEDULER_LEASES_TABLE)
                .where(SCHEDULER_LEASES_TABLE.c.lease_name == lease_name)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )


def _decode_scan(row: Mapping[Any, Any]) -> SchedulerScan:
    return SchedulerScan(
        discoverer=str(row["discoverer"]),
        next_run_at=_stored_datetime(row["next_run_at"], "next_run_at"),
        continuation=None if row["continuation"] is None else str(row["continuation"]),
        state_version=_positive_integer(row["state_version"], "state_version"),
        updated_at=_stored_datetime(row["updated_at"], "updated_at"),
    )


def _decode_member(row: Mapping[Any, Any]) -> RuntimeMember:
    return RuntimeMember.model_validate({
        "member_id": str(row["member_id"]),
        "role": str(row["role"]),
        "build_version": str(row["build_version"]),
        "schema_min": _positive_integer(row["schema_min"], "schema_min"),
        "schema_max": _positive_integer(row["schema_max"], "schema_max"),
        "payload_min": _positive_integer(row["payload_min"], "payload_min"),
        "payload_max": _positive_integer(row["payload_max"], "payload_max"),
        "behavior_revision": str(row["behavior_revision"]),
        "heartbeat_at": _stored_datetime(row["heartbeat_at"], "heartbeat_at"),
        "expires_at": _stored_datetime(row["expires_at"], "expires_at"),
    })


def _validate_member(spec: RuntimeMemberSpec) -> None:
    _require_text("member_id", spec.member_id, 128)
    _require_text("build_version", spec.build_version, 64)
    _require_text("behavior_revision", spec.behavior_revision, 128)
    for name in ("schema_min", "schema_max", "payload_min", "payload_max"):
        _require_positive(name, getattr(spec, name))
    if spec.schema_max < spec.schema_min:
        raise InvalidRepositoryArgumentError("schema_max", "must not be less than schema_min")
    if spec.payload_max < spec.payload_min:
        raise InvalidRepositoryArgumentError("payload_max", "must not be less than payload_min")


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


def _require_text(field: str, value: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")


__all__ = [
    "CoordinationRepository",
    "CoordinatorLease",
    "RuntimeMember",
    "RuntimeMemberSpec",
    "SchedulerScan",
    "StaleCoordinatorLeaseError",
    "StaleScanStateError",
]
