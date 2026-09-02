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

"""Relational implementation of base Source, Artifact, and Scope access."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import rfc8785
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.codec import stored_bytes
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_REVISION_RECORDS_TABLE,
    ARTIFACT_TOMBSTONES_TABLE,
    ARTIFACTS_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCE_RECORDS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.records import (
    ArtifactDeletion,
    ArtifactRecord,
    ArtifactRecordPage,
    ArtifactRevisionPreconditionError,
    ArtifactSearchHit,
    ArtifactSearchPage,
    ArtifactWrite,
    BaseOperationNotSupportedError,
    BaseValueConflictError,
    BaseValueNotFoundError,
    InvalidBaseAccessRequestError,
    ScopeSummary,
    ScopeSummaryPage,
    SourceQueryType,
    SourceRecord,
    SourceRecordPage,
    TextSearchMode,
)
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, CONTENT_SOURCE_NAME, ContentCapture, ContentSource
from powercontext.sources import SourceRef

Clock = Callable[[], datetime]

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_PROTECTED_ARTIFACT_FAMILIES = frozenset({"experience", "handoff", "memory", "skill"})


class RelationalRecordService:
    """Serve fixed Source and Artifact paths over the shared relational tables."""

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        /,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._database = database
        self._sources = sources
        self._clock = _utc_now if clock is None else clock

    async def create_source(
        self,
        scope_id: str,
        source_type: str,
        source_id: str,
        content: str,
        metadata: Mapping[str, JsonValue],
        /,
    ) -> SourceRecord:
        self._require_content_source(source_type, "create")
        capture = ContentCapture(source_id=source_id, content=content, metadata=dict(metadata))
        source = await CONTENT_SOURCE_ADAPTER.resolve(capture)
        digest = _content_digest({"content": source.content, "metadata": source.metadata})
        created_at = _aware_datetime(self._clock())
        try:
            async with self._database.transaction() as connection:
                stored = await self._sources.add(connection, scope_id, source)
                record = await _source_projection(connection, scope_id, stored.ref)
                if record is None:
                    with suppress(IntegrityError):
                        await connection.execute(
                            insert(SOURCE_RECORDS_TABLE).values(
                                scope_id=scope_id,
                                source_type=stored.ref.source_type,
                                source_id=stored.ref.source_id,
                                created_at=created_at,
                                content_digest=digest,
                            )
                        )
                    record = await _source_projection(connection, scope_id, stored.ref)
                projected_at = created_at if record is None else _aware_datetime(record.created_at)
                projected_digest = digest if record is None else str(record.content_digest)
                return _source_record(
                    scope_id,
                    stored,
                    created_at=projected_at,
                    content_digest=projected_digest,
                )
        except StoredPayloadConflictError as error:
            raise BaseValueConflictError("source", (scope_id, source_type, source_id)) from error

    async def get_source(self, scope_id: str, source_type: str, source_id: str, /) -> SourceRecord:
        self._require_content_source(source_type, "get")
        ref = SourceRef(source_type=source_type, source_id=source_id)
        async with self._database.transaction() as connection:
            stored = await self._get_source(connection, scope_id, ref)
            projection = await _source_projection(connection, scope_id, ref)
            return _source_record(
                scope_id,
                stored,
                created_at=None if projection is None else _aware_datetime(projection.created_at),
                content_digest=(_source_digest(stored) if projection is None else str(projection.content_digest)),
            )

    async def query_sources(
        self,
        scope_id: str,
        source_type: str,
        query_type: SourceQueryType,
        /,
        *,
        query: str | None,
        mode: TextSearchMode | None,
        limit: int,
        cursor: str | None,
    ) -> SourceRecordPage:
        self._require_content_source(source_type, query_type)
        normalized_query = _validate_source_query(query_type, query, mode)
        expected_cursor = {
            "kind": "source",
            "scope_id": scope_id,
            "source_type": source_type,
            "type": query_type,
            "q": normalized_query,
            "mode": None if mode is None else str(mode),
        }
        after = _cursor_after_int(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            statement = (
                select(SOURCES_TABLE.c.source_id, SOURCES_TABLE.c.journal_position)
                .where(
                    SOURCES_TABLE.c.scope_id == scope_id,
                    SOURCES_TABLE.c.source_type == source_type,
                    SOURCES_TABLE.c.journal_position > after,
                )
                .order_by(SOURCES_TABLE.c.journal_position)
            )
            if query_type == "list":
                statement = statement.limit(limit + 1)
            rows = (await connection.execute(statement)).all()
            matches: list[tuple[int, SourceRecord]] = []
            for row in rows:
                ref = SourceRef(source_type=source_type, source_id=str(row.source_id))
                stored = await self._get_source(connection, scope_id, ref)
                projection = await _source_projection(connection, scope_id, ref)
                record = _source_record(
                    scope_id,
                    stored,
                    created_at=None if projection is None else _aware_datetime(projection.created_at),
                    content_digest=(_source_digest(stored) if projection is None else str(projection.content_digest)),
                )
                if normalized_query is not None:
                    if not _matches(record.content, record.metadata, normalized_query):
                        continue
                    record = record.model_copy(
                        update={"score": 1.0, "snippets": (_snippet(record.content, normalized_query),)}
                    )
                matches.append((int(row.journal_position), record))
                if len(matches) > limit:
                    break

        has_more = len(matches) > limit
        selected = matches[:limit]
        next_cursor = None
        if has_more and selected:
            next_cursor = _encode_cursor(expected_cursor, selected[-1][0])
        return SourceRecordPage(
            query=normalized_query,
            mode=None if normalized_query is None else "keyword",
            items=tuple(record for _, record in selected),
            next_cursor=next_cursor,
        )

    async def create_artifact(
        self,
        scope_id: str,
        family: str,
        artifact_id: str | None,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord:
        self._require_direct_family(family, "create")
        identity = f"art_{uuid4().hex}" if artifact_id is None else artifact_id
        ref = ArtifactRef(family=family, artifact_id=identity, revision=1)
        now = _aware_datetime(self._clock())
        try:
            async with self._database.transaction() as connection:
                await self._require_new_artifact(connection, scope_id, ref)
                await self._validate_lineage(connection, scope_id, write)
                await _insert_artifact_revision(connection, scope_id, ref, write, now)
                await connection.execute(
                    insert(ARTIFACT_HEADS_TABLE).values(
                        scope_id=scope_id,
                        family=family,
                        artifact_id=identity,
                        revision=1,
                        searchable_text=_searchable_text(write.content, write.metadata),
                    )
                )
        except IntegrityError as error:
            raise BaseValueConflictError("artifact", (scope_id, family, identity)) from error
        return _artifact_record(scope_id, ref, write, now)

    async def get_artifact(self, scope_id: str, family: str, artifact_id: str, /) -> ArtifactRecord:
        ArtifactRef(family=family, artifact_id=artifact_id, revision=1)
        async with self._database.transaction() as connection:
            revision = await connection.scalar(
                select(ARTIFACT_HEADS_TABLE.c.revision).where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                )
            )
            if revision is None:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
            return await _load_artifact(connection, scope_id, family, artifact_id, int(revision))

    async def get_artifact_revision(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        revision: int,
        /,
    ) -> ArtifactRecord:
        ArtifactRef(family=family, artifact_id=artifact_id, revision=revision)
        async with self._database.transaction() as connection:
            return await _load_artifact(connection, scope_id, family, artifact_id, revision)

    async def list_artifacts(
        self,
        scope_id: str,
        family: str,
        /,
        *,
        limit: int,
        cursor: str | None,
    ) -> ArtifactRecordPage:
        ArtifactRef(family=family, artifact_id="validation", revision=1)
        expected_cursor = {"kind": "artifact-list", "scope_id": scope_id, "family": family}
        after = _cursor_after_text(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE.c.artifact_id, ARTIFACT_HEADS_TABLE.c.revision)
                    .where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id > after,
                    )
                    .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
                    .limit(limit + 1)
                )
            ).all()
            selected = rows[:limit]
            items = tuple([
                await _load_artifact(
                    connection,
                    scope_id,
                    family,
                    str(row.artifact_id),
                    int(row.revision),
                )
                for row in selected
            ])
        next_cursor = None
        if len(rows) > limit and selected:
            next_cursor = _encode_cursor(expected_cursor, str(selected[-1].artifact_id))
        return ArtifactRecordPage(items=items, next_cursor=next_cursor)

    async def search_artifacts(
        self,
        scope_id: str,
        family: str,
        query: str,
        /,
        *,
        mode: TextSearchMode,
        limit: int,
        cursor: str | None,
    ) -> ArtifactSearchPage:
        ArtifactRef(family=family, artifact_id="validation", revision=1)
        normalized_query = _require_query(query)
        if mode not in {"auto", "keyword"}:
            raise InvalidBaseAccessRequestError("mode", "must be auto or keyword")
        expected_cursor = {
            "kind": "artifact-search",
            "scope_id": scope_id,
            "family": family,
            "q": normalized_query,
            "mode": mode,
        }
        after = _cursor_after_text(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE.c.artifact_id, ARTIFACT_HEADS_TABLE.c.revision)
                    .where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id > after,
                    )
                    .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
                )
            ).all()
            matches: list[tuple[str, ArtifactSearchHit]] = []
            for row in rows:
                artifact = await _load_artifact(
                    connection,
                    scope_id,
                    family,
                    str(row.artifact_id),
                    int(row.revision),
                )
                text = _searchable_text(artifact.content, artifact.metadata)
                if not _matches_text(text, normalized_query):
                    continue
                matches.append((
                    artifact.artifact_ref.artifact_id,
                    ArtifactSearchHit(
                        artifact=artifact,
                        score=1.0,
                        snippets=(_snippet(text, normalized_query),),
                    ),
                ))
                if len(matches) > limit:
                    break

        has_more = len(matches) > limit
        selected_hits = matches[:limit]
        next_cursor = None
        if has_more and selected_hits:
            next_cursor = _encode_cursor(expected_cursor, selected_hits[-1][0])
        return ArtifactSearchPage(
            query=normalized_query,
            mode="keyword",
            hits=tuple(hit for _, hit in selected_hits),
            next_cursor=next_cursor,
        )

    async def replace_artifact(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_revision: int,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord:
        self._require_direct_family(family, "replace")
        ArtifactRef(family=family, artifact_id=artifact_id, revision=expected_revision)
        now = _aware_datetime(self._clock())
        try:
            async with self._database.transaction() as connection:
                current = await _head_revision(connection, scope_id, family, artifact_id)
                if current is None:
                    raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
                if current != expected_revision:
                    raise ArtifactRevisionPreconditionError(expected_revision, current)
                locked = await connection.execute(
                    update(ARTIFACT_HEADS_TABLE)
                    .where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                        ARTIFACT_HEADS_TABLE.c.revision == expected_revision,
                    )
                    .values(revision=ARTIFACT_HEADS_TABLE.c.revision)
                )
                if locked.rowcount != 1:
                    latest = await _head_revision(connection, scope_id, family, artifact_id)
                    raise ArtifactRevisionPreconditionError(expected_revision, latest or expected_revision)
                await self._validate_lineage(connection, scope_id, write)
                ref = ArtifactRef(family=family, artifact_id=artifact_id, revision=expected_revision + 1)
                await _insert_artifact_revision(connection, scope_id, ref, write, now)
                advanced = await connection.execute(
                    update(ARTIFACT_HEADS_TABLE)
                    .where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                        ARTIFACT_HEADS_TABLE.c.revision == expected_revision,
                    )
                    .values(
                        revision=ref.revision,
                        searchable_text=_searchable_text(write.content, write.metadata),
                    )
                )
                if advanced.rowcount != 1:
                    latest = await _head_revision(connection, scope_id, family, artifact_id)
                    raise ArtifactRevisionPreconditionError(expected_revision, latest or expected_revision)
        except IntegrityError as error:
            raise BaseValueConflictError("artifact", (scope_id, family, artifact_id)) from error
        return _artifact_record(scope_id, ref, write, now)

    async def delete_artifact(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_revision: int,
        /,
    ) -> ArtifactDeletion:
        self._require_direct_family(family, "delete")
        ArtifactRef(family=family, artifact_id=artifact_id, revision=expected_revision)
        async with self._database.transaction() as connection:
            tombstone = (
                await connection.execute(
                    select(
                        ARTIFACT_TOMBSTONES_TABLE.c.revision,
                        ARTIFACT_TOMBSTONES_TABLE.c.deleted_at,
                    ).where(
                        ARTIFACT_TOMBSTONES_TABLE.c.scope_id == scope_id,
                        ARTIFACT_TOMBSTONES_TABLE.c.family == family,
                        ARTIFACT_TOMBSTONES_TABLE.c.artifact_id == artifact_id,
                    )
                )
            ).one_or_none()
            if tombstone is not None:
                revision = int(tombstone.revision)
                if revision != expected_revision:
                    raise ArtifactRevisionPreconditionError(expected_revision, revision)
                return ArtifactDeletion(
                    artifact_ref=ArtifactRef(family=family, artifact_id=artifact_id, revision=revision),
                    deleted_at=_aware_datetime(tombstone.deleted_at),
                )

            current = await _head_revision(connection, scope_id, family, artifact_id)
            if current is None:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
            if current != expected_revision:
                raise ArtifactRevisionPreconditionError(expected_revision, current)
            removed = await connection.execute(
                delete(ARTIFACT_HEADS_TABLE).where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision == expected_revision,
                )
            )
            if removed.rowcount != 1:
                latest = await _head_revision(connection, scope_id, family, artifact_id)
                raise ArtifactRevisionPreconditionError(expected_revision, latest or expected_revision)
            deleted_at = _aware_datetime(self._clock())
            await connection.execute(
                insert(ARTIFACT_TOMBSTONES_TABLE).values(
                    scope_id=scope_id,
                    family=family,
                    artifact_id=artifact_id,
                    revision=expected_revision,
                    deleted_at=deleted_at,
                )
            )
        return ArtifactDeletion(
            artifact_ref=ArtifactRef(family=family, artifact_id=artifact_id, revision=expected_revision),
            deleted_at=deleted_at,
        )

    async def list_scopes(self, *, limit: int, cursor: str | None) -> ScopeSummaryPage:
        expected_cursor = {"kind": "scope-list"}
        after = _cursor_after_text(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            source_scopes = (await connection.execute(select(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id))).scalars()
            artifact_scopes = (await connection.execute(select(ARTIFACTS_TABLE.c.scope_id).distinct())).scalars()
            scope_ids = sorted({str(value) for value in (*source_scopes, *artifact_scopes) if str(value) > after})
            selected = scope_ids[:limit]
            summaries = tuple([await _scope_summary(connection, scope_id) for scope_id in selected])
        next_cursor = None
        if len(scope_ids) > limit and selected:
            next_cursor = _encode_cursor(expected_cursor, selected[-1])
        return ScopeSummaryPage(items=summaries, next_cursor=next_cursor)

    def _require_content_source(self, source_type: str, operation: str) -> None:
        if source_type != CONTENT_SOURCE_NAME:
            raise BaseOperationNotSupportedError("source_type", source_type, operation)

    def _require_direct_family(self, family: str, operation: str) -> None:
        if family in _PROTECTED_ARTIFACT_FAMILIES:
            raise BaseOperationNotSupportedError("artifact_family", family, operation)

    async def _get_source(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: SourceRef,
    ) -> StoredSource:
        try:
            return await self._sources.get(connection, scope_id, ref)
        except RepositoryNotFoundError as error:
            raise BaseValueNotFoundError("source", (scope_id, ref)) from error

    async def _require_new_artifact(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: ArtifactRef,
    ) -> None:
        existing = await connection.scalar(
            select(ARTIFACTS_TABLE.c.revision)
            .where(
                ARTIFACTS_TABLE.c.scope_id == scope_id,
                ARTIFACTS_TABLE.c.family == ref.family,
                ARTIFACTS_TABLE.c.artifact_id == ref.artifact_id,
            )
            .limit(1)
        )
        if existing is not None:
            raise BaseValueConflictError("artifact", (scope_id, ref.family, ref.artifact_id))

    async def _validate_lineage(
        self,
        connection: AsyncConnection,
        scope_id: str,
        write: ArtifactWrite,
    ) -> None:
        for source in write.source_refs:
            exists = await connection.scalar(
                select(func.count())
                .select_from(SOURCES_TABLE)
                .where(
                    SOURCES_TABLE.c.scope_id == scope_id,
                    SOURCES_TABLE.c.source_type == source.source_type,
                    SOURCES_TABLE.c.source_id == source.source_id,
                )
            )
            if not exists:
                raise InvalidBaseAccessRequestError("source_refs", "must identify same-Scope Sources")
        for artifact in write.artifact_refs:
            exists = await connection.scalar(
                select(func.count())
                .select_from(ARTIFACTS_TABLE)
                .where(
                    ARTIFACTS_TABLE.c.scope_id == scope_id,
                    ARTIFACTS_TABLE.c.family == artifact.family,
                    ARTIFACTS_TABLE.c.artifact_id == artifact.artifact_id,
                    ARTIFACTS_TABLE.c.revision == artifact.revision,
                )
            )
            if not exists:
                raise InvalidBaseAccessRequestError(
                    "artifact_refs",
                    "must identify exact same-Scope Artifact revisions",
                )


async def _insert_artifact_revision(
    connection: AsyncConnection,
    scope_id: str,
    ref: ArtifactRef,
    write: ArtifactWrite,
    created_at: datetime,
) -> None:
    content = _canonical_object(write.content)
    metadata = _canonical_object(write.metadata)
    await connection.execute(
        insert(ARTIFACTS_TABLE).values(
            scope_id=scope_id,
            family=ref.family,
            artifact_id=ref.artifact_id,
            revision=ref.revision,
            content=content,
        )
    )
    await connection.execute(
        insert(ARTIFACT_REVISION_RECORDS_TABLE).values(
            scope_id=scope_id,
            family=ref.family,
            artifact_id=ref.artifact_id,
            revision=ref.revision,
            schema_version=write.schema_version,
            metadata=metadata,
            created_at=created_at,
            content_digest=_content_digest(write.content),
        )
    )
    if write.source_refs:
        await connection.execute(
            insert(ARTIFACT_LINEAGE_SOURCES_TABLE),
            [
                {
                    "scope_id": scope_id,
                    "family": ref.family,
                    "artifact_id": ref.artifact_id,
                    "revision": ref.revision,
                    "ordinal": ordinal,
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                }
                for ordinal, source in enumerate(write.source_refs)
            ],
        )
    if write.artifact_refs:
        await connection.execute(
            insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE),
            [
                {
                    "scope_id": scope_id,
                    "family": ref.family,
                    "artifact_id": ref.artifact_id,
                    "revision": ref.revision,
                    "ordinal": ordinal,
                    "upstream_family": artifact.family,
                    "upstream_artifact_id": artifact.artifact_id,
                    "upstream_revision": artifact.revision,
                }
                for ordinal, artifact in enumerate(write.artifact_refs)
            ],
        )


async def _load_artifact(
    connection: AsyncConnection,
    scope_id: str,
    family: str,
    artifact_id: str,
    revision: int,
) -> ArtifactRecord:
    row = (
        await connection.execute(
            select(ARTIFACTS_TABLE.c.content).where(
                ARTIFACTS_TABLE.c.scope_id == scope_id,
                ARTIFACTS_TABLE.c.family == family,
                ARTIFACTS_TABLE.c.artifact_id == artifact_id,
                ARTIFACTS_TABLE.c.revision == revision,
            )
        )
    ).one_or_none()
    if row is None:
        raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id, revision))
    content = _decode_object(row.content, "content")
    projection = (
        await connection.execute(
            select(
                ARTIFACT_REVISION_RECORDS_TABLE.c.schema_version,
                ARTIFACT_REVISION_RECORDS_TABLE.c.metadata,
                ARTIFACT_REVISION_RECORDS_TABLE.c.created_at,
                ARTIFACT_REVISION_RECORDS_TABLE.c.content_digest,
            ).where(
                ARTIFACT_REVISION_RECORDS_TABLE.c.scope_id == scope_id,
                ARTIFACT_REVISION_RECORDS_TABLE.c.family == family,
                ARTIFACT_REVISION_RECORDS_TABLE.c.artifact_id == artifact_id,
                ARTIFACT_REVISION_RECORDS_TABLE.c.revision == revision,
            )
        )
    ).one_or_none()
    sources = (
        await connection.execute(
            select(
                ARTIFACT_LINEAGE_SOURCES_TABLE.c.source_type,
                ARTIFACT_LINEAGE_SOURCES_TABLE.c.source_id,
            )
            .where(
                ARTIFACT_LINEAGE_SOURCES_TABLE.c.scope_id == scope_id,
                ARTIFACT_LINEAGE_SOURCES_TABLE.c.family == family,
                ARTIFACT_LINEAGE_SOURCES_TABLE.c.artifact_id == artifact_id,
                ARTIFACT_LINEAGE_SOURCES_TABLE.c.revision == revision,
            )
            .order_by(ARTIFACT_LINEAGE_SOURCES_TABLE.c.ordinal)
        )
    ).all()
    artifacts = (
        await connection.execute(
            select(
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_family,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_artifact_id,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_revision,
            )
            .where(
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.scope_id == scope_id,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.family == family,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.artifact_id == artifact_id,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.revision == revision,
            )
            .order_by(ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.ordinal)
        )
    ).all()
    write = ArtifactWrite(
        schema_version=1 if projection is None else int(projection.schema_version),
        metadata={} if projection is None else _decode_object(projection.metadata, "metadata"),
        content=content,
        source_refs=tuple(
            SourceRef(source_type=str(source.source_type), source_id=str(source.source_id)) for source in sources
        ),
        artifact_refs=tuple(
            ArtifactRef(
                family=str(artifact.upstream_family),
                artifact_id=str(artifact.upstream_artifact_id),
                revision=int(artifact.upstream_revision),
            )
            for artifact in artifacts
        ),
    )
    ref = ArtifactRef(family=family, artifact_id=artifact_id, revision=revision)
    return ArtifactRecord(
        scope_id=scope_id,
        artifact_ref=ref,
        schema_version=write.schema_version,
        metadata=write.metadata,
        content=write.content,
        source_refs=write.source_refs,
        artifact_refs=write.artifact_refs,
        created_at=None if projection is None else _aware_datetime(projection.created_at),
        content_digest=(_content_digest(content) if projection is None else str(projection.content_digest)),
    )


async def _scope_summary(connection: AsyncConnection, scope_id: str) -> ScopeSummary:
    source_types = (
        await connection.execute(
            select(SOURCES_TABLE.c.source_type)
            .where(SOURCES_TABLE.c.scope_id == scope_id)
            .distinct()
            .order_by(SOURCES_TABLE.c.source_type)
        )
    ).scalars()
    artifact_families = (
        await connection.execute(
            select(ARTIFACT_HEADS_TABLE.c.family)
            .where(ARTIFACT_HEADS_TABLE.c.scope_id == scope_id)
            .distinct()
            .order_by(ARTIFACT_HEADS_TABLE.c.family)
        )
    ).scalars()
    source_count = await connection.scalar(
        select(func.count()).select_from(SOURCES_TABLE).where(SOURCES_TABLE.c.scope_id == scope_id)
    )
    artifact_count = await connection.scalar(
        select(func.count()).select_from(ARTIFACT_HEADS_TABLE).where(ARTIFACT_HEADS_TABLE.c.scope_id == scope_id)
    )
    return ScopeSummary(
        scope_id=scope_id,
        source_types=tuple(str(value) for value in source_types),
        artifact_families=tuple(str(value) for value in artifact_families),
        source_count=int(source_count or 0),
        artifact_count=int(artifact_count or 0),
    )


async def _source_projection(connection: AsyncConnection, scope_id: str, ref: SourceRef):
    return (
        await connection.execute(
            select(
                SOURCE_RECORDS_TABLE.c.created_at,
                SOURCE_RECORDS_TABLE.c.content_digest,
            ).where(
                SOURCE_RECORDS_TABLE.c.scope_id == scope_id,
                SOURCE_RECORDS_TABLE.c.source_type == ref.source_type,
                SOURCE_RECORDS_TABLE.c.source_id == ref.source_id,
            )
        )
    ).one_or_none()


async def _head_revision(
    connection: AsyncConnection,
    scope_id: str,
    family: str,
    artifact_id: str,
) -> int | None:
    value = await connection.scalar(
        select(ARTIFACT_HEADS_TABLE.c.revision).where(
            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
            ARTIFACT_HEADS_TABLE.c.family == family,
            ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
        )
    )
    return None if value is None else int(value)


def _source_record(
    scope_id: str,
    stored: StoredSource,
    *,
    created_at: datetime | None,
    content_digest: str,
) -> SourceRecord:
    if not isinstance(stored.value, ContentSource):
        raise BaseOperationNotSupportedError("source_type", stored.ref.source_type, "read")
    return SourceRecord(
        scope_id=scope_id,
        source_ref=stored.ref,
        content=stored.value.content,
        metadata=stored.value.metadata,
        created_at=created_at,
        position=stored.journal_position,
        content_digest=content_digest,
    )


def _source_digest(stored: StoredSource) -> str:
    if not isinstance(stored.value, ContentSource):
        raise BaseOperationNotSupportedError("source_type", stored.ref.source_type, "read")
    return _content_digest({"content": stored.value.content, "metadata": stored.value.metadata})


def _artifact_record(
    scope_id: str,
    ref: ArtifactRef,
    write: ArtifactWrite,
    created_at: datetime,
) -> ArtifactRecord:
    return ArtifactRecord(
        scope_id=scope_id,
        artifact_ref=ref,
        schema_version=write.schema_version,
        metadata=write.metadata,
        content=write.content,
        source_refs=write.source_refs,
        artifact_refs=write.artifact_refs,
        created_at=created_at,
        content_digest=_content_digest(write.content),
    )


def _canonical_object(value: Mapping[str, JsonValue]) -> bytes:
    validated = _JSON_OBJECT.validate_python(dict(value), strict=True)
    return rfc8785.dumps(cast(Any, validated))


def _decode_object(value: object, column: str) -> dict[str, JsonValue]:
    try:
        decoded = json.loads(stored_bytes(value, column=column))
        return _JSON_OBJECT.validate_python(decoded, strict=True)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise InvalidBaseAccessRequestError(column, "contains invalid stored JSON") from error


def _content_digest(value: Mapping[str, JsonValue]) -> str:
    return f"sha256:{sha256(_canonical_object(value)).hexdigest()}"


def _searchable_text(content: Mapping[str, JsonValue], metadata: Mapping[str, JsonValue]) -> str:
    return json.dumps({"content": content, "metadata": metadata}, ensure_ascii=False, sort_keys=True)


def _matches(content: str, metadata: Mapping[str, JsonValue], query: str) -> bool:
    return _matches_text(f"{content}\n{json.dumps(metadata, ensure_ascii=False, sort_keys=True)}", query)


def _matches_text(text: str, query: str) -> bool:
    searchable = text.casefold()
    return all(token in searchable for token in query.casefold().split())


def _snippet(text: str, query: str, *, maximum: int = 240) -> str:
    folded = text.casefold()
    first = query.casefold().split()[0]
    position = folded.find(first)
    if position < 0:
        return text[:maximum]
    start = max(position - maximum // 3, 0)
    return text[start : start + maximum]


def _validate_source_query(
    query_type: SourceQueryType,
    query: str | None,
    mode: TextSearchMode | None,
) -> str | None:
    if query_type == "list":
        if query is not None or mode is not None:
            raise InvalidBaseAccessRequestError("type", "list cannot include q or mode")
        return None
    if query_type != "search":
        raise InvalidBaseAccessRequestError("type", "must be list or search")
    if mode not in {None, "auto", "keyword"}:
        raise InvalidBaseAccessRequestError("mode", "must be auto or keyword")
    return _require_query(query)


def _require_query(query: str | None) -> str:
    if query is None or not query.strip():
        raise InvalidBaseAccessRequestError("q", "must contain non-whitespace text")
    return query.strip()


def _encode_cursor(expected: Mapping[str, JsonValue], after: int | str) -> str:
    payload = {**expected, "after": after}
    encoded = rfc8785.dumps(cast(Any, payload))
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _cursor_payload(cursor: str | None, expected: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(f"{cursor}{padding}")
        payload = _JSON_OBJECT.validate_json(decoded, strict=True)
    except (ValueError, ValidationError) as error:
        raise InvalidBaseAccessRequestError("cursor", "is invalid") from error
    if any(payload.get(key) != value for key, value in expected.items()):
        raise InvalidBaseAccessRequestError("cursor", "does not match the query")
    if set(payload) != {*expected, "after"}:
        raise InvalidBaseAccessRequestError("cursor", "is invalid")
    return payload


def _cursor_after_int(cursor: str | None, expected: Mapping[str, JsonValue]) -> int:
    payload = _cursor_payload(cursor, expected)
    if payload is None:
        return 0
    after = payload["after"]
    if not isinstance(after, int) or isinstance(after, bool) or after < 0:
        raise InvalidBaseAccessRequestError("cursor", "contains an invalid position")
    return after


def _cursor_after_text(cursor: str | None, expected: Mapping[str, JsonValue]) -> str:
    payload = _cursor_payload(cursor, expected)
    if payload is None:
        return ""
    after = payload["after"]
    if not isinstance(after, str):
        raise InvalidBaseAccessRequestError("cursor", "contains an invalid identity")
    return after


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidBaseAccessRequestError("timestamp", "must be a datetime")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
