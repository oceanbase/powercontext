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

"""Authorization Provider SPI and Server-owned Access use cases."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from base64 import b64decode, urlsafe_b64encode
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from uuid import uuid4

from powercontext.limits import MAX_POLICY_REVISION_LENGTH
from powercontext.server.authz.errors import (
    AccessBindingNotFoundError,
    AccessConflictError,
    AccessControlError,
    AccessDeniedError,
    AccessIdentityRequiredError,
    AccessInvalidRequestError,
    AccessUnavailableError,
)
from powercontext.server.authz.models import (
    DEFAULT_DEPLOYMENT_ID,
    ROLE_ACTIONS,
    ROLE_CHILD_ACTIONS,
    AccessAction,
    AccessAuditEvent,
    AccessBinding,
    AccessBindingState,
    AccessDecision,
    AccessResourceType,
    AccessRole,
    AccessSubjectRef,
    ArtifactOwnerRelation,
    CandidateOwnerAttestation,
    GroupRef,
    HandoffReceiptIdentity,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.profiles import (
    ARTIFACT_FAMILY_PROFILES,
    artifact_family_profile,
    validate_action_resource,
    validate_binding_role,
    validate_binding_subject,
)

_T = TypeVar("_T")
_MAX_AUTHORIZED_FILTER_IDENTITIES = 10_000


@dataclass(frozen=True, slots=True)
class AuthorizedResourceFilter:
    """Complete bounded identities and parent constraints produced before data access."""

    exact_resources: tuple[ResourceRef, ...]
    parent_constraints: tuple[ResourceRef, ...]
    complete: bool
    policy_revision: str | None
    max_direct_resource_keys: int


@dataclass(frozen=True, slots=True)
class AuthorizedResourcePage:
    """One stable, non-discovering page of resources visible to a Principal."""

    items: tuple[ResourceRef, ...]
    total: int
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AccessBindingPage:
    """One authorized page of canonical Binding metadata."""

    items: tuple[AccessBinding, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AccessAuditPage:
    """One authorized page from the append-only audit boundary."""

    items: tuple[AccessAuditEvent, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AccessProviderCapabilities:
    """Enforcement features that one configured Provider can safely supply."""

    safe_resource_filtering: bool
    multi_requirement_check: bool
    relationship_management: bool
    group_subjects: bool = False
    multi_principal: bool = False
    max_direct_resource_keys: int = _MAX_AUTHORIZED_FILTER_IDENTITIES


@dataclass(frozen=True, slots=True)
class AccessAuditContext:
    """Trusted request facts attached to one decision."""

    transport: str
    operation: str
    request_id: str | None = None
    actor: PrincipalRef | None = None
    subject_groups: tuple[GroupRef, ...] = ()


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """Normalized AuthZEN-shaped point decision request."""

    subject: PrincipalRef
    action: AccessAction
    resource: ResourceRef
    context: AccessAuditContext


@dataclass(frozen=True, slots=True)
class ResourceSearchRequest:
    """Normalized request for a safe Provider-owned resource filter."""

    subject: PrincipalRef
    action: AccessAction
    resource_type: AccessResourceType
    family: str | None
    context: AccessAuditContext


@dataclass(frozen=True, slots=True)
class BindingSearchRequest:
    """Bounded relationship query after its management boundary is authorized."""

    management_resource: ResourceRef
    subject: AccessSubjectRef | None = None
    role: AccessRole | None = None
    state: AccessBindingState | None = None
    cursor: str | None = None
    limit: int = 100
    visible_roles: tuple[AccessRole, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditSearchRequest:
    """Bounded audit query under one required administration boundary."""

    resource: ResourceRef
    action: AccessAction | None = None
    subject: AccessSubjectRef | None = None
    allowed: bool | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    cursor: str | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class CreateBinding:
    """Validated intent to create one immutable Access Binding."""

    subject: AccessSubjectRef
    resource: ResourceRef
    role: AccessRole
    idempotency_key: str
    reason: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_idempotency_key(self.idempotency_key)
        if self.reason is not None and len(self.reason) > 1_024:
            raise AccessInvalidRequestError("reason")


@dataclass(frozen=True, slots=True)
class ReplaceBinding:
    """Atomic compare-and-swap replacement of one immutable Binding."""

    binding_id: str
    expected_version: int
    subject: AccessSubjectRef
    idempotency_key: str
    reason: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_idempotency_key(self.idempotency_key)
        if self.expected_version < 1:
            raise AccessInvalidRequestError("binding-version")
        if self.reason is not None and len(self.reason) > 1_024:
            raise AccessInvalidRequestError("reason")


@dataclass(frozen=True, slots=True)
class BindingReplacement:
    """The revoked Binding and its active replacement."""

    previous: AccessBinding
    current: AccessBinding


class AuthorizationProvider(Protocol):
    """Replaceable decision interface suitable for embedded or remote PDPs."""

    async def check(self, request: AccessRequest, /) -> AccessDecision: ...

    async def check_batch(
        self,
        requests: Sequence[AccessRequest],
        /,
    ) -> Sequence[AccessDecision]: ...

    async def resolve_resource_filter(
        self,
        request: ResourceSearchRequest,
        /,
    ) -> AuthorizedResourceFilter: ...


class RelationshipReader(Protocol):
    """Read canonical relationship metadata without reading business content."""

    async def get_receipt_identity(self, scope_id: str, source_id: str, /) -> HandoffReceiptIdentity | None: ...

    async def get_binding(self, binding_id: str, /) -> AccessBinding | None: ...

    async def list_bindings(self, request: BindingSearchRequest, /) -> tuple[AccessBinding, ...]: ...

    async def get_artifact_owner(self, resource: ResourceRef, /) -> ArtifactOwnerRelation | None: ...

    async def get_candidate_owner(self, scope_id: str, candidate_id: str, /) -> CandidateOwnerAttestation | None: ...


class RelationshipWriter(Protocol):
    """Idempotent relationship mutations paired with a decision Provider."""

    async def record_receipt_identity(self, identity: HandoffReceiptIdentity, /) -> HandoffReceiptIdentity: ...

    async def establish_artifact_owner(self, relation: ArtifactOwnerRelation, /) -> ArtifactOwnerRelation: ...

    async def attest_candidate_owner(
        self,
        attestation: CandidateOwnerAttestation,
        /,
    ) -> CandidateOwnerAttestation: ...

    async def create_binding(self, binding: AccessBinding, /) -> AccessBinding: ...

    async def revoke_binding(
        self,
        binding_id: str,
        /,
        *,
        expected_version: int,
        idempotency_key: str,
        revoked_at: datetime,
        revoked_by: PrincipalRef,
    ) -> AccessBinding: ...

    async def replace_binding(
        self,
        request: ReplaceBinding,
        /,
        *,
        actor: PrincipalRef,
        changed_at: datetime,
    ) -> BindingReplacement: ...


class RelationshipStore(RelationshipReader, RelationshipWriter, Protocol):
    """Complete relationship management capability used by public Access APIs."""


class AccessAuditStore(Protocol):
    """Append-only audit boundary that can use a dedicated compliance backend."""

    async def append_audit(self, event: AccessAuditEvent, /) -> AccessAuditEvent: ...

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
    ) -> tuple[AccessAuditEvent, ...]: ...


class AccessRepository(RelationshipReader, RelationshipWriter, AccessAuditStore, Protocol):
    """Read requirements used by the built-in Provider."""

    async def policy_revision(self) -> str: ...

    async def active_bindings(
        self,
        subjects: Sequence[AccessSubjectRef],
        *,
        now: datetime,
    ) -> tuple[AccessBinding, ...]: ...

    async def list_owned_resources(self, owner: PrincipalRef, /) -> tuple[ResourceRef, ...]: ...


class BuiltinAuthorizationProvider:
    """Hierarchical RBAC profile backed by canonical immutable relationships."""

    def __init__(
        self,
        repository: AccessRepository,
        *,
        deployment_id: str = DEFAULT_DEPLOYMENT_ID,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._deployment_id = deployment_id
        self._clock = clock or (lambda: datetime.now(UTC))

    async def check(self, request: AccessRequest, /) -> AccessDecision:
        decisions = await self.check_batch((request,))
        return decisions[0]

    async def check_batch(
        self,
        requests: Sequence[AccessRequest],
        /,
    ) -> tuple[AccessDecision, ...]:
        revision = await self._repository.policy_revision()
        if not requests:
            return ()
        principal = requests[0].subject
        if any(request.subject != principal for request in requests):
            raise AccessInvalidRequestError("batch-subject")
        revision = contextual_policy_revision(revision, requests[0].context.subject_groups)
        subjects: tuple[AccessSubjectRef, ...] = (principal, *requests[0].context.subject_groups)
        bindings = await self._repository.active_bindings(subjects, now=self._clock())
        decisions: list[AccessDecision] = []
        for request in requests:
            if request.action is AccessAction.ACCESS_SELF:
                decisions.append(AccessDecision(True, "authenticated", revision))
                continue
            owner = (
                await self._repository.get_artifact_owner(request.resource)
                if request.resource.type is AccessResourceType.ARTIFACT
                else None
            )
            if request.resource.type is AccessResourceType.ARTIFACT and owner is None:
                binding_decision = _binding_decision(
                    bindings, request.action, request.resource, policy_revision=revision
                )
                reason = "artifact-owner-pending" if binding_decision.allowed else "no-matching-policy"
                decisions.append(AccessDecision(False, reason, revision))
                continue
            if (
                owner is not None
                and owner.owner == principal
                and request.action in ROLE_ACTIONS[AccessRole.ARTIFACT_OWNER]
            ):
                decisions.append(
                    AccessDecision(
                        True,
                        "artifact-owner",
                        revision,
                        matched_subject=principal,
                    )
                )
                continue
            decisions.append(_binding_decision(bindings, request.action, request.resource, policy_revision=revision))
        return tuple(decisions)

    async def resolve_resource_filter(
        self,
        request: ResourceSearchRequest,
        /,
    ) -> AuthorizedResourceFilter:
        revision = contextual_policy_revision(
            await self._repository.policy_revision(),
            request.context.subject_groups,
        )
        subjects: tuple[AccessSubjectRef, ...] = (request.subject, *request.context.subject_groups)
        bindings = await self._repository.active_bindings(subjects, now=self._clock())
        exact: dict[str, ResourceRef] = {}
        parents: dict[str, ResourceRef] = {}
        for binding in bindings:
            resource = binding.resource
            if (
                resource.type is request.resource_type
                and request.action in ROLE_ACTIONS[binding.role]
                and (request.family is None or resource.family == request.family)
            ):
                exact[resource.key] = resource
            elif _resource_is_parent(resource, request.resource_type) and _parent_binding_grants(
                binding, request.action, request.resource_type
            ):
                parents[resource.key] = resource
        if (
            request.resource_type is AccessResourceType.ARTIFACT
            and request.action in ROLE_ACTIONS[AccessRole.ARTIFACT_OWNER]
        ):
            for resource in await self._repository.list_owned_resources(request.subject):
                if request.family is None or resource.family == request.family:
                    exact[resource.key] = resource
        return AuthorizedResourceFilter(
            exact_resources=tuple(exact[key] for key in sorted(exact)),
            parent_constraints=tuple(parents[key] for key in sorted(parents)),
            complete=True,
            policy_revision=revision,
            max_direct_resource_keys=_MAX_AUTHORIZED_FILTER_IDENTITIES,
        )


class AccessControlService:
    """Fail-closed Access orchestration shared by HTTP and MCP transports."""

    def __init__(
        self,
        provider: AuthorizationProvider,
        *,
        relationships: RelationshipStore | None,
        audit: AccessAuditStore,
        deployment_id: str = DEFAULT_DEPLOYMENT_ID,
        provider_capabilities: AccessProviderCapabilities | None = None,
        clock: Callable[[], datetime] | None = None,
        cursor_secret: bytes | None = None,
        static_scope_principal: PrincipalRef | None = None,
    ) -> None:
        self.provider = provider
        self.relationships = relationships
        self.audit = audit
        self.deployment_id = deployment_id
        self.mode = "enforced"
        self.provider_capabilities = provider_capabilities or AccessProviderCapabilities(
            safe_resource_filtering=True,
            multi_requirement_check=True,
            relationship_management=relationships is not None,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cursor_secret = cursor_secret or secrets.token_bytes(32)
        self._static_scope_principal = static_scope_principal

    async def readiness(self) -> bool:
        """Probe decisions and required stores without granting or caching authority."""

        subject = PrincipalRef(type="service", id="powercontext-readiness")
        server = ResourceRef.server(self.deployment_id)
        artifact = ResourceRef.artifact("powercontext-readiness", family="handoff", artifact_id="handoff")
        context = AccessAuditContext(transport="background", operation="access_readiness")
        try:
            async with asyncio.timeout(5):
                decisions = tuple(
                    await self.provider.check_batch((
                        AccessRequest(subject, AccessAction.SERVER_OBSERVE, server, context),
                        AccessRequest(subject, AccessAction.ARTIFACT_READ, artifact, context),
                    ))
                )
                if len(decisions) != 2:
                    return False
                for decision in decisions:
                    _validate_provider_decision(decision)
                await self.audit.list_audit(resource=server, limit=1)
                if self.relationships is not None:
                    await self.relationships.get_binding("powercontext-readiness")
                    await self.relationships.get_artifact_owner(artifact)
                    await self.relationships.get_receipt_identity("powercontext-readiness", "powercontext-readiness")
        except Exception:
            return False
        else:
            return True

    def uses_static_preset(self, principal: PrincipalRef | None) -> bool:
        return self._static_scope_principal is not None and principal == self._static_scope_principal

    async def bootstrap_static_scope(
        self,
        principal: PrincipalRef | None,
        scope_id: str,
        *,
        context: AccessAuditContext,
    ) -> None:
        """Idempotently materialize the fixed static preset before scope content access."""

        actor = _required_principal(principal)
        if self._static_scope_principal is None or actor != self._static_scope_principal:
            return
        resource = ResourceRef.scope(scope_id)
        now = self._clock()
        for role in (
            AccessRole.SCOPE_VIEWER,
            AccessRole.SCOPE_CONTRIBUTOR,
            AccessRole.SCOPE_REVIEWER,
            AccessRole.SCOPE_DELEGATOR,
        ):
            key = f"static-preset:{self.deployment_id}:{actor.id}:{scope_id}:{role.value}"
            binding = AccessBinding(
                binding_id=str(uuid4()),
                subject=actor,
                resource=resource,
                role=role,
                granted_by=actor,
                reason="static bearer preset",
                created_at=now,
                expires_at=None,
                state=AccessBindingState.ACTIVE,
                version=1,
                policy_revision="pending",
                idempotency_key=key,
            )
            created = await _access_call(self._relationship_writer().create_binding(binding))
            if created.binding_id == binding.binding_id:
                await _access_call(self._record_relationship(created, principal=actor, context=context))

    async def check(
        self,
        principal: PrincipalRef | None,
        action: AccessAction,
        resource: ResourceRef,
        *,
        context: AccessAuditContext,
    ) -> AccessDecision:
        actor = _required_principal(principal)
        validate_action_resource(action, resource, deployment_id=self.deployment_id)
        request = AccessRequest(subject=actor, action=action, resource=resource, context=context)
        decision = await _access_call(self.provider.check(request))
        _validate_provider_decision(decision)
        if action is not AccessAction.ACCESS_SELF:
            await _access_call(self._record_decision(actor, action, resource, decision, context=context))
        return decision

    async def require(
        self,
        principal: PrincipalRef | None,
        action: AccessAction,
        resource: ResourceRef,
        *,
        context: AccessAuditContext,
    ) -> AccessDecision:
        decision = await self.check(principal, action, resource, context=context)
        if not decision.allowed and decision.reason_code == "artifact-owner-pending":
            raise AccessUnavailableError("artifact_owner_pending")
        if not decision.allowed:
            raise AccessDeniedError
        return decision

    async def check_batch(
        self,
        principal: PrincipalRef | None,
        checks: Sequence[tuple[AccessAction, ResourceRef]],
        *,
        context: AccessAuditContext,
    ) -> tuple[AccessDecision, ...]:
        if not self.provider_capabilities.multi_requirement_check:
            raise AccessUnavailableError("multi_requirement_check_unavailable")
        actor = _required_principal(principal)
        for action, resource in checks:
            validate_action_resource(action, resource, deployment_id=self.deployment_id)
        requests = tuple(
            AccessRequest(subject=actor, action=action, resource=resource, context=context)
            for action, resource in checks
        )
        decisions = tuple(await _access_call(self.provider.check_batch(requests)))
        if len(decisions) != len(checks):
            raise AccessUnavailableError()
        for decision in decisions:
            _validate_provider_decision(decision)
        for (action, resource), decision in zip(checks, decisions, strict=True):
            if action is not AccessAction.ACCESS_SELF:
                await _access_call(self._record_decision(actor, action, resource, decision, context=context))
        return decisions

    async def require_all(
        self,
        principal: PrincipalRef | None,
        checks: Sequence[tuple[AccessAction, ResourceRef]],
        *,
        context: AccessAuditContext,
    ) -> tuple[AccessDecision, ...]:
        decisions = await self.check_batch(principal, checks, context=context)
        if any(not decision.allowed and decision.reason_code != "artifact-owner-pending" for decision in decisions):
            raise AccessDeniedError
        if not all(decision.allowed for decision in decisions):
            raise AccessUnavailableError("artifact_owner_pending")
        return decisions

    async def require_any(
        self,
        principal: PrincipalRef | None,
        checks: Sequence[tuple[AccessAction, ResourceRef]],
        *,
        context: AccessAuditContext,
    ) -> tuple[AccessDecision, ...]:
        decisions = await self.check_batch(principal, checks, context=context)
        if not any(decision.allowed for decision in decisions):
            if any(decision.reason_code == "artifact-owner-pending" for decision in decisions):
                raise AccessUnavailableError("artifact_owner_pending")
            raise AccessDeniedError
        return decisions

    async def list_resources(
        self,
        principal: PrincipalRef | None,
        *,
        action: AccessAction,
        resource_type: AccessResourceType,
        family: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        context: AccessAuditContext,
        query_resources: Callable[[AuthorizedResourceFilter], Awaitable[Sequence[ResourceRef]]] | None = None,
    ) -> AuthorizedResourcePage:
        if not self.provider_capabilities.safe_resource_filtering:
            raise AccessUnavailableError("safe_resource_filtering_unavailable")
        if limit < 1 or limit > 500:
            raise AccessInvalidRequestError("limit")
        _validate_resource_list_query(action=action, resource_type=resource_type, family=family)
        actor = _required_principal(principal)
        request = ResourceSearchRequest(
            subject=actor,
            action=action,
            resource_type=resource_type,
            family=family,
            context=context,
        )
        authorized_filter = await _access_call(self.provider.resolve_resource_filter(request))
        _validate_resource_filter(
            authorized_filter,
            action=action,
            resource_type=resource_type,
            family=family,
            deployment_id=self.deployment_id,
            provider_limit=self.provider_capabilities.max_direct_resource_keys,
        )
        if authorized_filter.parent_constraints and query_resources is None:
            raise AccessUnavailableError("safe_resource_filtering_unavailable")
        resources = (
            authorized_filter.exact_resources
            if query_resources is None
            else tuple(await _access_call(query_resources(authorized_filter)))
        )
        if any(not _resource_allowed_by_filter(resource, authorized_filter) for resource in resources):
            raise AccessUnavailableError("safe_resource_filtering_unavailable")
        ordered_by_key = {resource.key: resource for resource in resources}
        ordered = tuple(ordered_by_key[key] for key in sorted(ordered_by_key))
        after_key = self._decode_cursor(
            cursor,
            actor=actor,
            action=action,
            resource_type=resource_type,
            family=family,
            policy_revision=authorized_filter.policy_revision,
        )
        visible = ordered if after_key is None else tuple(resource for resource in ordered if resource.key > after_key)
        items = visible[:limit]
        next_cursor = (
            self._encode_cursor(
                items[-1].key,
                actor=actor,
                action=action,
                resource_type=resource_type,
                family=family,
                policy_revision=authorized_filter.policy_revision,
            )
            if len(visible) > len(items)
            else None
        )
        return AuthorizedResourcePage(items=items, total=len(ordered), next_cursor=next_cursor)

    async def list_bindings(
        self,
        principal: PrincipalRef | None,
        request: BindingSearchRequest,
        *,
        context: AccessAuditContext,
    ) -> AccessBindingPage:
        if request.limit < 1 or request.limit > 500:
            raise AccessInvalidRequestError("limit")
        actor = _required_principal(principal)
        checks = _binding_list_checks(request.management_resource, deployment_id=self.deployment_id)
        decisions = await self.require_any(actor, checks, context=context)
        policy_revision = next(
            (decision.policy_revision for decision in decisions if decision.allowed),
            None,
        )
        delegate_only = (
            request.management_resource.type is AccessResourceType.SCOPE
            and decisions[0].allowed
            and not any(decision.allowed for decision in decisions[1:])
        )
        visible_roles = (
            (AccessRole.HANDOFF_VIEWER, AccessRole.HANDOFF_RECEIVER) if delegate_only else request.visible_roles
        )
        query = _binding_query_identity(request, visible_roles=visible_roles)
        after = self._decode_scoped_cursor(
            request.cursor,
            actor=actor,
            operation="access.bindings.list",
            query=query,
            policy_revision=policy_revision,
        )
        rows = await _access_call(
            self._relationship_reader().list_bindings(
                replace(request, cursor=after, limit=request.limit + 1, visible_roles=visible_roles)
            )
        )
        items = rows[: request.limit]
        next_cursor = (
            self._encode_scoped_cursor(
                items[-1].binding_id,
                actor=actor,
                operation="access.bindings.list",
                query=query,
                policy_revision=policy_revision,
            )
            if len(rows) > len(items)
            else None
        )
        return AccessBindingPage(items=items, next_cursor=next_cursor)

    async def list_audit(
        self,
        principal: PrincipalRef | None,
        request: AuditSearchRequest,
        *,
        context: AccessAuditContext,
    ) -> AccessAuditPage:
        if request.limit < 1 or request.limit > 500:
            raise AccessInvalidRequestError("limit")
        if (
            request.occurred_after is not None
            and request.occurred_before is not None
            and request.occurred_after >= request.occurred_before
        ):
            raise AccessInvalidRequestError("time-range")
        actor = _required_principal(principal)
        if request.resource.type is AccessResourceType.SERVER:
            checks = ((AccessAction.SERVER_ADMIN, request.resource),)
        elif request.resource.type is AccessResourceType.SCOPE:
            checks = (
                (AccessAction.SCOPE_ADMIN, request.resource),
                (AccessAction.SERVER_ADMIN, ResourceRef.server(self.deployment_id)),
            )
        else:
            raise AccessInvalidRequestError("action-resource")
        decisions = await self.require_any(actor, checks, context=context)
        policy_revision = next(
            (decision.policy_revision for decision in decisions if decision.allowed),
            None,
        )
        query = _audit_query_identity(request)
        decoded_after = self._decode_scoped_cursor(
            request.cursor,
            actor=actor,
            operation="access.audit.list",
            query=query,
            policy_revision=policy_revision,
        )
        try:
            after = None if decoded_after is None else int(decoded_after)
        except ValueError as error:
            raise AccessInvalidRequestError("cursor") from error
        rows = await _access_call(
            self.audit.list_audit(
                resource=request.resource,
                after=after,
                limit=request.limit + 1,
                action=request.action,
                subject=request.subject,
                allowed=request.allowed,
                occurred_after=request.occurred_after,
                occurred_before=request.occurred_before,
            )
        )
        items = rows[: request.limit]
        next_cursor = (
            self._encode_scoped_cursor(
                str(items[-1].cursor),
                actor=actor,
                operation="access.audit.list",
                query=query,
                policy_revision=policy_revision,
            )
            if len(rows) > len(items)
            else None
        )
        return AccessAuditPage(items=items, next_cursor=next_cursor)

    async def record_receipt_identity(self, identity: HandoffReceiptIdentity) -> HandoffReceiptIdentity:
        return await _access_call(self._relationship_writer().record_receipt_identity(identity))

    async def receipt_identity(self, scope_id: str, source_id: str) -> HandoffReceiptIdentity | None:
        return await _access_call(self._relationship_reader().get_receipt_identity(scope_id, source_id))

    async def establish_artifact_owner(
        self,
        resource: ResourceRef,
        owner: PrincipalRef,
        *,
        idempotency_key: str,
        context: AccessAuditContext,
    ) -> ArtifactOwnerRelation:
        if resource.type is not AccessResourceType.ARTIFACT:
            raise AccessInvalidRequestError("artifact-identity")
        _validate_idempotency_key(idempotency_key)
        relation = ArtifactOwnerRelation(
            resource=resource,
            owner=owner,
            established_at=self._clock(),
            policy_revision="pending",
            idempotency_key=idempotency_key,
        )
        established = await _access_call(self._relationship_writer().establish_artifact_owner(relation))
        await _access_call(self._record_owner(established, principal=owner, context=context))
        return established

    async def attest_candidate_owner(
        self,
        *,
        scope_id: str,
        candidate_id: str,
        family: str,
        proposed_owner: PrincipalRef,
        target: ResourceRef | None,
        idempotency_key: str,
    ) -> CandidateOwnerAttestation:
        _validate_idempotency_key(idempotency_key)
        attestation = CandidateOwnerAttestation(
            scope_id=scope_id,
            candidate_id=candidate_id,
            family=family,
            proposed_owner=proposed_owner,
            target=target,
            idempotency_key=idempotency_key,
        )
        return await _access_call(self._relationship_writer().attest_candidate_owner(attestation))

    async def candidate_owner(self, scope_id: str, candidate_id: str) -> CandidateOwnerAttestation | None:
        return await _access_call(self._relationship_reader().get_candidate_owner(scope_id, candidate_id))

    async def artifact_owner(self, resource: ResourceRef) -> ArtifactOwnerRelation | None:
        """Return owner metadata without reading the protected Artifact body."""

        if resource.type is not AccessResourceType.ARTIFACT:
            raise AccessInvalidRequestError("artifact-identity")
        return await _access_call(self._relationship_reader().get_artifact_owner(resource))

    async def create_binding(
        self,
        principal: PrincipalRef | None,
        request: CreateBinding,
        *,
        context: AccessAuditContext,
        validate_resource: Callable[[ResourceRef], Awaitable[None]] | None = None,
    ) -> AccessBinding:
        validate_binding_role(request.resource, request.role, deployment_id=self.deployment_id)
        validate_binding_subject(request.resource, request.role, request.subject.type)
        if isinstance(request.subject, GroupRef) and not self.provider_capabilities.group_subjects:
            raise AccessInvalidRequestError("group-subjects-unavailable")
        now = self._clock()
        if request.expires_at is not None and request.expires_at <= now:
            raise AccessInvalidRequestError("binding-expired")
        actor = _required_principal(principal)
        await self.require_any(
            actor,
            _administrative_checks(request.resource, deployment_id=self.deployment_id),
            context=context,
        )
        if (
            request.resource.type is AccessResourceType.ARTIFACT
            and await _access_call(self._relationship_reader().get_artifact_owner(request.resource)) is None
        ):
            raise AccessUnavailableError("artifact_owner_pending")
        if validate_resource is not None:
            await validate_resource(request.resource)
        candidate = AccessBinding(
            binding_id=str(uuid4()),
            subject=request.subject,
            resource=request.resource,
            role=request.role,
            granted_by=actor,
            reason=request.reason,
            created_at=now,
            expires_at=request.expires_at,
            state=AccessBindingState.ACTIVE,
            version=1,
            policy_revision="pending",
            idempotency_key=request.idempotency_key,
        )
        created = await _access_call(self._relationship_writer().create_binding(candidate))
        await _access_call(self._record_relationship(created, principal=actor, context=context))
        return created

    async def revoke_binding(
        self,
        principal: PrincipalRef | None,
        binding_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
        context: AccessAuditContext,
    ) -> AccessBinding:
        _validate_idempotency_key(idempotency_key)
        actor = _required_principal(principal)
        binding = await _access_call(self._relationship_reader().get_binding(binding_id))
        if binding is None:
            server = ResourceRef.server(self.deployment_id)
            decision = await self.check(actor, AccessAction.SERVER_ADMIN, server, context=context)
            if decision.allowed:
                raise AccessBindingNotFoundError
            raise AccessDeniedError
        await self.require_any(
            actor,
            _administrative_checks(binding.resource, deployment_id=self.deployment_id),
            context=context,
        )
        revoked = await _access_call(
            self._relationship_writer().revoke_binding(
                binding_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                revoked_at=self._clock(),
                revoked_by=actor,
            )
        )
        await _access_call(
            self._record_relationship(
                revoked,
                principal=actor,
                context=context,
                expected_version=expected_version,
            )
        )
        return revoked

    async def replace_binding(
        self,
        principal: PrincipalRef | None,
        request: ReplaceBinding,
        *,
        context: AccessAuditContext,
    ) -> BindingReplacement:
        actor = _required_principal(principal)
        current = await _access_call(self._relationship_reader().get_binding(request.binding_id))
        if current is None:
            decision = await self.check(
                actor,
                AccessAction.SERVER_ADMIN,
                ResourceRef.server(self.deployment_id),
                context=context,
            )
            if decision.allowed:
                raise AccessBindingNotFoundError
            raise AccessDeniedError
        validate_binding_subject(current.resource, current.role, request.subject.type)
        if isinstance(request.subject, GroupRef) and not self.provider_capabilities.group_subjects:
            raise AccessInvalidRequestError("group-subjects-unavailable")
        if request.expires_at is not None and request.expires_at <= self._clock():
            raise AccessInvalidRequestError("binding-expired")
        await self.require_any(
            actor,
            _administrative_checks(current.resource, deployment_id=self.deployment_id),
            context=context,
        )
        changed = await _access_call(
            self._relationship_writer().replace_binding(
                request,
                actor=actor,
                changed_at=self._clock(),
            )
        )
        await _access_call(
            self._record_relationship(
                changed.previous,
                principal=actor,
                context=context,
                expected_version=request.expected_version,
            )
        )
        await _access_call(self._record_relationship(changed.current, principal=actor, context=context))
        return changed

    def _relationship_reader(self) -> RelationshipReader:
        if self.relationships is None or not self.provider_capabilities.relationship_management:
            raise AccessUnavailableError("relationship_management_unavailable")
        return self.relationships

    def _relationship_writer(self) -> RelationshipWriter:
        if self.relationships is None or not self.provider_capabilities.relationship_management:
            raise AccessUnavailableError("relationship_management_unavailable")
        return self.relationships

    async def _record_decision(
        self,
        principal: PrincipalRef,
        action: AccessAction,
        resource: ResourceRef,
        decision: AccessDecision,
        *,
        context: AccessAuditContext,
    ) -> None:
        await self.audit.append_audit(
            AccessAuditEvent(
                cursor=None,
                event_id=str(uuid4()),
                occurred_at=self._clock(),
                request_id=context.request_id,
                transport=context.transport,
                operation=context.operation,
                principal=principal,
                actor=context.actor,
                action=action,
                resource=resource,
                allowed=decision.allowed,
                reason_code=decision.reason_code,
                policy_revision=decision.policy_revision,
                matched_subject=decision.matched_subject,
                binding_id=decision.matched_binding_id,
            )
        )

    async def _record_relationship(
        self,
        binding: AccessBinding,
        *,
        principal: PrincipalRef,
        context: AccessAuditContext,
        expected_version: int | None = None,
    ) -> None:
        await self.audit.append_audit(
            AccessAuditEvent(
                cursor=None,
                event_id=str(uuid4()),
                occurred_at=self._clock(),
                request_id=context.request_id,
                transport=context.transport,
                operation=context.operation,
                principal=principal,
                actor=context.actor,
                action=_administrative_checks(binding.resource, deployment_id=self.deployment_id)[0][0],
                resource=binding.resource,
                allowed=True,
                reason_code="binding-created" if binding.state is AccessBindingState.ACTIVE else "binding-revoked",
                policy_revision=binding.policy_revision,
                binding_id=binding.binding_id,
                target=binding.subject,
                role=binding.role,
                expected_version=expected_version,
                result_version=binding.version,
            )
        )

    async def _record_owner(
        self,
        relation: ArtifactOwnerRelation,
        *,
        principal: PrincipalRef,
        context: AccessAuditContext,
    ) -> None:
        await self.audit.append_audit(
            AccessAuditEvent(
                cursor=None,
                event_id=str(uuid4()),
                occurred_at=self._clock(),
                request_id=context.request_id,
                transport=context.transport,
                operation=context.operation,
                principal=principal,
                actor=context.actor,
                action=AccessAction.ARTIFACT_WRITE,
                resource=relation.resource,
                allowed=True,
                reason_code="artifact-owner-established",
                policy_revision=relation.policy_revision,
                target=relation.owner,
                role=AccessRole.ARTIFACT_OWNER,
            )
        )

    def _encode_cursor(
        self,
        after_key: str,
        *,
        actor: PrincipalRef,
        action: AccessAction,
        resource_type: AccessResourceType,
        family: str | None,
        policy_revision: str | None,
    ) -> str:
        payload = json.dumps(
            {
                "action": action.value,
                "after": after_key,
                "family": family,
                "policy_revision": policy_revision,
                "resource_type": resource_type.value,
                "subject": actor.id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.digest(self._cursor_secret, payload, "sha256")
        return urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def _decode_cursor(
        self,
        cursor: str | None,
        *,
        actor: PrincipalRef,
        action: AccessAction,
        resource_type: AccessResourceType,
        family: str | None,
        policy_revision: str | None,
    ) -> str | None:
        if cursor is None:
            return None
        decoded = _decode_signed_cursor(cursor, secret=self._cursor_secret)
        expected = {
            "action": action.value,
            "family": family,
            "resource_type": resource_type.value,
            "subject": actor.id,
        }
        if not isinstance(decoded, dict) or any(decoded.get(key) != value for key, value in expected.items()):
            raise AccessInvalidRequestError("cursor")
        if decoded.get("policy_revision") != policy_revision:
            raise AccessConflictError("access_cursor_stale")
        after = decoded.get("after")
        if not isinstance(after, str) or not after:
            raise AccessInvalidRequestError("cursor")
        return after

    def _encode_scoped_cursor(
        self,
        after: str,
        *,
        actor: PrincipalRef,
        operation: str,
        query: dict[str, object],
        policy_revision: str | None,
    ) -> str:
        payload = json.dumps(
            {
                "after": after,
                "operation": operation,
                "policy_revision": policy_revision,
                "query": query,
                "subject": actor.id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.digest(self._cursor_secret, payload, "sha256")
        return urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")

    def _decode_scoped_cursor(
        self,
        cursor: str | None,
        *,
        actor: PrincipalRef,
        operation: str,
        query: dict[str, object],
        policy_revision: str | None,
    ) -> str | None:
        if cursor is None:
            return None
        decoded = _decode_signed_cursor(cursor, secret=self._cursor_secret)
        if (
            not isinstance(decoded, dict)
            or decoded.get("operation") != operation
            or decoded.get("query") != query
            or decoded.get("subject") != actor.id
        ):
            raise AccessInvalidRequestError("cursor")
        if decoded.get("policy_revision") != policy_revision:
            raise AccessConflictError("access_cursor_stale")
        after = decoded.get("after")
        if not isinstance(after, str) or not after:
            raise AccessInvalidRequestError("cursor")
        return after


def _decode_signed_cursor(cursor: str, *, secret: bytes) -> object:
    try:
        padded = f"{cursor}{'=' * (-len(cursor) % 4)}"
        value = b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise AccessInvalidRequestError("cursor") from error
    payload, signature = value[:-32], value[-32:]
    if len(signature) != 32 or not hmac.compare_digest(signature, hmac.digest(secret, payload, "sha256")):
        raise AccessInvalidRequestError("cursor")
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise AccessInvalidRequestError("cursor") from error


def access_control_for_mode(access_control: AccessControlService | None, *, mode: str) -> AccessControlService | None:
    """Return the active PDP, failing closed when enforcement requires one."""

    if mode not in {"disabled", "enforced"}:
        raise AccessUnavailableError()
    if access_control is None and mode == "enforced":
        raise AccessUnavailableError()
    return access_control


def contextual_policy_revision(revision: str, groups: Sequence[GroupRef]) -> str:
    """Bind cursors and decisions to the trusted membership assertion."""

    if not groups:
        return revision
    identity = "\0".join(sorted(group.id for group in groups)).encode()
    membership = hashlib.sha256(identity).hexdigest()[:24]
    revision_prefix = revision[: MAX_POLICY_REVISION_LENGTH - len(membership) - 3]
    return f"{revision_prefix}:g:{membership}"


def _validate_resource_list_query(
    *,
    action: AccessAction,
    resource_type: AccessResourceType,
    family: str | None,
) -> None:
    if action is AccessAction.ACCESS_SELF or (family is not None and resource_type is not AccessResourceType.ARTIFACT):
        raise AccessInvalidRequestError("action-resource")
    allowed_actions = {
        AccessResourceType.SERVER: {AccessAction.SERVER_OBSERVE, AccessAction.SERVER_ADMIN},
        AccessResourceType.SCOPE: {
            AccessAction.SCOPE_READ,
            AccessAction.SCOPE_CONTRIBUTE,
            AccessAction.SCOPE_REVIEW,
            AccessAction.SCOPE_DELEGATE,
            AccessAction.SCOPE_ADMIN,
        },
    }
    if resource_type is not AccessResourceType.ARTIFACT:
        if action not in allowed_actions[resource_type]:
            raise AccessInvalidRequestError("action-resource")
        return
    if family is None:
        if not any(
            profile.enabled and action in profile.actions | profile.mutation_semantics | {AccessAction.ARTIFACT_SHARE}
            for profile in ARTIFACT_FAMILY_PROFILES.values()
        ):
            raise AccessInvalidRequestError("action-resource")
        return
    profile = ARTIFACT_FAMILY_PROFILES.get(family)
    if profile is None:
        raise AccessInvalidRequestError("artifact-family")
    if not profile.enabled:
        raise AccessInvalidRequestError("artifact-family-disabled")
    if action not in profile.actions | profile.mutation_semantics | {AccessAction.ARTIFACT_SHARE}:
        raise AccessInvalidRequestError("action-resource")


def _binding_grants(binding: AccessBinding, action: AccessAction, requested: ResourceRef) -> bool:
    if binding.resource == requested:
        return action in ROLE_ACTIONS[binding.role]
    return _parent_binding_grants(binding, action, requested.type) and _binding_covers(binding.resource, requested)


def _parent_binding_grants(
    binding: AccessBinding,
    action: AccessAction,
    requested_type: AccessResourceType,
) -> bool:
    if action not in ROLE_CHILD_ACTIONS.get(binding.role, frozenset()):
        return False
    if binding.resource.type is AccessResourceType.SCOPE:
        return requested_type is AccessResourceType.ARTIFACT
    if binding.resource.type is AccessResourceType.SERVER:
        return requested_type in {AccessResourceType.SCOPE, AccessResourceType.ARTIFACT}
    return False


def _binding_covers(binding: ResourceRef, requested: ResourceRef) -> bool:
    if binding == requested:
        return True
    if binding.type is AccessResourceType.SERVER:
        return True
    return (
        binding.type is AccessResourceType.SCOPE
        and requested.type is AccessResourceType.ARTIFACT
        and binding.scope_id == requested.scope_id
    )


def _binding_decision(
    bindings: Sequence[AccessBinding],
    action: AccessAction,
    resource: ResourceRef,
    *,
    policy_revision: str,
) -> AccessDecision:
    for binding in bindings:
        if _binding_grants(binding, action, resource):
            return AccessDecision(
                True,
                "role-binding",
                policy_revision,
                matched_subject=binding.subject,
                matched_binding_id=binding.binding_id,
            )
    return AccessDecision(False, "no-matching-binding", policy_revision)


def _administrative_checks(
    resource: ResourceRef,
    *,
    deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    if resource.type is AccessResourceType.SERVER:
        return ((AccessAction.SERVER_ADMIN, resource),)
    if resource.type is AccessResourceType.SCOPE:
        return (
            (AccessAction.SCOPE_ADMIN, resource),
            (AccessAction.SERVER_ADMIN, ResourceRef.server(deployment_id)),
        )
    parent = resource.parent_scope
    if parent is None:
        raise AccessInvalidRequestError("artifact-identity")
    checks = [(AccessAction.ARTIFACT_SHARE, resource), (AccessAction.SCOPE_ADMIN, parent)]
    if artifact_family_profile(resource).family == "handoff":
        checks.append((AccessAction.SCOPE_DELEGATE, parent))
    checks.append((AccessAction.SERVER_ADMIN, ResourceRef.server(deployment_id)))
    return tuple(checks)


def _binding_list_checks(
    resource: ResourceRef,
    *,
    deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    if resource.type is AccessResourceType.SERVER:
        return ((AccessAction.SERVER_ADMIN, resource),)
    if resource.type is AccessResourceType.SCOPE:
        return (
            (AccessAction.SCOPE_DELEGATE, resource),
            (AccessAction.SCOPE_ADMIN, resource),
            (AccessAction.SERVER_ADMIN, ResourceRef.server(deployment_id)),
        )
    return _administrative_checks(resource, deployment_id=deployment_id)


def _binding_query_identity(
    request: BindingSearchRequest,
    *,
    visible_roles: tuple[AccessRole, ...],
) -> dict[str, object]:
    return {
        "management_resource": request.management_resource.key,
        "role": None if request.role is None else request.role.value,
        "state": None if request.state is None else request.state.value,
        "subject": None if request.subject is None else [request.subject.type, request.subject.id],
        "visible_roles": [role.value for role in visible_roles],
    }


def _audit_query_identity(request: AuditSearchRequest) -> dict[str, object]:
    return {
        "action": None if request.action is None else request.action.value,
        "allowed": request.allowed,
        "resource": request.resource.key,
        "subject": None if request.subject is None else [request.subject.type, request.subject.id],
        "time_range": [
            None if request.occurred_after is None else request.occurred_after.isoformat(),
            None if request.occurred_before is None else request.occurred_before.isoformat(),
        ],
    }


def _resource_allowed_by_filter(resource: ResourceRef, value: AuthorizedResourceFilter) -> bool:
    return resource in value.exact_resources or any(
        _binding_covers(parent, resource) for parent in value.parent_constraints
    )


def _resource_is_parent(resource: ResourceRef, child_type: AccessResourceType) -> bool:
    if resource.type is AccessResourceType.SERVER:
        return child_type is not AccessResourceType.SERVER
    return resource.type is AccessResourceType.SCOPE and child_type is AccessResourceType.ARTIFACT


def _validate_resource_filter(
    value: AuthorizedResourceFilter,
    *,
    action: AccessAction,
    resource_type: AccessResourceType,
    family: str | None,
    deployment_id: str,
    provider_limit: int,
) -> None:
    if not isinstance(value, AuthorizedResourceFilter) or not value.complete:
        raise AccessUnavailableError("safe_resource_filtering_unavailable")
    limit = min(provider_limit, value.max_direct_resource_keys, _MAX_AUTHORIZED_FILTER_IDENTITIES)
    if limit < 1 or len(value.exact_resources) > limit:
        raise AccessUnavailableError("resource_filter_limit_exceeded")
    if len(value.parent_constraints) > _MAX_AUTHORIZED_FILTER_IDENTITIES:
        raise AccessUnavailableError("safe_resource_filtering_unavailable")
    if len({resource.key for resource in value.exact_resources}) != len(value.exact_resources):
        raise AccessUnavailableError("safe_resource_filtering_unavailable")
    for resource in value.exact_resources:
        if resource.type is not resource_type or (family is not None and resource.family != family):
            raise AccessUnavailableError("safe_resource_filtering_unavailable")
        validate_action_resource(action, resource, deployment_id=deployment_id)
    for resource in value.parent_constraints:
        if not _resource_is_parent(resource, resource_type):
            raise AccessUnavailableError("safe_resource_filtering_unavailable")


def _validate_provider_decision(value: object) -> None:
    if not isinstance(value, AccessDecision) or not isinstance(value.allowed, bool):
        raise AccessUnavailableError()
    reason = value.reason_code
    if (
        not reason
        or len(reason) > 64
        or not reason[0].isalnum()
        or any(not character.isascii() or not (character.isalnum() or character in "._-") for character in reason)
    ):
        raise AccessUnavailableError()
    if value.policy_revision is not None and (
        not value.policy_revision or len(value.policy_revision) > MAX_POLICY_REVISION_LENGTH
    ):
        raise AccessUnavailableError()
    if value.matched_subject is not None and not value.allowed:
        raise AccessUnavailableError()


def _required_principal(principal: PrincipalRef | None) -> PrincipalRef:
    if principal is None:
        raise AccessIdentityRequiredError
    return principal


def _validate_idempotency_key(value: str) -> None:
    if not value or len(value) > 255 or value != value.strip():
        raise AccessInvalidRequestError("idempotency-key")


async def _access_call(awaitable: Awaitable[_T]) -> _T:
    try:
        return await awaitable
    except AccessControlError:
        raise
    except Exception as error:
        raise AccessUnavailableError() from error


__all__ = (
    "AccessAuditContext",
    "AccessAuditPage",
    "AccessAuditStore",
    "AccessBindingPage",
    "AccessControlService",
    "AccessProviderCapabilities",
    "AccessRepository",
    "AccessRequest",
    "AuditSearchRequest",
    "AuthorizationProvider",
    "AuthorizedResourceFilter",
    "AuthorizedResourcePage",
    "BindingReplacement",
    "BindingSearchRequest",
    "BuiltinAuthorizationProvider",
    "CreateBinding",
    "RelationshipReader",
    "RelationshipStore",
    "RelationshipWriter",
    "ReplaceBinding",
    "ResourceSearchRequest",
    "access_control_for_mode",
)
