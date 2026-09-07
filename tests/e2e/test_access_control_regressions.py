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

"""Public regressions for owner readiness, identity attribution, and shared context."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.authentication import AuthenticationResult, ProviderReadiness
from powercontext.server.authz import AccessUnavailableError, PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control, open_casbin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings

ADMIN = PrincipalRef(type="service", id="admin")


class _Authentication:
    async def authenticate(self, request):
        identity = request.headers.get("authorization", "Bearer admin").removeprefix("Bearer ")
        return AuthenticationResult(subject=ADMIN if identity == "admin" else PrincipalRef(type="user", id=identity))

    async def readiness(self):
        return ProviderReadiness(ready=True)


@asynccontextmanager
async def _server(tmp_path: Path, backend="builtin"):
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'regressions.db'}")
    opener = open_builtin_access_control if backend == "builtin" else open_casbin_access_control
    async with opener(database, bootstrap_administrators=(ADMIN,), deployment_id="regressions") as access:
        app = create_server_app(
            settings=ServerSettings(
                database=database,
                access=AccessControlConfig(mode="enforced", deployment_id="regressions"),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            ),
            authentication_provider=_Authentication(),
            access_control=access,
            scheduler_path=tmp_path / "scheduler.db",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        ):
            yield app, client, access


async def _scope(client):
    result = await client.post(
        "/v1/scopes",
        json={"title": "Regression scope", "summary": "Regression tests", "idempotency_key": "regression-scope"},
    )
    assert result.status_code == 201, result.text
    return result.json()["scope_id"]


async def _grant(client, scope_id, principal, role, resource=None):
    response = await client.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": "user", "id": principal},
            "resource": resource or {"type": "scope", "scope_id": scope_id},
            "role": role,
            "idempotency_key": f"{principal}-{role}-{scope_id}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _resource(scope_id, family, artifact_id, entry_id=None):
    return {
        "type": "artifact",
        "scope_id": scope_id,
        "identity": {"family": family, "artifact_id": artifact_id},
        "selector": None if entry_id is None else {"type": "memory_entry", "entry_id": entry_id},
    }


async def _handoff(client, scope_id):
    source = await client.post(f"/v1/scopes/{scope_id}/sources", json={"content": "Verified task state."})
    assert source.status_code == 201, source.text
    created = await client.post(
        f"/v1/scopes/{scope_id}/artifacts",
        json={
            "family": "handoff",
            "content": {
                "schema": "powercontext.handoff.v1",
                "objective": "Continue the verified work.",
                "state": [
                    {
                        "text": "The current state is recorded.",
                        "citations": [
                            {
                                "kind": "source",
                                "source_ref": {"name": "content", "source_id": source.json()["source_id"]},
                            }
                        ],
                    }
                ],
                "disposition": "continuable",
                "next_action": {
                    "text": "Inspect the current state.",
                    "citations": [
                        {"kind": "source", "source_ref": {"name": "content", "source_id": source.json()["source_id"]}}
                    ],
                },
                "omissions": [],
            },
        },
    )
    assert created.status_code == 201, created.text
    return {"family": "handoff", "artifact_id": "handoff", "revision": created.json()["revision"]}


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_unprivileged_requests_cannot_distinguish_missing_owner(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            scope_id = await _scope(client)
            await _handoff(client, scope_id)
            responses = [
                await client.post(
                    "/v1/handoff/continue",
                    headers={"Authorization": "Bearer stranger"},
                    json={"scope_id": scope, "selection": "latest"},
                )
                for scope in (scope_id, "absent-scope")
            ]
            assert [response.status_code for response in responses] == [403, 403]
            assert responses[0].json()["error"] == responses[1].json()["error"]

    asyncio.run(scenario())


def test_owner_failure_blocks_collections_and_context_before_content(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (_, client, access):
            scope_id = await _scope(client)

            async def unavailable(*args, **kwargs):
                raise AccessUnavailableError("artifact_owner_pending")

            with monkeypatch.context() as patch:
                patch.setattr(access, "establish_artifact_owner", unavailable)
                created = await client.post(
                    f"/v1/scopes/{scope_id}/artifacts",
                    json={
                        "family": "memory",
                        "content": {"entries": [{"kind": "fact", "text": "OWNER_PENDING_PRIVATE_CONTENT"}]},
                    },
                )
                assert created.status_code == 503, created.text
            # The content is durably committed, but the owner did not commit.
            requests = [
                ("POST", "/v1/memory/entries/list", {"scope_id": scope_id}),
                ("POST", "/v1/memory/search", {"scope_id": scope_id, "query": "PRIVATE"}),
                ("POST", "/v1/context/prepare", {"scope_id": scope_id, "query": "PRIVATE"}),
                ("GET", f"/v1/scopes/{scope_id}/artifacts/memory/memory", None),
                ("GET", f"/v1/scopes/{scope_id}/artifacts/memory", None),
                ("POST", "/dashboard/skills/library", {"scope_id": scope_id}),
                ("POST", "/v1/stats", {"selection": {"mode": "all"}}),
            ]
            for method, path, body in requests:
                response = await client.request(method, path, json=body)
                assert response.status_code == 503, (path, response.text)
                assert response.json()["error"]["code"] == "artifact_owner_pending"
                assert "OWNER_PENDING_PRIVATE_CONTENT" not in response.text

    asyncio.run(scenario())


@pytest.mark.parametrize("dependency", ["provider", "audit", "relationships"])
def test_readiness_probes_dependencies_and_recovers(tmp_path, monkeypatch, dependency):
    async def scenario():
        async with _server(tmp_path) as (_, client, access):
            assert (await client.get("/health/ready")).status_code == 200

            async def unavailable(*args, **kwargs):
                raise RuntimeError("private-dependency-detail")

            target, method = {
                "provider": (access.provider, "check_batch"),
                "audit": (access.audit, "list_audit"),
                "relationships": (access.relationships, "get_receipt_identity"),
            }[dependency]
            with monkeypatch.context() as patch:
                patch.setattr(target, method, unavailable)
                ready = await client.get("/health/ready")
                assert ready.status_code == 503, ready.text
                assert ready.json()["checks"]["access_provider"] == "not_ready"
                assert "private-dependency-detail" not in ready.text
                assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/health/ready")).status_code == 200

    asyncio.run(scenario())


def test_candidate_permissions_preserve_proposer_restriction(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope_id = await _scope(client)
            for principal, role in (
                ("alice", "scope.contributor"),
                ("alice", "scope.reviewer"),
                ("bob", "scope.reviewer"),
                ("viewer", "scope.viewer"),
            ):
                await _grant(client, scope_id, principal, role)
            source = await client.post(f"/v1/scopes/{scope_id}/sources", json={"content": "Verified review evidence."})
            proposal = {
                "situation": "A handoff was reviewed.",
                "action": "Check permissions.",
                "outcome": "Review stays scoped.",
                "lesson": "Preserve proposal ownership.",
            }
            payload = {
                "scope_id": scope_id,
                "proposal": proposal,
                "source_refs": [{"name": "content", "source_id": source.json()["source_id"]}],
                "artifact_refs": [],
            }
            created = await client.post(
                "/v1/experience/propose", headers={"Authorization": "Bearer alice"}, json=payload
            )
            assert created.status_code == 201, created.text
            candidate_id = created.json()["candidate_id"]
            for principal, expected in (
                ("alice", (True, True, True)),
                ("bob", (False, True, True)),
                ("viewer", (False, False, False)),
            ):
                response = await client.post(
                    "/v1/artifact-candidates/get",
                    headers={"Authorization": f"Bearer {principal}"},
                    json={"scope_id": scope_id, "candidate_id": candidate_id},
                )
                assert response.status_code == 200, response.text
                assert response.json()["permissions"] == dict(
                    zip(("can_revise", "can_approve", "can_reject"), expected, strict=True)
                )
            revision = payload | {"candidate_id": candidate_id, "expected_version": created.json()["version"]}
            denied = await client.post(
                "/v1/artifact-candidates/revise", headers={"Authorization": "Bearer bob"}, json=revision
            )
            assert denied.status_code == 403, denied.text
            revised = await client.post(
                "/v1/artifact-candidates/revise", headers={"Authorization": "Bearer alice"}, json=revision
            )
            assert revised.status_code == 200, revised.text

    asyncio.run(scenario())


def test_shared_handoff_and_persisted_receipt_identity(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (_, client, access):
            scope_id = await _scope(client)
            revision = await _handoff(client, scope_id)
            resource = _resource(scope_id, "handoff", "handoff")
            binding = await _grant(client, scope_id, "bob", "handoff.receiver", resource)
            bob = {"Authorization": "Bearer bob"}
            visible = await client.post(
                "/v1/access/resources/list",
                headers=bob,
                json={"action": "artifact.read", "resource_type": "artifact", "family": "handoff"},
            )
            assert visible.json()["items"] == [resource]
            body = await client.post("/dashboard/shared/read", headers=bob, json=resource)
            assert body.status_code == 200, body.text
            payload = {
                "scope_id": scope_id,
                "source_id": "mismatch-receipt",
                "receiver": "someone-else",
                "status": "declined",
                "selection": "exact",
                "revision": revision,
                "message": "I cannot accept this task.",
            }
            receipt = await client.post("/v1/work/handoffs/acknowledge", headers=bob, json=payload)
            assert receipt.status_code == 200, receipt.text
            identity = {
                "principal": {"type": "user", "id": "bob", "description": None},
                "receiver_identity_matches": False,
            }
            assert receipt.json()["receipt_identity"] == identity
            replay = await client.post("/v1/work/handoffs/acknowledge", headers=bob, json=payload)
            assert replay.status_code == 200, replay.text
            assert replay.json() == receipt.json()
            reattributed = await client.post("/v1/work/handoffs/acknowledge", json=payload)
            assert reattributed.status_code == 409, reattributed.text
            audit = await client.post(
                "/v1/access/audit/list",
                json={"resource": {"type": "scope", "scope_id": scope_id}, "action": "handoff.acknowledge"},
            )
            assert audit.status_code == 200, audit.text
            attestations = [
                event for event in audit.json()["items"] if event["operation"] == "handoff.receipt.identity"
            ]
            assert len(attestations) == 1
            assert attestations[0]["principal"] == identity["principal"]
            assert attestations[0]["reason_code"] == "receiver_identity_mismatch"

            async def identity_unavailable(*args, **kwargs):
                raise AccessUnavailableError("access_unavailable")

            with monkeypatch.context() as patch:
                patch.setattr(access, "record_receipt_identity", identity_unavailable)
                failed = await client.post(
                    "/v1/work/handoffs/acknowledge", headers=bob, json=payload | {"source_id": "failed-identity"}
                )
                assert failed.status_code == 503, failed.text
            absent = await client.get(f"/v1/scopes/{scope_id}/sources/content/failed-identity")
            assert absent.status_code == 404, absent.text
            source = await client.get(f"/v1/scopes/{scope_id}/sources/content/mismatch-receipt")
            assert source.status_code == 200, source.text
            assert source.json()["receipt_identity"] == identity
            denied = await client.post(
                "/v1/work/handoffs/acknowledge",
                headers=bob,
                json=payload
                | {
                    "source_id": "accepted-mismatch",
                    "status": "accepted",
                    "receiver_checks": {
                        "live_state": "confirmed",
                        "capability": "confirmed",
                        "authorization": "confirmed",
                    },
                },
            )
            assert denied.status_code == 422, denied.text
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": binding["binding_id"],
                    "expected_version": binding["version"],
                    "idempotency_key": "revoke-receiver",
                },
            )
            assert revoked.status_code == 200, revoked.text
            assert (await client.post("/dashboard/shared/read", headers=bob, json=resource)).status_code == 403
            assert (await client.post("/v1/work/handoffs/acknowledge", headers=bob, json=payload)).status_code == 403
            return scope_id, identity

    scope_id, identity = asyncio.run(scenario())

    async def reopened():
        async with _server(tmp_path) as (_, client, _):
            response = await client.get(f"/v1/scopes/{scope_id}/sources/content/mismatch-receipt")
            assert response.status_code == 200, response.text
            assert response.json()["receipt_identity"] == identity

    asyncio.run(reopened())


def test_concurrent_handoff_receipts_cannot_replace_the_authenticated_submitter(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope_id = await _scope(client)
            revision = await _handoff(client, scope_id)
            await _grant(client, scope_id, "bob", "handoff.receiver", _resource(scope_id, "handoff", "handoff"))
            payload = {
                "scope_id": scope_id,
                "source_id": "concurrent-receipt",
                "receiver": "external-display-name",
                "status": "needs_clarification",
                "selection": "exact",
                "revision": revision,
                "message": "Please confirm the target environment.",
            }
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/v1/work/handoffs/acknowledge",
                        headers={"Authorization": f"Bearer {principal}"},
                        json=payload,
                    )
                    for principal in ("admin", "bob")
                )
            )
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner = next(response.json() for response in responses if response.status_code == 200)
            identity = winner["receipt_identity"]
            replay = await client.post(
                "/v1/work/handoffs/acknowledge",
                headers={"Authorization": f"Bearer {identity['principal']['id']}"},
                json=payload,
            )
            assert replay.status_code == 200, replay.text
            assert replay.json() == winner
            return scope_id, identity

    scope_id, identity = asyncio.run(scenario())

    async def reopened():
        async with _server(tmp_path) as (_, client, _):
            source = await client.get(f"/v1/scopes/{scope_id}/sources/content/concurrent-receipt")
            assert source.status_code == 200, source.text
            assert source.json()["receipt_identity"] == identity

    asyncio.run(reopened())


def test_shared_memory_resolves_only_the_granted_entry(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope_id = await _scope(client)
            created = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={
                    "family": "memory",
                    "content": {
                        "entries": [{"kind": "fact", "text": "shared fact"}, {"kind": "fact", "text": "private fact"}]
                    },
                },
            )
            assert created.status_code == 201, created.text
            artifact_id = created.json()["artifact_id"]
            visible = await client.post(
                "/v1/access/resources/list",
                json={"action": "artifact.read", "resource_type": "artifact", "family": "memory"},
            )
            available = visible.json()["items"]
            assert len(available) == 2, visible.text
            resource = available[0]
            assert resource["identity"]["artifact_id"] == artifact_id
            await _grant(client, scope_id, "bob", "artifact.viewer", resource)
            response = await client.post(
                "/dashboard/shared/read", headers={"Authorization": "Bearer bob"}, json=resource
            )
            assert response.status_code == 200, response.text
            text = response.json()["text"]
            assert text in {"shared fact", "private fact"}
            assert ("private fact" if text == "shared fact" else "shared fact") not in response.text
            assert (
                await client.post(
                    "/v1/memory/entries/list", headers={"Authorization": "Bearer bob"}, json={"scope_id": scope_id}
                )
            ).status_code == 403

    asyncio.run(scenario())
