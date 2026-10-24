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

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import uuid4

import rfc8785
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef, StableHandle
from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError
from powercontext.builtin.persistence.artifact_handles import ArtifactHandleRepository
from powercontext.builtin.persistence.artifacts import ArtifactRepository, with_artifact_content
from powercontext.builtin.persistence.cursor_codec import SignedCursorCodec
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import (
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.family_management import (
    AtomicMemoryManagementPrepared,
    AtomicMemoryManagementWriter,
    FamilyManagementWriterRegistry,
    PreparingFamilyManagementWriter,
)
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.revision_meta import (
    PendingRollback,
    bind_rollback,
    reset_rollback,
    revision_meta,
)
from powercontext.builtin.persistence.rollback import (
    merged_source_revision,
    normalize_reason,
    prepare_profile_command,
    require_matching_source,
)
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.persistence.tags import RelationalTagService, tag_predicate
from powercontext.builtin.records import (
    ArtifactCollectionItem,
    ArtifactCreated,
    ArtifactListReader,
    ArtifactRecord,
    ArtifactRecordPage,
    ArtifactRevisionActor,
    ArtifactRevisionPage,
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    BaseOperationNotSupportedError,
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
        tags: RelationalTagService | None = None,
        atomic_memory_tag_hook=None,
        atomic_memory_tag_authorizer=None,
        topic_memory_list_reader: ArtifactListReader | None = None,
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
        self._topic_memory_list_reader = topic_memory_list_reader
        self._artifact_handles = ArtifactHandleRepository(database)
        self._tags = tags or RelationalTagService(
            database,
            artifacts,
            cursor_secret=self._cursor_secret,
            clock=self._clock,
            cursor_ttl_seconds=cursor_ttl_seconds,
            projection_hook=atomic_memory_tag_hook,
            atomic_write_authorizer=atomic_memory_tag_authorizer,
        )

    async def bind_artifact_handle(
        self,
        scope_id: str,
        handle: StableHandle,
        target: ArtifactRef,
        /,
    ) -> ArtifactRef:
        try:
            return await self._artifact_handles.bind(scope_id, handle, target)
        except RepositoryNotFoundError as error:
            raise BaseValueNotFoundError(error.kind, error.identity) from None
        except StoredPayloadConflictError as error:
            raise BaseValueConflictError(error.kind, error.identity) from None

    async def resolve_artifact_handle(self, scope_id: str, handle: StableHandle, /) -> ArtifactRef:
        try:
            return await self._artifact_handles.resolve(scope_id, handle)
        except RepositoryNotFoundError as error:
            raise BaseValueNotFoundError(error.kind, error.identity) from None

    async def get_tags(self, scope_id: str, target: TagTarget) -> ArtifactTagSet:
        return await self._tags.get(scope_id, target)

    async def replace_tags(
        self, scope_id: str, target: TagTarget, tags: tuple[str, ...], *, expected_etag: str, execution_context=None
    ) -> ArtifactTagSet:
        return await self._tags.replace(
            scope_id, target, tags, expected_etag=expected_etag, execution_context=execution_context
        )

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
        return (await self._store_sources(scope_id, (source,)))[0]

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
        working_note: bool = False,
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
        source = (await CONTENT_SOURCE_ADAPTER.resolve(capture)).model_copy(
            update={"handoff_receipt": handoff_receipt, "working_note": working_note}
        )
        return (await self._store_sources(scope_id, (source,)))[0]

    async def capture_sources(self, scope_id: str, captures: Sequence[ContentCapture], /) -> tuple[SourceRecord, ...]:
        """Capture one ordered integration batch with a shared journal and pending transition."""

        sources = tuple([await CONTENT_SOURCE_ADAPTER.resolve(capture) for capture in captures])
        return await self._store_sources(scope_id, sources)

    async def _store_sources(self, scope_id: str, sources: Sequence[ContentSource]) -> tuple[SourceRecord, ...]:
        if not sources:
            return ()
        try:
            async with self._database.transaction() as connection:
                stored = await self._sources.add_many_with_status(connection, scope_id, sources)
                new_positions = [item.journal_position for item, created in stored if created]
                if new_positions and self._processing_pending is not None:
                    for binding_name in self._source_processing_bindings:
                        await self._processing_pending.note_source(
                            connection, scope_id, binding_name, max(new_positions)
                        )
        except StoredPayloadConflictError as error:
            scope, ref = cast(tuple[str, SourceRef], error.identity)
            raise BaseValueConflictError("source", (scope, ref.source_type, ref.source_id)) from error
        return tuple(_source_record(scope_id, item) for item, _ in stored)

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
        *,
        execution_context=None,
        on_commit=None,
    ) -> ArtifactCreated:
        if family == "memory":
            raise BaseOperationNotSupportedError("artifact_family", family, "collection writes")
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
        prepared = (
            await writer.prepare_command(scope_id, artifact_id, command, execution_context=execution_context)
            if isinstance(writer, AtomicMemoryManagementWriter)
            else await writer.prepare(command, usage_scope_id=scope_id)
            if isinstance(writer, PreparingFamilyManagementWriter)
            else command
        )
        try:
            async with self._database.transaction() as connection:
                stored = await self._sources.add(connection, scope_id, source)
                artifact = await writer.create(connection, scope_id, artifact_id, prepared, stored.ref)
                if on_commit is not None:
                    await on_commit(connection, _artifact_created(scope_id, artifact))
        except (StoredPayloadConflictError, RevisionConflictError) as error:
            raise BaseValueConflictError("artifact", (scope_id, family, artifact_id)) from error
        return _artifact_created(scope_id, artifact)

    async def create_atomic_memories(
        self, scope_id: str, contents: tuple[dict[str, JsonValue], ...], *, execution_context=None
    ) -> tuple[ArtifactCreated, ...]:
        """Prepare all standalone additions, then commit their Sources and Owners together."""
        if not contents:
            raise InvalidBaseAccessRequestError("entries", "must contain at least one memory")
        writer = self._family_writers.get("atomic-memory")
        if not isinstance(writer, AtomicMemoryManagementWriter):
            raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "batch creation")
        commands = []
        for content in contents:
            command = writer.validate_create(content)
            artifact_id = self._id_factory("atomic-memory")
            commands.append((artifact_id, command))
        values = await writer.prepare_creates(scope_id, commands, execution_context=execution_context)
        prepared = tuple(
            (artifact_id, command, value) for (artifact_id, command), value in zip(commands, values, strict=True)
        )
        source_values = []
        for artifact_id, command, _ in prepared:
            payload = cast(dict[str, JsonValue], command.model_dump(mode="json", by_alias=True, exclude_none=True))
            source_values.append(
                ContentSource(
                    name=self._id_factory("source"),
                    materialization=SourceMaterialization.CAPTURED,
                    content=_canonical_source_text(payload),
                    wire_content=payload,
                    wire_content_present=True,
                    internal=ContentSourceInternal(
                        role="lineage_only",
                        operation="artifact_create",
                        target=ContentSourceTarget(
                            scope_id=scope_id,
                            family="atomic-memory",
                            artifact_id=artifact_id,
                            revision=1,
                        ),
                    ),
                )
            )
        created = []
        async with self._database.transaction() as connection:
            stored_sources = await self._sources.add_many_with_status(connection, scope_id, source_values)
            stored_values = tuple(stored for stored, _ in stored_sources)
            source_read_set = await self._sources.preload(
                connection,
                scope_id,
                tuple(stored.ref for stored in stored_values),
                known=stored_values,
            )
            artifacts = await writer.create_many(
                connection,
                scope_id,
                tuple(
                    (artifact_id, value, stored.ref)
                    for (artifact_id, _, value), stored in zip(prepared, stored_values, strict=True)
                ),
                source_read_set=source_read_set,
            )
            created.extend(_artifact_created(scope_id, artifact) for artifact in artifacts)
        return tuple(created)

    async def get_artifact(
        self, scope_id: str, family: str, artifact_id: str, /, *, execution_context=None
    ) -> ArtifactRecord:
        self._require_family(family)
        if family == "atomic-memory" and execution_context is not None:
            return await self._get_authorized_atomic_artifact(scope_id, artifact_id, None, execution_context)
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
        *,
        execution_context=None,
    ) -> ArtifactRecord:
        self._require_family(family)
        if family == "atomic-memory" and execution_context is not None:
            return await self._get_authorized_atomic_artifact(scope_id, artifact_id, revision, execution_context)
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

    async def _get_authorized_atomic_artifact(
        self, scope_id: str, artifact_id: str, revision: int | None, execution_context
    ) -> ArtifactRecord:
        writer = cast(AtomicMemoryManagementWriter, self._family_writers.get("atomic-memory"))
        try:
            record = await writer.application.for_scope(scope_id).get(
                artifact_id, revision=revision, context=execution_context
            )
        except RepositoryNotFoundError:
            identity = (scope_id, "atomic-memory", artifact_id)
            if revision is not None:
                identity = (*identity, revision)
            raise BaseValueNotFoundError("artifact", identity) from None
        return _artifact_record(scope_id, record.artifact)

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
        async with self._database.transaction() as connection:
            rows, _, next_cursor = await self.revision_rows(
                connection, scope_id, family, artifact_id, limit=limit, cursor=cursor
            )
            artifacts = await self._artifacts.decode_many(connection, scope_id, rows)
            items = tuple(_artifact_collection_item(scope_id, artifact) for artifact in artifacts)
        return ArtifactRevisionPage(items=items, next_cursor=next_cursor)

    async def revision_rows(self, connection, scope_id, family, artifact_id, *, limit, cursor, columns=()):
        """Read a bounded immutable revision page through a borrowed transaction."""
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
        current_revision = await connection.scalar(
            select(ARTIFACT_HEADS_TABLE.c.revision).where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
            )
        )
        if current_revision is None:
            raise BaseValueNotFoundError("artifact", (scope_id, family, artifact_id))
        if after_text:
            try:
                snapshot_text, revision_text = after_text.split(":")
                snapshot, after = int(snapshot_text), int(revision_text)
            except ValueError:
                raise InvalidCursorError from None
            if not 1 <= after <= snapshot <= current_revision:
                raise InvalidCursorError
        else:
            snapshot, after = current_revision, current_revision + 1
        revisions = tuple(
            (
                await connection.execute(
                    select(*(columns or ARTIFACTS_TABLE.c))
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
            ).mappings()
        )
        selected = revisions[:limit]
        next_cursor = (
            self._encode_cursor(expected_cursor, f"{snapshot}:{selected[-1]['revision']}")
            if len(revisions) > limit and selected
            else None
        )
        return selected, current_revision, next_cursor

    async def logical_artifacts(self, scope_id: str, /) -> tuple[LogicalArtifactRecord, ...]:
        """Catalog current logical identities."""
        async with self._database.transaction() as connection:
            artifacts = (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE.c.family, ARTIFACT_HEADS_TABLE.c.artifact_id).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family != "memory",
                    )
                )
            ).all()
        return tuple(
            LogicalArtifactRecord(family=str(row.family), artifact_id=str(row.artifact_id)) for row in artifacts
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
        reader = self._topic_memory_list_reader
        if reader is not None and family == reader.family:
            return await reader.query(
                scope_id,
                limit=limit,
                cursor=cursor,
                tag_filter=tag_filter,
                cursor_codec=self._cursor_codec,
            )
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
                    ARTIFACT_HEADS_TABLE.c.scope_id,
                    ARTIFACT_HEADS_TABLE.c.family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == family,
                    ARTIFACT_HEADS_TABLE.c.artifact_id > after,
                    ARTIFACT_HEADS_TABLE.c.merged_into_id.is_(None),
                    ARTIFACT_HEADS_TABLE.c.lifecycle_state != "retired",
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
            heads = statement.subquery("selected_artifact_heads")
            rows = (
                (await connection.execute(with_artifact_content(select(heads), heads).order_by(heads.c.artifact_id)))
                .mappings()
                .all()
            )
            selected_rows = rows[:limit]
            artifacts = await self._artifacts.decode_many(connection, scope_id, selected_rows)
            items = tuple(_artifact_collection_item(scope_id, artifact) for artifact in artifacts)

        next_cursor = None
        if len(rows) > limit and selected_rows:
            next_cursor = self._encode_cursor(expected_cursor, str(selected_rows[-1]["artifact_id"]))
        return ArtifactRecordPage(
            items=items,
            next_cursor=next_cursor,
        )

    async def replace_artifact(  # noqa: C901
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_etag: str,
        write: ArtifactWrite,
        /,
        *,
        actor_type: str | None = None,
        actor_id: str | None = None,
        execution_context=None,
    ) -> ArtifactRecord:
        if write.prompt_key is not None:
            raise InvalidBaseAccessRequestError("prompt_key", "is not accepted for replacement")
        if family == "memory":
            raise BaseOperationNotSupportedError("artifact_family", family, "collection writes")
        writer = self._family_writers.get(family)
        command = writer.validate_replace(write.content)
        source_revision = merged_source_revision(family, write, command)
        command = prepare_profile_command(command, source_revision)
        reason = None if source_revision is None or write.reason is None else normalize_reason(write.reason)
        prepared = command
        if isinstance(writer, AtomicMemoryManagementWriter):
            record = await self.get_artifact(scope_id, family, artifact_id)
            if expected_etag != _artifact_etag(record.revision):
                raise ArtifactRevisionPreconditionError(expected_etag, _artifact_etag(record.revision))
            prepared = await writer.prepare_command(
                scope_id, artifact_id, command, expected_revision=record.revision, execution_context=execution_context
            )
        elif isinstance(writer, PreparingFamilyManagementWriter):
            current_record = await self.get_artifact(scope_id, family, artifact_id)
            if expected_etag != _artifact_etag(current_record.revision):
                raise ArtifactRevisionPreconditionError(expected_etag, _artifact_etag(current_record.revision))
            prepared = await writer.prepare(command, usage_scope_id=scope_id)
        try:
            async with self._database.transaction() as connection:
                if isinstance(writer, AtomicMemoryManagementWriter):
                    current = cast(AtomicMemoryManagementPrepared, prepared).prepared.plan.writes[0].current.artifact
                else:
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
                    token = await _bind_rollback(
                        self._artifacts,
                        connection,
                        scope_id,
                        family,
                        artifact_id,
                        current,
                        command,
                        source_revision,
                        reason,
                        writer,
                        next_revision,
                        actor_type,
                        actor_id,
                    )
                    try:
                        revised = await writer.replace(connection, scope_id, current, prepared, stored.ref)
                    finally:
                        if token is not None:
                            reset_rollback(token)
                except StoredPayloadConflictError as error:
                    raise BaseValueConflictError("source", (scope_id, CONTENT_SOURCE_NAME, source.name)) from error
                except RevisionConflictError:
                    latest = await self._artifacts.latest(connection, scope_id, family, artifact_id)
                    raise ArtifactRevisionPreconditionError(expected_etag, _artifact_etag(latest.revision)) from None
        except AtomicMemoryConflictError:
            # The write transaction has rolled back; inspect the committed head using a fresh read.
            latest_record = await self.get_artifact(scope_id, family, artifact_id)
            latest_etag = _artifact_etag(latest_record.revision)
            if expected_etag != latest_etag:
                raise ArtifactRevisionPreconditionError(expected_etag, latest_etag) from None
            raise
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
        if family == "memory":
            raise BaseOperationNotSupportedError("artifact_family", family, "legacy Memory collection read")
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
        **_revision_fields(artifact),
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
        content=content,
        **_revision_fields(artifact),
    )


async def _bind_rollback(
    artifacts: ArtifactRepository,
    connection: AsyncConnection,
    scope_id: str,
    family: str,
    artifact_id: str,
    current: Artifact[Any],
    command: Any,
    source_revision: int | None,
    reason: str | None,
    writer: object,
    next_revision: int,
    actor_type: str | None,
    actor_id: str | None,
) -> Any:
    if source_revision is None or reason is None:
        return None
    await require_matching_source(
        artifacts,
        connection,
        scope_id,
        family,
        artifact_id,
        current,
        command,
        source_revision,
        writer,
    )
    return bind_rollback(
        PendingRollback(
            family=family,
            artifact_id=artifact_id,
            revision=next_revision,
            restored_from_revision=source_revision,
            reason=reason,
            created_by_type=actor_type,
            created_by_id=actor_id,
        )
    )


def _revision_fields(artifact: Artifact[Any]) -> dict[str, Any]:
    meta = revision_meta(artifact)
    actor = None
    if meta.created_by_type in {"user", "service"} and meta.created_by_id is not None:
        actor = ArtifactRevisionActor(
            type=cast(Literal["user", "service"], meta.created_by_type),
            id=meta.created_by_id,
        )
    return {
        "created_at": meta.created_at,
        "created_by": actor,
        "restored_from_revision": meta.restored_from_revision,
        "reason": meta.reason,
    }


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
