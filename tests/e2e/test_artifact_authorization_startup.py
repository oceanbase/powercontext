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

"""Persisted custom artifacts remain authorized without a plugin manager."""

import asyncio

import httpx
import pytest
from sqlalchemy import insert

from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.persistence.custom_artifacts import StoredArtifactContent, custom_artifact_repository
from powercontext.builtin.persistence.tables import ARTIFACT_FAMILY_SCHEMAS_TABLE
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import AccessAuditContext, AccessRole, CreateBinding, PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.profiles import ARTIFACT_FAMILY_PROFILES
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, DashboardConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_atomic_memory_search_authorization_snapshot import _isolated_database


@pytest.mark.parametrize(
    "backend,role,mode",
    [("sqlite", "all", "global"), ("sqlite", "all", "dedicated"), ("oceanbase", "api", "global")],
)
def test_custom_artifact_authorization_survives_server_restart(tmp_path, monkeypatch, backend, role, mode):
    async def scenario():
        family = "startup-report"
        admin = PrincipalRef(type="service", id="admin")
        reader = PrincipalRef(type="user", id="reader")
        async with _isolated_database(backend, tmp_path) as database:
            settings = ServerSettings(
                database=database,
                workspace=tmp_path,
                access=AccessControlConfig(mode="enforced"),
                runtime=RuntimeConfig(
                    artifact_processing_families=(),
                    artifact_processing_role=role,
                    artifact_processing_supervisor_mode=mode,
                ),
                dashboard=DashboardConfig(enabled=False),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            )
            await asyncio.to_thread(prepare_server_database, settings, evidence_dir=tmp_path / "evidence")
            async with open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access:

                def application(principal):
                    return create_server_app(
                        settings=settings,
                        scheduler_path=tmp_path / "scheduler.db",
                        access_control=access,
                        authentication_provider=StaticBearerAuthenticationProvider("test-token", principal),
                    )

                app = application(admin)
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer test-token"},
                    ) as client,
                ):
                    scopes = []
                    for name in ("visible", "forbidden"):
                        response = await client.post(
                            "/v1/scopes", json={"title": name, "summary": name, "idempotency_key": name}
                        )
                        assert response.status_code == 201, response.text
                        scopes.append(response.json()["scope_id"])
                    async with app.state.application._records().database.transaction() as connection:
                        await connection.execute(
                            insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                                family=family,
                                schema_version="1",
                                declaration={
                                    "family": family,
                                    "schema_version": "1",
                                    "content_schema": {"type": "object"},
                                    "search_fields": [],
                                },
                            )
                        )
                        for scope in scopes:
                            await custom_artifact_repository(family).create(
                                connection,
                                scope,
                                "report",
                                RepositoryArtifactDraft(
                                    family=family,
                                    content=StoredArtifactContent(schema_version="1", content={"text": "retained"}),
                                ),
                            )
                    await access.create_binding(
                        admin,
                        CreateBinding(
                            subject=reader,
                            resource=ResourceRef.scope(scopes[0]),
                            role=AccessRole.SCOPE_VIEWER,
                            idempotency_key="reader",
                        ),
                        context=AccessAuditContext(transport="test", operation="artifact-startup"),
                    )
                # Model an empty process-local registry on restart, preserving built-in profiles.
                monkeypatch.delitem(ARTIFACT_FAMILY_PROFILES, family, raising=False)
                discovery_app = application(admin)
                async with (
                    discovery_app.router.lifespan_context(discovery_app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=discovery_app),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer test-token"},
                    ) as client,
                ):
                    metadata = await client.get("/v1/capabilities")
                    assert metadata.status_code == 200, metadata.text
                    assert family in metadata.json()["artifact_families"]
                    assert next(
                        item for item in metadata.json()["custom_artifact_schemas"] if item["family"] == family
                    ) == {
                        "family": family,
                        "schema_version": "1",
                        "content_schema": {"type": "object"},
                        "search_fields": [],
                        "search": None,
                    }
                monkeypatch.delitem(ARTIFACT_FAMILY_PROFILES, family, raising=False)
                app = application(reader)
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer test-token"},
                    ) as client,
                ):
                    path = f"/v1/scopes/{scopes[0]}/artifacts/{family}/report"
                    for suffix in ("", "/revisions/1"):
                        response = await client.get(path + suffix)
                        assert response.status_code == 200, response.text
                        assert response.json()["content"] == {"text": "retained"}
                        assert response.json()["revision"] == 1
                    denied = await client.get(f"/v1/scopes/{scopes[1]}/artifacts/{family}/report")
                    assert denied.status_code == 403, denied.text
                    unknown = await client.get(f"/v1/scopes/{scopes[0]}/artifacts/unregistered-family/report")
                    assert unknown.status_code in {404, 422}, unknown.text

    asyncio.run(scenario())


def test_live_family_discovery_and_access_query_budget(tmp_path, monkeypatch):
    """External storage writes do not update this Server's process-local profiles."""
    from sqlalchemy import event

    async def scenario():
        async with _isolated_database("sqlite", tmp_path) as database:
            settings = ServerSettings(
                database=database,
                workspace=tmp_path,
                runtime=RuntimeConfig(artifact_processing_families=(), artifact_processing_supervisor_mode="dedicated"),
                access=AccessControlConfig(mode="enforced"),
                dashboard=DashboardConfig(enabled=False),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=True),
            )
            await asyncio.to_thread(prepare_server_database, settings, evidence_dir=tmp_path / "evidence")
            admin = PrincipalRef(type="service", id="admin")
            async with open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access:
                app = create_server_app(
                    settings=settings,
                    scheduler_path=tmp_path / "scheduler.db",
                    access_control=access,
                    authentication_provider=StaticBearerAuthenticationProvider("test-token", admin),
                )
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer test-token"},
                    ) as client,
                ):
                    response = await client.post(
                        "/v1/scopes", json={"title": "live", "summary": "live", "idempotency_key": "live"}
                    )
                    assert response.status_code == 201, response.text
                    scope = response.json()["scope_id"]
                    db = app.state.application._records().database
                    queries = []
                    binding_reads = []
                    assert access.relationships is not None
                    get_binding = access.relationships.get_binding

                    async def counted_get_binding(binding_id):
                        binding_reads.append(binding_id)
                        return await get_binding(binding_id)

                    monkeypatch.setattr(access.relationships, "get_binding", counted_get_binding)

                    def track(_conn, _cursor, statement, _parameters, _context, _many):
                        if (
                            statement.lstrip().upper().startswith("SELECT")
                            and "pc_artifact_family_schemas" in statement
                        ):
                            queries.append(statement)

                    event.listen(db.engine.sync_engine, "before_cursor_execute", track)
                    try:
                        for entry in (
                            "read",
                            "capabilities",
                            "access/check",
                            "roles/list",
                            "bindings/list",
                            "bindings/create",
                            "resources/list",
                            "bindings/revoke",
                            "bindings/replace",
                        ):
                            family = "live-" + entry.replace("/", "-")
                            monkeypatch.delitem(ARTIFACT_FAMILY_PROFILES, family, raising=False)
                            # Model a different process committing storage without touching this registry.
                            async with db.transaction() as connection:
                                await connection.execute(
                                    insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                                        family=family,
                                        schema_version="1",
                                        declaration={
                                            "family": family,
                                            "schema_version": "1",
                                            "content_schema": {},
                                            "search_fields": [],
                                        },
                                    )
                                )
                                await custom_artifact_repository(family).create(
                                    connection,
                                    scope,
                                    "report",
                                    RepositoryArtifactDraft(
                                        family=family,
                                        content=StoredArtifactContent(schema_version="1", content={"text": "live"}),
                                    ),
                                )
                            assert family not in ARTIFACT_FAMILY_PROFILES
                            path = f"/v1/scopes/{scope}/artifacts/{family}/report"
                            queries.clear()
                            await _assert_family_display(client, family, present=False)
                            assert queries == [], "Family display must not refresh the schema registry"
                            binding_reads.clear()
                            result = await _request_family_operation(client, access, admin, entry, family, scope, path)
                            expected_status = (
                                422 if entry in {"bindings/create", "bindings/replace", "resources/list"} else 200
                            )
                            assert result.status_code == expected_status, result.text
                            assert len(queries) == (2 if entry == "read" else 1), queries
                            _assert_discovered_family(entry, family, result.json())
                            if entry in {"bindings/revoke", "bindings/replace"}:
                                assert binding_reads == [family], "Binding ID must be resolved only once"
                            if entry in {
                                "roles/list",
                                "bindings/list",
                                "bindings/create",
                                "resources/list",
                                "bindings/replace",
                            }:
                                queries.clear()
                                repeated = await _request_family_operation(
                                    client, access, admin, entry, family, scope, path
                                )
                                assert repeated.status_code == result.status_code, repeated.text
                                assert repeated.json() == result.json()
                                assert queries == [], "Known family management must not refresh the registry"
                            queries.clear()
                            await _assert_family_display(client, family, present=True)
                            assert queries == [], "Family display must use the local registry"
                            for suffix in ("", "/revisions/1"):
                                read = await client.get(path + suffix)
                                assert read.status_code == 200, read.text
                            assert len(queries) == 2, queries  # Existing persisted-family validation: one per read.
                    finally:
                        event.remove(db.engine.sync_engine, "before_cursor_execute", track)

    asyncio.run(scenario())


def _assert_discovered_family(entry, family, payload):
    if entry == "capabilities":
        assert family in payload["artifact_families"]
        assert any(item["family"] == family for item in payload["custom_artifact_schemas"])
    elif entry == "access/check":
        assert payload["allowed"] is True


async def _assert_family_display(client, family, *, present):
    response = await client.get("/v1/access/me")
    assert response.status_code == 200, response.text
    assert any(item["family"] == family for item in response.json()["artifact_families"]) is present
    response = await client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert (f"{family}:enabled" in response.json()["checks"]["access_artifact_families"]) is present


async def _request_family_operation(client, access, admin, entry, family, scope, path):
    if entry == "read":
        return await client.get(path)
    if entry == "capabilities":
        return await client.get("/v1/capabilities")
    resource = {"type": "artifact", "scope_id": scope, "identity": {"family": family, "artifact_id": "report"}}
    subject = {"type": "user", "id": "recipient"}
    payloads = {
        "access/check": {"requirements": [{"action": "artifact.read", "resource": resource}], "match": "all"},
        "roles/list": {"resource_type": "artifact", "family": family},
        "bindings/list": {"management_resource": resource},
        "bindings/create": {
            "resource": resource,
            "subject": subject,
            "role": "artifact.viewer",
            "idempotency_key": family,
        },
        "resources/list": {"resource_type": "artifact", "family": family, "action": "artifact.read"},
    }
    if entry in {"bindings/revoke", "bindings/replace"}:
        await _seed_retained_binding(access, admin, family, scope)
        payload = {"binding_id": family, "expected_version": 1, "idempotency_key": family + "-change"}
        if entry == "bindings/replace":
            payload["replacement"] = {"subject": subject}
    else:
        payload = payloads[entry]
    endpoint = entry if entry == "access/check" else "access/" + entry
    return await client.post("/v1/" + endpoint, json=payload)


async def _seed_retained_binding(access, admin, family, scope):
    from datetime import UTC, datetime

    from powercontext.server.authz.models import AccessBinding, AccessBindingState

    # Exercise an existing relationship row; custom families still cannot grant direct roles through HTTP.
    await access.relationships.create_binding(
        AccessBinding(
            binding_id=family,
            subject=admin,
            resource=ResourceRef.artifact(scope, family=family, artifact_id="report"),
            role=AccessRole.ARTIFACT_OWNER,
            granted_by=admin,
            reason=None,
            created_at=datetime.now(UTC),
            expires_at=None,
            state=AccessBindingState.ACTIVE,
            version=1,
            policy_revision="pending",
            idempotency_key=family,
        )
    )


class _FilteringProvider:
    """A custom Provider that retains Scope grants but denies one exact Artifact."""

    def __init__(self, delegate):
        self.delegate = delegate

    async def check(self, request):
        from dataclasses import replace

        decision = await self.delegate.check(request)
        if request.resource.identity is not None and request.resource.identity.artifact_id == "hidden":
            return replace(
                decision, allowed=False, reason_code="no-matching-policy", matched_subject=None, matched_binding_id=None
            )
        return decision

    async def check_batch(self, requests):
        return tuple([await self.check(request) for request in requests])

    async def resolve_resource_filter(self, request):
        return await self.delegate.resolve_resource_filter(request)


def test_first_search_loads_external_family_with_custom_provider(tmp_path, monkeypatch):
    from sqlalchemy import event

    from powercontext.artifacts.indexes import ArtifactSearchDeclaration, TextIndex
    from powercontext.builtin.persistence.artifact_indexes import ArtifactIndexBuilder

    async def scenario():
        family = "live-search-report"
        monkeypatch.delitem(ARTIFACT_FAMILY_PROFILES, family, raising=False)
        async with _isolated_database("sqlite", tmp_path) as database:
            settings = ServerSettings(
                database=database,
                workspace=tmp_path,
                access=AccessControlConfig(mode="enforced"),
                runtime=RuntimeConfig(artifact_processing_families=(), artifact_processing_supervisor_mode="dedicated"),
                dashboard=DashboardConfig(enabled=False),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            )
            await asyncio.to_thread(prepare_server_database, settings, evidence_dir=tmp_path / "evidence")
            admin = PrincipalRef(type="service", id="admin")
            async with open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access:
                access.provider = _FilteringProvider(access.provider)
                app = create_server_app(
                    settings=settings,
                    scheduler_path=tmp_path / "scheduler.db",
                    access_control=access,
                    authentication_provider=StaticBearerAuthenticationProvider("test-token", admin),
                )
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                        base_url="http://testserver",
                        headers={"Authorization": "Bearer test-token"},
                    ) as client,
                ):
                    response = await client.post(
                        "/v1/scopes", json={"title": "search", "summary": "search", "idempotency_key": "search"}
                    )
                    assert response.status_code == 201, response.text
                    scope = response.json()["scope_id"]
                    db = app.state.application._records().database
                    search = ArtifactSearchDeclaration(
                        version="1",
                        indexes=(TextIndex(name="text", fields=("text",), projection_version="1"),),
                        modes=("text",),
                        default_mode="text",
                    )
                    # Commit external storage changes without touching this Server's profile registry.
                    async with db.transaction() as connection:
                        await connection.execute(
                            insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                                family=family,
                                schema_version="1",
                                declaration={
                                    "family": family,
                                    "schema_version": "1",
                                    "content_schema": {},
                                    "search_fields": [],
                                    "search": search.model_dump(mode="json"),
                                },
                            )
                        )
                        for identity in ("visible", "hidden"):
                            await custom_artifact_repository(family).create(
                                connection,
                                scope,
                                identity,
                                RepositoryArtifactDraft(
                                    family=family,
                                    content=StoredArtifactContent(
                                        schema_version="1", content={"text": "database timeout"}
                                    ),
                                ),
                            )
                    builder = ArtifactIndexBuilder(db)
                    await builder.refresh(scope, family)
                    assert await builder.build(scope, family)
                    assert family not in ARTIFACT_FAMILY_PROFILES
                    queries = []

                    def track(_conn, _cursor, statement, _parameters, _context, _many):
                        if (
                            statement.lstrip().upper().startswith("SELECT")
                            and "pc_artifact_family_schemas" in statement
                        ):
                            queries.append(statement)

                    event.listen(db.engine.sync_engine, "before_cursor_execute", track)
                    try:
                        path = f"/v1/scopes/{scope}/artifacts/{family}/search"
                        first = await client.post(path, json={"query": "timeout"})
                        assert first.status_code == 200, first.text
                        assert [item["artifact_id"] for item in first.json()["results"]] == ["visible"]
                        first_queries = len(queries)
                        queries.clear()
                        second = await client.post(path, json={"query": "timeout"})
                        assert second.status_code == 200, second.text
                        assert second.json() == first.json()
                        assert len(queries) == first_queries - 1, "Only the first search may load the missing profile"
                    finally:
                        event.remove(db.engine.sync_engine, "before_cursor_execute", track)

    asyncio.run(scenario())
