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

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_scope_http_flow_resolves_default_durable_and_observation_ranges(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        default = client.get("/v1/scopes/default")
        assert default.status_code == 200
        default_scope_id = default.json()["scope_id"]

        root = client.post(
            "/v1/scopes",
            json={"title": "Feature", "summary": "Feature result", "idempotency_key": "feature"},
        )
        assert root.status_code == 201
        root_scope_id = root.json()["scope_id"]
        child = client.post(
            "/v1/scopes",
            json={
                "title": "Validation",
                "summary": "Independent validation",
                "parent_scope_id": root_scope_id,
                "idempotency_key": "validation",
            },
        )
        assert child.status_code == 201
        child_scope_id = child.json()["scope_id"]

        binding_key = {"integration": "codex", "kind": "session", "external_id": "session-1"}
        workspace_key = {"integration": "codex", "kind": "workspace", "external_id": "workspace-1"}
        assert (
            client.put(
                "/v1/scope-bindings",
                json={"key": binding_key, "scope_id": child_scope_id},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/v1/scope-bindings",
                json={"key": workspace_key, "scope_id": root_scope_id},
            ).status_code
            == 200
        )
        resolved = client.post(
            "/v1/scope-bindings/resolve",
            json={"binding_keys": [binding_key, workspace_key]},
        )
        assert resolved.status_code == 200
        assert resolved.json()["scope_id"] == child_scope_id
        workspace = client.post(
            "/v1/scope-bindings/resolve",
            json={"binding_keys": [workspace_key]},
        )
        assert workspace.json()["scope_id"] == root_scope_id

        subtree = client.post(
            "/v1/scopes/selection/resolve",
            json={"selection": {"mode": "subtree", "root_scope_id": root_scope_id}},
        )
        assert [scope["scope_id"] for scope in subtree.json()["items"]] == [root_scope_id, child_scope_id]
        all_scopes = client.post(
            "/v1/scopes/selection/resolve",
            json={"selection": {"mode": "all"}},
        )
        assert {scope["scope_id"] for scope in all_scopes.json()["items"]} == {
            default_scope_id,
            root_scope_id,
            child_scope_id,
        }


def test_scope_http_flow_rejects_stale_metadata_and_invalid_selection(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        created = client.post(
            "/v1/scopes",
            json={"title": "Work", "summary": "Initial", "idempotency_key": "work"},
        ).json()
        request = {
            "expected_version": created["version"],
            "title": "Work",
            "summary": "Updated",
        }
        scope_path = f"/v1/scopes/{created['scope_id']}"
        fetched = client.get(scope_path)
        assert fetched.status_code == 200
        assert fetched.json() == created

        assert client.put(scope_path, json=request).status_code == 200
        stale = client.put(scope_path, json=request)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "scope_version_conflict"

        invalid = client.post(
            "/v1/scopes/selection/resolve",
            json={"selection": {"mode": "exact", "scope_ids": []}},
        )
        assert invalid.status_code == 422


def test_scope_http_flow_rejects_incomplete_memory_publication(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        source_scope_id = client.post(
            "/v1/scopes",
            json={"title": "Source", "summary": "Working state", "idempotency_key": "source"},
        ).json()["scope_id"]
        target_scope_id = client.post(
            "/v1/scopes",
            json={"title": "Target", "summary": "Accepted state", "idempotency_key": "target"},
        ).json()["scope_id"]
        memory = client.post(
            "/v1/memory/remember",
            json={"scope_id": source_scope_id, "kind": "decision", "text": "Publish the accepted decision."},
        ).json()["memory"]
        request = {
            "source": {"scope_id": source_scope_id, "artifact": memory},
            "target_scope_id": target_scope_id,
            "idempotency_key": "accepted-decision",
        }

        rejected = client.post("/v1/artifact-publications", json=request)
        repeated = client.post("/v1/artifact-publications", json=request)

        assert rejected.status_code == 422
        assert repeated.status_code == 422
        assert rejected.json() == repeated.json()
        assert rejected.json()["error"] == {
            "code": "artifact_publication_unsupported",
            "message": "The Artifact family cannot be published as complete target state.",
            "details": {"family": "memory"},
        }


def test_data_plane_rejects_an_unknown_scope(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/memory/search",
            json={"scope_id": "scp_unknown", "query": "prior decision"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "scope_not_found"
