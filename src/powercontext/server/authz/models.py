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

"""Transport-independent Access Control values."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from powercontext.server.authz.errors import AccessInvalidRequestError

DEFAULT_DEPLOYMENT_ID = "powercontext"


class AccessAction(StrEnum):
    """Stable actions checked by Server business operations."""

    # Internal authentication-only requirement used by Access self-service routes.
    ACCESS_SELF = "access.self"
    SERVER_OBSERVE = "server.observe"
    SERVER_ADMIN = "server.admin"
    SCOPE_READ = "scope.read"
    SCOPE_CONTRIBUTE = "scope.contribute"
    SCOPE_REVIEW = "scope.review"
    SCOPE_DELEGATE = "scope.delegate"
    SCOPE_ADMIN = "scope.admin"
    ARTIFACT_READ = "artifact.read"
    ARTIFACT_WRITE = "artifact.write"
    ARTIFACT_SHARE = "artifact.share"
    HANDOFF_EVIDENCE_INSPECT = "handoff.evidence.inspect"
    HANDOFF_ACKNOWLEDGE = "handoff.acknowledge"
    PROMPT_USE = "prompt.use"


PUBLIC_ACCESS_ACTIONS = tuple(action for action in AccessAction if action is not AccessAction.ACCESS_SELF)


class AccessResourceType(StrEnum):
    """Stable Resource Kinds understood by the authorization boundary."""

    SERVER = "server"
    SCOPE = "scope"
    ARTIFACT = "artifact"


class AccessRole(StrEnum):
    """Fixed first-version roles exposed by the Access API."""

    HANDOFF_VIEWER = "handoff.viewer"
    HANDOFF_RECEIVER = "handoff.receiver"
    ARTIFACT_VIEWER = "artifact.viewer"
    PROMPT_USER = "prompt.user"
    ARTIFACT_OWNER = "artifact.owner"
    SCOPE_VIEWER = "scope.viewer"
    SCOPE_CONTRIBUTOR = "scope.contributor"
    SCOPE_REVIEWER = "scope.reviewer"
    SCOPE_DELEGATOR = "scope.delegator"
    SCOPE_ADMIN = "scope.admin"
    SERVER_OBSERVER = "server.observer"
    SERVER_ADMIN = "server.admin"


class AccessRoleCardinality(StrEnum):
    """Number of active assignments allowed for one role and resource."""

    MANY_PER_RESOURCE = "many_per_resource"
    ONE_PER_RESOURCE = "one_per_resource"


class AccessBindingState(StrEnum):
    """Lifecycle state of an immutable role assignment."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class PrincipalRef:
    """Canonical user or service identity established by authentication."""

    type: str
    id: str
    description: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.type not in {"user", "service"} or not _valid_text(self.id, maximum=255):
            raise AccessInvalidRequestError("principal")
        if self.description is not None and not _valid_text(self.description, maximum=255):
            raise AccessInvalidRequestError("principal")

    @property
    def key(self) -> str:
        """Return the deployment-wide identity key; display metadata is excluded."""

        return self.id


@dataclass(frozen=True, slots=True)
class GroupRef:
    """Canonical group identity resolved by a trusted identity source."""

    type: str
    id: str
    description: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.type != "group" or not _valid_text(self.id, maximum=255):
            raise AccessInvalidRequestError("group")
        if self.description is not None and not _valid_text(self.description, maximum=255):
            raise AccessInvalidRequestError("group")

    @property
    def key(self) -> str:
        """Return the deployment-wide identity key; display metadata is excluded."""

        return self.id


AccessSubjectRef: TypeAlias = PrincipalRef | GroupRef


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    """Version-independent identity of one logical Artifact."""

    family: str
    artifact_id: str

    def __post_init__(self) -> None:
        if not _valid_text(self.family, maximum=128) or not _valid_text(self.artifact_id, maximum=128):
            raise AccessInvalidRequestError("artifact-identity")


@dataclass(frozen=True, slots=True)
class MemoryEntrySelector:
    """Version-independent selector for one logical Memory Entry."""

    entry_id: str
    type: str = "memory_entry"

    def __post_init__(self) -> None:
        if self.type != "memory_entry" or not _valid_text(self.entry_id, maximum=128):
            raise AccessInvalidRequestError("memory-entry-selector")


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """Canonical structured target of one authorization decision."""

    type: AccessResourceType
    deployment_id: str | None = None
    scope_id: str | None = None
    identity: ArtifactIdentity | None = None
    selector: MemoryEntrySelector | None = None

    def __post_init__(self) -> None:
        if self.type is AccessResourceType.SERVER:
            valid = (
                _valid_text(self.deployment_id, maximum=128)
                and self.scope_id is None
                and self.identity is None
                and self.selector is None
            )
        elif self.type is AccessResourceType.SCOPE:
            valid = (
                self.deployment_id is None
                and _valid_text(self.scope_id, maximum=256)
                and self.identity is None
                and self.selector is None
            )
        else:
            valid = self.deployment_id is None and _valid_text(self.scope_id, maximum=256) and self.identity is not None
        if not valid:
            raise AccessInvalidRequestError("resource")

    @classmethod
    def server(cls, deployment_id: str = DEFAULT_DEPLOYMENT_ID) -> ResourceRef:
        return cls(type=AccessResourceType.SERVER, deployment_id=deployment_id)

    @classmethod
    def scope(cls, scope_id: str) -> ResourceRef:
        return cls(type=AccessResourceType.SCOPE, scope_id=scope_id)

    @classmethod
    def artifact(
        cls,
        scope_id: str,
        *,
        family: str,
        artifact_id: str,
        selector: MemoryEntrySelector | None = None,
    ) -> ResourceRef:
        return cls(
            type=AccessResourceType.ARTIFACT,
            scope_id=scope_id,
            identity=ArtifactIdentity(family=family, artifact_id=artifact_id),
            selector=selector,
        )

    @property
    def family(self) -> str | None:
        return None if self.identity is None else self.identity.family

    @property
    def artifact_id(self) -> str | None:
        return None if self.identity is None else self.identity.artifact_id

    @property
    def key(self) -> str:
        if self.type is AccessResourceType.SERVER:
            value: dict[str, object] = {"deployment_id": self.deployment_id, "type": self.type.value}
        elif self.type is AccessResourceType.SCOPE:
            value = {"scope_id": self.scope_id, "type": self.type.value}
        else:
            if self.identity is None:
                raise AccessInvalidRequestError("artifact-identity")
            value = {
                "identity": {
                    "artifact_id": self.identity.artifact_id,
                    "family": self.identity.family,
                },
                "scope_id": self.scope_id,
                "selector": (
                    None
                    if self.selector is None
                    else {
                        "entry_id": self.selector.entry_id,
                        "type": self.selector.type,
                    }
                ),
                "type": self.type.value,
            }
        return _canonical_json(value)

    @property
    def parent_scope(self) -> ResourceRef | None:
        return None if self.scope_id is None else ResourceRef.scope(self.scope_id)


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """One low-sensitivity authorization result plus internal attribution."""

    allowed: bool
    reason_code: str
    policy_revision: str | None
    matched_subject: AccessSubjectRef | None = None
    matched_binding_id: str | None = None


@dataclass(frozen=True, slots=True)
class AccessBinding:
    """One persisted role assignment."""

    binding_id: str
    subject: AccessSubjectRef
    resource: ResourceRef
    role: AccessRole
    granted_by: PrincipalRef
    reason: str | None
    created_at: datetime
    expires_at: datetime | None
    state: AccessBindingState
    version: int
    policy_revision: str
    idempotency_key: str
    revoked_at: datetime | None = None
    revoked_by: PrincipalRef | None = None

    def active_at(self, now: datetime) -> bool:
        return self.state is AccessBindingState.ACTIVE and (self.expires_at is None or self.expires_at > now)


@dataclass(frozen=True, slots=True)
class ArtifactOwnerRelation:
    """The single direct owner of one logical Artifact."""

    resource: ResourceRef
    owner: PrincipalRef
    established_at: datetime
    policy_revision: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class HandoffReceiptIdentity:
    """Server-attested identity retained as an immutable Access audit event."""

    scope_id: str
    source_id: str
    principal: PrincipalRef
    receiver_identity_matches: bool


@dataclass(frozen=True, slots=True)
class CandidateOwnerAttestation:
    """Server-attested proposed owner locked to one Review Candidate."""

    scope_id: str
    candidate_id: str
    family: str
    proposed_owner: PrincipalRef
    target: ResourceRef | None
    idempotency_key: str

    def __post_init__(self) -> None:
        if not _valid_text(self.scope_id, maximum=256) or not _valid_text(self.candidate_id, maximum=128):
            raise AccessInvalidRequestError("candidate-owner")
        if not _valid_text(self.family, maximum=128):
            raise AccessInvalidRequestError("candidate-owner")
        if self.target is not None and (
            self.target.type is not AccessResourceType.ARTIFACT
            or self.target.scope_id != self.scope_id
            or self.target.family != self.family
        ):
            raise AccessInvalidRequestError("candidate-owner")


@dataclass(frozen=True, slots=True)
class AccessAuditEvent:
    """Data-minimized authorization or relationship audit record."""

    cursor: int | None
    event_id: str
    occurred_at: datetime
    request_id: str | None
    transport: str
    operation: str
    principal: PrincipalRef
    actor: PrincipalRef | None
    action: AccessAction
    resource: ResourceRef
    allowed: bool
    reason_code: str
    policy_revision: str | None
    matched_subject: AccessSubjectRef | None = None
    binding_id: str | None = None
    target: AccessSubjectRef | None = None
    role: AccessRole | None = None
    expected_version: int | None = None
    result_version: int | None = None


# Parent-to-child implications are separate so management roles never become
# accidental content roles.
ROLE_ACTIONS: dict[AccessRole, frozenset[AccessAction]] = {
    AccessRole.HANDOFF_VIEWER: frozenset({AccessAction.ARTIFACT_READ, AccessAction.HANDOFF_EVIDENCE_INSPECT}),
    AccessRole.HANDOFF_RECEIVER: frozenset({
        AccessAction.ARTIFACT_READ,
        AccessAction.HANDOFF_EVIDENCE_INSPECT,
        AccessAction.HANDOFF_ACKNOWLEDGE,
    }),
    AccessRole.ARTIFACT_VIEWER: frozenset({AccessAction.ARTIFACT_READ}),
    AccessRole.PROMPT_USER: frozenset({AccessAction.ARTIFACT_READ, AccessAction.PROMPT_USE}),
    AccessRole.ARTIFACT_OWNER: frozenset({
        AccessAction.ARTIFACT_READ,
        AccessAction.ARTIFACT_WRITE,
        AccessAction.ARTIFACT_SHARE,
        AccessAction.HANDOFF_EVIDENCE_INSPECT,
    }),
    AccessRole.SCOPE_VIEWER: frozenset({AccessAction.SCOPE_READ}),
    AccessRole.SCOPE_CONTRIBUTOR: frozenset({AccessAction.SCOPE_READ, AccessAction.SCOPE_CONTRIBUTE}),
    AccessRole.SCOPE_REVIEWER: frozenset({AccessAction.SCOPE_READ, AccessAction.SCOPE_REVIEW}),
    AccessRole.SCOPE_DELEGATOR: frozenset({AccessAction.SCOPE_READ, AccessAction.SCOPE_DELEGATE}),
    AccessRole.SCOPE_ADMIN: frozenset({AccessAction.SCOPE_ADMIN}),
    AccessRole.SERVER_OBSERVER: frozenset({AccessAction.SERVER_OBSERVE}),
    AccessRole.SERVER_ADMIN: frozenset({AccessAction.SERVER_ADMIN}),
}


ROLE_CHILD_ACTIONS: dict[AccessRole, frozenset[AccessAction]] = {
    AccessRole.SCOPE_VIEWER: frozenset({
        AccessAction.ARTIFACT_READ,
        AccessAction.HANDOFF_EVIDENCE_INSPECT,
        AccessAction.PROMPT_USE,
    }),
    AccessRole.SCOPE_CONTRIBUTOR: frozenset({
        AccessAction.ARTIFACT_READ,
        AccessAction.HANDOFF_EVIDENCE_INSPECT,
        AccessAction.HANDOFF_ACKNOWLEDGE,
        AccessAction.PROMPT_USE,
    }),
    AccessRole.SCOPE_REVIEWER: frozenset({
        AccessAction.ARTIFACT_READ,
        AccessAction.HANDOFF_EVIDENCE_INSPECT,
        AccessAction.PROMPT_USE,
    }),
    AccessRole.SCOPE_DELEGATOR: frozenset({
        AccessAction.ARTIFACT_READ,
        AccessAction.HANDOFF_EVIDENCE_INSPECT,
        AccessAction.PROMPT_USE,
    }),
    AccessRole.SCOPE_ADMIN: frozenset({AccessAction.ARTIFACT_SHARE}),
    AccessRole.SERVER_ADMIN: frozenset({AccessAction.SCOPE_ADMIN, AccessAction.ARTIFACT_SHARE}),
}


ROLE_RESOURCE_TYPES: dict[AccessRole, AccessResourceType] = {
    AccessRole.HANDOFF_VIEWER: AccessResourceType.ARTIFACT,
    AccessRole.HANDOFF_RECEIVER: AccessResourceType.ARTIFACT,
    AccessRole.ARTIFACT_VIEWER: AccessResourceType.ARTIFACT,
    AccessRole.PROMPT_USER: AccessResourceType.ARTIFACT,
    AccessRole.ARTIFACT_OWNER: AccessResourceType.ARTIFACT,
    AccessRole.SCOPE_VIEWER: AccessResourceType.SCOPE,
    AccessRole.SCOPE_CONTRIBUTOR: AccessResourceType.SCOPE,
    AccessRole.SCOPE_REVIEWER: AccessResourceType.SCOPE,
    AccessRole.SCOPE_DELEGATOR: AccessResourceType.SCOPE,
    AccessRole.SCOPE_ADMIN: AccessResourceType.SCOPE,
    AccessRole.SERVER_OBSERVER: AccessResourceType.SERVER,
    AccessRole.SERVER_ADMIN: AccessResourceType.SERVER,
}


ROLE_CARDINALITIES: dict[AccessRole, AccessRoleCardinality] = dict.fromkeys(
    AccessRole, AccessRoleCardinality.MANY_PER_RESOURCE
)
ROLE_CARDINALITIES[AccessRole.HANDOFF_RECEIVER] = AccessRoleCardinality.ONE_PER_RESOURCE
ROLE_CARDINALITIES[AccessRole.ARTIFACT_OWNER] = AccessRoleCardinality.ONE_PER_RESOURCE


ROLE_SUBJECT_TYPES: dict[AccessRole, frozenset[str]] = {
    role: frozenset({"user", "service", "group"}) for role in AccessRole
}
ROLE_SUBJECT_TYPES[AccessRole.HANDOFF_RECEIVER] = frozenset({"user", "service"})
ROLE_SUBJECT_TYPES[AccessRole.ARTIFACT_OWNER] = frozenset({"user", "service"})


def _valid_text(value: object, *, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip() and len(value) <= maximum


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = (
    "DEFAULT_DEPLOYMENT_ID",
    "PUBLIC_ACCESS_ACTIONS",
    "ROLE_ACTIONS",
    "ROLE_CARDINALITIES",
    "ROLE_CHILD_ACTIONS",
    "ROLE_RESOURCE_TYPES",
    "ROLE_SUBJECT_TYPES",
    "AccessAction",
    "AccessAuditEvent",
    "AccessBinding",
    "AccessBindingState",
    "AccessDecision",
    "AccessResourceType",
    "AccessRole",
    "AccessRoleCardinality",
    "AccessSubjectRef",
    "ArtifactIdentity",
    "ArtifactOwnerRelation",
    "CandidateOwnerAttestation",
    "GroupRef",
    "MemoryEntrySelector",
    "PrincipalRef",
    "ResourceRef",
)
