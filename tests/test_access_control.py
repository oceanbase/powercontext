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

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from sqlalchemy import insert

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.server.authz import (
    ROLE_CARDINALITIES,
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessConflictError,
    AccessControlService,
    AccessDeniedError,
    AccessInvalidRequestError,
    AccessProviderCapabilities,
    AccessResourceType,
    AccessRole,
    AccessRoleCardinality,
    AccessUnavailableError,
    BuiltinAuthorizationProvider,
    CreateBinding,
    GroupRef,
    MemoryEntrySelector,
    PrincipalRef,
    ReplaceBinding,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.repository import (
    ACCESS_BINDING_LEASES_TABLE,
    ACCESS_TABLES,
    RelationalAccessRepository,
)

ADMIN = PrincipalRef(type="service", id="admin", description="deployment administrator")
ALICE = PrincipalRef(type="user", id="alice", description="artifact owner")
BOB = PrincipalRef(type="user", id="bob")
TEAM = GroupRef(type="group", id="team-platform", description="Platform team")
AUDIT = AccessAuditContext(transport="http", operation="test", request_id="req-1")


def test_logical_artifact_share_is_read_only_across_all_versions() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            skill = ResourceRef.artifact("scope-a", family="skill", artifact_id="skill-a")
            owner = await service.establish_artifact_owner(skill, ALICE, idempotency_key="owner-skill-a", context=AUDIT)
            assert owner.owner == ALICE

            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=skill,
                    role=AccessRole.ARTIFACT_VIEWER,
                    idempotency_key="share-skill-a-with-bob",
                ),
                context=AUDIT,
            )

            assert (await service.require(BOB, AccessAction.ARTIFACT_READ, skill, context=AUDIT)).allowed
            with pytest.raises(AccessDeniedError):
                await service.require(BOB, AccessAction.ARTIFACT_WRITE, skill, context=AUDIT)
            assert (await service.require(ALICE, AccessAction.ARTIFACT_WRITE, skill, context=AUDIT)).allowed
            assert (await service.require(ALICE, AccessAction.ARTIFACT_SHARE, skill, context=AUDIT)).allowed
            assert "revision" not in skill.key

    asyncio.run(scenario())


def test_memory_share_targets_one_logical_entry_without_entry_version() -> None:
    entry = ResourceRef.artifact(
        "scope-a",
        family="memory",
        artifact_id="memory",
        selector=MemoryEntrySelector(entry_id="entry-a"),
    )
    assert entry.selector == MemoryEntrySelector(entry_id="entry-a")
    assert "entry_version_id" not in entry.key


def test_administration_roles_do_not_become_content_writers() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            handoff = ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff")
            await service.establish_artifact_owner(handoff, ALICE, idempotency_key="owner-handoff-a", context=AUDIT)
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=ResourceRef.scope("scope-a"),
                    role=AccessRole.SCOPE_ADMIN,
                    idempotency_key="scope-admin-bob",
                ),
                context=AUDIT,
            )

            assert (await service.require(BOB, AccessAction.ARTIFACT_SHARE, handoff, context=AUDIT)).allowed
            for action in (AccessAction.ARTIFACT_READ, AccessAction.ARTIFACT_WRITE):
                with pytest.raises(AccessDeniedError):
                    await service.require(BOB, action, handoff, context=AUDIT)

    asyncio.run(scenario())


def test_group_binding_is_inherited_from_trusted_authentication_context() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_server_admin(repository)
            provider = BuiltinAuthorizationProvider(repository)
            service = AccessControlService(
                provider,
                relationships=repository,
                audit=repository,
                provider_capabilities=AccessProviderCapabilities(
                    safe_resource_filtering=True,
                    multi_requirement_check=True,
                    relationship_management=True,
                    group_subjects=True,
                ),
            )
            handoff = ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff")
            await service.establish_artifact_owner(handoff, ALICE, idempotency_key="owner-handoff-group", context=AUDIT)
            binding = await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=TEAM,
                    resource=handoff,
                    role=AccessRole.HANDOFF_VIEWER,
                    idempotency_key="share-handoff-with-team",
                ),
                context=AUDIT,
            )
            grouped = AccessAuditContext(
                transport="http",
                operation="test",
                request_id="req-group",
                subject_groups=(TEAM,),
            )
            decision = await service.require(BOB, AccessAction.ARTIFACT_READ, handoff, context=grouped)
            assert decision.matched_subject == TEAM
            assert decision.matched_binding_id == binding.binding_id

            with pytest.raises(AccessInvalidRequestError, match="subject"):
                await service.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=TEAM,
                        resource=handoff,
                        role=AccessRole.HANDOFF_RECEIVER,
                        idempotency_key="invalid-group-receiver",
                    ),
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_singleton_binding_replacement_is_atomic() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            handoff = ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff")
            await service.establish_artifact_owner(
                handoff, ALICE, idempotency_key="owner-handoff-receiver", context=AUDIT
            )
            first = await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=handoff,
                    role=AccessRole.HANDOFF_RECEIVER,
                    idempotency_key="receiver-bob",
                ),
                context=AUDIT,
            )
            with pytest.raises(AccessConflictError, match="maximum active Bindings"):
                await service.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=ALICE,
                        resource=handoff,
                        role=AccessRole.HANDOFF_RECEIVER,
                        idempotency_key="receiver-alice-conflict",
                    ),
                    context=AUDIT,
                )

            request = ReplaceBinding(
                binding_id=first.binding_id,
                expected_version=1,
                subject=ALICE,
                idempotency_key="reassign-to-alice",
            )
            changed = await service.replace_binding(ADMIN, request, context=AUDIT)
            replayed = await service.replace_binding(ADMIN, request, context=AUDIT)
            assert changed.previous.state is AccessBindingState.REVOKED
            assert changed.current.subject == ALICE
            assert changed.current.resource == handoff
            assert changed.current.role is AccessRole.HANDOFF_RECEIVER
            assert replayed == changed
            with pytest.raises(AccessDeniedError):
                await service.require(BOB, AccessAction.HANDOFF_ACKNOWLEDGE, handoff, context=AUDIT)
            assert (await service.require(ALICE, AccessAction.HANDOFF_ACKNOWLEDGE, handoff, context=AUDIT)).allowed

            await service.revoke_binding(
                ADMIN,
                changed.current.binding_id,
                expected_version=1,
                idempotency_key="revoke-alice-receiver",
                context=AUDIT,
            )
            replacement = await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=handoff,
                    role=AccessRole.HANDOFF_RECEIVER,
                    idempotency_key="receiver-bob-again",
                ),
                context=AUDIT,
            )
            assert replacement.subject == BOB

    asyncio.run(scenario())


def test_singleton_binding_fails_closed_when_a_claimed_lease_binding_is_not_visible() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_server_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            handoff = ResourceRef.artifact("scope-a", family="handoff", artifact_id="concurrent-handoff")
            await service.establish_artifact_owner(
                handoff,
                ALICE,
                idempotency_key="owner-concurrent-handoff",
                context=AUDIT,
            )
            async with profile.database.transaction() as connection:
                await connection.execute(
                    insert(ACCESS_BINDING_LEASES_TABLE).values(
                        resource_key_hash=sha256(handoff.key.encode()).hexdigest(),
                        role=AccessRole.HANDOFF_RECEIVER.value,
                        binding_id="not-yet-visible-binding",
                    )
                )

            with pytest.raises(AccessConflictError, match="maximum active Bindings"):
                await service.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=BOB,
                        resource=handoff,
                        role=AccessRole.HANDOFF_RECEIVER,
                        idempotency_key="concurrent-handoff-receiver",
                    ),
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_access_role_cardinalities_are_explicit() -> None:
    assert ROLE_CARDINALITIES[AccessRole.HANDOFF_RECEIVER] is AccessRoleCardinality.ONE_PER_RESOURCE
    assert ROLE_CARDINALITIES[AccessRole.ARTIFACT_OWNER] is AccessRoleCardinality.ONE_PER_RESOURCE
    assert ROLE_CARDINALITIES[AccessRole.ARTIFACT_VIEWER] is AccessRoleCardinality.MANY_PER_RESOURCE


def test_many_binding_can_be_replaced_without_domain_specific_behavior() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            skill = ResourceRef.artifact("scope-a", family="skill", artifact_id="skill-a")
            await service.establish_artifact_owner(skill, ALICE, idempotency_key="owner-replaced-skill", context=AUDIT)
            original = await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=skill,
                    role=AccessRole.ARTIFACT_VIEWER,
                    idempotency_key="viewer-bob",
                ),
                context=AUDIT,
            )

            changed = await service.replace_binding(
                ADMIN,
                ReplaceBinding(
                    binding_id=original.binding_id,
                    expected_version=1,
                    subject=ALICE,
                    idempotency_key="replace-viewer-with-alice",
                ),
                context=AUDIT,
            )

            assert changed.previous.state is AccessBindingState.REVOKED
            assert changed.current.subject == ALICE
            assert changed.current.resource == skill
            assert changed.current.role is AccessRole.ARTIFACT_VIEWER

    asyncio.run(scenario())


def test_resource_cursor_is_bound_to_policy_revision() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            resources = tuple(
                ResourceRef.artifact("scope-a", family="skill", artifact_id=f"skill-{index}") for index in range(3)
            )
            for index, resource in enumerate(resources):
                await service.establish_artifact_owner(resource, ALICE, idempotency_key=f"owner-{index}", context=AUDIT)
                await service.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=BOB,
                        resource=resource,
                        role=AccessRole.ARTIFACT_VIEWER,
                        idempotency_key=f"share-{index}",
                    ),
                    context=AUDIT,
                )

            first = await service.list_resources(
                BOB,
                action=AccessAction.ARTIFACT_READ,
                resource_type=AccessResourceType.ARTIFACT,
                family="skill",
                limit=2,
                context=AUDIT,
            )
            assert len(first.items) == 2
            assert first.total == 3
            assert first.next_cursor is not None

            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=ALICE,
                    resource=ResourceRef.server(),
                    role=AccessRole.SERVER_OBSERVER,
                    idempotency_key="advance-policy-revision",
                ),
                context=AUDIT,
            )
            with pytest.raises(AccessConflictError, match="older policy revision"):
                await service.list_resources(
                    BOB,
                    action=AccessAction.ARTIFACT_READ,
                    resource_type=AccessResourceType.ARTIFACT,
                    family="skill",
                    cursor=first.next_cursor,
                    limit=2,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_resource_cursor_is_bound_to_trusted_group_membership() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_server_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
                provider_capabilities=AccessProviderCapabilities(
                    safe_resource_filtering=True,
                    multi_requirement_check=True,
                    relationship_management=True,
                    group_subjects=True,
                ),
            )
            for index in range(2):
                resource = ResourceRef.artifact("scope-a", family="skill", artifact_id=f"team-skill-{index}")
                await service.establish_artifact_owner(
                    resource,
                    ALICE,
                    idempotency_key=f"team-owner-{index}",
                    context=AUDIT,
                )
                await service.create_binding(
                    ADMIN,
                    CreateBinding(
                        subject=TEAM,
                        resource=resource,
                        role=AccessRole.ARTIFACT_VIEWER,
                        idempotency_key=f"team-share-{index}",
                    ),
                    context=AUDIT,
                )

            grouped = AccessAuditContext(transport="http", operation="test", subject_groups=(TEAM,))
            first = await service.list_resources(
                BOB,
                action=AccessAction.ARTIFACT_READ,
                resource_type=AccessResourceType.ARTIFACT,
                family="skill",
                limit=1,
                context=grouped,
            )
            assert first.next_cursor is not None

            with pytest.raises(AccessConflictError, match="older policy revision"):
                await service.list_resources(
                    BOB,
                    action=AccessAction.ARTIFACT_READ,
                    resource_type=AccessResourceType.ARTIFACT,
                    family="skill",
                    cursor=first.next_cursor,
                    limit=1,
                    context=AUDIT,
                )

    asyncio.run(scenario())


def test_missing_owner_is_fail_closed_and_owner_is_immutable() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            skill = ResourceRef.artifact("scope-a", family="skill", artifact_id="skill-a")
            with pytest.raises(AccessUnavailableError, match="owner"):
                await service.require(ADMIN, AccessAction.ARTIFACT_READ, skill, context=AUDIT)
            await service.establish_artifact_owner(skill, ALICE, idempotency_key="owner-a", context=AUDIT)
            repeated = await service.establish_artifact_owner(skill, ALICE, idempotency_key="owner-a", context=AUDIT)
            assert repeated.owner == ALICE
            with pytest.raises(AccessConflictError, match="different owner"):
                await service.establish_artifact_owner(skill, BOB, idempotency_key="owner-b", context=AUDIT)

    asyncio.run(scenario())


def test_candidate_owner_is_locked_to_proposer_and_target() -> None:
    async def scenario() -> None:
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            first = await service.attest_candidate_owner(
                scope_id="scope-a",
                candidate_id="candidate-a",
                family="experience",
                proposed_owner=ALICE,
                target=None,
                idempotency_key="candidate-owner-a",
            )
            repeated = await service.attest_candidate_owner(
                scope_id="scope-a",
                candidate_id="candidate-a",
                family="experience",
                proposed_owner=ALICE,
                target=None,
                idempotency_key="candidate-owner-a",
            )
            assert repeated == first
            with pytest.raises(AccessConflictError, match="different proposed owner"):
                await service.attest_candidate_owner(
                    scope_id="scope-a",
                    candidate_id="candidate-a",
                    family="experience",
                    proposed_owner=BOB,
                    target=None,
                    idempotency_key="candidate-owner-b",
                )

    asyncio.run(scenario())


async def _seed_server_admin(repository: RelationalAccessRepository) -> None:
    await repository.create_binding(
        AccessBinding(
            binding_id="seed-admin",
            subject=ADMIN,
            resource=ResourceRef.server(),
            role=AccessRole.SERVER_ADMIN,
            granted_by=ADMIN,
            reason="test bootstrap",
            created_at=datetime.now(UTC),
            expires_at=None,
            state=AccessBindingState.ACTIVE,
            version=1,
            policy_revision="pending",
            idempotency_key="seed-admin",
        )
    )
