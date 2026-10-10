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
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

import httpx
import pytest
import rfc8785
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import insert, text
from starlette.middleware import Middleware

from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
    apply_atomic_memory_migration,
    plan_atomic_memory_migration,
    verify_atomic_memory_migration,
)
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, MEMORY_ENTRY_VERSIONS_TABLE
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import (
    AccessBinding,
    AccessBindingState,
    AccessConflictError,
    AccessRole,
    ArtifactOwnerRelation,
    MemoryEntrySelector,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.server.authz.service import ReplaceBinding
from powercontext.server.factory import create_server_app
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.atomic_memory_migration_backend import MigrationBackend, migration_index, migration_profile

ACTOR = PrincipalRef(type="service", id="server-token", description="PowerContext static bearer")
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
EXPIRES_AT = datetime(2030, 1, 1, tzinfo=UTC)


def _client(migration_backend: MigrationBackend) -> TestClient:
    return TestClient(
        create_server_app(
            settings=ServerSettings(
                database=migration_backend.config,
                runtime=RuntimeConfig(artifact_processing_families=()),
                access=AccessControlConfig(mode="enforced"),
                auth=BearerAuthConfig(enabled=True, token=SecretStr("migration-test-token")),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            ),
            scheduler_path=migration_backend.directory / "scheduler.db",
        ),
        headers={"Authorization": "Bearer migration-test-token"},
    )


def _snapshot(migration_backend: MigrationBackend, table: str) -> list[tuple[object, ...]]:
    async def read() -> list[tuple[object, ...]]:
        async with migration_profile(migration_backend.config) as profile, profile.database.transaction() as connection:
            result = await connection.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2"))  # noqa: S608
            return [tuple(row) for row in result]

    return asyncio.run(read())


async def _execute(migration_backend: MigrationBackend, statement: str, values: dict[str, Any]) -> None:
    async with migration_profile(migration_backend.config) as profile, profile.database.transaction() as connection:
        await connection.execute(text(statement), values)


async def _seed_legacy(
    migration_backend: MigrationBackend, scope_id: str, *, revoked: bool = False, replacement: bool = False
):
    resources = tuple(
        ResourceRef.artifact(
            scope_id, family="memory", artifact_id="memory", selector=MemoryEntrySelector(entry_id=entry_id)
        )
        for entry_id in ("legacy-entry-1", "legacy-entry-2")
    )
    versions = []
    for ordinal, resource in enumerate(resources, 1):
        assert resource.selector is not None
        entry_id = resource.selector.entry_id
        value = {"kind": "fact", "text": f"Legacy fact {ordinal}.", "source_refs": [], "artifact_refs": []}
        versions.append({
            **value,
            "entry_id": entry_id,
            "entry_version_id": f"{entry_id}-v1",
            "entry_content_hash": sha256(b"powercontext:entry-content:v1\0" + rfc8785.dumps(value)).hexdigest(),
        })
    content = {
        "schema": "powercontext.memory.v1",
        "manifest": {
            "format": "flat-v1",
            "entries": [
                {key: version[key] for key in ("entry_id", "entry_version_id", "entry_content_hash")}
                | {"state": "active"}
                for version in versions
            ],
        },
        "changes": [
            {
                "op": "add",
                "entry_id": version["entry_id"],
                "from_entry_version_id": None,
                "to_entry_version_id": version["entry_version_id"],
                "reason": None,
            }
            for version in versions
        ],
    }
    async with (
        migration_profile(migration_backend.config) as profile,
        profile.database.transaction() as connection,
    ):
        # Frozen legacy records exercise conversion independently of the current Memory models.
        await create_tables(connection, (MEMORY_ENTRY_VERSIONS_TABLE,))
        await connection.execute(
            insert(ARTIFACTS_TABLE).values(
                scope_id=scope_id,
                family="memory",
                artifact_id="memory",
                revision=1,
                content=json.dumps(content).encode(),
            )
        )
        await connection.execute(
            insert(ARTIFACT_HEADS_TABLE).values(
                scope_id=scope_id,
                family="memory",
                artifact_id="memory",
                revision=1,
                searchable_text=None,
                lifecycle_state="active",
                replacement_artifact_id=None,
                governance_generation=0,
            )
        )
        for version in versions:
            await connection.execute(
                text(
                    "INSERT INTO pc_memory_entry_versions (scope_id, family, memory_artifact_id, entry_id, entry_version_id, "
                    "version, previous_version_id, kind, text, source_refs, artifact_refs, entry_content_hash, created_in_revision) VALUES "
                    "(:scope, 'memory', 'memory', :entry_id, :entry_version_id, 1, NULL, :kind, :text, "
                    ":source_refs, :artifact_refs, :entry_content_hash, 1)"
                ),
                {**version, "scope": scope_id, "source_refs": b"[]", "artifact_refs": b"[]"},
            )
        repository = RelationalAccessRepository(profile.database, connection=connection)
        for resource in resources:
            assert resource.selector is not None
            await repository.establish_artifact_owner(
                ArtifactOwnerRelation(
                    resource=resource,
                    owner=ACTOR,
                    established_at=CREATED_AT,
                    policy_revision="pending",
                    idempotency_key=f"owner:{resource.selector.entry_id}",
                )
            )
        binding = AccessBinding(
            binding_id="legacy-grant",
            subject=PrincipalRef(type="user", id="reader"),
            resource=resources[0],
            role=AccessRole.ARTIFACT_VIEWER,
            granted_by=ACTOR,
            reason="Share exact legacy fact.",
            created_at=CREATED_AT,
            expires_at=EXPIRES_AT,
            state=AccessBindingState.ACTIVE,
            version=1,
            policy_revision="pending",
            idempotency_key="legacy-grant-key",
        )
        created = await repository.create_binding(binding)
        assert (await repository.create_binding(binding)).binding_id == created.binding_id
        if revoked:
            await repository.revoke_binding(
                created.binding_id,
                revoked_by=ACTOR,
                expected_version=created.version,
                idempotency_key="revoke-legacy",
                revoked_at=CREATED_AT,
            )
        if replacement:
            await repository.replace_binding(
                ReplaceBinding(
                    binding_id=created.binding_id,
                    expected_version=created.version,
                    subject=PrincipalRef(type="user", id="replacement-reader"),
                    idempotency_key="replace-legacy",
                    reason="Replacement grant.",
                    expires_at=EXPIRES_AT,
                ),
                actor=ACTOR,
                changed_at=CREATED_AT,
            )
    payload = {
        "subject": {"type": "user", "id": "reader"},
        "resource": {
            "type": "artifact",
            "scope_id": scope_id,
            "identity": {"family": "memory", "artifact_id": "memory"},
            "selector": {"type": "memory_entry", "entry_id": "legacy-entry-1"},
        },
        "role": "artifact.viewer",
        "reason": binding.reason,
        "expires_at": EXPIRES_AT.isoformat(),
        "idempotency_key": binding.idempotency_key,
    }
    return binding, payload


async def _apply(migration_backend: MigrationBackend):
    async with migration_profile(migration_backend.config) as profile:
        return await apply_atomic_memory_migration(
            profile.database,
            migration_index(migration_backend.config),
            maintenance_confirmed=True,
        )


def _prepare(migration_backend: MigrationBackend, **options):
    with _client(migration_backend) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
    return asyncio.run(_seed_legacy(migration_backend, scope_id, **options))


@pytest.mark.parametrize("revoked", [False, True])
def test_legacy_grant_replay_and_conflicts_survive_migration(
    migration_backend: MigrationBackend, revoked: bool
) -> None:
    original, payload = _prepare(migration_backend, revoked=revoked)
    audit = _snapshot(migration_backend, "pc_access_audit")
    receipts = _snapshot(migration_backend, "pc_access_idempotency")
    result = asyncio.run(_apply(migration_backend))
    assert result.ready, result.errors
    assert result.counts["migrated_grant_receipts"] == 1
    assert _snapshot(migration_backend, "pc_access_audit") == audit
    migrated_receipts = _snapshot(migration_backend, "pc_access_idempotency")
    assert len(migrated_receipts) == len(receipts)
    assert [row for row in migrated_receipts if row[2] != "binding.create"] == [
        row for row in receipts if row[2] != "binding.create"
    ]
    with _client(migration_backend) as client:
        legacy = client.post("/v1/access/bindings/create", json=payload)
        assert legacy.status_code == 422, legacy.text
        assert legacy.json()["error"]["code"] == "legacy_memory_operation_unsupported"
        response = client.post(
            "/v1/access/bindings/create",
            json=_atomic_payload(payload) | {"idempotency_key": "post-migration-grant"},
        )
        assert response.status_code == 201, response.text

    async def verify_role_and_current() -> None:
        async with migration_profile(migration_backend.config) as profile:
            repository = RelationalAccessRepository(profile.database)
            current = await repository.get_binding(original.binding_id)
            assert current is not None
            with pytest.raises(AccessConflictError) as error:
                await repository.create_binding(replace(current, role=AccessRole.ARTIFACT_OWNER))
            assert error.value.code == "idempotency-key"
            async with profile.database.transaction() as connection:
                report = await verify_atomic_memory_migration(
                    connection, index=migration_index(migration_backend.config)
                )
                assert report.ready, report.errors

    asyncio.run(verify_role_and_current())


def test_apply_repairs_receipts_left_by_completed_migration(migration_backend: MigrationBackend) -> None:
    original, payload = _prepare(migration_backend, replacement=True)
    legacy_receipt = next(
        row
        for row in _snapshot(migration_backend, "pc_access_idempotency")
        if row[2] == "binding.create" and row[4] == original.binding_id
    )
    assert asyncio.run(_apply(migration_backend)).ready
    asyncio.run(
        _execute(
            migration_backend,
            "UPDATE pc_access_idempotency SET payload_hash = :payload_hash "
            "WHERE actor_id = :actor_id AND idempotency_key_hash = :key_hash",
            {"payload_hash": legacy_receipt[3], "actor_id": legacy_receipt[0], "key_hash": legacy_receipt[1]},
        )
    )

    async def verify_before_repair() -> None:
        async with (
            migration_profile(migration_backend.config) as profile,
            profile.database.transaction() as connection,
        ):
            for report in (
                await verify_atomic_memory_migration(connection, index=migration_index(migration_backend.config)),
                await plan_atomic_memory_migration(connection, index=migration_index(migration_backend.config)),
            ):
                assert not report.ready
                assert any("rerun apply" in error for error in report.errors), report.errors
                assert report.counts["pending_grant_receipts"] == 1

    asyncio.run(verify_before_repair())
    before = _snapshot(migration_backend, "pc_access_relationships")
    audit = _snapshot(migration_backend, "pc_access_audit")
    noncreate = [row for row in _snapshot(migration_backend, "pc_access_idempotency") if row[2] != "binding.create"]
    result = asyncio.run(_apply(migration_backend))
    assert result.ready, result.errors
    assert result.counts["imported_entries"] == 0
    assert result.counts["migrated_grant_receipts"] == 1
    assert _snapshot(migration_backend, "pc_access_relationships") == before
    assert _snapshot(migration_backend, "pc_access_audit") == audit
    assert [
        row for row in _snapshot(migration_backend, "pc_access_idempotency") if row[2] != "binding.create"
    ] == noncreate
    with _client(migration_backend) as client:
        legacy = client.post("/v1/access/bindings/create", json=payload)
        assert legacy.status_code == 422, legacy.text
        replacement = client.post(
            "/v1/access/bindings/replace",
            json={
                "binding_id": original.binding_id,
                "expected_version": 1,
                "idempotency_key": "replace-legacy",
                "replacement": {
                    "subject": {"type": "user", "id": "replacement-reader"},
                    "reason": "Replacement grant.",
                    "expires_at": EXPIRES_AT.isoformat(),
                },
            },
        )
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["previous"]["binding_id"] == original.binding_id
        assert replacement.json()["current"]["subject"]["id"] == "replacement-reader"
    repeated = asyncio.run(_apply(migration_backend))
    assert repeated.ready, repeated.errors
    assert repeated.counts["migrated_grant_receipts"] == 0


@pytest.mark.parametrize("mutation", ["hash", "actor", "result", "missing"])
@pytest.mark.parametrize("already_migrated", [False, True])
def test_migration_rejects_unverifiable_grant_receipts(
    migration_backend: MigrationBackend, mutation: str, already_migrated: bool
) -> None:
    original, _payload = _prepare(migration_backend)
    if already_migrated:
        assert asyncio.run(_apply(migration_backend)).ready
    if mutation == "missing":
        statement = "DELETE FROM pc_access_idempotency WHERE result_binding_id = :binding_id"
        values = {"binding_id": original.binding_id}
    else:
        column, value = {
            "hash": ("payload_hash", "0" * 64),
            "actor": ("actor_id", "other-actor"),
            "result": ("result_binding_id", "other-binding"),
        }[mutation]
        statement = f"UPDATE pc_access_idempotency SET {column} = :value WHERE result_binding_id = :binding_id"  # noqa: S608
        values = {"value": value, "binding_id": original.binding_id}
    asyncio.run(_execute(migration_backend, statement, values))
    before = _snapshot(migration_backend, "pc_access_idempotency")
    result = asyncio.run(_apply(migration_backend))
    assert not result.ready
    assert any("receipt" in error for error in result.errors), result.errors
    assert _snapshot(migration_backend, "pc_access_idempotency") == before


@pytest.mark.parametrize("entrypoint", ["factory", "adapter"])
def test_server_startup_keeps_grant_projection_current(migration_backend: MigrationBackend, entrypoint: str) -> None:
    _original, payload = _prepare(migration_backend)
    assert asyncio.run(_apply(migration_backend)).ready

    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            if entrypoint == "factory":
                app = cast(FastAPI, _client(migration_backend).app)
            else:
                access = await resources.enter_async_context(
                    open_builtin_access_control(
                        migration_backend.config,
                        bootstrap_administrators=(ACTOR,),
                    )
                )
                runtime = await resources.enter_async_context(
                    open_builtin_runtime(
                        BuiltinConfig(
                            database=migration_backend.config, runtime=RuntimeConfig(artifact_processing_families=())
                        ),
                        scheduler_path=migration_backend.directory / "adapter-scheduler.db",
                    )
                )
                authentication = StaticBearerAuthenticationProvider("migration-test-token", ACTOR)
                app = create_app(
                    application=cast(ServerApplication, runtime),
                    access_control=access,
                    access_mode="enforced",
                    authentication_provider=authentication,
                    middleware=(Middleware(AuthenticationMiddleware, provider=authentication),),
                )
            await resources.enter_async_context(app.router.lifespan_context(app))
            client = await resources.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                    headers={"Authorization": "Bearer migration-test-token"},
                )
            )

            async def verify() -> None:
                async with (
                    migration_profile(migration_backend.config) as profile,
                    profile.database.transaction() as connection,
                ):
                    report = await verify_atomic_memory_migration(
                        connection, index=migration_index(migration_backend.config)
                    )
                    assert report.ready, report.errors

            response = await client.post(
                "/v1/access/bindings/create",
                json=_atomic_payload(payload)
                | {
                    "subject": {"type": "user", "id": "startup-reader"},
                    "idempotency_key": "startup-grant",
                },
            )
            assert response.status_code == 201, response.text
            binding = response.json()
            await verify()
            response = await client.post(
                "/v1/access/bindings/replace",
                json={
                    "binding_id": binding["binding_id"],
                    "expected_version": binding["version"],
                    "idempotency_key": "startup-replace",
                    "replacement": {"subject": {"type": "user", "id": "startup-replacement-reader"}},
                },
            )
            assert response.status_code == 200, response.text
            replacement = response.json()["current"]
            await verify()
            response = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": replacement["binding_id"],
                    "expected_version": replacement["version"],
                    "idempotency_key": "startup-revoke",
                },
            )
            assert response.status_code == 200, response.text
            await verify()

    asyncio.run(scenario())


def _atomic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    resource = payload["resource"]
    artifact_id = legacy_entry_artifact_id(
        resource["scope_id"], resource["identity"]["artifact_id"], resource["selector"]["entry_id"]
    )
    return payload | {
        "resource": {
            "type": "artifact",
            "scope_id": resource["scope_id"],
            "identity": {"family": "atomic-memory", "artifact_id": artifact_id},
        }
    }
