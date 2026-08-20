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

"""Source-window cursor persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import (
    GenerationConflictError,
    InvalidRepositoryArgumentError,
)
from powercontext.builtin.persistence.tables import SOURCE_CURSORS_TABLE
from powercontext.builtin.sources import SourceCursor
from powercontext.limits import MAX_BINDING_NAME_LENGTH, MAX_SCOPE_ID_LENGTH


class StoredSourceCursor(BaseModel):
    """A decoded Source cursor at one compare-and-swap generation."""

    scope_id: str
    binding_name: str
    cursor: SourceCursor
    generation: int


class SourceCursorRepository:
    """Persist source-window cursors with generation CAS."""

    async def load(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        /,
    ) -> StoredSourceCursor | None:
        _require_identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        row = (
            (
                await connection.execute(
                    select(SOURCE_CURSORS_TABLE).where(
                        SOURCE_CURSORS_TABLE.c.scope_id == scope_id,
                        SOURCE_CURSORS_TABLE.c.binding_name == binding_name,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_row(row)

    async def save(
        self,
        connection: AsyncConnection,
        scope_id: str,
        binding_name: str,
        cursor: SourceCursor,
        /,
        *,
        expected_generation: int | None,
    ) -> StoredSourceCursor:
        _require_identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        _require_identifier("binding_name", binding_name, MAX_BINDING_NAME_LENGTH)
        if expected_generation is not None and expected_generation < 1:
            raise InvalidRepositoryArgumentError("expected_generation", "must be positive")
        payload = dump_model(cursor, kind="source-cursor", name=binding_name)
        if expected_generation is None:
            existing = await self.load(connection, scope_id, binding_name)
            if existing is not None:
                raise GenerationConflictError(binding_name, None, existing.generation)
            generation = 1
            await connection.execute(
                insert(SOURCE_CURSORS_TABLE).values(
                    scope_id=scope_id,
                    binding_name=binding_name,
                    cursor=payload,
                    generation=generation,
                )
            )
        else:
            generation = expected_generation + 1
            result = await connection.execute(
                update(SOURCE_CURSORS_TABLE)
                .where(
                    SOURCE_CURSORS_TABLE.c.scope_id == scope_id,
                    SOURCE_CURSORS_TABLE.c.binding_name == binding_name,
                    SOURCE_CURSORS_TABLE.c.generation == expected_generation,
                )
                .values(cursor=payload, generation=generation)
            )
            if result.rowcount != 1:
                actual = await self.load(connection, scope_id, binding_name)
                raise GenerationConflictError(
                    binding_name,
                    expected_generation,
                    None if actual is None else actual.generation,
                )
        return StoredSourceCursor(
            scope_id=scope_id,
            binding_name=binding_name,
            cursor=cursor,
            generation=generation,
        )


def _decode_row(row: Mapping[Any, Any]) -> StoredSourceCursor:
    return StoredSourceCursor(
        scope_id=str(row["scope_id"]),
        binding_name=str(row["binding_name"]),
        cursor=load_model(
            SourceCursor,
            stored_bytes(row["cursor"], column="cursor"),
            kind="source-cursor",
            name=str(row["binding_name"]),
        ),
        generation=int(row["generation"]),
    )


def _require_identifier(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")
