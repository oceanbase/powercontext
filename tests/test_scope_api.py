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

import sqlite3

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def test_artifact_handle_bind_and_resolve_public_path(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handles.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        created = client.post(
            f"/v1/scopes/{scope_id}/artifacts",
            json={"family": "profile", "content": {"content": "# Chinese support agent"}},
        )
        assert created.status_code == 201
        artifact = created.json()
        path = f"/v1/scopes/{scope_id}/artifact-handles/profile/support-agent"
        expected = {
            "family": "profile",
            "key": "support-agent",
            "artifact": {
                "family": "profile",
                "artifact_id": artifact["artifact_id"],
                "revision": artifact["revision"],
            },
        }

        bound = client.put(
            path,
            json={"artifact_id": artifact["artifact_id"], "revision": artifact["revision"]},
        )
        assert bound.status_code == 200
        assert bound.json() == expected
        assert client.get(path).json() == expected

        artifact_path = f"/v1/scopes/{scope_id}/artifacts/profile/{artifact['artifact_id']}"
        revised = client.put(
            artifact_path,
            headers={"If-Match": created.headers["ETag"]},
            json={"content": {"content": "# English support agent"}},
        )
        assert revised.status_code == 200
        assert revised.json()["revision"] == 2
        assert client.get(path).json() == expected

        conflicting = client.put(
            path,
            json={"artifact_id": artifact["artifact_id"], "revision": revised.json()["revision"]},
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["error"]["details"] == {"kind": "artifact_handle"}

        missing = client.get(f"/v1/scopes/{scope_id}/artifact-handles/profile/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "artifact_handle_not_found"


@pytest.mark.parametrize("binding_kind", ["durable", "default"])
def test_missing_binding_target_is_a_conflict_without_fallback_or_replacement(tmp_path, binding_kind) -> None:
    database_path = tmp_path / "missing-target.db"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            mcp=McpConfig(enabled=False),
        )
    )
    key = {"integration": "test", "kind": "session", "external_id": "missing-target"}
    fallback_key = {**key, "external_id": "fallback"}
    with TestClient(app) as client:
        default_id = client.get("/v1/scopes/default").json()["scope_id"]
        missing_id = client.post(
            "/v1/scopes", json={"title": "Missing", "summary": "Lost scope", "idempotency_key": "missing"}
        ).json()["scope_id"]
        assert client.put("/v1/scope-bindings", json={"key": key, "scope_id": missing_id}).status_code == 200
        assert client.put("/v1/scope-bindings", json={"key": fallback_key, "scope_id": default_id}).status_code == 200
        if binding_kind == "default":
            assert client.put("/v1/scopes/default", json={"scope_id": missing_id}).status_code == 200

        # Simulate out-of-band data loss; normal writes enforce foreign keys.
        with sqlite3.connect(database_path) as connection:
            connection.execute("DELETE FROM pc_scopes WHERE scope_id = ?", (missing_id,))

        scope_count = len(client.get("/v1/scopes").json()["items"])
        request = {"binding_keys": [key, fallback_key]} if binding_kind == "durable" else {}
        for _ in range(2):
            response = client.post("/v1/scope-bindings/resolve", json=request)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "scope_binding_target_missing"
        if binding_kind == "default":
            response = client.get("/v1/scopes/default")
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "scope_binding_target_missing"

        # Direct lookup has no persisted binding provenance and remains a 404.
        response = client.post("/v1/scope-bindings/resolve", json={"explicit_scope_id": missing_id})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "scope_not_found"
        response = client.post("/v1/scope-bindings/resolve", json={"allow_default": False})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "scope_not_found"
        assert len(client.get("/v1/scopes").json()["items"]) == scope_count


def test_subject_source_rejects_a_missing_persisted_binding_target(tmp_path) -> None:
    database_path = tmp_path / "subject-target.db"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:
        origin = client.get("/v1/scopes/default").json()["scope_id"]
        path = f"/v1/scopes/{origin}/subject-sources"
        request = {"content": "Prefer concise answers.", "subject_key": "user-1"}
        created = client.post(path, json=request)
        assert created.status_code == 201
        target = created.json()["subject_scope_id"]
        with sqlite3.connect(database_path) as connection:
            connection.execute("DELETE FROM pc_scopes WHERE scope_id = ?", (target,))
        scope_count = len(client.get("/v1/scopes").json()["items"])
        for _ in range(2):
            response = client.post(path, json=request)
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "scope_binding_target_missing"
        assert len(client.get("/v1/scopes").json()["items"]) == scope_count


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
        ).json()["records"][0]["artifact"]
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
            "details": {"family": "atomic-memory"},
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


def test_historical_scope_backfill_upgrades_without_422(tmp_path) -> None:
    from sqlalchemy import create_engine

    from powercontext.builtin.persistence.migrations import historical

    db_path = tmp_path / "historical_upgrade.db"
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{db_path}"),
        mcp=McpConfig(enabled=False),
    )

    with TestClient(create_server_app(settings=settings)) as client:
        valid = client.post(
            "/v1/scopes",
            json={"title": "Valid Scope", "summary": "Valid Metadata", "idempotency_key": "valid-1"},
        )
        assert valid.status_code == 201
        valid_id = valid.json()["scope_id"]

    # Historical state: a source carries a scope ID before Scope was stored as separate entity
    historical_scope_id = "scp_historical_legacy"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO pc_sources (scope_id, source_type, source_id, payload, journal_position) "
            "VALUES (?, 'message', 'msg-1', ?, 1)",
            (historical_scope_id, b'{"text":"Legacy content"}'),
        )
        conn.execute("DELETE FROM pc_scopes WHERE scope_id = ?", (historical_scope_id,))

    # Upgrade through historical migration
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        historical._scopes(connection)

    # Calling unfiltered GET /v1/scopes must return 200 with both scopes
    with TestClient(create_server_app(settings=settings)) as client:
        response = client.get("/v1/scopes", params={"limit": 50})
        assert response.status_code == 200
        items = {item["scope_id"]: item for item in response.json()["items"]}

        assert valid_id in items
        assert items[valid_id]["title"] == "Valid Scope"
        assert items[valid_id]["summary"] == "Valid Metadata"

        assert historical_scope_id in items
        assert items[historical_scope_id]["title"] == historical_scope_id
        assert items[historical_scope_id]["summary"] == historical_scope_id


def test_ready_database_repairs_invalid_descriptors(tmp_path) -> None:
    from sqlalchemy import create_engine

    from powercontext.builtin.persistence.migrations import historical

    db_path = tmp_path / "ready_repair.db"
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{db_path}"),
        mcp=McpConfig(enabled=False),
    )

    with TestClient(create_server_app(settings=settings)) as client:
        valid = client.post(
            "/v1/scopes",
            json={"title": "Keep Valid", "summary": "Keep Summary", "idempotency_key": "valid-2"},
        )
        assert valid.status_code == 201
        valid_id = valid.json()["scope_id"]

    # Inject an invalid descriptor with empty strings into a ready database
    invalid_id = "scp_needs_repair"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO pc_scopes (scope_id, title, summary, scope_id_search, title_search, summary_search, version) "
            "VALUES (?, '', '', ?, '', '', 1)",
            (invalid_id, invalid_id),
        )

    # Server restarts against the ready database; read fallback recovers the descriptor while startup stays read-only
    with TestClient(create_server_app(settings=settings)) as client:
        response = client.get("/v1/scopes", params={"limit": 50})
        assert response.status_code == 200
        items = {item["scope_id"]: item for item in response.json()["items"]}

        assert valid_id in items
        assert items[valid_id]["title"] == "Keep Valid"
        assert items[valid_id]["summary"] == "Keep Summary"

        assert invalid_id in items
        assert items[invalid_id]["title"] == invalid_id
        assert items[invalid_id]["summary"] == invalid_id

    # Managed startup did not write to the database (persistent repair belongs to explicit maintenance)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT title, summary FROM pc_scopes WHERE scope_id = ?", (invalid_id,)).fetchone()
        assert row == ("", "")

    # Explicit managed maintenance persistently repairs the stored descriptor
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        historical._scopes(connection)

    with sqlite3.connect(db_path) as conn:
        repaired = conn.execute("SELECT title, summary FROM pc_scopes WHERE scope_id = ?", (invalid_id,)).fetchone()
        assert repaired == (invalid_id, invalid_id)


def test_managed_startup_succeeds_under_concurrent_database_write(tmp_path) -> None:
    db_path = tmp_path / "concurrent_write.db"
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{db_path}"),
        mcp=McpConfig(enabled=False),
    )

    with TestClient(create_server_app(settings=settings)) as client:
        res = client.post(
            "/v1/scopes",
            json={"title": "Primary Scope", "summary": "Primary Summary", "idempotency_key": "c-1"},
        )
        assert res.status_code == 201

    # Simulate an active write transaction
    with sqlite3.connect(db_path, timeout=1.0) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO pc_scopes (scope_id, title, summary, scope_id_search, title_search, summary_search, version) "
            "VALUES ('scp_concurrent', 'Concurrent', 'Summary', 'scp_concurrent', 'Concurrent', 'Summary', 1)"
        )

        # Managed startup remains read-only and succeeds without write-lock collisions
        with TestClient(create_server_app(settings=settings)) as client:
            response = client.get("/v1/scopes", params={"limit": 50})
            assert response.status_code == 200

        writer.execute("COMMIT")


