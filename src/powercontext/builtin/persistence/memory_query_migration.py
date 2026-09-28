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

"""Explicit, resumable migration for bounded Memory directory queries.

Normal startup creates the projection tables and marks only a data-empty
database ready.  Existing Memory data stays available through the legacy APIs,
but the bounded directory query remains gated until an operator applies and
verifies this migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import Memory, MemoryContent
from powercontext.builtin.persistence.codec import load_model, stored_bytes
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_DIRECTORY_TABLE,
    MEMORY_QUERY_INDEX_SCHEMA_TABLE,
    MEMORY_TAG_GENERATIONS_TABLE,
)

MEMORY_QUERY_INDEX_SCHEMA_VERSION = 1656
_MAX_VERIFICATION_ISSUES = 32
_VERIFICATION_PAGE_SIZE = 100


class MemoryQueryIndexUnavailableError(PersistenceError):
    """The bounded query projection has not completed verified migration."""

    def __init__(self, code: str = "unavailable") -> None:
        messages = {
            "unavailable": "Memory query index is unavailable; complete offline plan/apply/verify first",
            "migration-id": "Migration ID must contain 1 to 64 characters",
            "batch-size": "Migration batch size must be a positive integer",
            "marker": "Memory query migration marker is unavailable",
            "version": "Unsupported Memory query index schema version",
            "resume": "Resume the unfinished migration with its original ID",
            "dialect": "Unsupported Memory query migration database dialect",
            "conflict": "Existing directory row conflicts with authoritative Memory",
        }
        self.reason = messages[code]
        super().__init__(self.reason)


class MemoryQueryMigrationPlan(BaseModel):
    schema_version: int = MEMORY_QUERY_INDEX_SCHEMA_VERSION
    phase: str
    required: bool
    memory_revision_count: int
    directory_row_count: int


class MemoryQueryMigrationProgress(BaseModel):
    phase: str
    processed: int = 0
    complete: bool = False


class MemoryQueryMigrationVerification(BaseModel):
    ready: bool
    checked_revisions: int = 0
    issues: tuple[str, ...] = ()


async def bootstrap_memory_query_index(connection: AsyncConnection) -> None:
    """Mark a data-empty schema ready while leaving legacy Memory data gated."""

    marker = await _marker(connection)
    if marker is not None:
        return
    await _seed_marker(connection)
    marker = await _marker(connection, for_update=True)
    if marker is None or marker["phase"] == "complete":
        return
    memory_count = await connection.scalar(
        select(func.count()).select_from(ARTIFACT_HEADS_TABLE).where(ARTIFACT_HEADS_TABLE.c.family == Memory.family)
    )
    if int(memory_count or 0) == 0:
        await connection.execute(
            update(MEMORY_QUERY_INDEX_SCHEMA_TABLE)
            .where(MEMORY_QUERY_INDEX_SCHEMA_TABLE.c.singleton == 1)
            .values(phase="complete", migration_id="fresh")
        )


async def require_memory_query_index(connection: AsyncConnection) -> None:
    """Fail closed unless the exact query-index schema completed verification."""

    marker = await _marker(connection)
    if (
        marker is None
        or int(marker["schema_version"]) != MEMORY_QUERY_INDEX_SCHEMA_VERSION
        or marker["phase"] != "complete"
    ):
        raise MemoryQueryIndexUnavailableError


async def plan_memory_query_migration(connection: AsyncConnection) -> MemoryQueryMigrationPlan:
    marker = await _marker(connection)
    phase = "uninitialized" if marker is None else str(marker["phase"])
    revisions = await connection.scalar(
        select(func.count()).select_from(ARTIFACTS_TABLE).where(ARTIFACTS_TABLE.c.family == Memory.family)
    )
    directory = await connection.scalar(select(func.count()).select_from(MEMORY_ENTRY_DIRECTORY_TABLE))
    return MemoryQueryMigrationPlan(
        phase=phase,
        required=marker is None
        or int(marker["schema_version"]) != MEMORY_QUERY_INDEX_SCHEMA_VERSION
        or marker["phase"] != "complete",
        memory_revision_count=int(revisions or 0),
        directory_row_count=int(directory or 0),
    )


async def apply_memory_query_migration(  # noqa: C901 - explicit durable maintenance phases
    connection: AsyncConnection,
    *,
    migration_id: str = "rfc1656",
    batch_size: int = 100,
) -> MemoryQueryMigrationProgress:
    """Apply one bounded revision batch; the caller owns the transaction."""

    if not migration_id or len(migration_id) > 64:
        raise MemoryQueryIndexUnavailableError("migration-id")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise MemoryQueryIndexUnavailableError("batch-size")

    marker = await _marker(connection, for_update=True)
    if marker is None:
        await bootstrap_memory_query_index(connection)
        marker = await _marker(connection, for_update=True)
    if marker is None:
        raise MemoryQueryIndexUnavailableError("marker")
    if int(marker["schema_version"]) != MEMORY_QUERY_INDEX_SCHEMA_VERSION:
        raise MemoryQueryIndexUnavailableError("version")
    if marker["phase"] == "complete":
        return MemoryQueryMigrationProgress(phase="complete", complete=True)
    if marker["phase"] == "bootstrap":
        await connection.execute(
            update(MEMORY_QUERY_INDEX_SCHEMA_TABLE)
            .where(MEMORY_QUERY_INDEX_SCHEMA_TABLE.c.singleton == 1)
            .values(phase="backfill", migration_id=migration_id)
        )
        marker = {**marker, "phase": "backfill", "migration_id": migration_id}
    elif marker["migration_id"] != migration_id:
        raise MemoryQueryIndexUnavailableError("resume")

    rows = (
        (
            await connection.execute(
                _remaining_revisions(marker)
                .order_by(
                    ARTIFACTS_TABLE.c.scope_id,
                    ARTIFACTS_TABLE.c.artifact_id,
                    ARTIFACTS_TABLE.c.revision,
                )
                .limit(batch_size)
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        await connection.execute(
            update(MEMORY_QUERY_INDEX_SCHEMA_TABLE)
            .where(MEMORY_QUERY_INDEX_SCHEMA_TABLE.c.singleton == 1)
            .values(phase="verify")
        )
        return MemoryQueryMigrationProgress(phase="verify")

    for row in rows:
        await _backfill_revision(connection, row)
    last = rows[-1]
    await connection.execute(
        update(MEMORY_QUERY_INDEX_SCHEMA_TABLE)
        .where(MEMORY_QUERY_INDEX_SCHEMA_TABLE.c.singleton == 1)
        .values(
            checkpoint_scope_id=last["scope_id"],
            checkpoint_artifact_id=last["artifact_id"],
            checkpoint_revision=last["revision"],
        )
    )
    return MemoryQueryMigrationProgress(phase="backfill", processed=len(rows))


async def verify_memory_query_migration(  # noqa: C901 - independent persisted invariants
    connection: AsyncConnection,
) -> MemoryQueryMigrationVerification:
    """Verify exact revision views and mark the feature ready only on success."""

    marker = await _marker(connection, for_update=True)
    if marker is None:
        return MemoryQueryMigrationVerification(ready=False, issues=("missing migration marker",))
    issues: list[str] = []
    if int(marker["schema_version"]) != MEMORY_QUERY_INDEX_SCHEMA_VERSION:
        issues.append("unsupported schema version")
    if marker["phase"] not in {"verify", "complete"}:
        issues.append("migration backfill is incomplete")
    checked = 0
    if not issues:
        after: tuple[str, str, int] | None = None
        while len(issues) < _MAX_VERIFICATION_ISSUES:
            rows = (
                (
                    await connection.execute(
                        _revisions_after(after)
                        .order_by(
                            ARTIFACTS_TABLE.c.scope_id,
                            ARTIFACTS_TABLE.c.artifact_id,
                            ARTIFACTS_TABLE.c.revision,
                        )
                        .limit(_VERIFICATION_PAGE_SIZE)
                    )
                )
                .mappings()
                .all()
            )
            if not rows:
                break
            for row in rows:
                checked += 1
                content = _memory_content(row)
                actual = await _directory_at_revision(
                    connection,
                    str(row["scope_id"]),
                    str(row["artifact_id"]),
                    int(row["revision"]),
                )
                expected = {(item.entry_id, item.entry_version_id, item.state) for item in content.manifest.entries}
                if actual != expected:
                    issues.append(f"directory mismatch at {row['scope_id']}/{row['artifact_id']}@{row['revision']}")
                    if len(issues) >= _MAX_VERIFICATION_ISSUES:
                        break
            last = rows[-1]
            after = (str(last["scope_id"]), str(last["artifact_id"]), int(last["revision"]))
    missing_generations = await connection.scalar(
        select(func.count())
        .select_from(ARTIFACT_HEADS_TABLE)
        .where(
            ARTIFACT_HEADS_TABLE.c.family == Memory.family,
            ~select(MEMORY_TAG_GENERATIONS_TABLE.c.memory_artifact_id)
            .where(
                MEMORY_TAG_GENERATIONS_TABLE.c.scope_id == ARTIFACT_HEADS_TABLE.c.scope_id,
                MEMORY_TAG_GENERATIONS_TABLE.c.memory_artifact_id == ARTIFACT_HEADS_TABLE.c.artifact_id,
            )
            .exists(),
        )
    )
    if missing_generations:
        issues.append("Memory heads remain without tag generations")
    if issues:
        return MemoryQueryMigrationVerification(ready=False, checked_revisions=checked, issues=tuple(issues))

    await connection.execute(
        update(MEMORY_QUERY_INDEX_SCHEMA_TABLE)
        .where(MEMORY_QUERY_INDEX_SCHEMA_TABLE.c.singleton == 1)
        .values(phase="complete")
    )
    return MemoryQueryMigrationVerification(ready=True, checked_revisions=checked)


async def _seed_marker(connection: AsyncConnection) -> None:
    values = {
        "singleton": 1,
        "schema_version": MEMORY_QUERY_INDEX_SCHEMA_VERSION,
        "phase": "bootstrap",
        "migration_id": "pending",
        "checkpoint_scope_id": None,
        "checkpoint_artifact_id": None,
        "checkpoint_revision": None,
    }
    if connection.dialect.name == "sqlite":
        statement = sqlite_insert(MEMORY_QUERY_INDEX_SCHEMA_TABLE).values(**values).on_conflict_do_nothing()
    elif connection.dialect.name == "mysql":
        statement = (
            mysql_insert(MEMORY_QUERY_INDEX_SCHEMA_TABLE)
            .values(**values)
            .on_duplicate_key_update(schema_version=MEMORY_QUERY_INDEX_SCHEMA_TABLE.c.schema_version)
        )
    else:
        raise MemoryQueryIndexUnavailableError("dialect")
    await connection.execute(statement)


def _remaining_revisions(marker: Mapping[str, Any]):
    checkpoint = None
    if (
        marker["checkpoint_scope_id"] is not None
        and marker["checkpoint_artifact_id"] is not None
        and marker["checkpoint_revision"] is not None
    ):
        checkpoint = (
            str(marker["checkpoint_scope_id"]),
            str(marker["checkpoint_artifact_id"]),
            int(marker["checkpoint_revision"]),
        )
    return _revisions_after(checkpoint)


def _revisions_after(checkpoint: tuple[str, str, int] | None):
    statement = select(
        ARTIFACTS_TABLE.c.scope_id,
        ARTIFACTS_TABLE.c.artifact_id,
        ARTIFACTS_TABLE.c.revision,
        ARTIFACTS_TABLE.c.content,
    ).where(ARTIFACTS_TABLE.c.family == Memory.family)
    if checkpoint is None:
        return statement
    scope_id, artifact_id, revision = checkpoint
    return statement.where(
        or_(
            ARTIFACTS_TABLE.c.scope_id > scope_id,
            and_(
                ARTIFACTS_TABLE.c.scope_id == scope_id,
                ARTIFACTS_TABLE.c.artifact_id > artifact_id,
            ),
            and_(
                ARTIFACTS_TABLE.c.scope_id == scope_id,
                ARTIFACTS_TABLE.c.artifact_id == artifact_id,
                ARTIFACTS_TABLE.c.revision > revision,
            ),
        )
    )


async def _backfill_revision(connection: AsyncConnection, row: Mapping[Any, Any]) -> None:
    scope_id = str(row["scope_id"])
    artifact_id = str(row["artifact_id"])
    revision = int(row["revision"])
    content = _memory_content(row)
    manifest = {item.entry_id: item for item in content.manifest.entries}
    previous = (
        {}
        if revision == 1
        else {
            entry_id: (entry_version_id, state)
            for entry_id, entry_version_id, state in await _directory_at_revision(
                connection,
                scope_id,
                artifact_id,
                revision - 1,
            )
        }
    )
    current = {entry_id: (item.entry_version_id, item.state) for entry_id, item in manifest.items()}
    changed_ids = {
        entry_id for entry_id in previous.keys() | current.keys() if previous.get(entry_id) != current.get(entry_id)
    }

    for entry_id in sorted(changed_ids):
        await connection.execute(
            update(MEMORY_ENTRY_DIRECTORY_TABLE)
            .where(
                MEMORY_ENTRY_DIRECTORY_TABLE.c.scope_id == scope_id,
                MEMORY_ENTRY_DIRECTORY_TABLE.c.memory_artifact_id == artifact_id,
                MEMORY_ENTRY_DIRECTORY_TABLE.c.entry_id == entry_id,
                MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_from_revision < revision,
                MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_to_revision.is_(None),
            )
            .values(valid_to_revision=revision)
        )
        item = manifest.get(entry_id)
        if item is None:
            continue
        existing = (
            (
                await connection.execute(
                    select(MEMORY_ENTRY_DIRECTORY_TABLE).where(
                        MEMORY_ENTRY_DIRECTORY_TABLE.c.scope_id == scope_id,
                        MEMORY_ENTRY_DIRECTORY_TABLE.c.memory_artifact_id == artifact_id,
                        MEMORY_ENTRY_DIRECTORY_TABLE.c.entry_id == entry_id,
                        MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_from_revision == revision,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        expected = {
            "entry_version_id": item.entry_version_id,
            "state": item.state,
        }
        if existing is None:
            await connection.execute(
                insert(MEMORY_ENTRY_DIRECTORY_TABLE).values(
                    scope_id=scope_id,
                    family=Memory.family,
                    memory_artifact_id=artifact_id,
                    entry_id=item.entry_id,
                    entry_version_id=item.entry_version_id,
                    state=item.state,
                    valid_from_revision=revision,
                    valid_to_revision=None,
                )
            )
        elif any(existing[key] != value for key, value in expected.items()):
            raise MemoryQueryIndexUnavailableError("conflict")

    generation = await connection.scalar(
        select(MEMORY_TAG_GENERATIONS_TABLE.c.generation).where(
            MEMORY_TAG_GENERATIONS_TABLE.c.scope_id == scope_id,
            MEMORY_TAG_GENERATIONS_TABLE.c.memory_artifact_id == artifact_id,
        )
    )
    if generation is None:
        await connection.execute(
            insert(MEMORY_TAG_GENERATIONS_TABLE).values(
                scope_id=scope_id,
                family=Memory.family,
                memory_artifact_id=artifact_id,
                generation=0,
            )
        )


def _memory_content(row: Mapping[Any, Any]) -> MemoryContent:
    return load_model(
        MemoryContent,
        stored_bytes(row["content"], column="payload"),
        kind="artifact",
        name=Memory.family,
    )


async def _directory_at_revision(
    connection: AsyncConnection,
    scope_id: str,
    artifact_id: str,
    revision: int,
) -> set[tuple[str, str, str]]:
    rows = await connection.execute(
        select(
            MEMORY_ENTRY_DIRECTORY_TABLE.c.entry_id,
            MEMORY_ENTRY_DIRECTORY_TABLE.c.entry_version_id,
            MEMORY_ENTRY_DIRECTORY_TABLE.c.state,
        ).where(
            MEMORY_ENTRY_DIRECTORY_TABLE.c.scope_id == scope_id,
            MEMORY_ENTRY_DIRECTORY_TABLE.c.memory_artifact_id == artifact_id,
            MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_from_revision <= revision,
            or_(
                MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_to_revision.is_(None),
                MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_to_revision > revision,
            ),
        )
    )
    return {(str(entry_id), str(entry_version_id), str(state)) for entry_id, entry_version_id, state in rows}


async def _marker(connection: AsyncConnection, *, for_update: bool = False) -> Mapping[str, Any] | None:
    statement = select(MEMORY_QUERY_INDEX_SCHEMA_TABLE).where(MEMORY_QUERY_INDEX_SCHEMA_TABLE.c.singleton == 1)
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    return None if row is None else dict(row)


__all__ = [
    "MEMORY_QUERY_INDEX_SCHEMA_VERSION",
    "MemoryQueryIndexUnavailableError",
    "MemoryQueryMigrationPlan",
    "MemoryQueryMigrationProgress",
    "MemoryQueryMigrationVerification",
    "apply_memory_query_migration",
    "bootstrap_memory_query_index",
    "plan_memory_query_migration",
    "require_memory_query_index",
    "verify_memory_query_migration",
]
