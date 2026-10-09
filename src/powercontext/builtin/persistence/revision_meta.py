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

"""Immutable revision metadata stored beside artifact content."""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateColumn

from powercontext.artifacts import Artifact
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from powercontext.limits import MAX_ARTIFACT_REVISION_ACTOR_LENGTH

_ROLLBACK_STAMP: ContextVar[PendingRollback | None] = ContextVar("powercontext_rollback_stamp", default=None)


@dataclass(frozen=True, slots=True)
class RevisionMeta:
    """Read-back metadata for one stored revision. Missing history stays empty."""

    created_at: datetime | None = None
    created_by_type: str | None = None
    created_by_id: str | None = None
    restored_from_revision: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PendingRollback:
    """Rollback identity applied only to the matching inserted revision."""

    family: str
    artifact_id: str
    revision: int
    restored_from_revision: int
    reason: str
    created_by_type: str | None
    created_by_id: str | None


def bind_rollback(stamp: PendingRollback) -> Token[PendingRollback | None]:
    return _ROLLBACK_STAMP.set(stamp)


def reset_rollback(token: Token[PendingRollback | None]) -> None:
    _ROLLBACK_STAMP.reset(token)


def revision_meta(artifact: Artifact[Any]) -> RevisionMeta:
    meta = getattr(artifact, "_revision_meta", None)
    return meta if isinstance(meta, RevisionMeta) else RevisionMeta()


def attach_revision_meta(artifact: Artifact[Any], meta: RevisionMeta) -> None:
    object.__setattr__(artifact, "_revision_meta", meta)


def rollback_columns(family: str, artifact_id: str, revision: int) -> dict[str, Any]:
    """Return rollback columns when this insert is the bound rollback revision."""

    stamp = _ROLLBACK_STAMP.get()
    if stamp is None or stamp.family != family or stamp.artifact_id != artifact_id or stamp.revision != revision:
        return {}
    created_by = None
    if stamp.created_by_type is not None and stamp.created_by_id is not None:
        created_by = json.dumps(
            {"type": stamp.created_by_type, "id": stamp.created_by_id},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return {
        "created_by": created_by,
        "restored_from_revision": stamp.restored_from_revision,
        "rollback_reason": stamp.reason,
    }


def meta_from_row(row: dict[str, Any] | Any) -> RevisionMeta:
    created_by_type = None
    created_by_id = None
    raw_actor = row.get("created_by") if hasattr(row, "get") else None
    if isinstance(raw_actor, str) and raw_actor:
        try:
            actor = json.loads(raw_actor)
        except json.JSONDecodeError:
            actor = None
        if isinstance(actor, dict) and actor.get("type") in {"user", "service"} and isinstance(actor.get("id"), str):
            created_by_type = actor["type"]
            created_by_id = actor["id"]
    restored = row.get("restored_from_revision") if hasattr(row, "get") else None
    reason = row.get("rollback_reason") if hasattr(row, "get") else None
    return RevisionMeta(
        created_at=_utc(row.get("created_at") if hasattr(row, "get") else None),
        created_by_type=created_by_type,
        created_by_id=created_by_id,
        restored_from_revision=int(restored) if isinstance(restored, int) else None,
        reason=reason if isinstance(reason, str) else None,
    )


def _utc(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def ensure_revision_metadata_schema(connection: AsyncConnection, /) -> None:
    """Add revision metadata columns to databases created before this contract."""

    for name in ("created_at", "created_by", "restored_from_revision", "rollback_reason"):
        if await _has_column(connection, name):
            continue
        declaration = str(CreateColumn(ARTIFACTS_TABLE.c[name]).compile(dialect=connection.dialect))
        try:
            await connection.exec_driver_sql(f"ALTER TABLE {ARTIFACTS_TABLE.name} ADD COLUMN {declaration}")
        except DBAPIError:
            if not await _has_column(connection, name):
                raise
    await _widen_created_by(connection)


async def _widen_created_by(connection: AsyncConnection) -> None:
    """Grow an older created_by column so a maximum-length principal id can be stored."""

    if connection.dialect.name == "sqlite":
        return
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns(ARTIFACTS_TABLE.name))
    column = next((item for item in columns if item["name"] == "created_by"), None)
    length = None if column is None else getattr(column["type"], "length", None)
    if not isinstance(length, int) or length >= MAX_ARTIFACT_REVISION_ACTOR_LENGTH:
        return
    declaration = str(CreateColumn(ARTIFACTS_TABLE.c.created_by).compile(dialect=connection.dialect))
    await connection.exec_driver_sql(f"ALTER TABLE {ARTIFACTS_TABLE.name} MODIFY COLUMN {declaration}")


async def _has_column(connection: AsyncConnection, column_name: str) -> bool:
    columns = await connection.run_sync(lambda sync: inspect(sync).get_columns(ARTIFACTS_TABLE.name))
    return any(column["name"] == column_name for column in columns)
