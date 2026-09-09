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
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
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
async def _server(tmp_path: Path, backend="builtin", *, inference: InferenceConfig | None = None):
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'regressions.db'}")
    opener = open_builtin_access_control if backend == "builtin" else open_casbin_access_control
    async with opener(database, bootstrap_administrators=(ADMIN,), deployment_id="regressions") as access:
        app = create_server_app(
            settings=ServerSettings(
                database=database,
                inference=inference or InferenceConfig(),
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

            async def identity_missing(*args, **kwargs):
                return None

            with monkeypatch.context() as patch:
                patch.setattr(access, "receipt_identity", identity_missing)
                exact_missing = await client.get(f"/v1/scopes/{scope_id}/sources/content/mismatch-receipt")
                list_missing = await client.get(f"/v1/scopes/{scope_id}/sources")
                assert exact_missing.status_code == 503, exact_missing.text
                assert list_missing.status_code == 503, list_missing.text
            with monkeypatch.context() as patch:
                patch.setattr(access, "receipt_identity", identity_unavailable)
                exact_unavailable = await client.get(f"/v1/scopes/{scope_id}/sources/content/mismatch-receipt")
                list_unavailable = await client.get(f"/v1/scopes/{scope_id}/sources")
                assert exact_unavailable.status_code == 503, exact_unavailable.text
                assert list_unavailable.status_code == 503, list_unavailable.text
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


def test_startup_migrates_legacy_receipts_without_changing_public_source(tmp_path, monkeypatch):
    import sqlite3

    from powercontext.builtin.runtime.application import ScopedSourceApplication

    original_capture = ScopedSourceApplication._capture

    async def legacy_capture(self, value, /, *, handoff_receipt=False):
        return await original_capture(self, value, handoff_receipt=False)

    async def prepare():
        async with _server(tmp_path) as (app, client, _):
            scope_id = await _scope(client)
            revision = await _handoff(client, scope_id)
            with monkeypatch.context() as patch:
                patch.setattr(ScopedSourceApplication, "_capture", legacy_capture)
                created = await client.post(
                    "/v1/work/handoffs/acknowledge",
                    json={
                        "scope_id": scope_id,
                        "source_id": "legacy-receipt",
                        "receiver": "admin",
                        "status": "declined",
                        "selection": "exact",
                        "revision": revision,
                        "message": "Upgrade test",
                    },
                )
                assert created.status_code == 200, created.text
            ordinary = await app.state.application.records.for_scope(scope_id).create_source(
                "content",
                {"schema": "powercontext.handoff-receipt.v1"},
            )
            reserved_only = await app.state.application.records.for_scope(scope_id).create_source(
                "content",
                {"schema": "powercontext.handoff-receipt.v1"},
            )
            conflicted = await client.post(
                "/v1/work/handoffs/acknowledge",
                json={
                    "scope_id": scope_id,
                    "source_id": reserved_only.source_id,
                    "receiver": "admin",
                    "status": "declined",
                    "selection": "exact",
                    "revision": revision,
                    "message": "This write must conflict",
                },
            )
            assert conflicted.status_code == 409, conflicted.text
            before = await client.get(f"/v1/scopes/{scope_id}/sources/content/legacy-receipt")
            return scope_id, ordinary.source_id, reserved_only.source_id, before.json()

    scope_id, ordinary_id, reserved_only_id, before = asyncio.run(prepare())

    async def check_upgrade():
        async with _server(tmp_path) as (_, client, access):
            exact = await client.get(f"/v1/scopes/{scope_id}/sources/content/legacy-receipt")
            assert exact.status_code == 200 and exact.json() == before
            ordinary = await client.get(f"/v1/scopes/{scope_id}/sources/content/{ordinary_id}")
            assert ordinary.status_code == 200 and ordinary.json()["receipt_identity"] is None
            reserved_only = await client.get(f"/v1/scopes/{scope_id}/sources/content/{reserved_only_id}")
            assert reserved_only.status_code == 200 and reserved_only.json()["receipt_identity"] is None
            page = await client.get(f"/v1/scopes/{scope_id}/sources", params={"limit": 100})
            assert page.status_code == 200, page.text
            by_source_id = {item["source_id"]: item for item in page.json()["items"]}
            assert by_source_id[ordinary_id]["receipt_identity"] is None
            assert by_source_id[reserved_only_id]["receipt_identity"] is None

            async def missing(*args, **kwargs):
                return None

            with monkeypatch.context() as patch:
                patch.setattr(access, "receipt_identity", missing)
                exact = await client.get(f"/v1/scopes/{scope_id}/sources/content/legacy-receipt")
                page = await client.get(f"/v1/scopes/{scope_id}/sources")
                assert exact.status_code == 503, exact.text
                assert page.status_code == 503, page.text

    for _ in range(2):
        asyncio.run(check_upgrade())
        with sqlite3.connect(tmp_path / "regressions.db") as connection:
            pending = connection.execute(
                "SELECT scope_id, source_id, reason FROM pc_receipt_migration_review"
            ).fetchall()
            assert set(pending) == {
                (scope_id, ordinary_id, "missing_committed_receipt"),
                (scope_id, reserved_only_id, "missing_committed_receipt"),
            }


def test_generic_receipt_markers_cannot_block_source_collection(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (app, client, _):
            scope_id = await _scope(client)
            marker = {"schema": "powercontext.handoff-receipt.v1"}

            created = await client.post(f"/v1/scopes/{scope_id}/sources", json={"content": marker})
            captured = await client.post(
                "/v1/sources/content",
                json={
                    "scope_id": scope_id,
                    "source_id": "forged-receipt",
                    "content": json.dumps(marker),
                },
            )
            assert created.status_code == 422, created.text
            assert captured.status_code == 422, captured.text

            records = app.state.application.records.for_scope(scope_id)
            legacy = await records.create_source("content", marker)
            ordinary = await records.create_source("content", {"statement": "later source"})

            exact = await client.get(f"/v1/scopes/{scope_id}/sources/content/{legacy.source_id}")
            assert exact.status_code == 200, exact.text
            assert exact.json()["receipt_identity"] is None

            first = await client.get(f"/v1/scopes/{scope_id}/sources", params={"limit": 1})
            assert first.status_code == 200, first.text
            assert [item["source_id"] for item in first.json()["items"]] == [legacy.source_id]
            assert first.json()["next_cursor"] is not None
            second = await client.get(
                f"/v1/scopes/{scope_id}/sources",
                params={"limit": 1, "cursor": first.json()["next_cursor"]},
            )
            assert second.status_code == 200, second.text
            assert [item["source_id"] for item in second.json()["items"]] == [ordinary.source_id]

    asyncio.run(scenario())


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


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_prompt_management_respects_scope_and_artifact_permissions(tmp_path: Path, backend: str) -> None:
    async def scenario():
        async with _server(tmp_path, backend, inference=InferenceConfig(generation_model="test")) as (_, client, _):
            scope = await _scope(client)
            await _grant(client, scope, "author", "scope.admin")
            await _grant(client, scope, "contributor", "scope.contributor")
            await _grant(client, scope, "reader", "scope.viewer")
            author = {"Authorization": "Bearer author"}
            contributor = {"Authorization": "Bearer contributor"}
            reader = {"Authorization": "Bearer reader"}
            outsider = {"Authorization": "Bearer outsider"}
            configuration_path = f"/v1/scopes/{scope}/prompts/memory.extract"
            default = await client.get(configuration_path, headers=reader)
            assert default.status_code == 200 and default.json()["artifact"] is None
            for hidden_scope in (scope, "absent-scope"):
                denied = await client.get(f"/v1/scopes/{hidden_scope}/prompts/memory.extract", headers=outsider)
                assert denied.status_code == 403
            content = {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Keep stable preferences.",
                "demonstrations": [],
            }
            for headers in (contributor, reader, outsider):
                denied = await client.post(
                    f"/v1/scopes/{scope}/artifacts",
                    headers=headers,
                    json={"family": "prompt", "prompt_key": "memory.extract", "content": content},
                )
                assert denied.status_code == 403, denied.text
            assert (await client.get(configuration_path, headers=reader)).json()["artifact"] is None
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                headers=author,
                json={"family": "prompt", "prompt_key": "memory.extract", "content": content},
            )
            assert created.status_code == 201, created.text
            path = f"/v1/scopes/{scope}/artifacts/prompt/memory.extract"
            configuration = await client.get(configuration_path, headers=reader)
            assert configuration.status_code == 200
            assert configuration.json()["artifact"]["revision"] == 1
            assert (await client.get(configuration_path, headers=outsider)).status_code == 403
            for suffix in ("", "/revisions/1", "/revisions"):
                allowed = await client.get(path + suffix, headers=reader)
                assert allowed.status_code == 200, allowed.text
                denied = await client.get(path + suffix, headers=outsider)
                assert denied.status_code == 403, denied.text
            for headers in (contributor, reader, outsider):
                denied = await client.put(
                    path, headers={**headers, "If-Match": '"revision:1"'}, json={"content": content}
                )
                assert denied.status_code == 403, denied.text
            replaced = await client.put(path, headers={**author, "If-Match": '"revision:1"'}, json={"content": content})
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["revision"] == 2
            history = await client.get(path + "/revisions", headers=reader)
            assert [item["revision"] for item in history.json()["items"]] == [2, 1]
            for headers in (contributor, reader, outsider):
                denied = await client.post(
                    f"/v1/scopes/{scope}/prompts/memory.extract/demonstrations",
                    headers=headers,
                    json={"instructions": "Keep stable preferences.", "demonstration_count": 1},
                )
                assert denied.status_code == 403, denied.text

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
@pytest.mark.parametrize("revoked_role", ["scope.admin", "scope.contributor"])
def test_prompt_owner_cannot_mutate_after_scope_role_revocation(
    tmp_path: Path, backend: str, revoked_role: str
) -> None:
    async def scenario():
        async with _server(tmp_path, backend, inference=InferenceConfig(generation_model="test")) as (_, client, _):
            scope = await _scope(client)
            administrator = await _grant(client, scope, "author", "scope.admin")
            contributor = await _grant(client, scope, "author", "scope.contributor")
            author = {"Authorization": "Bearer author"}
            content = {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Keep stable preferences.",
                "demonstrations": [],
            }
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                headers=author,
                json={"family": "prompt", "prompt_key": "memory.extract", "content": content},
            )
            assert created.status_code == 201, created.text
            bindings = (
                [administrator, contributor] if revoked_role == "scope.contributor" else [contributor, administrator]
            )
            path = f"/v1/scopes/{scope}/artifacts/prompt/memory.extract"
            for binding in bindings:
                revoked = await client.post(
                    "/v1/access/bindings/revoke",
                    json={
                        "binding_id": binding["binding_id"],
                        "expected_version": binding["version"],
                        "idempotency_key": f"revoke-{binding['binding_id']}",
                    },
                )
                assert revoked.status_code == 200, revoked.text
                if binding == administrator and revoked_role == "scope.contributor":
                    denied = await client.put(
                        path, headers={**author, "If-Match": '"revision:1"'}, json={"content": content}
                    )
                    assert denied.status_code == 403, denied.text
            original = await client.get(path)
            auto = {**content, "mode": "auto", "instructions": ""}
            for replacement in ({**content, "instructions": "An unauthorized change."}, auto, content):
                denied = await client.put(
                    path, headers={**author, "If-Match": '"revision:1"'}, json={"content": replacement}
                )
                assert denied.status_code == 403, denied.text
            denied = await client.post(
                f"/v1/scopes/{scope}/prompts/memory.extract/demonstrations",
                headers=author,
                json={"instructions": "Keep stable preferences.", "demonstration_count": 1},
            )
            assert denied.status_code == 403, denied.text
            current = await client.get(path)
            assert current.json() == original.json()
            assert current.headers["etag"] == '"revision:1"'
            history = await client.get(path + "/revisions")
            assert [item["revision"] for item in history.json()["items"]] == [1]
            configuration = await client.get(f"/v1/scopes/{scope}/prompts/memory.extract")
            assert configuration.json()["effective"]["instructions"] == content["instructions"]
            # Scope administration remains sufficient even when a revoked user owns the Artifact.
            await _grant(client, scope, "manager", "scope.admin")
            replaced = await client.put(
                path,
                headers={"Authorization": "Bearer manager", "If-Match": '"revision:1"'},
                json={"content": auto},
            )
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["revision"] == 2

    asyncio.run(scenario())


def test_prompt_configuration_does_not_fall_back_when_saved_owner_is_pending(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (_, client, access):
            scope = await _scope(client)

            async def unavailable(*args, **kwargs):
                raise AccessUnavailableError("artifact_owner_pending")

            with monkeypatch.context() as patch:
                patch.setattr(access, "establish_artifact_owner", unavailable)
                created = await client.post(
                    f"/v1/scopes/{scope}/artifacts",
                    json={
                        "family": "prompt",
                        "prompt_key": "memory.extract",
                        "content": {
                            "schema_version": "powercontext.prompt.v1",
                            "mode": "auto",
                            "instructions": "",
                            "demonstrations": [],
                        },
                    },
                )
                assert created.status_code == 503
            response = await client.get(f"/v1/scopes/{scope}/prompts/memory.extract")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "artifact_owner_pending"
            assert "effective" not in response.json()

    asyncio.run(scenario())
