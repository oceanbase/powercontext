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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.middleware import Middleware

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryVersion
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.publication import ArtifactPublication, ArtifactPublicationRequest
from powercontext.builtin.runtime import MemoryEntryRecord
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.app import create_app
from powercontext.server.authentication import (
    AuthenticationResult,
    ProviderReadiness,
    StaticBearerAuthenticationProvider,
)
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessControlService,
    AccessRole,
    BuiltinAuthorizationProvider,
    CreateBinding,
    MemoryEntrySelector,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from powercontext.server.factory import create_server_app
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, ServerSettings
from powercontext.server.web import mount_web_ui

ADMIN = PrincipalRef(type="service", id="admin", description="deployment administrator")
BOB = PrincipalRef(type="user", id="bob")
ALICE = PrincipalRef(type="user", id="alice")
AUDIT = AccessAuditContext(transport="test", operation="seed")


class _FailingAuthenticationProvider:
    async def authenticate(self, request) -> AuthenticationResult:
        del request
        raise RuntimeError("private-provider-detail")

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=False)


class _ActingAuthenticationProvider:
    async def authenticate(self, request) -> AuthenticationResult:
        del request
        return AuthenticationResult(subject=BOB, actor=ADMIN, credential_id="delegated-test")

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True)


def test_enforced_mode_cannot_silently_start_without_authentication_or_provider() -> None:
    with pytest.raises(ValueError, match="injected Authentication Provider or legacy AUTH_TOKEN"):
        create_server_app(settings=ServerSettings(access=AccessControlConfig(mode="enforced")))

    scheduled = create_server_app(
        settings=ServerSettings(
            access=AccessControlConfig(mode="enforced"),
            runtime=RuntimeConfig(schedule_seconds=60),
        ),
        authentication_provider=_ActingAuthenticationProvider(),
    )
    with pytest.raises(ValueError, match="BACKGROUND_PRINCIPAL_ID"), TestClient(scheduled):
        pass


def test_enforced_mode_uses_injected_authentication_and_builtin_access(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            access=AccessControlConfig(mode="enforced"),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'injected-auth.db'}"),
        ),
        authentication_provider=_ActingAuthenticationProvider(),
    )

    with TestClient(app) as client:
        principal = client.get("/v1/access/me")
        protected = client.get("/v1/capabilities")

    assert principal.status_code == 200
    assert principal.json()["principal"]["id"] == "bob"
    assert principal.json()["mode"] == "enforced"
    assert protected.status_code == 403


def test_injected_authentication_takes_precedence_over_legacy_token(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            access=AccessControlConfig(mode="enforced"),
            auth=BearerAuthConfig(token=SecretStr("legacy-server-secret")),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'injected-precedence.db'}"),
        ),
        authentication_provider=_ActingAuthenticationProvider(),
    )

    with TestClient(app) as client:
        principal = client.get(
            "/v1/access/me",
            headers={"Authorization": "Bearer legacy-server-secret"},
        )
        protected = client.get(
            "/v1/capabilities",
            headers={"Authorization": "Bearer legacy-server-secret"},
        )

    assert principal.status_code == 200
    assert principal.json()["principal"]["id"] == "bob"
    assert protected.status_code == 403


def test_low_level_enforced_app_fails_closed_without_an_authorization_provider() -> None:
    async def scenario() -> None:
        async with _client(create_app(access_mode="enforced")) as client:
            readiness = await client.get("/health/ready")
            capabilities = await client.get("/v1/capabilities")

        assert readiness.status_code == 503
        assert readiness.json()["checks"]["access_provider"] == "not_ready"
        assert capabilities.status_code == 503
        assert capabilities.json()["error"]["code"] == "access_unavailable"

    asyncio.run(scenario())


def test_authentication_provider_failures_use_a_stable_secret_safe_response() -> None:
    async def scenario() -> None:
        provider = _FailingAuthenticationProvider()
        app = create_app(
            authentication_provider=provider,
            middleware=(Middleware(AuthenticationMiddleware, provider=provider),),
        )
        async with _client(app) as client:
            response = await client.get("/v1/access/me", headers=_auth("never-echo-this"))

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "authentication_unavailable"
        assert "private-provider-detail" not in response.text
        assert "never-echo-this" not in response.text

    asyncio.run(scenario())


def test_access_audit_time_range_filters_results_and_binds_the_cursor() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            await service.check(
                ADMIN,
                AccessAction.SCOPE_READ,
                ResourceRef.scope("scope-a"),
                context=AUDIT,
            )
            app = _app(service, principal=ADMIN, token="admin-token")  # noqa: S106 - test credential.
            now = datetime.now(UTC)
            current_range = {
                "start": (now - timedelta(days=1)).isoformat(),
                "end": (now + timedelta(days=1)).isoformat(),
            }
            payload = {
                "resource": {"type": "scope", "scope_id": "scope-a"},
                "time_range": current_range,
                "limit": 1,
            }
            async with _client(app) as client:
                first = await client.post(
                    "/v1/access/audit/list",
                    headers=_auth("admin-token"),
                    json=payload,
                )
                assert first.status_code == 200, first.json()
                assert len(first.json()["items"]) == 1
                assert first.json()["next_cursor"] is not None

                outside = await client.post(
                    "/v1/access/audit/list",
                    headers=_auth("admin-token"),
                    json=payload
                    | {
                        "time_range": {
                            "start": (now - timedelta(days=3)).isoformat(),
                            "end": (now - timedelta(days=2)).isoformat(),
                        }
                    },
                )
                assert outside.status_code == 200
                assert outside.json()["items"] == []

                changed_filter = await client.post(
                    "/v1/access/audit/list",
                    headers=_auth("admin-token"),
                    json=payload
                    | {
                        "time_range": current_range | {"start": (now - timedelta(hours=12)).isoformat()},
                        "cursor": first.json()["next_cursor"],
                    },
                )
                assert changed_filter.status_code == 422

                invalid = await client.post(
                    "/v1/access/audit/list",
                    headers=_auth("admin-token"),
                    json=payload | {"time_range": {"start": now.isoformat(), "end": now.isoformat()}},
                )
                assert invalid.status_code == 422

    asyncio.run(scenario())


def test_access_audit_persists_the_trusted_actor_separately_from_the_subject() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            provider = _ActingAuthenticationProvider()
            app = create_app(
                access_control=service,
                authentication_provider=provider,
                middleware=(Middleware(AuthenticationMiddleware, provider=provider),),
            )
            async with _client(app) as client:
                checked = await client.post(
                    "/v1/access/check",
                    headers=_auth("delegated-token"),
                    json={
                        "match": "all",
                        "requirements": [
                            {
                                "action": "scope.read",
                                "resource": {"type": "scope", "scope_id": "scope-a"},
                            }
                        ],
                    },
                )
                assert checked.status_code == 200

            admin_app = _app(service, principal=ADMIN, token="admin-token")  # noqa: S106 - test credential.
            async with _client(admin_app) as admin:
                audit = await admin.post(
                    "/v1/access/audit/list",
                    headers=_auth("admin-token"),
                    json={
                        "resource": {"type": "scope", "scope_id": "scope-a"},
                        "subject": {"type": "user", "id": "bob"},
                    },
                )
            assert audit.status_code == 200, audit.json()
            assert len(audit.json()["items"]) == 1
            event = audit.json()["items"][0]
            assert event["principal"] == {"type": "user", "id": "bob", "description": None}
            assert event["actor"] == {
                "type": "service",
                "id": "admin",
                "description": "deployment administrator",
            }

    asyncio.run(scenario())


def test_compound_access_check_supports_all_and_any_without_a_batch_route() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            app = _app(service, principal=ADMIN, token="admin-token")  # noqa: S106 - test credential.
            requirements = [
                {
                    "action": "server.admin",
                    "resource": {"type": "server", "deployment_id": "powercontext"},
                },
                {
                    "action": "scope.read",
                    "resource": {"type": "scope", "scope_id": "scope-a"},
                },
            ]
            async with _client(app) as client:
                all_required = await client.post(
                    "/v1/access/check",
                    headers=_auth("admin-token"),
                    json={"match": "all", "requirements": requirements},
                )
                any_required = await client.post(
                    "/v1/access/check",
                    headers=_auth("admin-token"),
                    json={"match": "any", "requirements": requirements},
                )
                removed_batch = await client.post(
                    "/v1/access/check-batch",
                    headers=_auth("admin-token"),
                    json={"checks": []},
                )

            assert all_required.status_code == 200
            assert all_required.json()["allowed"] is False
            assert [decision["allowed"] for decision in all_required.json()["decisions"]] == [True, False]
            assert any_required.status_code == 200
            assert any_required.json()["allowed"] is True
            assert any_required.json()["decisions"] == all_required.json()["decisions"]
            assert removed_batch.status_code == 404

    asyncio.run(scenario())


def test_access_binding_replace_is_generic_and_atomic() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            original = await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=ResourceRef.scope("scope-a"),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="scope-viewer-bob",
                ),
                context=AUDIT,
            )
            app = _app(service, principal=ADMIN, token="admin-token")  # noqa: S106 - test credential.
            async with _client(app) as client:
                response = await client.post(
                    "/v1/access/bindings/replace",
                    headers=_auth("admin-token"),
                    json={
                        "binding_id": original.binding_id,
                        "expected_version": 1,
                        "replacement": {
                            "subject": {"type": "user", "id": "alice"},
                            "reason": "transfer scope visibility",
                            "expires_at": None,
                        },
                        "idempotency_key": "replace-scope-viewer-with-alice",
                    },
                )
                removed_special_case = await client.post(
                    "/v1/access/bindings/reassign-handoff-receiver",
                    headers=_auth("admin-token"),
                    json={},
                )

            assert response.status_code == 200, response.json()
            assert response.json()["previous"]["state"] == "revoked"
            assert response.json()["previous"]["version"] == 2
            assert response.json()["current"]["subject"]["id"] == "alice"
            assert response.json()["current"]["resource"] == {
                "type": "scope",
                "scope_id": "scope-a",
            }
            assert response.json()["current"]["role"] == "scope.viewer"
            assert removed_special_case.status_code == 404

    asyncio.run(scenario())


def test_connector_checkpoint_routes_enforce_the_nested_scope_boundary() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=ResourceRef.scope("scope-a"),
                    role=AccessRole.SCOPE_CONTRIBUTOR,
                    idempotency_key="connector-worker-scope-a",
                ),
                context=AUDIT,
            )
            binding = {
                "scope_id": "scope-a",
                "binding_id": "connector-a",
                "connector_name": "test-connector",
                "connector_version": "1",
            }
            requests = (
                ("/v1/connector-checkpoints/get", {"binding": binding}),
                (
                    "/v1/connector-checkpoints/commit",
                    {"binding": binding, "expected": None, "checkpoint": {"cursor": 1}},
                ),
            )

            contributor_app = _app(service, principal=BOB, token="bob-token")  # noqa: S106 - test credential.
            denied_app = _app(service, principal=ALICE, token="alice-token")  # noqa: S106 - test credential.
            async with _client(contributor_app) as contributor, _client(denied_app) as denied:
                for path, payload in requests:
                    authorized = await contributor.post(path, headers=_auth("bob-token"), json=payload)
                    unauthorized = await denied.post(path, headers=_auth("alice-token"), json=payload)
                    assert authorized.status_code == 503
                    assert authorized.json()["error"]["code"] == "runtime_not_ready"
                    assert unauthorized.status_code == 403

    asyncio.run(scenario())


def test_standard_skill_lifecycle_routes_enforce_access_before_runtime() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            await service.establish_artifact_owner(
                ResourceRef.artifact("scope-a", family="skill", artifact_id="skill-a"),
                ADMIN,
                idempotency_key="owner-skill-a-lifecycle",
                context=AUDIT,
            )
            app = _app(service, principal=BOB, token="bob-token")  # noqa: S106 - test credential.
            artifact = {"family": "skill", "artifact_id": "skill-a", "revision": 1}
            requests = (
                ("/v1/skill/library", {"scope_id": "scope-a"}),
                (
                    "/v1/skill/lifecycle",
                    {
                        "scope_id": "scope-a",
                        "artifact_id": "skill-a",
                        "expected_generation": 0,
                        "lifecycle_state": "active",
                    },
                ),
                (
                    "/v1/skill/usage",
                    {
                        "scope_id": "scope-a",
                        "observation_id": "usage-a",
                        "skill_ref": artifact,
                        "package_digest": f"sha256:{'a' * 64}",
                        "target_id": "codex-project",
                        "selected": True,
                        "invoked": "true",
                        "validation": "passed",
                        "outcome": "success",
                    },
                ),
                ("/v1/skill/remote/targets", {"scope_id": "scope-a"}),
                (
                    "/v1/skill/remote/publication/publish",
                    {
                        "scope_id": "scope-a",
                        "target_id": "target-a",
                        "artifact": artifact,
                        "expected_generation": 0,
                    },
                ),
            )
            async with _client(app) as client:
                for path, payload in requests:
                    response = await client.post(path, headers=_auth("bob-token"), json=payload)
                    assert response.status_code == 403, (path, response.json())
                    assert response.json()["error"]["code"] == "forbidden"

    asyncio.run(scenario())


class _HandoffShareability:
    def for_scope(self, scope_id: str) -> Self:
        del scope_id
        return self

    async def revision(self, artifact) -> object:
        del artifact
        return object()


class _MemoryApplication:
    def __init__(self, record: MemoryEntryRecord) -> None:
        self.record = record

    def for_scope(self, scope_id: str) -> Self:
        del scope_id
        return self

    async def get(self, request) -> MemoryEntryRecord:
        del request
        return self.record


class _ArtifactPublicationApplication:
    def __init__(self) -> None:
        self.requests: list[ArtifactPublicationRequest] = []

    async def publish(self, request: ArtifactPublicationRequest) -> ArtifactPublication:
        self.requests.append(request)
        return ArtifactPublication(
            source=request.source,
            target=ArtifactAddress(
                scope_id=request.target_scope_id,
                artifact=ArtifactRef(family=request.source.artifact.family, artifact_id="published-skill", revision=1),
            ),
            content_digest="0" * 64,
        )


class _DashboardScopeApplication:
    async def list(self, *, scope_ids=None) -> tuple[SimpleNamespace, ...]:
        assert scope_ids is not None, "Dashboard queried unauthorized scope metadata"
        scopes = (
            SimpleNamespace(
                scope_id="scope-visible",
                title="Visible",
                summary="Visible Scope",
                parent_scope_id=None,
            ),
            SimpleNamespace(
                scope_id="scope-hidden",
                title="Hidden",
                summary="Hidden Scope",
                parent_scope_id=None,
            ),
        )

        return scopes if scope_ids is None else tuple(scope for scope in scopes if scope.scope_id in scope_ids)


def test_access_api_and_handoff_pep_enforce_exact_receiver_visibility() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            handoff = ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff-a")
            await service.establish_artifact_owner(
                handoff,
                ADMIN,
                idempotency_key="owner-handoff-a",
                context=AUDIT,
            )
            await service.establish_artifact_owner(
                ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff-b"),
                ADMIN,
                idempotency_key="owner-handoff-b",
                context=AUDIT,
            )
            await service.establish_artifact_owner(
                ResourceRef.artifact("scope-a", family="handoff", artifact_id="handoff"),
                ADMIN,
                idempotency_key="owner-default-handoff",
                context=AUDIT,
            )
            admin_app = _app(
                service,
                principal=ADMIN,
                token="admin-token",  # noqa: S106 - test credential.
                application=SimpleNamespace(handoff=_HandoffShareability()),
            )
            async with _client(admin_app) as admin:
                readiness = await admin.get("/health/ready")
                assert readiness.status_code == 200
                readiness_checks = readiness.json()["checks"]
                assert readiness_checks["access_mode"] == "enforced"
                assert readiness_checks["access_provider"] == "ready"
                assert readiness_checks["access_resource_kinds"] == "server,scope,artifact"
                principal = await admin.get("/v1/access/me", headers=_auth("admin-token"))
                assert principal.status_code == 200
                assert principal.json()["principal"] == {
                    "type": "service",
                    "id": "admin",
                    "description": "deployment administrator",
                }
                assert principal.json()["mode"] == "enforced"
                assert principal.json()["resource_kinds"] == ["server", "scope", "artifact"]
                assert {
                    profile["family"] for profile in principal.json()["artifact_families"] if profile["enabled"]
                } == {
                    "handoff",
                    "memory",
                    "experience",
                    "skill",
                }
                roles = await admin.post(
                    "/v1/access/roles/list",
                    headers=_auth("admin-token"),
                    json={"resource_type": "artifact", "family": "skill"},
                )
                assert roles.status_code == 200
                assert {item["role"] for item in roles.json()["items"]} == {
                    "artifact.owner",
                    "artifact.viewer",
                }
                assert all(item["artifact_families"] == ["skill"] for item in roles.json()["items"])
                role_cardinalities = {item["role"]: item["cardinality"] for item in roles.json()["items"]}
                assert role_cardinalities == {
                    "artifact.owner": "one_per_resource",
                    "artifact.viewer": "many_per_resource",
                }
                created = await admin.post(
                    "/v1/access/bindings/create",
                    headers=_auth("admin-token"),
                    json={
                        "subject": {"type": "user", "id": "bob", "description": "forged directory name"},
                        "resource": {
                            "type": "artifact",
                            "scope_id": "scope-a",
                            "identity": {"family": "handoff", "artifact_id": "handoff-a"},
                            "selector": None,
                        },
                        "role": "handoff.receiver",
                        "idempotency_key": "handoff-a-to-bob",
                    },
                )
                assert created.status_code == 201
                assert created.json()["policy_revision"]
                assert created.json()["subject"] == {"type": "user", "id": "bob", "description": None}

            bob_app = _app(service, principal=BOB, token="bob-token")  # noqa: S106 - test credential.
            async with _client(bob_app) as bob:
                exact = {
                    "type": "artifact",
                    "scope_id": "scope-a",
                    "identity": {"family": "handoff", "artifact_id": "handoff-a"},
                    "selector": None,
                }
                decision = await bob.post(
                    "/v1/access/check",
                    headers=_auth("bob-token"),
                    json={
                        "match": "all",
                        "requirements": [{"action": "handoff.acknowledge", "resource": exact}],
                    },
                )
                assert decision.status_code == 200
                assert decision.json()["allowed"] is True
                assert decision.json()["decisions"][0]["allowed"] is True

                resources = await bob.post(
                    "/v1/access/resources/list",
                    headers=_auth("bob-token"),
                    json={"action": "artifact.read", "resource_type": "artifact", "family": "handoff"},
                )
                assert resources.status_code == 200
                assert resources.json()["items"] == [exact]
                assert resources.json()["total"] == 1

                denied = await bob.post(
                    "/v1/handoff/continue",
                    headers=_auth("bob-token"),
                    json={
                        "scope_id": "scope-a",
                        "selection": "exact",
                        "revision": {"family": "handoff", "artifact_id": "handoff-b", "revision": 1},
                    },
                )
                assert denied.status_code == 403, denied.json()
                assert denied.json()["error"]["code"] == "forbidden"

                latest = await bob.post(
                    "/v1/handoff/continue",
                    headers=_auth("bob-token"),
                    json={"scope_id": "scope-a", "selection": "latest"},
                )
                assert latest.status_code == 403

                allowed_to_runtime_boundary = await bob.post(
                    "/v1/handoff/continue",
                    headers=_auth("bob-token"),
                    json={
                        "scope_id": "scope-a",
                        "selection": "exact",
                        "revision": {"family": "handoff", "artifact_id": "handoff-a", "revision": 3},
                    },
                )
                assert allowed_to_runtime_boundary.status_code == 503
                assert allowed_to_runtime_boundary.json()["error"]["code"] == "runtime_not_ready"

                cannot_delegate = await bob.post(
                    "/v1/access/bindings/create",
                    headers=_auth("bob-token"),
                    json={
                        "subject": {"type": "user", "id": "alice"},
                        "resource": exact,
                        "role": "handoff.viewer",
                        "idempotency_key": "bob-cannot-delegate",
                    },
                )
                assert cannot_delegate.status_code == 403

                unauthenticated = await bob.get("/v1/access/me")
                assert unauthenticated.status_code == 401

    asyncio.run(scenario())


def test_logical_memory_entry_grant_allows_every_entry_version_but_not_scope_listing() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            exact = ResourceRef.artifact(
                "scope-a",
                family="memory",
                artifact_id="memory-a",
                selector=MemoryEntrySelector(entry_id="entry-a"),
            )
            await service.establish_artifact_owner(
                exact,
                ADMIN,
                idempotency_key="owner-memory-entry-a",
                context=AUDIT,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=exact,
                    role=AccessRole.ARTIFACT_VIEWER,
                    idempotency_key="bob-exact-memory",
                ),
                context=AUDIT,
            )
            memory_ref = ArtifactRef(family="memory", artifact_id="memory-a", revision=4)
            record = MemoryEntryRecord(
                memory_ref=memory_ref,
                state="active",
                entry=MemoryEntryVersion(
                    memory_artifact_id="memory-a",
                    entry_id="entry-a",
                    entry_version_id="entry-version-2",
                    version=2,
                    previous_version_id="entry-version-1",
                    kind="decision",
                    text="Only this exact Memory Entry Version is shared.",
                    entry_content_hash="a" * 64,
                    created_in_revision=4,
                ),
            )
            app = _app(
                service,
                principal=BOB,
                token="bob-token",  # noqa: S106 - test credential.
                application=SimpleNamespace(memory=_MemoryApplication(record)),
            )
            request = {
                "scope_id": "scope-a",
                "citation": {
                    "memory_ref": {"family": "memory", "artifact_id": "memory-a", "revision": 4},
                    "entry_id": "entry-a",
                    "entry_version_id": "entry-version-2",
                },
            }
            async with _client(app) as client:
                allowed = await client.post("/v1/memory/entries/get", headers=_auth("bob-token"), json=request)
                assert allowed.status_code == 200, allowed.json()
                assert allowed.json()["text"] == "Only this exact Memory Entry Version is shared."

                future_version = request | {"citation": request["citation"] | {"entry_version_id": "entry-version-3"}}
                allowed_future = await client.post(
                    "/v1/memory/entries/get", headers=_auth("bob-token"), json=future_version
                )
                assert allowed_future.status_code == 200

                aggregate = await client.post(
                    "/v1/memory/entries/list",
                    headers=_auth("bob-token"),
                    json={"scope_id": "scope-a"},
                )
                assert aggregate.status_code == 403

    asyncio.run(scenario())


def test_artifact_publication_requires_logical_share_and_target_scope_admin() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            skill = ResourceRef.artifact("scope-a", family="skill", artifact_id="skill-a")
            await service.establish_artifact_owner(
                skill,
                BOB,
                idempotency_key="owner-skill-a",
                context=AUDIT,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=ResourceRef.scope("scope-b"),
                    role=AccessRole.SCOPE_ADMIN,
                    idempotency_key="bob-target-scope-admin",
                ),
                context=AUDIT,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=ALICE,
                    resource=skill,
                    role=AccessRole.ARTIFACT_VIEWER,
                    idempotency_key="alice-skill-viewer",
                ),
                context=AUDIT,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=ALICE,
                    resource=ResourceRef.scope("scope-b"),
                    role=AccessRole.SCOPE_ADMIN,
                    idempotency_key="alice-target-scope-admin",
                ),
                context=AUDIT,
            )
            publications = _ArtifactPublicationApplication()
            application = SimpleNamespace(publications=publications)
            bob_provider = StaticBearerAuthenticationProvider("bob-token", BOB)
            bob_app = create_app(
                application=application,
                access_control=service,
                authentication_provider=bob_provider,
                middleware=(Middleware(AuthenticationMiddleware, provider=bob_provider),),
            )
            payload = {
                "source": {
                    "scope_id": "scope-a",
                    "artifact": {"family": "skill", "artifact_id": "skill-a", "revision": 7},
                },
                "target_scope_id": "scope-b",
                "idempotency_key": "publish-skill-a-r7",
            }
            async with _client(bob_app) as bob:
                published = await bob.post(
                    "/v1/artifact-publications",
                    headers=_auth("bob-token"),
                    json=payload,
                )
                assert published.status_code == 201, published.json()
                assert published.json()["source"] == payload["source"]
                assert publications.requests[0].source.artifact.revision == 7
                target_resource = ResourceRef.artifact(
                    "scope-b",
                    family="skill",
                    artifact_id=published.json()["target"]["artifact"]["artifact_id"],
                )
                target_owner = await service.artifact_owner(target_resource)
                assert target_owner is not None
                assert target_owner.owner == BOB
                assert (
                    await service.check(
                        BOB,
                        AccessAction.ARTIFACT_WRITE,
                        target_resource,
                        context=AUDIT,
                    )
                ).allowed
                repeated = await bob.post(
                    "/v1/artifact-publications",
                    headers=_auth("bob-token"),
                    json=payload,
                )
                assert repeated.status_code == 201, repeated.json()
                assert await service.artifact_owner(target_resource) == target_owner

            alice_provider = StaticBearerAuthenticationProvider("alice-token", ALICE)
            alice_app = create_app(
                application=application,
                access_control=service,
                authentication_provider=alice_provider,
                middleware=(Middleware(AuthenticationMiddleware, provider=alice_provider),),
            )
            async with _client(alice_app) as alice:
                denied = await alice.post(
                    "/v1/artifact-publications",
                    headers=_auth("alice-token"),
                    json=payload,
                )
                assert denied.status_code == 403
            assert len(publications.requests) == 2

    asyncio.run(scenario())


def test_dashboard_scope_discovery_uses_the_same_principal_and_filters_before_response() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=ACCESS_TABLES) as profile:
            repository = RelationalAccessRepository(profile.database)
            await _seed_admin(repository)
            service = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=ResourceRef.scope("scope-visible"),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="bob-dashboard-scope",
                ),
                context=AUDIT,
            )
            await service.create_binding(
                ADMIN,
                CreateBinding(
                    subject=BOB,
                    resource=ResourceRef.server(),
                    role=AccessRole.SERVER_OBSERVER,
                    idempotency_key="bob-dashboard-observer",
                ),
                context=AUDIT,
            )
            app = _app(
                service,
                principal=BOB,
                token="bob-token",  # noqa: S106 - test credential.
                application=SimpleNamespace(scopes=_DashboardScopeApplication()),
            )
            mount_web_ui(
                app,
                dashboard_enabled=True,
                authentication_required=True,
            )
            async with _client(app) as client:
                response = await client.get("/dashboard/scopes", headers=_auth("bob-token"))
                hidden_library = await client.post(
                    "/dashboard/skills/library",
                    headers=_auth("bob-token"),
                    json={"scope_id": "scope-hidden"},
                )
                assert response.status_code == 200
                assert response.json() == [
                    {
                        "scope_id": "scope-visible",
                        "display_name": "Visible",
                        "summary": "Visible Scope",
                        "parent_scope_id": None,
                    }
                ]
                assert "scope-hidden" not in response.text
                assert hidden_library.status_code == 403

    asyncio.run(scenario())


def _app(service: AccessControlService, *, principal: PrincipalRef, token: str, application=None):
    provider = StaticBearerAuthenticationProvider(token, principal)
    return create_app(
        application=application,
        access_control=service,
        authentication_provider=provider,
        middleware=(Middleware(AuthenticationMiddleware, provider=provider),),
    )


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
