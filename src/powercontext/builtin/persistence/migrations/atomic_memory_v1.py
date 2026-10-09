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

"""Offline memory.v1 -> atomic-memory.v1 domain conversion.

The input models, hash algorithm, SQL columns and output encoding in this file
are frozen. Do not substitute today's Memory/Artifact repositories or models:
old history must retain its original interpretation. The normal projection
publisher remains the boundary for a deployment's current search format.

Legacy tables remain read-only evidence. Idempotency follows deterministic
identities and exact imported revisions, not a processing schema marker. Each
entry commits independently; retry verifies existing content before continuing.
No old cursor, accepted request, Source, or collection content is rewritten.
"""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from types import SimpleNamespace
from typing import Any, ClassVar, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import BigInteger, CheckConstraint, Column, Index, MetaData, String, Table, inspect, text
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndex,
    AtomicMemoryProjectionPublisher,
    AtomicMemoryProjectionSecurity,
    AtomicMemoryReadGrant,
    PreparedAtomicMemoryProjection,
    atomic_memory_embedding_input_hash,
    atomic_memory_profile_fingerprint,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import PersistenceError

MIGRATION_ID = "powercontext.memory.v1-to-atomic-memory.v1"
_FAMILY = "atomic-memory"
_PROJECTION_FORMAT = "powercontext.atomic-memory.current.v1"
_ENTRY_HASH_DOMAIN = b"powercontext:entry-content:v1\0"


class AtomicMemoryMigrationError(PersistenceError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("Atomic Memory offline migration is not ready: " + "; ".join(errors))


class AtomicMemoryMigrationReport(BaseModel):
    migration: str = MIGRATION_ID
    action: Literal["plan", "apply", "verify"]
    ready: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()
    processing_snapshot_hash: str | None = None


class _FrozenValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _SourceRef(_FrozenValue):
    source_type: str
    source_id: str


class _ArtifactRef(_FrozenValue):
    family: str
    artifact_id: str
    revision: StrictInt = Field(ge=1)


class _Pointer(_FrozenValue):
    entry_id: str
    entry_version_id: str
    entry_content_hash: str
    state: Literal["active", "inactive"]


class _Manifest(_FrozenValue):
    entries: tuple[_Pointer, ...] = ()
    format: Literal["flat-v1"] = "flat-v1"


class _Change(_FrozenValue):
    op: Literal["add", "revise", "deactivate", "reactivate", "compact"]
    entry_id: str
    from_entry_version_id: str | None
    to_entry_version_id: str | None
    reason: str | None = None


class _MemoryContent(_FrozenValue):
    schema_: Literal["powercontext.memory.v1"] = Field(alias="schema")
    manifest: _Manifest
    changes: tuple[_Change, ...] = ()


class _AtomicContent(_FrozenValue):
    schema_: Literal["powercontext.atomic-memory.v1"] = Field(default="powercontext.atomic-memory.v1", alias="schema")
    kind: str
    text: str
    creation: None = None


class _AtomicArtifact(Artifact[_AtomicContent]):
    family: ClassVar[str] = _FAMILY


@dataclass(frozen=True)
class _Entry:
    scope_id: str
    memory_id: str
    entry_id: str
    versions: tuple[dict[str, Any], ...]
    state: Literal["active", "forgotten", "retired"]
    state_version: int
    collection_revision: int
    collection_content_hash: str

    @property
    def artifact_id(self) -> str:
        return legacy_entry_artifact_id(self.scope_id, self.memory_id, self.entry_id)

    @property
    def tail(self) -> dict[str, Any]:
        return self.versions[-1]


@dataclass(frozen=True)
class _Inventory:
    entries: tuple[_Entry, ...]
    counts: dict[str, int]
    errors: tuple[str, ...]
    processing_snapshot_hash: str


# Only the two Family tables are new. A separate metadata and fixed types keep
# the domain conversion stable when runtime declarations evolve.
_STATE_METADATA = MetaData()


def _identity(length: int) -> Any:
    return String(length).with_variant(VARCHAR(length, collation="utf8mb4_bin"), "mysql")


_STATES = Table(
    "pc_atomic_memory_states",
    _STATE_METADATA,
    Column("scope_id", _identity(256), primary_key=True),
    Column("artifact_id", _identity(128), primary_key=True),
    Column("state", _identity(16), nullable=False),
    Column("state_version", BigInteger, nullable=False),
    Column("merged_into_id", _identity(128)),
    CheckConstraint("state IN ('active', 'forgotten', 'merged', 'retired')", name="ck_pc_atomic_memory_state"),
    CheckConstraint("state_version >= 0", name="ck_pc_atomic_memory_state_version"),
    CheckConstraint(
        "(state = 'merged' AND merged_into_id IS NOT NULL AND merged_into_id <> artifact_id) "
        "OR (state <> 'merged' AND merged_into_id IS NULL)",
        name="ck_pc_atomic_memory_merge_target",
    ),
)
Index("ix_pc_atomic_memory_states_management", _STATES.c.scope_id, _STATES.c.state)

_ARTIFACT_COLUMNS = ("scope_id", "family", "artifact_id", "revision", "content", "memory_citations")
_HEAD_COLUMNS = (
    "scope_id",
    "family",
    "artifact_id",
    "revision",
    "searchable_text",
    "lifecycle_state",
    "replacement_artifact_id",
    "governance_generation",
)
_VERSION_COLUMNS = (
    "scope_id",
    "family",
    "memory_artifact_id",
    "entry_id",
    "entry_version_id",
    "version",
    "previous_version_id",
    "kind",
    "text",
    "source_refs",
    "artifact_refs",
    "entry_content_hash",
    "created_in_revision",
)
_OWNER_COLUMNS = (
    "owner_kind",
    "object_key_hash",
    "scope_id",
    "family",
    "artifact_id",
    "candidate_id",
    "target_artifact_id",
    "selector_type",
    "selector_entry_id",
    "owner_type",
    "owner_id",
    "owner_description",
    "established_at",
    "policy_revision",
    "idempotency_key",
)
_TAG_COLUMNS = (
    "scope_id",
    "family",
    "artifact_id",
    "target_type",
    "target_id",
    "tag_key_hash",
    "tag_key",
    "tag",
    "assigned_at",
)
_BINDING_COLUMNS = (
    "binding_id",
    "subject_type",
    "subject_id",
    "subject_description",
    "resource_key_hash",
    "resource_type",
    "deployment_id",
    "scope_id",
    "family",
    "artifact_id",
    "selector_type",
    "selector_entry_id",
    "role",
    "singleton_key",
    "granted_by_type",
    "granted_by_id",
    "granted_by_description",
    "reason",
    "created_at",
    "expires_at",
    "state",
    "version",
    "policy_revision",
    "idempotency_key",
    "revoked_at",
    "revoked_by_type",
    "revoked_by_id",
    "revoked_by_description",
)
_IDEMPOTENCY_COLUMNS = (
    "actor_id",
    "idempotency_key_hash",
    "operation",
    "payload_hash",
    "result_binding_id",
    "secondary_binding_id",
)
_PROCESSING_TABLES = (
    "pc_sources",
    "pc_source_journal_heads",
    "pc_source_cursors",
    "pc_memory_source_windows",
    "pc_artifact_processing_pending",
    "pc_artifact_processing_auto_wave_targets",
    "pc_artifact_processing_binding_states",
    "pc_artifact_processing_intents",
    "pc_artifact_processing_sequences",
    "pc_topic_memory_processing_targets",
    "pc_artifact_candidate_heads",
    "pc_artifact_candidate_versions",
)
_PROCESSING_COLUMNS = {
    "pc_sources": {"scope_id", "source_type", "source_id", "payload", "journal_position"},
    "pc_source_journal_heads": {"scope_id", "position"},
    "pc_source_cursors": {"scope_id", "binding_name", "cursor", "generation"},
    "pc_memory_source_windows": {"scope_id", "source_through", "window_limit"},
    "pc_artifact_processing_pending": {
        "binding_name",
        "scope_id",
        "source_through",
        "flush_generation",
        "handled_flush_generation",
    },
    "pc_artifact_processing_auto_wave_targets": {
        "wave_id",
        "binding_name",
        "scope_id",
        "source_through",
        "completed",
    },
    "pc_artifact_processing_binding_states": {
        "binding_name",
        "last_auto_wave_completed_at",
        "last_schedule_checkpoint_at",
        "scan_generation",
        "scan_in_progress",
        "scan_upper_pending_sequence",
    },
    "pc_artifact_processing_intents": {
        "binding_name",
        "scope_id",
        "pending_sequence",
        "dirty_generation",
        "clean_generation",
        "requested_generation",
        "handled_generation",
        "last_auto_scan_generation",
    },
    "pc_artifact_processing_sequences": {"singleton", "sequence"},
    "pc_topic_memory_processing_targets": {
        "binding_name",
        "scope_id",
        "target_request_generation",
        "source_through",
        "captured_flush_generation",
        "observed_dirty_generation",
    },
    "pc_artifact_candidate_heads": {
        "scope_id",
        "candidate_id",
        "family",
        "version",
        "status",
        "result_family",
        "result_artifact_id",
        "result_revision",
        "decision_reason",
    },
    "pc_artifact_candidate_versions": {
        "scope_id",
        "candidate_id",
        "version",
        "family",
        "proposal",
        "source_refs",
        "artifact_refs",
        "memory_citations",
        "target_family",
        "target_artifact_id",
        "target_revision",
        "reason",
    },
}


async def _tables(connection: AsyncConnection) -> set[str]:
    return set(await connection.run_sync(lambda value: inspect(value).get_table_names()))


async def _rows(
    connection: AsyncConnection, table: str, columns: tuple[str, ...], where: str = "", **params: Any
) -> list[dict[str, Any]]:
    # Every table, column and predicate is a constant owned by this versioned resource.
    result = await connection.execute(text(f"SELECT {', '.join(columns)} FROM {table} {where}"), params)  # noqa: S608
    return [dict(row) for row in result.mappings()]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return json.loads(bytes(value))
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError("expected a stored JSON payload")  # noqa: TRY003


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _resource_key(scope_id: str, family: str, artifact_id: str, entry_id: str | None = None) -> str:
    return _json_bytes({
        "type": "artifact",
        "scope_id": scope_id,
        "identity": {"family": family, "artifact_id": artifact_id},
        "selector": None if entry_id is None else {"type": "memory_entry", "entry_id": entry_id},
    }).decode("utf-8")


def _grant_creation_hash(binding: Mapping[str, Any], resource_key: str) -> str:
    # Frozen binding.create digest. Revocation changes no creation fields;
    # replacement creates a separate binding and a binding.replace receipt.
    expires_at = binding["expires_at"]
    if expires_at is not None:
        expiry = datetime.fromisoformat(str(expires_at))
        _require(expiry.tzinfo is not None, "grant creation receipt has an invalid expiry")
        expires_at = expiry.astimezone(UTC).isoformat(timespec="microseconds")
    return _digest(
        "\0".join((
            binding["subject_type"],
            binding["subject_id"],
            resource_key,
            binding["role"],
            binding["reason"] or "",
            expires_at or "",
        ))
    )


async def _grant_creation_receipt(
    connection: AsyncConnection, binding: Mapping[str, Any], legacy_key: str, new_key: str
) -> tuple[dict[str, Any], str] | None:
    receipts = await _rows(
        connection,
        "pc_access_idempotency",
        _IDEMPOTENCY_COLUMNS,
        "WHERE actor_id = :actor AND idempotency_key_hash = :key",
        actor=binding["granted_by_id"],
        key=_digest(binding["idempotency_key"]),
    )
    prefix = f"{binding['binding_id']}: grant creation receipt"
    _require(len(receipts) == 1, f"{prefix} is missing or ambiguous")
    receipt = receipts[0]
    if receipt["operation"] == "binding.replace":
        _require(
            receipt["secondary_binding_id"] == binding["binding_id"] and bool(receipt["result_binding_id"]),
            f"{prefix} replacement association differs",
        )
        return None
    _require(
        receipt["operation"] == "binding.create"
        and receipt["result_binding_id"] == binding["binding_id"]
        and receipt["secondary_binding_id"] is None,
        f"{prefix} association differs",
    )
    new_hash = _grant_creation_hash(binding, new_key)
    _require(
        receipt["payload_hash"] in {new_hash, _grant_creation_hash(binding, legacy_key)},
        f"{prefix} payload hash differs",
    )
    return receipt, new_hash


async def _mapped_grants(connection: AsyncConnection, entry: _Entry) -> list[dict[str, Any]]:
    return await _rows(
        connection,
        "pc_access_relationships",
        _BINDING_COLUMNS,
        "WHERE resource_type = 'artifact' AND scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id",
        scope=entry.scope_id,
        id=entry.artifact_id,
    )


async def _migrate_grant_receipts(connection: AsyncConnection, entry: _Entry) -> int:
    legacy_key = _resource_key(entry.scope_id, "memory", entry.memory_id, entry.entry_id)
    new_key = _resource_key(entry.scope_id, _FAMILY, entry.artifact_id)
    migrated = 0
    for binding in await _mapped_grants(connection, entry):
        try:
            creation = await _grant_creation_receipt(connection, binding, legacy_key, new_key)
        except ValueError as error:
            raise AtomicMemoryMigrationError((str(error),)) from error
        if creation is None or creation[0]["payload_hash"] == creation[1]:
            continue
        receipt, new_hash = creation
        result = await connection.execute(
            text(
                "UPDATE pc_access_idempotency SET payload_hash = :new_hash "
                "WHERE actor_id = :actor_id AND idempotency_key_hash = :idempotency_key_hash "
                "AND operation = 'binding.create' AND payload_hash = :payload_hash "
                "AND result_binding_id = :result_binding_id AND secondary_binding_id IS NULL"
            ),
            {**receipt, "new_hash": new_hash},
        )
        if result.rowcount != 1:
            raise AtomicMemoryMigrationError((
                f"{binding['binding_id']}: grant creation receipt changed during maintenance",
            ))
        migrated += 1
    return migrated


def _content(row: Mapping[str, Any]) -> _AtomicContent:
    return _AtomicContent(kind=str(row["kind"]), text=str(row["text"]))


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", key): _normalize(item) for key, item in value.items()}
    return value


def _entry_hash(row: Mapping[str, Any]) -> str:
    def refs(column: str) -> list[Any]:
        values = _normalize(_decode(row[column]))
        encoded = {rfc8785.dumps(item): item for item in values}
        return [encoded[key] for key in sorted(encoded)]

    payload = {
        "kind": unicodedata.normalize("NFC", str(row["kind"])).strip(),
        "text": unicodedata.normalize("NFC", str(row["text"])).strip(),
        "source_refs": refs("source_refs"),
        "artifact_refs": refs("artifact_refs"),
    }
    return sha256(_ENTRY_HASH_DOMAIN + rfc8785.dumps(payload)).hexdigest()


async def _processing_snapshot(connection: AsyncConnection, tables: set[str], errors: list[str]) -> str:
    digest = sha256()
    for name in _PROCESSING_TABLES:
        if name not in tables:
            continue
        # Read unchanged scheduler records as bytes and scalar values, without
        # routing them through future scheduling models.
        records = (await connection.execute(text(f"SELECT * FROM {name}"))).mappings()  # noqa: S608
        encoded: list[bytes] = []
        for row in records:
            values = dict(row)
            if set(values) != _PROCESSING_COLUMNS[name]:
                errors.append(f"{name}: unsupported persisted task columns; complete processing schema migration first")
            if name == "pc_source_cursors":
                try:
                    cursor = _decode(values["cursor"])
                    if set(cursor) != {"sequence"} or type(cursor["sequence"]) is not int or cursor["sequence"] < 0:
                        errors.append(
                            f"{name}: unsupported cursor format at {values['scope_id']}/{values['binding_name']}"
                        )
                except (TypeError, ValueError, KeyError):
                    errors.append(f"{name}: undecodable cursor")
            if (
                name == "pc_artifact_candidate_heads"
                and values.get("family") == "memory"
                and values.get("status") == "pending"
            ):
                errors.append(f"pending legacy Memory candidate {values['candidate_id']} requires explicit resolution")
            projected = {
                key: {"bytes": bytes(value).hex()}
                if isinstance(value, bytes | bytearray | memoryview)
                else value.isoformat()
                if isinstance(value, datetime)
                else value
                for key, value in values.items()
            }
            encoded.append(_json_bytes(projected))
        digest.update(name.encode())
        for value in sorted(encoded):
            digest.update(value)
    return digest.hexdigest()


async def _inventory(connection: AsyncConnection) -> _Inventory:  # noqa: C901
    tables = await _tables(connection)
    errors: list[str] = []
    counts = {
        "containers": 0,
        "collection_revisions": 0,
        "entries": 0,
        "entry_versions": 0,
        "active": 0,
        "forgotten": 0,
        "retired": 0,
    }
    if "pc_artifacts" not in tables:
        return _Inventory((), counts, (), sha256(b"").hexdigest())
    snapshots = await _rows(connection, "pc_artifacts", _ARTIFACT_COLUMNS, "WHERE family = 'memory'")
    required = {
        "pc_artifact_heads",
        "pc_memory_entry_versions",
        "pc_artifact_lineage_artifacts",
        "pc_artifact_lineage_sources",
        "pc_artifact_tags",
        "pc_sources",
        "pc_access_owners",
        "pc_access_relationships",
        "pc_access_idempotency",
    }
    if snapshots and not required <= tables:
        return _Inventory(
            (),
            counts,
            ("legacy migration tables are absent: " + ", ".join(sorted(required - tables)),),
            sha256(b"").hexdigest(),
        )
    versions = (
        []
        if "pc_memory_entry_versions" not in tables
        else await _rows(connection, "pc_memory_entry_versions", _VERSION_COLUMNS)
    )
    heads = (
        []
        if "pc_artifact_heads" not in tables
        else await _rows(connection, "pc_artifact_heads", _HEAD_COLUMNS, "WHERE family = 'memory'")
    )
    processing_hash = await _processing_snapshot(connection, tables, errors)
    for row in await _rows(
        connection, "pc_artifacts", _ARTIFACT_COLUMNS, "WHERE family = 'prompt' AND artifact_id = 'memory.extract'"
    ):
        current = await connection.scalar(
            text(
                "SELECT revision FROM pc_artifact_heads WHERE scope_id = :scope_id AND family = 'prompt' "
                "AND artifact_id = 'memory.extract'"
            ),
            {"scope_id": row["scope_id"]},
        )
        if current == row["revision"]:
            try:
                if _decode(row["content"]).get("mode") == "custom":
                    errors.append(
                        f"{row['scope_id']}: custom memory.extract requires explicit Atomic Prompt conversion"
                    )
            except (ValueError, AttributeError):
                errors.append(f"{row['scope_id']}: legacy extraction Prompt is undecodable")
    grouped_snapshots: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    grouped_versions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in snapshots:
        grouped_snapshots[(row["scope_id"], row["artifact_id"])].append(row)
    for row in versions:
        grouped_versions[(row["scope_id"], row["memory_artifact_id"])].append(row)
    head_map = {(row["scope_id"], row["artifact_id"]): row for row in heads}
    entries: list[_Entry] = []
    for key in sorted(set(grouped_snapshots) | set(grouped_versions)):
        prefix = f"{key[0]}/{key[1]}"
        old_revisions = sorted(grouped_snapshots[key], key=lambda item: item["revision"])
        rows = grouped_versions[key]
        counts["containers"] += 1
        counts["collection_revisions"] += len(old_revisions)
        counts["entry_versions"] += len(rows)
        try:
            migrated = _validate_container(key, old_revisions, rows, head_map.get(key))
            await _validate_legacy_bindings(connection, key, {entry.entry_id for entry in migrated})
            for entry in migrated:
                await _validate_evidence(connection, entry)
                await _validate_owner(connection, tables, entry)
                counts[entry.state] += 1
            entries.extend(migrated)
        except (ValueError, ValidationError) as error:
            errors.append(f"{prefix}: {error}")
    counts["entries"] = len(entries)
    counts["legacy_collection_payload_bytes"] = sum(len(bytes(row["content"])) for row in snapshots)
    counts["atomic_content_payload_bytes"] = sum(
        len(_content(row).model_dump_json(by_alias=True).encode("utf-8")) for entry in entries for row in entry.versions
    )
    return _Inventory(tuple(entries), counts, tuple(errors), processing_hash)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_container(  # noqa: C901
    key: tuple[str, str],
    snapshots: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    head: dict[str, Any] | None,
) -> tuple[_Entry, ...]:
    _require(bool(snapshots), "entry history has no owning collection history")
    _require(
        [item["revision"] for item in snapshots] == list(range(1, len(snapshots) + 1)),
        "collection revisions are not continuous from 1",
    )
    _require(
        head is not None and head["revision"] == snapshots[-1]["revision"],
        "collection head does not point to its history tail",
    )
    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_version: dict[str, dict[str, Any]] = {}
    for row in rows:
        _require(row["family"] == "memory", "entry history uses an unsupported Family")
        _require(row["entry_version_id"] not in by_version, "duplicate entry-version identity")
        by_version[row["entry_version_id"]] = row
        by_entry[row["entry_id"]].append(row)
    for entry_id, chain in by_entry.items():
        chain.sort(key=lambda item: item["version"])
        _require(
            [item["version"] for item in chain] == list(range(1, len(chain) + 1)),
            f"{entry_id}: versions are not continuous from 1",
        )
        previous = None
        for row in chain:
            _require(row["previous_version_id"] == previous, f"{entry_id}: broken or branched previous-version chain")
            _require(row["entry_content_hash"] == _entry_hash(row), f"{entry_id}: content hash disagrees")
            _require(1 <= row["created_in_revision"] <= len(snapshots), f"{entry_id}: creation revision is absent")
            _require(bool(row["kind"].strip()) and len(row["kind"]) <= 128, f"{entry_id}: invalid kind")
            _require(
                bool(row["text"].strip()) and len(row["text"].encode("utf-8")) <= 8192, f"{entry_id}: invalid text"
            )
            previous = row["entry_version_id"]
    previous_manifest: dict[str, _Pointer] = {}
    last_seen: dict[str, _Pointer] = {}
    state_versions: dict[str, int] = defaultdict(int)
    compacted: set[str] = set()
    observed_versions: set[str] = set()
    for snapshot in snapshots:
        content = _MemoryContent.model_validate_json(bytes(snapshot["content"]), strict=True)
        manifest = {pointer.entry_id: pointer for pointer in content.manifest.entries}
        _require(len(manifest) == len(content.manifest.entries), "manifest repeats a logical entry")
        changes = {change.entry_id: change for change in content.changes}
        _require(len(changes) == len(content.changes), "changes repeat a logical entry")
        for entry_id in set(previous_manifest) | set(manifest) | set(changes):
            before, after, change = previous_manifest.get(entry_id), manifest.get(entry_id), changes.get(entry_id)
            if after is not None:
                row = by_version.get(after.entry_version_id)
                _require(
                    row is not None and row["entry_id"] == entry_id,
                    f"{entry_id}: manifest pointer has another identity or is missing",
                )
                if row is None:
                    raise ValueError("manifest version is missing")  # noqa: TRY003
                _require(row["entry_content_hash"] == after.entry_content_hash, f"{entry_id}: manifest hash differs")
                _require(
                    row["created_in_revision"] <= snapshot["revision"],
                    f"{entry_id}: manifest references a future version",
                )
                _require(entry_id not in compacted, f"{entry_id}: compacted identity reappears")
                if row["created_in_revision"] == snapshot["revision"]:
                    observed_versions.add(after.entry_version_id)
                last_seen[entry_id] = after
            if before == after:
                _require(change is None, f"{entry_id}: change without a manifest transition")
                continue
            _require(change is not None, f"{entry_id}: manifest transition lacks a change")
            if change is None:
                raise ValueError("manifest transition lacks a change")  # noqa: TRY003
            expected_from = None if before is None or change.op == "reactivate" else before.entry_version_id
            expected_to = None if after is None or change.op == "deactivate" else after.entry_version_id
            _require(
                change.from_entry_version_id == expected_from and change.to_entry_version_id == expected_to,
                f"{entry_id}: change pointers disagree",
            )
            if before is None:
                _require(
                    change.op == "add" and after is not None and after.state == "active", f"{entry_id}: invalid add"
                )
            elif after is None:
                _require(
                    change.op == "compact" and before.state == "inactive", f"{entry_id}: unexplained disappearance"
                )
                compacted.add(entry_id)
                state_versions[entry_id] += 1
            elif before.entry_version_id != after.entry_version_id:
                _require(change.op == "revise" and before.state == after.state, f"{entry_id}: invalid revise")
                _require(
                    by_version[after.entry_version_id]["previous_version_id"] == before.entry_version_id,
                    f"{entry_id}: manifest skips an entry version",
                )
            else:
                expected = "deactivate" if after.state == "inactive" else "reactivate"
                _require(change.op == expected, f"{entry_id}: invalid lifecycle change")
                state_versions[entry_id] += 1
        previous_manifest = manifest
    _require(observed_versions == set(by_version), "entry versions are not present at their creation revisions")
    result: list[_Entry] = []
    for entry_id, chain in sorted(by_entry.items()):
        pointer = last_seen.get(entry_id)
        _require(
            pointer is not None and pointer.entry_version_id == chain[-1]["entry_version_id"],
            f"{entry_id}: current or last compacted pointer is behind the entry tail",
        )
        state = (
            "retired"
            if entry_id in compacted
            else "active"
            if previous_manifest[entry_id].state == "active"
            else "forgotten"
        )
        result.append(
            _Entry(
                key[0],
                key[1],
                entry_id,
                tuple(chain),
                state,
                state_versions[entry_id],
                snapshots[-1]["revision"],
                sha256(bytes(snapshots[-1]["content"])).hexdigest(),
            )
        )
    return tuple(result)


async def _validate_evidence(connection: AsyncConnection, entry: _Entry) -> None:
    for row in entry.versions:
        for value in _decode(row["source_refs"]):
            ref = _SourceRef.model_validate_json(_json_bytes(value), strict=True)
            present = await connection.scalar(
                text("SELECT 1 FROM pc_sources WHERE scope_id = :scope AND source_type = :type AND source_id = :id"),
                {"scope": entry.scope_id, "type": ref.source_type, "id": ref.source_id},
            )
            _require(present == 1, f"{entry.entry_id}: exact Source evidence is missing")
        for value in _decode(row["artifact_refs"]):
            ref = _ArtifactRef.model_validate_json(_json_bytes(value), strict=True)
            present = await connection.scalar(
                text(
                    "SELECT 1 FROM pc_artifacts WHERE scope_id = :scope AND family = :family AND artifact_id = :id AND revision = :revision"
                ),
                {"scope": entry.scope_id, "family": ref.family, "id": ref.artifact_id, "revision": ref.revision},
            )
            _require(present == 1, f"{entry.entry_id}: exact Artifact evidence is missing")


async def _validate_owner(connection: AsyncConnection, tables: set[str], entry: _Entry) -> None:
    _require(
        "pc_access_owners" in tables and "pc_access_relationships" in tables,
        f"{entry.entry_id}: formal access schema is absent",
    )
    owners = await _rows(
        connection,
        "pc_access_owners",
        _OWNER_COLUMNS,
        "WHERE owner_kind = 'artifact' AND scope_id = :scope AND family = 'memory' AND artifact_id = :memory "
        "AND selector_type = 'memory_entry' AND selector_entry_id = :entry",
        scope=entry.scope_id,
        memory=entry.memory_id,
        entry=entry.entry_id,
    )
    new_owners = await _rows(
        connection,
        "pc_access_owners",
        _OWNER_COLUMNS,
        "WHERE owner_kind = 'artifact' AND object_key_hash = :key",
        key=_digest(_resource_key(entry.scope_id, _FAMILY, entry.artifact_id)),
    )
    _require(len(owners) == 1, f"{entry.entry_id}: exact per-entry Owner is missing or ambiguous")
    owner = owners[0]
    _require(
        owner["object_key_hash"] == _digest(_resource_key(entry.scope_id, "memory", entry.memory_id, entry.entry_id)),
        f"{entry.entry_id}: legacy Owner identity hash differs",
    )
    _require(
        bool(owner["owner_id"]) and owner["owner_type"] in {"user", "service"},
        f"{entry.entry_id}: invalid formal Owner",
    )
    _require(
        not new_owners
        or (
            len(new_owners) == 1
            and new_owners[0]["owner_type"] == owner["owner_type"]
            and new_owners[0]["owner_id"] == owner["owner_id"]
        ),
        f"{entry.entry_id}: mapped Owner conflicts",
    )


async def _validate_legacy_bindings(connection: AsyncConnection, key: tuple[str, str], entry_ids: set[str]) -> None:
    bindings = await _rows(
        connection,
        "pc_access_relationships",
        _BINDING_COLUMNS,
        "WHERE resource_type = 'artifact' AND scope_id = :scope AND family = 'memory' AND artifact_id = :memory",
        scope=key[0],
        memory=key[1],
    )
    for binding in bindings:
        _require(
            binding["selector_type"] == "memory_entry" and bool(binding["selector_entry_id"]),
            "legacy Memory grant has no supported per-entry scope",
        )
        _require(binding["selector_entry_id"] in entry_ids, "legacy Memory grant targets an unknown entry identity")
        _require(
            binding["role"] in {"artifact.viewer", "artifact.owner"}, "legacy Memory grant has an unsupported role"
        )
        _require(
            binding["state"] in {"active", "revoked"} and binding["version"] > 0,
            "legacy Memory grant has an unsupported state",
        )
        _require(
            binding["resource_key_hash"]
            == _digest(_resource_key(key[0], "memory", key[1], binding["selector_entry_id"])),
            "legacy grant resource identity hash differs",
        )
        await _grant_creation_receipt(
            connection,
            binding,
            _resource_key(key[0], "memory", key[1], binding["selector_entry_id"]),
            _resource_key(key[0], _FAMILY, legacy_entry_artifact_id(key[0], key[1], binding["selector_entry_id"])),
        )


async def plan_atomic_memory_migration(
    connection: AsyncConnection, *, index: AtomicMemoryIndex | None = None
) -> AtomicMemoryMigrationReport:
    """Scan every legacy container and entry version without changing data."""

    inventory = await _inventory(connection)
    tables = await _tables(connection)
    pending = len(inventory.entries)
    if "pc_atomic_memory_states" in tables:
        pending = 0
        for entry in inventory.entries:
            present = await connection.scalar(
                text("SELECT 1 FROM pc_atomic_memory_states WHERE scope_id = :scope AND artifact_id = :id"),
                {"scope": entry.scope_id, "id": entry.artifact_id},
            )
            pending += present is None
    verification = await verify_atomic_memory_migration(connection, index=index) if pending == 0 else None
    return AtomicMemoryMigrationReport(
        action="plan",
        ready=verification is not None and verification.ready,
        counts={
            **inventory.counts,
            **({} if verification is None else verification.counts),
            "pending_entries": pending,
        },
        errors=inventory.errors if verification is None else verification.errors,
        processing_snapshot_hash=inventory.processing_snapshot_hash,
    )


async def _insert(connection: AsyncConnection, table: str, columns: tuple[str, ...], values: Mapping[str, Any]) -> None:
    await connection.execute(
        text(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + name for name in columns)})"  # noqa: S608
        ),
        {name: values.get(name) for name in columns},
    )


def _imported_refs(entry: _Entry, row: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    # The creating collection is an immutable provenance anchor, not every
    # entry's generation evidence. Runtime readers resolve this imported
    # identity against its exact retained entry version before following Sources.
    values = [
        {"family": "memory", "artifact_id": entry.memory_id, "revision": row["created_in_revision"]},
        *_decode(row["artifact_refs"]),
    ]
    if row["version"] > 1:
        # Legacy entry evidence accumulates, while a collection revision only
        # records the evidence supplied to that particular collection write.
        # Following the imported predecessor retains inherited Sources without
        # copying a lineage_only Source onto a different exact target.
        values.append({"family": _FAMILY, "artifact_id": entry.artifact_id, "revision": row["version"] - 1})
    refs: list[dict[str, Any]] = []
    for value in values:
        if value not in refs:
            refs.append(value)
    return tuple(refs)


async def _history_issues(connection: AsyncConnection, entry: _Entry) -> list[str]:
    errors: list[str] = []
    for row in entry.versions:
        imported = await _rows(
            connection,
            "pc_artifacts",
            _ARTIFACT_COLUMNS,
            "WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id AND revision = :revision",
            scope=entry.scope_id,
            id=entry.artifact_id,
            revision=row["version"],
        )
        expected = _content(row).model_dump(mode="json", by_alias=True)
        if (
            len(imported) != 1
            or _decode(imported[0]["content"]) != expected
            or imported[0]["memory_citations"] is not None
        ):
            errors.append(f"{entry.entry_id}@{row['version']}: imported body differs or is missing")
            continue
        refs = await _rows(
            connection,
            "pc_artifact_lineage_artifacts",
            ("ordinal", "upstream_family", "upstream_artifact_id", "upstream_revision"),
            "WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id AND revision = :revision ORDER BY ordinal",
            scope=entry.scope_id,
            id=entry.artifact_id,
            revision=row["version"],
        )
        actual = tuple(
            {
                "family": item["upstream_family"],
                "artifact_id": item["upstream_artifact_id"],
                "revision": item["upstream_revision"],
            }
            for item in refs
        )
        if actual != _imported_refs(entry, row) or [item["ordinal"] for item in refs] != list(range(len(refs))):
            errors.append(f"{entry.entry_id}@{row['version']}: imported exact evidence differs")
        direct_sources = await connection.scalar(
            text(
                "SELECT COUNT(*) FROM pc_artifact_lineage_sources WHERE scope_id = :scope AND family = 'atomic-memory' "
                "AND artifact_id = :id AND revision = :revision"
            ),
            {"scope": entry.scope_id, "id": entry.artifact_id, "revision": row["version"]},
        )
        if direct_sources:
            errors.append(f"{entry.entry_id}@{row['version']}: import unexpectedly rebound historical Sources")
    return errors


async def _head_and_state(
    connection: AsyncConnection, entry: _Entry
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    heads = await _rows(
        connection,
        "pc_artifact_heads",
        _HEAD_COLUMNS,
        "WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id",
        scope=entry.scope_id,
        id=entry.artifact_id,
    )
    states = await _rows(
        connection,
        "pc_atomic_memory_states",
        ("scope_id", "artifact_id", "state", "state_version", "merged_into_id"),
        "WHERE scope_id = :scope AND artifact_id = :id",
        scope=entry.scope_id,
        id=entry.artifact_id,
    )
    return (None if not heads else heads[0], None if not states else states[0])


async def _import_entry(connection: AsyncConnection, entry: _Entry) -> bool:
    head, state = await _head_and_state(connection, entry)
    if head is not None:
        errors = await _history_issues(connection, entry)
        if errors or state is None or head["revision"] < entry.tail["version"]:
            raise AtomicMemoryMigrationError(tuple(errors) or (f"{entry.entry_id}: existing target is incomplete",))
        # A completed import may since have been revised, merged or retagged.
        # Its head and mutable metadata never return to the legacy snapshot.
        return False
    existing = await connection.scalar(
        text(
            "SELECT COUNT(*) FROM pc_artifacts WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id"
        ),
        {"scope": entry.scope_id, "id": entry.artifact_id},
    )
    if existing or state is not None:
        raise AtomicMemoryMigrationError((
            f"{entry.entry_id}: target has orphan content or state; no overwrite allowed",
        ))
    for row in entry.versions:
        await _insert(
            connection,
            "pc_artifacts",
            _ARTIFACT_COLUMNS,
            {
                "scope_id": entry.scope_id,
                "family": _FAMILY,
                "artifact_id": entry.artifact_id,
                "revision": row["version"],
                "content": _content(row).model_dump_json(by_alias=True).encode("utf-8"),
                "memory_citations": None,
            },
        )
        for ordinal, ref in enumerate(_imported_refs(entry, row)):
            await _insert(
                connection,
                "pc_artifact_lineage_artifacts",
                (
                    "scope_id",
                    "family",
                    "artifact_id",
                    "revision",
                    "ordinal",
                    "upstream_family",
                    "upstream_artifact_id",
                    "upstream_revision",
                ),
                {
                    "scope_id": entry.scope_id,
                    "family": _FAMILY,
                    "artifact_id": entry.artifact_id,
                    "revision": row["version"],
                    "ordinal": ordinal,
                    "upstream_family": ref["family"],
                    "upstream_artifact_id": ref["artifact_id"],
                    "upstream_revision": ref["revision"],
                },
            )
    summary = "active" if entry.state == "active" else "retired" if entry.state == "retired" else "deprecated"
    await _insert(
        connection,
        "pc_artifact_heads",
        _HEAD_COLUMNS,
        {
            "scope_id": entry.scope_id,
            "family": _FAMILY,
            "artifact_id": entry.artifact_id,
            "revision": entry.tail["version"],
            "searchable_text": None,
            "lifecycle_state": summary,
            "replacement_artifact_id": None,
            "governance_generation": entry.state_version,
        },
    )
    await _insert(
        connection,
        "pc_atomic_memory_states",
        ("scope_id", "artifact_id", "state", "state_version", "merged_into_id"),
        {
            "scope_id": entry.scope_id,
            "artifact_id": entry.artifact_id,
            "state": entry.state,
            "state_version": entry.state_version,
            "merged_into_id": None,
        },
    )
    owner = (
        await _rows(
            connection,
            "pc_access_owners",
            _OWNER_COLUMNS,
            "WHERE owner_kind = 'artifact' AND scope_id = :scope AND family = 'memory' AND artifact_id = :memory "
            "AND selector_type = 'memory_entry' AND selector_entry_id = :entry",
            scope=entry.scope_id,
            memory=entry.memory_id,
            entry=entry.entry_id,
        )
    )[0]
    mapped_owner = {
        **owner,
        "object_key_hash": _digest(_resource_key(entry.scope_id, _FAMILY, entry.artifact_id)),
        "family": _FAMILY,
        "artifact_id": entry.artifact_id,
        "selector_type": None,
        "selector_entry_id": None,
    }
    present = await connection.scalar(
        text("SELECT COUNT(*) FROM pc_access_owners WHERE owner_kind = 'artifact' AND object_key_hash = :key"),
        {"key": mapped_owner["object_key_hash"]},
    )
    if not present:
        await _insert(connection, "pc_access_owners", _OWNER_COLUMNS, mapped_owner)
    tags = await _rows(
        connection,
        "pc_artifact_tags",
        _TAG_COLUMNS,
        "WHERE scope_id = :scope AND family = 'memory' AND artifact_id = :memory AND target_type = 'memory_entry' AND target_id = :entry",
        scope=entry.scope_id,
        memory=entry.memory_id,
        entry=entry.entry_id,
    )
    for tag in tags:
        await _insert(
            connection,
            "pc_artifact_tags",
            _TAG_COLUMNS,
            {
                **tag,
                "family": _FAMILY,
                "artifact_id": entry.artifact_id,
                "target_type": "artifact",
                "target_id": entry.artifact_id,
            },
        )
    bindings = await _rows(
        connection,
        "pc_access_relationships",
        _BINDING_COLUMNS,
        "WHERE resource_type = 'artifact' AND scope_id = :scope AND family = 'memory' AND artifact_id = :memory "
        "AND selector_type = 'memory_entry' AND selector_entry_id = :entry",
        scope=entry.scope_id,
        memory=entry.memory_id,
        entry=entry.entry_id,
    )
    new_key = _resource_key(entry.scope_id, _FAMILY, entry.artifact_id)
    for binding in bindings:
        singleton = None
        if binding["singleton_key"] is not None:
            singleton = _digest(json.dumps((new_key, binding["role"]), separators=(",", ":")))
        await connection.execute(
            text(
                "UPDATE pc_access_relationships SET family = 'atomic-memory', artifact_id = :id, selector_type = NULL, "
                "selector_entry_id = NULL, resource_key_hash = :key, singleton_key = :singleton "
                "WHERE binding_id = :binding AND resource_key_hash = :old_key"
            ),
            {
                "id": entry.artifact_id,
                "key": _digest(new_key),
                "singleton": singleton,
                "binding": binding["binding_id"],
                "old_key": binding["resource_key_hash"],
            },
        )
    return True


async def _load_tags(connection: AsyncConnection, scope_id: str, artifact_id: str) -> tuple[str, ...]:
    rows = await _rows(
        connection,
        "pc_artifact_tags",
        ("tag_key",),
        "WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id AND target_type = 'artifact' AND target_id = :id",
        scope=scope_id,
        id=artifact_id,
    )
    return tuple(sorted({row["tag_key"] for row in rows}))


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def _load_security(
    connection: AsyncConnection, scope_id: str, artifact_id: str, _context: Any
) -> AtomicMemoryProjectionSecurity:
    key = _digest(_resource_key(scope_id, _FAMILY, artifact_id))
    owners = await _rows(
        connection,
        "pc_access_owners",
        _OWNER_COLUMNS,
        "WHERE owner_kind = 'artifact' AND object_key_hash = :key",
        key=key,
    )
    if len(owners) != 1:
        raise AtomicMemoryMigrationError((f"{scope_id}/{artifact_id}: formal Owner is absent or ambiguous",))
    grants = await _rows(
        connection,
        "pc_access_relationships",
        _BINDING_COLUMNS,
        "WHERE resource_key_hash = :key AND state = 'active' AND role IN ('artifact.viewer', 'artifact.owner')",
        key=key,
    )
    return AtomicMemoryProjectionSecurity(
        owner_type=owners[0]["owner_type"],
        owner_id=owners[0]["owner_id"],
        read_grants=tuple(
            AtomicMemoryReadGrant(
                binding_id=row["binding_id"],
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                expires_at=_timestamp(row["expires_at"]),
            )
            for row in sorted(grants, key=lambda item: item["binding_id"])
        ),
    )


async def apply_atomic_memory_migration(
    database: AsyncDatabase,
    index: AtomicMemoryIndex,
    *,
    maintenance_confirmed: bool,
    embedding_model: Any = None,
) -> AtomicMemoryMigrationReport:
    """Prepare outside transactions; commit immutable history and projections together."""

    if not maintenance_confirmed:
        raise AtomicMemoryMigrationError(("apply requires stopped old writers and --maintenance-confirmed",))
    started = perf_counter()
    async with database.transaction() as connection:
        inventory = await _inventory(connection)
    if inventory.errors:
        return AtomicMemoryMigrationReport(
            action="apply",
            errors=inventory.errors,
            counts=inventory.counts,
            processing_snapshot_hash=inventory.processing_snapshot_hash,
        )
    async with database.transaction() as connection:
        await connection.run_sync(lambda value: _STATE_METADATA.create_all(value, tables=[_STATES], checkfirst=True))
        await index.initialize(connection)
    publisher = AtomicMemoryProjectionPublisher(
        index, load_tags=_load_tags, load_security=_load_security, embedding_model=embedding_model
    )
    imported = 0
    migrated_grant_receipts = 0
    for entry in inventory.entries:
        async with database.transaction() as connection:
            head, _state = await _head_and_state(connection, entry)
        prepared: PreparedAtomicMemoryProjection | None = None
        if head is None and entry.state == "active":
            prepared = await publisher.prepare(_content(entry.tail))
        async with database.transaction() as connection:
            # Explicit maintenance is still required; this detects writes to a
            # container between initial inspection and this entry's commit.
            current = await connection.execute(
                text(
                    "SELECT h.revision, a.content FROM pc_artifact_heads h JOIN pc_artifacts a "
                    "ON a.scope_id = h.scope_id AND a.family = h.family AND a.artifact_id = h.artifact_id AND a.revision = h.revision "
                    "WHERE h.scope_id = :scope AND h.family = 'memory' AND h.artifact_id = :id"
                ),
                {"scope": entry.scope_id, "id": entry.memory_id},
            )
            source = current.mappings().one_or_none()
            if (
                source is None
                or source["revision"] != entry.collection_revision
                or sha256(bytes(source["content"])).hexdigest() != entry.collection_content_hash
            ):
                raise AtomicMemoryMigrationError(("legacy collection head changed during maintenance",))
            imported_entry = await _import_entry(connection, entry)
            migrated_grant_receipts += await _migrate_grant_receipts(connection, entry)
            if not imported_entry:
                continue
            if prepared is not None:
                record = SimpleNamespace(
                    artifact=_AtomicArtifact(
                        artifact_id=entry.artifact_id, revision=entry.tail["version"], content=_content(entry.tail)
                    ),
                    state=SimpleNamespace(state="active", state_version=entry.state_version),
                )
                await publisher.publish(connection, entry.scope_id, record, prepared, None)
            else:
                await publisher.remove(connection, entry.scope_id, entry.artifact_id)
            imported += 1
    async with database.transaction() as connection:
        tables = await _tables(connection)
        after = await _processing_snapshot(connection, tables, [])
        if after != inventory.processing_snapshot_hash:
            raise AtomicMemoryMigrationError((
                "cursor, generation, high-water mark or accepted processing work changed",
            ))
        if "pc_artifact_processing_leases" in tables:
            await connection.execute(
                text(
                    "UPDATE pc_artifact_processing_leases SET holder_id = 'atomic-memory-v1-maintenance', "
                    "supervisor_generation = supervisor_generation + 1, lease_expires_at = :expired "
                    "WHERE holder_id <> 'atomic-memory-v1-maintenance' OR lease_expires_at IS NULL OR lease_expires_at > :expired"
                ),
                {"expired": datetime(1970, 1, 1, tzinfo=UTC).replace(tzinfo=None)},
            )
        report = await verify_atomic_memory_migration(connection, index=index)
    return report.model_copy(
        update={
            "action": "apply",
            "counts": {
                **report.counts,
                "imported_entries": imported,
                "migrated_grant_receipts": migrated_grant_receipts,
                "elapsed_ms": int((perf_counter() - started) * 1000),
            },
        }
    )


async def verify_atomic_memory_migration(
    connection: AsyncConnection,
    *,
    index: AtomicMemoryIndex | None = None,
) -> AtomicMemoryMigrationReport:
    """Prove import plus current consistency; never reset a post-upgrade head."""

    return await _verify_atomic_memory_migration(connection, index=index, check_projection=True)


async def verify_atomic_memory_migration_authority(connection: AsyncConnection) -> AtomicMemoryMigrationReport:
    """Require completed history import before repairing only the derived current rows."""

    return await _verify_atomic_memory_migration(connection, check_projection=False)


async def _verify_atomic_memory_migration(  # noqa: C901 - One frozen import verification with optional projection checks.
    connection: AsyncConnection,
    *,
    index: AtomicMemoryIndex | None = None,
    check_projection: bool,
) -> AtomicMemoryMigrationReport:

    inventory = await _inventory(connection)
    errors = list(inventory.errors)
    tables = await _tables(connection)
    required = {"pc_atomic_memory_states"}
    if check_projection:
        required.add("pc_atomic_memory_current")
    if inventory.entries and not required <= tables:
        errors.append("Atomic Memory Family state/current tables are absent")
        return AtomicMemoryMigrationReport(
            action="verify",
            errors=tuple(errors),
            counts=inventory.counts,
            processing_snapshot_hash=inventory.processing_snapshot_hash,
        )
    verified = 0
    pending_grant_receipts = 0
    for entry in inventory.entries:
        prefix = f"{entry.scope_id}/{entry.memory_id}/{entry.entry_id}"
        previous_errors = len(errors)
        try:
            errors.extend(f"{prefix}: {issue}" for issue in await _history_issues(connection, entry))
            head, state = await _head_and_state(connection, entry)
            _require(head is not None and state is not None, "mapped head or Family state is missing")
            if head is None or state is None:
                raise ValueError("mapped head or Family state is missing")  # noqa: TRY003, TRY301
            _require(head["revision"] >= entry.tail["version"], "mapped head is behind imported history")
            _require(
                state["state_version"] >= entry.state_version, "mapped lifecycle generation is behind legacy history"
            )
            if head["revision"] == entry.tail["version"] and state["state_version"] == entry.state_version:
                _require(state["state"] == entry.state, "initial mapped lifecycle differs")
            summary = (
                "active" if state["state"] == "active" else "retired" if state["state"] == "retired" else "deprecated"
            )
            _require(
                head["lifecycle_state"] == summary
                and head["governance_generation"] == state["state_version"]
                and head["replacement_artifact_id"] is None,
                "public governance summary disagrees with Family state",
            )
            _require((state["state"] == "merged") == (state["merged_into_id"] is not None), "invalid merge destination")
            security = await _load_security(connection, entry.scope_id, entry.artifact_id, None)
            legacy_bindings = await _rows(
                connection,
                "pc_access_relationships",
                ("binding_id",),
                "WHERE scope_id = :scope AND family = 'memory' AND artifact_id = :memory AND selector_entry_id = :entry",
                scope=entry.scope_id,
                memory=entry.memory_id,
                entry=entry.entry_id,
            )
            _require(not legacy_bindings, "legacy grants were not retargeted")
            for binding in await _mapped_grants(connection, entry):
                creation = await _grant_creation_receipt(
                    connection,
                    binding,
                    _resource_key(entry.scope_id, "memory", entry.memory_id, entry.entry_id),
                    _resource_key(entry.scope_id, _FAMILY, entry.artifact_id),
                )
                if creation is not None and creation[0]["payload_hash"] != creation[1]:
                    pending_grant_receipts += 1
                    errors.append(
                        f"{prefix}: {binding['binding_id']}: grant creation receipt uses legacy resource; rerun apply"
                    )
            if not check_projection:
                verified += len(errors) == previous_errors
                continue
            projection = (
                (
                    await connection.execute(
                        text(
                            "SELECT revision, state_version, content_hash, projection_format, kind, text, searchable_text, tag_keys, "
                            "owner_type, owner_id, read_grants, embedding, profile_fingerprint, embedding_input_hash "
                            "FROM pc_atomic_memory_current WHERE scope_id = :scope AND artifact_id = :id"
                        ),
                        {"scope": entry.scope_id, "id": entry.artifact_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if state["state"] != "active":
                _require(projection is None, "nonactive memory remains in current projection")
            else:
                _require(projection is not None, "active memory has no current projection")
                if projection is None:
                    raise ValueError("active memory has no current projection")  # noqa: TRY003, TRY301
                content_row = (
                    await _rows(
                        connection,
                        "pc_artifacts",
                        _ARTIFACT_COLUMNS,
                        "WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id AND revision = :revision",
                        scope=entry.scope_id,
                        id=entry.artifact_id,
                        revision=head["revision"],
                    )
                )[0]
                content = _decode(content_row["content"])
                _require(
                    projection["revision"] == head["revision"]
                    and projection["state_version"] == state["state_version"],
                    "projection versions differ",
                )
                _require(
                    projection["kind"] == content["kind"]
                    and projection["text"] == content["text"]
                    and projection["content_hash"] == sha256(rfc8785.dumps(_normalize(content))).hexdigest(),
                    "projection body or hash differs",
                )
                _require(projection["projection_format"] == _PROJECTION_FORMAT, "unsupported current projection format")
                _require(
                    projection["searchable_text"] == analyze_text(content["kind"] + "\n" + content["text"]),
                    "projection full-text body differs",
                )
                _require(
                    tuple(sorted(_decode(projection["tag_keys"])))
                    == await _load_tags(connection, entry.scope_id, entry.artifact_id),
                    "projection tags differ from formal tags",
                )
                _require(
                    (projection["owner_type"], projection["owner_id"]) == (security.owner_type, security.owner_id),
                    "projection Owner differs",
                )
                _require(
                    sorted(_decode(projection["read_grants"]), key=lambda item: item["binding_id"])
                    == [grant.as_json() for grant in security.read_grants],
                    "projection grants differ from current bindings",
                )
                if index is not None:
                    profile = index.capabilities.embedding_profile
                    if profile is None:
                        _require(
                            all(
                                projection[name] is None
                                for name in ("embedding", "profile_fingerprint", "embedding_input_hash")
                            ),
                            "no-vector current row retained a vector",
                        )
                    else:
                        _require(
                            projection["embedding"] is not None
                            and projection["profile_fingerprint"] == atomic_memory_profile_fingerprint(profile)
                            and projection["embedding_input_hash"]
                            == atomic_memory_embedding_input_hash(content["kind"], content["text"]),
                            "projection vector input/profile is incomplete",
                        )
            verified += len(errors) == previous_errors
        except (ValueError, KeyError, IndexError, AtomicMemoryMigrationError) as error:
            errors.append(f"{prefix}: {error}")
    return AtomicMemoryMigrationReport(
        action="verify",
        ready=not errors,
        counts={
            **inventory.counts,
            "verified_entries": verified,
            "pending_grant_receipts": pending_grant_receipts,
        },
        errors=tuple(errors),
        processing_snapshot_hash=inventory.processing_snapshot_hash,
    )


async def assert_atomic_memory_migration_ready(
    connection: AsyncConnection, *, index: AtomicMemoryIndex | None = None
) -> None:
    """Read-only startup gate, independent of the processing schema marker."""

    report = await verify_atomic_memory_migration(connection, index=index)
    if not report.ready:
        raise AtomicMemoryMigrationError(report.errors)


__all__ = [
    "MIGRATION_ID",
    "AtomicMemoryMigrationError",
    "AtomicMemoryMigrationReport",
    "apply_atomic_memory_migration",
    "assert_atomic_memory_migration_ready",
    "plan_atomic_memory_migration",
    "verify_atomic_memory_migration",
    "verify_atomic_memory_migration_authority",
]
