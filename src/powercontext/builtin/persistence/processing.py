# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Persistent dirty-set primitives for Source-driven Artifact processing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import and_, case, delete, exists, insert, literal, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE,
    ARTIFACT_PROCESSING_PENDING_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
)
from powercontext.limits import MAX_BINDING_NAME_LENGTH, MAX_SCOPE_ID_LENGTH


class StoredArtifactProcessingPending(BaseModel):
    """One binding/scope processing watermark and its flush generations."""

    binding_name: str
    scope_id: str
    source_through: int
    flush_generation: int
    handled_flush_generation: int


class StoredArtifactProcessingAutoWaveTarget(BaseModel):
    """One immutable target captured at automatic-wave start."""

    wave_id: str
    binding_name: str
    scope_id: str
    source_through: int
    completed: bool


class ArtifactProcessingPendingRepository:
    """Persist coalesced processing intent inside caller-owned transactions."""

    async def load(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        /,
        *,
        for_update: bool = False,
    ) -> StoredArtifactProcessingPending | None:
        _require_identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        statement = select(ARTIFACT_PROCESSING_PENDING_TABLE).where(
            ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
            ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == binding_name,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _decode(row)

    async def raise_source(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        source_position: int,
        /,
    ) -> StoredArtifactProcessingPending:
        _require_position("source_position", source_position)
        await _upsert_pending(
            connection,
            scope_id=scope_id,
            binding_name=binding_name,
            source_through=source_position,
            increment_flush=False,
        )
        stored = await self.load(connection, scope_id, binding_name)
        return cast(StoredArtifactProcessingPending, stored)

    async def scan(
        self,
        connection: AsyncConnection,
        /,
        *,
        binding_name: str | None = None,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
        for_update: bool = False,
    ) -> tuple[StoredArtifactProcessingPending, ...]:
        """Return one deterministic keyset page of the durable dirty set."""

        statement = select(ARTIFACT_PROCESSING_PENDING_TABLE)
        if binding_name is not None:
            _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
            statement = statement.where(ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == binding_name)
        if after is not None:
            if not isinstance(after, tuple) or len(after) != 2:
                raise InvalidRepositoryArgumentError("after", "must be a binding_name/scope_id tuple")
            after_binding_name, after_scope_id = after
            _require_identifier("after.binding_name", after_binding_name, MAX_BINDING_NAME_LENGTH)
            _require_identifier("after.scope_id", after_scope_id, MAX_SCOPE_ID_LENGTH)
            statement = statement.where(
                or_(
                    ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name > after_binding_name,
                    and_(
                        ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == after_binding_name,
                        ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id > after_scope_id,
                    ),
                )
            )
        statement = statement.order_by(
            ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name,
            ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id,
        )
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                raise InvalidRepositoryArgumentError("limit", "must be a positive integer")
            statement = statement.limit(limit)
        if for_update:
            statement = statement.with_for_update()
        rows = (await connection.execute(statement)).mappings()
        return tuple(_decode(row) for row in rows)

    async def has_unhandled_flush(
        self,
        connection: AsyncConnection,
        binding_name: str,
        /,
    ) -> bool:
        """Return whether a binding has an explicit flush awaiting a wave."""

        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        pending = ARTIFACT_PROCESSING_PENDING_TABLE
        return bool(
            await connection.scalar(
                select(
                    exists(
                        select(1).where(
                            pending.c.binding_name == binding_name,
                            pending.c.flush_generation > pending.c.handled_flush_generation,
                        )
                    )
                )
            )
        )

    async def request_flush(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        /,
    ) -> StoredArtifactProcessingPending | None:
        _require_identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        head = await connection.scalar(
            select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        )
        source_head = 0 if head is None else int(head)
        cursor = await SourceCursorRepository().load(connection, scope_id, binding_name)
        if (0 if cursor is None else cursor.cursor.sequence) >= source_head:
            return None
        await _upsert_pending(
            connection,
            scope_id=scope_id,
            binding_name=binding_name,
            source_through=source_head,
            increment_flush=True,
        )
        return await self.load(connection, scope_id, binding_name)

    async def mark_flush_handled(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        claimed_flush_generation: int,
        /,
    ) -> StoredArtifactProcessingPending | None:
        if claimed_flush_generation < 0:
            raise InvalidRepositoryArgumentError("claimed_flush_generation", "must be non-negative")
        current = await self.load(connection, scope_id, binding_name, for_update=True)
        if current is None:
            return None
        handled = max(current.handled_flush_generation, min(claimed_flush_generation, current.flush_generation))
        await connection.execute(
            update(ARTIFACT_PROCESSING_PENDING_TABLE)
            .where(
                ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == binding_name,
            )
            .values(handled_flush_generation=handled)
        )
        return await self.load(connection, scope_id, binding_name)

    async def delete_if_covered(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        /,
        *,
        cursor: int | None = None,
        source_through_limit: int | None = None,
    ) -> bool:
        if cursor is None:
            stored_cursor = await SourceCursorRepository().load(connection, scope_id, binding_name)
            cursor = 0 if stored_cursor is None else stored_cursor.cursor.sequence
        if cursor < 0:
            raise InvalidRepositoryArgumentError("cursor", "must be non-negative")
        if source_through_limit is not None and source_through_limit < 1:
            raise InvalidRepositoryArgumentError("source_through_limit", "must be positive")
        covered_through = cursor if source_through_limit is None else min(cursor, source_through_limit)
        result = await connection.execute(
            delete(ARTIFACT_PROCESSING_PENDING_TABLE).where(
                ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == binding_name,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through <= covered_through,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.handled_flush_generation
                == ARTIFACT_PROCESSING_PENDING_TABLE.c.flush_generation,
            )
        )
        return result.rowcount == 1


class ArtifactProcessingAutoWaveTargetRepository:
    """Persist an automatic-wave snapshot outside the bounded memory queue."""

    async def clear_all(self, connection: AsyncConnection, /) -> None:
        """Discard non-recoverable targets from an earlier Supervisor term."""

        await connection.execute(delete(ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE))

    async def freeze_pending(
        self,
        connection: AsyncConnection,
        wave_id: str,
        binding_name: str,
        /,
    ) -> None:
        """Freeze every current Pending member and watermark in one statement."""

        _require_identifier("wave_id", wave_id, 36)
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        pending = ARTIFACT_PROCESSING_PENDING_TABLE
        targets = ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE
        snapshot = select(
            literal(wave_id),
            pending.c.binding_name,
            pending.c.scope_id,
            pending.c.source_through,
            literal(False),
        ).where(pending.c.binding_name == binding_name)
        await connection.execute(
            insert(targets).from_select(
                (
                    targets.c.wave_id,
                    targets.c.binding_name,
                    targets.c.scope_id,
                    targets.c.source_through,
                    targets.c.completed,
                ),
                snapshot,
            )
        )

    async def scan(
        self,
        connection: AsyncConnection,
        wave_id: str,
        binding_name: str,
        /,
        *,
        after_scope_id: str | None = None,
        limit: int,
    ) -> tuple[StoredArtifactProcessingAutoWaveTarget, ...]:
        """Read one bounded keyset page from the immutable wave snapshot."""

        _require_identifier("wave_id", wave_id, 36)
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        if after_scope_id is not None:
            _require_identifier("after_scope_id", after_scope_id, MAX_SCOPE_ID_LENGTH)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise InvalidRepositoryArgumentError("limit", "must be a positive integer")
        targets = ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE
        statement = select(targets).where(
            targets.c.wave_id == wave_id,
            targets.c.binding_name == binding_name,
        )
        if after_scope_id is not None:
            statement = statement.where(targets.c.scope_id > after_scope_id)
        rows = (await connection.execute(statement.order_by(targets.c.scope_id).limit(limit))).mappings()
        return tuple(_decode_auto_wave_target(row) for row in rows)

    async def mark_completed(
        self,
        connection: AsyncConnection,
        wave_id: str,
        scope_id: str,
        /,
    ) -> bool:
        _require_identifier("wave_id", wave_id, 36)
        _require_identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        result = await connection.execute(
            update(ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE)
            .where(
                ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE.c.wave_id == wave_id,
                ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE.c.scope_id == scope_id,
            )
            .values(completed=True)
        )
        return result.rowcount == 1

    async def all_completed(
        self,
        connection: AsyncConnection,
        wave_id: str,
        /,
    ) -> bool:
        _require_identifier("wave_id", wave_id, 36)
        incomplete = exists(
            select(1).where(
                ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE.c.wave_id == wave_id,
                ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE.c.completed.is_(False),
            )
        )
        return not bool(await connection.scalar(select(incomplete)))

    async def delete_covered_pending(
        self,
        connection: AsyncConnection,
        wave_id: str,
        binding_name: str,
        /,
    ) -> int:
        """Delete only unchanged Pending rows represented by a completed wave."""

        _require_identifier("wave_id", wave_id, 36)
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        target = ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE
        pending = ARTIFACT_PROCESSING_PENDING_TABLE
        covered = exists(
            select(1).where(
                target.c.wave_id == wave_id,
                target.c.binding_name == pending.c.binding_name,
                target.c.scope_id == pending.c.scope_id,
                target.c.completed.is_(True),
                pending.c.source_through <= target.c.source_through,
            )
        )
        result = await connection.execute(
            delete(pending).where(
                pending.c.binding_name == binding_name,
                pending.c.handled_flush_generation == pending.c.flush_generation,
                covered,
            )
        )
        return result.rowcount

    async def clear_wave(
        self,
        connection: AsyncConnection,
        wave_id: str,
        /,
    ) -> None:
        _require_identifier("wave_id", wave_id, 36)
        await connection.execute(
            delete(ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE).where(
                ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE.c.wave_id == wave_id
            )
        )


def _decode(row: Mapping[Any, Any]) -> StoredArtifactProcessingPending:
    return StoredArtifactProcessingPending(
        binding_name=str(row["binding_name"]),
        scope_id=str(row["scope_id"]),
        source_through=int(row["source_through"]),
        flush_generation=int(row["flush_generation"]),
        handled_flush_generation=int(row["handled_flush_generation"]),
    )


def _decode_auto_wave_target(row: Mapping[Any, Any]) -> StoredArtifactProcessingAutoWaveTarget:
    return StoredArtifactProcessingAutoWaveTarget(
        wave_id=str(row["wave_id"]),
        binding_name=str(row["binding_name"]),
        scope_id=str(row["scope_id"]),
        source_through=int(row["source_through"]),
        completed=bool(row["completed"]),
    )


async def _upsert_pending(
    connection: AsyncConnection,
    *,
    scope_id: str,
    binding_name: str,
    source_through: int,
    increment_flush: bool,
) -> None:
    values = {
        "binding_name": binding_name,
        "scope_id": scope_id,
        "source_through": source_through,
        "flush_generation": int(increment_flush),
        "handled_flush_generation": 0,
    }
    dialect = connection.dialect.name
    if dialect == "sqlite":
        statement = sqlite_insert(ARTIFACT_PROCESSING_PENDING_TABLE).values(**values)
        incoming = statement.excluded
        changes = {
            "source_through": case(
                (
                    ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through < incoming.source_through,
                    incoming.source_through,
                ),
                else_=ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through,
            )
        }
        if increment_flush:
            changes["flush_generation"] = ARTIFACT_PROCESSING_PENDING_TABLE.c.flush_generation + 1
        statement = statement.on_conflict_do_update(
            index_elements=["binding_name", "scope_id"],
            set_=changes,
        )
    elif dialect == "mysql":
        statement = mysql_insert(ARTIFACT_PROCESSING_PENDING_TABLE).values(**values)
        incoming = statement.inserted
        changes = {
            "source_through": case(
                (
                    ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through < incoming.source_through,
                    incoming.source_through,
                ),
                else_=ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through,
            )
        }
        if increment_flush:
            changes["flush_generation"] = ARTIFACT_PROCESSING_PENDING_TABLE.c.flush_generation + 1
        statement = statement.on_duplicate_key_update(**changes)
    else:
        raise InvalidRepositoryArgumentError("dialect", f"unsupported database dialect: {dialect}")
    await connection.execute(statement)


def _require_identifier(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")


def _require_position(field: str, value: int) -> None:
    if value < 1:
        raise InvalidRepositoryArgumentError(field, "must be positive")
