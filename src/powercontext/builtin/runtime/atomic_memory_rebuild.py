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

"""Explicit offline repair of only the current Atomic Memory projection."""

from __future__ import annotations

import json
from hashlib import sha256
from time import perf_counter

from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, inspect, select, tuple_
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemory, AtomicMemoryRecord, AtomicMemoryStateValue
from powercontext.builtin.artifacts.memory.canonical import canonical_json
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndex,
    AtomicMemoryIndexError,
    AtomicMemoryProjectionPublisher,
    atomic_memory_embedding_input,
    atomic_memory_embedding_input_hash,
    atomic_memory_profile_fingerprint,
    drop_obsolete_atomic_memory_projection,
    load_atomic_memory_tags,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import ATOMIC_MEMORY_PROJECTION_FORMAT
from powercontext.builtin.persistence.atomic_memory_readiness import atomic_memory_readiness_issues
from powercontext.builtin.persistence.atomic_memory_schema import ATOMIC_MEMORY_STATES_TABLE
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE


class AtomicMemoryProjectionRebuildReport(BaseModel):
    ready: bool = False
    profile_fingerprint: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()


async def _require_authority(connection: AsyncConnection) -> None:
    tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    required = {
        "pc_artifact_heads",
        "pc_artifacts",
        "pc_atomic_memory_states",
        "pc_artifact_tags",
        "pc_access_owners",
        "pc_access_relationships",
        "pc_access_relationship_heads",
    }
    if not required <= tables:
        raise AtomicMemoryIndexError(
            "authority-schema", "Atomic Memory authority is absent; complete initialization and history migration first"
        )
    issues = await atomic_memory_readiness_issues(connection, tables)
    if issues:
        raise AtomicMemoryIndexError("migration-pending", "Atomic Memory migration is not ready: " + "; ".join(issues))
    heads, states = ARTIFACT_HEADS_TABLE, ATOMIC_MEMORY_STATES_TABLE
    relation = and_(heads.c.scope_id == states.c.scope_id, heads.c.artifact_id == states.c.artifact_id)
    missing_state = await connection.scalar(
        select(heads.c.artifact_id)
        .where(heads.c.family == AtomicMemory.family, ~exists(select(states.c.artifact_id).where(relation)))
        .limit(1)
    )
    missing_head = await connection.scalar(
        select(states.c.artifact_id)
        .where(~exists(select(heads.c.artifact_id).where(relation, heads.c.family == AtomicMemory.family)))
        .limit(1)
    )
    if missing_state is not None or missing_head is not None:
        raise AtomicMemoryIndexError("authority-orphan", "Atomic Memory has an orphan head or Family state")
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


async def _record(
    connection: AsyncConnection, artifacts: ArtifactRepository, scope_id: str, artifact_id: str
) -> AtomicMemoryRecord:
    state_repository = AtomicMemoryStateRepository()
    state = await state_repository.get(connection, scope_id, artifact_id)
    await state_repository.require_summary(connection, scope_id, artifact_id, state)
    artifact = await artifacts.latest(connection, scope_id, AtomicMemory.family, artifact_id)
    if not isinstance(artifact, AtomicMemory):
        raise AtomicMemoryIndexError("authority-content", "Atomic Memory has an unsupported authoritative content type")
    return AtomicMemoryRecord(artifact=artifact, state=state)


def _obsolete_current(index: AtomicMemoryIndex):
    current = index.table
    states, heads = ATOMIC_MEMORY_STATES_TABLE, ARTIFACT_HEADS_TABLE
    return ~exists(
        select(states.c.artifact_id)
        .join(
            heads,
            and_(
                heads.c.scope_id == states.c.scope_id,
                heads.c.artifact_id == states.c.artifact_id,
                heads.c.family == AtomicMemory.family,
            ),
        )
        .where(
            states.c.scope_id == current.c.scope_id,
            states.c.artifact_id == current.c.artifact_id,
            states.c.state == "active",
        )
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


async def verify_atomic_memory_current(
    connection: AsyncConnection, index: AtomicMemoryIndex, /, *, batch_size: int = 100
) -> AtomicMemoryProjectionRebuildReport:
    """Check every current identity against authority, including post-migration creations."""

    await _require_authority(connection)
    artifacts = ArtifactRepository((AtomicMemory,))
    profile = index.capabilities.embedding_profile
    fingerprint = None if profile is None else atomic_memory_profile_fingerprint(profile)
    errors: list[str] = []
    after = None
    active = 0
    while batch := await _identity_batch(connection, after, batch_size):
        for scope_id, artifact_id in batch:
            record = await _record(connection, artifacts, scope_id, artifact_id)
            after = scope_id, artifact_id
            if record.state.state is not AtomicMemoryStateValue.ACTIVE:
                continue
            active += 1
            row = (
                (
                    await connection.execute(
                        select(index.table).where(
                            index.table.c.scope_id == scope_id, index.table.c.artifact_id == artifact_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
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
    return AtomicMemoryProjectionRebuildReport(
        ready=not errors,
        profile_fingerprint=fingerprint,
        counts={"active_rows": active, "obsolete_rows": obsolete},
        errors=tuple(errors),
    )


async def _initialize_rebuild(connection: AsyncConnection, index: AtomicMemoryIndex) -> None:
    await _require_authority(connection)
    await drop_obsolete_atomic_memory_projection(connection, index.table)
    try:
        await index.initialize(connection)
    except AtomicMemoryIndexError as error:
        if error.code != "vector-schema" or not isinstance(index, OceanBaseAtomicMemoryIndex):
            raise
        await index.reconfigure_vector_column(connection)


async def rebuild_atomic_memory_projection(
    database: AsyncDatabase,
    index: AtomicMemoryIndex,
    *,
    maintenance_confirmed: bool,
    embedding_model: EmbeddingModel | None = None,
    batch_size: int = 100,
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
    rebuilt = removed = 0
    while True:
        async with database.transaction() as connection:
            batch = await _identity_batch(connection, after, batch_size)
        if not batch:
            break
        for scope_id, artifact_id in batch:
            async with database.transaction() as connection:
                before = await _record(connection, artifacts, scope_id, artifact_id)
            prepared = (
                await publisher.prepare(before.artifact.content)
                if before.state.state is AtomicMemoryStateValue.ACTIVE
                else None
            )
            async with database.transaction() as connection:
                await artifacts.lock_heads(connection, scope_id, (before.ref,))
                current = await _record(connection, artifacts, scope_id, artifact_id)
                if current.as_read() != before.as_read():
                    raise AtomicMemoryIndexError(
                        "rebuild-conflict", "Atomic Memory authority changed during projection preparation"
                    )
                if prepared is not None:
                    publisher.validate_prepared(prepared)
                    await publisher.publish(connection, scope_id, current, prepared)
                    rebuilt += 1
                else:
                    present = await connection.scalar(
                        select(index.table.c.artifact_id).where(
                            index.table.c.scope_id == scope_id, index.table.c.artifact_id == artifact_id
                        )
                    )
                    await publisher.remove(connection, scope_id, artifact_id)
                    removed += present is not None
            after = scope_id, artifact_id
    async with database.transaction() as connection:
        obsolete = int(
            await connection.scalar(select(func.count()).select_from(index.table).where(_obsolete_current(index))) or 0
        )
        await connection.execute(index.table.delete().where(_obsolete_current(index)))
        removed += obsolete
        report = await verify_atomic_memory_current(connection, index, batch_size=batch_size)
    return report.model_copy(
        update={
            "counts": {
                **report.counts,
                "rebuilt_rows": rebuilt,
                "removed_rows": removed,
                "embedding_calls": rebuilt if index.capabilities.vector else 0,
                "elapsed_ms": int((perf_counter() - started) * 1000),
            }
        }
    )


__all__ = ["AtomicMemoryProjectionRebuildReport", "rebuild_atomic_memory_projection", "verify_atomic_memory_current"]
