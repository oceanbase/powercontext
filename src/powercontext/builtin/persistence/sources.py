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

from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, cast

import rfc8785
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter
from sqlalchemy import func, insert, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncTransaction

from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import (
    IdentityMismatchError,
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    InvalidStoredPayloadError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE, SOURCES_TABLE
from powercontext.builtin.sources.content import ContentSource
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.errors import SourceDefinitionNotFoundError
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import Source, SourceAdapter, SourceDefinitionRegistry, SourceObservation, SourceRef

_AnySourceAdapter = SourceAdapter[Any, Any, Any]
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
SOURCE_PROCESSING_BINDINGS = (
    SOURCE_WINDOW_TRIGGER_NAME,
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    EXPERIENCE_INCUBATION_CURSOR_NAME,
    PROFILE_SOURCE_WINDOW_BINDING,
)
_SOURCE_LOOKUP_KEYS_PER_STATEMENT = 400  # Two bind values per key, including SQLite's 999-variable builds.


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


@dataclass(frozen=True, slots=True)
class _SourceReadSet:
    connection: AsyncConnection
    transaction: AsyncTransaction | None
    nested_transaction: AsyncTransaction | None
    scope_id: str
    rows: Mapping[tuple[str, str], StoredSource]

    def find(self, connection: AsyncConnection, scope_id: str, ref: SourceRef) -> StoredSource | None:
        if (
            connection is self.connection
            and self.transaction is not None
            and connection.get_transaction() is self.transaction
            and connection.get_nested_transaction() is self.nested_transaction
            and scope_id == self.scope_id
        ):
            return self.rows.get((ref.source_type, ref.source_id))
        return None


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

        stored, _created = await self.add_with_status(connection, scope_id, source)
        return stored

    async def add_with_status(
        self,
        connection: AsyncConnection,
        scope_id: str,
        source: Source,
        /,
    ) -> tuple[StoredSource, bool]:
        """Add one Source and report whether this call inserted its journal row."""

        return (await self.add_many_with_status(connection, scope_id, (source,)))[0]

    async def add_many_with_status(
        self,
        connection: AsyncConnection,
        scope_id: str,
        sources: Sequence[Source],
        /,
    ) -> tuple[tuple[StoredSource, bool], ...]:
        """Classify one ordered Source batch and reserve positions for new identities together."""

        if not sources:
            return ()
        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        prepared = tuple((source, *self._encode(source)) for source in sources)

        position = await _lock_journal_head(connection, scope_id)
        existing = await self._existing_many(connection, scope_id, tuple(ref for _, ref, _ in prepared))
        results: list[tuple[StoredSource, bool]] = []
        new_rows = []
        upgrades: dict[tuple[str, str], bytes] = {}
        for source, ref, payload in prepared:
            key = (ref.source_type, ref.source_id)
            stored = existing.get(key)
            if stored is not None:
                if (
                    isinstance(source, ContentSource)
                    and source.handoff_receipt
                    and isinstance(stored.value, ContentSource)
                    and not stored.value.handoff_receipt
                    and stored.value.model_copy(update={"handoff_receipt": True}) == source
                ):
                    stored = StoredSource(ref=ref, value=source, journal_position=stored.journal_position)
                    existing[key] = stored
                    upgrades[key] = payload
                elif stored.value != source:
                    raise StoredPayloadConflictError("source", (scope_id, ref))
                results.append((stored, False))
                continue
            position += 1
            stored = StoredSource(ref=ref, value=source, journal_position=position)
            existing[key] = stored
            new_rows.append({
                "scope_id": scope_id,
                "source_type": ref.source_type,
                "source_id": ref.source_id,
                "payload": payload,
                "journal_position": position,
            })
            results.append((stored, True))

        if new_rows:
            await connection.execute(
                update(SOURCE_JOURNAL_HEADS_TABLE)
                .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
                .values(position=position)
            )
            await connection.execute(insert(SOURCES_TABLE), new_rows)
        for (source_type, source_id), payload in upgrades.items():
            await connection.execute(
                update(SOURCES_TABLE)
                .where(
                    SOURCES_TABLE.c.scope_id == scope_id,
                    SOURCES_TABLE.c.source_type == source_type,
                    SOURCES_TABLE.c.source_id == source_id,
                )
                .values(payload=payload)
            )
        if new_rows:
            await ArtifactProcessingIntentRepository().mark_dirty_many(
                connection, scope_id, SOURCE_PROCESSING_BINDINGS, increment=len(new_rows)
            )
        return tuple(results)

    def _encode(self, source: Source) -> tuple[SourceRef, bytes]:
        if isinstance(source, SourceObservation):
            ref = SourceRef(source_type=source.source_type, source_id=source.name)
            representation: Literal["native", "observation"] = "observation"
        else:
            definition = self._registry.definition_for_source(source)
            ref = SourceRef(source_type=definition.name, source_id=source.name)
            representation = "native"
        return ref, _dump_source(source, representation=representation, name=ref.source_type)

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
        return self.decode_source(row)

    async def get_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[SourceRef],
        /,
    ) -> tuple[StoredSource, ...]:
        """Load exact Sources in first-requested order using one query."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        ordered: list[SourceRef] = []
        identities: set[tuple[str, str]] = set()
        for ref in refs:
            identity = (ref.source_type, ref.source_id)
            if identity not in identities:
                ordered.append(ref)
                identities.add(identity)
        if not ordered:
            return ()

        decoded = await self._existing_many(connection, scope_id, tuple(ordered))
        for ref in ordered:
            if (ref.source_type, ref.source_id) not in decoded:
                raise RepositoryNotFoundError("source", (scope_id, ref))
        return tuple(decoded[(ref.source_type, ref.source_id)] for ref in ordered)

    async def _existing_many(
        self, connection: AsyncConnection, scope_id: str, refs: Sequence[SourceRef]
    ) -> dict[tuple[str, str], StoredSource]:
        keys = tuple(dict.fromkeys((ref.source_type, ref.source_id) for ref in refs))
        decoded: dict[tuple[str, str], StoredSource] = {}
        for offset in range(0, len(keys), _SOURCE_LOOKUP_KEYS_PER_STATEMENT):
            rows = (
                (
                    await connection.execute(
                        select(SOURCES_TABLE).where(
                            SOURCES_TABLE.c.scope_id == scope_id,
                            tuple_(SOURCES_TABLE.c.source_type, SOURCES_TABLE.c.source_id).in_(
                                keys[offset : offset + _SOURCE_LOOKUP_KEYS_PER_STATEMENT]
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                try:
                    stored = self.decode_source(row)
                except RepositoryNotFoundError:
                    ref = SourceRef(source_type=str(row["source_type"]), source_id=str(row["source_id"]))
                    raise RepositoryNotFoundError("source", (scope_id, ref)) from None
                decoded[(stored.ref.source_type, stored.ref.source_id)] = stored
        return decoded

    async def preload(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[SourceRef],
        /,
        *,
        known: Sequence[StoredSource] = (),
    ) -> _SourceReadSet:
        """Load lineage evidence once; ``known`` must come from this transaction's reads or inserts."""
        rows = {(item.ref.source_type, item.ref.source_id): item for item in known}
        missing = tuple(ref for ref in refs if (ref.source_type, ref.source_id) not in rows)
        rows.update({
            (item.ref.source_type, item.ref.source_id): item
            for item in await self.get_many(connection, scope_id, missing)
        })
        return _SourceReadSet(
            connection, connection.get_transaction(), connection.get_nested_transaction(), scope_id, rows
        )

    async def list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int = 0,
        through: int | None = None,
        limit: int | None = None,
        source_type: str | None = None,
    ) -> tuple[StoredSource, ...]:
        """Return a stable journal-ordered page for one scope."""

        return tuple([
            item
            async for item in self.iter_list(
                connection,
                scope_id,
                after=after,
                through=through,
                limit=limit,
                source_type=source_type,
            )
        ])

    async def iter_list(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int = 0,
        through: int | None = None,
        limit: int | None = None,
        source_type: str | None = None,
        source_types: tuple[str, ...] | None = None,
    ) -> AsyncGenerator[StoredSource, None]:
        """Stream a stable journal-ordered page without decoding it eagerly."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if after < 0:
            raise InvalidRepositoryArgumentError("after", "must be non-negative")
        if limit is not None and limit < 1:
            raise InvalidRepositoryArgumentError("limit", "must be positive")
        if through is not None and through < after:
            raise InvalidRepositoryArgumentError("through", "must not precede after")
        predicates = [
            SOURCES_TABLE.c.scope_id == scope_id,
            SOURCES_TABLE.c.journal_position > after,
        ]
        if through is not None:
            predicates.append(SOURCES_TABLE.c.journal_position <= through)
        if source_type is not None:
            predicates.append(SOURCES_TABLE.c.source_type == source_type)
        if source_types is not None:
            predicates.append(SOURCES_TABLE.c.source_type.in_(source_types))
        statement = select(SOURCES_TABLE).where(*predicates).order_by(SOURCES_TABLE.c.journal_position)
        if limit is not None:
            statement = statement.limit(limit)
        async with connection.stream(statement.execution_options(yield_per=1)) as result:
            async for row in result.mappings():
                yield self.decode_source(row)

    async def list_window(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        after: int,
        through: int,
    ) -> tuple[StoredSource, ...]:
        """Return the exact journal interval ``(after, through]``."""

        _require_identity("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        if after < 0:
            raise InvalidRepositoryArgumentError("after", "must be non-negative")
        if through < after:
            raise InvalidRepositoryArgumentError("through", "must not precede after")
        rows = (
            (
                await connection.execute(
                    select(SOURCES_TABLE)
                    .where(
                        SOURCES_TABLE.c.scope_id == scope_id,
                        SOURCES_TABLE.c.journal_position > after,
                        SOURCES_TABLE.c.journal_position <= through,
                    )
                    .order_by(SOURCES_TABLE.c.journal_position)
                )
            )
            .mappings()
            .all()
        )
        return tuple(self.decode_source(row) for row in rows)

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

    async def read_value(self, source: Source, /) -> object:
        """Read one decoded Source through the same registered adapter route."""

        return await self._registry.read(source)

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

    def decode_source(self, row: Mapping[Any, Any], /) -> StoredSource:
        """Decode and validate one stored Source using the registered adapter contract."""

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


async def _lock_journal_head(connection: AsyncConnection, scope_id: str) -> int:
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
    return position


def _require_identity(field: str, value: object, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must not exceed {maximum} characters")
