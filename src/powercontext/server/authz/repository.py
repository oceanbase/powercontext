# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Dialect-neutral persistence for terminal Access relationships."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import identity_string
from powercontext.limits import (
    MAX_ARTIFACT_FAMILY_LENGTH,
    MAX_ARTIFACT_ID_LENGTH,
    MAX_POLICY_REVISION_LENGTH,
    MAX_SCOPE_ID_LENGTH,
)
from powercontext.server.authz.errors import AccessConflictError, AccessInvalidRequestError, AccessUnavailableError
from powercontext.server.authz.models import (
    ROLE_CARDINALITIES,
    AccessAction,
    AccessAuditEvent,
    AccessBinding,
    AccessBindingState,
    AccessResourceType,
    AccessRole,
    AccessRoleCardinality,
    AccessSubjectRef,
    ArtifactOwnerRelation,
    CandidateOwnerAttestation,
    GroupRef,
    HandoffReceiptIdentity,
    MemoryEntrySelector,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.service import BindingReplacement, BindingSearchRequest, ReplaceBinding

ACCESS_METADATA = MetaData()

ACCESS_POLICY_HEADS_TABLE = Table(
    "pc_access_relationship_heads",
    ACCESS_METADATA,
    Column("name", identity_string(32), primary_key=True),
    Column("revision", Integer, nullable=False),
    CheckConstraint("revision >= 0", name="ck_pc_access_relationship_heads_revision_nonnegative"),
)

ACCESS_BINDINGS_TABLE = Table(
    "pc_access_relationships",
    ACCESS_METADATA,
    Column("binding_id", identity_string(64), primary_key=True),
    Column("subject_type", identity_string(16), nullable=False),
    Column("subject_id", identity_string(255), nullable=False),
    Column("subject_description", Text),
    Column("resource_key_hash", identity_string(64), nullable=False),
    Column("resource_type", identity_string(16), nullable=False),
    Column("deployment_id", identity_string(128)),
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH)),
    Column("family", identity_string(MAX_ARTIFACT_FAMILY_LENGTH)),
    Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("selector_type", identity_string(32)),
    Column("selector_entry_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("role", identity_string(32), nullable=False),
    Column("singleton_key", identity_string(64), unique=True),
    Column("granted_by_type", identity_string(16), nullable=False),
    Column("granted_by_id", identity_string(255), nullable=False),
    Column("granted_by_description", Text),
    Column("reason", Text),
    Column("created_at", identity_string(32), nullable=False),
    Column("expires_at", identity_string(32)),
    Column("state", identity_string(16), nullable=False),
    Column("version", Integer, nullable=False),
    Column("policy_revision", identity_string(MAX_POLICY_REVISION_LENGTH), nullable=False),
    Column("idempotency_key", identity_string(255), nullable=False),
    Column("revoked_at", identity_string(32)),
    Column("revoked_by_type", identity_string(16)),
    Column("revoked_by_id", identity_string(255)),
    Column("revoked_by_description", Text),
    CheckConstraint("version > 0", name="ck_pc_access_relationships_version_positive"),
)

ACCESS_OWNERS_TABLE = Table(
    "pc_access_owners",
    ACCESS_METADATA,
    Column("owner_kind", identity_string(16), primary_key=True),
    Column("object_key_hash", identity_string(64), primary_key=True),
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
    Column("family", identity_string(MAX_ARTIFACT_FAMILY_LENGTH), nullable=False),
    Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("candidate_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("target_artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("selector_type", identity_string(32)),
    Column("selector_entry_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("owner_type", identity_string(16), nullable=False),
    Column("owner_id", identity_string(255), nullable=False),
    Column("owner_description", Text),
    Column("established_at", identity_string(32)),
    Column("policy_revision", identity_string(MAX_POLICY_REVISION_LENGTH)),
    Column("idempotency_key", identity_string(255), nullable=False),
    CheckConstraint(
        "(owner_kind = 'artifact' AND artifact_id IS NOT NULL AND candidate_id IS NULL "
        "AND target_artifact_id IS NULL AND established_at IS NOT NULL AND policy_revision IS NOT NULL) "
        "OR (owner_kind = 'candidate' AND candidate_id IS NOT NULL AND artifact_id IS NULL "
        "AND selector_type IS NULL AND selector_entry_id IS NULL "
        "AND established_at IS NULL AND policy_revision IS NULL)",
        name="ck_pc_access_owners_kind",
    ),
)

ACCESS_IDEMPOTENCY_TABLE = Table(
    "pc_access_idempotency",
    ACCESS_METADATA,
    Column("actor_id", identity_string(255), primary_key=True),
    Column("idempotency_key_hash", identity_string(64), primary_key=True),
    Column("operation", identity_string(64), nullable=False),
    Column("payload_hash", identity_string(64), nullable=False),
    Column("result_binding_id", identity_string(64)),
    Column("secondary_binding_id", identity_string(64)),
    UniqueConstraint("actor_id", "idempotency_key_hash", name="uq_pc_access_idempotency_actor_key"),
)

ACCESS_AUDIT_EVENTS_TABLE = Table(
    "pc_access_audit",
    ACCESS_METADATA,
    Column("cursor", Integer, primary_key=True, autoincrement=True),
    Column("event_id", identity_string(64), nullable=False, unique=True),
    Column("occurred_at", identity_string(32), nullable=False),
    Column("request_id", identity_string(128)),
    Column("transport", identity_string(16), nullable=False),
    Column("operation", identity_string(128), nullable=False),
    Column("principal_type", identity_string(16), nullable=False),
    Column("principal_id", identity_string(255), nullable=False),
    Column("principal_description", Text),
    Column("actor_type", identity_string(16)),
    Column("actor_id", identity_string(255)),
    Column("actor_description", Text),
    Column("action", identity_string(64), nullable=False),
    Column("resource_type", identity_string(16), nullable=False),
    Column("deployment_id", identity_string(128)),
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH)),
    Column("family", identity_string(MAX_ARTIFACT_FAMILY_LENGTH)),
    Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("selector_type", identity_string(32)),
    Column("selector_entry_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    Column("allowed", Boolean, nullable=False),
    Column("reason_code", identity_string(64), nullable=False),
    Column("policy_revision", identity_string(MAX_POLICY_REVISION_LENGTH)),
    Column("matched_subject_type", identity_string(16)),
    Column("matched_subject_id", identity_string(255)),
    Column("matched_subject_description", Text),
    Column("binding_id", identity_string(64)),
    Column("target_type", identity_string(16)),
    Column("target_id", identity_string(255)),
    Column("target_description", Text),
    Column("role", identity_string(32)),
    Column("expected_version", Integer),
    Column("result_version", Integer),
)

ACCESS_TABLES = (
    ACCESS_POLICY_HEADS_TABLE,
    ACCESS_BINDINGS_TABLE,
    ACCESS_OWNERS_TABLE,
    ACCESS_IDEMPOTENCY_TABLE,
    ACCESS_AUDIT_EVENTS_TABLE,
)
_POLICY_HEAD = "authorization"
_RECEIPT_IDENTITY_OPERATION = "handoff.receipt.identity"
_RECEIVER_IDENTITY_MATCHES = "receiver_identity_matches"
_RECEIVER_IDENTITY_MISMATCH = "receiver_identity_mismatch"


class RelationalAccessRepository:
    """Persist logical bindings, direct ownership and minimized audit events."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def get_receipt_identity(self, scope_id: str, source_id: str, /) -> HandoffReceiptIdentity | None:
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(ACCESS_AUDIT_EVENTS_TABLE).where(
                            ACCESS_AUDIT_EVENTS_TABLE.c.event_id == _receipt_identity_event_id(scope_id, source_id),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if (
            row["operation"] != _RECEIPT_IDENTITY_OPERATION
            or row["scope_id"] != scope_id
            or row["action"] != AccessAction.HANDOFF_ACKNOWLEDGE.value
            or not row["allowed"]
            or row["reason_code"] not in {_RECEIVER_IDENTITY_MATCHES, _RECEIVER_IDENTITY_MISMATCH}
        ):
            raise AccessUnavailableError("receipt_identity_pending")
        return HandoffReceiptIdentity(
            scope_id,
            source_id,
            _principal(row, "principal"),
            row["reason_code"] == _RECEIVER_IDENTITY_MATCHES,
        )

    async def record_receipt_identity(self, identity: HandoffReceiptIdentity, /) -> HandoffReceiptIdentity:
        existing = await self.get_receipt_identity(identity.scope_id, identity.source_id)
        if existing is not None:
            if existing != identity:
                raise AccessConflictError("receipt-identity")
            return existing
        # The unique event ID reserves this Source's attribution across retries
        # and concurrent Principals. Retain this audit event with the Receipt.
        event = AccessAuditEvent(
            cursor=None,
            event_id=_receipt_identity_event_id(identity.scope_id, identity.source_id),
            occurred_at=datetime.now(UTC),
            request_id=None,
            transport="server",
            operation=_RECEIPT_IDENTITY_OPERATION,
            principal=identity.principal,
            actor=None,
            action=AccessAction.HANDOFF_ACKNOWLEDGE,
            resource=ResourceRef.artifact(identity.scope_id, family="handoff", artifact_id="handoff"),
            allowed=True,
            reason_code=_RECEIVER_IDENTITY_MATCHES
            if identity.receiver_identity_matches
            else _RECEIVER_IDENTITY_MISMATCH,
            policy_revision=None,
        )
        try:
            await self.append_audit(event)
        except IntegrityError:
            existing = await self.get_receipt_identity(identity.scope_id, identity.source_id)
            if existing is None or existing != identity:
                raise AccessConflictError("receipt-identity") from None
            return existing
        return identity

    async def policy_revision(self) -> str:
        async with self._database.transaction() as connection:
            revision = await connection.scalar(
                select(ACCESS_POLICY_HEADS_TABLE.c.revision).where(ACCESS_POLICY_HEADS_TABLE.c.name == _POLICY_HEAD)
            )
        return str(revision or 0)

    async def active_bindings(
        self,
        subjects: Sequence[AccessSubjectRef],
        *,
        now: datetime,
    ) -> tuple[AccessBinding, ...]:
        if not subjects:
            return ()
        statement = select(ACCESS_BINDINGS_TABLE).where(
            or_(
                *(
                    (ACCESS_BINDINGS_TABLE.c.subject_type == subject.type)
                    & (ACCESS_BINDINGS_TABLE.c.subject_id == subject.id)
                    for subject in subjects
                )
            ),
            ACCESS_BINDINGS_TABLE.c.state == AccessBindingState.ACTIVE.value,
        )
        async with self._database.transaction() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(binding for row in rows if (binding := _decode_binding(row)).active_at(now))

    async def get_binding(self, binding_id: str, /) -> AccessBinding | None:
        async with self._database.transaction() as connection:
            row = await _binding_by_id(connection, binding_id)
        return None if row is None else _decode_binding(row)

    async def list_bindings(self, request: BindingSearchRequest, /) -> tuple[AccessBinding, ...]:
        statement = select(ACCESS_BINDINGS_TABLE).where(*_boundary_predicates(request.management_resource))
        if request.subject is not None:
            statement = statement.where(
                ACCESS_BINDINGS_TABLE.c.subject_type == request.subject.type,
                ACCESS_BINDINGS_TABLE.c.subject_id == request.subject.id,
            )
        if request.role is not None:
            statement = statement.where(ACCESS_BINDINGS_TABLE.c.role == request.role.value)
        if request.state is not None:
            statement = statement.where(ACCESS_BINDINGS_TABLE.c.state == request.state.value)
        if request.visible_roles:
            statement = statement.where(
                ACCESS_BINDINGS_TABLE.c.role.in_(tuple(role.value for role in request.visible_roles))
            )
        if request.cursor is not None:
            statement = statement.where(ACCESS_BINDINGS_TABLE.c.binding_id > request.cursor)
        statement = statement.order_by(ACCESS_BINDINGS_TABLE.c.binding_id).limit(request.limit)
        async with self._database.transaction() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_decode_binding(row) for row in rows)

    async def establish_artifact_owner(
        self,
        relation: ArtifactOwnerRelation,
        /,
    ) -> ArtifactOwnerRelation:
        if relation.resource.type is not AccessResourceType.ARTIFACT:
            raise AccessInvalidRequestError("artifact-identity")
        resource_hash = _digest(relation.resource.key)
        async with self._database.transaction() as connection:
            existing = (
                (
                    await connection.execute(
                        select(ACCESS_OWNERS_TABLE).where(
                            ACCESS_OWNERS_TABLE.c.owner_kind == "artifact",
                            ACCESS_OWNERS_TABLE.c.object_key_hash == resource_hash,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                owner = _decode_owner(existing)
                if owner.owner == relation.owner and owner.idempotency_key == relation.idempotency_key:
                    return owner
                raise AccessConflictError("artifact-owner")
            revision = await self._increment_policy_revision(connection)
            established = replace(relation, policy_revision=str(revision))
            try:
                await connection.execute(insert(ACCESS_OWNERS_TABLE).values(_owner_row(established)))
            except IntegrityError as error:
                raise AccessConflictError("artifact-owner") from error
        return established

    async def get_artifact_owner(self, resource: ResourceRef, /) -> ArtifactOwnerRelation | None:
        if resource.type is not AccessResourceType.ARTIFACT:
            return None
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(ACCESS_OWNERS_TABLE).where(
                            ACCESS_OWNERS_TABLE.c.owner_kind == "artifact",
                            ACCESS_OWNERS_TABLE.c.object_key_hash == _digest(resource.key),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _decode_owner(row)

    async def attest_candidate_owner(
        self,
        attestation: CandidateOwnerAttestation,
        /,
    ) -> CandidateOwnerAttestation:
        async with self._database.transaction() as connection:
            existing = (
                (
                    await connection.execute(
                        select(ACCESS_OWNERS_TABLE).where(
                            ACCESS_OWNERS_TABLE.c.owner_kind == "candidate",
                            ACCESS_OWNERS_TABLE.c.object_key_hash
                            == _candidate_owner_key(attestation.scope_id, attestation.candidate_id),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                decoded = _decode_candidate_owner(existing)
                if decoded == attestation:
                    return decoded
                raise AccessConflictError("candidate-owner")
            try:
                await connection.execute(insert(ACCESS_OWNERS_TABLE).values(_candidate_owner_row(attestation)))
            except IntegrityError as error:
                raise AccessConflictError("candidate-owner") from error
        return attestation

    async def get_candidate_owner(
        self,
        scope_id: str,
        candidate_id: str,
        /,
    ) -> CandidateOwnerAttestation | None:
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(ACCESS_OWNERS_TABLE).where(
                            ACCESS_OWNERS_TABLE.c.owner_kind == "candidate",
                            ACCESS_OWNERS_TABLE.c.object_key_hash == _candidate_owner_key(scope_id, candidate_id),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _decode_candidate_owner(row)

    async def list_owned_resources(self, owner: PrincipalRef, /) -> tuple[ResourceRef, ...]:
        async with self._database.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        select(ACCESS_OWNERS_TABLE).where(
                            ACCESS_OWNERS_TABLE.c.owner_kind == "artifact",
                            ACCESS_OWNERS_TABLE.c.owner_type == owner.type,
                            ACCESS_OWNERS_TABLE.c.owner_id == owner.id,
                        )
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_decode_resource(row, artifact_only=True) for row in rows)

    async def create_binding(self, binding: AccessBinding, /) -> AccessBinding:
        payload_hash = _creation_hash(binding)
        async with self._database.transaction() as connection:
            replay = await _idempotent_result(
                connection,
                actor=binding.granted_by,
                key=binding.idempotency_key,
                operation="binding.create",
                payload_hash=payload_hash,
            )
            if replay is not None:
                row = await _binding_by_id(connection, replay[0])
                if row is None:
                    raise AccessConflictError("idempotency-key")
                return _decode_binding(row)
            revision = await self._increment_policy_revision(connection)
            created = replace(binding, policy_revision=str(revision))
            await _insert_binding(connection, created)
            try:
                await _record_idempotency(
                    connection,
                    actor=binding.granted_by,
                    key=binding.idempotency_key,
                    operation="binding.create",
                    payload_hash=payload_hash,
                    result_binding_id=binding.binding_id,
                )
            except IntegrityError as error:
                raise AccessConflictError("idempotency-key") from error
        return created

    async def revoke_binding(
        self,
        binding_id: str,
        /,
        *,
        expected_version: int,
        idempotency_key: str,
        revoked_at: datetime,
        revoked_by: PrincipalRef,
    ) -> AccessBinding:
        payload_hash = _digest(f"{binding_id}\0{expected_version}")
        async with self._database.transaction() as connection:
            replay = await _idempotent_result(
                connection,
                actor=revoked_by,
                key=idempotency_key,
                operation="binding.revoke",
                payload_hash=payload_hash,
            )
            if replay is not None:
                row = await _binding_by_id(connection, replay[0])
                if row is None:
                    raise AccessConflictError("idempotency-key")
                return _decode_binding(row)
            revision = await self._increment_policy_revision(connection)
            current_row = await _binding_by_id(connection, binding_id, for_update=True)
            if current_row is None:
                raise AccessConflictError("binding-version")
            current = _decode_binding(current_row)
            if current.version != expected_version or current.state is not AccessBindingState.ACTIVE:
                raise AccessConflictError("binding-version")
            revoked = replace(
                current,
                state=AccessBindingState.REVOKED,
                version=expected_version + 1,
                policy_revision=str(revision),
                revoked_at=revoked_at,
                revoked_by=revoked_by,
            )
            result = await connection.execute(
                update(ACCESS_BINDINGS_TABLE)
                .where(
                    ACCESS_BINDINGS_TABLE.c.binding_id == binding_id,
                    ACCESS_BINDINGS_TABLE.c.version == expected_version,
                    ACCESS_BINDINGS_TABLE.c.state == AccessBindingState.ACTIVE.value,
                )
                .values(_binding_mutation_row(revoked))
            )
            if result.rowcount != 1:
                raise AccessConflictError("binding-version")
            await _record_idempotency(
                connection,
                actor=revoked_by,
                key=idempotency_key,
                operation="binding.revoke",
                payload_hash=payload_hash,
                result_binding_id=binding_id,
            )
        return revoked

    async def replace_binding(
        self,
        request: ReplaceBinding,
        /,
        *,
        actor: PrincipalRef,
        changed_at: datetime,
    ) -> BindingReplacement:
        payload_hash = _digest(
            "\0".join((
                request.binding_id,
                str(request.expected_version),
                request.subject.type,
                request.subject.id,
                request.reason or "",
                "" if request.expires_at is None else _timestamp(request.expires_at),
            ))
        )
        async with self._database.transaction() as connection:
            replay = await _idempotent_result(
                connection,
                actor=actor,
                key=request.idempotency_key,
                operation="binding.replace",
                payload_hash=payload_hash,
            )
            if replay is not None:
                old_row = await _binding_by_id(connection, replay[0])
                new_row = None if replay[1] is None else await _binding_by_id(connection, replay[1])
                if old_row is None or new_row is None:
                    raise AccessConflictError("idempotency-key")
                return BindingReplacement(_decode_binding(old_row), _decode_binding(new_row))
            revision = await self._increment_policy_revision(connection)
            old_row = await _binding_by_id(connection, request.binding_id, for_update=True)
            if old_row is None:
                raise AccessConflictError("binding-version")
            old = _decode_binding(old_row)
            if old.version != request.expected_version or old.state is not AccessBindingState.ACTIVE:
                raise AccessConflictError("binding-version")
            if _singleton_key(old) != old_row["singleton_key"]:
                raise AccessConflictError("binding_cardinality_conflict")
            revoked = replace(
                old,
                state=AccessBindingState.REVOKED,
                version=old.version + 1,
                policy_revision=str(revision),
                revoked_at=changed_at,
                revoked_by=actor,
            )
            created = AccessBinding(
                binding_id=f"bind_{sha256(f'{request.idempotency_key}:{old.binding_id}'.encode()).hexdigest()[:32]}",
                subject=request.subject,
                resource=old.resource,
                role=old.role,
                granted_by=actor,
                reason=request.reason,
                created_at=changed_at,
                expires_at=request.expires_at,
                state=AccessBindingState.ACTIVE,
                version=1,
                policy_revision=str(revision),
                idempotency_key=request.idempotency_key,
            )
            result = await connection.execute(
                update(ACCESS_BINDINGS_TABLE)
                .where(
                    ACCESS_BINDINGS_TABLE.c.binding_id == old.binding_id,
                    ACCESS_BINDINGS_TABLE.c.version == request.expected_version,
                    ACCESS_BINDINGS_TABLE.c.state == AccessBindingState.ACTIVE.value,
                )
                .values(_binding_mutation_row(revoked))
            )
            if result.rowcount != 1:
                raise AccessConflictError("binding-version")
            await _insert_binding(connection, created)
            try:
                await _record_idempotency(
                    connection,
                    actor=actor,
                    key=request.idempotency_key,
                    operation="binding.replace",
                    payload_hash=payload_hash,
                    result_binding_id=old.binding_id,
                    secondary_binding_id=created.binding_id,
                )
            except IntegrityError as error:
                raise AccessConflictError("idempotency-key") from error
        return BindingReplacement(revoked, created)

    async def append_audit(self, event: AccessAuditEvent, /) -> AccessAuditEvent:
        async with self._database.transaction() as connection:
            await connection.execute(insert(ACCESS_AUDIT_EVENTS_TABLE).values(_audit_row(event)))
            cursor = await connection.scalar(
                select(ACCESS_AUDIT_EVENTS_TABLE.c.cursor).where(ACCESS_AUDIT_EVENTS_TABLE.c.event_id == event.event_id)
            )
            if cursor is None:
                raise RuntimeError("Access audit insert did not return a cursor")  # noqa: TRY003
        return replace(event, cursor=int(cursor))

    async def list_audit(
        self,
        *,
        resource: ResourceRef,
        after: int | None = None,
        limit: int = 100,
        action: AccessAction | None = None,
        subject: AccessSubjectRef | None = None,
        allowed: bool | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
    ) -> tuple[AccessAuditEvent, ...]:
        statement = select(ACCESS_AUDIT_EVENTS_TABLE).where(*_boundary_predicates(resource, audit=True))
        if action is not None:
            statement = statement.where(ACCESS_AUDIT_EVENTS_TABLE.c.action == action.value)
        if subject is not None:
            statement = statement.where(
                ACCESS_AUDIT_EVENTS_TABLE.c.principal_type == subject.type,
                ACCESS_AUDIT_EVENTS_TABLE.c.principal_id == subject.id,
            )
        if allowed is not None:
            statement = statement.where(ACCESS_AUDIT_EVENTS_TABLE.c.allowed == allowed)
        if occurred_after is not None:
            statement = statement.where(ACCESS_AUDIT_EVENTS_TABLE.c.occurred_at >= _timestamp(occurred_after))
        if occurred_before is not None:
            statement = statement.where(ACCESS_AUDIT_EVENTS_TABLE.c.occurred_at < _timestamp(occurred_before))
        if after is not None:
            statement = statement.where(ACCESS_AUDIT_EVENTS_TABLE.c.cursor > after)
        statement = statement.order_by(ACCESS_AUDIT_EVENTS_TABLE.c.cursor).limit(limit)
        async with self._database.transaction() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(_decode_audit(row) for row in rows)

    @staticmethod
    async def _increment_policy_revision(connection: Any) -> int:
        # All Binding mutations acquire this row before locking Binding rows.
        # Keeping one lock order also covers expiration and replacement races.
        current = await connection.scalar(
            select(ACCESS_POLICY_HEADS_TABLE.c.revision)
            .where(ACCESS_POLICY_HEADS_TABLE.c.name == _POLICY_HEAD)
            .with_for_update()
        )
        if current is None:
            try:
                await connection.execute(insert(ACCESS_POLICY_HEADS_TABLE).values(name=_POLICY_HEAD, revision=1))
            except IntegrityError as error:
                raise AccessConflictError("binding-version") from error
            return 1
        result = await connection.execute(
            update(ACCESS_POLICY_HEADS_TABLE)
            .where(
                ACCESS_POLICY_HEADS_TABLE.c.name == _POLICY_HEAD,
                ACCESS_POLICY_HEADS_TABLE.c.revision == current,
            )
            .values(revision=int(current) + 1)
        )
        if result.rowcount != 1:
            raise AccessConflictError("binding-version")
        return int(current) + 1


def _receipt_identity_event_id(scope_id: str, source_id: str) -> str:
    return _digest(
        json.dumps((_RECEIPT_IDENTITY_OPERATION, scope_id, source_id), ensure_ascii=False, separators=(",", ":"))
    )


async def _binding_by_id(connection: Any, binding_id: str, *, for_update: bool = False) -> Mapping[Any, Any] | None:
    statement = select(ACCESS_BINDINGS_TABLE).where(ACCESS_BINDINGS_TABLE.c.binding_id == binding_id)
    if for_update:
        statement = statement.with_for_update()
    return (await connection.execute(statement)).mappings().one_or_none()


async def _idempotent_result(
    connection: Any,
    *,
    actor: PrincipalRef,
    key: str,
    operation: str,
    payload_hash: str,
) -> tuple[str, str | None] | None:
    row = (
        (
            await connection.execute(
                select(ACCESS_IDEMPOTENCY_TABLE).where(
                    ACCESS_IDEMPOTENCY_TABLE.c.actor_id == actor.id,
                    ACCESS_IDEMPOTENCY_TABLE.c.idempotency_key_hash == _digest(key),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if row["operation"] != operation or row["payload_hash"] != payload_hash:
        raise AccessConflictError("idempotency-key")
    result = row["result_binding_id"]
    if result is None:
        raise AccessConflictError("idempotency-key")
    secondary = row["secondary_binding_id"]
    return str(result), None if secondary is None else str(secondary)


async def _record_idempotency(
    connection: Any,
    *,
    actor: PrincipalRef,
    key: str,
    operation: str,
    payload_hash: str,
    result_binding_id: str,
    secondary_binding_id: str | None = None,
) -> None:
    await connection.execute(
        insert(ACCESS_IDEMPOTENCY_TABLE).values(
            actor_id=actor.id,
            idempotency_key_hash=_digest(key),
            operation=operation,
            payload_hash=payload_hash,
            result_binding_id=result_binding_id,
            secondary_binding_id=secondary_binding_id,
        )
    )


def _singleton_key(binding: AccessBinding) -> str | None:
    if (
        ROLE_CARDINALITIES[binding.role] is AccessRoleCardinality.ONE_PER_RESOURCE
        and binding.state is AccessBindingState.ACTIVE
    ):
        return _digest(json.dumps((binding.resource.key, binding.role.value), separators=(",", ":")))
    return None


async def _insert_binding(connection: Any, binding: AccessBinding) -> None:
    singleton_key = _singleton_key(binding)
    if singleton_key is not None:
        # Expiration releases only the slot; retain the historical Binding for
        # lookup and idempotent replay. The unique key arbitrates concurrent claims.
        await connection.execute(
            update(ACCESS_BINDINGS_TABLE)
            .where(
                ACCESS_BINDINGS_TABLE.c.singleton_key == singleton_key,
                ACCESS_BINDINGS_TABLE.c.expires_at <= _timestamp(binding.created_at),
            )
            .values(singleton_key=None)
        )
    try:
        await connection.execute(insert(ACCESS_BINDINGS_TABLE).values(_binding_row(binding)))
    except IntegrityError as error:
        reason = "binding_cardinality_conflict" if singleton_key is not None else "idempotency-key"
        raise AccessConflictError(reason) from error


def _boundary_predicates(resource: ResourceRef, *, audit: bool = False) -> tuple[Any, ...]:
    table = ACCESS_AUDIT_EVENTS_TABLE if audit else ACCESS_BINDINGS_TABLE
    if resource.type is AccessResourceType.SERVER:
        return ()
    if resource.type is AccessResourceType.SCOPE:
        return (table.c.scope_id == resource.scope_id,)
    return _resource_predicates(table, resource)


def _resource_predicates(table: Table, resource: ResourceRef) -> tuple[Any, ...]:
    selector = resource.selector
    return (
        table.c.resource_type == resource.type.value,
        table.c.deployment_id == resource.deployment_id,
        table.c.scope_id == resource.scope_id,
        table.c.family == resource.family,
        table.c.artifact_id == resource.artifact_id,
        table.c.selector_type == (None if selector is None else selector.type),
        table.c.selector_entry_id == (None if selector is None else selector.entry_id),
    )


def _resource_row(resource: ResourceRef) -> dict[str, object | None]:
    selector = resource.selector
    return {
        "resource_type": resource.type.value,
        "deployment_id": resource.deployment_id,
        "scope_id": resource.scope_id,
        "family": resource.family,
        "artifact_id": resource.artifact_id,
        "selector_type": None if selector is None else selector.type,
        "selector_entry_id": None if selector is None else selector.entry_id,
    }


def _binding_row(binding: AccessBinding) -> dict[str, object | None]:
    return {
        "binding_id": binding.binding_id,
        **_subject_row("subject", binding.subject),
        "resource_key_hash": _digest(binding.resource.key),
        **_resource_row(binding.resource),
        "role": binding.role.value,
        "singleton_key": _singleton_key(binding),
        **_subject_row("granted_by", binding.granted_by),
        "reason": binding.reason,
        "created_at": _timestamp(binding.created_at),
        "expires_at": None if binding.expires_at is None else _timestamp(binding.expires_at),
        "state": binding.state.value,
        "version": binding.version,
        "policy_revision": binding.policy_revision,
        "idempotency_key": binding.idempotency_key,
        "revoked_at": None if binding.revoked_at is None else _timestamp(binding.revoked_at),
        **_optional_subject_row("revoked_by", binding.revoked_by),
    }


def _binding_mutation_row(binding: AccessBinding) -> dict[str, object | None]:
    return {
        "state": binding.state.value,
        "singleton_key": _singleton_key(binding),
        "version": binding.version,
        "policy_revision": binding.policy_revision,
        "revoked_at": None if binding.revoked_at is None else _timestamp(binding.revoked_at),
        **_optional_subject_row("revoked_by", binding.revoked_by),
    }


def _decode_binding(row: Mapping[Any, Any]) -> AccessBinding:
    return AccessBinding(
        binding_id=str(row["binding_id"]),
        subject=_subject(row, "subject"),
        resource=_decode_resource(row),
        role=AccessRole(str(row["role"])),
        granted_by=_principal(row, "granted_by"),
        reason=None if row["reason"] is None else str(row["reason"]),
        created_at=_parse_timestamp(row["created_at"]),
        expires_at=None if row["expires_at"] is None else _parse_timestamp(row["expires_at"]),
        state=AccessBindingState(str(row["state"])),
        version=int(row["version"]),
        policy_revision=str(row["policy_revision"]),
        idempotency_key=str(row["idempotency_key"]),
        revoked_at=None if row["revoked_at"] is None else _parse_timestamp(row["revoked_at"]),
        revoked_by=_optional_principal(row, "revoked_by"),
    )


def _owner_row(relation: ArtifactOwnerRelation) -> dict[str, object | None]:
    resource = relation.resource
    selector = resource.selector
    return {
        "owner_kind": "artifact",
        "object_key_hash": _digest(resource.key),
        "scope_id": resource.scope_id,
        "family": resource.family,
        "artifact_id": resource.artifact_id,
        "selector_type": None if selector is None else selector.type,
        "selector_entry_id": None if selector is None else selector.entry_id,
        **_subject_row("owner", relation.owner),
        "established_at": _timestamp(relation.established_at),
        "policy_revision": relation.policy_revision,
        "idempotency_key": relation.idempotency_key,
    }


def _decode_owner(row: Mapping[Any, Any]) -> ArtifactOwnerRelation:
    return ArtifactOwnerRelation(
        resource=_decode_resource(row, artifact_only=True),
        owner=_principal(row, "owner"),
        established_at=_parse_timestamp(row["established_at"]),
        policy_revision=str(row["policy_revision"]),
        idempotency_key=str(row["idempotency_key"]),
    )


def _candidate_owner_key(scope_id: str, candidate_id: str) -> str:
    return _digest(json.dumps((scope_id, candidate_id), ensure_ascii=False, separators=(",", ":")))


def _candidate_owner_row(attestation: CandidateOwnerAttestation) -> dict[str, object | None]:
    return {
        "scope_id": attestation.scope_id,
        "candidate_id": attestation.candidate_id,
        "owner_kind": "candidate",
        "object_key_hash": _candidate_owner_key(attestation.scope_id, attestation.candidate_id),
        "family": attestation.family,
        **_subject_row("owner", attestation.proposed_owner),
        "target_artifact_id": None if attestation.target is None else attestation.target.artifact_id,
        "idempotency_key": attestation.idempotency_key,
    }


def _decode_candidate_owner(row: Mapping[Any, Any]) -> CandidateOwnerAttestation:
    scope_id = str(row["scope_id"])
    family = str(row["family"])
    target_id = row["target_artifact_id"]
    return CandidateOwnerAttestation(
        scope_id=scope_id,
        candidate_id=str(row["candidate_id"]),
        family=family,
        proposed_owner=_principal(row, "owner"),
        target=(
            None if target_id is None else ResourceRef.artifact(scope_id, family=family, artifact_id=str(target_id))
        ),
        idempotency_key=str(row["idempotency_key"]),
    )


def _audit_row(event: AccessAuditEvent) -> dict[str, object | None]:
    return {
        "event_id": event.event_id,
        "occurred_at": _timestamp(event.occurred_at),
        "request_id": event.request_id,
        "transport": event.transport,
        "operation": event.operation,
        **_subject_row("principal", event.principal),
        **_optional_subject_row("actor", event.actor),
        "action": event.action.value,
        **_resource_row(event.resource),
        "allowed": event.allowed,
        "reason_code": event.reason_code,
        "policy_revision": event.policy_revision,
        **_optional_subject_row("matched_subject", event.matched_subject),
        "binding_id": event.binding_id,
        **_optional_subject_row("target", event.target),
        "role": None if event.role is None else event.role.value,
        "expected_version": event.expected_version,
        "result_version": event.result_version,
    }


def _decode_audit(row: Mapping[Any, Any]) -> AccessAuditEvent:
    return AccessAuditEvent(
        cursor=int(row["cursor"]),
        event_id=str(row["event_id"]),
        occurred_at=_parse_timestamp(row["occurred_at"]),
        request_id=None if row["request_id"] is None else str(row["request_id"]),
        transport=str(row["transport"]),
        operation=str(row["operation"]),
        principal=_principal(row, "principal"),
        actor=_optional_principal(row, "actor"),
        action=AccessAction(str(row["action"])),
        resource=_decode_resource(row),
        allowed=bool(row["allowed"]),
        reason_code=str(row["reason_code"]),
        policy_revision=None if row["policy_revision"] is None else str(row["policy_revision"]),
        matched_subject=_optional_subject(row, "matched_subject"),
        binding_id=None if row["binding_id"] is None else str(row["binding_id"]),
        target=_optional_subject(row, "target"),
        role=None if row["role"] is None else AccessRole(str(row["role"])),
        expected_version=None if row["expected_version"] is None else int(row["expected_version"]),
        result_version=None if row["result_version"] is None else int(row["result_version"]),
    )


def _decode_resource(row: Mapping[Any, Any], *, artifact_only: bool = False) -> ResourceRef:
    resource_type = AccessResourceType.ARTIFACT if artifact_only else AccessResourceType(str(row["resource_type"]))
    if resource_type is AccessResourceType.SERVER:
        return ResourceRef.server(str(row["deployment_id"]))
    if resource_type is AccessResourceType.SCOPE:
        return ResourceRef.scope(str(row["scope_id"]))
    selector_type = row["selector_type"]
    selector = (
        None
        if selector_type is None
        else MemoryEntrySelector(type=str(selector_type), entry_id=str(row["selector_entry_id"]))
    )
    return ResourceRef.artifact(
        str(row["scope_id"]),
        family=str(row["family"]),
        artifact_id=str(row["artifact_id"]),
        selector=selector,
    )


def _subject_row(prefix: str, subject: AccessSubjectRef) -> dict[str, object | None]:
    return {
        f"{prefix}_type": subject.type,
        f"{prefix}_id": subject.id,
        f"{prefix}_description": subject.description,
    }


def _optional_subject_row(prefix: str, subject: AccessSubjectRef | None) -> dict[str, object | None]:
    return (
        {f"{prefix}_type": None, f"{prefix}_id": None, f"{prefix}_description": None}
        if subject is None
        else _subject_row(prefix, subject)
    )


def _subject(row: Mapping[Any, Any], prefix: str) -> AccessSubjectRef:
    subject_type = str(row[f"{prefix}_type"])
    values = {
        "type": subject_type,
        "id": str(row[f"{prefix}_id"]),
        "description": None if row[f"{prefix}_description"] is None else str(row[f"{prefix}_description"]),
    }
    return GroupRef(**values) if subject_type == "group" else PrincipalRef(**values)


def _optional_subject(row: Mapping[Any, Any], prefix: str) -> AccessSubjectRef | None:
    return None if row[f"{prefix}_type"] is None else _subject(row, prefix)


def _principal(row: Mapping[Any, Any], prefix: str) -> PrincipalRef:
    value = _subject(row, prefix)
    if not isinstance(value, PrincipalRef):
        raise AccessInvalidRequestError("principal")
    return value


def _optional_principal(row: Mapping[Any, Any], prefix: str) -> PrincipalRef | None:
    value = _optional_subject(row, prefix)
    if value is not None and not isinstance(value, PrincipalRef):
        raise AccessInvalidRequestError("principal")
    return value


def _creation_hash(binding: AccessBinding) -> str:
    return _digest(
        "\0".join((
            binding.subject.type,
            binding.subject.id,
            binding.resource.key,
            binding.role.value,
            binding.reason or "",
            "" if binding.expires_at is None else _timestamp(binding.expires_at),
        ))
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise AccessInvalidRequestError("timestamp")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


__all__ = ("ACCESS_TABLES", "RelationalAccessRepository")
