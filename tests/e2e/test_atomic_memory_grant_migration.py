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
import sqlite3
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
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
    verify_atomic_memory_migration_authority,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE
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

ACTOR = PrincipalRef(type="service", id="server-token", description="PowerContext static bearer")
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
EXPIRES_AT = datetime(2030, 1, 1, tzinfo=UTC)


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_server_app(
            settings=ServerSettings(
                database=_config(tmp_path),
                runtime=RuntimeConfig(artifact_processing_families=()),
                access=AccessControlConfig(mode="enforced"),
                auth=BearerAuthConfig(enabled=True, token=SecretStr("migration-test-token")),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            ),
            scheduler_path=tmp_path / "scheduler.db",
        ),
        headers={"Authorization": "Bearer migration-test-token"},
    )


def _config(tmp_path: Path) -> SQLiteConfig:
    return SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")


def _snapshot(tmp_path: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(tmp_path / "migration.db") as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()  # noqa: S608


async def _seed_legacy(
    tmp_path: Path, scope_id: str, *, revoked: bool = False, replacement: bool = False, artifact_evidence: bool = False
):
    resources = tuple(
        ResourceRef.artifact(
            scope_id, family="memory", artifact_id="memory", selector=MemoryEntrySelector(entry_id=entry_id)
        )
        for entry_id in ("legacy-entry-1", "legacy-entry-2")
    )
    evidence = (
        [{"family": "experience", "artifact_id": "migration-evidence", "revision": 1}] if artifact_evidence else []
    )
    versions = []
    for ordinal, resource in enumerate(resources, 1):
        assert resource.selector is not None
        entry_id = resource.selector.entry_id
        value = {"kind": "fact", "text": f"Legacy fact {ordinal}.", "source_refs": [], "artifact_refs": evidence}
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
        SQLiteProfile.open(_config(tmp_path), tables=()) as profile,
        profile.database.transaction() as connection,
    ):
        if artifact_evidence:
            await connection.execute(
                insert(ARTIFACTS_TABLE).values(
                    scope_id=scope_id,
                    family="experience",
                    artifact_id="migration-evidence",
                    revision=1,
                    content=json.dumps({
                        "situation": "Preserve migration evidence.",
                        "action": "Verify exact imports.",
                        "outcome": "Evidence remains readable.",
                        "lesson": "Keep lineage separate from merge inputs.",
                    }).encode(),
                )
            )
            await connection.execute(
                insert(ARTIFACT_HEADS_TABLE).values(
                    scope_id=scope_id,
                    family="experience",
                    artifact_id="migration-evidence",
                    revision=1,
                    searchable_text=None,
                    lifecycle_state="active",
                    replacement_artifact_id=None,
                    governance_generation=0,
                )
            )
        # Frozen legacy records exercise conversion independently of the current Memory models.
        await connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS pc_memory_entry_versions (scope_id TEXT, family TEXT, memory_artifact_id TEXT, "
                "entry_id TEXT, entry_version_id TEXT, version INTEGER, previous_version_id TEXT, kind TEXT, "
                "text TEXT, source_refs BLOB, artifact_refs BLOB, entry_content_hash TEXT, created_in_revision INTEGER)"
            )
        )
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
                    "INSERT INTO pc_memory_entry_versions VALUES "
                    "(:scope, 'memory', 'memory', :entry_id, :entry_version_id, 1, NULL, :kind, :text, "
                    ":source_refs, :artifact_refs, :entry_content_hash, 1)"
                ),
                {**version, "scope": scope_id, "source_refs": b"[]", "artifact_refs": json.dumps(evidence).encode()},
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


async def _apply(tmp_path: Path):
    async with SQLiteProfile.open(_config(tmp_path), tables=()) as profile:
        return await apply_atomic_memory_migration(
            profile.database,
            SQLiteAtomicMemoryIndex(),
            maintenance_confirmed=True,
        )


def _prepare(tmp_path: Path, **options):
    with _client(tmp_path) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
    return asyncio.run(_seed_legacy(tmp_path, scope_id, **options))


@pytest.mark.parametrize("revoked", [False, True])
def test_legacy_grant_replay_and_conflicts_survive_migration(tmp_path: Path, revoked: bool) -> None:
    original, payload = _prepare(tmp_path, revoked=revoked)
    audit = _snapshot(tmp_path, "pc_access_audit")
    receipts = _snapshot(tmp_path, "pc_access_idempotency")
    result = asyncio.run(_apply(tmp_path))
    assert result.ready, result.errors
    assert result.counts["migrated_grant_receipts"] == 1
    assert _snapshot(tmp_path, "pc_access_audit") == audit
    migrated_receipts = _snapshot(tmp_path, "pc_access_idempotency")
    assert len(migrated_receipts) == len(receipts)
    assert [row for row in migrated_receipts if row[2] != "binding.create"] == [
        row for row in receipts if row[2] != "binding.create"
    ]
    with _client(tmp_path) as client:
        legacy = client.post("/v1/access/bindings/create", json=payload)
        assert legacy.status_code == 422, legacy.text
        assert legacy.json()["error"]["code"] == "legacy_memory_operation_unsupported"
        response = client.post(
            "/v1/access/bindings/create",
            json=_atomic_payload(payload) | {"idempotency_key": "post-migration-grant"},
        )
        assert response.status_code == 201, response.text

    async def verify_role_and_current() -> None:
        async with SQLiteProfile.open(_config(tmp_path), tables=()) as profile:
            repository = RelationalAccessRepository(profile.database)
            current = await repository.get_binding(original.binding_id)
            assert current is not None
            with pytest.raises(AccessConflictError) as error:
                await repository.create_binding(replace(current, role=AccessRole.ARTIFACT_OWNER))
            assert error.value.code == "idempotency-key"
            async with profile.database.transaction() as connection:
                report = await verify_atomic_memory_migration(connection, index=SQLiteAtomicMemoryIndex())
                assert report.ready, report.errors

    asyncio.run(verify_role_and_current())


def test_apply_repairs_receipts_left_by_completed_migration(tmp_path: Path) -> None:
    original, payload = _prepare(tmp_path, replacement=True)
    legacy_receipt = next(
        row
        for row in _snapshot(tmp_path, "pc_access_idempotency")
        if row[2] == "binding.create" and row[4] == original.binding_id
    )
    assert asyncio.run(_apply(tmp_path)).ready
    with sqlite3.connect(tmp_path / "migration.db") as connection:
        connection.execute(
            "UPDATE pc_access_idempotency SET payload_hash = ? WHERE actor_id = ? AND idempotency_key_hash = ?",
            (legacy_receipt[3], legacy_receipt[0], legacy_receipt[1]),
        )

    async def verify_before_repair() -> None:
        async with (
            SQLiteProfile.open(_config(tmp_path), tables=()) as profile,
            profile.database.transaction() as connection,
        ):
            for report in (
                await verify_atomic_memory_migration(connection, index=SQLiteAtomicMemoryIndex()),
                await plan_atomic_memory_migration(connection, index=SQLiteAtomicMemoryIndex()),
            ):
                assert not report.ready
                assert any("rerun apply" in error for error in report.errors), report.errors
                assert report.counts["pending_grant_receipts"] == 1

    asyncio.run(verify_before_repair())
    before = _snapshot(tmp_path, "pc_access_relationships")
    audit = _snapshot(tmp_path, "pc_access_audit")
    noncreate = [row for row in _snapshot(tmp_path, "pc_access_idempotency") if row[2] != "binding.create"]
    result = asyncio.run(_apply(tmp_path))
    assert result.ready, result.errors
    assert result.counts["imported_entries"] == 0
    assert result.counts["migrated_grant_receipts"] == 1
    assert _snapshot(tmp_path, "pc_access_relationships") == before
    assert _snapshot(tmp_path, "pc_access_audit") == audit
    assert [row for row in _snapshot(tmp_path, "pc_access_idempotency") if row[2] != "binding.create"] == noncreate
    with _client(tmp_path) as client:
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
    repeated = asyncio.run(_apply(tmp_path))
    assert repeated.ready, repeated.errors
    assert repeated.counts["migrated_grant_receipts"] == 0


@pytest.mark.parametrize("mutation", ["hash", "actor", "result", "missing"])
@pytest.mark.parametrize("already_migrated", [False, True])
def test_migration_rejects_unverifiable_grant_receipts(tmp_path: Path, mutation: str, already_migrated: bool) -> None:
    original, _payload = _prepare(tmp_path)
    if already_migrated:
        assert asyncio.run(_apply(tmp_path)).ready
    with sqlite3.connect(tmp_path / "migration.db") as connection:
        if mutation == "missing":
            connection.execute("DELETE FROM pc_access_idempotency WHERE result_binding_id = ?", (original.binding_id,))
        else:
            column, value = {
                "hash": ("payload_hash", "0" * 64),
                "actor": ("actor_id", "other-actor"),
                "result": ("result_binding_id", "other-binding"),
            }[mutation]
            connection.execute(
                f"UPDATE pc_access_idempotency SET {column} = ? WHERE result_binding_id = ?",  # noqa: S608
                (value, original.binding_id),
            )
    before = _snapshot(tmp_path, "pc_access_idempotency")
    result = asyncio.run(_apply(tmp_path))
    assert not result.ready
    assert any("receipt" in error for error in result.errors), result.errors
    assert _snapshot(tmp_path, "pc_access_idempotency") == before


@pytest.mark.parametrize("entrypoint", ["factory", "adapter"])
def test_server_startup_keeps_grant_projection_current(tmp_path: Path, entrypoint: str) -> None:
    _original, payload = _prepare(tmp_path)
    assert asyncio.run(_apply(tmp_path)).ready

    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            if entrypoint == "factory":
                app = cast(FastAPI, _client(tmp_path).app)
            else:
                access = await resources.enter_async_context(
                    open_builtin_access_control(
                        _config(tmp_path),
                        bootstrap_administrators=(ACTOR,),
                    )
                )
                runtime = await resources.enter_async_context(
                    open_builtin_runtime(
                        BuiltinConfig(
                            database=_config(tmp_path), runtime=RuntimeConfig(artifact_processing_families=())
                        ),
                        scheduler_path=tmp_path / "adapter-scheduler.db",
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
                    SQLiteProfile.open(_config(tmp_path), tables=()) as profile,
                    profile.database.transaction() as connection,
                ):
                    report = await verify_atomic_memory_migration(connection, index=SQLiteAtomicMemoryIndex())
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


def test_offline_inspection_and_apply_upgrade_released_common_columns(tmp_path: Path) -> None:
    _prepare(tmp_path, artifact_evidence=True)
    database = tmp_path / "migration.db"
    with sqlite3.connect(database) as connection:
        for table, columns in (
            (
                "pc_artifact_heads",
                "scope_id, family, artifact_id, revision, searchable_text, lifecycle_state, "
                "replacement_artifact_id, governance_generation",
            ),
            (
                "pc_artifact_lineage_artifacts",
                "scope_id, family, artifact_id, revision, ordinal, "
                "upstream_family, upstream_artifact_id, upstream_revision",
            ),
        ):
            connection.execute(f"CREATE TABLE released AS SELECT {columns} FROM {table}")  # noqa: S608
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE released RENAME TO {table}")
            if table == "pc_artifact_heads":
                connection.execute(
                    "CREATE UNIQUE INDEX released_head_identity ON pc_artifact_heads(scope_id, family, artifact_id)"
                )
        connection.execute("PRAGMA journal_mode = DELETE")
    original = database.read_bytes()

    async def inspect() -> None:
        async with (
            SQLiteProfile.open_readonly(_config(tmp_path)) as profile,
            profile.database.transaction() as connection,
        ):
            plan = await plan_atomic_memory_migration(connection)
            assert plan.counts["pending_entries"] == 2
            assert not plan.ready
            for report in (
                await verify_atomic_memory_migration(connection),
                await verify_atomic_memory_migration_authority(connection),
            ):
                assert not report.ready
                assert any("merge columns" in error for error in report.errors), report.errors

    asyncio.run(inspect())
    assert database.read_bytes() == original
    result = asyncio.run(_apply(tmp_path))
    assert result.ready, result.errors
    assert result.counts["imported_entries"] == 2
    with sqlite3.connect(database) as connection:
        heads = connection.execute(
            "SELECT lifecycle_state, governance_generation, merged_into_id, revision "
            "FROM pc_artifact_heads WHERE family = 'atomic-memory' ORDER BY artifact_id"
        ).fetchall()
        assert heads == [("active", 0, None, 1), ("active", 0, None, 1)]
        assert connection.execute(
            "SELECT DISTINCT is_merge_input FROM pc_artifact_lineage_artifacts WHERE family = 'atomic-memory'"
        ).fetchall() == [(0,)]
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name = 'pc_atomic_memory_states'").fetchone()
            is None
        )


def test_migration_replay_preserves_evolved_head_and_imported_history(tmp_path: Path) -> None:
    _prepare(tmp_path)
    assert asyncio.run(_apply(tmp_path)).ready
    with sqlite3.connect(tmp_path / "migration.db") as connection:
        target = connection.execute(
            "SELECT artifact_id FROM pc_artifact_heads WHERE family = 'atomic-memory' ORDER BY artifact_id LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE pc_artifact_heads SET lifecycle_state = 'deprecated', governance_generation = 4 "
            "WHERE family = 'atomic-memory' AND artifact_id = ?",
            (target,),
        )
        connection.execute("DELETE FROM pc_atomic_memory_current WHERE artifact_id = ?", (target,))
        connection.execute("DELETE FROM pc_atomic_memory_current_fts WHERE artifact_id = ?", (target,))
    retained = {
        table: _snapshot(tmp_path, table)
        for table in (
            "pc_artifacts",
            "pc_artifact_heads",
            "pc_artifact_lineage_artifacts",
            "pc_access_owners",
            "pc_artifact_tags",
            "pc_access_relationships",
            "pc_access_idempotency",
        )
    }
    repeated = asyncio.run(_apply(tmp_path))
    assert repeated.ready, repeated.errors
    assert repeated.counts["imported_entries"] == 0
    for table, rows in retained.items():
        assert _snapshot(tmp_path, table) == rows


def test_verification_rejects_imported_evidence_marked_as_merge_input(tmp_path: Path) -> None:
    _prepare(tmp_path, artifact_evidence=True)
    assert asyncio.run(_apply(tmp_path)).ready
    with sqlite3.connect(tmp_path / "migration.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM pc_artifact_lineage_artifacts WHERE family = 'atomic-memory' AND is_merge_input = 0"
            ).fetchone()[0]
            == 2
        )
        connection.execute("UPDATE pc_artifact_lineage_artifacts SET is_merge_input = 1 WHERE family = 'atomic-memory'")

    async def scenario() -> None:
        async with (
            SQLiteProfile.open_readonly(_config(tmp_path)) as profile,
            profile.database.transaction() as connection,
        ):
            report = await verify_atomic_memory_migration(connection)
            assert not report.ready
            assert any("imported exact evidence differs" in error for error in report.errors), report.errors

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
