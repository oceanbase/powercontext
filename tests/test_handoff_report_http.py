import sqlite3

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import HandoffReportConfig
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_handoff_report_catalog_and_json_projection_are_available_over_http(tmp_path) -> None:
    database_path = tmp_path / "report.db"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            handoff_report=HandoffReportConfig(enabled=True),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        created = client.post(
            "/v1/handoff-reports/projects/create",
            json={"project_key": "powercontext", "title": "PowerContext"},
        )
        assert created.status_code == 201
        project = created.json()
        fetched_project = client.post(
            "/v1/handoff-reports/projects/get",
            json={"project_id": project["project_id"]},
        )
        updated_project_payload = {**project, "title": "PowerContext Reports", "version": 2}
        updated_project = client.post(
            "/v1/handoff-reports/projects/update",
            json={"project": updated_project_payload, "expected_version": 1},
        )

        registered = client.post(
            "/v1/handoff-reports/workstreams/register",
            json={
                "project_id": project["project_id"],
                "scope_id": "scope-report",
                "title": "Handoff Report",
                "kind": "feature",
            },
        )
        assert registered.status_code == 201
        workstream = registered.json()
        updated_workstream = client.post(
            "/v1/handoff-reports/workstreams/update",
            json={
                "workstream": {**workstream, "title": "Handoff Report API", "version": 2},
                "expected_version": 1,
            },
        )

        recorded = client.post(
            "/v1/handoff-reports/activities/record",
            json={
                "project_id": project["project_id"],
                "scope_id": "scope-report",
                "source": "git_commit",
                "source_event_id": "git:abc123",
                "occurred_at": "2026-08-07T01:00:00Z",
                "time_basis": "source_reported",
                "title": "Implement report APIs",
            },
        )
        repeated = client.post(
            "/v1/handoff-reports/activities/record",
            json={
                "project_id": project["project_id"],
                "scope_id": "scope-report",
                "source": "git_commit",
                "source_event_id": "git:abc123",
                "occurred_at": "2026-08-07T01:00:00Z",
                "time_basis": "source_reported",
                "title": "Implement report APIs",
            },
        )
        previous_activity = client.post(
            "/v1/handoff-reports/activities/record",
            json={
                "project_id": project["project_id"],
                "scope_id": "scope-report",
                "source": "git_commit",
                "source_event_id": "git:previous",
                "occurred_at": "2026-08-05T01:00:00Z",
                "time_basis": "source_reported",
                "title": "Prepare report APIs",
            },
        )
        activities = client.post(
            "/v1/handoff-reports/activities/list",
            json={"project_id": project["project_id"]},
        )
        attached = client.post(
            "/v1/handoff-reports/workspace-bindings/attach",
            json={
                "workspace_instance_id": "workspace-1",
                "project_id": project["project_id"],
                "repository_ref": {
                    "provider": "github",
                    "repository_id": "oceanbase/powercontext",
                    "normalized_remote": "https://github.com/oceanbase/powercontext.git",
                    "subpath": ".",
                },
                "expected_version": None,
            },
        )
        binding = client.post(
            "/v1/handoff-reports/workspace-bindings/get",
            json={"workspace_instance_id": "workspace-1"},
        )

        listed = client.post("/v1/handoff-reports/projects/list", json={})
        report_response = client.post(
            "/v1/handoff-reports/get",
            json={
                "project_id": project["project_id"],
                "include_evidence_checks": False,
                "format": "json",
            },
        )
        periodic_response = client.post(
            "/v1/handoff-reports/get",
            json={
                "project_id": project["project_id"],
                "include_evidence_checks": False,
                "format": "json",
                "period": {
                    "start": "2026-08-06T00:00:00Z",
                    "end": "2026-08-08T00:00:00Z",
                    "timezone": "Asia/Shanghai",
                    "compare_to_previous_period": True,
                },
            },
        )
        markdown_response = client.post(
            "/v1/handoff-reports/get",
            json={
                "project_id": project["project_id"],
                "include_evidence_checks": False,
                "format": "markdown",
                "locale": "en",
            },
        )
        default_response = client.post(
            "/v1/handoff-reports/get",
            json={"project_id": project["project_id"], "include_evidence_checks": False},
        )
        downloaded = client.post(
            "/v1/handoff-reports/get",
            json={
                "project_id": project["project_id"],
                "include_evidence_checks": False,
                "format": "markdown",
                "download": True,
            },
        )
        detached = client.post(
            "/v1/handoff-reports/workspace-bindings/detach",
            json={"workspace_instance_id": "workspace-1", "expected_version": 1},
        )

    assert listed.status_code == 200
    assert fetched_project.json() == project
    assert updated_project.status_code == 200
    assert updated_project.json()["version"] == 2
    assert updated_workstream.status_code == 200
    assert updated_workstream.json()["version"] == 2
    assert listed.json()["items"][0]["project_id"] == project["project_id"]
    assert report_response.status_code == 200
    body = report_response.json()
    assert body["format"] == "json"
    assert body["report"]["project"]["project_id"] == project["project_id"]
    assert body["report"]["summary"]["no_handoff_count"] == 1
    assert body["report"]["coverage"]["activity_without_handoff_workstreams"] == 1
    assert body["selection_digest"].startswith("sha256:")
    assert body["report_digest"].startswith("sha256:")
    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-type"].startswith("text/markdown")
    assert "# PowerContext Project Handoff Report" in markdown_response.text
    assert default_response.status_code == 200
    assert default_response.headers["content-type"].startswith("text/markdown")
    assert "# PowerContext 项目交接报告" in default_response.text
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("text/markdown")
    assert downloaded.headers["content-disposition"] == 'attachment; filename="handoff-report.md"'
    assert downloaded.headers["x-powercontext-selection-digest"].startswith("sha256:")
    assert "# PowerContext 项目交接报告" in downloaded.text
    assert recorded.status_code == 201
    assert previous_activity.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json() == recorded.json()
    assert recorded.json()["cursor"] == 1
    assert activities.status_code == 200
    assert activities.json()["high_watermark"] == 2
    assert [item["source_event_id"] for item in activities.json()["items"]] == ["git:abc123", "git:previous"]
    assert periodic_response.status_code == 200
    periodic = periodic_response.json()["report"]
    assert periodic["report_kind"] == "periodic"
    assert periodic["normalized_period"]["timezone"] == "Asia/Shanghai"
    assert periodic["period_comparison"]["current_activity_count"] == 1
    assert periodic["period_comparison"]["previous_activity_count"] == 1
    assert periodic["period_comparison"]["activity_delta"] == 0
    assert periodic["period_comparison"]["handoff_boundary_coverage"] == "unavailable"
    assert attached.status_code == 200
    assert binding.status_code == 200
    assert binding.json() == attached.json()
    assert detached.status_code == 200
    assert detached.json()["state"] == "detached"
    assert detached.json()["version"] == 2
    with sqlite3.connect(database_path) as connection:
        report_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'pc_handoff_report_%'"
        ).fetchall()
    assert report_tables


def test_handoff_report_routes_are_not_registered_when_feature_is_disabled() -> None:
    response = TestClient(create_app()).post(
        "/v1/handoff-reports/projects/list",
        json={},
    )

    assert response.status_code == 404
    assert "/v1/handoff-reports/projects/list" not in create_app().openapi()["paths"]


def test_disabled_handoff_report_does_not_create_report_tables(tmp_path) -> None:
    database_path = tmp_path / "without-report.db"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            mcp=McpConfig(enabled=False),
            handoff_report=HandoffReportConfig(enabled=False),
        )
    )

    with TestClient(app):
        pass

    with sqlite3.connect(database_path) as connection:
        report_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'pc_handoff_report_%'"
        ).fetchall()
    assert report_tables == []
