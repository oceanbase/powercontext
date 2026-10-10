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

"""Frozen offline archive of legacy Memory collections.

Each collection is archived in one transaction before any public row is
rewritten. The head revision row also stores the collection head, Owners,
tags, grants and grant receipts exactly as they were at archive time; later
import, retargeting and removal read this snapshot instead of mutable live
rows. The archive has no online read path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table, inspect, text
from sqlalchemy.dialects.mysql import LONGBLOB, VARCHAR
from sqlalchemy.ext.asyncio import AsyncConnection

ARCHIVE_TABLE_NAME = "pc_memory_artifact_archive"
ARCHIVE_FORMAT = "powercontext.memory-artifact-archive.v1"

ARCHIVE_METADATA = MetaData()


def frozen_identity(length: int) -> Any:
    return String(length).with_variant(VARCHAR(length, collation="utf8mb4_bin"), "mysql")


def _blob() -> Any:
    return LargeBinary().with_variant(LONGBLOB(), "mysql")


ARCHIVE_TABLE = Table(
    ARCHIVE_TABLE_NAME,
    ARCHIVE_METADATA,
    Column("scope_id", frozen_identity(256), primary_key=True),
    Column("family", frozen_identity(128), primary_key=True),
    Column("artifact_id", frozen_identity(128), primary_key=True),
    Column("revision", Integer, primary_key=True, autoincrement=False),
    Column("content", _blob(), nullable=False),
    Column("memory_citations", _blob()),
    Column("metadata", _blob(), nullable=False),
)

LEGACY_CITATION_COLUMN = "memory_citations"
ARTIFACT_COLUMNS = ("scope_id", "family", "artifact_id", "revision", "content", LEGACY_CITATION_COLUMN)
HEAD_COLUMNS = (
    "scope_id",
    "family",
    "artifact_id",
    "revision",
    "searchable_text",
    "lifecycle_state",
    "replacement_artifact_id",
    "governance_generation",
)
OWNER_COLUMNS = (
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
TAG_COLUMNS = (
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
BINDING_COLUMNS = (
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
IDEMPOTENCY_COLUMNS = (
    "actor_id",
    "idempotency_key_hash",
    "operation",
    "payload_hash",
    "result_binding_id",
    "secondary_binding_id",
)
_LINEAGE_SOURCE_COLUMNS = ("ordinal", "source_type", "source_id")
_LINEAGE_ARTIFACT_COLUMNS = ("ordinal", "upstream_family", "upstream_artifact_id", "upstream_revision")


@dataclass(frozen=True)
class LegacyCollection:
    """One legacy Memory collection read from the archive or, before archiving, from live rows."""

    scope_id: str
    memory_id: str
    revisions: tuple[dict[str, Any], ...]
    head: dict[str, Any] | None
    owners: tuple[dict[str, Any], ...]
    tags: tuple[dict[str, Any], ...]
    bindings: tuple[dict[str, Any], ...]
    archived: bool

    def owners_for(self, entry_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            owner
            for owner in self.owners
            if owner["selector_type"] == "memory_entry" and owner["selector_entry_id"] == entry_id
        )

    def tags_for(self, entry_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(tag for tag in self.tags if tag["target_type"] == "memory_entry" and tag["target_id"] == entry_id)

    def bindings_for(self, entry_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            binding
            for binding in self.bindings
            if binding["selector_type"] == "memory_entry" and binding["selector_entry_id"] == entry_id
        )


async def table_names(connection: AsyncConnection) -> set[str]:
    return set(await connection.run_sync(lambda value: inspect(value).get_table_names()))


async def has_column(connection: AsyncConnection, table: str, column: str) -> bool:
    columns = await connection.run_sync(lambda value: inspect(value).get_columns(table))
    return any(item["name"] == column for item in columns)


async def live_artifact_columns(connection: AsyncConnection) -> tuple[str, ...]:
    """Public Artifact columns; completion drops the legacy citation column, which then reads as absent."""

    if await has_column(connection, "pc_artifacts", LEGACY_CITATION_COLUMN):
        return ARTIFACT_COLUMNS
    return tuple(name for name in ARTIFACT_COLUMNS if name != LEGACY_CITATION_COLUMN)


async def rows(
    connection: AsyncConnection, table: str, columns: tuple[str, ...], where: str = "", **params: Any
) -> list[dict[str, Any]]:
    # Every table, column and predicate is a constant owned by this versioned resource.
    result = await connection.execute(text(f"SELECT {', '.join(columns)} FROM {table} {where}"), params)  # noqa: S608
    return [dict(row) for row in result.mappings()]


def _encode_value(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$bytes"}:
        return bytes.fromhex(value["$bytes"])
    if isinstance(value, dict) and set(value) == {"$datetime"}:
        return datetime.fromisoformat(value["$datetime"])
    return value


def encode_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _encode_value(value) for key, value in row.items()}


def decode_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _decode_value(value) for key, value in row.items()}


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_metadata(value: Any) -> dict[str, Any]:
    payload = json.loads(bytes(value))
    if not isinstance(payload, dict) or payload.get("format") != ARCHIVE_FORMAT:
        raise ValueError("unsupported Memory archive metadata format")  # noqa: TRY003
    return payload


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


async def ensure_archive_table(connection: AsyncConnection) -> None:
    await connection.run_sync(lambda value: ARCHIVE_METADATA.create_all(value, tables=[ARCHIVE_TABLE], checkfirst=True))


async def _live_snapshot(connection: AsyncConnection, scope_id: str, memory_id: str) -> dict[str, Any]:
    identity = {"scope": scope_id, "memory": memory_id}
    owned = "WHERE scope_id = :scope AND family = 'memory' AND artifact_id = :memory"
    heads = await rows(connection, "pc_artifact_heads", HEAD_COLUMNS, owned, **identity)
    owners = await rows(
        connection,
        "pc_access_owners",
        OWNER_COLUMNS,
        "WHERE owner_kind = 'artifact' AND scope_id = :scope AND family = 'memory' AND artifact_id = :memory "
        "ORDER BY object_key_hash",
        **identity,
    )
    tags = await rows(
        connection, "pc_artifact_tags", TAG_COLUMNS, owned + " ORDER BY target_type, target_id, tag_key", **identity
    )
    bindings = await rows(
        connection,
        "pc_access_relationships",
        BINDING_COLUMNS,
        "WHERE resource_type = 'artifact' AND scope_id = :scope AND family = 'memory' AND artifact_id = :memory "
        "ORDER BY binding_id",
        **identity,
    )
    receipts: list[dict[str, Any]] = []
    for binding in bindings:
        if binding["idempotency_key"] is None:
            continue
        receipts.extend(
            await rows(
                connection,
                "pc_access_idempotency",
                IDEMPOTENCY_COLUMNS,
                "WHERE actor_id = :actor AND idempotency_key_hash = :key",
                actor=binding["granted_by_id"],
                key=_digest(binding["idempotency_key"]),
            )
        )
    return {
        "head": None if not heads else heads[0],
        "owners": owners,
        "tags": tags,
        "bindings": bindings,
        "idempotency": receipts,
    }


async def _lineage(connection: AsyncConnection, row: dict[str, Any]) -> dict[str, Any]:
    where = (
        "WHERE scope_id = :scope AND family = :family AND artifact_id = :id AND revision = :revision ORDER BY ordinal"
    )
    identity = {
        "scope": row["scope_id"],
        "family": row["family"],
        "id": row["artifact_id"],
        "revision": row["revision"],
    }
    return {
        "sources": await rows(connection, "pc_artifact_lineage_sources", _LINEAGE_SOURCE_COLUMNS, where, **identity),
        "artifacts": await rows(
            connection, "pc_artifact_lineage_artifacts", _LINEAGE_ARTIFACT_COLUMNS, where, **identity
        ),
    }


async def archive_collection(connection: AsyncConnection, scope_id: str, memory_id: str) -> bool:
    """Archive one complete live collection; return whether this call wrote it.

    An existing archive is reused only when its revision bodies equal the live
    rows. A collection that changed after archiving is a conflict, never an
    overwrite of the original snapshot.
    """

    live = await rows(
        connection,
        "pc_artifacts",
        await live_artifact_columns(connection),
        "WHERE scope_id = :scope AND family = 'memory' AND artifact_id = :memory ORDER BY revision",
        scope=scope_id,
        memory=memory_id,
    )
    if not live:
        return False
    archived = await rows(
        connection,
        ARCHIVE_TABLE_NAME,
        ("revision", "content", "memory_citations"),
        "WHERE scope_id = :scope AND family = 'memory' AND artifact_id = :memory ORDER BY revision",
        scope=scope_id,
        memory=memory_id,
    )
    if archived:
        same = len(archived) == len(live) and all(
            old["revision"] == new["revision"]
            and bytes(old["content"]) == bytes(new["content"])
            and (None if old["memory_citations"] is None else bytes(old["memory_citations"]))
            == (None if new.get("memory_citations") is None else bytes(new["memory_citations"]))
            for old, new in zip(archived, live, strict=False)
        )
        if not same:
            raise ValueError(f"{scope_id}/{memory_id}: legacy collection differs from its archive")  # noqa: TRY003
        return False
    snapshot = await _live_snapshot(connection, scope_id, memory_id)
    head_revision = live[-1]["revision"] if snapshot["head"] is None else snapshot["head"]["revision"]
    for row in live:
        metadata: dict[str, Any] = {
            "format": ARCHIVE_FORMAT,
            "lineage": await _lineage(connection, row),
            "incoming_references": [],
        }
        if row["revision"] == head_revision:
            metadata["collection"] = {
                "head": None if snapshot["head"] is None else encode_row(snapshot["head"]),
                "owners": [encode_row(item) for item in snapshot["owners"]],
                "tags": [encode_row(item) for item in snapshot["tags"]],
                "bindings": [encode_row(item) for item in snapshot["bindings"]],
                "idempotency": [encode_row(item) for item in snapshot["idempotency"]],
            }
        await connection.execute(
            ARCHIVE_TABLE.insert().values(
                scope_id=row["scope_id"],
                family=row["family"],
                artifact_id=row["artifact_id"],
                revision=row["revision"],
                content=bytes(row["content"]),
                memory_citations=None if row.get("memory_citations") is None else bytes(row["memory_citations"]),
                metadata=_dump(metadata),
            )
        )
    return True


async def load_collections(connection: AsyncConnection, tables: set[str]) -> dict[tuple[str, str], LegacyCollection]:
    """Read every legacy collection, preferring its frozen archive snapshot."""

    result: dict[tuple[str, str], LegacyCollection] = {}
    if ARCHIVE_TABLE_NAME in tables:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in await rows(
            connection,
            ARCHIVE_TABLE_NAME,
            (*ARTIFACT_COLUMNS, "metadata"),
            "WHERE family = 'memory' ORDER BY scope_id, artifact_id, revision",
        ):
            grouped.setdefault((row["scope_id"], row["artifact_id"]), []).append(row)
        for key, revisions in grouped.items():
            collection: dict[str, Any] | None = None
            for row in revisions:
                metadata = load_metadata(row.pop("metadata"))
                collection = metadata.get("collection", collection)
            if collection is None:
                raise ValueError(f"{key[0]}/{key[1]}: archived collection has no head snapshot")  # noqa: TRY003
            result[key] = LegacyCollection(
                scope_id=key[0],
                memory_id=key[1],
                revisions=tuple(revisions),
                head=None if collection["head"] is None else decode_row(collection["head"]),
                owners=tuple(decode_row(item) for item in collection["owners"]),
                tags=tuple(decode_row(item) for item in collection["tags"]),
                bindings=tuple(decode_row(item) for item in collection["bindings"]),
                archived=True,
            )
    if "pc_artifacts" not in tables:
        return result
    live: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in await rows(
        connection,
        "pc_artifacts",
        await live_artifact_columns(connection),
        "WHERE family = 'memory' ORDER BY scope_id, artifact_id, revision",
    ):
        live.setdefault((row["scope_id"], row["artifact_id"]), []).append(row)
    for key, revisions in live.items():
        if key in result:
            continue
        snapshot = await _live_snapshot(connection, *key)
        result[key] = LegacyCollection(
            scope_id=key[0],
            memory_id=key[1],
            revisions=tuple(revisions),
            head=snapshot["head"],
            owners=tuple(snapshot["owners"]),
            tags=tuple(snapshot["tags"]),
            bindings=tuple(snapshot["bindings"]),
            archived=False,
        )
    return result


async def archive_incoming_reference(
    connection: AsyncConnection,
    scope_id: str,
    target: dict[str, Any],
    reference: dict[str, Any],
) -> None:
    """Record one online relationship to a whole collection before it is removed."""

    current = await connection.execute(
        text(
            f"SELECT metadata FROM {ARCHIVE_TABLE_NAME} WHERE scope_id = :scope AND family = 'memory' "  # noqa: S608
            "AND artifact_id = :id AND revision = :revision"
        ),
        {"scope": scope_id, "id": target["artifact_id"], "revision": target["revision"]},
    )
    value = current.scalar_one_or_none()
    if value is None:
        raise ValueError(  # noqa: TRY003
            f"{scope_id}/{target['artifact_id']}@{target['revision']}: referenced collection revision is not archived"
        )
    metadata = load_metadata(value)
    metadata["incoming_references"].append(reference)
    await connection.execute(
        text(
            f"UPDATE {ARCHIVE_TABLE_NAME} SET metadata = :metadata WHERE scope_id = :scope "  # noqa: S608
            "AND family = 'memory' AND artifact_id = :id AND revision = :revision"
        ),
        {"metadata": _dump(metadata), "scope": scope_id, "id": target["artifact_id"], "revision": target["revision"]},
    )


__all__ = [
    "ARCHIVE_FORMAT",
    "ARCHIVE_TABLE",
    "ARCHIVE_TABLE_NAME",
    "ARTIFACT_COLUMNS",
    "BINDING_COLUMNS",
    "HEAD_COLUMNS",
    "IDEMPOTENCY_COLUMNS",
    "LEGACY_CITATION_COLUMN",
    "OWNER_COLUMNS",
    "TAG_COLUMNS",
    "LegacyCollection",
    "archive_collection",
    "archive_incoming_reference",
    "decode_row",
    "encode_row",
    "ensure_archive_table",
    "frozen_identity",
    "has_column",
    "live_artifact_columns",
    "load_collections",
    "load_metadata",
    "rows",
    "table_names",
]
