"""Exercise HTML against real runtime operations and isolated databases."""

from pathlib import Path
from secrets import token_urlsafe
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.dashboard.routes import LABELS
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


@pytest.fixture
def dashboard(tmp_path: Path):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/data.db"), mcp=McpConfig(enabled=False)
        ),
        scheduler_path=tmp_path / "scheduler.db",
    )
    with TestClient(app) as client:
        yield client


def create_scope(client: TestClient, title: str, parent: str | None = None) -> dict[str, Any]:
    result = client.post(
        "/v1/scopes",
        json={"title": title, "summary": f"Work on {title}", "parent_scope_id": parent, "idempotency_key": title},
    )
    assert result.status_code == 201
    return result.json()


def test_scope_selection_default_and_unknown_scope(dashboard: TestClient) -> None:
    parent = create_scope(dashboard, "Project")
    child = create_scope(dashboard, "Documentation", parent["scope_id"])
    assert dashboard.put("/v1/scopes/default", json={"scope_id": child["scope_id"]}).status_code == 200
    result = dashboard.get("/dashboard/home")
    assert result.status_code == 200
    assert f'value="{child["scope_id"]}" selected' in result.text
    assert "Work on Documentation" in result.text
    assert "Work on Project" not in result.text
    result = dashboard.get("/dashboard/home", params={"scope": parent["scope_id"]})
    assert "Work on Project" in result.text
    assert dashboard.get("/v1/scopes/default").json()["scope_id"] == child["scope_id"]
    assert dashboard.get("/dashboard/home?scope=missing").status_code == 404
    assert LABELS["fresh_heading"] not in dashboard.get("/dashboard/home?scope=missing").text
    assert LABELS["entry_heading"] in dashboard.get("/dashboard/home?scope=").text
    assert dashboard.get("/dashboard/preview").status_code == 404


def test_memory_exact_revision_and_cross_scope_isolation(dashboard: TestClient) -> None:
    first = create_scope(dashboard, "Library")
    other = create_scope(dashboard, "Unrelated")
    saved = dashboard.post(
        "/v1/memory/remember",
        json={
            "scope_id": first["scope_id"],
            "kind": "constraint",
            "text": "Keep <public> interfaces stable & preserve line breaks.\nReview every migration.",
        },
    )
    assert saved.status_code == 200
    entry = saved.json()["entry"]
    citation = entry["citation"]
    query = {
        "scope": first["scope_id"],
        "entry": citation["entry_id"],
        "memory_id": citation["memory_ref"]["artifact_id"],
        "memory_revision": citation["memory_ref"]["revision"],
        "entry_version": citation["entry_version_id"],
    }
    response = dashboard.get("/dashboard/notes", params=query)
    assert response.status_code == 200
    assert "&lt;public&gt;" in response.text
    assert "<public>" not in response.text
    assert dashboard.get("/dashboard/notes", params={**query, "entry_version": "missing"}).status_code == 404
    assert (
        dashboard.get(
            "/dashboard/notes", params={key: value for key, value in query.items() if key != "memory_id"}
        ).status_code
        == 422
    )
    assert dashboard.get("/dashboard/notes", params={**query, "scope": other["scope_id"]}).status_code == 404
    assert "Review every migration" not in dashboard.get("/dashboard/notes", params={"scope": other["scope_id"]}).text
    partial = dashboard.get("/dashboard/notes", params=query, headers={"HX-Request": "true"})
    assert "<!doctype" not in partial.text
    restored = dashboard.get(
        "/dashboard/notes", params=query, headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    )
    assert "<!doctype" in restored.text
    assert restored.headers["cache-control"] == "no-store"


def test_authentication_recovers_without_exposing_credentials(tmp_path: Path) -> None:
    token = token_urlsafe(24)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/auth.db"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=tmp_path / "scheduler.db",
    )
    with TestClient(app) as client:
        assert client.get("/dashboard/home").status_code == 401
        assert client.get("/dashboard/static/layout.css").status_code == 200
        assert (
            client.post(
                "/dashboard/session", data={"token": token}, headers={"Origin": "https://foreign.test"}
            ).status_code
            == 403
        )
        response = client.post("/dashboard/session", data={"token": token}, headers={"Origin": "http://testserver"})
        assert response.status_code == 200
        assert token not in response.text
        assert client.get("/v1/scopes").status_code == 401
        assert client.get("/v1/scopes", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert (
            client.post(
                "/dashboard/session", data={"token": "invalid"}, headers={"Origin": "http://testserver"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/dashboard/session", data={"token": token}, headers={"Origin": "http://testserver"}
            ).status_code
            == 200
        )


def commit_handoff(dashboard: TestClient, scope: str) -> dict[str, Any]:
    source = dashboard.post(
        "/v1/sources/content",
        json={"scope_id": scope, "source_id": "review", "content": "Review remains in progress."},
    ).json()["source"]
    citation = {"kind": "source", "source_ref": source}
    prepared = dashboard.post(
        "/v1/handoff/finalize",
        json={
            "scope_id": scope,
            "draft": {
                "objective": "Review the HTTP contract",
                "state": [{"text": "Review remains in progress.", "citations": [citation]}],
                "disposition": "continuable",
                "next_action": {"text": "Continue the review.", "citations": [citation]},
                "omissions": [],
            },
        },
    )
    assert prepared.status_code == 200
    committed = dashboard.post("/v1/handoff/commit", json={"scope_id": scope, "handoff": prepared.json()})
    assert committed.status_code == 200
    return committed.json()["reference"]


def test_committed_handoff_json_and_its_sources_are_readable(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Contract review")["scope_id"]
    record = commit_handoff(dashboard, scope)
    query = {"scope": scope, "artifact": record["artifact_id"], "revision": record["revision"]}
    response = dashboard.get("/dashboard/handoff-detail", params=query)
    assert response.status_code == 200
    assert "Review remains in progress." in response.text
    assert "Continue the review." in response.text
    evidence = dashboard.get(
        "/dashboard/evidence/review", params={**query, "origin": "handoff-detail", "source_type": "content"}
    )
    assert evidence.status_code == 200
    assert "Review remains in progress." in evidence.text
    assert (
        dashboard.get(
            "/dashboard/evidence/missing", params={**query, "origin": "handoff-detail", "source_type": "content"}
        ).status_code
        == 404
    )


def test_scope_viewer_reads_without_server_observation_rights(tmp_path: Path) -> None:
    from powercontext.server.authentication import StaticBearerAuthenticationProvider
    from powercontext.server.authz import PrincipalRef
    from powercontext.server.settings import AccessControlConfig

    token = token_urlsafe(24)
    viewer_token = token_urlsafe(24)
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/access.db"),
        access=AccessControlConfig(mode="enforced"),
        auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
        mcp=McpConfig(enabled=False),
    )
    with TestClient(create_server_app(settings=settings, scheduler_path=tmp_path / "admin-scheduler.db")) as admin:
        admin.headers["Authorization"] = f"Bearer {token}"
        allowed = create_scope(admin, "Visible")
        denied = create_scope(admin, "Confidential")
        grant = admin.post(
            "/v1/access/bindings/create",
            json={
                "subject": {"type": "user", "id": "viewer"},
                "resource": {"type": "scope", "scope_id": allowed["scope_id"]},
                "role": "scope.viewer",
                "idempotency_key": "viewer-access",
            },
        )
        assert grant.status_code == 201
        record = commit_handoff(admin, denied["scope_id"])
        grant = admin.post(
            "/v1/access/bindings/create",
            json={
                "subject": {"type": "user", "id": "viewer"},
                "resource": {
                    "type": "artifact",
                    "scope_id": denied["scope_id"],
                    "identity": {"family": "handoff", "artifact_id": record["artifact_id"]},
                },
                "role": "handoff.viewer",
                "idempotency_key": "single-record-access",
            },
        )
        assert grant.status_code == 201
    app = create_server_app(
        settings=settings,
        authentication_provider=StaticBearerAuthenticationProvider(
            viewer_token, PrincipalRef(type="user", id="viewer")
        ),
        scheduler_path=tmp_path / "viewer-scheduler.db",
    )
    with TestClient(app) as viewer:
        viewer.headers["Authorization"] = f"Bearer {viewer_token}"
        assert viewer.get("/v1/capabilities").status_code == 403
        for page in ("home", "notes", "handoff", "methods", "usage"):
            response = viewer.get("/dashboard/" + page, params={"scope": allowed["scope_id"]})
            assert response.status_code == 200
            assert "Work on Visible" in response.text or "Visible" in response.text
            assert "Confidential" not in response.text
        assert viewer.get("/dashboard/home", params={"scope": denied["scope_id"]}).status_code in {403, 404}
        response = viewer.get(
            "/dashboard/handoff-detail",
            params={"scope": denied["scope_id"], "artifact": record["artifact_id"], "revision": record["revision"]},
        )
        assert response.status_code == 200
        assert "Continue the review." in response.text
        assert "Confidential" not in response.text
