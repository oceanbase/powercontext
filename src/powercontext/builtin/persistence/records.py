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

from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import rfc8785
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryEntryVersion, MemoryService
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.cursor_codec import SignedCursorCodec
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import (
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.family_management import FamilyManagementWriterRegistry
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.persistence.tags import RelationalTagService, tag_predicate
from powercontext.builtin.records import (
    ArtifactCollectionItem,
    ArtifactCreated,
    ArtifactRecord,
    ArtifactRecordPage,
    ArtifactRevisionPage,
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    BaseValueConflictError,
    BaseValueNotFoundError,
    InvalidBaseAccessRequestError,
    InvalidCursorError,
    LogicalArtifactRecord,
    ScopeSummary,
    ScopeSummaryPage,
    SourceRecord,
    SourceRecordPage,
)
from powercontext.builtin.sources import (
    CONTENT_SOURCE_ADAPTER,
    CONTENT_SOURCE_NAME,
    ContentCapture,
    ContentSource,
    ContentSourceInternal,
    ContentSourceTarget,
)
from powercontext.builtin.tags import ArtifactTagSet, TagFilter, TagQuery, TagQueryPage, TagTarget
from powercontext.errors import RevisionConflictError
from powercontext.sources import SourceMaterialization, SourceRef

Clock = Callable[[], datetime]
IdFactory = Callable[[str], str]


_JSON_VALUE = TypeAdapter(JsonValue)
_DEFAULT_CURSOR_TTL_SECONDS = 3_600
_SOURCE_PAGE_BUDGET_BYTES = 4 * 1024 * 1024


class RelationalRecordService:
    """Serve fixed Source and Artifact paths over the shared relational tables."""

    async def migrate_handoff_receipts(
        self,
        committed_identity_lookup: Callable[[str, str], Awaitable[object | None]],
        /,
    ) -> tuple[int, int]:
        from powercontext.builtin.persistence.receipt_migration import migrate_handoff_receipts

        return await migrate_handoff_receipts(self._database, self._sources, committed_identity_lookup)

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        family_writers: FamilyManagementWriterRegistry,
        /,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        cursor_secret: bytes | None = None,
        cursor_ttl_seconds: int = _DEFAULT_CURSOR_TTL_SECONDS,
        processing_pending: ArtifactProcessingPendingRepository | None = None,
        source_processing_bindings: tuple[str, ...] = (),
    ) -> None:
        self._database = database
        self._sources = sources
        self._artifacts = artifacts
        self._family_writers = family_writers
        self._clock = _utc_now if clock is None else clock
        self._id_factory = _resource_id if id_factory is None else id_factory
        self._cursor_codec = SignedCursorCodec(
            secret=cursor_secret,
            clock=self._clock,
            ttl_seconds=cursor_ttl_seconds,
        )
        self._cursor_secret = self._cursor_codec.secret
        self._processing_pending = processing_pending
        self._source_processing_bindings = source_processing_bindings
        self._tags = RelationalTagService(
            database,
            artifacts,
            cursor_secret=self._cursor_secret,
            clock=self._clock,
            cursor_ttl_seconds=cursor_ttl_seconds,
        )

    async def get_tags(self, scope_id: str, target: TagTarget) -> ArtifactTagSet:
        return await self._tags.get(scope_id, target)

    async def replace_tags(
        self, scope_id: str, target: TagTarget, tags: tuple[str, ...], *, expected_etag: str
    ) -> ArtifactTagSet:
        return await self._tags.replace(scope_id, target, tags, expected_etag=expected_etag)

    async def query_tags(self, scope_id: str, query: TagQuery, *, caller: str = "runtime") -> TagQueryPage:
        return await self._tags.query(scope_id, query, caller=caller)

    async def create_source(
        self,
        scope_id: str,
        source_type: str,
        content: JsonValue,
        /,
    ) -> SourceRecord:
        self._require_content_source(source_type)
        source = ContentSource(
            name=self._id_factory("source"),
            materialization=SourceMaterialization.CAPTURED,
            content=_canonical_source_text(content),
            wire_content=_JSON_VALUE.validate_python(content, strict=True),
            wire_content_present=True,
        )
        return await self._store_source(scope_id, source_type, source)

    async def capture_source(
        self,
        scope_id: str,
        source_type: str,
        source_id: str,
        content: JsonValue,
        metadata: Mapping[str, JsonValue],
        /,
        *,
        handoff_receipt: bool = False,
    ) -> SourceRecord:
        """Preserve the caller-stable identity used by the existing capture API."""

        self._require_content_source(source_type)
        try:
            capture = ContentCapture.model_validate(
                {"source_id": source_id, "content": content, "metadata": dict(metadata)},
                strict=True,
            )
        except ValidationError as error:
            raise InvalidBaseAccessRequestError("content", "does not match the Source adapter") from error
        return await self._store_source(
            scope_id,
            source_type,
            (await CONTENT_SOURCE_ADAPTER.resolve(capture)).model_copy(update={"handoff_receipt": handoff_receipt}),
        )

    async def _store_source(
        self,
        scope_id: str,
        source_type: str,
        source: ContentSource,
    ) -> SourceRecord:
        try:
            async with self._database.transaction() as connection:
                stored, created = await self._sources.add_with_status(connection, scope_id, source)
                if created and self._processing_pending is not None:
                    for binding_name in self._source_processing_bindings:
                        await self._processing_pending.raise_source(
                            connection,
                            scope_id,
                            binding_name,
                            stored.journal_position,
                        )
        except StoredPayloadConflictError as error:
            raise BaseValueConflictError("source", (scope_id, source_type, source.name)) from error
        return _source_record(scope_id, stored)

    async def get_source(self, scope_id: str, source_type: str, source_id: str, /) -> SourceRecord:
        self._require_content_source(source_type)
        ref = SourceRef(source_type=source_type, source_id=source_id)
        async with self._database.transaction() as connection:
            return _source_record(scope_id, await self._get_source(connection, scope_id, ref))

    async def list_sources(
        self,
        scope_id: str,
        /,
        *,
        limit: int,
        cursor: str | None,
        caller: str = "runtime",
    ) -> SourceRecordPage:
        _require_limit(limit)
        expected_cursor = {
            "version": 1,
            "endpoint": "list_sources",
            "scope_id": scope_id,
            "source_types": [CONTENT_SOURCE_NAME],
            "authorization": "scope_read",
            "caller": caller,
            "limit": limit,
            "order": "journal_position:asc",
        }
        cursor_state = self._cursor_after_text(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            high_watermark = await self._sources.journal_position(connection, scope_id)
            if cursor_state:
                try:
                    through_text, after_text = cursor_state.split(":")
                    through, after = int(through_text), int(after_text)
                except ValueError:
                    raise InvalidCursorError from None
                if not 0 <= after <= through <= high_watermark:
                    raise InvalidCursorError
            else:
                through, after = high_watermark, 0
            selected: list[SourceRecord] = []
            page_bytes = 0
            has_more = False
            source_stream = self._sources.iter_list(
                connection,
                scope_id,
                after=after,
                through=through,
                limit=limit + 1,
                source_type=CONTENT_SOURCE_NAME,
            )
            async with aclosing(source_stream):
                async for stored in source_stream:
                    item = _source_record(scope_id, stored)
                    if len(selected) == limit:
                        has_more = True
                        break
                    item_bytes = len(item.model_dump_json().encode("utf-8"))
                    if selected and page_bytes + item_bytes > _SOURCE_PAGE_BUDGET_BYTES:
                        has_more = True
                        break
                    selected.append(item)
                    page_bytes += item_bytes

        next_cursor = None
        if has_more and selected:
            next_cursor = self._encode_cursor(expected_cursor, f"{through}:{selected[-1].position}")
        return SourceRecordPage(items=tuple(selected), next_cursor=next_cursor)

    async def create_artifact(
        self,
        scope_id: str,
        family: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactCreated:
        writer = self._family_writers.get(family)
        command = writer.validate_create(write.content)
        if family == "prompt":
            if write.prompt_key is None:
                raise InvalidBaseAccessRequestError("prompt_key", "is required for Prompt Create")
            selected_id = write.prompt_key
        else:
            if write.prompt_key is not None:
                raise InvalidBaseAccessRequestError("prompt_key", "is only accepted for Prompt Create")
            selected_id = self._id_factory(family)
        artifact_id = writer.artifact_id_for_create(selected_id)
        source_id = self._id_factory("source")
        canonical_content = cast(
            dict[str, JsonValue],
            command.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        source = ContentSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            content=_canonical_source_text(canonical_content),
            wire_content=canonical_content,
            wire_content_present=True,
            internal=ContentSourceInternal(
                role="lineage_only",
                operation="artifact_create",
                target=ContentSourceTarget(
                    scope_id=scope_id,
                    family=cast(Any, family),
                    artifact_id=artifact_id,
                    revision=1,
                ),
            ),
        )
        try:
            async with self._database.transaction() as connection:
                stored = await self._sources.add(connection, scope_id, source)
                artifact = await writer.create(connection, scope_id, artifact_id, command, stored.ref)
        except (StoredPayloadConflictError, RevisionConflictError) as error:
            raise BaseValueConflictError("artifact", (scope_id, family, artifact_id)) from error
        return _artifact_created(scope_id, artifact)

    async def get_artifact(self, scope_id: str, family: str, artifact_id: str, /) -> ArtifactRecord:
        self._require_family(family)
        async with self._database.transaction() as connection:
            try:
                artifact = await self._artifacts.latest(connection, scope_id, family, artifact_id)
            except RepositoryNotFoundError:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id)) from None
            return _artifact_record(scope_id, artifact)

    async def get_artifact_revision(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        revision: int,
        /,
    ) -> ArtifactRecord:
        self._require_family(family)
        async with self._database.transaction() as connection:
            try:
                artifact = await self._artifacts.get(
                    connection,
                    scope_id,
                    ArtifactRef(family=family, artifact_id=artifact_id, revision=revision),
                )
            except RepositoryNotFoundError:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id, revision)) from None
            return _artifact_record(scope_id, artifact)

    async def list_artifact_revisions(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        /,
        *,
        limit: int,
        cursor: str | None,
    ) -> ArtifactRevisionPage:
        self._require_family(family)
        _require_limit(limit)
        expected_cursor = {
            "version": 1,
            "endpoint": "list_artifact_revisions",
            "scope_id": scope_id,
            "family": family,
            "artifact_id": artifact_id,
            "authorization": "scope_read",
            "order": "revision:desc",
        }
        after_text = self._cursor_after_text(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            try:
                current = await self._artifacts.latest(connection, scope_id, family, artifact_id)
            except RepositoryNotFoundError:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id)) from None
            if after_text:
                try:
                    snapshot_text, revision_text = after_text.split(":")
                    snapshot, after = int(snapshot_text), int(revision_text)
                except ValueError:
                    raise InvalidCursorError from None
                if not 1 <= after <= snapshot <= current.revision:
                    raise InvalidCursorError
            else:
                snapshot, after = current.revision, current.revision + 1
            revisions = tuple(
                (
                    await connection.execute(
                        select(ARTIFACTS_TABLE.c.revision)
                        .where(
                            ARTIFACTS_TABLE.c.scope_id == scope_id,
                            ARTIFACTS_TABLE.c.family == family,
                            ARTIFACTS_TABLE.c.artifact_id == artifact_id,
                            ARTIFACTS_TABLE.c.revision <= snapshot,
                            ARTIFACTS_TABLE.c.revision < after,
                        )
                        .order_by(ARTIFACTS_TABLE.c.revision.desc())
                        .limit(limit + 1)
                    )
                ).scalars()
            )
            selected = revisions[:limit]
            artifacts = await self._artifacts.get_many(
                connection,
                scope_id,
                tuple(ArtifactRef(family=family, artifact_id=artifact_id, revision=revision) for revision in selected),
            )
            items = tuple(_artifact_collection_item(scope_id, artifact) for artifact in artifacts)
        next_cursor = (
            self._encode_cursor(expected_cursor, f"{snapshot}:{selected[-1]}")
            if len(revisions) > limit and selected
            else None
        )
        return ArtifactRevisionPage(items=items, next_cursor=next_cursor)

    async def current_memory_entry(self, scope_id: str, artifact_id: str, entry_id: str, /) -> MemoryEntryVersion:
        """Resolve only one entry body, including entries in base-API Memory artifacts."""
        backend = RelationalMemoryBackend(database=self._database, scope_id=scope_id, artifacts=self._artifacts)
        memory = await backend.latest(artifact_id)
        entry = next((value for value in memory.content.manifest.entries if value.entry_id == entry_id), None)
        if entry is None:
            raise BaseValueNotFoundError("artifact", (scope_id, artifact_id, entry_id))
        citation = MemoryCitation(
            memory_ref=memory.as_ref(), entry_id=entry_id, entry_version_id=entry.entry_version_id
        )
        return await MemoryService(backend=backend).validate_citation(citation)

    async def logical_artifacts(self, scope_id: str, /) -> tuple[LogicalArtifactRecord, ...]:
        """Read only catalog identities, including retained Memory entries."""

        async with self._database.transaction() as connection:
            artifacts = (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE.c.family, ARTIFACT_HEADS_TABLE.c.artifact_id).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family != "memory",
                    )
                )
            ).all()
            entries = (
                await connection.execute(
                    select(MEMORY_ENTRY_VERSIONS_TABLE.c.memory_artifact_id, MEMORY_ENTRY_VERSIONS_TABLE.c.entry_id)
                    .where(
                        MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == scope_id,
                    )
                    .distinct()
                )
            ).all()
        return (
            *(LogicalArtifactRecord(family=str(row.family), artifact_id=str(row.artifact_id)) for row in artifacts),
            *(
                LogicalArtifactRecord(
                    family="memory", artifact_id=str(row.memory_artifact_id), entry_id=str(row.entry_id)
                )
                for row in entries
            ),
        )

    async def query_artifacts(
        self,
        scope_id: str,
        family: str,
        /,
        *,
        limit: int,
        cursor: str | None,
        tag_filter: TagFilter | None = None,
    ) -> ArtifactRecordPage:
        self._require_family(family)
        _require_limit(limit)
        expected_cursor = {
            "version": 1,
            "endpoint": "list_artifacts",
            "scope_id": scope_id,
            "family": family,
            "order": "artifact_id:asc",
        }
        if tag_filter is not None:
            expected_cursor["tag_filter"] = sha256(
                rfc8785.dumps({
                    "keys": list(tag_filter.keys),
                    "match": tag_filter.match,
                })
            ).hexdigest()
        after = self._cursor_after_text(cursor, expected_cursor)
        async with self._database.transaction() as connection:
            statement = (
                select(
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id > after,
                )
                .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
                .limit(limit + 1)
            )
            if tag_filter is not None:
                statement = statement.where(
                    tag_predicate(
                        scope_id,
                        family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id,
                        "artifact",
                        ARTIFACT_HEADS_TABLE.c.artifact_id,
                        tag_filter,
                    )
                )
            rows = (await connection.execute(statement)).all()
            selected_rows = rows[:limit]
            artifacts = await self._artifacts.get_many(
                connection,
                scope_id,
                tuple(
                    ArtifactRef(family=family, artifact_id=str(row.artifact_id), revision=int(row.revision))
                    for row in selected_rows
                ),
            )
            items = tuple(_artifact_collection_item(scope_id, artifact) for artifact in artifacts)

        next_cursor = None
        if len(rows) > limit and selected_rows:
            next_cursor = self._encode_cursor(expected_cursor, str(selected_rows[-1].artifact_id))
        return ArtifactRecordPage(
            items=items,
            next_cursor=next_cursor,
        )

    async def replace_artifact(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_etag: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord:
        if write.prompt_key is not None:
            raise InvalidBaseAccessRequestError("prompt_key", "is not accepted for replacement")
        writer = self._family_writers.get(family)
        command = writer.validate_replace(write.content)
        async with self._database.transaction() as connection:
            try:
                current = await self._artifacts.latest(connection, scope_id, family, artifact_id)
            except RepositoryNotFoundError:
                raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id)) from None
            current_etag = _artifact_etag(current.revision)
            if expected_etag != current_etag:
                raise ArtifactRevisionPreconditionError(expected_etag, current_etag)
            next_revision = current.revision + 1
            canonical_content = cast(
                dict[str, JsonValue],
                command.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
            source = ContentSource(
                name=self._id_factory("source"),
                materialization=SourceMaterialization.CAPTURED,
                content=_canonical_source_text(canonical_content),
                wire_content=canonical_content,
                wire_content_present=True,
                internal=ContentSourceInternal(
                    role="lineage_only",
                    operation="artifact_replace",
                    target=ContentSourceTarget(
                        scope_id=scope_id,
                        family=cast(Any, family),
                        artifact_id=artifact_id,
                        revision=next_revision,
                    ),
                ),
            )
            try:
                stored = await self._sources.add(connection, scope_id, source)
                revised = await writer.replace(connection, scope_id, current, command, stored.ref)
            except StoredPayloadConflictError as error:
                raise BaseValueConflictError("source", (scope_id, CONTENT_SOURCE_NAME, source.name)) from error
            except RevisionConflictError:
                latest = await self._artifacts.latest(connection, scope_id, family, artifact_id)
                raise ArtifactRevisionPreconditionError(expected_etag, _artifact_etag(latest.revision)) from None
        return _artifact_record(scope_id, revised)

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
        return self._cursor_codec.encode(expected, after)

    def _cursor_after_text(self, cursor: str | None, expected: Mapping[str, JsonValue]) -> str:
        return self._cursor_codec.after_text(cursor, expected)

    def _require_content_source(self, source_type: str) -> None:
        if source_type != CONTENT_SOURCE_NAME:
            raise InvalidBaseAccessRequestError("source_type", "must be content")

    def _require_family(self, family: str) -> None:
        if family not in self._artifacts.families:
            raise InvalidBaseAccessRequestError("family", "must be a registered Artifact family")

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


def _source_record(
    scope_id: str,
    stored: StoredSource,
) -> SourceRecord:
    if not isinstance(stored.value, ContentSource):
        raise BaseValueNotFoundError("source", (scope_id, stored.ref))
    return SourceRecord(
        scope_id=scope_id,
        source_type=cast(Any, stored.ref.source_type),
        source_id=stored.ref.source_id,
        content=_source_content(stored.value),
        position=stored.journal_position,
        content_digest=_content_digest(_source_content(stored.value)),
        handoff_receipt=stored.value.handoff_receipt,
    )


def _source_content(source: ContentSource) -> JsonValue:
    if source.wire_content_present or source.wire_content is not None:
        return source.wire_content
    return source.content


def _artifact_record(
    scope_id: str,
    artifact: Artifact[Any],
) -> ArtifactRecord:
    content = cast(dict[str, JsonValue], artifact.content.model_dump(mode="json", by_alias=True))
    return ArtifactRecord(
        scope_id=scope_id,
        family=cast(Any, artifact.family),
        artifact_id=artifact.artifact_id,
        revision=artifact.revision,
        content=content,
        sources=artifact.lineage.sources,
        artifacts=artifact.lineage.artifacts,
        content_digest=_content_digest(content),
    )


def _artifact_created(scope_id: str, artifact: Artifact[Any]) -> ArtifactCreated:
    return ArtifactCreated(
        scope_id=scope_id,
        family=cast(Any, artifact.family),
        artifact_id=artifact.artifact_id,
        revision=artifact.revision,
        sources=artifact.lineage.sources,
        artifacts=artifact.lineage.artifacts,
    )


def _artifact_collection_item(scope_id: str, artifact: Artifact[Any]) -> ArtifactCollectionItem:
    content = cast(dict[str, JsonValue], artifact.content.model_dump(mode="json", by_alias=True))
    return ArtifactCollectionItem(
        scope_id=scope_id,
        family=cast(Any, artifact.family),
        artifact_id=artifact.artifact_id,
        revision=artifact.revision,
        sources=artifact.lineage.sources,
        artifacts=artifact.lineage.artifacts,
        content_digest=_content_digest(content),
    )


def _artifact_etag(revision: int) -> str:
    return f'"revision:{revision}"'


def _content_digest(value: JsonValue) -> str:
    validated = _JSON_VALUE.validate_python(value, strict=True)
    return f"sha256:{sha256(rfc8785.dumps(cast(Any, validated))).hexdigest()}"


def _canonical_source_text(value: JsonValue) -> str:
    validated = _JSON_VALUE.validate_python(value, strict=True)
    if isinstance(validated, str):
        return validated
    return rfc8785.dumps(cast(Any, validated)).decode("utf-8")


def _require_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise InvalidBaseAccessRequestError("limit", "must be between 1 and 100")


def _resource_id(kind: str) -> str:
    prefix = "src" if kind == "source" else "art"
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)
