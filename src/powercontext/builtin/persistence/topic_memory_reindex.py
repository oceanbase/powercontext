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

"""Shared source and completeness rules for staged Topic Memory reindexing."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import Table, and_, exists, func, inspect, or_, select, text, tuple_
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import ColumnElement

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.topic_memory import TopicMemoryCapabilityError, TopicMemoryStorageInvariantError
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndex,
    AtomicMemoryIndexError,
    atomic_memory_profile_fingerprint,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
)
from powercontext.builtin.persistence.topic_memory_index import validate_current_topic_vectors

TOPIC_MEMORY_REINDEX_TOPIC_TABLE = "pc_topic_memory_reindex_topics"
TOPIC_MEMORY_REINDEX_CHUNK_TABLE = "pc_topic_memory_reindex_chunks"
TOPIC_MEMORY_REINDEX_SHAPE_TABLE = "pc_topic_memory_reindex_shape"
TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE = "pc_topic_memory_reindex_storage_profile"
TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE = "pc_topic_memory_reindex_topic_vec"
TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE = "pc_topic_memory_reindex_chunk_vec"
TOPIC_MEMORY_REINDEX_PREVIOUS_PREFIX = "pc_topic_memory_reindex_previous_"


@dataclass(frozen=True, slots=True)
class TopicMemoryReindexChunk:
    ordinal: int
    text: str


@dataclass(frozen=True, slots=True)
class TopicMemoryReindexSource:
    scope_id: str
    artifact_id: str
    revision: int
    title: str
    summary: str
    chunks: tuple[TopicMemoryReindexChunk, ...]

    @property
    def key(self) -> tuple[str, str]:
        return self.scope_id, self.artifact_id

    @property
    def embedding_inputs(self) -> tuple[str, ...]:
        return (f"{self.title}\n{self.summary}", *(chunk.text for chunk in self.chunks))


class TopicMemoryReindexBackend(Protocol):
    profile: EmbeddingProfile
    fingerprint: str
    topic_table: Table
    chunk_table: Table
    shape_table: Table
    staging_progress_tables: frozenset[str]

    def topic_vector_present(self, vectors: Any, /) -> ColumnElement[bool]: ...

    def chunk_vector_present(self, vectors: Any, /) -> ColumnElement[bool]: ...

    async def validate_active(self, connection: AsyncConnection, fingerprint: str | None, /) -> None: ...

    async def validate_staging(self, connection: AsyncConnection, /) -> None: ...

    async def prepare(self, connection: AsyncConnection, *, update_storage_profile: bool) -> None: ...

    async def write_staging(
        self,
        connection: AsyncConnection,
        sources: Sequence[TopicMemoryReindexSource],
        vectors: Sequence[tuple[float, ...]],
        /,
    ) -> None: ...

    async def activate(self, connection: AsyncConnection, *, update_storage_profile: bool) -> None: ...

    async def cleanup(self, connection: AsyncConnection) -> None: ...


async def _table_names(connection: AsyncConnection) -> set[str]:
    return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))


async def staged_fingerprint(connection: AsyncConnection, backend: TopicMemoryReindexBackend) -> str | None:
    names = await _table_names(connection)
    if backend.shape_table.name not in names:
        # Rows without their profile declaration cannot be trusted as progress
        # for this target. prepare() recreates only that derived staging state.
        return None
    row = (
        await connection.execute(
            select(backend.shape_table.c.shape, backend.shape_table.c.profile_fingerprint).where(
                backend.shape_table.c.singleton == 1
            )
        )
    ).one_or_none()
    # Nontransactional DDL can stop after CREATE TABLE or before its declaration
    # insert. prepare() owns completion of either state; inspection must not make
    # that interruption unrecoverable. Missing vector tables likewise have no
    # committed progress and are completed by prepare().
    if row is None:
        return None
    if row[0] != "hybrid" or row[1] is None:
        raise TopicMemoryStorageInvariantError("reindex-staging-shape", 1)
    return str(row[1])


def _topic_completion(
    backend: TopicMemoryReindexBackend,
    active: Any,
    expected: Any,
) -> ColumnElement[bool]:
    staged_topic = backend.topic_table.alias("reindex_topic")
    topic_valid = exists(
        select(staged_topic.c.vector_id)
        .where(
            staged_topic.c.scope_id == active.c.scope_id,
            staged_topic.c.artifact_id == active.c.artifact_id,
            staged_topic.c.revision == active.c.revision,
            staged_topic.c.profile_fingerprint == backend.fingerprint,
            backend.topic_vector_present(staged_topic),
        )
        .correlate(active)
    )
    staged_chunk = backend.chunk_table.alias("reindex_chunk")
    expected_matches = and_(
        expected.c.scope_id == active.c.scope_id,
        expected.c.artifact_id == active.c.artifact_id,
        expected.c.revision == active.c.revision,
    )
    chunk_valid = exists(
        select(staged_chunk.c.vector_id)
        .where(
            staged_chunk.c.scope_id == expected.c.scope_id,
            staged_chunk.c.artifact_id == expected.c.artifact_id,
            staged_chunk.c.revision == expected.c.revision,
            staged_chunk.c.chunk_ordinal == expected.c.chunk_ordinal,
            staged_chunk.c.profile_fingerprint == backend.fingerprint,
            backend.chunk_vector_present(staged_chunk),
        )
        .correlate(expected)
    )
    missing_chunk = exists(select(expected.c.chunk_ordinal).where(expected_matches, ~chunk_valid).correlate(active))
    extra_staged = backend.chunk_table.alias("reindex_extra_chunk")
    expected_for_extra = exists(
        select(expected.c.chunk_ordinal)
        .where(
            expected.c.scope_id == extra_staged.c.scope_id,
            expected.c.artifact_id == extra_staged.c.artifact_id,
            expected.c.revision == extra_staged.c.revision,
            expected.c.chunk_ordinal == extra_staged.c.chunk_ordinal,
        )
        .correlate(extra_staged)
    )
    extra_chunk = exists(
        select(extra_staged.c.vector_id)
        .where(
            extra_staged.c.scope_id == active.c.scope_id,
            extra_staged.c.artifact_id == active.c.artifact_id,
            ~expected_for_extra,
        )
        .correlate(active)
    )
    return topic_valid & ~missing_chunk & ~extra_chunk


async def _sources_from_topics(
    connection: AsyncConnection,
    rows: Sequence[RowMapping],
) -> tuple[TopicMemoryReindexSource, ...]:
    if not rows:
        return ()
    keys = tuple((str(row["scope_id"]), str(row["artifact_id"])) for row in rows)
    chunks = TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE
    chunk_rows = (
        (
            await connection.execute(
                select(chunks.c.scope_id, chunks.c.artifact_id, chunks.c.chunk_ordinal, chunks.c.chunk_text)
                .where(tuple_(chunks.c.scope_id, chunks.c.artifact_id).in_(keys))
                .order_by(chunks.c.scope_id, chunks.c.artifact_id, chunks.c.chunk_ordinal)
            )
        )
        .mappings()
        .all()
    )
    by_topic: dict[tuple[str, str], list[TopicMemoryReindexChunk]] = {}
    for row in chunk_rows:
        by_topic.setdefault((str(row["scope_id"]), str(row["artifact_id"])), []).append(
            TopicMemoryReindexChunk(ordinal=int(row["chunk_ordinal"]), text=str(row["chunk_text"]))
        )
    return tuple(
        TopicMemoryReindexSource(
            scope_id=str(row["scope_id"]),
            artifact_id=str(row["artifact_id"]),
            revision=int(row["revision"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            chunks=tuple(by_topic.get((str(row["scope_id"]), str(row["artifact_id"])), ())),
        )
        for row in rows
    )


async def pending_sources(
    connection: AsyncConnection,
    backend: TopicMemoryReindexBackend,
    *,
    after: tuple[str, str] | None,
    batch_size: int,
) -> tuple[TopicMemoryReindexSource, ...]:
    active = TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.alias("reindex_active")
    expected = TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.alias("reindex_expected")
    statement = select(
        active.c.scope_id, active.c.artifact_id, active.c.revision, active.c.title, active.c.summary
    ).where(~_topic_completion(backend, active, expected))
    if after is not None:
        statement = statement.where(tuple_(active.c.scope_id, active.c.artifact_id) > after)
    rows = (
        (await connection.execute(statement.order_by(active.c.scope_id, active.c.artifact_id).limit(batch_size)))
        .mappings()
        .all()
    )
    return await _sources_from_topics(connection, rows)


async def require_sources_current(
    connection: AsyncConnection,
    sources: Sequence[TopicMemoryReindexSource],
) -> None:
    if not sources:
        return
    active = TOPIC_MEMORY_ACTIVE_TOPICS_TABLE
    keys = tuple(source.key for source in sources)
    rows = (
        (
            await connection.execute(
                select(active.c.scope_id, active.c.artifact_id, active.c.revision, active.c.title, active.c.summary)
                .where(tuple_(active.c.scope_id, active.c.artifact_id).in_(keys))
                .order_by(active.c.scope_id, active.c.artifact_id)
            )
        )
        .mappings()
        .all()
    )
    current = {source.key: source for source in await _sources_from_topics(connection, rows)}
    for source in sources:
        if current.get(source.key) != source:
            raise TopicMemoryStorageInvariantError("reindex-source-changed", source.key)


async def validate_staging_vectors(connection: AsyncConnection, backend: TopicMemoryReindexBackend) -> None:
    await validate_current_topic_vectors(
        connection,
        backend.topic_table,
        backend.chunk_table,
        backend.fingerprint,
        topic_present=backend.topic_vector_present(backend.topic_table),
        chunk_present=backend.chunk_vector_present(backend.chunk_table),
    )
    active = TOPIC_MEMORY_ACTIVE_TOPICS_TABLE
    expected = TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE
    current_topic = exists(
        select(active.c.artifact_id)
        .where(
            active.c.scope_id == backend.topic_table.c.scope_id,
            active.c.artifact_id == backend.topic_table.c.artifact_id,
            active.c.revision == backend.topic_table.c.revision,
        )
        .correlate(backend.topic_table)
    )
    stale_topic = await connection.scalar(
        select(backend.topic_table.c.vector_id)
        .where(or_(~current_topic, backend.topic_table.c.profile_fingerprint != backend.fingerprint))
        .limit(1)
    )
    current_chunk = exists(
        select(expected.c.chunk_ordinal)
        .where(
            expected.c.scope_id == backend.chunk_table.c.scope_id,
            expected.c.artifact_id == backend.chunk_table.c.artifact_id,
            expected.c.revision == backend.chunk_table.c.revision,
            expected.c.chunk_ordinal == backend.chunk_table.c.chunk_ordinal,
        )
        .correlate(backend.chunk_table)
    )
    stale_chunk = await connection.scalar(
        select(backend.chunk_table.c.vector_id)
        .where(or_(~current_chunk, backend.chunk_table.c.profile_fingerprint != backend.fingerprint))
        .limit(1)
    )
    if stale_topic is not None or stale_chunk is not None:
        raise TopicMemoryStorageInvariantError("reindex-staging-stale", 1)


async def staging_counts(connection: AsyncConnection, backend: TopicMemoryReindexBackend) -> dict[str, int]:
    active = TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.alias("count_active")
    expected = TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.alias("count_expected")
    topics_total = int(await connection.scalar(select(func.count()).select_from(active)) or 0)
    chunks_total = int(await connection.scalar(select(func.count()).select_from(expected)) or 0)
    topics_processed = 0
    chunks_processed = 0
    tables_ready = backend.staging_progress_tables <= await _table_names(connection)
    if tables_ready and await staged_fingerprint(connection, backend) == backend.fingerprint:
        topics_processed = int(
            await connection.scalar(
                select(func.count()).select_from(active).where(_topic_completion(backend, active, expected))
            )
            or 0
        )
        staged_chunk = backend.chunk_table.alias("count_staged_chunk")
        chunks_processed = int(
            await connection.scalar(
                select(func.count())
                .select_from(expected)
                .where(
                    exists(
                        select(staged_chunk.c.vector_id)
                        .where(
                            staged_chunk.c.scope_id == expected.c.scope_id,
                            staged_chunk.c.artifact_id == expected.c.artifact_id,
                            staged_chunk.c.revision == expected.c.revision,
                            staged_chunk.c.chunk_ordinal == expected.c.chunk_ordinal,
                            staged_chunk.c.profile_fingerprint == backend.fingerprint,
                            backend.chunk_vector_present(staged_chunk),
                        )
                        .correlate(expected)
                    )
                )
            )
            or 0
        )
    return {
        "topics_total": topics_total,
        "topics_processed": topics_processed,
        "topics_remaining": topics_total - topics_processed,
        "chunks_total": chunks_total,
        "chunks_processed": chunks_processed,
        "chunks_remaining": chunks_total - chunks_processed,
    }


class TopicMemoryReindexReport(BaseModel):
    """Operator-visible dry-run or activation result; never includes Memory content."""

    action: Literal["dry-run", "apply"]
    ready: bool = False
    activated: bool = False
    profile_fingerprint: str
    counts: dict[str, int] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()
    embedded_vectors: int = 0
    atomic_memory_rebuild_required: bool = False
    atomic_memory_rebuilt_rows: int = 0


@dataclass(frozen=True, slots=True)
class _Inspection:
    report: TopicMemoryReindexReport
    update_storage_profile: bool = False
    cleanup_required: bool = False
    atomic_rebuild_required: bool = False


async def _active_fingerprint(connection: AsyncConnection) -> str | None:
    shape = "pc_topic_memory_retrieval_shape"
    rows = (await connection.execute(text(f"SELECT shape,profile_fingerprint FROM {shape}"))).all()  # noqa: S608
    if len(rows) == 1 and rows[0][0] == "fts" and rows[0][1] is None:
        return None
    if len(rows) != 1 or rows[0][0] != "hybrid" or rows[0][1] is None:
        raise TopicMemoryStorageInvariantError("reindex-active-shape", len(rows))
    return str(rows[0][1])


async def _declared_profile(connection: AsyncConnection) -> EmbeddingProfile | None:
    names = await _table_names(connection)
    if "pc_server_storage_profile" not in names:
        return None
    rows = (await connection.execute(text("SELECT singleton,embedding_profile FROM pc_server_storage_profile"))).all()
    if len(rows) != 1 or rows[0][0] != 1:
        raise TopicMemoryStorageInvariantError("reindex-storage-profile", len(rows))
    try:
        return EmbeddingProfile.model_validate_json(rows[0][1])
    except ValueError as error:
        raise TopicMemoryStorageInvariantError("reindex-storage-profile", 1) from error


async def _atomic_profile_error(connection: AsyncConnection, profile: EmbeddingProfile) -> str | None:
    """Reject a Topic-only declaration change that would strand Atomic vectors."""

    names = await _table_names(connection)
    if "pc_atomic_memory_current" not in names:
        return "managed Atomic Memory storage is missing; complete Server migration first"
    fingerprint = atomic_memory_profile_fingerprint(profile)
    invalid = (
        "embedding IS NOT NULL AND (embedding_input_hash IS NULL OR profile_fingerprint IS NULL "
        "OR profile_fingerprint <> :fingerprint OR projection_format <> 'powercontext.atomic-memory.current.v1'"
    )
    if connection.dialect.name == "sqlite":
        invalid += " OR length(embedding) <> :vector_bytes"
    invalid += ")"
    incompatible = await connection.scalar(
        text(f"SELECT 1 FROM pc_atomic_memory_current WHERE {invalid} LIMIT 1"),  # noqa: S608
        {"fingerprint": fingerprint, "vector_bytes": profile.dimension * 4},
    )
    if incompatible is not None:
        return "Atomic Memory vectors are not target-compatible; explicit Atomic Memory rebuild is required"
    if connection.dialect.name == "mysql":
        actual = await connection.scalar(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'pc_atomic_memory_current' "
                "AND column_name = 'embedding'"
            )
        )
        if str(actual).upper() != f"VECTOR({profile.dimension})":
            return (
                "Atomic Memory's physical vector column is not target-compatible; "
                "explicit Atomic Memory rebuild is required"
            )
    return None


def _atomic_index(connection: AsyncConnection, profile: EmbeddingProfile) -> AtomicMemoryIndex:
    from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
    from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

    return (
        SQLiteAtomicMemoryIndex(profile) if connection.dialect.name == "sqlite" else OceanBaseAtomicMemoryIndex(profile)
    )


async def _verify_atomic_current(connection: AsyncConnection, profile: EmbeddingProfile) -> None:
    if await _declared_profile(connection) is None:
        return
    # A declaration alone is not readiness: active heads, current rows, metadata,
    # vectors, retained evidence and exact-revision projections must all agree.
    from powercontext.builtin.runtime.atomic_memory_rebuild import verify_atomic_memory_current

    verification = await verify_atomic_memory_current(
        connection,
        _atomic_index(connection, profile),
        require_evidence=True,
        require_evidence_projection=True,
    )
    if not verification.ready:
        raise TopicMemoryStorageInvariantError("reindex-atomic-memory", verification.errors)


async def _verify_managed_target(connection: AsyncConnection, profile: EmbeddingProfile) -> None:
    declared = await _declared_profile(connection)
    if declared is not None and declared != profile:
        raise TopicMemoryStorageInvariantError("reindex-storage-profile", declared.profile_id)
    if declared is not None:
        await _verify_atomic_current(connection, profile)


async def _inspect_atomic_rebuild(
    connection: AsyncConnection,
    profile: EmbeddingProfile,
    *,
    requested: bool,
) -> tuple[bool, str | None]:
    atomic_error = await _atomic_profile_error(connection, profile)
    rebuild_can_address = atomic_error is None or not atomic_error.startswith("managed Atomic Memory storage")
    if atomic_error is None:
        from powercontext.builtin.runtime.atomic_memory_rebuild import verify_atomic_memory_current

        try:
            verification = await verify_atomic_memory_current(
                connection,
                _atomic_index(connection, profile),
                require_evidence=True,
                require_evidence_projection=True,
            )
        except AtomicMemoryIndexError as error:
            return False, str(error)
        if not verification.ready:
            atomic_error = "Atomic Memory vectors are not target-compatible; explicit Atomic Memory rebuild is required"
    if atomic_error is None:
        return False, None
    if requested and rebuild_can_address:
        return True, None
    return False, atomic_error


async def _inspect_managed_storage(
    connection: AsyncConnection,
    backend: TopicMemoryReindexBackend,
    declared: EmbeddingProfile | None,
    *,
    activated: bool,
    rebuild_requested: bool,
) -> tuple[bool, bool, tuple[str, ...]]:
    if declared is None:
        errors = ("explicit Atomic Memory rebuild requires managed Server storage",) if rebuild_requested else ()
        return False, False, errors
    update_storage_profile = declared != backend.profile
    errors = []
    if activated and update_storage_profile:
        errors.append("the managed Server vector declaration differs from active Topic vectors")
    atomic_rebuild_required, atomic_error = await _inspect_atomic_rebuild(
        connection,
        backend.profile,
        requested=rebuild_requested,
    )
    if atomic_error is not None:
        errors.append(atomic_error)
    return update_storage_profile, atomic_rebuild_required, tuple(errors)


async def _inspect(
    connection: AsyncConnection,
    backend: TopicMemoryReindexBackend,
    *,
    action: Literal["dry-run", "apply"],
    rebuild_atomic_memory: bool,
) -> _Inspection:
    errors: list[str] = []
    update_storage_profile = False
    cleanup_required = False
    atomic_rebuild_required = False
    activated = False
    counts = await staging_counts(connection, backend)
    try:
        active_fingerprint = await _active_fingerprint(connection)
        names = await _table_names(connection)
        cleanup_required = any(name.startswith(TOPIC_MEMORY_REINDEX_PREVIOUS_PREFIX) for name in names)
        declared = await _declared_profile(connection)
        activated = active_fingerprint == backend.fingerprint
        if activated:
            counts.update(
                topics_processed=counts["topics_total"],
                topics_remaining=0,
                chunks_processed=counts["chunks_total"],
                chunks_remaining=0,
            )
        if cleanup_required and not activated:
            errors.append("an earlier activation needs recovery with its original target profile")
        staged = await staged_fingerprint(connection, backend)
        if staged is not None and staged != backend.fingerprint:
            errors.append("staging belongs to a different embedding profile")
        if activated and staged is not None:
            errors.append("activated vectors and incomplete staging coexist; inspect recovery state")
        if not errors and (not cleanup_required or activated):
            await backend.validate_active(connection, active_fingerprint)
        update_storage_profile, atomic_rebuild_required, managed_errors = await _inspect_managed_storage(
            connection,
            backend,
            declared,
            activated=activated,
            rebuild_requested=rebuild_atomic_memory,
        )
        errors.extend(managed_errors)
    except TopicMemoryStorageInvariantError as error:
        activated = False
        errors.append(str(error))
    return _Inspection(
        report=TopicMemoryReindexReport(
            action=action,
            ready=not errors,
            activated=activated,
            profile_fingerprint=backend.fingerprint,
            counts=counts,
            errors=tuple(dict.fromkeys(errors)),
            atomic_memory_rebuild_required=atomic_rebuild_required,
        ),
        update_storage_profile=update_storage_profile,
        cleanup_required=cleanup_required,
        atomic_rebuild_required=atomic_rebuild_required,
    )


async def plan_topic_memory_reindex(
    connection: AsyncConnection,
    backend: TopicMemoryReindexBackend,
    /,
    *,
    rebuild_atomic_memory: bool = False,
) -> TopicMemoryReindexReport:
    """Inspect source, staging and shared-profile readiness without writing or embedding."""

    return (await _inspect(connection, backend, action="dry-run", rebuild_atomic_memory=rebuild_atomic_memory)).report


def _canonical_vectors(
    sources: Sequence[TopicMemoryReindexSource],
    vectors: Sequence[Sequence[float]],
    profile: EmbeddingProfile,
) -> tuple[tuple[float, ...], ...]:
    expected = sum(len(source.embedding_inputs) for source in sources)
    if len(vectors) != expected:
        raise TopicMemoryCapabilityError(
            "embedding-result", f"provider returned {len(vectors)} vectors for {expected} Topic Memory inputs"
        )
    try:
        return tuple(
            canonical_embedding(tuple(vector), dimension=profile.dimension, normalization=profile.normalization)
            for vector in vectors
        )
    except ValueError as error:
        raise TopicMemoryCapabilityError("embedding-values", str(error)) from error


async def _stage_pending_vectors(
    database: AsyncDatabase,
    backend: TopicMemoryReindexBackend,
    *,
    embedding_model: EmbeddingModel | None,
    batch_size: int,
    progress: Callable[[dict[str, int]], None],
) -> int:
    after: tuple[str, str] | None = None
    embedded_vectors = 0
    while True:
        async with database.transaction() as connection:
            sources = await pending_sources(connection, backend, after=after, batch_size=batch_size)
        if not sources:
            return embedded_vectors
        inputs = tuple(text for source in sources for text in source.embedding_inputs)
        if embedding_model is None:
            raise TopicMemoryCapabilityError(
                "embedding-profile", "pending Topic Memory vectors require the configured embedding provider"
            )
        # Provider I/O must never hold a database transaction or Source row locks.
        embedded = await embedding_model.embed(inputs)
        vectors = _canonical_vectors(sources, embedded.vectors, backend.profile)
        async with database.transaction() as connection:
            await require_sources_current(connection, sources)
            await backend.write_staging(connection, sources, vectors)
        embedded_vectors += len(vectors)
        after = sources[-1].key
        async with database.transaction() as connection:
            counts = await staging_counts(connection, backend)
        progress(counts)


async def _rebuild_managed_atomic(
    database: AsyncDatabase,
    profile: EmbeddingProfile,
    *,
    embedding_model: EmbeddingModel | None,
    batch_size: int,
) -> int:
    if embedding_model is None:
        raise TopicMemoryCapabilityError(
            "embedding-profile", "Atomic Memory rebuild requires the configured embedding provider"
        )
    async with database.transaction() as connection:
        if await _declared_profile(connection) is None:
            raise TopicMemoryStorageInvariantError("reindex-storage-profile", 0)
        index = _atomic_index(connection, profile)
    from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection

    report = await rebuild_atomic_memory_projection(
        database,
        index,
        maintenance_confirmed=True,
        embedding_model=embedding_model,
        batch_size=batch_size,
    )
    if not report.ready:
        raise TopicMemoryStorageInvariantError("reindex-atomic-memory", report.errors)
    return int(report.counts.get("rebuilt_rows", 0))


async def reindex_topic_memory(
    database: AsyncDatabase,
    backend: TopicMemoryReindexBackend,
    *,
    embedding_model: EmbeddingModel | None,
    maintenance_confirmed: bool,
    dry_run: bool = False,
    batch_size: int = 25,
    rebuild_atomic_memory: bool = False,
    progress: Callable[[dict[str, int]], None] = lambda _counts: None,
) -> TopicMemoryReindexReport:
    """Stage current vectors in committed batches, then atomically activate the target shape.

    The old active generation is never changed by preparation or provider work.
    Apply requires stopped writers because final source validation and activation
    are intentionally separate from external inference. Repeating apply resumes
    complete staged revisions and recognizes an already committed activation.
    """

    if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise TopicMemoryCapabilityError("reindex-batch-size", "batch size must be between 1 and 1000")
    if not dry_run and embedding_model is not None and embedding_model.profile != backend.profile:
        raise TopicMemoryCapabilityError(
            "embedding-profile", "reindex model does not match the configured target profile"
        )
    if not dry_run and not maintenance_confirmed:
        raise TopicMemoryCapabilityError(
            "maintenance-required", "Topic Memory reindex requires stopped writers and --maintenance-confirmed"
        )
    action = "dry-run" if dry_run else "apply"
    async with database.transaction() as connection:
        inspection = await _inspect(
            connection,
            backend,
            action=action,
            rebuild_atomic_memory=rebuild_atomic_memory,
        )
    if inspection.report.errors or dry_run:
        return inspection.report

    atomic_rebuilt_rows = 0
    if inspection.report.activated:
        if inspection.atomic_rebuild_required:
            atomic_rebuilt_rows = await _rebuild_managed_atomic(
                database,
                backend.profile,
                embedding_model=embedding_model,
                batch_size=batch_size,
            )
        if inspection.cleanup_required:
            async with database.transaction() as connection:
                # A committed OceanBase rename is not proof that its vectors and
                # shared declaration are usable. Never discard recovery first.
                await backend.validate_active(connection, backend.fingerprint)
                await _verify_managed_target(connection, backend.profile)
                await backend.cleanup(connection)
                await backend.validate_active(connection, backend.fingerprint)
        return inspection.report.model_copy(update={"atomic_memory_rebuilt_rows": atomic_rebuilt_rows})

    async with database.transaction() as connection:
        await backend.prepare(connection, update_storage_profile=inspection.update_storage_profile)

    embedded_vectors = await _stage_pending_vectors(
        database,
        backend,
        embedding_model=embedding_model,
        batch_size=batch_size,
        progress=progress,
    )
    if inspection.atomic_rebuild_required:
        # Atomic has its own committed row recovery. If a later Topic activation
        # fails, keep service stopped and resume this same target and option.
        atomic_rebuilt_rows = await _rebuild_managed_atomic(
            database,
            backend.profile,
            embedding_model=embedding_model,
            batch_size=batch_size,
        )

    async with database.transaction() as connection:
        if await pending_sources(connection, backend, after=None, batch_size=1):
            raise TopicMemoryStorageInvariantError("reindex-staging-incomplete", 1)
        await backend.validate_staging(connection)
        # Check the real Atomic projection before OceanBase commits its rename;
        # the managed declaration is still old until activation swaps it.
        await _verify_atomic_current(connection, backend.profile)
        await backend.activate(connection, update_storage_profile=inspection.update_storage_profile)
        # SQLite rolls activation back if this validation fails. OceanBase has
        # already committed its rename, so its previous generation must remain.
        await backend.validate_active(connection, backend.fingerprint)
        await _verify_managed_target(connection, backend.profile)
    # OceanBase DDL is nontransactional. Keep the previous generation until the
    # activated vectors and shared declaration have both passed validation.
    async with database.transaction() as connection:
        await backend.cleanup(connection)
        await backend.validate_active(connection, backend.fingerprint)
        counts = await staging_counts(connection, backend)
    counts.update(
        topics_processed=counts["topics_total"],
        topics_remaining=0,
        chunks_processed=counts["chunks_total"],
        chunks_remaining=0,
    )
    progress(counts)
    return TopicMemoryReindexReport(
        action="apply",
        ready=True,
        activated=True,
        profile_fingerprint=backend.fingerprint,
        counts=counts,
        embedded_vectors=embedded_vectors,
        atomic_memory_rebuild_required=inspection.atomic_rebuild_required,
        atomic_memory_rebuilt_rows=atomic_rebuilt_rows,
    )


__all__ = [
    "TopicMemoryReindexBackend",
    "TopicMemoryReindexChunk",
    "TopicMemoryReindexReport",
    "TopicMemoryReindexSource",
    "pending_sources",
    "plan_topic_memory_reindex",
    "reindex_topic_memory",
    "require_sources_current",
    "staged_fingerprint",
    "staging_counts",
    "validate_staging_vectors",
]
