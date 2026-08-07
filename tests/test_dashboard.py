from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from powercontext.builtin.persistence.sqlite import SQLiteConfig
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
    assert scopes.json() == [
        {"scope_id": "person:psiace", "display_name": "PsiACE"},
        {"scope_id": "project:powercontext", "display_name": "PowerContext"},
    ]
