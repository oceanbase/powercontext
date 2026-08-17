from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import HandoffReportConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    BearerAuthConfig,
    DashboardConfig,
    DashboardScopeConfig,
    McpConfig,
    ServerSettings,
)

_AUTH_HEADERS = {"Authorization": "Bearer dashboard-secret"}


def test_dashboard_cannot_run_without_server_authentication() -> None:
    dashboard = DashboardConfig(
        enabled=True,
        scopes=[DashboardScopeConfig(scope_id="person:psiace", display_name="PsiACE")],
    )

    with pytest.raises(ValidationError, match="Dashboard requires Server bearer authentication"):
        ServerSettings(dashboard=dashboard)


def test_dashboard_is_the_authenticated_server_ui_entry(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            auth=BearerAuthConfig(
                enabled=True,
                token=SecretStr("dashboard-secret"),
            ),
            dashboard=DashboardConfig(
                enabled=True,
                scopes=[
                    DashboardScopeConfig(scope_id="person:psiace", display_name="PsiACE"),
                    DashboardScopeConfig(scope_id="project:powercontext", display_name="PowerContext"),
                ],
            ),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dashboard.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        home = client.get("/")
        removed_dashboard_alias = client.get("/dashboard", headers=_AUTH_HEADERS)
        missing_scopes = client.get("/dashboard/scopes")
        scopes = client.get("/dashboard/scopes", headers=_AUTH_HEADERS)

    assert home.status_code == 200
    assert removed_dashboard_alias.status_code == 404
    assert missing_scopes.status_code == 401
    assert scopes.status_code == 200
    assert 'data-server-session="missing"' in home.text
    assert 'id="auth-shell"' in home.text
    assert 'id="auth-shell" hidden' not in home.text
    assert 'id="page-status" hidden' in home.text
    assert 'class="server-content" id="dashboard"' in home.text
    assert 'id="dashboard" hidden' not in home.text
    assert "dashboard.js?v=state-races" in home.text
    assert scopes.json() == [
        {"scope_id": "person:psiace", "display_name": "PsiACE"},
        {"scope_id": "project:powercontext", "display_name": "PowerContext"},
    ]


def test_handoff_report_page_is_available_only_when_both_features_are_enabled(tmp_path) -> None:
    database_path = tmp_path / "handoff-dashboard.db"
    disabled_app = create_server_app(settings=_dashboard_settings(database_path, handoff_report_enabled=False))
    enabled_app = create_server_app(settings=_dashboard_settings(database_path, handoff_report_enabled=True))

    with TestClient(disabled_app) as client:
        disabled_page = client.get("/handoff-reports")
    with TestClient(enabled_app) as client:
        enabled_page = client.get("/handoff-reports")
        protected_projects = client.post(
            "/v1/handoff-reports/projects/list",
            json={"limit": 100, "include_archived": False},
        )

    assert disabled_page.status_code == 404
    assert enabled_page.status_code == 200
    assert 'data-server-session="missing"' in enabled_page.text
    assert 'id="auth-shell"' in enabled_page.text
    assert 'id="auth-shell" hidden' not in enabled_page.text
    assert 'id="page-status" hidden' in enabled_page.text
    assert 'class="server-content" id="handoff-report"' in enabled_page.text
    assert 'id="handoff-report" hidden' not in enabled_page.text
    assert 'data-period-mode="day"' in enabled_page.text
    assert 'data-period-mode="week"' in enabled_page.text
    assert 'data-period-mode="month"' in enabled_page.text
    assert 'id="period-start" type="date"' in enabled_page.text
    assert 'id="period-end" type="date"' in enabled_page.text
    assert 'id="project-select"' in enabled_page.text
    assert 'id="project-tabs"' not in enabled_page.text
    assert '<section class="report-overview"' in enabled_page.text
    assert '<dl class="report-overview"' not in enabled_page.text
    assert "handoff-report.js?v=state-races" in enabled_page.text
    assert protected_projects.status_code == 401


def _dashboard_settings(database_path: Path, *, handoff_report_enabled: bool) -> ServerSettings:
    return ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(
            enabled=True,
            scopes=[DashboardScopeConfig(scope_id="project:powercontext", display_name="PowerContext")],
        ),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
        mcp=McpConfig(enabled=False),
        handoff_report=HandoffReportConfig(enabled=handoff_report_enabled),
    )
