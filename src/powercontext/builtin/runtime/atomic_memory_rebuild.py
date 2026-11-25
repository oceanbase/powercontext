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

"""Offline current-projection repair and verification of retained Atomic history."""

from __future__ import annotations

import json
import struct
from collections.abc import AsyncIterator, Callable
from hashlib import sha256
from itertools import groupby
from time import perf_counter

from pydantic import BaseModel, Field
from sqlalchemy import and_, case, exists, func, inspect, literal, select, tuple_
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemory,
    AtomicMemoryContent,
    AtomicMemoryRecord,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.memory.canonical import canonical_json
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_evidence import (
    AtomicMemoryEvidenceAnnotation,
    AtomicMemoryEvidenceRepository,
)
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndex,
    AtomicMemoryIndexError,
    AtomicMemoryProjectionPublisher,
    PreparedAtomicMemoryProjection,
    atomic_memory_embedding_input,
    atomic_memory_embedding_input_hash,
    atomic_memory_profile_fingerprint,
    drop_obsolete_atomic_memory_projection,
    load_atomic_memory_tags,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import (
    ATOMIC_MEMORY_ALIAS_FORMAT,
    ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE,
    ATOMIC_MEMORY_PROJECTION_FORMAT,
)
from powercontext.builtin.persistence.atomic_memory_readiness import (
    atomic_memory_readiness_issues,
    ensure_supported_atomic_memory_schema,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.revision_metadata_schema import ensure_artifact_revision_metadata_schema
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_REVISION_COLUMNS,
    ARTIFACTS_TABLE,
)
from powercontext.sources import SourceRef


class AtomicMemoryProjectionRebuildReport(BaseModel):
    ready: bool = False
    profile_fingerprint: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()


async def _require_authority(connection: AsyncConnection, *, require_evidence: bool = True) -> None:
    await ensure_supported_atomic_memory_schema(connection)
    tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    required = {
        "pc_artifact_heads",
        "pc_artifacts",
        "pc_artifact_tags",
        "pc_access_owners",
        "pc_access_relationships",
        "pc_access_relationship_heads",
    }
    if not required <= tables:
        raise AtomicMemoryIndexError(
            "authority-schema", "Atomic Memory authority is absent; complete initialization and history migration first"
        )
    issues = await atomic_memory_readiness_issues(connection, tables, require_evidence=require_evidence)
    if issues:
        raise AtomicMemoryIndexError("migration-pending", "Atomic Memory migration is not ready: " + "; ".join(issues))
    heads = ARTIFACT_HEADS_TABLE
    artifacts = ARTIFACTS_TABLE
    identity = and_(
        artifacts.c.scope_id == heads.c.scope_id,
        artifacts.c.family == heads.c.family,
        artifacts.c.artifact_id == heads.c.artifact_id,
    )
    missing_history_head = await connection.scalar(
        select(artifacts.c.artifact_id)
        .where(artifacts.c.family == AtomicMemory.family, ~exists(select(heads.c.artifact_id).where(identity)))
        .limit(1)
    )
    if missing_history_head is not None:
        raise AtomicMemoryIndexError("authority-orphan", "Atomic Memory history has no head")
    latest_revision = select(func.max(artifacts.c.revision)).where(identity).scalar_subquery()
    inconsistent_head = await connection.scalar(
        select(heads.c.artifact_id)
        .where(
            heads.c.family == AtomicMemory.family,
            (latest_revision.is_(None)) | (heads.c.revision != latest_revision),
        )
        .limit(1)
    )
    if inconsistent_head is not None:
        raise AtomicMemoryIndexError("authority-head", "Atomic Memory head does not select its latest revision")


async def _identity_batch(
    connection: AsyncConnection, after: tuple[str, str] | None, batch_size: int
) -> tuple[tuple[str, str], ...]:
    heads = ARTIFACT_HEADS_TABLE
    statement = select(heads.c.scope_id, heads.c.artifact_id).where(heads.c.family == AtomicMemory.family)
    if after is not None:
        statement = statement.where(tuple_(heads.c.scope_id, heads.c.artifact_id) > after)
    rows = (await connection.execute(statement.order_by(heads.c.scope_id, heads.c.artifact_id).limit(batch_size))).all()
    return tuple((str(row.scope_id), str(row.artifact_id)) for row in rows)


async def _evidence_history(
    connection: AsyncConnection,
    artifacts: ArtifactRepository,
    batch_size: int,
    sources: SourceRepository | None,
    *,
    selected_scope: str | None = None,
) -> AsyncIterator[AtomicMemoryEvidenceAnnotation]:
    table = ARTIFACTS_TABLE
    evidence = AtomicMemoryEvidenceRepository()
    after: tuple[str, str, int] | None = None
    while True:
        statement = select(*ARTIFACT_REVISION_COLUMNS).where(table.c.family == AtomicMemory.family)
        if selected_scope is not None:
            statement = statement.where(table.c.scope_id == selected_scope)
        if after is not None:
            statement = statement.where(tuple_(table.c.scope_id, table.c.artifact_id, table.c.revision) > after)
        rows = (
            await connection.execute(
                statement.order_by(table.c.scope_id, table.c.artifact_id, table.c.revision).limit(batch_size)
            )
        ).all()
        if not rows:
            break
        # Rows are ordered by scope, so one page holds contiguous per-scope groups.
        for scope_id, group in groupby(rows, key=lambda row: str(row.scope_id)):
            revisions = tuple(group)
            lineage = ARTIFACT_LINEAGE_SOURCES_TABLE
            keys = tuple((str(row.artifact_id), int(row.revision)) for row in revisions)
            source_rows = (
                (
                    await connection.execute(
                        select(lineage)
                        .where(
                            lineage.c.scope_id == scope_id,
                            lineage.c.family == AtomicMemory.family,
                            tuple_(lineage.c.artifact_id, lineage.c.revision).in_(keys),
                        )
                        .order_by(lineage.c.artifact_id, lineage.c.revision, lineage.c.ordinal)
                    )
                )
                .mappings()
                .all()
            )
            by_revision: dict[tuple[str, int], list[SourceRef]] = {}
            for row in source_rows:
                by_revision.setdefault((str(row["artifact_id"]), int(row["revision"])), []).append(
                    SourceRef(source_type=row["source_type"], source_id=row["source_id"])
                )
            for row in revisions:
                reference = ArtifactRef(
                    family=AtomicMemory.family, artifact_id=str(row.artifact_id), revision=int(row.revision)
                )
                AtomicMemoryContent.model_validate(artifacts.decode_content(row._mapping))
                # Evidence binds exact Source identities, independently of citation
                # positions introduced by a later schema revision.
                yield await evidence.verify(
                    connection,
                    scope_id,
                    reference,
                    by_revision.get((reference.artifact_id, reference.revision), ()),
                    sources=sources,
                )
                after = scope_id, reference.artifact_id, reference.revision


def _obsolete_evidence():
    projection = ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE
    return ~exists(
        select(ARTIFACTS_TABLE.c.artifact_id).where(
            ARTIFACTS_TABLE.c.scope_id == projection.c.scope_id,
            ARTIFACTS_TABLE.c.family == AtomicMemory.family,
            ARTIFACTS_TABLE.c.artifact_id == projection.c.artifact_id,
            ARTIFACTS_TABLE.c.revision == projection.c.revision,
        )
    )


async def rebuild_atomic_memory_evidence(
    connection: AsyncConnection,
    index: AtomicMemoryIndex,
    *,
    batch_size: int = 100,
    sources: SourceRepository | None = None,
    guard: Callable[[], None] = lambda: None,
    scope_id: str | None = None,
) -> None:
    """Rebuild disposable revision facts without changing history or invoking models.

    The maintenance caller owns schema admission, writer exclusion and the transaction.
    """
    if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise AtomicMemoryIndexError("batch-size", "Projection rebuild batch size must be between 1 and 1000")
    artifacts = ArtifactRepository((AtomicMemory,))
    async for annotation in _evidence_history(connection, artifacts, batch_size, sources, selected_scope=scope_id):
        guard()
        await index.project_evidence(connection, annotation)
    table = ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE
    statement = table.delete().where(_obsolete_evidence())
    if scope_id is not None:
        statement = statement.where(table.c.scope_id == scope_id)
    await connection.execute(statement)


async def _verify_history(
    connection: AsyncConnection,
    index: AtomicMemoryIndex,
    artifacts: ArtifactRepository,
    batch_size: int,
    sources: SourceRepository | None,
    require_projection: bool,
) -> dict[str, int]:
    revisions = neutral_sources = differing = 0
    async for annotation in _evidence_history(connection, artifacts, batch_size, sources):
        revisions += 1
        neutral_sources += sum(
            source.declaration.authority == "untrusted" and source.declaration.verification == "unknown"
            for source in annotation.sources
        )
        if require_projection:
            projected = await index.read_evidence_projection(connection, annotation.scope_id, annotation.artifact_ref)
            differing += projected != annotation
    counts = {"historical_revisions": revisions, "neutral_sources": neutral_sources}
    if require_projection:
        counts["evidence_projection_differences"] = differing + int(
            await connection.scalar(
                select(func.count()).select_from(ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE).where(_obsolete_evidence())
            )
            or 0
        )
    return counts


async def _record(
    connection: AsyncConnection,
    artifacts: ArtifactRepository,
    scope_id: str,
    artifact_id: str,
    *,
    for_update: bool,
) -> AtomicMemoryRecord:
    state_repository = AtomicMemoryStateRepository()
    state = await state_repository.get(connection, scope_id, artifact_id)
    if for_update:
        await state_repository.require_summary(connection, scope_id, artifact_id, state)
    # Projection repair needs only exact content and authoritative state. Historical
    # migration checkpoints must not depend on lineage tables added by later steps.
    heads, bodies = ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE
    statement = (
        select(heads.c.artifact_id, heads.c.family, heads.c.revision, bodies.c.content)
        .select_from(
            heads.outerjoin(
                bodies,
                and_(*(heads.c[name] == bodies.c[name] for name in ("scope_id", "family", "artifact_id", "revision"))),
            )
        )
        .where(
            heads.c.scope_id == scope_id,
            heads.c.family == AtomicMemory.family,
            heads.c.artifact_id == artifact_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None or row["content"] is None:
        raise AtomicMemoryIndexError("authority-content", "Atomic Memory head has no authoritative content")
    artifact = AtomicMemory(
        artifact_id=str(row["artifact_id"]),
        revision=int(row["revision"]),
        content=AtomicMemoryContent.model_validate(artifacts.decode_content(row)),
    )
    return AtomicMemoryRecord(artifact=artifact, state=state)


def _obsolete_current(index: AtomicMemoryIndex):
    current = index.table
    heads = ARTIFACT_HEADS_TABLE
    return ~exists(
        select(heads.c.artifact_id).where(
            heads.c.scope_id == current.c.scope_id,
            heads.c.artifact_id == current.c.artifact_id,
            heads.c.family == AtomicMemory.family,
            heads.c.lifecycle_state == "active",
            heads.c.merged_into_id.is_(None),
        )
    )


def _obsolete_alias(index: AtomicMemoryIndex):
    alias, heads = index.alias_table, ARTIFACT_HEADS_TABLE
    return ~exists(
        select(heads.c.artifact_id).where(
            heads.c.scope_id == alias.c.scope_id,
            heads.c.family == AtomicMemory.family,
            heads.c.artifact_id == alias.c.alias_artifact_id,
            heads.c.lifecycle_state == "deprecated",
            heads.c.merged_into_id.is_not(None),
        )
    )


def _alias_differences(index: AtomicMemoryIndex, record: AtomicMemoryRecord, row: RowMapping) -> list[str]:
    content = record.artifact.content
    profile = index.capabilities.embedding_profile
    expected = {
        "alias_revision": record.ref.revision,
        "merged_state_version": record.state.state_version,
        "target_artifact_id": record.state.merged_into_id,
        "projection_format": ATOMIC_MEMORY_ALIAS_FORMAT,
        "kind": content.kind,
        "text": content.text,
        "searchable_text": analyze_text(atomic_memory_embedding_input(content.kind, content.text)),
        "profile_fingerprint": None if profile is None else atomic_memory_profile_fingerprint(profile),
        "embedding_input_hash": None
        if profile is None
        else atomic_memory_embedding_input_hash(content.kind, content.text),
    }
    differing = [name for name, value in expected.items() if row[name] != value]
    if (row["embedding"] is None) != (profile is None):
        differing.append("embedding")
    elif profile is not None and isinstance(row["embedding"], bytes) and len(row["embedding"]) != profile.dimension * 4:
        differing.append("embedding_dimension")
    return differing


async def verify_atomic_memory_aliases(
    connection: AsyncConnection, index: AtomicMemoryIndex, *, batch_size: int = 100
) -> AtomicMemoryProjectionRebuildReport:
    """Compare the complete alias identity set and fields with latest authority."""
    artifacts = ArtifactRepository((AtomicMemory,))
    errors: list[str] = []
    after = None
    merged = 0
    page_size = min(batch_size, 400) if connection.dialect.name == "sqlite" else batch_size
    while batch := await _identity_batch(connection, after, page_size):
        aliases = {
            (str(row["scope_id"]), str(row["alias_artifact_id"])): row
            for row in (
                await connection.execute(
                    select(index.alias_table).where(
                        tuple_(index.alias_table.c.scope_id, index.alias_table.c.alias_artifact_id).in_(batch)
                    )
                )
            ).mappings()
        }
        for scope_id, artifact_id in batch:
            record = await _record(connection, artifacts, scope_id, artifact_id, for_update=False)
            after = scope_id, artifact_id
            if record.state.state is not AtomicMemoryStateValue.MERGED:
                continue
            merged += 1
            row = aliases.get((scope_id, artifact_id))
            if row is None:
                errors.append(f"{scope_id}/{artifact_id}: merged alias is missing")
            elif differing := _alias_differences(index, record, row):
                errors.append(f"{scope_id}/{artifact_id}: alias differs: {', '.join(differing)}")
    obsolete = int(
        await connection.scalar(select(func.count()).select_from(index.alias_table).where(_obsolete_alias(index))) or 0
    )
    if obsolete:
        errors.append(f"{obsolete} nonmerged or orphan rows remain in the alias projection")
    return AtomicMemoryProjectionRebuildReport(
        ready=not errors, counts={"alias_rows": merged, "obsolete_alias_rows": obsolete}, errors=tuple(errors)
    )


async def _retained_projection(
    connection: AsyncConnection, index: AtomicMemoryIndex, scope_id: str, record: AtomicMemoryRecord
) -> RowMapping | None:
    """Select an exact compatible stored row without decoding its original vector."""
    alias = index.alias_table
    if record.state.state is AtomicMemoryStateValue.MERGED:
        row = (
            (
                await connection.execute(
                    select(alias).where(
                        alias.c.scope_id == scope_id, alias.c.alias_artifact_id == record.ref.artifact_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None and _compatible_projection_vector(index, record, row, alias=True):
            return row
    current = index.table
    row = (
        (
            await connection.execute(
                select(current).where(current.c.scope_id == scope_id, current.c.artifact_id == record.ref.artifact_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return row if _compatible_projection_vector(index, record, row, alias=False) else None


def _compatible_projection_vector(
    index: AtomicMemoryIndex, record: AtomicMemoryRecord, row: RowMapping, *, alias: bool
) -> bool:
    # Authority metadata may be repaired independently of the unchanged embedding input.
    content, profile = record.artifact.content, index.capabilities.embedding_profile
    return not (
        row["kind"] != content.kind
        or row["text"] != content.text
        or row["searchable_text"] != analyze_text(atomic_memory_embedding_input(content.kind, content.text))
        or row["projection_format"] != (ATOMIC_MEMORY_ALIAS_FORMAT if alias else ATOMIC_MEMORY_PROJECTION_FORMAT)
        or row["profile_fingerprint"] != (None if profile is None else atomic_memory_profile_fingerprint(profile))
        or row["embedding_input_hash"]
        != (None if profile is None else atomic_memory_embedding_input_hash(content.kind, content.text))
        or (row["embedding"] is None) != (profile is None)
        or (
            profile is not None
            and isinstance(row["embedding"], bytes)
            and len(row["embedding"]) != profile.dimension * 4
        )
    )


def _prepared_retained(
    index: AtomicMemoryIndex, record: AtomicMemoryRecord, row: RowMapping
) -> PreparedAtomicMemoryProjection:
    content = record.artifact.content
    vector = row["embedding"]
    if isinstance(vector, bytes):
        vector = struct.unpack(f"<{len(vector) // 4}f", vector)
    return PreparedAtomicMemoryProjection(
        kind=content.kind,
        text=content.text,
        description=content.description,
        searchable_text=row["searchable_text"],
        content_hash=sha256(canonical_json(content.model_dump(mode="json", by_alias=True))).hexdigest(),
        embedding=None if vector is None else tuple(float(value) for value in vector),
        embedding_profile=index.capabilities.embedding_profile,
        profile_fingerprint=row["profile_fingerprint"],
        embedding_input_hash=row["embedding_input_hash"],
    )


async def _projection_differences(
    connection: AsyncConnection,
    index: AtomicMemoryIndex,
    scope_id: str,
    record: AtomicMemoryRecord,
    row: RowMapping,
) -> list[str]:
    artifact_id = record.ref.artifact_id
    content = record.artifact.content
    profile = index.capabilities.embedding_profile
    fingerprint = None if profile is None else atomic_memory_profile_fingerprint(profile)
    expected = {
        "revision": record.artifact.revision,
        "state_version": record.state.state_version,
        "content_hash": sha256(canonical_json(content.model_dump(mode="json", by_alias=True))).hexdigest(),
        "projection_format": ATOMIC_MEMORY_PROJECTION_FORMAT,
        "kind": content.kind,
        "text": content.text,
        "description": content.description,
        "searchable_text": analyze_text(atomic_memory_embedding_input(content.kind, content.text)),
        "profile_fingerprint": fingerprint,
        "embedding_input_hash": None
        if profile is None
        else atomic_memory_embedding_input_hash(content.kind, content.text),
    }
    differing = [name for name, value in expected.items() if row[name] != value]
    if tuple(json.loads(row["tag_keys"])) != await load_atomic_memory_tags(connection, scope_id, artifact_id):
        differing.append("tag_keys")
    if (row["embedding"] is None) != (profile is None):
        differing.append("embedding")
    elif profile is not None and connection.dialect.name == "sqlite" and len(row["embedding"]) != profile.dimension * 4:
        differing.append("embedding_dimension")
    return differing


async def verify_atomic_memory_evidence(
    connection: AsyncConnection,
    index: AtomicMemoryIndex,
    *,
    batch_size: int = 100,
    sources: SourceRepository | None = None,
) -> AtomicMemoryProjectionRebuildReport:
    """Validate exact revision facts independently of the deployed vector profile."""
    if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise AtomicMemoryIndexError("batch-size", "Projection rebuild batch size must be between 1 and 1000")
    await _require_authority(connection)
    counts = await _verify_history(connection, index, ArtifactRepository((AtomicMemory,)), batch_size, sources, True)
    differences = counts["evidence_projection_differences"]
    return AtomicMemoryProjectionRebuildReport(
        ready=not differences,
        counts=counts,
        errors=(f"{differences} evidence projections differ from retained history",) if differences else (),
    )


def _verification_columns(index: AtomicMemoryIndex, available: set[str], dialect: str):
    projection_columns = []
    for column in index.table.c:
        if column.name == "description" and column.name not in available:
            projection_columns.append(literal(None).label("description"))
        elif column.name == "embedding" and dialect == "mysql":
            # OceanBase enforces the registered vector dimension. This audit uses
            # only presence; avoid transferring and decoding every vector value.
            projection_columns.append(case((column.is_(None), None), else_=True).label("embedding"))
        else:
            projection_columns.append(column)
    return projection_columns


async def verify_atomic_memory_current(
    connection: AsyncConnection,
    index: AtomicMemoryIndex,
    /,
    *,
    batch_size: int = 100,
    sources: SourceRepository | None = None,
    require_evidence: bool = False,
    require_evidence_projection: bool = False,
    require_alias_projection: bool = False,
) -> AtomicMemoryProjectionRebuildReport:
    """Check current projections and optionally each exact revision's retained evidence without business row locks.

    The caller owns a consistent read snapshot or an exclusive maintenance
    transaction, including for identities created after migration. Frozen
    pre-evidence migrations retain projection-only validation; current maintenance
    and rebuild operations explicitly require evidence verification.
    """

    if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise AtomicMemoryIndexError("batch-size", "Projection rebuild batch size must be between 1 and 1000")
    await _require_authority(connection, require_evidence=require_evidence or require_evidence_projection)
    artifacts = ArtifactRepository((AtomicMemory,))
    history_counts = (
        await _verify_history(connection, index, artifacts, batch_size, sources, require_evidence_projection)
        if require_evidence or require_evidence_projection
        else {}
    )
    profile = index.capabilities.embedding_profile
    fingerprint = None if profile is None else atomic_memory_profile_fingerprint(profile)
    columns = await connection.run_sync(
        lambda sync: {column["name"] for column in inspect(sync).get_columns(index.table.name)}
    )
    # Intermediate registered revisions predate the nullable description column.
    # Compare absent metadata as null without selecting a column not yet added.
    projection_columns = _verification_columns(index, columns, connection.dialect.name)
    errors: list[str] = []
    if history_counts.get("evidence_projection_differences"):
        errors.append(
            f"{history_counts['evidence_projection_differences']} evidence projections differ from retained history"
        )
    after = None
    active = 0
    # Two bound values per identity must fit older SQLite parameter limits.
    page_size = min(batch_size, 400) if connection.dialect.name == "sqlite" else batch_size
    while batch := await _identity_batch(connection, after, page_size):
        # The caller owns the same consistent snapshot used for authority reads.
        # Bound the projection read to this page; preserve every per-row verdict.
        projected = {
            (str(row["scope_id"]), str(row["artifact_id"])): row
            for row in (
                await connection.execute(
                    select(*projection_columns).where(
                        tuple_(index.table.c.scope_id, index.table.c.artifact_id).in_(batch)
                    )
                )
            ).mappings()
        }
        for scope_id, artifact_id in batch:
            record = await _record(connection, artifacts, scope_id, artifact_id, for_update=False)
            after = scope_id, artifact_id
            if record.state.state is not AtomicMemoryStateValue.ACTIVE:
                continue
            active += 1
            row = projected.get((scope_id, artifact_id))
            if row is None:
                errors.append(f"{scope_id}/{artifact_id}: active projection is missing")
                continue
            differing = await _projection_differences(connection, index, scope_id, record, row)
            if differing:
                errors.append(f"{scope_id}/{artifact_id}: projection differs: {', '.join(differing)}")
    obsolete = int(
        await connection.scalar(select(func.count()).select_from(index.table).where(_obsolete_current(index))) or 0
    )
    if obsolete:
        errors.append(f"{obsolete} nonactive or orphan rows remain in the current projection")
    alias_counts: dict[str, int] = {}
    if require_alias_projection:
        alias_report = await verify_atomic_memory_aliases(connection, index, batch_size=batch_size)
        errors.extend(alias_report.errors)
        alias_counts = alias_report.counts
    return AtomicMemoryProjectionRebuildReport(
        ready=not errors,
        profile_fingerprint=fingerprint,
        counts={"active_rows": active, "obsolete_rows": obsolete, **history_counts, **alias_counts},
        errors=tuple(errors),
    )


async def _initialize_rebuild(connection: AsyncConnection, index: AtomicMemoryIndex) -> None:
    await _require_authority(connection)
    await ensure_artifact_revision_metadata_schema(connection)
    await drop_obsolete_atomic_memory_projection(connection, index.table)
    try:
        await index.initialize(connection)
    except AtomicMemoryIndexError as error:
        if error.code != "vector-schema" or not isinstance(index, OceanBaseAtomicMemoryIndex):
            raise
        await index.reconfigure_vector_column(connection)


async def rebuild_atomic_memory_current(
    connection: AsyncConnection,
    index: AtomicMemoryIndex,
    *,
    batch_size: int = 100,
    sources: SourceRepository | None = None,
    guard: Callable[[], None] = lambda: None,
) -> AtomicMemoryProjectionRebuildReport:
    """Repair registered no-vector rows inside the maintenance owner's transaction.

    The caller validates the existing schema, owns backup/recovery and commits
    only after full Server validation. No initialization or external I/O occurs.
    ``sources`` is the deployment's Source registry: passing it keeps this repair's
    evidence verdict identical to the runtime read path. A maintenance bundle has no
    host registry, so it omits this and admission uses ``BUILTIN_SOURCE_REGISTRY``.
    """
    if index.capabilities.embedding_profile is not None:
        raise AtomicMemoryIndexError("embedding-profile", "Registered repair requires a no-vector profile")
    await _require_authority(connection)
    artifacts = ArtifactRepository((AtomicMemory,))
    publisher = AtomicMemoryProjectionPublisher(index)
    after = None
    while batch := await _identity_batch(connection, after, batch_size):
        guard()
        for scope_id, artifact_id in batch:
            record = await _record(connection, artifacts, scope_id, artifact_id, for_update=True)
            if record.state.state is AtomicMemoryStateValue.ACTIVE:
                await publisher.publish(connection, scope_id, record, await publisher.prepare(record.artifact.content))
            elif record.state.state is AtomicMemoryStateValue.MERGED:
                await publisher.rebuild_alias(
                    connection, scope_id, record, await publisher.prepare(record.artifact.content)
                )
            else:
                await publisher.retire(connection, scope_id, record)
            after = scope_id, artifact_id
    await connection.execute(index.table.delete().where(_obsolete_current(index)))
    await connection.execute(index.alias_table.delete().where(_obsolete_alias(index)))
    await rebuild_atomic_memory_evidence(connection, index, batch_size=batch_size, sources=sources, guard=guard)
    return await verify_atomic_memory_current(
        connection,
        index,
        batch_size=batch_size,
        sources=sources,
        require_evidence=True,
        require_evidence_projection=True,
        require_alias_projection=True,
    )


async def _publish_rebuilt_record(
    connection: AsyncConnection,
    index: AtomicMemoryIndex,
    artifacts: ArtifactRepository,
    publisher: AtomicMemoryProjectionPublisher,
    scope_id: str,
    before: AtomicMemoryRecord,
    prepared: PreparedAtomicMemoryProjection | None,
    retained: RowMapping | None,
    fingerprint: str | None,
) -> tuple[int, int]:
    await artifacts.lock_heads(connection, scope_id, (before.ref,))
    current = await _record(connection, artifacts, scope_id, before.ref.artifact_id, for_update=True)
    if current.as_read() != before.as_read() or current.artifact.content != before.artifact.content:
        raise AtomicMemoryIndexError(
            "rebuild-conflict", "Atomic Memory authority changed during projection preparation"
        )
    if publisher.profile_fingerprint != fingerprint:
        raise AtomicMemoryIndexError("embedding-profile", "Embedding profile changed during rebuild")
    if prepared is not None:
        publisher.validate_prepared(prepared)
    if current.state.state is AtomicMemoryStateValue.MERGED:
        if prepared is not None:
            await publisher.rebuild_alias(connection, scope_id, current, prepared)
        elif retained is not None:
            await publisher.rebuild_alias_from_row(connection, scope_id, current, retained)
        else:
            raise AtomicMemoryIndexError("rebuild-conflict", "Merged alias has no prepared or compatible vector")
        return 1, 0
    if prepared is not None:
        await publisher.publish(connection, scope_id, current, prepared)
        return 1, 0
    present = await connection.scalar(
        select(index.table.c.artifact_id).where(
            index.table.c.scope_id == scope_id, index.table.c.artifact_id == current.ref.artifact_id
        )
    )
    await publisher.retire(connection, scope_id, current)
    return 0, int(present is not None)


async def rebuild_atomic_memory_projection(
    database: AsyncDatabase,
    index: AtomicMemoryIndex,
    *,
    maintenance_confirmed: bool,
    embedding_model: EmbeddingModel | None = None,
    batch_size: int = 100,
    sources: SourceRepository | None = None,
) -> AtomicMemoryProjectionRebuildReport:
    """Repair current without changing revisions, Family state or retained history.

    Each row commits after its prepared content/state/profile are rechecked.
    A failed run may leave partial derived repair; repeat it while maintenance
    remains in force. No candidate, vector-history cache or progress table exists.
    """

    if not maintenance_confirmed:
        raise AtomicMemoryIndexError(
            "maintenance-required", "Projection rebuild requires stopped writers and --maintenance-confirmed"
        )
    if isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise AtomicMemoryIndexError("batch-size", "Projection rebuild batch size must be between 1 and 1000")
    profile = index.capabilities.embedding_profile
    if profile is not None and (embedding_model is None or embedding_model.profile != profile):
        raise AtomicMemoryIndexError(
            "embedding-profile", "Projection rebuild model does not match the configured profile"
        )
    started = perf_counter()
    async with database.transaction() as connection:
        await _initialize_rebuild(connection, index)
    artifacts = ArtifactRepository((AtomicMemory,))
    publisher = AtomicMemoryProjectionPublisher(
        index,
        embedding_model=embedding_model,
    )
    after = None
    rebuilt = removed = embedding_calls = 0
    while True:
        async with database.transaction() as connection:
            batch = await _identity_batch(connection, after, batch_size)
        if not batch:
            break
        for scope_id, artifact_id in batch:
            async with database.transaction() as connection:
                before = await _record(connection, artifacts, scope_id, artifact_id, for_update=True)
                retained = await _retained_projection(connection, index, scope_id, before)
            prepares_content = before.state.state in {AtomicMemoryStateValue.ACTIVE, AtomicMemoryStateValue.MERGED}
            prepared = None
            if prepares_content and retained is None:
                prepared = await publisher.prepare(before.artifact.content)
                embedding_calls += int(profile is not None)
            elif before.state.state is AtomicMemoryStateValue.ACTIVE and retained is not None:
                prepared = _prepared_retained(index, before, retained)
            async with database.transaction() as connection:
                rebuilt_count, removed_count = await _publish_rebuilt_record(
                    connection,
                    index,
                    artifacts,
                    publisher,
                    scope_id,
                    before,
                    prepared,
                    retained,
                    None if profile is None else atomic_memory_profile_fingerprint(profile),
                )
                rebuilt += rebuilt_count
                removed += removed_count
            after = scope_id, artifact_id
    async with database.transaction() as connection:
        obsolete = int(
            await connection.scalar(select(func.count()).select_from(index.table).where(_obsolete_current(index))) or 0
        )
        await connection.execute(index.table.delete().where(_obsolete_current(index)))
        removed += obsolete
        await connection.execute(index.alias_table.delete().where(_obsolete_alias(index)))
        await rebuild_atomic_memory_evidence(connection, index, batch_size=batch_size, sources=sources)
        report = await verify_atomic_memory_current(
            connection,
            index,
            batch_size=batch_size,
            sources=sources,
            require_evidence=True,
            require_evidence_projection=True,
            require_alias_projection=True,
        )
    return report.model_copy(
        update={
            "counts": {
                **report.counts,
                "rebuilt_rows": rebuilt,
                "removed_rows": removed,
                "embedding_calls": embedding_calls,
                "elapsed_ms": int((perf_counter() - started) * 1000),
            }
        }
    )


__all__ = [
    "AtomicMemoryProjectionRebuildReport",
    "rebuild_atomic_memory_current",
    "rebuild_atomic_memory_evidence",
    "rebuild_atomic_memory_projection",
    "verify_atomic_memory_aliases",
    "verify_atomic_memory_current",
    "verify_atomic_memory_evidence",
]
