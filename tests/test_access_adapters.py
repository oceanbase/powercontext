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
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessControlService,
    AccessProviderCapabilities,
    AccessRequest,
    AccessRole,
    AccessUnavailableError,
    AuthZenAuthorizationProvider,
    BuiltinAuthorizationProvider,
    CasbinAuthorizationProvider,
    CreateBinding,
    GroupRef,
    MemoryEntrySelector,
    PrincipalRef,
    ResourceRef,
    ResourceSearchRequest,
)
from powercontext.server.authz.composition import open_casbin_access_control
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository

ADMIN = PrincipalRef(type="service", id="admin")
ALICE = PrincipalRef(type="user", id="alice", description="Alice")
BOB = PrincipalRef(type="user", id="bob")
AUDIT = AccessAuditContext(transport="http", operation="adapter-conformance", request_id="req-adapter")


def test_builtin_and_casbin_adapters_share_terminal_semantics() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            builtin = BuiltinAuthorizationProvider(repository)
            casbin = CasbinAuthorizationProvider(repository)
            service = AccessControlService(builtin, relationships=repository, audit=repository)
            handoff = ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff")
            other = ResourceRef.artifact("scope-a", family="handoff", artifact_id="other")
            await service.establish_artifact_owner(handoff, ALICE, idempotency_key="owner-handoff", context=AUDIT)
            await service.establish_artifact_owner(other, ALICE, idempotency_key="owner-other", context=AUDIT)
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=handoff,
                    role=AccessRole.HANDOFF_RECEIVER,
                    idempotency_key="receiver-bob",
                ),
                context=AUDIT,
            )

            vectors = (
                (AccessAction.ARTIFACT_READ, handoff, True),
                (AccessAction.HANDOFF_EVIDENCE_INSPECT, handoff, True),
                (AccessAction.HANDOFF_ACKNOWLEDGE, handoff, True),
                (AccessAction.ARTIFACT_WRITE, handoff, False),
                (AccessAction.ARTIFACT_READ, other, False),
                (AccessAction.SCOPE_READ, ResourceRef.scope("scope-a"), False),
            )
            for action, resource, expected in vectors:
                request = AccessRequest(subject=BOB, action=action, resource=resource, context=AUDIT)
                builtin_decision = await builtin.check(request)
                casbin_decision = await casbin.check(request)
                assert builtin_decision.allowed is casbin_decision.allowed is expected
                assert builtin_decision.policy_revision == casbin_decision.policy_revision

    asyncio.run(scenario())


def test_casbin_composition_has_writable_relationships_and_owner_enforcement() -> None:
    async def scenario() -> None:
        async with open_casbin_access_control(SQLiteConfig(), bootstrap_administrators=(ADMIN,)) as service:
            experience = ResourceRef.artifact("scope-a", family="experience", artifact_id="experience-a")
            await service.establish_artifact_owner(experience, ALICE, idempotency_key="owner-experience", context=AUDIT)
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=experience,
                    role=AccessRole.ARTIFACT_VIEWER,
                    idempotency_key="share-experience",
                ),
                context=AUDIT,
            )
            assert (await service.require(BOB, AccessAction.ARTIFACT_READ, experience, context=AUDIT)).allowed
            assert not (await service.check(BOB, AccessAction.ARTIFACT_WRITE, experience, context=AUDIT)).allowed

    asyncio.run(scenario())


def test_authzen_uses_logical_identity_and_trusted_powercontext_context() -> None:
    seen: list[dict[str, object]] = []
    subject = PrincipalRef(type="user", id="workforce:alice", description="Alice")
    actor = PrincipalRef(type="service", id="agent:codex", description="Codex")
    group = GroupRef(type="group", id="workforce:payments", description="Payments")
    context = AccessAuditContext(
        transport="http",
        operation="adapter-conformance",
        request_id="req-adapter",
        actor=actor,
        subject_groups=(group,),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer provider-token"
        payload = json.loads(request.content)
        seen.append(payload)
        if request.url.path.endswith("/evaluation"):
            return httpx.Response(200, json={"decision": True, "context": {"policy_revision": "pdp-42"}})
        return httpx.Response(200, json={"evaluations": [{"decision": True}]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AuthZenAuthorizationProvider(
                "http://127.0.0.1:9876",
                token=SecretStr("provider-token"),
                http_client=client,
            )
            resource = ResourceRef.artifact(
                "scope-a",
                family="memory",
                artifact_id="memory",
                selector=MemoryEntrySelector(entry_id="entry-a"),
            )
            decision = await provider.check(
                AccessRequest(subject=subject, action=AccessAction.ARTIFACT_READ, resource=resource, context=context)
            )
            assert decision.allowed
            assert decision.policy_revision == "pdp-42"
            assert seen[0] == {
                "subject": {"type": "user", "id": "workforce:alice", "properties": {"description": "Alice"}},
                "action": {"name": "artifact.read"},
                "resource": {
                    "type": "artifact",
                    "id": resource.key,
                    "properties": {
                        "scope_id": "scope-a",
                        "identity": {"family": "memory", "artifact_id": "memory"},
                        "selector": {"type": "memory_entry", "entry_id": "entry-a"},
                    },
                },
                "context": {
                    "request_id": "req-adapter",
                    "transport": "http",
                    "operation": "adapter-conformance",
                    "powercontext": {
                        "actor": {
                            "type": "service",
                            "id": "agent:codex",
                            "properties": {"description": "Codex"},
                        },
                        "subject_groups": [
                            {
                                "type": "group",
                                "id": "workforce:payments",
                                "properties": {"description": "Payments"},
                            }
                        ],
                    },
                },
            }
            with pytest.raises(AccessUnavailableError, match="filtering"):
                await provider.resolve_resource_filter(
                    ResourceSearchRequest(
                        subject=ALICE,
                        action=AccessAction.ARTIFACT_READ,
                        resource_type=resource.type,
                        family="memory",
                        context=AUDIT,
                    )
                )

    asyncio.run(scenario())


def test_authzen_group_context_matches_builtin_and_casbin_decisions() -> None:
    group = GroupRef(type="group", id="workforce:payments")
    actor = PrincipalRef(type="service", id="agent:codex")
    scope = ResourceRef.scope("scope-a")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        trusted = payload["context"]["powercontext"]
        assert trusted["actor"] == {"type": "service", "id": "agent:codex", "properties": {}}
        group_ids = {item["id"] for item in trusted["subject_groups"]}
        return httpx.Response(200, json={"decision": "workforce:payments" in group_ids})

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            builtin = BuiltinAuthorizationProvider(repository)
            casbin = CasbinAuthorizationProvider(repository)
            service = AccessControlService(
                builtin,
                relationships=repository,
                audit=repository,
                provider_capabilities=AccessProviderCapabilities(
                    safe_resource_filtering=True,
                    multi_requirement_check=True,
                    relationship_management=True,
                    group_subjects=True,
                ),
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=group,
                    resource=scope,
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="group-scope-viewer",
                ),
                context=AUDIT,
            )

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                authzen = AuthZenAuthorizationProvider("http://127.0.0.1:9876", http_client=client)
                for subject_groups, expected in (((group,), True), ((), False)):
                    context = AccessAuditContext(
                        transport="http",
                        operation="adapter-conformance",
                        actor=actor,
                        subject_groups=subject_groups,
                    )
                    request = AccessRequest(
                        subject=BOB,
                        action=AccessAction.SCOPE_READ,
                        resource=scope,
                        context=context,
                    )
                    decisions = await asyncio.gather(
                        builtin.check(request),
                        casbin.check(request),
                        authzen.check(request),
                    )
                    assert all(decision.allowed is expected for decision in decisions)

    asyncio.run(scenario())


def test_authzen_fails_closed_on_malformed_decisions_and_cannot_manage_relationships() -> None:
    async def scenario() -> None:
        malformed = httpx.MockTransport(lambda _request: httpx.Response(200, json={"decision": "allow"}))
        async with httpx.AsyncClient(transport=malformed) as client:
            provider = AuthZenAuthorizationProvider("http://127.0.0.1:9876", http_client=client)
            request = AccessRequest(
                subject=BOB,
                action=AccessAction.SERVER_OBSERVE,
                resource=ResourceRef.server(),
                context=AUDIT,
            )
            with pytest.raises(AccessUnavailableError):
                await provider.check(request)

        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)

            def allow(request: httpx.Request) -> httpx.Response:
                payload = json.loads(request.content)
                if request.url.path.endswith("/evaluations"):
                    return httpx.Response(
                        200,
                        json={"evaluations": [{"decision": True} for _ in payload["evaluations"]]},
                    )
                return httpx.Response(200, json={"decision": True})

            transport = httpx.MockTransport(allow)
            async with httpx.AsyncClient(transport=transport) as client:
                provider = AuthZenAuthorizationProvider("http://127.0.0.1:9876", http_client=client)
                service = AccessControlService(
                    provider,
                    relationships=None,
                    audit=repository,
                    provider_capabilities=AccessProviderCapabilities(
                        safe_resource_filtering=False,
                        multi_requirement_check=True,
                        relationship_management=False,
                    ),
                )
                with pytest.raises(AccessUnavailableError, match="relationship"):
                    await service.create_binding(
                        ADMIN,
                        CreateBinding(
                            subject=BOB,
                            resource=ResourceRef.scope("scope-a"),
                            role=AccessRole.SCOPE_VIEWER,
                            idempotency_key="unsupported-relationship",
                        ),
                        context=AUDIT,
                    )

    asyncio.run(scenario())


def test_authzen_rejects_credential_urls_and_insecure_remote_http() -> None:
    with pytest.raises(ValueError, match="credential-free"):
        AuthZenAuthorizationProvider("https://user:secret@pdp.example")
    with pytest.raises(ValueError, match="credential-free"):
        AuthZenAuthorizationProvider("http://pdp.example")


async def _seed_admin(repository: RelationalAccessRepository) -> None:
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
