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

A runner registers under a name the Server decides and that stays bound to the
registering principal. Users write a target's configuration columns; the runner writes
its status columns together with the target's checkpoint. Every change a runner must not
collect across increases the target's configuration version, and a commit is accepted
only for the version the runner collected under.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from collections.abc import Iterator, Mapping, Sequence
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
class RunnerPrincipal:
    """The authenticated identity a runner is registered under; type and identifier are compared together."""

    type: str
    id: str


# The identity of every caller while access control is disabled.
LOCAL_PRINCIPAL = RunnerPrincipal("local", "local")


@dataclass(frozen=True)
class Runner:
    runner: str
    display_name: str
    principal: RunnerPrincipal
    registration_id: str | None
    registered_at: datetime
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
    config_version: int
    updated_at: datetime
    updated_by: str
    status: TargetStatus


_NAME_LENGTH = 128
_UNUSABLE = re.compile(r"[^A-Za-z0-9._:@-]+")


def _usable_name(suggested: str | None) -> str:
    """A runner name made from a suggestion: unsupported characters become hyphens."""

    name = _UNUSABLE.sub("-", (suggested or "").strip()).strip("-._:@")
    return name[: _NAME_LENGTH - 16] or "runner"


def _name_candidates(base: str) -> Iterator[str]:
    yield base
    for number in range(2, 10):
        yield f"{base}-{number}"
    for _ in range(8):
        yield f"{base}-{secrets.token_hex(4)}"


def _unchanged(target: Target) -> Any:
    """The target row while it is still what a management request read and was authorized for."""

    return and_(
        _TARGETS.c.scope_id == target.scope_id,
        _TARGETS.c.target_id == target.target_id,
        _TARGETS.c.runner == target.runner,
        _TARGETS.c.config_version == target.config_version,
    )


def _changed_meanwhile() -> SourceCollectionError:
    return SourceCollectionError(409, "conflict", "The target was changed concurrently; read it again and retry.")


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
        display_name=row["display_name"],
        principal=RunnerPrincipal(row["principal_type"], row["principal_id"]),
        registration_id=row["registration_id"],
        registered_at=_instant(row["registered_at"]) or now(),
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
        config_version=int(row["config_version"]),
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

    async def register_suggested(
        self, connection: AsyncConnection, principal: RunnerPrincipal, registration_id: str, suggested_name: str | None
    ) -> tuple[Runner, bool]:
        """Register a runner whose name the Server decides; True when it was created.

        The registration ID makes the request repeatable for its principal. A suggested name
        is used only when no runner has it, so a suggestion never takes over an existing name.
        """

        row = (
            (
                await connection.execute(
                    select(_RUNNERS).where(
                        _RUNNERS.c.principal_type == principal.type,
                        _RUNNERS.c.principal_id == principal.id,
                        _RUNNERS.c.registration_id == registration_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is not None:
            return _runner(row), False
        base = _usable_name(suggested_name)
        display = (suggested_name or "").strip() or base
        for candidate in _name_candidates(base):
            if candidate == SERVER_RUNNER or await self.runner(connection, candidate) is not None:
                continue
            return await self._insert_runner(connection, candidate, display, principal, registration_id), True
        raise SourceCollectionError(409, "conflict", "No free runner name was found; retry.")

    async def register_fixed(
        self, connection: AsyncConnection, principal: RunnerPrincipal, name: str
    ) -> tuple[Runner, bool]:
        """Register a runner under exactly ``name``, or return the principal's own runner of that name."""

        if name == SERVER_RUNNER:
            raise SourceCollectionError(
                403, "access_denied", "The name server is reserved for the Server's own runner."
            )
        existing = await self.runner(connection, name)
        if existing is None:
            return await self._insert_runner(connection, name, name, principal, None), True
        if existing.principal != principal:
            raise SourceCollectionError(409, "runner_name_taken", "Another principal registered this runner name.")
        return existing, False

    async def _insert_runner(
        self,
        connection: AsyncConnection,
        name: str,
        display_name: str,
        principal: RunnerPrincipal,
        registration_id: str | None,
    ) -> Runner:
        encoded = canonical([])
        try:
            await connection.execute(
                insert(_RUNNERS).values(
                    runner=name,
                    display_name=display_name[:256],
                    principal_type=principal.type,
                    principal_id=principal.id,
                    registration_id=registration_id,
                    registered_at=now(),
                    capabilities=encoded,
                    capabilities_digest=hashlib.sha256(encoded).hexdigest(),
                    last_seen_at=None,
                )
            )
        except IntegrityError:
            raise SourceCollectionError(409, "conflict", "The runner was registered concurrently; retry.") from None
        created = await self.runner(connection, name)
        assert created is not None  # noqa: S101 - written above in this transaction
        return created

    async def record_sync(
        self, connection: AsyncConnection, runner: str, capabilities: Sequence[Mapping[str, JsonValue]]
    ) -> None:
        """Record a registered runner's sync: capabilities only when they changed, ``last_seen_at`` every time."""

        encoded = canonical(list(capabilities))
        digest = hashlib.sha256(encoded).hexdigest()
        values: dict[str, Any] = {"last_seen_at": now()}
        current = (
            await connection.execute(select(_RUNNERS.c.capabilities_digest).where(_RUNNERS.c.runner == runner))
        ).scalar_one()
        if current != digest:
            values.update(capabilities=encoded, capabilities_digest=digest)
        await connection.execute(update(_RUNNERS).where(_RUNNERS.c.runner == runner).values(**values))

    async def set_display_name(self, connection: AsyncConnection, runner: str, display_name: str) -> Runner:
        result = await connection.execute(
            update(_RUNNERS).where(_RUNNERS.c.runner == runner).values(display_name=display_name)
        )
        if result.rowcount != 1:
            raise SourceCollectionError(404, "not_found", "The runner was not found.")
        updated = await self.runner(connection, runner)
        assert updated is not None  # noqa: S101 - updated above in this transaction
        return updated

    async def delete_runner(self, connection: AsyncConnection, runner: Runner) -> None:
        """Delete the record the caller read and was authorized for, once it has no targets."""

        await connection.execute(select(_RUNNERS.c.runner).where(_RUNNERS.c.runner == runner.runner).with_for_update())
        remaining = (
            await connection.execute(
                select(func.count()).select_from(_TARGETS).where(_TARGETS.c.runner == runner.runner)
            )
        ).scalar_one()
        if remaining:
            raise SourceCollectionError(409, "conflict", "Transfer or delete the runner's targets first.")
        result = await connection.execute(
            delete(_RUNNERS).where(
                _RUNNERS.c.runner == runner.runner,
                _RUNNERS.c.principal_type == runner.principal.type,
                _RUNNERS.c.principal_id == runner.principal.id,
            )
        )
        if result.rowcount != 1:
            raise SourceCollectionError(
                409, "conflict", "The runner was changed concurrently; read it again and retry."
            )

    async def require_registered(self, connection: AsyncConnection, runner: str) -> None:
        """Targets go only to runners that exist; the Server's own runner always does."""

        if runner == SERVER_RUNNER:
            return
        # Locked where the backend supports it, so the runner is not deleted while a target is assigned to it.
        found = await connection.execute(select(_RUNNERS.c.runner).where(_RUNNERS.c.runner == runner).with_for_update())
        if found.first() is None:
            raise SourceCollectionError(422, "runner_not_registered", "The runner is not registered.")

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

        await self.require_registered(connection, runner)
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
                    config_version=1,
                    updated_at=now(),
                    updated_by=actor,
                    consecutive_failures=0,
                    has_more=False,
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
        if (parameters is not None and canonical(dict(parameters)) != canonical(target.parameters)) or (
            enabled is not None and enabled != target.enabled
        ):
            # Results collected under the previous configuration are refused when they commit.
            values["config_version"] = _TARGETS.c.config_version + 1
        # ``target`` is what the caller compared with and was authorized for; it must still be the row.
        result = await connection.execute(update(_TARGETS).where(_unchanged(target)).values(**values))
        if result.rowcount != 1:
            raise _changed_meanwhile()
        updated = await self.target(connection, target.scope_id, target.target_id)
        assert updated is not None  # noqa: S101 - updated above in this transaction
        return updated

    async def transfer_target(
        self, connection: AsyncConnection, target: Target, runner: str, *, reset_checkpoint: bool, actor: str
    ) -> Target:
        """Assign the target to another registered runner; it keeps its ID and, unless reset, its checkpoint."""

        await self.require_registered(connection, runner)
        if runner != target.runner:
            taken = (
                await connection.execute(
                    select(_TARGETS.c.target_id).where(
                        _TARGETS.c.scope_id == target.scope_id,
                        _TARGETS.c.runner == runner,
                        _TARGETS.c.capability == target.capability,
                        _TARGETS.c.target_key == target.target_key,
                    )
                )
            ).first()
            if taken is not None:
                raise SourceCollectionError(
                    409, "target_conflict", "The destination runner already has a target with the same identity."
                )
        result = await connection.execute(
            update(_TARGETS)
            .where(_unchanged(target))
            .values(
                runner=runner,
                config_version=_TARGETS.c.config_version + 1,
                updated_at=now(),
                updated_by=actor,
            )
        )
        if result.rowcount != 1:
            raise _changed_meanwhile()
        if reset_checkpoint:
            await self._delete_checkpoint(connection, target)
        transferred = await self.target(connection, target.scope_id, target.target_id)
        assert transferred is not None  # noqa: S101 - updated above in this transaction
        return transferred

    async def committable(
        self, connection: AsyncConnection, scope_id: str, target_id: str, *, runner: str, config_version: int | None
    ) -> Target:
        """The target a runner may commit a checkpoint and status for, checked inside the commit's transaction.

        The row is locked where the backend supports it, so a concurrent change, transfer, or
        deletion either precedes this check or waits for the commit.
        """

        row = (
            (
                await connection.execute(
                    select(_TARGETS)
                    .where(_TARGETS.c.scope_id == scope_id, _TARGETS.c.target_id == target_id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise SourceCollectionError(409, "target_changed", "The collection target no longer exists.")
        target = _target(row)
        if target.runner != runner:
            raise SourceCollectionError(409, "target_changed", "The collection target belongs to another runner.")
        if not target.enabled:
            raise SourceCollectionError(409, "target_changed", "The collection target is disabled.")
        if config_version != target.config_version:
            raise SourceCollectionError(
                409, "target_changed", "The collection target changed after it was collected; sync and collect again."
            )
        return target

    async def committable_by(self, connection: AsyncConnection, binding: Any, commit: Any) -> Target:
        """The collection target a checkpoint commit names, when the caller is its runner with its current version.

        ``commit`` describes the caller; it is None when the request carried nothing a runner sends.
        Targets of the Server's own runner are committed only by the Server's collector.
        """

        refused = SourceCollectionError(
            403, "access_denied", "Only the runner a collection target belongs to commits its checkpoint."
        )
        current = await self.target(connection, binding.scope_id, binding.binding_id)
        if current is None:
            raise SourceCollectionError(409, "target_changed", "The collection target no longer exists.")
        if commit is None or current.runner == SERVER_RUNNER:
            raise refused
        owner = await self.runner(connection, current.runner)
        if owner is None or owner.principal != RunnerPrincipal(commit.principal_type, commit.principal_id):
            raise refused
        if binding.connector_name != current.capability or binding.connector_version != TARGET_CHECKPOINT_VERSION:
            raise SourceCollectionError(
                422, "invalid_request", "The binding must name the target's plugin and the target checkpoint version."
            )
        if commit.plugin != current.capability:
            # The accepted version must be the target's own plugin's, not that of another loaded plugin.
            raise SourceCollectionError(
                422, "invalid_request", "A commit for a collection target names the version of the target's plugin."
            )
        return await self.committable(
            connection,
            binding.scope_id,
            binding.binding_id,
            runner=current.runner,
            config_version=commit.config_version,
        )

    async def is_target(self, connection: AsyncConnection, scope_id: str, target_id: str) -> bool:
        return (
            await connection.execute(
                select(_TARGETS.c.target_id).where(_TARGETS.c.scope_id == scope_id, _TARGETS.c.target_id == target_id)
            )
        ).first() is not None

    async def _delete_checkpoint(self, connection: AsyncConnection, target: Target) -> None:
        await connection.execute(
            delete(CONNECTOR_CHECKPOINTS_TABLE).where(
                CONNECTOR_CHECKPOINTS_TABLE.c.scope_id == target.scope_id,
                CONNECTOR_CHECKPOINTS_TABLE.c.binding_id == target.target_id,
            )
        )

    async def delete_target(self, connection: AsyncConnection, target: Target) -> None:
        """Delete the target and its checkpoint; the Sources it collected stay."""

        result = await connection.execute(delete(_TARGETS).where(_unchanged(target)))
        if result.rowcount != 1:
            raise _changed_meanwhile()
        await self._delete_checkpoint(connection, target)

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
        """Up to ``limit`` enabled targets of one Scope, so many targets rotate.

        Targets whose last attempt left more to collect go first, then the least recently attempted.
        """

        statement = (
            select(_TARGETS)
            .where(
                _TARGETS.c.scope_id == scope_id,
                _TARGETS.c.runner == runner,
                _TARGETS.c.capability == capability,
                _TARGETS.c.enabled.is_(True),
            )
            .order_by(
                _TARGETS.c.has_more.desc(),
                _TARGETS.c.last_attempt_at.is_not(None),
                _TARGETS.c.last_attempt_at,
                _TARGETS.c.target_id,
            )
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
        has_more: bool = False,
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
                "has_more": has_more,
            }
        else:
            values = {
                "last_attempt_at": attempted,
                "consecutive_failures": _TARGETS.c.consecutive_failures + 1,
                "last_error_code": error_code[:128],
                "last_error_message": None if error_message is None else error_message[:1024],
                "has_more": False,
            }
        result = await connection.execute(update(_TARGETS).where(matches).values(**values))
        return result.rowcount == 1
