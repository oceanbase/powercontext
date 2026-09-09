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

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def test_scope_discovery_filters_one_explicit_field_in_sql_and_paginates(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scope-query.db'}"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        created = client.post(
            "/v1/scopes",
            json={
                "title": "ＰowerContext title",  # noqa: RUF001 - preserve database-native width semantics
                "summary": "Needle résumé 中文 %_*",
                "external_references": [{"kind": "repository", "value": "https://github.com/OceanBase/PowerContext"}],
                "idempotency_key": "needle",
            },
        ).json()
        scope_id = created["scope_id"]
        fragment = scope_id[4:12]
        second = client.post(
            "/v1/scopes",
            json={
                "title": "PowerContext child",
                "summary": "Child scope",
                "parent_scope_id": scope_id,
                "idempotency_key": "child",
            },
        ).json()
        binding_key = {"integration": "codex", "kind": "session", "external_id": "Workspace-PowerContext"}
        assert client.put("/v1/scope-bindings", json={"key": binding_key, "scope_id": scope_id}).status_code == 200

        matched = client.get(
            "/v1/scopes",
            params={"query": f"  {fragment}  ", "query_field": "scope_id"},
        )
        assert [item["scope_id"] for item in matched.json()["items"]] == [scope_id]
        assert client.get("/v1/scopes", params={"query": "ＰowerContext", "query_field": "title"}).json()["items"]  # noqa: RUF001
        assert client.get("/v1/scopes", params={"query": "powercontext", "query_field": "title"}).json()["items"] == []
        assert [
            item["scope_id"]
            for item in client.get("/v1/scopes", params={"query": "PowerContext", "query_field": "title"}).json()[
                "items"
            ]
        ] == [second["scope_id"]]
        for query in ("résumé", "中文", "%_*"):
            assert client.get("/v1/scopes", params={"query": query, "query_field": "summary"}).json()["items"]
        assert client.get("/v1/scopes", params={"query": "resume", "query_field": "summary"}).json()["items"] == []
        assert (
            client.get(
                "/v1/scopes", params={"query": "OceanBase/PowerContext", "query_field": "external_reference_value"}
            ).json()["items"][0]["scope_id"]
            == scope_id
        )
        assert (
            client.get(
                "/v1/scopes",
                params={
                    "query": "Workspace-PowerContext",
                    "query_field": "binding_external_id",
                    "binding_integration": "codex",
                    "binding_kind": "session",
                },
            ).json()["items"][0]["scope_id"]
            == scope_id
        )
        assert [
            item["scope_id"] for item in client.get("/v1/scopes", params={"parent_scope_id": scope_id}).json()["items"]
        ] == [second["scope_id"]]
        assert client.get("/v1/scopes", params={"query": "%_*", "query_field": "title"}).json()["items"] == []

        first_page = client.get("/v1/scopes", params={"limit": 1}).json()
        assert len(first_page["items"]) == 1
        assert first_page["next_cursor"]
        second_page = client.get("/v1/scopes", params={"limit": 1, "cursor": first_page["next_cursor"]}).json()
        assert second_page["items"][0]["scope_id"] > first_page["items"][0]["scope_id"]

        assert len(client.get("/v1/scopes", params={"query": "   "}).json()["items"]) == 3
        assert client.get("/v1/scopes", params={"query": fragment}).status_code == 422
        assert client.get("/v1/scopes", params={"query_field": "scope_id"}).status_code == 422
        assert client.get("/v1/scopes?query=one&query=two").status_code == 422
        assert client.get("/v1/scopes", params={"title": "Needle"}).status_code == 422


def test_scope_discovery_cursor_handles_expanding_unicode_query(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'unicode-query.db'}"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )
    query = "\ufdfa" * 256
    with TestClient(app) as client:
        scope_ids = set()
        for index in range(2):
            created = client.post(
                "/v1/scopes",
                json={"title": query, "summary": "Unicode pagination", "idempotency_key": f"unicode-{index}"},
            )
            assert created.status_code == 201, created.text
            scope_ids.add(created.json()["scope_id"])
        params = {"query": query, "query_field": "title", "limit": 1}
        first = client.get("/v1/scopes", params=params)
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert cursor and len(cursor) <= 4096
        second = client.get("/v1/scopes", params=params | {"cursor": cursor})
        assert second.status_code == 200, second.text
        assert second.json()["next_cursor"] is None
        assert {item["scope_id"] for page in (first, second) for item in page.json()["items"]} == scope_ids
        changed = client.get("/v1/scopes", params=params | {"cursor": cursor, "query": "other"})
        assert changed.status_code == 400, changed.text
        assert changed.json()["error"]["code"] == "invalid_cursor"


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


@pytest.mark.parametrize("target_has_profile", [False, True])
def test_scope_http_rejects_profile_publication(tmp_path, target_has_profile) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile-copy.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:
        scope_ids = [
            client.post("/v1/scopes", json={"title": name, "summary": name, "idempotency_key": name}).json()["scope_id"]
            for name in ("Source", "Target")
        ]
        source_id, target_id = scope_ids
        for sid in scope_ids if target_has_profile else scope_ids[:1]:
            assert (
                client.post(
                    f"/v1/scopes/{sid}/artifacts", json={"family": "profile", "content": {"content": "# Profile"}}
                ).status_code
                == 201
            )
        target_path = f"/v1/scopes/{target_id}/artifacts/profile/profile"
        before = client.get(target_path)
        request = {
            "source": {
                "scope_id": source_id,
                "artifact": {"family": "profile", "artifact_id": "profile", "revision": 1},
            },
            "target_scope_id": target_id,
            "idempotency_key": "profile-copy",
        }
        for _ in range(2):
            response = client.post("/v1/artifact-publications", json=request)
            assert response.status_code == 422
            assert response.json()["error"] == {
                "code": "artifact_publication_unsupported",
                "message": "Profile artifacts cannot be copied or published across Scopes.",
                "details": {"family": "profile"},
            }
        after = client.get(target_path)
        assert after.status_code == before.status_code
        assert after.json() == before.json()


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
