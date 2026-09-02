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

"""Relational Source repository routed by actual adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import suppress
from typing import Any, Literal, cast

import rfc8785
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import (
    IdentityMismatchError,
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    InvalidStoredPayloadError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE, SOURCES_TABLE
from powercontext.errors import SourceDefinitionNotFoundError
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import Source, SourceAdapter, SourceDefinitionRegistry, SourceObservation, SourceRef

_AnySourceAdapter = SourceAdapter[Any, Any, Any]
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class _StoredSourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    encoding: Literal["powercontext-source-v1"]
    representation: Literal["native", "observation"]
    value: dict[str, JsonValue]


class StoredSource(BaseModel):
    """A decoded Source and its per-scope journal position."""

    ref: SourceRef
    value: Source
    journal_position: int


class SourceRepository:
    """Persist Sources using the Runtime's fixed Source Definition registry."""

    def __init__(
        self,
        definitions: SourceDefinitionRegistry | Iterable[_AnySourceAdapter],
        /,
    ) -> None:
        self._registry = (
            definitions
            if isinstance(definitions, SourceDefinitionRegistry)
            else SourceDefinitionRegistry.from_adapters(definitions)
        )

    async def add(
        self,
        connection: AsyncConnection,
        scope_id: str,
        source: Source,
        /,
    ) -> StoredSource:
        """Add one stable Source or return an identical existing capture."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if isinstance(source, SourceObservation):
            ref = SourceRef(source_type=source.source_type, source_id=source.name)
            representation: Literal["native", "observation"] = "observation"
        else:
            definition = self._registry.definition_for_source(source)
            ref = SourceRef(source_type=definition.name, source_id=source.name)
            representation = "native"
        payload = _dump_source(source, representation=representation, name=ref.source_type)
        await _lock_journal_head(connection, scope_id)
        existing = await self._find_row(connection, scope_id, ref)
        if existing is not None:
            stored = self._decode_row(existing)
            if stored.value != source:
                raise StoredPayloadConflictError("source", (scope_id, ref))
            return stored

        position = await _next_journal_position(connection, scope_id)
        try:
            await connection.execute(
                insert(SOURCES_TABLE).values(
                    scope_id=scope_id,
                    source_type=ref.source_type,
                    source_id=ref.source_id,
                    payload=payload,
                    journal_position=position,
                )
            )
        except IntegrityError:
            # A concurrent transaction may have committed the same stable
            # Source identity after our initial read. Only an identical
            # canonical value is an idempotent success.
            existing = await self._find_row(connection, scope_id, ref)
            if existing is None:
                raise
            stored = self._decode_row(existing)
            if stored.value != source:
                raise StoredPayloadConflictError("source", (scope_id, ref)) from None
            return stored
        return StoredSource(ref=ref, value=source, journal_position=position)

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: SourceRef,
        /,
    ) -> StoredSource:
        """Load and validate one Source by its complete stable identity."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        row = await self._find_row(connection, scope_id, ref)
        if row is None:
            raise RepositoryNotFoundError("source", (scope_id, ref))
        return self._decode_row(row)

    async def list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int = 0,
        limit: int | None = None,
    ) -> tuple[StoredSource, ...]:
        """Return a stable journal-ordered page for one scope."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if after < 0:
            raise InvalidRepositoryArgumentError("after", "must be non-negative")
        if limit is not None and limit < 1:
            raise InvalidRepositoryArgumentError("limit", "must be positive")
        statement = (
            select(SOURCES_TABLE)
            .where(
                SOURCES_TABLE.c.scope_id == scope_id,
                SOURCES_TABLE.c.journal_position > after,
            )
            .order_by(SOURCES_TABLE.c.journal_position)
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = (await connection.execute(statement)).mappings()
        return tuple(self._decode_row(row) for row in rows)

    async def journal_position(self, connection: AsyncConnection, scope_id: str, /) -> int:
        """Return the current high watermark for one scope."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        value = await connection.scalar(
            select(func.coalesce(func.max(SOURCES_TABLE.c.journal_position), 0)).where(
                SOURCES_TABLE.c.scope_id == scope_id
            )
        )
        if value is None:
            raise InvalidStoredColumnError("journal_position", "an integer")
        return int(value)

    def _definition_by_name(self, name: str) -> _AnySourceAdapter:
        try:
            return self._registry.definition_for_name(name)
        except SourceDefinitionNotFoundError:
            raise RepositoryNotFoundError("source-definition", name) from None

    async def _find_row(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: SourceRef,
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(SOURCES_TABLE).where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        SOURCES_TABLE.c.source_type == ref.source_type,
                        SOURCES_TABLE.c.source_id == ref.source_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    def _decode_row(self, row: Mapping[Any, Any]) -> StoredSource:
        source_type = str(row["source_type"])
        source_id = str(row["source_id"])
        payload = stored_bytes(row["payload"], column="payload")
        try:
            envelope = load_model(
                _StoredSourcePayload,
                payload,
                kind="source-envelope",
                name=source_type,
            )
        except InvalidStoredPayloadError:
            source, decoded = self._decode_legacy_payload(source_type, payload)
        else:
            source, decoded = self._decode_envelope(source_type, envelope)
        indexed = SourceRef(source_type=source_type, source_id=source_id)
        if indexed != decoded:
            raise IdentityMismatchError("source", indexed, decoded)
        return StoredSource(
            ref=indexed,
            value=source,
            journal_position=int(row["journal_position"]),
        )

    def _decode_envelope(
        self,
        source_type: str,
        envelope: _StoredSourcePayload,
    ) -> tuple[Source, SourceRef]:
        payload = rfc8785.dumps(envelope.value)
        if envelope.representation == "observation":
            source = load_model(
                SourceObservation,
                payload,
                kind="projected-source",
                name=source_type,
            )
            return source, SourceRef(source_type=source.source_type, source_id=source.name)

        definition = self._definition_by_name(source_type)
        source = load_model(
            definition.source_class,
            payload,
            kind="source",
            name=source_type,
        )
        self._registry.definition_for_source(source)
        return source, SourceRef(source_type=definition.name, source_id=source.name)

    def _decode_legacy_payload(self, source_type: str, payload: bytes) -> tuple[Source, SourceRef]:
        try:
            definition = self._definition_by_name(source_type)
        except RepositoryNotFoundError:
            source = load_model(
                SourceObservation,
                payload,
                kind="projected-source",
                name=source_type,
            )
            decoded = SourceRef(source_type=source.source_type, source_id=source.name)
        else:
            source = load_model(
                definition.source_class,
                payload,
                kind="source",
                name=source_type,
            )
            self._registry.definition_for_source(source)
            decoded = SourceRef(source_type=definition.name, source_id=source.name)
        return cast(Source, source), decoded


def _dump_source(
    source: Source,
    *,
    representation: Literal["native", "observation"],
    name: str,
) -> bytes:
    try:
        envelope = _StoredSourcePayload(
            encoding="powercontext-source-v1",
            representation=representation,
            value=_JSON_OBJECT.validate_python(source.model_dump(mode="json", by_alias=True)),
        )
        return rfc8785.dumps(envelope.model_dump(mode="json"))
    except (TypeError, ValueError) as error:
        raise InvalidStoredPayloadError("source", name, "value is not JSON serializable") from error


async def _next_journal_position(connection: AsyncConnection, scope_id: str) -> int:
    """Atomically reserve a monotonically increasing position for one scope."""

    result = await connection.execute(
        update(SOURCE_JOURNAL_HEADS_TABLE)
        .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position + 1)
    )
    if result.rowcount != 1:
        raise InvalidStoredColumnError("journal_position", "an initialized scope head")

    position = await _journal_head_position(connection, scope_id)
    if position < 1:
        raise InvalidStoredColumnError("journal_position", "a positive integer")
    return position


async def _lock_journal_head(connection: AsyncConnection, scope_id: str) -> None:
    """Create and lock one scope allocator before reading Source identity."""

    # This no-op UPDATE is intentionally the operation's first database access.
    # SQLite therefore acquires its write reservation before any read, while
    # MySQL/OceanBase locks an existing allocator row.
    await connection.execute(
        update(SOURCE_JOURNAL_HEADS_TABLE)
        .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
    )
    position = await connection.scalar(
        select(SOURCE_JOURNAL_HEADS_TABLE.c.position)
        .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        .with_for_update()
    )
    if position is None:
        with suppress(IntegrityError):
            await connection.execute(
                insert(SOURCE_JOURNAL_HEADS_TABLE).values(
                    scope_id=scope_id,
                    position=0,
                )
            )
        position = await connection.scalar(
            select(SOURCE_JOURNAL_HEADS_TABLE.c.position)
            .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
            .with_for_update()
        )
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise InvalidStoredColumnError("journal_position", "a non-negative scope head")


async def _journal_head_position(connection: AsyncConnection, scope_id: str) -> int:
    position = await connection.scalar(
        select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
    )
    if not isinstance(position, int) or isinstance(position, bool):
        raise InvalidStoredColumnError("journal_position", "an integer")
    return position


def _require_identity(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")
