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

from powercontext.builtin.artifacts.skill import AgentSkillTarget
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig, HandoffReportConfig
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
        skills = client.get("/skills")
        review = client.get("/reviews")
        scopes = client.get("/dashboard/scopes")

    assert settings.dashboard.enabled is True
    assert settings.dashboard.scopes == []
    assert home.status_code == 200
    assert skills.status_code == 200
    assert review.status_code == 200
    assert 'class="server-content" id="skills-library"' in skills.text
    assert 'class="server-content" id="review-inbox"' in review.text
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
        skills = client.get("/skills")
        review = client.get("/reviews")
        health = client.get("/health/live")

    assert home.status_code == 404
    assert skills.status_code == 404
    assert review.status_code == 404
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
        skills = client.get("/skills")
        review = client.get("/reviews")
        removed_dashboard_alias = client.get("/dashboard", headers=_AUTH_HEADERS)
        missing_scopes = client.get("/dashboard/scopes")
        scopes = client.get("/dashboard/scopes", headers=_AUTH_HEADERS)

    assert home.status_code == 200
    assert skills.status_code == 200
    assert review.status_code == 200
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
    assert 'data-i18n="skillsTitle"' in skills.text
    assert 'aria-current="page" data-i18n="skillsTitle"' in skills.text
    assert 'id="skills-scope-search"' in skills.text
    assert 'role="combobox"' in skills.text
    assert 'aria-controls="skills-scope-options"' in skills.text
    assert 'id="skills-scope-options" role="listbox"' in skills.text
    assert 'id="skills-search"' in skills.text
    assert 'id="skills-authority-filter"' in skills.text
    assert 'id="skills-list" role="listbox"' in skills.text
    assert 'id="skills-managed-content"' in skills.text
    assert 'id="skills-delivery"' in skills.text
    assert 'id="skills-create-revision"' in skills.text
    assert 'id="skills-publish-dialog"' in skills.text
    assert "skills.js?v=agent-targets-v1" in skills.text
    assert 'data-i18n="reviewTitle"' in review.text
    assert 'aria-current="page" data-i18n="reviewTitle"' in review.text
    assert 'id="review-scope-select"' not in review.text
    assert 'id="review-scope-search"' in review.text
    assert 'role="combobox"' in review.text
    assert 'aria-controls="review-scope-options"' in review.text
    assert 'id="review-scope-options" role="listbox"' in review.text
    assert 'id="review-family-filter"' in review.text
    assert 'id="review-status-filter"' in review.text
    assert 'id="review-list" role="listbox"' in review.text
    assert 'id="review-revision-form" hidden' in review.text
    assert 'id="review-approve-dialog"' in review.text
    assert 'id="review-reject-dialog"' in review.text
    assert 'id="review-publication"' in review.text
    assert 'id="review-create-skill-revision"' in review.text
    assert 'id="review-revision-title"' in review.text
    assert 'id="review-publish-dialog"' in review.text
    assert "review.js?v=agent-targets-v1" in review.text
    assert scopes.json() == [
        {"scope_id": "person:psiace", "display_name": "PsiACE"},
        {"scope_id": "project:powercontext", "display_name": "PowerContext"},
    ]


def test_review_publishes_an_approved_managed_skill_into_configured_agent_targets(tmp_path) -> None:
    codex_skill_root = tmp_path / "repository" / ".agents" / "skills"
    claude_skill_root = tmp_path / "repository" / ".claude" / "skills"
    settings = ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(
            enabled=True,
            scopes=[DashboardScopeConfig(scope_id="project:powercontext", display_name="PowerContext")],
        ),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'managed-skill-publish.db'}"),
        external_skills=ExternalSkillsConfig(
            host_id="dashboard-test",
            targets=(
                AgentSkillTarget(
                    target_id="codex-project",
                    agent_kind="codex",
                    installation_scope="project",
                    path=codex_skill_root,
                    allow_managed_publish=True,
                ),
                AgentSkillTarget(
                    target_id="claude-project",
                    agent_kind="claude_code",
                    installation_scope="project",
                    path=claude_skill_root,
                    allow_managed_publish=True,
                ),
            ),
        ),
        mcp=McpConfig(enabled=False),
    )
    app = create_server_app(settings=settings)

    with TestClient(app) as client:
        source = client.post(
            "/v1/sources/content",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": "project:powercontext",
                "source_id": "managed-skill-evidence",
                "content": "The contract workflow was reviewed and its validation passed.",
            },
        ).json()["source"]
        candidate = client.post(
            "/v1/skill/propose",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": "project:powercontext",
                "proposal": {
                    "name": "review-contract-change",
                    "description": "Use when changing the reviewed public contract.",
                    "instructions": "Regenerate the client and inspect the contract diff.",
                    "validation": ["Run the contract tests."],
                },
                "source_refs": [source],
                "artifact_refs": [],
            },
        ).json()
        approved = client.post(
            "/v1/artifact-candidates/approve",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": "project:powercontext",
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        ).json()
        selection = {
            "scope_id": "project:powercontext",
            "candidate_id": approved["candidate_id"],
            "artifact": approved["result_artifact"],
        }
        unauthenticated = client.post("/dashboard/skill-projections/status", json=selection)
        wrong_revision = client.post(
            "/dashboard/skill-projections/status",
            headers=_AUTH_HEADERS,
            json={
                **selection,
                "artifact": {**approved["result_artifact"], "revision": approved["result_artifact"]["revision"] + 1},
            },
        )
        before = client.post(
            "/dashboard/skill-projections/status",
            headers=_AUTH_HEADERS,
            json=selection,
        )
        published = client.post(
            "/dashboard/skill-projections/publish",
            headers=_AUTH_HEADERS,
            json={**selection, "target_id": "codex-project"},
        )
        claude_published = client.post(
            "/dashboard/skill-projections/publish",
            headers=_AUTH_HEADERS,
            json={**selection, "target_id": "claude-project"},
        )
        registered = client.post(
            "/v1/external-skills/list",
            headers=_AUTH_HEADERS,
            json={"scope_id": "project:powercontext", "include_unavailable": False},
        )
        revision_source = client.post(
            "/v1/sources/content",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": "project:powercontext",
                "source_id": "managed-skill-revision-evidence",
                "content": "The packaged contract must also be verified after regeneration.",
            },
        ).json()["source"]
        revision_candidate = client.post(
            "/v1/skill/propose",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": "project:powercontext",
                "proposal": {
                    "name": "review-contract-change",
                    "description": "Use when changing the reviewed public contract.",
                    "instructions": "Regenerate the client, inspect the diff, and verify the packaged contract.",
                    "validation": ["Run the contract tests."],
                },
                "source_refs": [revision_source],
                "artifact_refs": [approved["result_artifact"]],
                "target": approved["result_artifact"],
                "reason": "Add package verification to the reviewed contract workflow.",
            },
        ).json()
        revision_approved = client.post(
            "/v1/artifact-candidates/approve",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": "project:powercontext",
                "candidate_id": revision_candidate["candidate_id"],
                "expected_version": revision_candidate["version"],
            },
        ).json()
        revision_selection = {
            "scope_id": "project:powercontext",
            "candidate_id": revision_approved["candidate_id"],
            "artifact": revision_approved["result_artifact"],
        }
        update_available = client.post(
            "/dashboard/skill-projections/status",
            headers=_AUTH_HEADERS,
            json=revision_selection,
        )
        updated = client.post(
            "/dashboard/skill-projections/publish",
            headers=_AUTH_HEADERS,
            json={**revision_selection, "target_id": "codex-project"},
        )
        claude_updated = client.post(
            "/dashboard/skill-projections/publish",
            headers=_AUTH_HEADERS,
            json={**revision_selection, "target_id": "claude-project"},
        )

    codex_destination = codex_skill_root / "review-contract-change"
    claude_destination = claude_skill_root / "review-contract-change"
    assert unauthenticated.status_code == 401
    assert wrong_revision.status_code == 409
    assert wrong_revision.json()["error"]["code"] == "skill_projection_not_approved"
    assert before.status_code == 200
    assert before.json()["targets"][0]["state"] == "unpublished"
    assert [target["agent_kind"] for target in before.json()["targets"]] == ["codex", "claude_code"]
    assert published.status_code == 200
    assert published.json()["targets"][0]["state"] == "current"
    assert published.json()["targets"][0]["discovery"] == "available"
    assert claude_published.status_code == 200
    assert claude_published.json()["targets"][1]["state"] == "current"
    assert claude_published.json()["targets"][1]["discovery"] == "available"
    assert revision_approved["result_artifact"] == {
        **approved["result_artifact"],
        "revision": approved["result_artifact"]["revision"] + 1,
    }
    assert update_available.status_code == 200
    assert update_available.json()["targets"][0]["state"] == "update_available"
    assert updated.status_code == 200
    assert updated.json()["targets"][0]["state"] == "current"
    assert updated.json()["targets"][0]["published_revision"] == 2
    assert claude_updated.status_code == 200
    assert claude_updated.json()["targets"][1]["state"] == "current"
    assert claude_updated.json()["targets"][1]["published_revision"] == 2
    assert codex_destination.joinpath("SKILL.md").is_file()
    assert claude_destination.joinpath("SKILL.md").is_file()
    assert "verify the packaged contract" in codex_destination.joinpath("SKILL.md").read_text(encoding="utf-8")
    assert "verify the packaged contract" in claude_destination.joinpath("SKILL.md").read_text(encoding="utf-8")
    assert registered.status_code == 200
    assert {skill["registration"]["locator"] for skill in registered.json()["skills"]} == {
        str(codex_destination),
        str(claude_destination),
    }


def test_handoff_report_page_is_available_without_the_statistics_dashboard(tmp_path) -> None:
    database_path = tmp_path / "handoff-dashboard.db"
    disabled_app = create_server_app(settings=_handoff_report_settings(database_path, enabled=False))
    enabled_app = create_server_app(settings=_handoff_report_settings(database_path, enabled=True))

    with TestClient(disabled_app) as client:
        disabled_page = client.get("/handoff-reports")
    with TestClient(enabled_app) as client:
        enabled_page = client.get("/handoff-reports")
        disabled_skills = client.get("/skills")
        disabled_review = client.get("/reviews")
        disabled_dashboard = client.get("/")
        disabled_dashboard_scopes = client.get("/dashboard/scopes", headers=_AUTH_HEADERS)
        protected_projects = client.post(
            "/v1/handoff-reports/projects/list",
            json={"limit": 100, "include_archived": False},
        )

    assert disabled_page.status_code == 404
    assert enabled_page.status_code == 200
    assert disabled_skills.status_code == 404
    assert disabled_review.status_code == 404
    assert disabled_dashboard.status_code == 404
    assert disabled_dashboard_scopes.status_code == 404
    assert 'data-i18n="dashboardTitle"' not in enabled_page.text
    assert 'data-i18n="skillsTitle"' not in enabled_page.text
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
