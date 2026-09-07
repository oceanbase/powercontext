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

from powercontext.server.authz.errors import AccessInvalidRequestError
from powercontext.server.authz.models import (
    DEFAULT_DEPLOYMENT_ID,
    ROLE_ACTIONS,
    ROLE_CHILD_ACTIONS,
    AccessAction,
    AccessBinding,
    AccessDecision,
    AccessResourceType,
    AccessRole,
    AccessSubjectRef,
    ResourceRef,
)
from powercontext.server.authz.service import (
    AccessRepository,
    AccessRequest,
    AuthorizedResourceFilter,
    ResourceSearchRequest,
    contextual_policy_revision,
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

    async def check(self, request: AccessRequest, /) -> AccessDecision:
        return (await self.check_batch((request,)))[0]

    async def check_batch(self, requests: Sequence[AccessRequest], /) -> tuple[AccessDecision, ...]:
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
            matched = _matching_binding(bindings, request.action, request.resource)
            owner = (
                await self._repository.get_artifact_owner(request.resource)
                if request.resource.type is AccessResourceType.ARTIFACT
                else None
            )
            if request.resource.type is AccessResourceType.ARTIFACT and owner is None:
                reason = "artifact-owner-pending" if matched is not None else "no-matching-policy"
                decisions.append(AccessDecision(False, reason, revision))
                continue
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
            decisions.append(
                AccessDecision(
                    allowed,
                    "casbin-policy" if allowed else "no-matching-policy",
                    revision,
                    matched_subject=(principal if owner_allow else None if matched is None else matched.subject),
                    matched_binding_id=None if matched is None else matched.binding_id,
                )
            )
        return tuple(decisions)

    async def resolve_resource_filter(self, request: ResourceSearchRequest, /) -> AuthorizedResourceFilter:
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
            elif _is_parent(resource, request.resource_type) and request.action in ROLE_CHILD_ACTIONS.get(
                binding.role, frozenset()
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
            max_direct_resource_keys=10_000,
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


def _is_parent(resource: ResourceRef, requested_type: AccessResourceType) -> bool:
    return resource.type is AccessResourceType.SERVER or (
        resource.type is AccessResourceType.SCOPE and requested_type is AccessResourceType.ARTIFACT
    )


__all__ = ("CasbinAuthorizationProvider",)
