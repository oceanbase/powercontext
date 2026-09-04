# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Persistent dirty-set primitives for Source-driven Artifact processing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import case, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.tables import (
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
        current = await self.load(connection, scope_id, binding_name, for_update=True)
        if current is None:
            await connection.execute(
                insert(ARTIFACT_PROCESSING_PENDING_TABLE).values(
                    binding_name=binding_name,
                    scope_id=scope_id,
                    source_through=source_position,
                    flush_generation=0,
                    handled_flush_generation=0,
                )
            )
        else:
            await connection.execute(
                update(ARTIFACT_PROCESSING_PENDING_TABLE)
                .where(
                    ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
                    ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == binding_name,
                )
                .values(
                    source_through=case(
                        (ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through < source_position, source_position),
                        else_=ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through,
                    )
                )
            )
        stored = await self.load(connection, scope_id, binding_name)
        assert stored is not None
        return stored

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
        current = await self.load(connection, scope_id, binding_name, for_update=True)
        if current is None:
            await connection.execute(
                insert(ARTIFACT_PROCESSING_PENDING_TABLE).values(
                    binding_name=binding_name,
                    scope_id=scope_id,
                    source_through=source_head,
                    flush_generation=1,
                    handled_flush_generation=0,
                )
            )
        else:
            await connection.execute(
                update(ARTIFACT_PROCESSING_PENDING_TABLE)
                .where(
                    ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
                    ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == binding_name,
                )
                .values(
                    source_through=case(
                        (ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through < source_head, source_head),
                        else_=ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through,
                    ),
                    flush_generation=ARTIFACT_PROCESSING_PENDING_TABLE.c.flush_generation + 1,
                )
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
    ) -> bool:
        if cursor is None:
            stored_cursor = await SourceCursorRepository().load(connection, scope_id, binding_name)
            cursor = 0 if stored_cursor is None else stored_cursor.cursor.sequence
        if cursor < 0:
            raise InvalidRepositoryArgumentError("cursor", "must be non-negative")
        result = await connection.execute(
            delete(ARTIFACT_PROCESSING_PENDING_TABLE).where(
                ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == binding_name,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.source_through <= cursor,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.handled_flush_generation
                == ARTIFACT_PROCESSING_PENDING_TABLE.c.flush_generation,
            )
        )
        return result.rowcount == 1


def _decode(row: Mapping[Any, Any]) -> StoredArtifactProcessingPending:
    return StoredArtifactProcessingPending(
        binding_name=str(row["binding_name"]),
        scope_id=str(row["scope_id"]),
        source_through=int(row["source_through"]),
        flush_generation=int(row["flush_generation"]),
        handled_flush_generation=int(row["handled_flush_generation"]),
    )


def _require_identifier(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")


def _require_position(field: str, value: int) -> None:
    if value < 1:
        raise InvalidRepositoryArgumentError(field, "must be positive")
