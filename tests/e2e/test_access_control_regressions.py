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
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import cast

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import RecordService
from powercontext.builtin.runtime import BuiltinRuntime, InferenceConfig, RuntimeCapabilities, RuntimeConfig
from powercontext.server.authentication import AuthenticationResult, ProviderReadiness
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessDecision,
    AccessUnavailableError,
    BuiltinAuthorizationProvider,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control, open_casbin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ADMIN = PrincipalRef(type="service", id="admin")
AUDIT = AccessAuditContext(transport="http", operation="test")


@contextmanager
def _reject_owner_insert(path: Path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_owner_insert BEFORE INSERT ON pc_access_owners "
            "BEGIN SELECT RAISE(ABORT, 'owner storage failed'); END"
        )
    try:
        yield
    finally:
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER reject_owner_insert")


class _Authentication:
    async def authenticate(self, request):
        identity = request.headers.get("authorization", "Bearer admin").removeprefix("Bearer ")
        return AuthenticationResult(subject=ADMIN if identity == "admin" else PrincipalRef(type="user", id=identity))

    async def readiness(self):
        return ProviderReadiness(ready=True)


@asynccontextmanager
async def _server(tmp_path: Path, backend="builtin", *, inference: InferenceConfig | None = None):
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'regressions.db'}")
    settings = ServerSettings(
        database=database,
        inference=inference or InferenceConfig(),
        runtime=RuntimeConfig(artifact_processing_families=()),
        access=AccessControlConfig(mode="enforced", deployment_id="regressions"),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
    )
    await asyncio.to_thread(prepare_server_database, settings)
    opener = open_builtin_access_control if backend == "builtin" else open_casbin_access_control
    async with opener(database, bootstrap_administrators=(ADMIN,), deployment_id="regressions") as access:
        app = create_server_app(
            settings=settings,
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


def test_remember_memory_survives_concurrent_write_after_authorization(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        async with _server(tmp_path) as (_, client, access):
            scope_id = await _scope(client)
            original_check = BuiltinAuthorizationProvider.check
            interleaved = False

            async def check_with_concurrent_write(provider, request):
                nonlocal interleaved
                decision = await original_check(provider, request)
                if (
                    not interleaved
                    and request.context.operation == "remember_memory"
                    and request.action == AccessAction.SCOPE_CONTRIBUTE
                    and provider._repository._bound_connection is not None
                ):
                    interleaved = True
                    other = await client.post(
                        "/v1/scopes",
                        json={
                            "title": "Concurrent scope",
                            "summary": "Another writer",
                            "idempotency_key": "concurrent",
                        },
                    )
                    assert other.status_code == 201, other.text
                return decision

            monkeypatch.setattr(BuiltinAuthorizationProvider, "check", check_with_concurrent_write)
            remembered = await client.post(
                "/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": "Concurrent save succeeds."}
            )
            assert interleaved
            assert remembered.status_code == 200, remembered.text
            artifact = remembered.json()["records"][0]["artifact"]
            revision = await client.get(
                f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact['artifact_id']}/revisions/{artifact['revision']}"
            )
            assert revision.status_code == 200, revision.text
            assert revision.json()["content"]["text"] == "Concurrent save succeeds."
            audit = await access.audit.list_audit(resource=ResourceRef.scope(scope_id), limit=100)
            assert any(event.operation == "remember_memory" and event.allowed for event in audit)

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_stable_handle_binding_requires_scope_admin(tmp_path: Path, backend: str) -> None:
    async def scenario() -> None:
        async with _server(tmp_path, backend) as (_, client, _):
            scope = await _scope(client)
            await _grant(client, scope, "contributor", "scope.contributor")
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={"family": "profile", "content": {"content": "# Support agent"}},
            )
            assert created.status_code == 201, created.text
            path = f"/v1/scopes/{scope}/artifact-handles/profile/support-agent"
            payload = {"artifact_id": created.json()["artifact_id"], "revision": created.json()["revision"]}

            denied = await client.put(path, headers={"Authorization": "Bearer contributor"}, json=payload)
            assert denied.status_code == 403, denied.text

            bound = await client.put(path, json=payload)
            assert bound.status_code == 200, bound.text

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
@pytest.mark.parametrize("target_contributor", [False, True])
def test_missing_subject_target_preserves_authorization(tmp_path, backend, target_contributor):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            origin = await _scope(client)
            path = f"/v1/scopes/{origin}/subject-sources"
            payload = {"subject_key": "private-subject", "content": "Subject evidence"}
            created = await client.post(path, json=payload)
            assert created.status_code == 201, created.text
            target = created.json()["subject_scope_id"]
            await _grant(client, origin, "writer", "scope.contributor")
            if target_contributor:
                await _grant(client, target, "writer", "scope.contributor")
            headers = {"Authorization": "Bearer writer"}
            healthy = await client.post(path, headers=headers, json=payload)
            assert healthy.status_code == (201 if target_contributor else 403), healthy.text
            if not target_contributor:
                assert target not in healthy.text
            sources_before = await client.get(f"/v1/scopes/{origin}/sources")
            assert sources_before.status_code == 200, sources_before.text

            # Simulate out-of-band loss while preserving bindings and access grants.
            with sqlite3.connect(tmp_path / "regressions.db") as connection:
                connection.execute("DELETE FROM pc_scopes WHERE scope_id = ?", (target,))

            missing = await client.post(path, headers=headers, json=payload)
            if target_contributor:
                assert missing.status_code == 409, missing.text
                assert missing.json()["error"]["code"] == "scope_binding_target_missing"
                assert missing.json()["error"]["details"]["scope_id"] == target
            else:
                assert missing.status_code == 403, missing.text
                assert missing.json()["error"]["code"] == healthy.json()["error"]["code"]
                assert target not in missing.text
            sources_after = await client.get(f"/v1/scopes/{origin}/sources")
            assert sources_after.status_code == 200, sources_after.text
            assert {item["source_id"] for item in sources_after.json()["items"]} == {
                item["source_id"] for item in sources_before.json()["items"]
            }

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_topic_memory_generic_writes_and_publication_use_scope_authority(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            scope = await _scope(client)
            target_response = await client.post(
                "/v1/scopes", json={"title": "Target", "summary": "Target", "idempotency_key": "target"}
            )
            target = target_response.json()["scope_id"]
            await _grant(client, scope, "reader", "scope.viewer")
            await _grant(client, scope, "writer", "scope.contributor")
            content = {"title": "Topic", "summary": "Scope knowledge", "detail": "Shared recovery procedures."}
            create_path = f"/v1/scopes/{scope}/artifacts"
            reader = {"Authorization": "Bearer reader"}
            writer = {"Authorization": "Bearer writer"}
            assert (
                await client.post(create_path, headers=reader, json={"family": "topic-memory", "content": content})
            ).status_code == 403
            created = await client.post(
                create_path, headers=writer, json={"family": "topic-memory", "content": content}
            )
            assert created.status_code == 201, created.text
            path = created.headers["Location"]
            assert (await client.get(path, headers=reader)).status_code == 200
            assert (await client.get(create_path + "/topic-memory", headers=reader)).status_code == 200
            tags = await client.get(path + "/tags", headers=reader)
            assert tags.status_code == 200, tags.text
            for principal in (reader, writer):
                replacement = await client.put(
                    path, headers=principal | {"If-Match": created.headers["ETag"]}, json={"content": content}
                )
                assert replacement.status_code == 403, replacement.text
                retag = await client.put(
                    path + "/tags", headers=principal | {"If-Match": tags.headers["ETag"]}, json={"tags": ["shared"]}
                )
                assert retag.status_code == 403, retag.text
            updated = await client.put(path, headers={"If-Match": created.headers["ETag"]}, json={"content": content})
            assert updated.status_code == 200, updated.text
            tagged = await client.put(
                path + "/tags", headers={"If-Match": tags.headers["ETag"]}, json={"tags": ["shared"]}
            )
            assert tagged.status_code == 200, tagged.text
            found = await client.post(
                f"/v1/scopes/{scope}/artifact-tags/query", headers=reader, json={"tags": ["shared"]}
            )
            assert found.status_code == 200 and len(found.json()["items"]) == 1, found.text
            ref = {key: created.json()[key] for key in ("family", "artifact_id", "revision")}
            request = {
                "source": {"scope_id": scope, "artifact": ref},
                "target_scope_id": target,
                "idempotency_key": "topic-publication",
            }
            await _grant(client, target, "reader", "scope.admin")
            assert (await client.post("/v1/artifact-publications", headers=reader, json=request)).status_code == 403
            source_grant = await _grant(client, scope, "reader", "scope.admin")
            published = await client.post("/v1/artifact-publications", headers=reader, json=request)
            assert published.status_code == 201, published.text
            await _grant(client, target, "reader", "scope.viewer")
            assert (await client.get(f"/v1/scopes/{target}/artifacts/topic-memory", headers=reader)).status_code == 200
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": source_grant["binding_id"],
                    "expected_version": source_grant["version"],
                    "idempotency_key": "revoke-topic-admin",
                },
            )
            assert revoked.status_code == 200
            assert (await client.post("/v1/artifact-publications", headers=reader, json=request)).status_code == 403
            assert (await client.get(path, headers={"Authorization": "Bearer outsider"})).status_code == 403

    asyncio.run(scenario())


@pytest.mark.parametrize("assembly", [None, {}, {"sections": [{"family": "profile", "limit": 2}]}])
def test_prepare_requires_read_access_to_every_referenced_scope(tmp_path, assembly):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            shared = await _scope(client)
            remembered = await client.post(
                "/v1/memory/remember",
                json={
                    "scope_id": shared,
                    "kind": "fact",
                    "text": "PRIVATE shared deployment contract.",
                },
            )
            assert remembered.status_code == 200
            profile = await client.post(
                f"/v1/scopes/{shared}/artifacts",
                json={"family": "profile", "content": {"content": "PRIVATE shared deployment contract."}},
            )
            assert profile.status_code == 201, profile.text
            created = await client.post(
                "/v1/scopes",
                json={
                    "title": "Current",
                    "summary": "References shared evidence",
                    "idempotency_key": "current",
                    "context_references": [shared],
                },
            )
            assert created.status_code == 201
            current = created.json()["scope_id"]
            await _grant(client, current, "bob", "scope.viewer")
            payload = {"scope_id": current, "query": "PRIVATE"}
            if assembly is not None:
                payload["assembly"] = assembly
            response = await client.post("/v1/context/prepare", headers={"Authorization": "Bearer bob"}, json=payload)
            assert response.status_code == 403
            assert "PRIVATE" not in response.text
            opted_denied = await client.post(
                "/v1/context/prepare",
                headers={"Authorization": "Bearer bob"},
                json={**payload, "include_receipt": True},
            )
            assert opted_denied.status_code == 403 and "PRIVATE" not in opted_denied.text
            disabled = await client.post(
                "/v1/context/prepare",
                headers={"Authorization": "Bearer bob"},
                json={
                    **payload,
                    "assembly": {"sections": []},
                    "include_receipt": True,
                },
            )
            assert disabled.status_code == 200 and disabled.json()["status"] == "empty"
            assert disabled.json()["receipt"]["selected"] == []
            grant = await _grant(client, shared, "bob", "scope.viewer")
            allowed = await client.post("/v1/context/prepare", headers={"Authorization": "Bearer bob"}, json=payload)
            assert allowed.status_code == 200
            assert "PRIVATE shared deployment contract." in allowed.json()["content"]
            opted_allowed = await client.post(
                "/v1/context/prepare",
                headers={"Authorization": "Bearer bob"},
                json={**payload, "include_receipt": True},
            )
            assert opted_allowed.status_code == 200
            selected = opted_allowed.json()["receipt"]["selected"]
            assert selected and all(item["artifact_address"]["scope_id"] == shared for item in selected)
            artifact = selected[0]["artifact_address"]["artifact"]
            exact_path = (
                f"/v1/scopes/{shared}/artifacts/{artifact['family']}/"
                f"{artifact['artifact_id']}/revisions/{artifact['revision']}"
            )
            exact = await client.get(exact_path, headers={"Authorization": "Bearer bob"})
            assert exact.status_code == 200
            assert exact.json()["revision"] == artifact["revision"]
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": grant["binding_id"],
                    "expected_version": grant["version"],
                    "idempotency_key": "revoke-shared-reader",
                },
            )
            assert revoked.status_code == 200, revoked.text
            denied = await client.post("/v1/context/prepare", headers={"Authorization": "Bearer bob"}, json=payload)
            assert denied.status_code == 403 and "PRIVATE" not in denied.text
            revoked_exact = await client.get(exact_path, headers={"Authorization": "Bearer bob"})
            assert revoked_exact.status_code == 403

    asyncio.run(scenario())


def test_bootstrap_retry_reauthorizes_exact_items_after_context_references_change(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            shared = await _scope(client)
            remembered = await client.post(
                "/v1/memory/remember",
                json={
                    "scope_id": shared,
                    "kind": "constraint",
                    "text": "PRIVATE bootstrap constraint.",
                },
            )
            assert remembered.status_code == 200, remembered.text
            memory_id = remembered.json()["records"][0]["artifact"]["artifact_id"]
            tags_path = f"/v1/scopes/{shared}/artifacts/atomic-memory/{memory_id}/tags"
            tags = await client.get(tags_path)
            assert tags.status_code == 200, tags.text
            tagged = await client.put(
                tags_path,
                headers={"If-Match": tags.headers["ETag"]},
                json={"tags": ["bootstrap-context"]},
            )
            assert tagged.status_code == 200, tagged.text

            created = await client.post(
                "/v1/scopes",
                json={
                    "title": "Current bootstrap scope",
                    "summary": "References reviewed bootstrap context",
                    "idempotency_key": "bootstrap-current",
                    "context_references": [shared],
                },
            )
            assert created.status_code == 201, created.text
            current = created.json()["scope_id"]
            await _grant(client, current, "bob", "scope.viewer")
            headers = {"Authorization": "Bearer bob"}
            payload = {
                "scope_id": current,
                "enabled": True,
                "lifecycle": "resume",
                "integration": "access-regression",
                "event_id": "stable-bootstrap-event",
            }
            disabled = await client.post(
                "/v1/context/bootstrap",
                headers=headers,
                json={**payload, "enabled": False, "event_id": "disabled-bootstrap-event"},
            )
            assert disabled.status_code == 200, disabled.text
            assert disabled.json()["status"] == "skipped"
            assert "PRIVATE bootstrap constraint" not in disabled.text
            denied = await client.post("/v1/context/bootstrap", headers=headers, json=payload)
            assert denied.status_code == 403
            assert "PRIVATE bootstrap constraint" not in denied.text

            grant = await _grant(client, shared, "bob", "scope.viewer")
            pending = await client.post("/v1/context/bootstrap", headers=headers, json=payload)
            assert pending.status_code == 200, pending.text
            assert pending.json()["status"] == "ready"
            assert "PRIVATE bootstrap constraint" in pending.json()["content"]

            updated = await client.put(
                f"/v1/scopes/{current}",
                json={
                    "expected_version": created.json()["version"],
                    "title": created.json()["title"],
                    "summary": created.json()["summary"],
                    "context_references": [],
                },
            )
            assert updated.status_code == 200, updated.text
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": grant["binding_id"],
                    "expected_version": grant["version"],
                    "idempotency_key": "revoke-bootstrap-reader",
                },
            )
            assert revoked.status_code == 200, revoked.text
            retry = await client.post("/v1/context/bootstrap", headers=headers, json=payload)
            assert retry.status_code == 403
            assert "PRIVATE bootstrap constraint" not in retry.text

    asyncio.run(scenario())


@pytest.mark.parametrize("reader_has_candidate", [False, True])
def test_bootstrap_insert_conflict_reauthorizes_the_winning_receipt(tmp_path, monkeypatch, reader_has_candidate):
    async def scenario():
        async with _server(tmp_path) as (app, client, _):
            private = await _scope(client)
            remembered = await client.post(
                "/v1/memory/remember",
                json={
                    "scope_id": private,
                    "kind": "constraint",
                    "text": "PRIVATE concurrent bootstrap constraint.",
                },
            )
            assert remembered.status_code == 200, remembered.text
            memory_id = remembered.json()["records"][0]["artifact"]["artifact_id"]
            tags_path = f"/v1/scopes/{private}/artifacts/atomic-memory/{memory_id}/tags"
            tags = await client.get(tags_path)
            assert tags.status_code == 200, tags.text
            tagged = await client.put(
                tags_path,
                headers={"If-Match": tags.headers["ETag"]},
                json={"tags": ["bootstrap-context"]},
            )
            assert tagged.status_code == 200, tagged.text

            created = await client.post(
                "/v1/scopes",
                json={
                    "title": "Concurrent bootstrap scope",
                    "summary": "Starts without a Context Reference",
                    "idempotency_key": "bootstrap-insert-conflict",
                },
            )
            assert created.status_code == 201, created.text
            current = created.json()["scope_id"]
            await _grant(client, current, "bob", "scope.viewer")
            if reader_has_candidate:
                public = await client.post(
                    "/v1/memory/remember",
                    json={
                        "scope_id": current,
                        "kind": "fact",
                        "text": "Public concurrent bootstrap context.",
                    },
                )
                assert public.status_code == 200, public.text
                public_id = public.json()["records"][0]["artifact"]["artifact_id"]
                public_tags_path = f"/v1/scopes/{current}/artifacts/atomic-memory/{public_id}/tags"
                public_tags = await client.get(public_tags_path)
                assert public_tags.status_code == 200, public_tags.text
                public_tagged = await client.put(
                    public_tags_path,
                    headers={"If-Match": public_tags.headers["ETag"]},
                    json={"tags": ["bootstrap-context"]},
                )
                assert public_tagged.status_code == 200, public_tagged.text
            headers = {"Authorization": "Bearer bob"}
            payload = {
                "scope_id": current,
                "enabled": True,
                "lifecycle": "resume",
                "integration": "access-regression",
                "event_id": "concurrent-bootstrap-event",
            }

            repository = app.state.application._bootstrap_receipts
            original_create = repository.create
            reader_waiting = asyncio.Event()
            release_reader = asyncio.Event()
            first_create = True

            async def pause_first_receipt(receipt):
                nonlocal first_create
                if first_create:
                    first_create = False
                    assert bool(receipt.items) is reader_has_candidate
                    reader_waiting.set()
                    await release_reader.wait()
                return await original_create(receipt)

            monkeypatch.setattr(repository, "create", pause_first_receipt)
            reader_task = asyncio.create_task(client.post("/v1/context/bootstrap", headers=headers, json=payload))
            try:
                await asyncio.wait_for(reader_waiting.wait(), timeout=5)
                updated = await client.put(
                    f"/v1/scopes/{current}",
                    json={
                        "expected_version": created.json()["version"],
                        "title": created.json()["title"],
                        "summary": created.json()["summary"],
                        "context_references": [private],
                    },
                )
                assert updated.status_code == 200, updated.text
                winner = await client.post("/v1/context/bootstrap", json=payload)
                assert winner.status_code == 200, winner.text
                assert winner.json()["status"] == "ready"
                assert "PRIVATE concurrent bootstrap constraint" in winner.json()["content"]
            finally:
                release_reader.set()

            denied = await reader_task
            assert denied.status_code == 403, denied.text
            assert "PRIVATE concurrent bootstrap constraint" not in denied.text
            assert (
                await client.get(
                    f"/v1/scopes/{private}/artifacts/atomic-memory/{memory_id}",
                    headers=headers,
                )
            ).status_code == 403
            retry = await client.post("/v1/context/bootstrap", headers=headers, json=payload)
            assert retry.status_code == 403
            assert "PRIVATE concurrent bootstrap constraint" not in retry.text

    asyncio.run(scenario())


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
def test_hints_share_continue_manifest_authority_and_resolve_published_evidence_in_its_original_scope(
    tmp_path, backend
):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            scope_id = await _scope(client)
            revision = await _handoff(client, scope_id)
            await _grant(client, scope_id, "bob", "handoff.viewer", _resource(scope_id, "handoff", "handoff"))
            bob = {"Authorization": "Bearer bob"}
            payload = {"scope_id": scope_id, "selection": "exact", "revision": revision}
            for selection in (payload, {"scope_id": scope_id, "selection": "latest"}):
                continued = await client.post("/v1/handoff/continue", headers=bob, json=selection)
                assert continued.status_code == 200 and continued.json()["status"] == "resolved", continued.text
                ready = await client.post("/v1/handoff/hint", headers=bob, json=selection | {"max_bytes": 4000})
                assert ready.status_code == 200 and ready.json()["status"] == "ready", ready.text
            source_ref = continued.json()["content"]["state"][0]["citations"][0]["source_ref"]
            direct = await client.get(
                f"/v1/scopes/{scope_id}/sources/{source_ref['name']}/{source_ref['source_id']}", headers=bob
            )
            assert direct.status_code == 403, direct.text
            prepared = {
                "schema": "powercontext.prepared-handoff.v1",
                "scope_id": scope_id,
                "base": revision,
                "content": continued.json()["content"],
            }
            prepared_payload = {"scope_id": scope_id, "selection": "prepared", "prepared": prepared}
            for path in ("/v1/handoff/continue", "/v1/handoff/hint"):
                denied = await client.post(path, headers=bob, json=prepared_payload)
                assert denied.status_code == 403, denied.text

            target = await client.post(
                "/v1/scopes", json={"title": "Target", "summary": "Published Handoff", "idempotency_key": "hint-target"}
            )
            target_id = target.json()["scope_id"]
            publication = await client.post(
                "/v1/artifact-publications",
                json={
                    "source": {"scope_id": scope_id, "artifact": revision},
                    "target_scope_id": target_id,
                    "idempotency_key": "hint-publication",
                },
            )
            assert publication.status_code == 201, publication.text
            published_revision = publication.json()["target"]["artifact"]
            published_access = await _grant(
                client,
                target_id,
                "bob",
                "handoff.viewer",
                _resource(target_id, "handoff", published_revision["artifact_id"]),
            )
            published_payload = {"scope_id": target_id, "selection": "exact", "revision": published_revision}
            continued = await client.post("/v1/handoff/continue", headers=bob, json=published_payload)
            assert continued.status_code == 200 and continued.json()["status"] == "resolved", continued.text
            ready = await client.post("/v1/handoff/hint", headers=bob, json=published_payload | {"max_bytes": 4000})
            assert ready.status_code == 200 and ready.json()["status"] == "ready", ready.text
            hint_data = json.loads(
                "\n".join(
                    line.removeprefix(">     ")
                    for line in ready.json()["content"].splitlines()
                    if line.startswith(">     ")
                )
            )
            assert hint_data["evidence_scope_id"] == scope_id
            assert hint_data["scope_id"] == target_id
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": published_access["binding_id"],
                    "expected_version": published_access["version"],
                    "idempotency_key": "revoke-hint-handoff-access",
                },
            )
            assert revoked.status_code == 200, revoked.text
            for path in ("/v1/handoff/continue", "/v1/handoff/hint"):
                denied = await client.post(path, headers=bob, json=published_payload)
                assert denied.status_code == 403, denied.text
            await _grant(client, scope_id, "bob", "scope.viewer")
            for path in ("/v1/handoff/continue", "/v1/handoff/hint"):
                request = prepared_payload | {"max_bytes": 4000} if path.endswith("hint") else prepared_payload
                allowed = await client.post(path, headers=bob, json=request)
                assert allowed.status_code == 200, allowed.text
                assert allowed.json()["status"] == ("ready" if path.endswith("hint") else "resolved"), allowed.text
            denied = await client.post("/v1/handoff/hint", headers={"Authorization": "Bearer stranger"}, json=payload)
            assert denied.status_code == 403

    asyncio.run(scenario())


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


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_static_preset_initializes_only_needed_permissions_without_reviving_revoked_bindings(tmp_path, backend):
    from powercontext.builtin.scope import ScopeDraft

    async def scenario():
        async with _server(tmp_path, backend) as (app, client, _):
            # An SDK-created Scope predates Server preset initialization.
            created_scope = await app.state.application.scopes.create(
                ScopeDraft(title="Existing", summary="No static preset", idempotency_key="existing-scope")
            )
            scope = created_scope.scope_id
            resource = {"type": "scope", "scope_id": scope}
            viewer = await client.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": "service", "id": "admin"},
                    "resource": resource,
                    "role": "scope.viewer",
                    "idempotency_key": "explicit-static-viewer",
                },
            )
            assert viewer.status_code == 201, viewer.text
            assert (await client.get(f"/v1/scopes/{scope}")).status_code == 200
            bindings = await client.post("/v1/access/bindings/list", json={"management_resource": resource})
            assert [item["binding_id"] for item in bindings.json()["items"]] == [viewer.json()["binding_id"]]
            created = await client.post(f"/v1/scopes/{scope}/sources", json={"content": "Initialize contribution"})
            assert created.status_code == 201, created.text
            bindings = await client.post(
                "/v1/access/bindings/list", json={"management_resource": resource, "role": "scope.contributor"}
            )
            (contributor,) = bindings.json()["items"]
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": contributor["binding_id"],
                    "expected_version": contributor["version"],
                    "idempotency_key": "revoke-static-contributor",
                },
            )
            assert revoked.status_code == 200, revoked.text
            denied = await client.post(f"/v1/scopes/{scope}/sources", json={"content": "Must stay denied"})
            assert denied.status_code == 403, denied.text
            assert (await client.get(f"/v1/scopes/{scope}")).status_code == 200
            bindings = await client.post(
                "/v1/access/bindings/list", json={"management_resource": resource, "role": "scope.contributor"}
            )
            assert [(item["binding_id"], item["state"]) for item in bindings.json()["items"]] == [
                (contributor["binding_id"], "revoked")
            ]

    asyncio.run(scenario())


def test_static_preset_can_create_and_read_atomic_memories_in_a_cold_scope(tmp_path):
    from powercontext.artifacts.search import ArtifactSearchExecutionContext
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
    from powercontext.server.authz import AccessControlService
    from powercontext.server.authz.repository import RelationalAccessRepository

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cold-preset.db'}"))
        async with open_builtin_contexts(config) as contexts:
            await contexts.get("project")
            actor = PrincipalRef(type="service", id="static-preset")
            repository = RelationalAccessRepository(contexts.database)
            access = AccessControlService(
                BuiltinAuthorizationProvider(repository),
                relationships=repository,
                audit=repository,
                static_scope_principal=actor,
            )
            execution = ArtifactSearchExecutionContext(
                principal=actor,
                access=access,
                audit=AccessAuditContext(transport="background", operation="cold-preset-create"),
            )
            created = await contexts.records.create_atomic_memories(
                "project",
                ({"kind": "fact", "text": "First fact"}, {"kind": "fact", "text": "Second fact"}),
                execution_context=execution,
            )
            records = await contexts.atomic_memory.for_scope("project").get_many(
                tuple(item.artifact_id for item in created), context=execution
            )
            assert [item.artifact.content.text for item in records] == ["First fact", "Second fact"]

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_missing_memory_in_readable_scope_is_not_owner_pending(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            source_scope = await _scope(client)
            target = await client.post(
                "/v1/scopes", json={"title": "Empty", "summary": "Empty", "idempotency_key": "empty"}
            )
            assert target.status_code == 201, target.text
            empty_scope = target.json()["scope_id"]
            for scope in (source_scope, empty_scope):
                await _grant(client, scope, "reader", "scope.viewer")
            remembered = await client.post(
                "/v1/memory/remember",
                json={"scope_id": source_scope, "kind": "fact", "text": "PRIVATE_SCOPE_FACT"},
            )
            assert remembered.status_code == 200, remembered.text
            entries = await client.post("/v1/atomic-memory/list", json={"scope_id": source_scope})
            assert entries.status_code == 200, entries.text
            artifact = entries.json()["items"][0]["artifact"]
            reader = {"Authorization": "Bearer reader"}
            suffix = f"/artifacts/atomic-memory/{artifact['artifact_id']}/revisions/{artifact['revision']}"
            correct = await client.get(f"/v1/scopes/{source_scope}{suffix}", headers=reader)
            assert correct.status_code == 200, correct.text
            assert correct.json()["content"]["text"] == "PRIVATE_SCOPE_FACT"
            for _ in range(2):
                missing = await client.get(f"/v1/scopes/{empty_scope}{suffix}", headers=reader)
                assert missing.status_code == 404, missing.text
                assert missing.json()["error"]["code"] == "artifact_not_found"
                assert "PRIVATE_SCOPE_FACT" not in missing.text
            for missing_suffix in ("", "/revisions/1", "/state"):
                missing = await client.get(
                    f"/v1/scopes/{source_scope}/artifacts/atomic-memory/absent-artifact{missing_suffix}", headers=reader
                )
                assert missing.status_code == 404, missing.text
                assert missing.json()["error"] == {
                    "code": "artifact_not_found",
                    "message": "The requested Artifact was not found.",
                    "details": None,
                }
            denied = [
                await client.get(f"/v1/scopes/{scope}{suffix}", headers={"Authorization": "Bearer stranger"})
                for scope in (source_scope, empty_scope, "absent-scope")
            ]
            assert [response.status_code for response in denied] == [403, 403, 403]
            assert denied[0].json()["error"] == denied[1].json()["error"] == denied[2].json()["error"]

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_missing_owner_does_not_block_explicit_reads(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, access):
            scope_id = await _scope(client)

            seeded = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "atomic-memory", "content": {"kind": "fact", "text": "OWNER_PENDING_PRIVATE_CONTENT"}},
            )
            assert seeded.status_code == 201, seeded.text
            stored = seeded.json()
            searchable = await client.post(
                "/v1/memory/search",
                json={"scope_id": scope_id, "query": "OWNER_PENDING_PRIVATE_CONTENT", "mode": "fts"},
            )
            assert searchable.status_code == 200, searchable.text
            assert len(searchable.json()["hits"]) == 1
            # A preexisting identity can be missing its Owner relation.
            from sqlalchemy import delete

            from powercontext.server.authz.repository import ACCESS_OWNERS_TABLE

            async with access.relationships._database.transaction() as connection:
                await connection.execute(
                    delete(ACCESS_OWNERS_TABLE).where(
                        ACCESS_OWNERS_TABLE.c.scope_id == scope_id,
                        ACCESS_OWNERS_TABLE.c.family == "atomic-memory",
                        ACCESS_OWNERS_TABLE.c.artifact_id == stored["artifact_id"],
                    )
                )
            artifact_url = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{stored['artifact_id']}"
            referencing = await client.post(
                "/v1/scopes",
                json={
                    "title": "Referencing scope",
                    "summary": "Owner readiness across context references",
                    "idempotency_key": "referencing-owner-pending",
                    "context_references": [scope_id],
                },
            )
            assert referencing.status_code == 201
            current = referencing.json()["scope_id"]
            requests = [
                ("GET", artifact_url + f"/revisions/{stored['revision']}", None),
                ("POST", "/v1/context/prepare", {"scope_id": current, "query": "OWNER_PENDING_PRIVATE_CONTENT"}),
                (
                    "POST",
                    "/v1/context/prepare",
                    {"scope_id": current, "query": "OWNER_PENDING_PRIVATE_CONTENT", "assembly": {}},
                ),
                ("POST", "/v1/atomic-memory/list", {"scope_id": scope_id}),
                ("POST", "/v1/atomic-memory/search", {"scope_id": scope_id, "query": "OWNER_PENDING_PRIVATE_CONTENT"}),
                ("POST", "/v1/memory/entries/list", {"scope_id": scope_id}),
                ("POST", "/v1/memory/search", {"scope_id": scope_id, "query": "OWNER_PENDING_PRIVATE_CONTENT"}),
                ("POST", "/v1/context/prepare", {"scope_id": scope_id, "query": "OWNER_PENDING_PRIVATE_CONTENT"}),
                ("GET", artifact_url, None),
                ("GET", f"/v1/scopes/{scope_id}/artifacts/atomic-memory", None),
                ("POST", "/v1/skill/library", {"scope_id": scope_id}),
                ("POST", "/v1/stats", {"selection": {"mode": "all"}}),
            ]
            for method, path, body in requests:
                response = await client.request(method, path, json=body)
                assert response.status_code == 200, (path, response.text)
            exact = await client.get(artifact_url + f"/revisions/{stored['revision']}")
            assert exact.json()["content"]["text"] == "OWNER_PENDING_PRIVATE_CONTENT"
            state = await client.get(artifact_url + "/state")
            assert state.status_code == 200, state.text
            assert state.json()["artifact"]["artifact_id"] == stored["artifact_id"]
            for path in (artifact_url, artifact_url + "/state"):
                denied = await client.get(path, headers={"Authorization": "Bearer stranger"})
                assert denied.status_code == 403, denied.text
                assert "OWNER_PENDING_PRIVATE_CONTENT" not in denied.text
            grant = await _grant(
                client,
                scope_id,
                "reader",
                "artifact.viewer",
                _resource(scope_id, "atomic-memory", stored["artifact_id"]),
            )
            reader = {"Authorization": "Bearer reader"}
            assert (
                await client.get(artifact_url + f"/revisions/{stored['revision']}", headers=reader)
            ).status_code == 200
            assert (await client.get(artifact_url + "/state", headers=reader)).status_code == 200
            assert (await client.get(f"/v1/scopes/{scope_id}", headers=reader)).status_code == 403
            denied_list = await client.post("/v1/atomic-memory/list", headers=reader, json={"scope_id": scope_id})
            assert denied_list.status_code == 403, denied_list.text
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": grant["binding_id"],
                    "expected_version": grant["version"],
                    "idempotency_key": "revoke-ownerless-reader",
                },
            )
            assert revoked.status_code == 200, revoked.text
            assert (await client.get(artifact_url, headers=reader)).status_code == 403

    asyncio.run(scenario())


@pytest.mark.parametrize("with_database", [False, True])
@pytest.mark.parametrize("legacy_records", [False, True])
def test_custom_runtime_preserves_record_service_composition(tmp_path, with_database, legacy_records):
    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            original = app.state.application
            records = original._records()

            class LegacyRecords:
                def __getattr__(self, name):
                    return getattr(records, name)

                async def create_artifact(self, scope_id, family, write, /, *, execution_context=None):
                    return await records.create_artifact(scope_id, family, write, execution_context=execution_context)

            async with BuiltinRuntime(
                provider=original._provider,
                capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
                primary_database=original.primary_database if with_database else None,
                scope_application=original.scopes,
                record_service=cast(RecordService, LegacyRecords()) if legacy_records else records,
            ) as custom:
                app.state.application = custom
                try:
                    created = await client.post(
                        f"/v1/scopes/{scope_id}/artifacts",
                        json={"family": "profile", "content": {"content": "# An externally composed runtime"}},
                    )
                    assert created.status_code == 201, created.text
                    resource = ResourceRef.artifact(
                        scope_id, family="profile", artifact_id=created.json()["artifact_id"]
                    )
                    owner = await access.artifact_owner(resource)
                    assert owner is not None and owner.owner == ADMIN
                finally:
                    app.state.application = original

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_owner_failure_rolls_back_base_artifact_creation(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (app, client, _):
            scope_id = await _scope(client)

            with _reject_owner_insert(tmp_path / "regressions.db"):
                created = await client.post(
                    f"/v1/scopes/{scope_id}/artifacts",
                    json={"family": "profile", "content": {"content": "# Owned profile"}},
                )
                assert created.status_code == 409, created.text
            records = app.state.application.records.for_scope(scope_id)
            assert await records.logical_artifacts() == ()
            assert (
                await client.post(
                    f"/v1/scopes/{scope_id}/artifacts",
                    json={"family": "profile", "content": {"content": "# Owned profile"}},
                )
            ).status_code == 201

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
@pytest.mark.parametrize("family", ["experience", "skill"])
def test_owner_failure_rolls_back_candidate_creation(tmp_path, backend, family):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            scope_id = await _scope(client)
            source = await client.post(f"/v1/scopes/{scope_id}/sources", json={"content": "Exact proposal evidence."})
            proposal = (
                {
                    "situation": "An owner store failed.",
                    "action": "Retry the proposal.",
                    "outcome": "No stranded candidate.",
                    "lesson": "Commit ownership with the proposal.",
                }
                if family == "experience"
                else {
                    "name": "owner-safe-proposal",
                    "description": "Persist an owned proposal.",
                    "instructions": "Commit ownership with the candidate.",
                    "validation": ["The candidate can be approved."],
                }
            )
            payload = {
                "scope_id": scope_id,
                "proposal": proposal,
                "source_refs": [{"name": "content", "source_id": source.json()["source_id"]}],
                "artifact_refs": [],
            }
            path = f"/v1/{family}/propose"

            with _reject_owner_insert(tmp_path / "regressions.db"):
                created = await client.post(path, json=payload)
                assert created.status_code == 409, created.text
            listed = await client.post("/v1/candidates/list", json={"scope_id": scope_id})
            assert listed.status_code == 200, listed.text
            assert listed.json()["candidates"] == []
            retried = await client.post(path, json=payload)
            assert retried.is_success, retried.text
            candidate = retried.json()
            approved = await client.post(
                "/v1/candidates/approve",
                json={"scope_id": scope_id, "candidate_id": candidate["candidate_id"], "expected_version": 1},
            )
            assert approved.status_code == 200, approved.text

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["handoff", "publication"])
def test_owner_failure_rolls_back_handoff_and_publication(tmp_path, operation):
    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            if operation == "publication":
                revision = await _handoff(client, scope_id)
                target = await client.post(
                    "/v1/scopes",
                    json={"title": "Target", "summary": "Owner rollback", "idempotency_key": "owner-target"},
                )
                destination = target.json()["scope_id"]
                path = "/v1/artifact-publications"
                payload = {
                    "source": {"scope_id": scope_id, "artifact": revision},
                    "target_scope_id": destination,
                    "idempotency_key": "owner-publication",
                }
            else:
                destination = scope_id
                source = await client.post(
                    f"/v1/scopes/{scope_id}/sources", json={"content": "Evidence for a first Handoff."}
                )
                statement = {
                    "text": "Continue the verified task.",
                    "citations": [
                        {
                            "kind": "source",
                            "source_ref": {
                                "name": "content",
                                "source_id": source.json()["source_id"],
                            },
                        }
                    ],
                }
                path = "/v1/handoff/commit"
                payload = {
                    "scope_id": scope_id,
                    "handoff": {
                        "schema": "powercontext.prepared-handoff.v1",
                        "scope_id": scope_id,
                        "base": None,
                        "content": {
                            "schema": "powercontext.handoff.v1",
                            "objective": "Continue verified work.",
                            "state": [statement],
                            "disposition": "continuable",
                            "next_action": statement,
                            "omissions": [],
                        },
                    },
                }

            with _reject_owner_insert(tmp_path / "regressions.db"):
                failed = await client.post(path, json=payload)
                assert failed.status_code == 409, failed.text
            assert await app.state.application.records.for_scope(destination).logical_artifacts() == ()
            retried = await client.post(path, json=payload)
            assert retried.is_success, retried.text
            artifacts = await app.state.application.records.for_scope(destination).logical_artifacts()
            assert len(artifacts) == 1
            resource = ResourceRef.artifact(destination, family="handoff", artifact_id=artifacts[0].artifact_id)
            owner = await access.artifact_owner(resource)
            assert owner is not None and owner.owner == ADMIN
            replay = await client.post(path, json=payload)
            assert replay.is_success, replay.text
            with sqlite3.connect(tmp_path / "regressions.db") as connection:
                connection.execute(
                    "DELETE FROM pc_access_owners WHERE scope_id = ? AND owner_kind = 'artifact'",
                    (destination,),
                )
            pending = await client.post(path, json=payload)
            assert pending.status_code == 503, pending.text
            assert await access.artifact_owner(resource) is None

    asyncio.run(scenario())


def test_custom_relationship_writer_preserves_its_owner_failure_semantics(tmp_path):
    from powercontext.server.authz.repository import RelationalAccessRepository

    class CustomRelationships(RelationalAccessRepository):
        async def establish_artifact_owner(self, relation):
            raise AccessUnavailableError("custom_owner_store_unavailable")

    async def scenario():
        async with _server(tmp_path) as (app, client, access):
            scope_id = await _scope(client)
            custom = CustomRelationships(access.relationships._database)
            access.relationships = access.audit = custom
            created = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "profile", "content": {"content": "# Custom owner store"}},
            )
            assert created.status_code == 503, created.text
            assert (
                await access.artifact_owner(ResourceRef.artifact(scope_id, family="profile", artifact_id="profile"))
                is None
            )
            # External stores retain RFC-1396's commit-then-fail-closed contract.
            records = await app.state.application.records.for_scope(scope_id).logical_artifacts()
            assert len(records) == 1

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
                    "/v1/candidates/get",
                    headers={"Authorization": f"Bearer {principal}"},
                    json={"scope_id": scope_id, "candidate_id": candidate_id},
                )
                assert response.status_code == 200, response.text
                assert response.json()["permissions"] == dict(
                    zip(("can_revise", "can_approve", "can_reject"), expected, strict=True)
                )
                listing = await client.post(
                    "/v1/candidates/list",
                    headers={"Authorization": f"Bearer {principal}"},
                    json={"scope_id": scope_id, "status": "pending"},
                )
                assert listing.status_code == 200, listing.text
                listed = next(item for item in listing.json()["candidates"] if item["candidate_id"] == candidate_id)
                assert listed["permissions"] == response.json()["permissions"]
            revision = payload | {"candidate_id": candidate_id, "expected_version": created.json()["version"]}
            denied = await client.post("/v1/candidates/revise", headers={"Authorization": "Bearer bob"}, json=revision)
            assert denied.status_code == 403, denied.text
            revised = await client.post(
                "/v1/candidates/revise", headers={"Authorization": "Bearer alice"}, json=revision
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
            body = await client.get(f"/v1/scopes/{scope_id}/artifacts/handoff/handoff", headers=bob)
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
            assert (
                await client.get(f"/v1/scopes/{scope_id}/artifacts/handoff/handoff", headers=bob)
            ).status_code == 403
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


def test_startup_removes_legacy_topic_memory_owners_and_keeps_other_relations(tmp_path):
    import sqlite3

    async def seed():
        async with _server(tmp_path) as (_, client, access):
            scope_id = await _scope(client)
            legacy = ResourceRef.artifact(scope_id, family="topic-memory", artifact_id="legacy-topic")
            skill = ResourceRef.artifact(scope_id, family="skill", artifact_id="skill-a")
            await access.establish_artifact_owner(legacy, ADMIN, idempotency_key="legacy-topic", context=AUDIT)
            await access.establish_artifact_owner(skill, ADMIN, idempotency_key="skill-a", context=AUDIT)
            await access.attest_candidate_owner(
                scope_id=scope_id,
                candidate_id="candidate-a",
                family="experience",
                proposed_owner=ADMIN,
                target=None,
                idempotency_key="candidate-a",
            )

    def relations():
        with sqlite3.connect(tmp_path / "regressions.db") as connection:
            return set(
                connection.execute(
                    "SELECT owner_kind, family, artifact_id, candidate_id FROM pc_access_owners"
                ).fetchall()
            )

    asyncio.run(seed())
    before = relations()
    assert ("artifact", "topic-memory", "legacy-topic", None) in before

    async def restart():
        async with _server(tmp_path) as (_, client, _):
            assert (await client.get("/v1/scopes")).status_code == 200

    asyncio.run(restart())
    after = relations()
    assert ("artifact", "topic-memory", "legacy-topic", None) not in after
    assert ("artifact", "skill", "skill-a", None) in after
    assert ("candidate", "experience", None, "candidate-a") in after

    # A later restart of an already-clean database changes nothing.
    asyncio.run(restart())
    assert relations() == after


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
            configuration_path = f"/v1/scopes/{scope}/prompts/atomic_memory.extract"
            default = await client.get(configuration_path, headers=reader)
            assert default.status_code == 200 and default.json()["artifact"] is None
            for hidden_scope in (scope, "absent-scope"):
                denied = await client.get(f"/v1/scopes/{hidden_scope}/prompts/atomic_memory.extract", headers=outsider)
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
                    json={"family": "prompt", "prompt_key": "atomic_memory.extract", "content": content},
                )
                assert denied.status_code == 403, denied.text
            assert (await client.get(configuration_path, headers=reader)).json()["artifact"] is None
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                headers=author,
                json={"family": "prompt", "prompt_key": "atomic_memory.extract", "content": content},
            )
            assert created.status_code == 201, created.text
            path = f"/v1/scopes/{scope}/artifacts/prompt/atomic_memory.extract"
            configuration = await client.get(configuration_path, headers=reader)
            assert configuration.status_code == 200
            assert configuration.json()["artifact"]["revision"] == 1
            assert (await client.get(configuration_path, headers=outsider)).status_code == 403
            for suffix in ("", "/revisions/1", "/revisions", "/tags"):
                allowed = await client.get(path + suffix, headers=reader)
                assert allowed.status_code == 200, allowed.text
                denied = await client.get(path + suffix, headers=outsider)
                assert denied.status_code == 403, denied.text
            tags = await client.get(path + "/tags", headers=reader)
            for headers in (contributor, reader, outsider):
                denied = await client.put(
                    path + "/tags", headers=headers | {"If-Match": tags.headers["ETag"]}, json={"tags": ["release"]}
                )
                assert denied.status_code == 403, denied.text
            tagged = await client.put(
                path + "/tags", headers=author | {"If-Match": tags.headers["ETag"]}, json={"tags": ["release"]}
            )
            assert tagged.status_code == 200, tagged.text
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
                    f"/v1/scopes/{scope}/prompts/atomic_memory.extract/demonstrations",
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
                json={"family": "prompt", "prompt_key": "atomic_memory.extract", "content": content},
            )
            assert created.status_code == 201, created.text
            bindings = (
                [administrator, contributor] if revoked_role == "scope.contributor" else [contributor, administrator]
            )
            path = f"/v1/scopes/{scope}/artifacts/prompt/atomic_memory.extract"
            tags = await client.get(path + "/tags", headers=author)
            assert tags.status_code == 200, tags.text
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
                if binding == administrator:
                    denied = await client.put(
                        path + "/tags",
                        headers=author | {"If-Match": tags.headers["ETag"]},
                        json={"tags": ["unauthorized"]},
                    )
                    assert denied.status_code == 403, denied.text
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
                f"/v1/scopes/{scope}/prompts/atomic_memory.extract/demonstrations",
                headers=author,
                json={"instructions": "Keep stable preferences.", "demonstration_count": 1},
            )
            assert denied.status_code == 403, denied.text
            current = await client.get(path)
            assert current.json() == original.json()
            assert current.headers["etag"] == '"revision:1"'
            history = await client.get(path + "/revisions")
            assert [item["revision"] for item in history.json()["items"]] == [1]
            configuration = await client.get(f"/v1/scopes/{scope}/prompts/atomic_memory.extract")
            assert configuration.json()["effective"]["instructions"] == content["instructions"]
            # Scope administration remains sufficient even when a revoked user owns the Artifact.
            await _grant(client, scope, "manager", "scope.admin")
            tagged = await client.put(
                path + "/tags",
                headers={"Authorization": "Bearer manager", "If-Match": tags.headers["ETag"]},
                json={"tags": ["release"]},
            )
            assert tagged.status_code == 200, tagged.text
            replaced = await client.put(
                path,
                headers={"Authorization": "Bearer manager", "If-Match": '"revision:1"'},
                json={"content": auto},
            )
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["revision"] == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_prompt_configuration_reads_saved_content_through_scope_grant_without_owner(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            scope = await _scope(client)

            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={
                    "family": "prompt",
                    "prompt_key": "atomic_memory.extract",
                    "content": {
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "auto",
                        "instructions": "",
                        "demonstrations": [],
                    },
                },
            )
            assert created.status_code == 201, created.text
            # Preserve the legacy ownerless-read scenario without creating a new partial write.
            with sqlite3.connect(tmp_path / "regressions.db") as connection:
                connection.execute(
                    "DELETE FROM pc_access_owners WHERE scope_id = ? AND family = 'prompt'",
                    (scope,),
                )
            response = await client.get(f"/v1/scopes/{scope}/prompts/atomic_memory.extract")
            assert response.status_code == 200, response.text
            assert response.json()["artifact"] == {
                "family": "prompt",
                "artifact_id": "atomic_memory.extract",
                "revision": 1,
            }
            assert response.json()["effective"] is not None
            denied = await client.get(
                f"/v1/scopes/{scope}/prompts/atomic_memory.extract", headers={"Authorization": "Bearer stranger"}
            )
            assert denied.status_code == 403, denied.text

    asyncio.run(scenario())


def test_atomic_state_read_keeps_content_and_lifecycle_in_one_snapshot(tmp_path, monkeypatch):
    """A concurrent write cannot pair an old content identity with new state."""
    from powercontext.server.settings import BearerAuthConfig

    async def scenario():
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-read-snapshot.db'}"),
                runtime=RuntimeConfig(artifact_processing_families=()),
                auth=BearerAuthConfig(enabled=False),
                mcp=McpConfig(enabled=False),
            )
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        ):
            scope = await _scope(client)
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={"family": "atomic-memory", "content": {"kind": "fact", "text": "Original content"}},
            )
            assert created.status_code == 201, created.text
            identity = created.json()["artifact_id"]
            path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{identity}"
            repository = app.state.application.atomic_memory._application.service.shared.states
            get_state = repository.get
            changed = False

            async def interleaved_state(connection, scope_id, family, artifact_id, **kwargs):
                nonlocal changed
                value = await get_state(connection, scope_id, family, artifact_id, **kwargs)
                if not changed and artifact_id == identity and not kwargs.get("for_update", False):
                    changed = True
                    replaced = await client.put(
                        path,
                        headers={"If-Match": created.headers["ETag"]},
                        json={"content": {"kind": "fact", "text": "Updated content"}},
                    )
                    assert replaced.status_code == 200, replaced.text
                    forgotten = await client.post(
                        "/v1/atomic-memory/lifecycle",
                        json={
                            "scope_id": scope,
                            "target": {
                                "artifact": {"family": "atomic-memory", "artifact_id": identity, "revision": 2},
                                "state_version": 0,
                            },
                            "state": "forgotten",
                        },
                    )
                    assert forgotten.status_code == 200, forgotten.text
                return value

            with monkeypatch.context() as patch:
                patch.setattr(repository, "get", interleaved_state)
                snapshot = await client.get(path + "/state")
            assert changed
            assert snapshot.status_code == 200, snapshot.text
            assert snapshot.json()["artifact"]["revision"] == 1
            assert snapshot.json()["state"] == "active"
            assert snapshot.json()["state_version"] == 0
            assert snapshot.headers["ETag"] == '"revision:1:state:0"'
            current = await client.get(path + "/state")
            assert current.status_code == 200, current.text
            assert current.json()["artifact"]["revision"] == 2
            assert current.json()["state"] == "forgotten"
            assert current.json()["state_version"] == 1
            assert current.headers["ETag"] == '"revision:2:state:1"'
            unchanged = await client.get(path + "/state", headers={"If-None-Match": current.headers["ETag"]})
            assert unchanged.status_code == 304

    asyncio.run(scenario())


def test_atomic_lifecycle_respects_a_custom_builtin_provider_denial(tmp_path):
    class DenyArtifactWrite(BuiltinAuthorizationProvider):
        async def check_batch(self, requests, /):
            decisions = await super().check_batch(requests)
            return tuple(
                AccessDecision(False, "custom-denial", decision.policy_revision)
                if request.action is AccessAction.ARTIFACT_WRITE
                else decision
                for request, decision in zip(requests, decisions, strict=True)
            )

    async def scenario():
        async with _server(tmp_path) as (_, client, access):
            scope_id = await _scope(client)
            remembered = await client.post(
                "/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": "Alpha scope fact."}
            )
            assert remembered.status_code == 200, remembered.text
            identity = remembered.json()["records"][0]["artifact"]["artifact_id"]
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{identity}"
            current = await client.get(path)
            assert current.status_code == 200, current.text

            access.provider = DenyArtifactWrite(access.provider._repository, deployment_id=access.deployment_id)
            edited = await client.put(
                path, headers={"If-Match": current.headers["ETag"]}, json={"content": {"kind": "fact", "text": "Beta."}}
            )
            assert edited.status_code == 403, edited.text
            forgotten = await client.post(
                "/v1/atomic-memory/lifecycle",
                json={
                    "scope_id": scope_id,
                    "target": {
                        "artifact": {"family": "atomic-memory", "artifact_id": identity, "revision": 1},
                        "state_version": 0,
                    },
                    "state": "forgotten",
                },
            )
            assert forgotten.status_code == 403, forgotten.text
            state = await client.get(path + "/state")
            assert state.status_code == 200, state.text
            assert state.json()["state"] == "active"
            assert state.json()["state_version"] == 0

    asyncio.run(scenario())


async def _assert_shared_note_dashboard(client, scope_id, identity, neighbor):
    reader = {"Authorization": "Bearer reader"}
    for headers in (reader, {**reader, "HX-Request": "true"}):
        selected = await client.get(
            "/dashboard/notes",
            headers=headers,
            params={"scope": scope_id, "artifact": identity, "revision": 1, "notes_cursor": "invalid"},
        )
        assert selected.status_code == 200, selected.text
        assert "Alpha scope fact." in selected.text
        assert "Alpha unshared fact." not in selected.text
        assert "Regression scope" not in selected.text
    for page in ("home", "notes", "usage"):
        denied = await client.get(f"/dashboard/{page}", headers=reader, params={"scope": scope_id})
        assert denied.status_code == 403, denied.text
    for principal, artifact_id in (
        ("reader", neighbor),
        ("stranger", identity),
    ):
        denied = await client.get(
            "/dashboard/notes",
            headers={"Authorization": f"Bearer {principal}"},
            params={"scope": scope_id, "artifact": artifact_id, "revision": 1},
        )
        assert denied.status_code == 403, denied.text


async def _assert_scope_memory_dashboard(client, scope_id, identity):
    reader = {"Authorization": "Bearer reader"}
    path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{identity}"
    for page in ("home", "notes", "usage"):
        allowed = await client.get(f"/dashboard/{page}", headers=reader, params={"scope": scope_id})
        assert allowed.status_code == 200, allowed.text
        assert "Regression scope" in allowed.text
        if page == "notes":
            assert "Alpha scope fact." in allowed.text
            assert "Alpha unshared fact." in allowed.text
        denied = await client.get(
            f"/dashboard/{page}", headers={"Authorization": "Bearer stranger"}, params={"scope": scope_id}
        )
        assert denied.status_code == 403, denied.text
    for endpoint in (path + "/revisions/1", path + "/state"):
        allowed = await client.get(endpoint, headers=reader)
        assert allowed.status_code == 200, allowed.text
    listed = await client.post("/v1/atomic-memory/list", headers=reader, json={"scope_id": scope_id})
    assert listed.status_code == 200, listed.text


@pytest.mark.parametrize("provider", ["builtin", "casbin", "custom"])
def test_atomic_scope_search_and_list_keep_exact_artifact_shares_separate(tmp_path, provider):
    from types import SimpleNamespace

    from powercontext.server.dashboard import mount_dashboard

    async def scenario():
        async with _server(tmp_path, "builtin" if provider == "custom" else provider) as (app, client, access):
            if provider == "custom":
                access.provider = SimpleNamespace(check=access.provider.check, check_batch=access.provider.check_batch)
            else:
                mount_dashboard(app)
            scope_id = await _scope(client)
            response = await client.post(
                "/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": "Alpha scope fact."}
            )
            assert response.status_code == 200, response.text
            memory = response.json()["records"][0]
            identity = memory["artifact"]["artifact_id"]
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{identity}"
            additional = await client.post(
                "/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": "Alpha unshared fact."}
            )
            assert additional.status_code == 200, additional.text
            additional_memory = additional.json()["records"][0]
            exact_grant = await _grant(
                client,
                scope_id,
                "reader",
                "artifact.viewer",
                resource={
                    "type": "artifact",
                    "scope_id": scope_id,
                    "identity": {"family": "atomic-memory", "artifact_id": identity},
                    "selector": None,
                },
            )
            reader = {"Authorization": "Bearer reader"}

            if provider != "custom":
                await _assert_shared_note_dashboard(
                    client, scope_id, identity, additional_memory["artifact"]["artifact_id"]
                )
            for endpoint in [path, path + "/revisions/1", path + "/revisions"]:
                exact = await client.get(endpoint, headers=reader)
                assert exact.status_code == 200, exact.text
            queries = [
                ("/v1/atomic-memory/search", {"scope_id": scope_id, "query": "alpha"}),
                ("/v1/memory/search", {"scope_id": scope_id, "query": "alpha"}),
                ("/v1/atomic-memory/list", {"scope_id": scope_id}),
                ("/v1/memory/entries/list", {"scope_id": scope_id}),
            ]
            for endpoint, payload in queries:
                denied = await client.post(endpoint, headers=reader, json=payload)
                assert denied.status_code == 403, denied.text
            scope_grant = await _grant(client, scope_id, "reader", "scope.viewer")
            # Scope authority reads another principal's current memory.
            for endpoint, payload in queries:
                allowed = await client.post(endpoint, headers=reader, json=payload)
                assert allowed.status_code == 200, allowed.text
            search = await client.post(queries[0][0], headers=reader, json=queries[0][1])
            assert {hit["memory"]["artifact"]["artifact_id"] for hit in search.json()["hits"]} == {
                identity,
                additional_memory["artifact"]["artifact_id"],
            }
            if provider == "custom":
                return
            revised = await client.put(
                path,
                headers={"If-Match": '"revision:1"'},
                json={"content": {"kind": "fact", "text": "Alpha scope fact. Updated current body."}},
            )
            assert revised.status_code == 200, revised.text
            for owner_present in (True, False):
                if not owner_present:
                    with sqlite3.connect(tmp_path / "regressions.db") as connection:
                        connection.execute(
                            "DELETE FROM pc_access_owners WHERE scope_id = ? AND family = ? AND artifact_id = ?",
                            (scope_id, "atomic-memory", identity),
                        )
                await _assert_scope_memory_dashboard(client, scope_id, identity)
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": scope_grant["binding_id"],
                    "expected_version": scope_grant["version"],
                    "idempotency_key": "revoke-dashboard-scope-viewer",
                },
            )
            assert revoked.status_code == 200, revoked.text
            await _assert_shared_note_dashboard(
                client, scope_id, identity, additional_memory["artifact"]["artifact_id"]
            )

            query = {"scope": scope_id, "artifact": identity, "revision": 1}
            historical = await client.get("/dashboard/notes", headers=reader, params=query)
            assert historical.status_code == 200, historical.text
            assert "Alpha scope fact." in historical.text
            assert "Updated current body." not in historical.text
            assert "Alpha unshared fact." not in historical.text
            assert "Regression scope" not in historical.text
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": exact_grant["binding_id"],
                    "expected_version": exact_grant["version"],
                    "idempotency_key": "revoke-dashboard-artifact-viewer",
                },
            )
            assert revoked.status_code == 200, revoked.text
            denied = await client.get("/dashboard/notes", headers=reader, params=query)
            assert denied.status_code == 403, denied.text
            assert "Alpha scope fact." not in denied.text

    asyncio.run(scenario())
