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

"""HTTP writes recover through an independent, fault-injected relationship store."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from typing import cast

import httpx
import pytest
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.cli.app import create_cli
from powercontext.server.authentication import AuthenticationResult, ProviderReadiness
from powercontext.server.authz import AccessUnavailableError, PrincipalRef, RelationshipStore, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings

ALICE = PrincipalRef(type="user", id="alice")


class _Authentication:
    async def authenticate(self, request):
        identity = request.headers.get("authorization", "Bearer alice").removeprefix("Bearer ")
        return AuthenticationResult(subject=PrincipalRef(type="user", id=identity))

    async def readiness(self):
        return ProviderReadiness(ready=True)


class _InterruptedRelationships:
    def __init__(self, repository):
        self.repository = repository
        self.unavailable = False

    def __getattr__(self, name):
        return getattr(self.repository, name)

    async def establish_artifact_owner(self, relation):
        if self.unavailable:
            raise AccessUnavailableError("external_owner_unavailable")
        return await self.repository.establish_artifact_owner(relation)

    async def attest_candidate_owner(self, attestation):
        if self.unavailable:
            raise AccessUnavailableError("external_owner_unavailable")
        return await self.repository.attest_candidate_owner(attestation)


@asynccontextmanager
async def _server(tmp_path, *, interrupted=False):
    primary = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'domain.db'}")
    external = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'relationships.db'}")
    async with open_builtin_access_control(
        external, bootstrap_administrators=(ALICE,), deployment_id="external-test"
    ) as access:
        writer = _InterruptedRelationships(access.relationships)
        access.relationships = cast("RelationshipStore", writer)
        app = create_server_app(
            settings=ServerSettings(
                database=primary,
                runtime=RuntimeConfig(artifact_processing_families=()),
                access=AccessControlConfig(mode="enforced", deployment_id="external-test"),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            ),
            access_control=access,
            authentication_provider=_Authentication(),
            scheduler_path=tmp_path / "scheduler.db",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        ):
            writer.unavailable = interrupted
            yield app, client, access


async def _scope(client):
    response = await client.post(
        "/v1/scopes",
        json={"title": "External ownership", "summary": "Recovery", "idempotency_key": "external-scope"},
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


def test_http_creation_and_restart_recover_the_original_owner(tmp_path):
    async def scenario():
        async with _server(tmp_path, interrupted=True) as (app, client, access):
            scope_id = await _scope(client)
            response = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "profile", "content": {"content": "# Original private profile"}},
            )
            assert response.status_code == 503, response.text
            pending = await app.state.owner_recovery.pending()
            assert len(pending) == 1 and pending[0].principal == ALICE
            resource = ResourceRef.artifact(scope_id, family="profile", artifact_id="profile")
            assert await access.artifact_owner(resource) is None
            records = await app.state.application.records.for_scope(scope_id).logical_artifacts()
            assert len(records) == 1
        async with _server(tmp_path) as (app, client, access):
            # No request Principal is bound while the durable background task repairs.
            async with asyncio.timeout(12):
                while await app.state.owner_recovery.pending():  # noqa: ASYNC110 - poll durable state, no process-local event
                    await asyncio.sleep(0.05)
            assert await app.state.owner_recovery.pending() == ()
            relation = await access.artifact_owner(resource)
            assert relation is not None and relation.owner == ALICE
            restored = await client.get(f"/v1/scopes/{scope_id}/artifacts/profile/profile")
            assert restored.status_code == 200, restored.text
            assert restored.json()["content"]["content"] == "# Original private profile\n"
            denied = await client.get(
                f"/v1/scopes/{scope_id}/artifacts/profile/profile", headers={"Authorization": "Bearer bob"}
            )
            assert denied.status_code == 403, denied.text
            records = await app.state.application.records.for_scope(scope_id).logical_artifacts()
            assert len(records) == 1
            audit = await access.audit.list_audit(resource=resource, limit=100)
            owners = [record for record in audit if record.reason_code == "artifact-owner-established"]
            assert len(owners) == 1 and owners[0].principal == ALICE

    asyncio.run(scenario())


def test_candidate_http_failure_retains_a_replayable_proposer_attestation(tmp_path):
    async def scenario():
        async with _server(tmp_path, interrupted=True) as (app, client, access):
            scope_id = await _scope(client)
            source = await client.post(
                f"/v1/scopes/{scope_id}/sources", json={"content": "A verified recovery observation."}
            )
            assert source.status_code == 201, source.text
            response = await client.post(
                "/v1/experience/propose",
                json={
                    "scope_id": scope_id,
                    "proposal": {
                        "situation": "An external owner write fails.",
                        "action": "Persist a trusted recovery intent.",
                        "outcome": "The original identity can be restored.",
                        "lesson": "Do not infer ownership from the retrying caller.",
                    },
                    "source_refs": [{"name": "content", "source_id": source.json()["source_id"]}],
                    "artifact_refs": [],
                },
            )
            assert response.status_code == 503, response.text
            pending = await app.state.owner_recovery.pending()
            assert len(pending) == 1 and pending[0].kind == "candidate"
            candidate_id = pending[0].candidate_id
            assert candidate_id is not None
            assert await access.candidate_owner(scope_id, candidate_id) is None
        async with _server(tmp_path) as (app, client, access):
            pending = await app.state.owner_recovery.pending()
            await app.state.owner_recovery.replay(pending[0].operation_id)
            attestation = await access.candidate_owner(scope_id, candidate_id)
            assert attestation is not None and attestation.proposed_owner == ALICE
            response = await client.post(
                "/v1/candidates/get", json={"scope_id": scope_id, "candidate_id": candidate_id}
            )
            assert response.status_code == 200, response.text
            assert response.json()["candidate_id"] == candidate_id

    asyncio.run(scenario())


@pytest.mark.parametrize(("kind", "count"), [("artifact", 1), ("candidate", 101)])
def test_ownership_migration_cannot_replace_pending_original_attribution(tmp_path, kind, count):
    async def scenario():
        async with _server(tmp_path, interrupted=True) as (app, client, access):
            scope_id = await _scope(client)
            if kind == "artifact":
                created = await client.post(
                    f"/v1/scopes/{scope_id}/artifacts",
                    json={"family": "profile", "content": {"content": "# Original private profile"}},
                )
                assert created.status_code == 503, created.text
            else:
                source = await client.post(f"/v1/scopes/{scope_id}/sources", json={"content": "Original evidence."})
                assert source.status_code == 201, source.text
                for index in range(count):
                    created = await client.post(
                        "/v1/experience/propose",
                        json={
                            "scope_id": scope_id,
                            "proposal": {
                                "situation": f"Observation {index}.",
                                "action": "Retain the original proposer.",
                                "outcome": "Ownership delivery is pending.",
                                "lesson": "Preserve recorded attribution.",
                            },
                            "source_refs": [{"name": "content", "source_id": source.json()["source_id"]}],
                            "artifact_refs": [],
                        },
                    )
                    assert created.status_code == 503, created.text
            pending = await app.state.owner_recovery.pending(scope_id=scope_id, limit=500)
            assert len(pending) == count
            for dry_run in (True, False):
                response = await client.post(
                    "/v1/access/ownership/apply",
                    json={"scope_ids": [scope_id], "owner": {"type": "user", "id": "bob"}, "dry_run": dry_run},
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["applied"] == []
                assert len(result["conflicts"]) == count
                assert {item["conflict_code"] for item in result["conflicts"]} == {"owner-recovery-pending"}
            access.relationships.unavailable = False
            for intent in pending:
                await app.state.owner_recovery.replay(intent.operation_id)
                if intent.resource is not None:
                    relation = await access.artifact_owner(intent.resource)
                    assert relation is not None and relation.owner == ALICE
                else:
                    attestation = await access.candidate_owner(scope_id, intent.candidate_id)
                    assert attestation is not None and attestation.proposed_owner == ALICE
            assert await app.state.owner_recovery.pending(scope_id=scope_id) == ()

    asyncio.run(scenario())


def test_migration_preserves_completed_ownership_evidence_after_external_relation_loss(tmp_path):
    async def scenario():
        async with _server(tmp_path, interrupted=True) as (app, client, access):
            scope_id = await _scope(client)
            created = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "profile", "content": {"content": "# Original private profile"}},
            )
            assert created.status_code == 503, created.text
            (intent,) = await app.state.owner_recovery.pending(scope_id=scope_id)
            access.relationships.unavailable = False
            await app.state.owner_recovery.replay(intent.operation_id)
            # Model out-of-band loss in the external relationship store after delivery was acknowledged.
            with closing(sqlite3.connect(tmp_path / "relationships.db")) as connection:
                connection.execute("DELETE FROM pc_access_owners WHERE owner_kind = 'artifact'")
                connection.commit()
            for dry_run in (True, False):
                response = await client.post(
                    "/v1/access/ownership/apply",
                    json={"scope_ids": [scope_id], "owner": {"type": "user", "id": "bob"}, "dry_run": dry_run},
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["applied"] == []
                assert [item["conflict_code"] for item in result["conflicts"]] == [
                    "owner-recovery-delivered_relation_missing"
                ]
            assert await access.artifact_owner(intent.resource) is None
            assert await app.state.owner_recovery.pending(scope_id=scope_id) == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("completed", [False, True])
def test_offline_migration_preserves_external_owner_evidence(tmp_path, completed):
    from powercontext.server.cli import app as server_cli

    async def seed():
        async with _server(tmp_path, interrupted=True) as (app, client, access):
            scope_id = await _scope(client)
            created = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "profile", "content": {"content": "# Original private profile"}},
            )
            assert created.status_code == 503, created.text
            (intent,) = await app.state.owner_recovery.pending(scope_id=scope_id)
            if completed:
                access.relationships.unavailable = False
                await app.state.owner_recovery.replay(intent.operation_id)
            return scope_id, intent

    scope_id, intent = asyncio.run(seed())
    environment = tmp_path / "offline.env"
    environment.write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        f"POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{(tmp_path / 'domain.db').as_posix()}\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=disabled\n"
        "POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID=external-test\n"
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES=[]\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        create_cli([server_cli]),
        [
            "server",
            "access-ownership-migrate",
            "--env-file",
            str(environment),
            "--action",
            "apply",
            "--owner",
            "user:bob",
            "--scope-id",
            scope_id,
            "--maintenance-confirmed",
        ],
    )
    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    assert report["applied"] == []
    reason = "delivered_relation_missing" if completed else "pending"
    assert [item["detail"] for item in report["conflicts"]] == [f"owner-recovery-{reason}"]

    async def verify():
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'domain.db'}")
        async with open_builtin_access_control(database, deployment_id="external-test") as local:
            assert await local.artifact_owner(intent.resource) is None
        async with _server(tmp_path) as (app, _, access):
            await app.state.owner_recovery.replay(intent.operation_id)
            relation = await access.artifact_owner(intent.resource)
            assert relation is not None and relation.owner == ALICE
            assert await app.state.owner_recovery.pending(scope_id=scope_id) == ()

    asyncio.run(verify())


def test_migration_retries_complete_external_owner_audit(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            await app.state.application.records.for_scope(scope_id).create_artifact(
                "profile", ArtifactWrite(content={"content": "# Legacy profile"})
            )
            resource = ResourceRef.artifact(scope_id, family="profile", artifact_id="profile")
            payload = {"scope_ids": [scope_id], "owner": {"type": ALICE.type, "id": ALICE.id}}

            async def unavailable(event):
                if event.reason_code == "artifact-owner-established":
                    raise AccessUnavailableError("external_audit_unavailable")
                return await append(event)

            append = access.audit.append_audit
            with monkeypatch.context() as patch:
                patch.setattr(access.audit, "append_audit", unavailable)
                patch.setattr(access.audit, "append_audit_once", unavailable)
                for _ in range(2):
                    response = await client.post("/v1/access/ownership/apply", json=payload)
                    assert response.status_code == 503, response.text
                    relation = await access.artifact_owner(resource)
                    assert relation is not None and relation.owner == ALICE
                    pending = await app.state.owner_recovery.pending(scope_id=scope_id)
                    assert len(pending) == 1 and pending[0].resource == resource
                preview = await client.post("/v1/access/ownership/apply", json={**payload, "dry_run": True})
                assert preview.status_code == 200, preview.text
                assert await app.state.owner_recovery.pending(scope_id=scope_id) == pending
            response = await client.post("/v1/access/ownership/apply", json=payload)
            assert response.status_code == 200, response.text
            assert await app.state.owner_recovery.pending(scope_id=scope_id) == ()
            audit = await access.audit.list_audit(resource=resource, limit=100)
            owners = [event for event in audit if event.reason_code == "artifact-owner-established"]
            assert len(owners) == 1 and owners[0].principal == ALICE
            assert owners[0].operation == "access.ownership.apply"
            response = await client.post("/v1/access/ownership/apply", json=payload)
            assert response.status_code == 200, response.text
            audit = await access.audit.list_audit(resource=resource, limit=100)
            assert [event for event in audit if event.reason_code == "artifact-owner-established"] == owners

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["publication", "handoff"])
def test_idempotent_retry_requires_the_original_owner_audit_to_complete(tmp_path, monkeypatch, operation):
    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            source = await client.post(f"/v1/scopes/{scope_id}/sources", json={"content": "Verified task state."})
            assert source.status_code == 201, source.text
            statement = {
                "text": "Continue the verified task.",
                "citations": [
                    {"kind": "source", "source_ref": {"name": "content", "source_id": source.json()["source_id"]}}
                ],
            }
            content = {
                "schema": "powercontext.handoff.v1",
                "objective": "Continue verified work.",
                "state": [statement],
                "disposition": "continuable",
                "next_action": statement,
                "omissions": [],
            }
            destination = scope_id
            path = "/v1/handoff/commit"
            payload = {
                "scope_id": scope_id,
                "handoff": {
                    "schema": "powercontext.prepared-handoff.v1",
                    "scope_id": scope_id,
                    "base": None,
                    "content": content,
                },
            }
            if operation == "publication":
                created = await client.post(
                    f"/v1/scopes/{scope_id}/artifacts", json={"family": "handoff", "content": content}
                )
                assert created.status_code == 201, created.text
                target = await client.post(
                    "/v1/scopes",
                    json={"title": "Publication target", "summary": "Audit retry", "idempotency_key": "audit-target"},
                )
                assert target.status_code == 201, target.text
                destination = target.json()["scope_id"]
                path = "/v1/artifact-publications"
                payload = {
                    "source": {
                        "scope_id": scope_id,
                        "artifact": {
                            "family": "handoff",
                            "artifact_id": "handoff",
                            "revision": created.json()["revision"],
                        },
                    },
                    "target_scope_id": destination,
                    "idempotency_key": "audit-publication",
                }

            async def unavailable(_event):
                raise AccessUnavailableError("external_audit_unavailable")

            with monkeypatch.context() as patch:
                patch.setattr(access.audit, "append_audit_once", unavailable)
                failed = await client.post(
                    path, json=payload, headers={"X-PowerContext-Request-ID": "accepted-owner-request"}
                )
                assert failed.status_code == 503, failed.text
                original_request_id = failed.headers["X-PowerContext-Request-ID"]
                pending = await app.state.owner_recovery.pending()
                assert len(pending) == 1
                resource = pending[0].resource
                assert resource is not None
                assert (await access.artifact_owner(resource)).owner == ALICE
                retried = await client.post(
                    path, json=payload, headers={"X-PowerContext-Request-ID": "retry-owner-request"}
                )
                assert retried.status_code == 503, retried.text
                assert await app.state.owner_recovery.pending() == pending
                audit = await access.audit.list_audit(resource=resource, limit=100)
                assert not [item for item in audit if item.reason_code == "artifact-owner-established"]
            retried = await client.post(path, json=payload)
            assert retried.is_success, retried.text
            assert await app.state.owner_recovery.pending() == ()
            audit = await access.audit.list_audit(resource=resource, limit=100)
            owners = [item for item in audit if item.reason_code == "artifact-owner-established"]
            assert len(owners) == 1
            assert owners[0].principal == ALICE and owners[0].request_id == original_request_id
            assert len(await app.state.application.records.for_scope(destination).logical_artifacts()) == 1
            assert (await client.post(path, json=payload)).json() == retried.json()
            audit = await access.audit.list_audit(resource=resource, limit=100)
            assert [item for item in audit if item.reason_code == "artifact-owner-established"] == owners

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("activation_mode", "failure"),
    [("automatic", "relationship"), ("automatic", "audit"), ("review_required", "relationship")],
)
def test_profile_flush_retry_completes_the_original_ownership_intent(tmp_path, monkeypatch, activation_mode, failure):
    class Generator:
        async def generate(self, _value):
            return "# Profile\n\n- Preserve the original owner."

    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            policy = await client.put(
                f"/v1/scopes/{scope_id}/profile-policy",
                json={"generation_enabled": True, "activation_mode": activation_mode, "expected_version": 0},
            )
            assert policy.status_code == 200, policy.text
            source = await client.post(
                f"/v1/scopes/{scope_id}/sources", json={"content": "Keep the accepted ownership identity."}
            )
            assert source.status_code == 201, source.text
            app.state.application.profiles.generator = Generator()

            async def unavailable(_event):
                raise AccessUnavailableError("external_audit_unavailable")

            with monkeypatch.context() as patch:
                if failure == "audit":
                    patch.setattr(access.audit, "append_audit_once", unavailable)
                else:
                    patch.setattr(access.relationships, "unavailable", True)
                failed = await client.post(
                    "/v1/profile/flush",
                    json={"scope_id": scope_id},
                    headers={"X-PowerContext-Request-ID": "accepted-profile-owner"},
                )
                assert failed.status_code == 503, failed.text
                original_request_id = failed.headers["X-PowerContext-Request-ID"]
                pending = await app.state.owner_recovery.pending(scope_id=scope_id)
                assert len(pending) == 1 and pending[0].principal == ALICE
                retried = await client.post("/v1/profile/flush", json={"scope_id": scope_id})
                assert retried.status_code == 503, retried.text
                assert await app.state.owner_recovery.pending(scope_id=scope_id) == pending
            retried = await client.post("/v1/profile/flush", json={"scope_id": scope_id})
            assert retried.status_code == 200, retried.text
            assert await app.state.owner_recovery.pending(scope_id=scope_id) == ()
            if activation_mode == "review_required":
                assert retried.json()["status"] == "review_pending"
                assert retried.json()["candidate_id"] == pending[0].candidate_id
                attestation = await access.candidate_owner(scope_id, pending[0].candidate_id)
                assert attestation is not None and attestation.proposed_owner == ALICE
            else:
                assert retried.json()["status"] == "noop"
                resource = ResourceRef.artifact(scope_id, family="profile", artifact_id="profile")
                relation = await access.artifact_owner(resource)
                assert relation is not None and relation.owner == ALICE
                audit = await access.audit.list_audit(resource=resource, limit=100)
                owners = [item for item in audit if item.reason_code == "artifact-owner-established"]
                assert len(owners) == 1 and owners[0].request_id == original_request_id
                records = await app.state.application.records.for_scope(scope_id).logical_artifacts()
                assert len(records) == 1
            repeated = await client.post("/v1/profile/flush", json={"scope_id": scope_id})
            assert repeated.json() == retried.json()
            assert await app.state.owner_recovery.pending(scope_id=scope_id) == ()

    asyncio.run(scenario())


def test_unknown_historical_owner_inventory_never_returns_content_or_assigns_a_caller(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            records = app.state.application.records.for_scope(scope_id)
            await records.create_artifact("profile", ArtifactWrite(content={"content": "# HISTORICAL_PRIVATE_CONTENT"}))
            page = await app.state.owner_recovery.inventory_gaps(scope_id)
            assert len(page.items) == 1 and page.after is None
            gap = page.items[0]
            assert gap.identity == "profile" and gap.reason == "unknown" and gap.operation_id is None
            assert "HISTORICAL_PRIVATE_CONTENT" not in repr(page)
            assert await app.state.owner_recovery.pending() == ()
            # An explicit read grant remains valid without inferring ownership.
            response = await client.get(
                f"/v1/scopes/{scope_id}/artifacts/profile/profile",
                headers={"Authorization": "Bearer bob"},
            )
            assert response.status_code == 403, response.text
            assert "HISTORICAL_PRIVATE_CONTENT" not in response.text
            assert (
                await access.artifact_owner(ResourceRef.artifact(scope_id, family="profile", artifact_id="profile"))
                is None
            )

    asyncio.run(scenario())


def test_inventory_paginates_metadata_without_loading_historical_bodies(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (app, client, _access):
            scope_id = await _scope(client)
            records = app.state.application.records.for_scope(scope_id)
            created = []
            for index in range(3):
                result = await records.create_artifact(
                    "experience",
                    ArtifactWrite(
                        content={
                            "situation": f"Private historical observation {index}",
                            "action": "Observe the prior work.",
                            "outcome": "The creator was not recorded.",
                            "lesson": "Do not guess the owner.",
                        }
                    ),
                )
                created.append(result.artifact_id)
            after = None
            identities = []
            while True:
                page = await app.state.owner_recovery.inventory_gaps(scope_id, after=after, limit=1)
                assert len(page.items) == 1 and page.items[0].reason == "unknown"
                assert "Private historical observation" not in repr(page)
                identities.append(page.items[0].identity)
                if page.after is None:
                    break
                after = page.after
            assert sorted(identities) == sorted(created)

    asyncio.run(scenario())


def test_non_idempotent_external_audit_fails_before_the_domain_commit(tmp_path):
    class AuditWithoutReplay:
        append_audit_once = None

        def __init__(self, audit):
            self.audit = audit

        def __getattr__(self, name):
            return getattr(self.audit, name)

    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            access.audit = AuditWithoutReplay(access.audit)
            response = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "profile", "content": {"content": "# Rejected before commit"}},
            )
            assert response.status_code == 503, response.text
            assert await app.state.owner_recovery.pending() == ()
            records = await app.state.application.records.for_scope(scope_id).logical_artifacts()
            assert records == ()

    asyncio.run(scenario())
