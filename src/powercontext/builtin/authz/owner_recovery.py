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

"""Persist trusted ownership intents alongside domain commits, then replay externally."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Boolean, Column, MetaData, Table, Text, UniqueConstraint, insert, select, tuple_, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.authz.errors import (
    AccessConflictError,
    AccessInvalidRequestError,
    AccessUnavailableError,
)
from powercontext.builtin.authz.models import (
    AccessAction,
    AccessAuditEvent,
    AccessResourceType,
    AccessRole,
    ArtifactOwnerRelation,
    CandidateOwnerAttestation,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.profiles import ARTIFACT_FAMILY_PROFILES
from powercontext.builtin.authz.service import (
    AccessAuditContext,
    AccessControlService,
    IdempotentAccessAuditStore,
    _access_call,
    _validate_idempotency_key,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, CANDIDATE_HEADS_TABLE, identity_string

_METADATA = MetaData()
OWNER_RECOVERY_TABLE = Table(
    "pc_access_owner_recovery",
    _METADATA,
    Column("operation_id", identity_string(64), primary_key=True),
    Column("deployment_id", identity_string(128), nullable=False),
    Column("scope_id", identity_string(256), nullable=False),
    Column("completed", Boolean, nullable=False),
    Column("payload", Text, nullable=False),
    # Render the pending-page index inside CREATE TABLE so concurrent startup
    # never races a separate CREATE INDEX after the table becomes visible.
    UniqueConstraint("deployment_id", "completed", "operation_id", name="uq_pc_access_owner_recovery_pending"),
)


@dataclass(frozen=True, slots=True)
class _ArtifactIntent:
    relation: ArtifactOwnerRelation
    context: AccessAuditContext
    kind: Literal["artifact"] = "artifact"
    schema: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class _CandidateIntent:
    attestation: CandidateOwnerAttestation
    kind: Literal["candidate"] = "candidate"
    schema: Literal[1] = 1


_Intent = _ArtifactIntent | _CandidateIntent
_CODEC = TypeAdapter(_Intent)


@dataclass(frozen=True, slots=True)
class PendingOwnerWrite:
    """Operator metadata; no Artifact, Candidate, or Source content is included."""

    operation_id: str
    scope_id: str
    kind: Literal["artifact", "candidate"]
    principal: PrincipalRef
    resource: ResourceRef | None
    candidate_id: str | None
    target: ResourceRef | None


@dataclass(frozen=True, slots=True)
class OwnerGap:
    """An operator-visible identity, never an inferred ownership assignment."""

    kind: Literal["artifact", "candidate"]
    scope_id: str
    family: str
    identity: str
    reason: Literal["pending", "unknown", "delivered_relation_missing", "legacy_selector_required"]
    operation_id: str | None


@dataclass(frozen=True, slots=True)
class OwnerGapPage:
    items: tuple[OwnerGap, ...]
    after: tuple[str, str] | None


@dataclass(slots=True)
class _RecoverySession:
    recovery: OwnerRecovery
    operation_ids: list[str]


_SESSION: ContextVar[_RecoverySession | None] = ContextVar("powercontext_owner_recovery", default=None)


class OwnerRecovery:
    """An outbox owned by one primary database and one configured deployment.

    The relationship writer must honor its existing same-owner/key idempotency
    contract. Artifact recovery additionally requires event-idempotent audit.
    """

    def __init__(self, database: AsyncDatabase, access: AccessControlService) -> None:
        self.database = database
        self.access = access

    async def initialize(self) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(CreateTable(OWNER_RECOVERY_TABLE, if_not_exists=True))

    def for_transaction(self, connection: AsyncConnection) -> OwnerCommit:
        if connection.engine is not self.database.engine:
            raise AccessUnavailableError("owner_recovery_database_mismatch")
        return OwnerCommit(self, connection)

    async def pending(
        self, *, scope_id: str | None = None, after: str | None = None, limit: int = 100
    ) -> tuple[PendingOwnerWrite, ...]:
        if not 1 <= limit <= 500:
            raise AccessInvalidRequestError("limit")
        statement = select(OWNER_RECOVERY_TABLE).where(
            OWNER_RECOVERY_TABLE.c.deployment_id == self.access.deployment_id,
            OWNER_RECOVERY_TABLE.c.completed.is_(False),
        )
        if scope_id is not None:
            statement = statement.where(OWNER_RECOVERY_TABLE.c.scope_id == scope_id)
        if after is not None:
            statement = statement.where(OWNER_RECOVERY_TABLE.c.operation_id > after)
        statement = statement.order_by(OWNER_RECOVERY_TABLE.c.operation_id).limit(limit)
        async with self.database.transaction() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_pending(str(row["operation_id"]), self._decode_row(row)) for row in rows)

    async def inventory_gaps(
        self,
        scope_id: str,
        *,
        kind: Literal["artifact", "candidate"] = "artifact",
        after: tuple[str, str] | None = None,
        limit: int = 100,
    ) -> OwnerGapPage:
        """Inspect metadata in bounded pages; unknown history grants no authority."""
        if kind not in {"artifact", "candidate"}:
            raise AccessInvalidRequestError("owner-recovery-kind")
        if not 1 <= limit <= 500:
            raise AccessInvalidRequestError("limit")
        table = ARTIFACT_HEADS_TABLE if kind == "artifact" else CANDIDATE_HEADS_TABLE
        identity = table.c.artifact_id if kind == "artifact" else table.c.candidate_id
        families = tuple(family for family, profile in ARTIFACT_FAMILY_PROFILES.items() if profile.enabled)
        statement = select(table.c.family, identity).where(table.c.scope_id == scope_id, table.c.family.in_(families))
        if kind == "candidate":
            statement = statement.where(table.c.candidate_kind == "artifact")
        if after is not None:
            statement = statement.where(tuple_(table.c.family, identity) > after)
        statement = statement.order_by(table.c.family, identity).limit(limit + 1)
        async with self.database.transaction() as connection:
            rows = (await connection.execute(statement)).all()
        items = []
        for family, object_id in rows[:limit]:
            resource = ResourceRef.artifact(scope_id, family=family, artifact_id=object_id)
            if kind == "artifact" and ARTIFACT_FAMILY_PROFILES[family].share_unit == "memory_entry":
                items.append(OwnerGap(kind, scope_id, family, object_id, "legacy_selector_required", None))
                continue
            owner = (
                await self.access.artifact_owner(resource)
                if kind == "artifact"
                else await self.access.candidate_owner(scope_id, object_id)
            )
            if owner is not None:
                continue
            key = resource.key if kind == "artifact" else f"{scope_id}\0{object_id}"
            operation_id = _key(self.access.deployment_id, kind, key)
            async with self.database.transaction() as connection:
                completed = await connection.scalar(
                    select(OWNER_RECOVERY_TABLE.c.completed).where(
                        OWNER_RECOVERY_TABLE.c.operation_id == operation_id,
                        OWNER_RECOVERY_TABLE.c.deployment_id == self.access.deployment_id,
                    )
                )
            reason = "unknown" if completed is None else "delivered_relation_missing" if completed else "pending"
            items.append(
                OwnerGap(kind, scope_id, family, object_id, reason, None if completed is None else operation_id)
            )
        next_after = (str(rows[limit - 1][0]), str(rows[limit - 1][1])) if len(rows) > limit else None
        return OwnerGapPage(tuple(items), next_after)

    async def replay(self, operation_id: str) -> None:
        """Complete an accepted intent without accepting a caller-supplied owner."""
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(OWNER_RECOVERY_TABLE).where(
                            OWNER_RECOVERY_TABLE.c.operation_id == operation_id,
                            OWNER_RECOVERY_TABLE.c.deployment_id == self.access.deployment_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["completed"]:
            return
        intent = self._decode_row(row)
        # No primary transaction is held across external relationship/audit I/O.
        if isinstance(intent, _ArtifactIntent):
            await self._deliver_artifact(operation_id, intent)
        else:
            await self._deliver_candidate(intent)
        async with self.database.transaction() as connection:
            await connection.execute(
                update(OWNER_RECOVERY_TABLE)
                .where(
                    OWNER_RECOVERY_TABLE.c.operation_id == operation_id,
                    OWNER_RECOVERY_TABLE.c.deployment_id == self.access.deployment_id,
                    OWNER_RECOVERY_TABLE.c.payload == row["payload"],
                )
                .values(completed=True)
            )

    def _decode_row(self, row: RowMapping) -> _Intent:
        intent = _decode(str(row["payload"]))
        if (
            row["deployment_id"] != self.access.deployment_id
            or row["operation_id"] != _operation_id(self.access.deployment_id, intent)
            or row["scope_id"] != _scope(intent)
        ):
            raise AccessUnavailableError("owner_recovery_payload_invalid")
        return intent

    @asynccontextmanager
    async def monitor(self) -> AsyncIterator[None]:
        """Retry bounded pages, advancing past unavailable or conflicting intents."""

        async def run() -> None:
            after = None
            while True:
                await asyncio.sleep(5)
                try:
                    # Decode each intent during replay so one damaged payload
                    # cannot prevent delivery of every other item on its page.
                    statement = select(OWNER_RECOVERY_TABLE.c.operation_id).where(
                        OWNER_RECOVERY_TABLE.c.deployment_id == self.access.deployment_id,
                        OWNER_RECOVERY_TABLE.c.completed.is_(False),
                    )
                    if after is not None:
                        statement = statement.where(OWNER_RECOVERY_TABLE.c.operation_id > after)
                    statement = statement.order_by(OWNER_RECOVERY_TABLE.c.operation_id).limit(100)
                    async with self.database.transaction() as connection:
                        pending = (await connection.execute(statement)).scalars().all()
                except Exception:  # noqa: S112
                    # Leave durable work pending; do not emit external error values.
                    continue
                for operation in pending:
                    try:
                        await self.replay(operation)
                    except Exception:  # noqa: S112
                        # Explicit replay reports errors; this worker retains the intent.
                        continue
                after = pending[-1] if len(pending) == 100 else None

        task = asyncio.create_task(run(), name="powercontext-owner-recovery")
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _deliver_artifact(self, operation_id: str, intent: _ArtifactIntent) -> None:
        audit = _idempotent_audit(self.access)
        writer = self.access.relationships
        if writer is None:
            raise AccessUnavailableError("relationship_management_unavailable")
        try:
            established = await _access_call(writer.establish_artifact_owner(intent.relation))
        except AccessConflictError:
            # Concurrent idempotent inserts may race at the remote unique key.
            established = await self.access.artifact_owner(intent.relation.resource)
        if established is None or not _same_relation(established, intent.relation):
            raise AccessConflictError("artifact-owner")
        context = intent.context
        event = AccessAuditEvent(
            cursor=None,
            event_id=str(uuid5(NAMESPACE_URL, f"powercontext:owner-recovery:{operation_id}")),
            occurred_at=intent.relation.established_at,
            request_id=context.request_id,
            transport=context.transport,
            operation=context.operation,
            principal=intent.relation.owner,
            actor=context.actor,
            action=AccessAction.ARTIFACT_WRITE,
            resource=established.resource,
            allowed=True,
            reason_code="artifact-owner-established",
            policy_revision=established.policy_revision,
            target=established.owner,
            role=AccessRole.ARTIFACT_OWNER,
        )
        recorded = await _access_call(audit.append_audit_once(event))
        if replace(recorded, cursor=None) != event:
            raise AccessConflictError("owner-recovery-audit")

    async def _deliver_candidate(self, intent: _CandidateIntent) -> None:
        writer = self.access.relationships
        if writer is None:
            raise AccessUnavailableError("relationship_management_unavailable")
        attestation = intent.attestation
        try:
            established = await _access_call(writer.attest_candidate_owner(attestation))
        except AccessConflictError:
            established = await self.access.candidate_owner(attestation.scope_id, attestation.candidate_id)
        if established != attestation:
            raise AccessConflictError("candidate-owner")

    async def _stage(self, connection: AsyncConnection, intent: _Intent) -> str:
        operation_id = _operation_id(self.access.deployment_id, intent)
        if connection.dialect.name == "sqlite":
            # Legacy sqlite3 does not BEGIN on SELECT or SAVEPOINT. Start a
            # native write transaction before a nested insert can be released.
            await connection.execute(
                update(OWNER_RECOVERY_TABLE)
                .where(OWNER_RECOVERY_TABLE.c.operation_id == operation_id)
                .values(completed=OWNER_RECOVERY_TABLE.c.completed)
            )
        statement = (
            select(OWNER_RECOVERY_TABLE.c.payload)
            .where(OWNER_RECOVERY_TABLE.c.operation_id == operation_id)
            .with_for_update()
        )
        existing = await connection.scalar(statement)
        if existing is not None:
            _require_same_intent(_decode(existing), intent)
            _track(self, operation_id)
            return operation_id
        try:
            async with connection.begin_nested():
                await connection.execute(
                    insert(OWNER_RECOVERY_TABLE).values(
                        operation_id=operation_id,
                        deployment_id=self.access.deployment_id,
                        scope_id=_scope(intent),
                        completed=False,
                        payload=_CODEC.dump_json(intent).decode(),
                    )
                )
        except IntegrityError:
            existing = await connection.scalar(statement)
            if existing is None:
                raise
            _require_same_intent(_decode(existing), intent)
        _track(self, operation_id)
        return operation_id


@asynccontextmanager
async def owner_recovery_session(recovery: OwnerRecovery | None) -> AsyncIterator[None]:
    """Deliver this operation's staged intents before its response succeeds."""
    if recovery is None:
        yield
        return
    token = _SESSION.set(_RecoverySession(recovery, []))
    try:
        yield
        await flush_owner_writes()
    finally:
        _SESSION.reset(token)


async def flush_owner_writes(*, artifact: ResourceRef | None = None, candidate: tuple[str, str] | None = None) -> None:
    session = _SESSION.get()
    if session is not None:
        while session.operation_ids:
            await session.recovery.replay(session.operation_ids[0])
            session.operation_ids.pop(0)
        if artifact is not None:
            # Idempotent domain retries can return before their commit hook.
            # Replay only the original persisted intent, never the retry identity.
            await session.recovery.replay(_key(session.recovery.access.deployment_id, "artifact", artifact.key))
        if candidate is not None:
            scope_id, candidate_id = candidate
            await session.recovery.replay(
                _key(session.recovery.access.deployment_id, "candidate", f"{scope_id}\0{candidate_id}")
            )


def owner_recovery_available(access: AccessControlService, database: AsyncDatabase) -> bool:
    session = _SESSION.get()
    return session is not None and session.recovery.access is access and session.recovery.database is database


def owner_write_access(access: AccessControlService, connection: AsyncConnection) -> AccessControlService | OwnerCommit:
    if access.supports_write_transaction(connection.engine):
        return access.with_connection(connection)
    session = _SESSION.get()
    if session is None or session.recovery.access is not access:
        raise AccessUnavailableError("owner_recovery_unavailable")
    return session.recovery.for_transaction(connection)


class OwnerCommit:
    """The two ownership operations that may be staged inside a domain commit."""

    def __init__(self, recovery: OwnerRecovery, connection: AsyncConnection) -> None:
        self.recovery = recovery
        self.connection = connection

    async def establish_artifact_owner(
        self, resource: ResourceRef, owner: PrincipalRef, *, idempotency_key: str, context: AccessAuditContext
    ) -> str:
        if resource.type is not AccessResourceType.ARTIFACT:
            raise AccessInvalidRequestError("artifact-identity")
        _validate_idempotency_key(idempotency_key)
        _idempotent_audit(self.recovery.access)
        relation = ArtifactOwnerRelation(
            resource=resource,
            owner=_identity(owner),
            established_at=datetime.now(UTC),
            policy_revision="pending",
            idempotency_key=idempotency_key,
        )
        context = replace(context, actor=None if context.actor is None else _identity(context.actor), subject_groups=())
        return await self.recovery._stage(self.connection, _ArtifactIntent(relation, context))

    async def attest_candidate_owner(
        self,
        *,
        scope_id: str,
        candidate_id: str,
        family: str,
        proposed_owner: PrincipalRef,
        target: ResourceRef | None,
        idempotency_key: str,
    ) -> str:
        _validate_idempotency_key(idempotency_key)
        attestation = CandidateOwnerAttestation(
            scope_id, candidate_id, family, _identity(proposed_owner), target, idempotency_key
        )
        return await self.recovery._stage(self.connection, _CandidateIntent(attestation))

    async def attest_candidate_owners(self, attestations: Sequence[CandidateOwnerAttestation]) -> tuple[str, ...]:
        operations = []
        for item in attestations:
            operations.append(
                await self.attest_candidate_owner(
                    scope_id=item.scope_id,
                    candidate_id=item.candidate_id,
                    family=item.family,
                    proposed_owner=item.proposed_owner,
                    target=item.target,
                    idempotency_key=item.idempotency_key,
                )
            )
        return tuple(operations)


def _idempotent_audit(access: AccessControlService) -> IdempotentAccessAuditStore:
    if not callable(getattr(access.audit, "append_audit_once", None)):
        raise AccessUnavailableError("owner_recovery_audit_idempotency_unavailable")
    return cast("IdempotentAccessAuditStore", access.audit)


def _track(recovery: OwnerRecovery, operation_id: str) -> None:
    session = _SESSION.get()
    if session is not None and session.recovery is recovery and operation_id not in session.operation_ids:
        session.operation_ids.append(operation_id)


def _identity(principal: PrincipalRef) -> PrincipalRef:
    return PrincipalRef(type=principal.type, id=principal.id)


def _same_relation(left: ArtifactOwnerRelation, right: ArtifactOwnerRelation) -> bool:
    return (
        left.resource == right.resource
        and _identity(left.owner) == _identity(right.owner)
        and left.idempotency_key == right.idempotency_key
    )


def _require_same_intent(left: _Intent, right: _Intent) -> None:
    if isinstance(left, _ArtifactIntent) and isinstance(right, _ArtifactIntent):
        same = _same_relation(left.relation, right.relation)
    else:
        same = isinstance(left, _CandidateIntent) and isinstance(right, _CandidateIntent) and left == right
    if not same:
        raise AccessConflictError("owner-recovery-intent")


def _scope(intent: _Intent) -> str:
    if isinstance(intent, _CandidateIntent):
        return intent.attestation.scope_id
    if intent.relation.resource.scope_id is None:
        raise AccessInvalidRequestError("artifact-identity")
    return intent.relation.resource.scope_id


def _operation_id(deployment_id: str, intent: _Intent) -> str:
    key = (
        intent.relation.resource.key
        if isinstance(intent, _ArtifactIntent)
        else f"{intent.attestation.scope_id}\0{intent.attestation.candidate_id}"
    )
    return _key(deployment_id, intent.kind, key)


def _key(deployment_id: str, kind: str, logical_key: str) -> str:
    return sha256(f"{deployment_id}\0{kind}\0{logical_key}".encode()).hexdigest()


def _decode(payload: str) -> _Intent:
    try:
        return _CODEC.validate_json(payload)
    except ValidationError as error:
        raise AccessUnavailableError("owner_recovery_payload_invalid") from error


def _pending(operation_id: str, intent: _Intent) -> PendingOwnerWrite:
    if isinstance(intent, _ArtifactIntent):
        return PendingOwnerWrite(
            operation_id, _scope(intent), "artifact", intent.relation.owner, intent.relation.resource, None, None
        )
    attestation = intent.attestation
    return PendingOwnerWrite(
        operation_id,
        attestation.scope_id,
        "candidate",
        attestation.proposed_owner,
        None,
        attestation.candidate_id,
        attestation.target,
    )
