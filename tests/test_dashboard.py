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

import logging
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

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


def test_dashboard_is_enabled_by_default_without_authentication_or_scopes(tmp_path, monkeypatch) -> None:
    for name in (
        "POWERCONTEXT_SERVER_AUTH_ENABLED",
        "POWERCONTEXT_SERVER_AUTH_TOKEN",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED",
        "POWERCONTEXT_SERVER_DASHBOARD_SCOPES",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dashboard-default.db'}"),
        mcp=McpConfig(enabled=False),
    )
    app = create_server_app(settings=settings)

    with TestClient(app) as client:
        home = client.get("/")
        scopes = client.get("/dashboard/scopes")

    assert settings.dashboard.enabled is True
    assert settings.dashboard.scopes == []
    assert home.status_code == 200
    assert 'data-server-session="active"' in home.text
    assert 'data-server-auth-required="false"' in home.text
    assert scopes.status_code == 200
    assert scopes.json() == []


def test_dashboard_can_be_disabled_explicitly(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            dashboard=DashboardConfig(enabled=False),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dashboard-disabled.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        home = client.get("/")
        health = client.get("/health/live")

    assert home.status_code == 404
    assert health.status_code == 200


def test_dashboard_mount_failure_does_not_prevent_server_startup(tmp_path, monkeypatch, caplog) -> None:
    def fail_to_mount(*_args, **_kwargs) -> None:
        raise RuntimeError("static assets are unavailable")  # noqa: TRY003 - verifies direct failure reporting

    monkeypatch.setattr("powercontext.server.factory.mount_web_ui", fail_to_mount)
    caplog.set_level(logging.WARNING, logger="powercontext.server.factory")
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dashboard-fallback.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        health = client.get("/health/live")
        dashboard = client.get("/")

    assert health.status_code == 200
    assert dashboard.status_code == 404
    assert "PowerContext Dashboard failed to start: static assets are unavailable" in caplog.text


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
    assert 'data-server-auth-required="true"' in home.text
    assert 'data-i18n-aria-label="brandHomeLabel"' in home.text
    assert 'data-i18n-aria-label="primaryNavigation"' in home.text
    assert 'data-i18n-aria-label="scopeOverview"' in home.text
    assert 'data-i18n-aria-label="activityAria"' in home.text
    assert "dashboard.js?v=default-startup-locale-v1" in home.text
    assert scopes.json() == [
        {"scope_id": "person:psiace", "display_name": "PsiACE"},
        {"scope_id": "project:powercontext", "display_name": "PowerContext"},
    ]


def test_handoff_report_page_is_available_without_the_statistics_dashboard(tmp_path) -> None:
    database_path = tmp_path / "handoff-dashboard.db"
    disabled_app = create_server_app(settings=_handoff_report_settings(database_path, enabled=False))
    enabled_app = create_server_app(settings=_handoff_report_settings(database_path, enabled=True))

    with TestClient(disabled_app) as client:
        disabled_page = client.get("/handoff-reports")
    with TestClient(enabled_app) as client:
        enabled_page = client.get("/handoff-reports")
        disabled_dashboard = client.get("/")
        disabled_dashboard_scopes = client.get("/dashboard/scopes", headers=_AUTH_HEADERS)
        protected_projects = client.post(
            "/v1/handoff-reports/projects/list",
            json={"limit": 100, "include_archived": False},
        )

    assert disabled_page.status_code == 404
    assert enabled_page.status_code == 200
    assert disabled_dashboard.status_code == 404
    assert disabled_dashboard_scopes.status_code == 404
    assert 'data-i18n="dashboardTitle"' not in enabled_page.text
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
    assert 'id="handoff-content-list"' in enabled_page.text
    assert 'id="handoff-save-status"' in enabled_page.text
    assert 'id="handoff-editor-actions"' in enabled_page.text
    assert 'id="edit-handoff-content"' in enabled_page.text
    assert 'id="save-handoff-revision"' in enabled_page.text
    assert 'form="handoff-content-editor"' in enabled_page.text
    assert 'id="cancel-handoff-edit"' in enabled_page.text
    assert 'id="handoff-editor"' not in enabled_page.text
    assert "data-handoff-choice=" not in enabled_page.text
    assert 'id="receiver-live-state"' not in enabled_page.text
    assert 'id="receiver-capability"' not in enabled_page.text
    assert 'id="receiver-authorization"' not in enabled_page.text
    assert 'id="continuity-timeline"' in enabled_page.text
    assert 'id="continuity-timeline-toggle"' in enabled_page.text
    assert 'aria-controls="continuity-timeline"' in enabled_page.text
    assert 'data-i18n-aria-label="handoffSummary"' in enabled_page.text
    assert 'id="auto-refresh-status"' in enabled_page.text
    assert 'id="handoff-revision-history"' in enabled_page.text
    assert 'id="revision-history-summary"' in enabled_page.text
    assert 'id="transfer-state-status"' in enabled_page.text
    assert 'id="outcome-state-status"' in enabled_page.text
    assert 'id="task-outcome-form"' not in enabled_page.text
    assert 'id="project-select"' not in enabled_page.text
    assert 'id="project-search"' in enabled_page.text
    assert 'role="combobox"' in enabled_page.text
    assert 'aria-controls="project-options"' in enabled_page.text
    assert 'id="project-options" role="listbox"' in enabled_page.text
    assert 'id="project-search-status" role="status"' in enabled_page.text
    assert 'id="workstream-list"' in enabled_page.text
    assert 'id="workstream-switcher-toolbar"' in enabled_page.text
    assert 'id="workstream-search"' in enabled_page.text
    assert 'id="previous-workstream"' in enabled_page.text
    assert 'id="workstream-position"' in enabled_page.text
    assert 'id="next-workstream"' in enabled_page.text
    assert 'id="workstream-filter-empty"' in enabled_page.text
    assert 'id="handoff-snapshot"' in enabled_page.text
    assert 'id="open-handoff-workbench"' not in enabled_page.text
    assert 'id="handoff-workbench-panel"' not in enabled_page.text
    assert 'id="handoff-workstream"' not in enabled_page.text
    assert 'id="activity-title"' in enabled_page.text
    assert 'id="activity-breakdown-list"' in enabled_page.text
    assert '<details class="continuity-panel">' in enabled_page.text
    assert '<details class="report-metadata">' in enabled_page.text
    assert 'id="project-tabs"' not in enabled_page.text
    assert '<section class="report-overview"' in enabled_page.text
    assert '<dl class="report-overview"' not in enabled_page.text
    assert enabled_page.text.index('class="report-overview"') < enabled_page.text.index('id="blockers-section"')
    assert enabled_page.text.index('id="blockers-section"') < enabled_page.text.index(
        'class="data-section workstream-browser"'
    )
    assert enabled_page.text.index('class="data-section workstream-browser"') < enabled_page.text.index(
        'class="data-section activity-section"'
    )
    assert enabled_page.text.index('class="data-section activity-section"') < enabled_page.text.index(
        '<details class="report-metadata">'
    )
    assert "handoff-report.js?v=default-startup-unified-editor-v1" in enabled_page.text
    assert protected_projects.status_code == 401


def _handoff_report_settings(database_path: Path, *, enabled: bool) -> ServerSettings:
    return ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(enabled=False),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
        mcp=McpConfig(enabled=False),
        handoff_report=HandoffReportConfig(enabled=enabled),
    )
