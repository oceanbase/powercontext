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

"""Server-owned Artifact Family Access Profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from powercontext.server.authz.errors import AccessInvalidRequestError
from powercontext.server.authz.models import (
    ROLE_SUBJECT_TYPES,
    AccessAction,
    AccessResourceType,
    AccessRole,
    ResourceRef,
)


@dataclass(frozen=True, slots=True)
class ArtifactFamilyAccessProfile:
    """Fixed authorization semantics for one registered Artifact Family."""

    family: str
    enabled: bool
    share_unit: Literal["artifact", "memory_entry"]
    shareable_states: frozenset[str]
    base_action: AccessAction
    additional_actions: frozenset[AccessAction]
    grantable_roles: frozenset[AccessRole]
    selector: Literal["forbidden", "memory_entry"]
    transitivity: Literal["none", "manifest"] = "none"
    mutation_semantics: frozenset[AccessAction] = frozenset()

    @property
    def actions(self) -> frozenset[AccessAction]:
        return frozenset({self.base_action, *self.additional_actions})

    @property
    def subject_compatibility(self) -> dict[AccessRole, frozenset[str]]:
        return {role: ROLE_SUBJECT_TYPES[role] for role in self.grantable_roles}


ARTIFACT_FAMILY_PROFILES: dict[str, ArtifactFamilyAccessProfile] = {
    "handoff": ArtifactFamilyAccessProfile(
        family="handoff",
        enabled=True,
        share_unit="artifact",
        shareable_states=frozenset({"committed"}),
        base_action=AccessAction.ARTIFACT_READ,
        additional_actions=frozenset({
            AccessAction.HANDOFF_EVIDENCE_INSPECT,
            AccessAction.HANDOFF_ACKNOWLEDGE,
        }),
        grantable_roles=frozenset({AccessRole.HANDOFF_VIEWER, AccessRole.HANDOFF_RECEIVER}),
        selector="forbidden",
        transitivity="manifest",
        mutation_semantics=frozenset({AccessAction.ARTIFACT_WRITE}),
    ),
    "memory": ArtifactFamilyAccessProfile(
        family="memory",
        enabled=True,
        share_unit="memory_entry",
        shareable_states=frozenset({"active", "retired"}),
        base_action=AccessAction.ARTIFACT_READ,
        additional_actions=frozenset(),
        grantable_roles=frozenset({AccessRole.ARTIFACT_VIEWER}),
        selector="memory_entry",
        mutation_semantics=frozenset({AccessAction.ARTIFACT_WRITE}),
    ),
    "experience": ArtifactFamilyAccessProfile(
        family="experience",
        enabled=True,
        share_unit="artifact",
        shareable_states=frozenset({"approved"}),
        base_action=AccessAction.ARTIFACT_READ,
        additional_actions=frozenset(),
        grantable_roles=frozenset({AccessRole.ARTIFACT_VIEWER}),
        selector="forbidden",
        mutation_semantics=frozenset({AccessAction.ARTIFACT_WRITE}),
    ),
    "skill": ArtifactFamilyAccessProfile(
        family="skill",
        enabled=True,
        share_unit="artifact",
        shareable_states=frozenset({"approved"}),
        base_action=AccessAction.ARTIFACT_READ,
        additional_actions=frozenset(),
        grantable_roles=frozenset({AccessRole.ARTIFACT_VIEWER}),
        selector="forbidden",
        mutation_semantics=frozenset({AccessAction.ARTIFACT_WRITE}),
    ),
    # Prompt authorization vocabulary is reserved, but this deployment does not yet
    # implement an immutable approved Prompt lifecycle or exact get/use operations.
    "prompt": ArtifactFamilyAccessProfile(
        family="prompt",
        enabled=False,
        share_unit="artifact",
        shareable_states=frozenset({"approved"}),
        base_action=AccessAction.ARTIFACT_READ,
        additional_actions=frozenset({AccessAction.PROMPT_USE}),
        grantable_roles=frozenset(),
        selector="forbidden",
        mutation_semantics=frozenset({AccessAction.ARTIFACT_WRITE}),
    ),
}


def artifact_family_profile(resource: ResourceRef) -> ArtifactFamilyAccessProfile:
    """Validate an exact Artifact resource and return its enabled profile."""

    if resource.type is not AccessResourceType.ARTIFACT or resource.identity is None:
        raise AccessInvalidRequestError("artifact-identity")
    profile = ARTIFACT_FAMILY_PROFILES.get(resource.identity.family)
    if profile is None:
        raise AccessInvalidRequestError("artifact-family")
    if not profile.enabled:
        raise AccessInvalidRequestError("artifact-family-disabled")
    if profile.selector == "memory_entry" and resource.selector is None:
        raise AccessInvalidRequestError("memory-entry-selector")
    if profile.selector == "forbidden" and resource.selector is not None:
        raise AccessInvalidRequestError("artifact-selector")
    return profile


def validate_action_resource(action: AccessAction, resource: ResourceRef, *, deployment_id: str) -> None:
    """Reject action/resource combinations outside the stable wire contract."""

    if resource.type is AccessResourceType.SERVER:
        if resource.deployment_id != deployment_id:
            raise AccessInvalidRequestError("deployment")
        if action not in {AccessAction.ACCESS_SELF, AccessAction.SERVER_OBSERVE, AccessAction.SERVER_ADMIN}:
            raise AccessInvalidRequestError("action-resource")
        return
    if resource.type is AccessResourceType.SCOPE:
        if action not in {
            AccessAction.SCOPE_READ,
            AccessAction.SCOPE_CONTRIBUTE,
            AccessAction.SCOPE_REVIEW,
            AccessAction.SCOPE_DELEGATE,
            AccessAction.SCOPE_ADMIN,
        }:
            raise AccessInvalidRequestError("action-resource")
        return
    profile = artifact_family_profile(resource)
    if action not in profile.actions | profile.mutation_semantics | {AccessAction.ARTIFACT_SHARE}:
        raise AccessInvalidRequestError("action-resource")


def validate_binding_role(resource: ResourceRef, role: AccessRole, *, deployment_id: str) -> None:
    """Reject role/resource and role/Family mismatches before policy mutation."""

    if resource.type is AccessResourceType.SERVER:
        if resource.deployment_id != deployment_id or role not in {AccessRole.SERVER_OBSERVER, AccessRole.SERVER_ADMIN}:
            raise AccessInvalidRequestError("binding-role")
        return
    if resource.type is AccessResourceType.SCOPE:
        if role not in {
            AccessRole.SCOPE_VIEWER,
            AccessRole.SCOPE_CONTRIBUTOR,
            AccessRole.SCOPE_REVIEWER,
            AccessRole.SCOPE_DELEGATOR,
            AccessRole.SCOPE_ADMIN,
        }:
            raise AccessInvalidRequestError("binding-role")
        return
    profile = artifact_family_profile(resource)
    if role not in profile.grantable_roles:
        raise AccessInvalidRequestError("binding-role")


def validate_binding_subject(resource: ResourceRef, role: AccessRole, subject_type: str) -> None:
    """Reject subject kinds a role cannot represent."""

    if role is AccessRole.ARTIFACT_OWNER:
        raise AccessInvalidRequestError("binding-role")
    if subject_type not in ROLE_SUBJECT_TYPES[role]:
        raise AccessInvalidRequestError("binding-subject")
    if resource.type is AccessResourceType.ARTIFACT:
        profile = artifact_family_profile(resource)
        if subject_type not in profile.subject_compatibility[role]:
            raise AccessInvalidRequestError("binding-subject")


__all__ = (
    "ARTIFACT_FAMILY_PROFILES",
    "ArtifactFamilyAccessProfile",
    "artifact_family_profile",
    "validate_action_resource",
    "validate_binding_role",
    "validate_binding_subject",
)
