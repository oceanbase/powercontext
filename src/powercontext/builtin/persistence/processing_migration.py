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

"""Explicit, resumable maintenance migration of processing scheduling state.

Stop all old hosts and Workers and pause input/API writes before applying.
Each apply call performs one bounded step and joins a caller-owned transaction.
Commit that transaction before invoking the next step. MySQL DDL may commit
independently; every DDL step therefore checks its actual postcondition again.
Ordinary runtime startup only bootstraps a provably empty database or validates
the completed marker. It never imports old work or discards old wave targets.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import Boolean, and_, case, delete, exists, func, insert, inspect, literal, or_, select, update
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.supervision import database_utc_now
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE as WAVES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE as STATES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE as INTENTS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_LEASES_TABLE as LEASES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_MIGRATION_RECEIPTS_TABLE as RECEIPTS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_PENDING_TABLE as PENDING,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_SCHEMA_TABLE as SCHEMA,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_SEQUENCES_TABLE as SEQUENCES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACTS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.persistence.tables import (
    SOURCE_CURSORS_TABLE as CURSORS,
)
from powercontext.builtin.persistence.tables import (
    SOURCE_JOURNAL_HEADS_TABLE as HEADS,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE as TARGETS,
)

PROCESSING_SCHEMA_VERSION = 1515
_TOPIC_BINDING = "topic-memory-source-window"
_DEFAULT_BINDINGS = {
    "memory-source-window": "memory",
    _TOPIC_BINDING: "topic-memory",
    "experience-incubation": "experience",
    "profile-source-window": "profile",
}
_STATE_COLUMNS = {
    "last_schedule_checkpoint_at": "DATETIME NULL",
    "scan_generation": "BIGINT NOT NULL DEFAULT 0",
    "scan_in_progress": "BOOLEAN NOT NULL DEFAULT 0",
    "scan_upper_pending_sequence": "BIGINT NULL",
}
_NEW_TABLES = (SCHEMA, RECEIPTS, SEQUENCES, INTENTS, TARGETS)


class ProcessingSchemaNotReadyError(PersistenceError):
    """Processing state needs explicit offline migration or a matching mode."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ProcessingMigrationPlan(BaseModel):
    schema_version: int = PROCESSING_SCHEMA_VERSION
    phase: str
    required: bool
    missing_state_columns: tuple[str, ...]
    legacy_pending_count: int
    legacy_wave_count: int
    conservative_automatic_bindings: tuple[str, ...]


class ProcessingMigrationProgress(BaseModel):
    phase: str
    processed: int = 0
    complete: bool = False


class ProcessingMigrationVerification(BaseModel):
    ready: bool
    issues: tuple[str, ...] = ()


async def plan_processing_migration(
    connection: AsyncConnection,
    *,
    config_manifest: Mapping[str, Any] | str | None = None,
) -> ProcessingMigrationPlan:
    """Inspect migration needs without writing or exposing per-Scope data."""

    columns = await _columns(connection, STATES.name)
    marker = await _marker(connection)
    manifest = _manifest(config_manifest)
    return ProcessingMigrationPlan(
        phase="uninitialized" if marker is None else str(marker["phase"]),
        required=marker is None
        or marker["phase"] != "complete"
        or (
            config_manifest is not None
            and _deployment_manifest(str(marker["config_manifest"])) != _deployment_manifest(manifest)
        ),
        missing_state_columns=tuple(name for name in _STATE_COLUMNS if name not in columns),
        legacy_pending_count=await _count_if_present(connection, PENDING),
        legacy_wave_count=await _count_if_present(connection, WAVES),
        conservative_automatic_bindings=_automatic_bindings(manifest),
    )


async def bootstrap_processing_schema(
    connection: AsyncConnection,
    config_manifest: Mapping[str, Any] | str | None = None,
) -> None:
    """Mark an empty, current-schema database ready; never upgrade legacy work.

    Call after normal create_all, before any API, domain initializer or Worker
    can write. Existing complete databases are only checked. The absence of a
    marker alone is never proof that a database is new.
    """

    marker = await _marker(connection)
    if marker is not None:
        await assert_processing_schema_ready(connection, config_manifest)
        return
    columns = await _columns(connection, STATES.name)
    if not set(_STATE_COLUMNS) <= columns.keys():
        raise ProcessingSchemaNotReadyError(reason="Legacy processing schema requires offline processing migration")
    for table in _NEW_TABLES:
        if not await _has_table(connection, table.name):
            raise ProcessingSchemaNotReadyError(
                reason="Processing tables must be created before fresh database bootstrap"
            )
    await _seed_fresh_marker(connection, _manifest(config_manifest))
    marker = await _marker(connection, for_update=True)
    if marker is not None and marker["phase"] == "complete":
        await assert_processing_schema_ready(connection, config_manifest)
        return
    for table in (HEADS, SOURCES_TABLE, ARTIFACTS_TABLE, CURSORS, PENDING, WAVES, LEASES, STATES, INTENTS):
        if await _count_if_present(connection, table):
            raise ProcessingSchemaNotReadyError(reason="Existing processing data requires offline processing migration")
    problems = await _schema_issues(connection)
    if problems:
        raise ProcessingSchemaNotReadyError(reason="; ".join(problems))
    await _phase(connection, "complete")


async def _seed_fresh_marker(connection: AsyncConnection, manifest: str) -> None:
    values = {
        "singleton": 1,
        "schema_version": PROCESSING_SCHEMA_VERSION,
        "source_version": PROCESSING_SCHEMA_VERSION,
        "phase": "bootstrap",
        "migration_id": "fresh",
        "config_manifest": manifest,
    }
    if connection.dialect.name == "sqlite":
        statement = sqlite_insert(SCHEMA).values(**values).on_conflict_do_nothing()
    elif connection.dialect.name == "mysql":
        statement = (
            mysql_insert(SCHEMA).values(**values).on_duplicate_key_update(schema_version=SCHEMA.c.schema_version)
        )
    else:
        raise ProcessingSchemaNotReadyError(reason="Unsupported processing database dialect")
    # An upsert serializes fresh OceanBase candidates without a read/insert
    # race. Its marker is rolled back with this transaction on legacy detection.
    await connection.execute(statement)


async def assert_processing_schema_ready(
    connection: AsyncConnection,
    config_manifest: Mapping[str, Any] | str | None = None,
) -> None:
    """Reject incomplete migration and mixed deployment modes before dispatch."""

    marker = await _marker(connection)
    if marker is None or marker["schema_version"] != PROCESSING_SCHEMA_VERSION or marker["phase"] != "complete":
        raise ProcessingSchemaNotReadyError(
            reason="Processing schema is not ready; complete offline plan/apply/verify first"
        )
    if config_manifest is not None and _deployment_manifest(str(marker["config_manifest"])) != _deployment_manifest(
        _manifest(config_manifest)
    ):
        raise ProcessingSchemaNotReadyError(
            reason="Processing configuration differs from the completed maintenance manifest"
        )
    if not set(_STATE_COLUMNS) <= (await _columns(connection, STATES.name)).keys():
        raise ProcessingSchemaNotReadyError(reason="Processing scan schema is incomplete")
    problems = await _schema_issues(connection)
    if problems:
        raise ProcessingSchemaNotReadyError(reason="; ".join(problems))


async def apply_processing_migration(  # noqa: C901 - explicit durable maintenance phases
    connection: AsyncConnection,
    *,
    config_manifest: Mapping[str, Any] | str | None = None,
    migration_id: str = "rfc1515",
    batch_size: int = 100,
) -> ProcessingMigrationProgress:
    """Apply one resumable step; caller commits this transaction before retry.

    A different manifest on an already migrated database is an offline mode
    switch. It requires a new migration ID and invalidates every old Lease, but
    never remaps domain inputs or increments accepted request counters again.
    """

    if not migration_id or len(migration_id) > 64:
        raise ProcessingSchemaNotReadyError(reason="Migration ID must contain 1 to 64 characters")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ProcessingSchemaNotReadyError(reason="Migration batch size must be a positive integer")
    manifest = _manifest(config_manifest)
    marker = await _marker(connection)
    if marker is None:
        await connection.run_sync(lambda sync: SCHEMA.create(sync, checkfirst=True))
        await connection.execute(
            insert(SCHEMA).values(
                singleton=1,
                schema_version=PROCESSING_SCHEMA_VERSION,
                source_version=1490,
                phase="schema",
                migration_id=migration_id,
                config_manifest=manifest,
            )
        )
        return ProcessingMigrationProgress(phase="schema")
    if marker["phase"] == "complete":
        if _deployment_manifest(str(marker["config_manifest"])) == _deployment_manifest(manifest):
            return ProcessingMigrationProgress(phase="complete", complete=True)
        if marker["migration_id"] == migration_id:
            raise ProcessingSchemaNotReadyError(reason="A mode switch requires a new migration ID")
        await connection.execute(
            update(SCHEMA)
            .where(SCHEMA.c.singleton == 1)
            .values(
                source_version=PROCESSING_SCHEMA_VERSION,
                phase="invalidate",
                migration_id=migration_id,
                config_manifest=manifest,
            )
        )
        return ProcessingMigrationProgress(phase="invalidate")
    if marker["migration_id"] != migration_id or marker["config_manifest"] != manifest:
        raise ProcessingSchemaNotReadyError(reason="Resume the unfinished migration with its original ID and manifest")
    phase = str(marker["phase"])
    if phase == "schema":
        for table in (*_NEW_TABLES, STATES):
            if not await _has_table(connection, table.name):
                await connection.run_sync(lambda sync, owned=table: owned.create(sync, checkfirst=True))
                return ProcessingMigrationProgress(phase="schema")
        columns = await _columns(connection, STATES.name)
        for name, definition in _STATE_COLUMNS.items():
            if name not in columns:
                # Names/definitions are fixed constants, never user SQL.
                await connection.exec_driver_sql(f"ALTER TABLE {STATES.name} ADD COLUMN {name} {definition}")
                return ProcessingMigrationProgress(phase="schema")
        problems = await _schema_issues(connection)
        if problems:
            raise ProcessingSchemaNotReadyError(reason="; ".join(problems))
        await _phase(connection, "invalidate")
        return ProcessingMigrationProgress(phase="invalidate")
    if phase == "invalidate":
        holder = "migration-" + hashlib.sha256(migration_id.encode()).hexdigest()[:24]
        expired_at = await database_utc_now(connection) - timedelta(seconds=1)
        await connection.execute(
            update(LEASES).values(
                holder_id=holder, supervisor_generation=LEASES.c.supervisor_generation + 1, lease_expires_at=expired_at
            )
        )
        next_phase = "backfill" if marker["source_version"] < PROCESSING_SCHEMA_VERSION else "verify"
        await _phase(connection, next_phase)
        return ProcessingMigrationProgress(phase=next_phase)
    if phase == "backfill":
        keys = await _unmapped_keys(connection, migration_id, manifest, batch_size)
        for binding_name, scope_id in keys:
            await _migrate_scope(connection, migration_id, manifest, binding_name, scope_id)
        if not keys:
            await _phase(connection, "checkpoint")
            return ProcessingMigrationProgress(phase="checkpoint")
        return ProcessingMigrationProgress(phase="backfill", processed=len(keys))
    if phase == "checkpoint":
        await connection.execute(
            update(STATES)
            .where(STATES.c.last_schedule_checkpoint_at.is_(None))
            .values(last_schedule_checkpoint_at=STATES.c.last_auto_wave_completed_at)
        )
        await _phase(connection, "verify")
        return ProcessingMigrationProgress(phase="verify")
    if phase == "verify":
        report = await verify_processing_migration(connection, config_manifest=config_manifest)
        if report.issues:
            raise ProcessingSchemaNotReadyError(
                reason="Processing migration verification failed: " + "; ".join(report.issues)
            )
        await _phase(connection, "cleanup")
        return ProcessingMigrationProgress(phase="cleanup")
    if phase == "cleanup":
        # Only scheduling snapshots are obsolete. Topic Pending, Cursor, budget
        # and evidence remain domain-owned. Keep the empty legacy table so
        # create_all cannot accidentally change upgrade detection.
        await connection.execute(delete(WAVES))
        await _phase(connection, "complete")
        return ProcessingMigrationProgress(phase="complete", complete=True)
    raise ProcessingSchemaNotReadyError(reason="Unknown processing migration phase")


async def verify_processing_migration(  # noqa: C901 - independent persisted invariants
    connection: AsyncConnection,
    *,
    config_manifest: Mapping[str, Any] | str | None = None,
) -> ProcessingMigrationVerification:
    """Check durable postconditions using bounded pages, including on resume."""

    marker = await _marker(connection)
    if marker is None:
        return ProcessingMigrationVerification(ready=False, issues=("missing migration marker",))
    issues: list[str] = []
    if marker["schema_version"] != PROCESSING_SCHEMA_VERSION:
        issues.append("unsupported schema version")
    if config_manifest is not None and _deployment_manifest(str(marker["config_manifest"])) != _deployment_manifest(
        _manifest(config_manifest)
    ):
        issues.append("configuration manifest mismatch")
    if not set(_STATE_COLUMNS) <= (await _columns(connection, STATES.name)).keys():
        issues.append("incomplete scan state columns")
    for table in _NEW_TABLES:
        if not await _has_table(connection, table.name):
            issues.append("missing " + table.name)
    if not issues:
        issues.extend(await _schema_issues(connection))
    if issues:
        return ProcessingMigrationVerification(ready=False, issues=tuple(issues))
    if marker["phase"] not in {"verify", "cleanup", "complete"}:
        return ProcessingMigrationVerification(ready=False, issues=("migration backfill is not complete",))
    migration_id = str(marker["migration_id"])
    manifest = str(marker["config_manifest"])
    if marker["source_version"] < PROCESSING_SCHEMA_VERSION and marker["phase"] != "complete":
        if await _unmapped_keys(connection, migration_id, manifest, 1):
            issues.append("legacy inputs remain without migration receipts")
        after: tuple[str, str] | None = None
        while True:
            statement = select(RECEIPTS).where(RECEIPTS.c.migration_id == migration_id)
            if after is not None:
                statement = statement.where(
                    or_(
                        RECEIPTS.c.binding_name > after[0],
                        and_(RECEIPTS.c.binding_name == after[0], RECEIPTS.c.scope_id > after[1]),
                    )
                )
            rows = (
                (await connection.execute(statement.order_by(RECEIPTS.c.binding_name, RECEIPTS.c.scope_id).limit(100)))
                .mappings()
                .all()
            )
            if not rows:
                break
            for receipt in rows:
                binding, scope = str(receipt["binding_name"]), str(receipt["scope_id"])
                intent = await ArtifactProcessingIntentRepository().load(connection, scope, binding)
                if (
                    intent is None
                    or intent.requested_generation < receipt["requested_generation"]
                    or intent.dirty_generation < receipt["dirty_generation"]
                ):
                    issues.append("receipt has no matching invocation and dirty state")
                    break
                snapshot = await _snapshot(connection, binding, scope)
                if _snapshot_bytes(snapshot) != bytes(receipt["source_snapshot"]):
                    issues.append("legacy inputs or Cursor changed during maintenance")
                    break
                if receipt["source_through"] > 0 and receipt["requested_generation"] > 0 and binding == _TOPIC_BINDING:
                    target = (
                        (
                            await connection.execute(
                                select(TARGETS).where(TARGETS.c.binding_name == binding, TARGETS.c.scope_id == scope)
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        target is None
                        or target["source_through"] < receipt["source_through"]
                        or target["target_request_generation"] != receipt["requested_generation"]
                    ):
                        issues.append("accepted Topic target is missing or changed")
                        break
            if issues:
                break
            after = str(rows[-1]["binding_name"]), str(rows[-1]["scope_id"])
    invalid = await connection.scalar(
        select(func.count())
        .select_from(INTENTS)
        .where(
            or_(
                INTENTS.c.clean_generation < 0,
                INTENTS.c.clean_generation > INTENTS.c.dirty_generation,
                INTENTS.c.handled_generation < 0,
                INTENTS.c.handled_generation > INTENTS.c.requested_generation,
                INTENTS.c.pending_sequence <= 0,
            )
        )
    )
    if invalid:
        issues.append("intent counter invariant violated")
    maximum = int(await connection.scalar(select(func.max(INTENTS.c.pending_sequence))) or 0)
    sequence = int(await connection.scalar(select(SEQUENCES.c.sequence).where(SEQUENCES.c.singleton == 1)) or 0)
    if sequence < maximum:
        issues.append("sequence allocator is behind registered intents")
    return ProcessingMigrationVerification(ready=not issues and marker["phase"] == "complete", issues=tuple(issues))


async def _migrate_scope(
    connection: AsyncConnection, migration_id: str, manifest: str, binding_name: str, scope_id: str
) -> None:
    if binding_name not in _bindings(manifest):
        raise ProcessingSchemaNotReadyError(reason="Legacy binding has no explicit Family mapping: " + binding_name)
    snapshot = await _snapshot(connection, binding_name, scope_id)
    cursor = int(snapshot["cursor"])
    if int(snapshot["completed_through"]) > cursor:
        raise ProcessingSchemaNotReadyError(reason="Completed legacy target is not covered by its Cursor")
    pending_through = int(snapshot["pending_through"])
    dirty = max(int(snapshot["head"]), pending_through) > cursor
    unhandled = int(snapshot["flush"]) > int(snapshot["handled_flush"])
    conservative = (
        binding_name in _automatic_bindings(manifest) and pending_through > cursor and int(snapshot["wave_count"]) == 0
    )
    accepted = unhandled or conservative or int(snapshot["unfinished_count"]) > 0
    through = max(pending_through if unhandled or conservative else 0, int(snapshot["unfinished_through"]))
    if through > int(snapshot["head"]):
        raise ProcessingSchemaNotReadyError(reason="Legacy processing target exceeds its Source head")
    intents = ArtifactProcessingIntentRepository()
    intent = await intents.ensure(connection, scope_id, binding_name)
    if dirty and intent.dirty_generation == intent.clean_generation:
        intent = await intents.mark_dirty(connection, scope_id, binding_name)
    if accepted and intent.requested_generation == intent.handled_generation:
        intent = await intents.request(connection, scope_id, binding_name)
    if binding_name == _TOPIC_BINDING:
        if dirty and int(snapshot["head"]) > pending_through:
            await ArtifactProcessingPendingRepository().raise_source(
                connection, scope_id, binding_name, int(snapshot["head"])
            )
        if accepted:
            target = (
                (
                    await connection.execute(
                        select(TARGETS)
                        .where(TARGETS.c.binding_name == binding_name, TARGETS.c.scope_id == scope_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            values = {
                "target_request_generation": intent.requested_generation,
                "source_through": through,
                "captured_flush_generation": int(snapshot["flush"]),
                "observed_dirty_generation": intent.dirty_generation,
            }
            if target is None:
                await connection.execute(insert(TARGETS).values(binding_name=binding_name, scope_id=scope_id, **values))
            else:
                values["source_through"] = max(through, int(target["source_through"]))
                await connection.execute(
                    update(TARGETS)
                    .where(TARGETS.c.binding_name == binding_name, TARGETS.c.scope_id == scope_id)
                    .values(**values)
                )
    # A receipt and all of its side effects commit together. Existence is tested
    # by the bounded anti-join before processing; it never executes an upsert++.
    await connection.execute(
        insert(RECEIPTS).values(
            migration_id=migration_id,
            binding_name=binding_name,
            scope_id=scope_id,
            source_snapshot=_snapshot_bytes(await _snapshot(connection, binding_name, scope_id)),
            requested_generation=intent.requested_generation,
            dirty_generation=intent.dirty_generation,
            source_through=through,
        )
    )


async def _snapshot(connection: AsyncConnection, binding_name: str, scope_id: str) -> dict[str, int]:
    pending = await ArtifactProcessingPendingRepository().load(connection, scope_id, binding_name)
    cursor = await SourceCursorRepository().load(connection, scope_id, binding_name)
    wave = (
        (
            await connection.execute(
                select(
                    func.count().label("count"),
                    func.sum(case((WAVES.c.completed.is_(False), 1), else_=0)).label("unfinished_count"),
                    func.max(case((WAVES.c.completed.is_(False), WAVES.c.source_through), else_=0)).label(
                        "unfinished_through"
                    ),
                    func.max(case((WAVES.c.completed.is_(True), WAVES.c.source_through), else_=0)).label(
                        "completed_through"
                    ),
                ).where(WAVES.c.binding_name == binding_name, WAVES.c.scope_id == scope_id)
            )
        )
        .mappings()
        .one()
    )
    return {
        "pending_through": 0 if pending is None else pending.source_through,
        "flush": 0 if pending is None else pending.flush_generation,
        "handled_flush": 0 if pending is None else pending.handled_flush_generation,
        "cursor": 0 if cursor is None else cursor.cursor.sequence,
        "cursor_generation": 0 if cursor is None else cursor.generation,
        "head": int(await connection.scalar(select(HEADS.c.position).where(HEADS.c.scope_id == scope_id)) or 0),
        "wave_count": int(wave["count"]),
        "unfinished_count": int(wave["unfinished_count"] or 0),
        "unfinished_through": int(wave["unfinished_through"] or 0),
        "completed_through": int(wave["completed_through"] or 0),
    }


async def _unmapped_keys(
    connection: AsyncConnection, migration_id: str, manifest: str, limit: int
) -> tuple[tuple[str, str], ...]:
    queries = [select(PENDING.c.binding_name, PENDING.c.scope_id), select(WAVES.c.binding_name, WAVES.c.scope_id)]
    queries.extend(select(literal(binding).label("binding_name"), HEADS.c.scope_id) for binding in _bindings(manifest))
    keys = queries[0].union(*queries[1:]).subquery()
    recorded = exists(
        select(1).where(
            RECEIPTS.c.migration_id == migration_id,
            RECEIPTS.c.binding_name == keys.c.binding_name,
            RECEIPTS.c.scope_id == keys.c.scope_id,
        )
    )
    rows = await connection.execute(
        select(keys.c.binding_name, keys.c.scope_id)
        .where(~recorded)
        .order_by(keys.c.binding_name, keys.c.scope_id)
        .limit(limit)
    )
    return tuple((str(binding), str(scope)) for binding, scope in rows)


async def _phase(connection: AsyncConnection, phase: str) -> None:
    await connection.execute(update(SCHEMA).where(SCHEMA.c.singleton == 1).values(phase=phase))


async def _marker(connection: AsyncConnection, *, for_update: bool = False) -> Mapping[str, Any] | None:
    if not await _has_table(connection, SCHEMA.name):
        return None
    statement = select(SCHEMA).where(SCHEMA.c.singleton == 1)
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    return None if row is None else dict(row)


async def _columns(connection: AsyncConnection, table_name: str) -> dict[str, dict[str, Any]]:
    if not await _has_table(connection, table_name):
        return {}
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns(table_name))
    return {str(column["name"]): dict(column) for column in columns}


async def _schema_issues(connection: AsyncConnection) -> list[str]:  # noqa: C901 - DDL postconditions
    """Validate existing DDL, not merely the presence of an earlier ADD COLUMN."""

    problems: list[str] = []
    for table in (*_NEW_TABLES, STATES):
        columns = await _columns(connection, table.name)
        for expected in table.columns:
            actual = columns.get(expected.name)
            if actual is None:
                problems.append(f"missing {table.name}.{expected.name}")
                continue
            mysql_boolean = (
                connection.dialect.name == "mysql"
                and isinstance(expected.type, Boolean)
                and isinstance(actual["type"], TINYINT)
                and getattr(actual["type"], "display_width", None) in {None, 1}
            )
            if actual["type"]._type_affinity is not expected.type._type_affinity and not mysql_boolean:
                problems.append(f"incompatible type for {table.name}.{expected.name}")
            if bool(actual["nullable"]) != expected.nullable:
                problems.append(f"incompatible nullability for {table.name}.{expected.name}")
            if expected.server_default is not None:
                actual_default = str(actual.get("default") or "").strip("()'\"")
                if actual_default != str(getattr(expected.server_default, "arg", None)):
                    problems.append(f"incompatible default for {table.name}.{expected.name}")
        if columns:
            primary = await connection.run_sync(lambda sync, owned=table: inspect(sync).get_pk_constraint(owned.name))
            if tuple(primary.get("constrained_columns") or ()) != tuple(column.name for column in table.primary_key):
                problems.append(f"incompatible primary key for {table.name}")
    if not problems:
        unique = await connection.run_sync(lambda sync: inspect(sync).get_unique_constraints(INTENTS.name))
        if not any(tuple(item.get("column_names") or ()) == ("pending_sequence",) for item in unique):
            problems.append("intent pending sequence is not unique")
    return problems


async def _has_table(connection: AsyncConnection, name: str) -> bool:
    return bool(await connection.run_sync(lambda sync: inspect(sync).has_table(name)))


async def _count_if_present(connection: AsyncConnection, table: Any) -> int:
    if not await _has_table(connection, table.name):
        return 0
    return int(await connection.scalar(select(func.count()).select_from(table)) or 0)


def _manifest(value: Mapping[str, Any] | str | None) -> str:
    decoded = json.loads(value) if isinstance(value, str) else dict(value or {})
    if not isinstance(decoded, dict):
        raise ProcessingSchemaNotReadyError(reason="Processing manifest must be a JSON object")
    if decoded.get("mode", "global") not in {"global", "dedicated"}:
        raise ProcessingSchemaNotReadyError(reason="Unknown processing Supervisor mode")
    return json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _deployment_manifest(manifest: str) -> str:
    value = json.loads(manifest)
    value.pop("legacy_automatic_bindings", None)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _bindings(manifest: str) -> dict[str, str]:
    value = json.loads(manifest).get("bindings", _DEFAULT_BINDINGS)
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ProcessingSchemaNotReadyError(reason="Manifest bindings must map stable binding names to Family names")
    return cast(dict[str, str], value)


def _automatic_bindings(manifest: str) -> tuple[str, ...]:
    value = json.loads(manifest).get("legacy_automatic_bindings", [_TOPIC_BINDING])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProcessingSchemaNotReadyError(reason="Legacy automatic bindings must be a list")
    return tuple(value)


def _snapshot_bytes(value: Mapping[str, int]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()


__all__ = [
    "PROCESSING_SCHEMA_VERSION",
    "ProcessingMigrationPlan",
    "ProcessingMigrationProgress",
    "ProcessingMigrationVerification",
    "ProcessingSchemaNotReadyError",
    "apply_processing_migration",
    "assert_processing_schema_ready",
    "bootstrap_processing_schema",
    "plan_processing_migration",
    "verify_processing_migration",
]
