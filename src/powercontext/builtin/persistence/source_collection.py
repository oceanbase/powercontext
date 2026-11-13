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

"""Collection runners and the targets users assign to them.

Users write a target's configuration columns; the runner writes its status columns
together with the target's checkpoint. A runner pulls its whole target list at every
sync, so targets carry no revision: the last writer wins, and a runner has applied a
change once its ``last_seen_at`` is later than the target's ``updated_at``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import JsonValue
from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import (
    CONNECTOR_CHECKPOINTS_TABLE,
    SOURCE_COLLECT_TARGETS_TABLE,
    SOURCE_RUNNERS_TABLE,
)
from powercontext.sources.collection import TARGET_CHECKPOINT_VERSION as TARGET_CHECKPOINT_VERSION

# The Server's own runner, which collects in Supervisor Workers.
SERVER_RUNNER = "server"

_TARGETS = SOURCE_COLLECT_TARGETS_TABLE
_RUNNERS = SOURCE_RUNNERS_TABLE


class SourceCollectionError(Exception):
    """A runner or target request that core refuses, with its HTTP status and stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Runner:
    runner: str
    subject: str
    capabilities: tuple[dict[str, JsonValue], ...]
    last_seen_at: datetime | None


@dataclass(frozen=True)
class TargetStatus:
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_error_code: str | None
    last_error_message: str | None


@dataclass(frozen=True)
class Target:
    scope_id: str
    target_id: str
    runner: str
    capability: str
    target_key: str
    parameters: dict[str, JsonValue]
    enabled: bool
    updated_at: datetime
    updated_by: str
    status: TargetStatus


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def now() -> datetime:
    return datetime.now(UTC)


def _instant(value: datetime | None) -> datetime | None:
    # SQLite returns naive values; every stored instant is UTC.
    return None if value is None or value.tzinfo is not None else value.replace(tzinfo=UTC)


def _runner(row: Mapping[Any, Any]) -> Runner:
    return Runner(
        runner=row["runner"],
        subject=row["subject"],
        capabilities=tuple(json.loads(bytes(row["capabilities"]))),
        last_seen_at=_instant(row["last_seen_at"]),
    )


def _target(row: Mapping[Any, Any]) -> Target:
    return Target(
        scope_id=row["scope_id"],
        target_id=row["target_id"],
        runner=row["runner"],
        capability=row["capability"],
        target_key=row["target_key"],
        parameters=json.loads(bytes(row["parameters"])),
        enabled=bool(row["enabled"]),
        updated_at=_instant(row["updated_at"]) or now(),
        updated_by=row["updated_by"],
        status=TargetStatus(
            last_attempt_at=_instant(row["last_attempt_at"]),
            last_success_at=_instant(row["last_success_at"]),
            consecutive_failures=int(row["consecutive_failures"]),
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
        ),
    )


class SourceCollectionRepository:
    async def runner(self, connection: AsyncConnection, runner: str) -> Runner | None:
        row = (await connection.execute(select(_RUNNERS).where(_RUNNERS.c.runner == runner))).mappings().first()
        return None if row is None else _runner(row)

    async def runners(self, connection: AsyncConnection) -> tuple[Runner, ...]:
        rows = (await connection.execute(select(_RUNNERS).order_by(_RUNNERS.c.runner))).mappings().all()
        return tuple(_runner(row) for row in rows)

    async def record_sync(
        self, connection: AsyncConnection, runner: str, subject: str, capabilities: Sequence[Mapping[str, JsonValue]]
    ) -> Runner:
        """Record a sync: capabilities only when they changed, ``last_seen_at`` every time.

        A runner name is bound to the subject of its first sync; another subject may use it
        only when its own identifier is the runner name.
        """

        encoded = canonical(list(capabilities))
        digest = hashlib.sha256(encoded).hexdigest()
        existing = await self.runner(connection, runner)
        seen = now()
        if existing is None:
            try:
                await connection.execute(
                    insert(_RUNNERS).values(
                        runner=runner,
                        subject=subject,
                        capabilities=encoded,
                        capabilities_digest=digest,
                        last_seen_at=seen,
                    )
                )
            except IntegrityError:
                raise SourceCollectionError(409, "conflict", "The runner synced concurrently; retry.") from None
        else:
            values: dict[str, Any] = {"last_seen_at": seen}
            current = (
                await connection.execute(select(_RUNNERS.c.capabilities_digest).where(_RUNNERS.c.runner == runner))
            ).scalar_one()
            if current != digest:
                values.update(capabilities=encoded, capabilities_digest=digest)
            await connection.execute(update(_RUNNERS).where(_RUNNERS.c.runner == runner).values(**values))
        loaded = await self.runner(connection, runner)
        assert loaded is not None  # noqa: S101 - written above in this transaction
        return loaded

    async def delete_runner(self, connection: AsyncConnection, runner: str) -> None:
        if await self.runner(connection, runner) is None:
            raise SourceCollectionError(404, "not_found", "The runner was not found.")
        remaining = (
            await connection.execute(select(func.count()).select_from(_TARGETS).where(_TARGETS.c.runner == runner))
        ).scalar_one()
        if remaining:
            raise SourceCollectionError(409, "conflict", "Delete the runner's targets first.")
        await connection.execute(delete(_RUNNERS).where(_RUNNERS.c.runner == runner))

    async def target(self, connection: AsyncConnection, scope_id: str, target_id: str) -> Target | None:
        row = (
            (
                await connection.execute(
                    select(_TARGETS).where(_TARGETS.c.scope_id == scope_id, _TARGETS.c.target_id == target_id)
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else _target(row)

    async def targets(self, connection: AsyncConnection, scope_id: str) -> tuple[Target, ...]:
        statement = select(_TARGETS).where(_TARGETS.c.scope_id == scope_id).order_by(_TARGETS.c.target_id)
        return tuple(_target(row) for row in (await connection.execute(statement)).mappings().all())

    async def add_target(
        self,
        connection: AsyncConnection,
        *,
        scope_id: str,
        runner: str,
        capability: str,
        target_key: str,
        parameters: Mapping[str, JsonValue],
        actor: str,
    ) -> tuple[Target, bool]:
        """Add a target, or return the existing one with the same key; True when it was created."""

        same_key = and_(
            _TARGETS.c.scope_id == scope_id,
            _TARGETS.c.runner == runner,
            _TARGETS.c.capability == capability,
            _TARGETS.c.target_key == target_key,
        )
        row = (await connection.execute(select(_TARGETS).where(same_key))).mappings().first()
        if row is not None:
            existing = _target(row)
            if canonical(existing.parameters) != canonical(dict(parameters)):
                raise SourceCollectionError(
                    409, "target_conflict", "A target with the same identity has other parameters."
                )
            return existing, False
        target_id = uuid.uuid4().hex
        try:
            await connection.execute(
                insert(_TARGETS).values(
                    scope_id=scope_id,
                    target_id=target_id,
                    runner=runner,
                    capability=capability,
                    target_key=target_key,
                    parameters=canonical(dict(parameters)),
                    enabled=True,
                    updated_at=now(),
                    updated_by=actor,
                    consecutive_failures=0,
                )
            )
        except IntegrityError:
            raise SourceCollectionError(409, "conflict", "The target was added concurrently; retry.") from None
        created = await self.target(connection, scope_id, target_id)
        assert created is not None  # noqa: S101 - written above in this transaction
        return created, True

    async def update_target(
        self,
        connection: AsyncConnection,
        target: Target,
        *,
        parameters: Mapping[str, JsonValue] | None,
        enabled: bool | None,
        actor: str,
    ) -> Target:
        values: dict[str, Any] = {"updated_at": now(), "updated_by": actor}
        if parameters is not None:
            values["parameters"] = canonical(dict(parameters))
        if enabled is not None:
            values["enabled"] = enabled
        await connection.execute(
            update(_TARGETS)
            .where(_TARGETS.c.scope_id == target.scope_id, _TARGETS.c.target_id == target.target_id)
            .values(**values)
        )
        updated = await self.target(connection, target.scope_id, target.target_id)
        assert updated is not None  # noqa: S101 - updated above in this transaction
        return updated

    async def delete_target(self, connection: AsyncConnection, target: Target) -> None:
        """Delete the target and its checkpoint; the Sources it collected stay."""

        await connection.execute(
            delete(_TARGETS).where(_TARGETS.c.scope_id == target.scope_id, _TARGETS.c.target_id == target.target_id)
        )
        await connection.execute(
            delete(CONNECTOR_CHECKPOINTS_TABLE).where(
                CONNECTOR_CHECKPOINTS_TABLE.c.scope_id == target.scope_id,
                CONNECTOR_CHECKPOINTS_TABLE.c.binding_id == target.target_id,
            )
        )

    async def assigned(
        self, connection: AsyncConnection, runner: str, capabilities: Sequence[str]
    ) -> tuple[Target, ...]:
        """The runner's enabled targets of the given capabilities, across all Scopes."""

        if not capabilities:
            return ()
        statement = (
            select(_TARGETS)
            .where(_TARGETS.c.runner == runner, _TARGETS.c.enabled.is_(True), _TARGETS.c.capability.in_(capabilities))
            .order_by(_TARGETS.c.scope_id, _TARGETS.c.target_id)
        )
        return tuple(_target(row) for row in (await connection.execute(statement)).mappings().all())

    async def due(
        self, connection: AsyncConnection, scope_id: str, runner: str, capability: str, limit: int
    ) -> tuple[Target, ...]:
        """Up to ``limit`` enabled targets of one Scope, least recently attempted first, so many targets rotate."""

        statement = (
            select(_TARGETS)
            .where(
                _TARGETS.c.scope_id == scope_id,
                _TARGETS.c.runner == runner,
                _TARGETS.c.capability == capability,
                _TARGETS.c.enabled.is_(True),
            )
            .order_by(_TARGETS.c.last_attempt_at.is_not(None), _TARGETS.c.last_attempt_at, _TARGETS.c.target_id)
            .limit(limit)
        )
        return tuple(_target(row) for row in (await connection.execute(statement)).mappings().all())

    async def scopes_with_targets(self, connection: AsyncConnection, runner: str, capability: str) -> tuple[str, ...]:
        statement = (
            select(_TARGETS.c.scope_id)
            .where(_TARGETS.c.runner == runner, _TARGETS.c.capability == capability, _TARGETS.c.enabled.is_(True))
            .distinct()
            .order_by(_TARGETS.c.scope_id)
        )
        return tuple((await connection.execute(statement)).scalars().all())

    async def record_status(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target_id: str,
        runner: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Record one collection attempt of a target of this runner; False when no such target exists."""

        attempted = now()
        matches = and_(_TARGETS.c.scope_id == scope_id, _TARGETS.c.target_id == target_id, _TARGETS.c.runner == runner)
        if error_code is None:
            values: dict[str, Any] = {
                "last_attempt_at": attempted,
                "last_success_at": attempted,
                "consecutive_failures": 0,
                "last_error_code": None,
                "last_error_message": None,
            }
        else:
            values = {
                "last_attempt_at": attempted,
                "consecutive_failures": _TARGETS.c.consecutive_failures + 1,
                "last_error_code": error_code[:128],
                "last_error_message": None if error_message is None else error_message[:1024],
            }
        result = await connection.execute(update(_TARGETS).where(matches).values(**values))
        return result.rowcount == 1
