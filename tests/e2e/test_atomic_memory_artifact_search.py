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

"""Unified Atomic search preserves Scope authorization and current exact results."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.server.authz import AccessControlService, AccessDeniedError, PrincipalRef
from powercontext.server.authz.repository import RelationalAccessRepository
from tests.e2e.test_access_control_regressions import AUDIT, _grant, _resource, _scope, _server


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_unified_atomic_search_requires_scope_read_but_exact_share_remains_readable(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (app, client, access):
            scope = await _scope(client)
            seeded = await client.post(
                "/v1/memory/remember",
                json={"scope_id": scope, "kind": "fact", "text": "Alpha private body."},
            )
            assert seeded.status_code == 200, seeded.text
            record = seeded.json()["records"][0]
            artifact_id = record["artifact"]["artifact_id"]
            await _grant(client, scope, "viewer", "artifact.viewer", _resource(scope, "atomic-memory", artifact_id))
            headers = {"Authorization": "Bearer viewer"}
            path = f"/v1/scopes/{scope}/artifacts/atomic-memory/search"
            exact = await client.get(f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}", headers=headers)
            assert exact.status_code == 200 and exact.json()["content"]["text"] == "Alpha private body."
            denied = await client.post(path, headers=headers, json={"query": "alpha"})
            assert denied.status_code == 403 and "private body" not in denied.text
            application = app.state.application.artifacts.for_scope(scope)
            context = ArtifactSearchExecutionContext(
                principal=PrincipalRef(type="user", id="viewer"), access=access, audit=AUDIT
            )
            with pytest.raises(AccessDeniedError):
                await application.search("atomic-memory", {"query": "alpha"}, execution_context=context)
            with pytest.raises(AccessDeniedError):
                await application.search(
                    "atomic-memory", {"query": "alpha"}, execution_context=ArtifactSearchExecutionContext()
                )
            await _grant(client, scope, "viewer", "scope.viewer")
            await _grant(client, scope, "alice", "scope.contributor")
            other = await client.post(
                "/v1/memory/remember",
                headers={"Authorization": "Bearer alice"},
                json={"scope_id": scope, "kind": "preference", "text": "Alpha foreign owner body."},
            )
            assert other.status_code == 200, other.text
            allowed = await client.post(path, headers=headers, json={"query": "alpha", "mode": "text"})
            assert allowed.status_code == 200, allowed.text
            assert {item["content"]["text"] for item in allowed.json()["results"]} == {
                "Alpha private body.",
                "Alpha foreign owner body.",
            }
            assert all("scores" not in item for item in allowed.json()["results"])
            outcome = await application.search(
                "atomic-memory", {"query": "alpha", "mode": "text"}, execution_context=context
            )
            page = await app.state.application.atomic_memory.for_scope(scope).search(
                "alpha",
                mode="text",
                context=ArtifactSearchExecutionContext(principal=context.principal, access=access, audit=AUDIT),
            )
            assert tuple(match.artifact_ref for match in outcome.matches) == tuple(
                hit.hit.artifact_ref for hit in page.hits
            )
            assert tuple(match.retrieval_score for match in outcome.matches) == tuple(
                hit.hit.score for hit in page.hits
            )
            assert tuple(artifact.as_ref() for artifact in outcome.artifacts) == tuple(
                match.artifact_ref for match in outcome.matches
            )
            scored = await client.post(path, headers=headers, json={"query": "alpha", "include_scores": True})
            assert scored.status_code == 422
            assert scored.json()["error"]["code"] == "artifact_search_not_supported"
            assert scored.json()["error"]["details"]["field"] == "include_scores"
            invalid = await client.post(path, headers=headers, json={"query": "alpha", "limit": 101})
            assert invalid.status_code == 422

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_unified_atomic_search_materializes_current_revision_and_family_filters(tmp_path, backend):
    async def scenario():
        async with _server(tmp_path, backend) as (_, client, _):
            scope = await _scope(client)
            seeded = await client.post(
                "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Obsolete atomic body."}
            )
            assert seeded.status_code == 200, seeded.text
            artifact_id = seeded.json()["records"][0]["artifact"]["artifact_id"]
            changed = await client.put(
                f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}",
                headers={"If-Match": '"revision:1"'},
                json={"content": {"kind": "preference", "text": "Current atomic body."}},
            )
            assert changed.status_code == 200, changed.text
            path = f"/v1/scopes/{scope}/artifacts/atomic-memory/search"
            response = await client.post(path, json={"query": "current", "filters": {"kind": "preference"}})
            assert response.status_code == 200, response.text
            result = response.json()["results"]
            assert len(result) == 1
            assert result[0]["artifact_id"] == artifact_id and result[0]["revision"] == 2
            assert result[0]["content"] == {
                "schema": "powercontext.atomic-memory.v1",
                "kind": "preference",
                "text": "Current atomic body.",
                "creation": None,
            }
            for request in ({"query": "obsolete"}, {"query": "current", "filters": {"kind": "fact"}}):
                empty = await client.post(path, json=request)
                assert empty.status_code == 200 and empty.json()["results"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
@pytest.mark.parametrize("relationships", [True, False])
def test_unified_atomic_search_preserves_custom_decision_repository_denial(tmp_path, backend, relationships):
    async def scenario():
        policy = {"allowed": True}
        viewer = PrincipalRef(type="user", id="viewer")

        class PolicyRepository(RelationalAccessRepository):
            async def decision_snapshot(self, subjects, **kwargs):
                state = await super().decision_snapshot(subjects, **kwargs)
                return replace(state, bindings=()) if viewer in subjects and not policy["allowed"] else state

        async with _server(tmp_path, backend) as (app, client, access):
            scope = await _scope(client)
            created = await client.post(
                "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Alpha configured body."}
            )
            assert created.status_code == 200, created.text
            await _grant(client, scope, "viewer", "scope.viewer")
            database = app.state.application.atomic_memory._application.database
            access.provider = type(access.provider)(PolicyRepository(database), deployment_id="regressions")
            path = f"/v1/scopes/{scope}/artifacts/atomic-memory/search"
            headers = {"Authorization": "Bearer viewer"}
            allowed = await client.post(path, headers=headers, json={"query": "alpha"})
            assert allowed.status_code == 200 and len(allowed.json()["results"]) == 1
            policy["allowed"] = False
            denied = await client.post(path, headers=headers, json={"query": "alpha"})
            assert denied.status_code == 403 and "configured body" not in denied.text
            point_access = AccessControlService(
                access.provider,
                relationships=access.relationships if relationships else None,
                audit=access.audit,
                deployment_id="regressions",
            )
            context = ArtifactSearchExecutionContext(principal=viewer, access=point_access, audit=AUDIT)
            with pytest.raises(AccessDeniedError):
                await app.state.application.artifacts.for_scope(scope).search(
                    "atomic-memory", {"query": "alpha"}, execution_context=context
                )
            policy["allowed"] = True
            runtime_recovered = await app.state.application.artifacts.for_scope(scope).search(
                "atomic-memory", {"query": "alpha"}, execution_context=context
            )
            assert len(runtime_recovered.artifacts) == 1
            recovered = await client.post(path, headers=headers, json={"query": "alpha"})
            assert recovered.status_code == 200 and len(recovered.json()["results"]) == 1

    asyncio.run(scenario())
