# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Persistent leadership and automatic-wave scheduling state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.errors import (
    ArtifactProcessingLeadershipLostError,
    InvalidRepositoryArgumentError,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
    ARTIFACT_PROCESSING_LEASES_TABLE,
)
from powercontext.limits import MAX_BINDING_NAME_LENGTH

GLOBAL_ARTIFACT_PROCESSING_SUPERVISOR_GROUP = "global"
ArtifactProcessingLeaseMode = Literal["single-process", "oceanbase"]


class ArtifactProcessingFence(BaseModel):
    """The exact Supervisor leadership term carried by a Worker assignment."""

    supervisor_group: str
    holder_id: str
    supervisor_generation: int
    lease_mode: ArtifactProcessingLeaseMode


class StoredArtifactProcessingLease(BaseModel):
    """One persisted Supervisor leadership term."""

    supervisor_group: str
    holder_id: str
    supervisor_generation: int
    lease_expires_at: datetime | None

    def fence(self, lease_mode: ArtifactProcessingLeaseMode) -> ArtifactProcessingFence:
        """Freeze this term into a Worker-safe fencing token."""

        return ArtifactProcessingFence(
            supervisor_group=self.supervisor_group,
            holder_id=self.holder_id,
            supervisor_generation=self.supervisor_generation,
            lease_mode=lease_mode,
        )


class StoredArtifactProcessingBindingState(BaseModel):
    """The persisted scheduling baseline for one registered binding."""

    binding_name: str
    last_auto_wave_completed_at: datetime | None


class ArtifactProcessingLeaseRepository:
    """Acquire, renew, and validate the fixed global Supervisor Lease."""

    async def load(
        self,
        connection: AsyncConnection,
        supervisor_group: str = GLOBAL_ARTIFACT_PROCESSING_SUPERVISOR_GROUP,
        /,
        *,
        for_update: bool = False,
    ) -> StoredArtifactProcessingLease | None:
        _require_identifier("supervisor_group", supervisor_group, 64)
        statement = select(ARTIFACT_PROCESSING_LEASES_TABLE).where(
            ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == supervisor_group
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _decode_lease(row)

    async def start_single_process_term(
        self,
        connection: AsyncConnection,
        holder_id: str,
        /,
        *,
        supervisor_group: str = GLOBAL_ARTIFACT_PROCESSING_SUPERVISOR_GROUP,
    ) -> StoredArtifactProcessingLease:
        """Start a new non-expiring SQLite/embedded term."""

        _require_identifier("holder_id", holder_id, 36)
        current = await self.load(connection, supervisor_group, for_update=True)
        generation = 1 if current is None else current.supervisor_generation + 1
        if current is None:
            await connection.execute(
                insert(ARTIFACT_PROCESSING_LEASES_TABLE).values(
                    supervisor_group=supervisor_group,
                    holder_id=holder_id,
                    supervisor_generation=generation,
                    lease_expires_at=None,
                )
            )
        else:
            await connection.execute(
                update(ARTIFACT_PROCESSING_LEASES_TABLE)
                .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == supervisor_group)
                .values(
                    holder_id=holder_id,
                    supervisor_generation=generation,
                    lease_expires_at=None,
                )
            )
        return StoredArtifactProcessingLease(
            supervisor_group=supervisor_group,
            holder_id=holder_id,
            supervisor_generation=generation,
            lease_expires_at=None,
        )

    async def try_acquire(
        self,
        connection: AsyncConnection,
        holder_id: str,
        lease_seconds: float,
        /,
        *,
        supervisor_group: str = GLOBAL_ARTIFACT_PROCESSING_SUPERVISOR_GROUP,
    ) -> StoredArtifactProcessingLease | None:
        """Atomically acquire an absent or expired OceanBase Lease."""

        _require_identifier("holder_id", holder_id, 36)
        _require_positive("lease_seconds", lease_seconds)
        now = await database_utc_now(connection)
        current = await self.load(connection, supervisor_group, for_update=True)
        if current is None:
            lease = StoredArtifactProcessingLease(
                supervisor_group=supervisor_group,
                holder_id=holder_id,
                supervisor_generation=1,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            try:
                async with connection.begin_nested():
                    await connection.execute(insert(ARTIFACT_PROCESSING_LEASES_TABLE).values(**lease.model_dump()))
            except IntegrityError:
                current = await self.load(connection, supervisor_group, for_update=True)
                if current is None:
                    raise
            else:
                return lease
        if current is None or current.lease_expires_at is None or current.lease_expires_at > now:
            return None
        generation = current.supervisor_generation + 1
        expires_at = now + timedelta(seconds=lease_seconds)
        result = await connection.execute(
            update(ARTIFACT_PROCESSING_LEASES_TABLE)
            .where(
                ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == supervisor_group,
                ARTIFACT_PROCESSING_LEASES_TABLE.c.holder_id == current.holder_id,
                ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_generation == current.supervisor_generation,
                ARTIFACT_PROCESSING_LEASES_TABLE.c.lease_expires_at == current.lease_expires_at,
            )
            .values(
                holder_id=holder_id,
                supervisor_generation=generation,
                lease_expires_at=expires_at,
            )
        )
        if result.rowcount != 1:
            return None
        return StoredArtifactProcessingLease(
            supervisor_group=supervisor_group,
            holder_id=holder_id,
            supervisor_generation=generation,
            lease_expires_at=expires_at,
        )

    async def renew(
        self,
        connection: AsyncConnection,
        fence: ArtifactProcessingFence,
        lease_seconds: float,
        /,
    ) -> StoredArtifactProcessingLease:
        """Renew an unexpired OceanBase Lease without changing its generation."""

        if fence.lease_mode != "oceanbase":
            raise InvalidRepositoryArgumentError("lease_mode", "only OceanBase leases are renewable")
        _require_positive("lease_seconds", lease_seconds)
        now = await database_utc_now(connection)
        expires_at = now + timedelta(seconds=lease_seconds)
        result = await connection.execute(
            update(ARTIFACT_PROCESSING_LEASES_TABLE)
            .where(
                ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == fence.supervisor_group,
                ARTIFACT_PROCESSING_LEASES_TABLE.c.holder_id == fence.holder_id,
                ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_generation == fence.supervisor_generation,
                ARTIFACT_PROCESSING_LEASES_TABLE.c.lease_expires_at.is_not(None),
                ARTIFACT_PROCESSING_LEASES_TABLE.c.lease_expires_at > now,
            )
            .values(lease_expires_at=expires_at)
        )
        if result.rowcount != 1:
            raise _leadership_lost(fence)
        return StoredArtifactProcessingLease(
            supervisor_group=fence.supervisor_group,
            holder_id=fence.holder_id,
            supervisor_generation=fence.supervisor_generation,
            lease_expires_at=expires_at,
        )

    async def require_fence(
        self,
        connection: AsyncConnection,
        fence: ArtifactProcessingFence,
        /,
    ) -> StoredArtifactProcessingLease:
        """Lock and validate the authoritative term inside a publication transaction."""

        current = await self.load(connection, fence.supervisor_group, for_update=True)
        if (
            current is None
            or current.holder_id != fence.holder_id
            or current.supervisor_generation != fence.supervisor_generation
        ):
            raise _leadership_lost(fence)
        if fence.lease_mode == "single-process":
            if current.lease_expires_at is not None:
                raise _leadership_lost(fence)
            return current
        now = await database_utc_now(connection)
        if current.lease_expires_at is None or current.lease_expires_at <= now:
            raise _leadership_lost(fence)
        return current


class ArtifactProcessingBindingStateRepository:
    """Persist the database-time completion baseline for automatic waves."""

    async def load(
        self,
        connection: AsyncConnection,
        binding_name: str,
        /,
        *,
        for_update: bool = False,
    ) -> StoredArtifactProcessingBindingState | None:
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        statement = select(ARTIFACT_PROCESSING_BINDING_STATES_TABLE).where(
            ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.binding_name == binding_name
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _decode_binding_state(row)

    async def mark_auto_wave_completed(
        self,
        connection: AsyncConnection,
        binding_name: str,
        /,
    ) -> StoredArtifactProcessingBindingState:
        """Set a binding's completion baseline from authoritative database UTC."""

        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        completed_at = await database_utc_now(connection)
        current = await self.load(connection, binding_name, for_update=True)
        if current is None:
            try:
                async with connection.begin_nested():
                    await connection.execute(
                        insert(ARTIFACT_PROCESSING_BINDING_STATES_TABLE).values(
                            binding_name=binding_name,
                            last_auto_wave_completed_at=completed_at,
                        )
                    )
            except IntegrityError:
                await connection.execute(
                    update(ARTIFACT_PROCESSING_BINDING_STATES_TABLE)
                    .where(ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.binding_name == binding_name)
                    .values(last_auto_wave_completed_at=completed_at)
                )
        else:
            await connection.execute(
                update(ARTIFACT_PROCESSING_BINDING_STATES_TABLE)
                .where(ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.binding_name == binding_name)
                .values(last_auto_wave_completed_at=completed_at)
            )
        return StoredArtifactProcessingBindingState(
            binding_name=binding_name,
            last_auto_wave_completed_at=completed_at,
        )


async def database_utc_now(connection: AsyncConnection) -> datetime:
    """Read current UTC from the authoritative database connection."""

    expression = func.utc_timestamp(6) if connection.dialect.name == "mysql" else func.current_timestamp()
    value = await connection.scalar(select(expression))
    if not isinstance(value, datetime):
        raise InvalidRepositoryArgumentError("database_time", "database did not return a datetime")
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _decode_lease(row: Mapping[Any, Any]) -> StoredArtifactProcessingLease:
    return StoredArtifactProcessingLease(
        supervisor_group=str(row["supervisor_group"]),
        holder_id=str(row["holder_id"]),
        supervisor_generation=int(row["supervisor_generation"]),
        lease_expires_at=row["lease_expires_at"],
    )


def _decode_binding_state(row: Mapping[Any, Any]) -> StoredArtifactProcessingBindingState:
    return StoredArtifactProcessingBindingState(
        binding_name=str(row["binding_name"]),
        last_auto_wave_completed_at=row["last_auto_wave_completed_at"],
    )


def _leadership_lost(fence: ArtifactProcessingFence) -> ArtifactProcessingLeadershipLostError:
    return ArtifactProcessingLeadershipLostError(
        fence.supervisor_group,
        fence.holder_id,
        fence.supervisor_generation,
    )


def _require_identifier(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")


def _require_positive(field: str, value: float) -> None:
    if value <= 0:
        raise InvalidRepositoryArgumentError(field, "must be positive")


__all__ = [
    "GLOBAL_ARTIFACT_PROCESSING_SUPERVISOR_GROUP",
    "ArtifactProcessingBindingStateRepository",
    "ArtifactProcessingFence",
    "ArtifactProcessingLeaseMode",
    "ArtifactProcessingLeaseRepository",
    "StoredArtifactProcessingBindingState",
    "StoredArtifactProcessingLease",
    "database_utc_now",
]
