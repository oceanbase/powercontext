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


class _ScanFailingScopedExternalSkills:
    """Delegate everything to the real scoped application except scan."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def scan(self):
        raise OSError

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _ScanFailingExternalSkills:
    def __init__(self, inner) -> None:
        self._inner = inner

    def for_scope(self, scope_id: str):
        return _ScanFailingScopedExternalSkills(self._inner.for_scope(scope_id))


def test_publish_reports_success_when_post_publish_scan_fails(tmp_path, caplog) -> None:
    codex_skill_root = tmp_path / "repository" / ".agents" / "skills"
    settings = ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(
            enabled=True,
            scopes=[DashboardScopeConfig(scope_id="project:powercontext", display_name="PowerContext")],
        ),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'publish-scan-failure.db'}"),
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
            ),
        ),
        mcp=McpConfig(enabled=False),
    )
    app = create_server_app(settings=settings)

    with TestClient(app) as client, caplog.at_level(logging.WARNING):
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
        application = app.state.application
        application.external_skills = _ScanFailingExternalSkills(application.external_skills)
        published = client.post(
            "/dashboard/skill-projections/publish",
            headers=_AUTH_HEADERS,
            json={**selection, "target_id": "codex-project"},
        )

    assert published.status_code == 200
    assert published.json()["targets"][0]["state"] == "current"
    assert codex_skill_root.joinpath("review-contract-change").joinpath("SKILL.md").is_file()
    scan_failures = [
        record for record in caplog.records if record.levelno == logging.WARNING and "scan failed" in record.message
    ]
    assert len(scan_failures) == 1


class _RegistryUnavailableScopedExternalSkills:
    """Delegate everything to the real scoped application except registry reads and writes."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def scan(self):
        raise OSError

    async def list(self, *args, **kwargs):
        raise OSError

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _RegistryUnavailableExternalSkills:
    def __init__(self, inner) -> None:
        self._inner = inner

    def for_scope(self, scope_id: str):
        return _RegistryUnavailableScopedExternalSkills(self._inner.for_scope(scope_id))


def test_publish_reports_stale_discovery_when_registry_database_is_unavailable(tmp_path, caplog) -> None:
    codex_skill_root = tmp_path / "repository" / ".agents" / "skills"
    settings = ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(
            enabled=True,
            scopes=[DashboardScopeConfig(scope_id="project:powercontext", display_name="PowerContext")],
        ),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'publish-registry-down.db'}"),
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
            ),
        ),
        mcp=McpConfig(enabled=False),
    )
    app = create_server_app(settings=settings)

    with TestClient(app) as client, caplog.at_level(logging.WARNING):
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
        application = app.state.application
        application.external_skills = _RegistryUnavailableExternalSkills(application.external_skills)
        published = client.post(
            "/dashboard/skill-projections/publish",
            headers=_AUTH_HEADERS,
            json={**selection, "target_id": "codex-project"},
        )

    assert published.status_code == 200
    assert published.json()["targets"][0]["state"] == "current"
    assert published.json()["targets"][0]["discovery"] == "unavailable"
    assert codex_skill_root.joinpath("review-contract-change").joinpath("SKILL.md").is_file()
    warnings = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert sum("scan failed" in message for message in warnings) == 1
    assert sum("discovery failed" in message for message in warnings) == 1


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
        protected_scopes = client.post(
            "/v1/handoff-reports/scopes/list-known",
            json={"limit": 100},
        )

    assert disabled_page.status_code == 404
    assert enabled_page.status_code == 200
    assert disabled_skills.status_code == 404
    assert disabled_review.status_code == 404
    assert disabled_dashboard.status_code == 404
    assert disabled_dashboard_scopes.status_code == 404
    assert protected_scopes.status_code == 401


def _handoff_report_settings(database_path: Path, *, enabled: bool) -> ServerSettings:
    return ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(enabled=False),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
        mcp=McpConfig(enabled=False),
        handoff_report=HandoffReportConfig(enabled=enabled),
    )
