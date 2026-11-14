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

"""Embedded Casbin decision adapter over canonical Access relationships."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import casbin

from powercontext.builtin.authz.errors import AccessInvalidRequestError
from powercontext.builtin.authz.models import (
    DEFAULT_DEPLOYMENT_ID,
    ROLE_ACTIONS,
    ROLE_CHILD_ACTIONS,
    AccessAction,
    AccessBinding,
    AccessDecision,
    AccessResourceType,
    AccessRole,
    GroupRef,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.service import (
    AccessRepository,
    AccessRequest,
    AuthorizedResourceFilter,
    ResourceSearchRequest,
    _derive_authorized_resource_filter,
    contextual_policy_revision,
    read_decision_state,
)

_MODEL = """
[request_definition]
r = act, obj, scope, deployment

[policy_definition]
p = act, obj, scope, deployment

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = r.act == p.act && (p.obj == "*" || r.obj == p.obj) && (p.scope == "*" || r.scope == p.scope) && r.deployment == p.deployment
"""


class CasbinAuthorizationProvider:
    """Evaluate only active direct/group relationships with a fresh Casbin enforcer."""

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

    def with_repository(self, repository: AccessRepository) -> CasbinAuthorizationProvider:
        """Preserve the fixed policy while binding reads to the caller's transaction."""

        return CasbinAuthorizationProvider(repository, deployment_id=self._deployment_id, clock=self._clock)

    async def check(self, request: AccessRequest, /) -> AccessDecision:
        return (await self.check_batch((request,)))[0]

    async def prepare_point_decisions(
        self,
        principal: PrincipalRef,
        groups: tuple[GroupRef, ...],
        resources: Sequence[ResourceRef],
        *,
        at: datetime | None = None,
    ) -> Callable[[AccessRequest], AccessDecision]:
        """Pin policy relations once and evaluate expiry for every point decision."""
        observed_at = self._clock() if at is None else at
        state = await read_decision_state(
            self._repository,
            (principal, *groups),
            now=observed_at,
            binding_resources=resources,
            artifact_resources=tuple(
                resource for resource in resources if resource.type is AccessResourceType.ARTIFACT
            ),
        )
        revision = contextual_policy_revision(state.policy_revision, groups)

        def decide(request: AccessRequest) -> AccessDecision:
            if request.action is AccessAction.ACCESS_SELF:
                return AccessDecision(True, "authenticated", revision)
            now = self._clock() if at is None else at
            bindings = tuple(binding for binding in state.bindings if binding.active_at(now))
            matched = _matching_binding(bindings, request.action, request.resource)
            owner = (
                state.artifact_owners.get(request.resource.key)
                if request.resource.type is AccessResourceType.ARTIFACT
                else None
            )
            owner_allow = (
                owner is not None
                and owner.owner == principal
                and request.action in ROLE_ACTIONS[AccessRole.ARTIFACT_OWNER]
            )
            enforcer = _enforcer(
                bindings,
                owner_allow=owner_allow,
                requested=request.resource,
                deployment_id=self._deployment_id,
            )
            allowed = bool(enforcer.enforce(*_casbin_request(request, self._deployment_id)))
            return AccessDecision(
                allowed,
                "casbin-policy" if allowed else "no-matching-policy",
                revision,
                matched_subject=(matched.subject if matched is not None else principal if owner_allow else None),
                matched_binding_id=None if matched is None else matched.binding_id,
            )

        return decide

    async def check_batch(self, requests: Sequence[AccessRequest], /) -> tuple[AccessDecision, ...]:
        if not requests:
            return ()
        principal = requests[0].subject
        if any(request.subject != principal for request in requests):
            raise AccessInvalidRequestError("batch-subject")
        decide = await self.prepare_point_decisions(
            principal,
            requests[0].context.subject_groups,
            tuple(request.resource for request in requests),
            at=self._clock(),
        )
        return tuple(decide(request) for request in requests)

    async def resolve_resource_filter(self, request: ResourceSearchRequest, /) -> AuthorizedResourceFilter:
        owned_by = (
            request.subject
            if request.resource_type is AccessResourceType.ARTIFACT
            and request.action in ROLE_ACTIONS[AccessRole.ARTIFACT_OWNER]
            else None
        )
        state = await read_decision_state(
            self._repository,
            (request.subject, *request.context.subject_groups),
            now=self._clock(),
            owned_by=owned_by,
        )
        revision = contextual_policy_revision(state.policy_revision, request.context.subject_groups)
        return _derive_authorized_resource_filter(
            bindings=state.bindings,
            owned_resources=state.owned_resources,
            request=request,
            policy_revision=revision,
        )


def _enforcer(
    bindings: Sequence[AccessBinding],
    *,
    owner_allow: bool,
    requested: ResourceRef,
    deployment_id: str,
) -> casbin.Enforcer:
    model = casbin.Model()
    model.load_model_from_text(_MODEL)
    enforcer = casbin.Enforcer(model)
    policies: list[list[str]] = []
    for binding in bindings:
        resource = binding.resource
        policies.extend(
            [action.value, resource.key, resource.scope_id or "", resource.deployment_id or deployment_id]
            for action in ROLE_ACTIONS[binding.role]
        )
        policies.extend(
            [action.value, "*", resource.scope_id or "*", resource.deployment_id or deployment_id]
            for action in ROLE_CHILD_ACTIONS.get(binding.role, frozenset())
        )
    if owner_allow:
        policies.append([
            AccessAction.ARTIFACT_READ.value,
            requested.key,
            requested.scope_id or "",
            requested.deployment_id or deployment_id,
        ])
        for action in ROLE_ACTIONS[AccessRole.ARTIFACT_OWNER] - {AccessAction.ARTIFACT_READ}:
            policies.append([
                action.value,
                requested.key,
                requested.scope_id or "",
                requested.deployment_id or deployment_id,
            ])
    if policies:
        enforcer.add_policies(policies)
    return enforcer


def _casbin_request(request: AccessRequest, deployment_id: str) -> tuple[str, str, str, str]:
    resource = request.resource
    return (
        request.action.value,
        resource.key,
        resource.scope_id or "",
        resource.deployment_id or deployment_id,
    )


def _matching_binding(
    bindings: Sequence[AccessBinding],
    action: AccessAction,
    resource: ResourceRef,
) -> AccessBinding | None:
    for binding in bindings:
        if binding.resource == resource and action in ROLE_ACTIONS[binding.role]:
            return binding
        if _covers(binding.resource, resource) and action in ROLE_CHILD_ACTIONS.get(binding.role, frozenset()):
            return binding
    return None


def _covers(parent: ResourceRef, child: ResourceRef) -> bool:
    return parent.type is AccessResourceType.SERVER or (
        parent.type is AccessResourceType.SCOPE
        and child.type is AccessResourceType.ARTIFACT
        and parent.scope_id == child.scope_id
    )


__all__ = ("CasbinAuthorizationProvider",)
