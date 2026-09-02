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
import binascii
import hmac
import json
import secrets
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import rfc8785
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import func, insert, select, update
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
    ARTIFACTS_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.records import (
    ArtifactCollectionItem,
    ArtifactDeletion,
    ArtifactRecord,
    ArtifactRecordPage,
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    BaseOperationNotSupportedError,
    BaseValueConflictError,
    BaseValueNotFoundError,
    CursorExpiredError,
    InvalidBaseAccessRequestError,
    InvalidCursorError,
    ScopeSummary,
    ScopeSummaryPage,
    SourceCollectionItem,
    SourceRecord,
    SourceRecordPage,
    TextSearchMode,
)
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, CONTENT_SOURCE_NAME, ContentCapture, ContentSource
from powercontext.sources import SourceRef

Clock = Callable[[], datetime]
IdFactory = Callable[[str], str]

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_JSON_VALUE = TypeAdapter(JsonValue)
_PROTECTED_ARTIFACT_FAMILIES = frozenset({"experience", "handoff", "memory", "skill"})
_DEFAULT_CURSOR_TTL_SECONDS = 3_600


class RelationalRecordService:
    """Serve fixed Source and Artifact paths over the shared relational tables."""

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        /,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        cursor_secret: bytes | None = None,
        cursor_ttl_seconds: int = _DEFAULT_CURSOR_TTL_SECONDS,
        protected_artifact_families: Iterable[str] | None = None,
    ) -> None:
        if isinstance(cursor_ttl_seconds, bool) or cursor_ttl_seconds < 1:
            raise ValueError("cursor_ttl_seconds must be a positive integer")  # noqa: TRY003
        if cursor_secret is not None and not cursor_secret:
            raise ValueError("cursor_secret must not be empty")  # noqa: TRY003
        self._database = database
        self._sources = sources
        self._clock = _utc_now if clock is None else clock
        self._id_factory = _resource_id if id_factory is None else id_factory
        self._cursor_secret = secrets.token_bytes(32) if cursor_secret is None else cursor_secret
        self._cursor_ttl = timedelta(seconds=cursor_ttl_seconds)
        self._protected_artifact_families = frozenset(
            _PROTECTED_ARTIFACT_FAMILIES if protected_artifact_families is None else protected_artifact_families
        )

    async def create_source(
        self,
        scope_id: str,
        source_type: str,
        content: JsonValue,
        metadata: Mapping[str, JsonValue],
        /,
    ) -> SourceRecord:
        return await self._store_source(
            scope_id,
            source_type,
            self._id_factory("source"),
            content,
            metadata,
        )

    async def capture_source(
        self,
        scope_id: str,
        source_type: str,
        source_id: str,
        content: JsonValue,
        metadata: Mapping[str, JsonValue],
        /,
    ) -> SourceRecord:
        """Preserve the caller-stable identity used by the existing capture API."""

        return await self._store_source(scope_id, source_type, source_id, content, metadata)

    async def _store_source(
        self,
        scope_id: str,
        source_type: str,
        source_id: str,
        content: JsonValue,
        metadata: Mapping[str, JsonValue],
    ) -> SourceRecord:
        self._require_content_source(source_type, "create")
        try:
            capture = ContentCapture.model_validate(
                {"source_id": source_id, "content": content, "metadata": dict(metadata)},
                strict=True,
            )
        except ValidationError as error:
            raise InvalidBaseAccessRequestError("content", "does not match the Source adapter") from error
        source = await CONTENT_SOURCE_ADAPTER.resolve(capture)
        try:
            async with self._database.transaction() as connection:
                stored = await self._sources.add(
                    connection,
                    scope_id,
                    source,
                    created_at=_aware_datetime(self._clock()),
                )
        except StoredPayloadConflictError as error:
            raise BaseValueConflictError("source", (scope_id, source_type, source_id)) from error
        return _source_record(scope_id, stored)

    async def get_source(self, scope_id: str, source_type: str, source_id: str, /) -> SourceRecord:
        self._require_content_source(source_type, "get")
        ref = SourceRef(source_type=source_type, source_id=source_id)
        async with self._database.transaction() as connection:
            return _source_record(scope_id, await self._get_source(connection, scope_id, ref))

    async def query_sources(
        self,
        scope_id: str,
        source_type: str,
        /,
        *,
        query: str | None,
        mode: TextSearchMode | None,
        limit: int,
        cursor: str | None,
    ) -> SourceRecordPage:
        normalized_query = _normalize_query(query, mode)
        self._require_content_source(source_type, "list" if normalized_query is None else "search")
        _require_limit(limit)
        actual_mode = None if normalized_query is None else "keyword"
        expected_cursor = {
            "version": 1,
            "endpoint": "list_sources",
            "scope_id": scope_id,
            "source_type": source_type,
            "query": normalized_query,
            "mode": actual_mode,
            "order": "journal_position:asc",
        }
        after = self._cursor_after_int(cursor, expected_cursor)
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
            if normalized_query is None:
                statement = statement.limit(limit + 1)
            rows = (await connection.execute(statement)).all()
            matches: list[tuple[int, SourceCollectionItem]] = []
            for row in rows:
                ref = SourceRef(source_type=source_type, source_id=str(row.source_id))
                record = _source_record(scope_id, await self._get_source(connection, scope_id, ref))
                score: float | None = None
                snippets: tuple[str, ...] = ()
                if normalized_query is not None:
                    text = _searchable_text(record.content)
                    if not _matches_text(text, normalized_query):
                        continue
                    score = 1.0
                    snippets = (_snippet(text, normalized_query),)
                matches.append((int(row.journal_position), _source_collection_item(record, score, snippets)))
                if len(matches) > limit:
                    break

        selected = matches[:limit]
        next_cursor = None
        if len(matches) > limit and selected:
            next_cursor = self._encode_cursor(expected_cursor, selected[-1][0])
        return SourceRecordPage(
            query=normalized_query,
            mode=actual_mode,
            items=tuple(item for _, item in selected),
            next_cursor=next_cursor,
        )

    async def create_artifact(
        self,
        scope_id: str,
        family: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord:
        self._require_direct_family(family, "create")
        identity = self._id_factory("artifact")
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
                        searchable_text=_searchable_text(write.content),
                        deleted_at=None,
                    )
                )
        except IntegrityError as error:
            raise BaseValueConflictError("artifact", (scope_id, family, identity)) from error
        return _artifact_record(scope_id, ref, write, now)

    async def get_artifact(self, scope_id: str, family: str, artifact_id: str, /) -> ArtifactRecord:
        ArtifactRef(family=family, artifact_id=artifact_id, revision=1)
        async with self._database.transaction() as connection:
            revision = await _head_revision(connection, scope_id, family, artifact_id)
            if revision is None:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
            return await _load_artifact(connection, scope_id, family, artifact_id, revision)

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

    async def query_artifacts(
        self,
        scope_id: str,
        family: str,
        /,
        *,
        query: str | None,
        mode: TextSearchMode | None,
        limit: int,
        cursor: str | None,
    ) -> ArtifactRecordPage:
        ArtifactRef(family=family, artifact_id="validation", revision=1)
        normalized_query = _normalize_query(query, mode)
        _require_limit(limit)
        actual_mode = None if normalized_query is None else "keyword"
        expected_cursor = {
            "version": 1,
            "endpoint": "list_artifacts",
            "scope_id": scope_id,
            "family": family,
            "query": normalized_query,
            "mode": actual_mode,
            "order": "artifact_id:asc",
        }
        after = self._cursor_after_text(cursor, expected_cursor)
        head_matches_revision = (
            (ARTIFACTS_TABLE.c.scope_id == ARTIFACT_HEADS_TABLE.c.scope_id)
            & (ARTIFACTS_TABLE.c.family == ARTIFACT_HEADS_TABLE.c.family)
            & (ARTIFACTS_TABLE.c.artifact_id == ARTIFACT_HEADS_TABLE.c.artifact_id)
            & (ARTIFACTS_TABLE.c.revision == ARTIFACT_HEADS_TABLE.c.revision)
        )
        async with self._database.transaction() as connection:
            statement = (
                select(
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                    ARTIFACTS_TABLE.c.content,
                    ARTIFACTS_TABLE.c.created_at,
                )
                .join(ARTIFACTS_TABLE, head_matches_revision)
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id > after,
                    ARTIFACT_HEADS_TABLE.c.deleted_at.is_(None),
                )
                .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
            )
            if normalized_query is None:
                statement = statement.limit(limit + 1)
            rows = (await connection.execute(statement)).all()
            matches: list[tuple[str, ArtifactCollectionItem]] = []
            for row in rows:
                content = _decode_object(row.content, "content")
                text = _searchable_text(content)
                score: float | None = None
                snippets: tuple[str, ...] = ()
                if normalized_query is not None:
                    if not _matches_text(text, normalized_query):
                        continue
                    score = 1.0
                    snippets = (_snippet(text, normalized_query),)
                artifact_id = str(row.artifact_id)
                matches.append((
                    artifact_id,
                    ArtifactCollectionItem(
                        scope_id=scope_id,
                        artifact_ref=ArtifactRef(
                            family=family,
                            artifact_id=artifact_id,
                            revision=int(row.revision),
                        ),
                        created_at=None if row.created_at is None else _aware_datetime(row.created_at),
                        content_digest=_content_digest(content),
                        score=score,
                        snippets=snippets,
                    ),
                ))
                if len(matches) > limit:
                    break

        selected = matches[:limit]
        next_cursor = None
        if len(matches) > limit and selected:
            next_cursor = self._encode_cursor(expected_cursor, selected[-1][0])
        return ArtifactRecordPage(
            query=normalized_query,
            mode=actual_mode,
            items=tuple(item for _, item in selected),
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
                        ARTIFACT_HEADS_TABLE.c.deleted_at.is_(None),
                    )
                    .values(revision=ARTIFACT_HEADS_TABLE.c.revision)
                )
                if locked.rowcount != 1:
                    latest = await _head_revision(connection, scope_id, family, artifact_id)
                    if latest is None:
                        raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
                    raise ArtifactRevisionPreconditionError(expected_revision, latest)
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
                        ARTIFACT_HEADS_TABLE.c.deleted_at.is_(None),
                    )
                    .values(
                        revision=ref.revision,
                        searchable_text=_searchable_text(write.content),
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
            state = await _head_state(connection, scope_id, family, artifact_id)
            if state is None:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
            revision, existing_deletion = state
            if existing_deletion is not None:
                if revision != expected_revision:
                    raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
                return _artifact_deletion(family, artifact_id, revision, existing_deletion)
            if revision != expected_revision:
                raise ArtifactRevisionPreconditionError(expected_revision, revision)

            deleted_at = _aware_datetime(self._clock())
            removed = await connection.execute(
                update(ARTIFACT_HEADS_TABLE)
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision == expected_revision,
                    ARTIFACT_HEADS_TABLE.c.deleted_at.is_(None),
                )
                .values(deleted_at=deleted_at)
            )
            if removed.rowcount != 1:
                latest = await _head_state(connection, scope_id, family, artifact_id)
                if latest is not None and latest[0] == expected_revision and latest[1] is not None:
                    return _artifact_deletion(family, artifact_id, latest[0], latest[1])
                if latest is None or latest[1] is not None:
                    raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
                raise ArtifactRevisionPreconditionError(expected_revision, latest[0])
        return _artifact_deletion(family, artifact_id, expected_revision, deleted_at)

    async def list_scopes(self, *, limit: int, cursor: str | None) -> ScopeSummaryPage:
        _require_limit(limit)
        expected_cursor = {"version": 1, "endpoint": "list_scopes", "order": "scope_id:asc"}
        after = self._cursor_after_text(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            source_scopes = (await connection.execute(select(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id))).scalars()
            artifact_scopes = (await connection.execute(select(ARTIFACTS_TABLE.c.scope_id).distinct())).scalars()
            scope_ids = sorted({str(value) for value in (*source_scopes, *artifact_scopes) if str(value) > after})
            selected = scope_ids[:limit]
            summaries = tuple([await _scope_summary(connection, scope_id) for scope_id in selected])
        next_cursor = None
        if len(scope_ids) > limit and selected:
            next_cursor = self._encode_cursor(expected_cursor, selected[-1])
        return ScopeSummaryPage(items=summaries, next_cursor=next_cursor)

    def _encode_cursor(self, expected: Mapping[str, JsonValue], after: int | str) -> str:
        expires_at = _aware_datetime(self._clock()) + self._cursor_ttl
        return _encode_cursor(expected, after, secret=self._cursor_secret, expires_at=expires_at)

    def _cursor_after_int(self, cursor: str | None, expected: Mapping[str, JsonValue]) -> int:
        return _cursor_after_int(cursor, expected, secret=self._cursor_secret, now=_aware_datetime(self._clock()))

    def _cursor_after_text(self, cursor: str | None, expected: Mapping[str, JsonValue]) -> str:
        return _cursor_after_text(cursor, expected, secret=self._cursor_secret, now=_aware_datetime(self._clock()))

    def _require_content_source(self, source_type: str, operation: str) -> None:
        if source_type != CONTENT_SOURCE_NAME:
            raise BaseOperationNotSupportedError("source_type", source_type, operation)

    def _require_direct_family(self, family: str, operation: str) -> None:
        ArtifactRef(family=family, artifact_id="validation", revision=1)
        if family in self._protected_artifact_families:
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
    await connection.execute(
        insert(ARTIFACTS_TABLE).values(
            scope_id=scope_id,
            family=ref.family,
            artifact_id=ref.artifact_id,
            revision=ref.revision,
            content=_canonical_object(write.content),
            created_at=created_at,
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
            select(ARTIFACTS_TABLE.c.content, ARTIFACTS_TABLE.c.created_at).where(
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
        content=write.content,
        source_refs=write.source_refs,
        artifact_refs=write.artifact_refs,
        created_at=None if row.created_at is None else _aware_datetime(row.created_at),
        content_digest=_content_digest(content),
    )


async def _scope_summary(connection: AsyncConnection, scope_id: str) -> ScopeSummary:
    active = ARTIFACT_HEADS_TABLE.c.deleted_at.is_(None)
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
            .where(ARTIFACT_HEADS_TABLE.c.scope_id == scope_id, active)
            .distinct()
            .order_by(ARTIFACT_HEADS_TABLE.c.family)
        )
    ).scalars()
    source_count = await connection.scalar(
        select(func.count()).select_from(SOURCES_TABLE).where(SOURCES_TABLE.c.scope_id == scope_id)
    )
    artifact_count = await connection.scalar(
        select(func.count())
        .select_from(ARTIFACT_HEADS_TABLE)
        .where(ARTIFACT_HEADS_TABLE.c.scope_id == scope_id, active)
    )
    return ScopeSummary(
        scope_id=scope_id,
        source_types=tuple(str(value) for value in source_types),
        artifact_families=tuple(str(value) for value in artifact_families),
        source_count=int(source_count or 0),
        artifact_count=int(artifact_count or 0),
    )


async def _head_state(
    connection: AsyncConnection,
    scope_id: str,
    family: str,
    artifact_id: str,
) -> tuple[int, datetime | None] | None:
    row = (
        await connection.execute(
            select(ARTIFACT_HEADS_TABLE.c.revision, ARTIFACT_HEADS_TABLE.c.deleted_at).where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return int(row.revision), None if row.deleted_at is None else _aware_datetime(row.deleted_at)


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
            ARTIFACT_HEADS_TABLE.c.deleted_at.is_(None),
        )
    )
    return None if value is None else int(value)


def _source_record(
    scope_id: str,
    stored: StoredSource,
) -> SourceRecord:
    if not isinstance(stored.value, ContentSource):
        raise BaseOperationNotSupportedError("source_type", stored.ref.source_type, "read")
    return SourceRecord(
        scope_id=scope_id,
        source_type=stored.ref.source_type,
        source_id=stored.ref.source_id,
        content=stored.value.content,
        metadata=stored.value.metadata,
        created_at=stored.created_at,
        position=stored.journal_position,
        content_digest=_content_digest(stored.value.content),
    )


def _source_collection_item(
    record: SourceRecord,
    score: float | None,
    snippets: tuple[str, ...],
) -> SourceCollectionItem:
    return SourceCollectionItem(
        scope_id=record.scope_id,
        source_type=record.source_type,
        source_id=record.source_id,
        metadata=record.metadata,
        created_at=record.created_at,
        position=record.position,
        content_digest=record.content_digest,
        score=score,
        snippets=snippets,
    )


def _artifact_record(
    scope_id: str,
    ref: ArtifactRef,
    write: ArtifactWrite,
    created_at: datetime,
) -> ArtifactRecord:
    return ArtifactRecord(
        scope_id=scope_id,
        artifact_ref=ref,
        content=write.content,
        source_refs=write.source_refs,
        artifact_refs=write.artifact_refs,
        created_at=created_at,
        content_digest=_content_digest(write.content),
    )


def _artifact_deletion(
    family: str,
    artifact_id: str,
    revision: int,
    deleted_at: datetime,
) -> ArtifactDeletion:
    return ArtifactDeletion(
        artifact_ref=ArtifactRef(family=family, artifact_id=artifact_id, revision=revision),
        deleted_at=_aware_datetime(deleted_at),
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


def _content_digest(value: JsonValue) -> str:
    validated = _JSON_VALUE.validate_python(value, strict=True)
    return f"sha256:{sha256(rfc8785.dumps(cast(Any, validated))).hexdigest()}"


def _searchable_text(value: JsonValue) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


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


def _normalize_query(query: str | None, mode: TextSearchMode | None) -> str | None:
    if mode not in {None, "auto", "keyword"}:
        raise InvalidBaseAccessRequestError("mode", "must be auto or keyword")
    normalized = None if query is None else query.strip()
    if not normalized:
        if mode is not None:
            raise InvalidBaseAccessRequestError("mode", "is only valid with a non-empty query")
        return None
    return normalized


def _require_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise InvalidBaseAccessRequestError("limit", "must be between 1 and 100")


def _encode_cursor(
    expected: Mapping[str, JsonValue],
    after: int | str,
    *,
    secret: bytes,
    expires_at: datetime,
) -> str:
    payload = {**expected, "after": after, "expires_at": int(expires_at.timestamp())}
    encoded = rfc8785.dumps(cast(Any, payload))
    signature = hmac.digest(secret, encoded, "sha256")
    return f"{_encode_token_part(encoded)}.{_encode_token_part(signature)}"


def _cursor_payload(
    cursor: str | None,
    expected: Mapping[str, JsonValue],
    *,
    secret: bytes,
    now: datetime,
) -> dict[str, JsonValue] | None:
    if cursor is None:
        return None
    try:
        encoded_payload, encoded_signature = cursor.split(".")
        decoded = _decode_token_part(encoded_payload)
        signature = _decode_token_part(encoded_signature)
        payload = _JSON_OBJECT.validate_json(decoded, strict=True)
    except (binascii.Error, UnicodeEncodeError, ValueError, ValidationError) as error:
        raise InvalidCursorError from error
    if not hmac.compare_digest(signature, hmac.digest(secret, decoded, "sha256")):
        raise InvalidCursorError
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise InvalidCursorError
    if any(payload.get(key) != value for key, value in expected.items()):
        raise InvalidCursorError
    if set(payload) != {*expected, "after", "expires_at"}:
        raise InvalidCursorError
    if int(now.timestamp()) >= expires_at:
        raise CursorExpiredError
    return payload


def _cursor_after_int(
    cursor: str | None,
    expected: Mapping[str, JsonValue],
    *,
    secret: bytes,
    now: datetime,
) -> int:
    payload = _cursor_payload(cursor, expected, secret=secret, now=now)
    if payload is None:
        return 0
    after = payload["after"]
    if not isinstance(after, int) or isinstance(after, bool) or after < 0:
        raise InvalidCursorError
    return after


def _cursor_after_text(
    cursor: str | None,
    expected: Mapping[str, JsonValue],
    *,
    secret: bytes,
    now: datetime,
) -> str:
    payload = _cursor_payload(cursor, expected, secret=secret, now=now)
    if payload is None:
        return ""
    after = payload["after"]
    if not isinstance(after, str):
        raise InvalidCursorError
    return after


def _encode_token_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_token_part(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(f"{value}{padding}".encode("ascii"), altchars=b"-_", validate=True)


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidBaseAccessRequestError("timestamp", "must be a datetime")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _resource_id(kind: str) -> str:
    prefix = "src" if kind == "source" else "art"
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)
