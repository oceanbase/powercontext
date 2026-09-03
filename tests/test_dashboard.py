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

import asyncio
import logging
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.artifacts.skill import AgentSkillTarget, CodexSkillRoot, Skill, SkillContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig, HandoffReportConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    ServerSettings,
)
from powercontext.server.web import _skill_projection_response

_AUTH_HEADERS = {"Authorization": "Bearer dashboard-secret"}


def test_dashboard_is_enabled_by_default_without_authentication_or_scopes(tmp_path, monkeypatch) -> None:
    for name in (
        "POWERCONTEXT_SERVER_AUTH_ENABLED",
        "POWERCONTEXT_SERVER_AUTH_TOKEN",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED",
        "POWERCONTEXT_SERVER_PUBLIC_URL",
        "POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP",
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
    assert home.status_code == 200
    assert skills.status_code == 200
    assert review.status_code == 200
    assert scopes.status_code == 200
    assert scopes.json()[0]["display_name"] == "Default"
    assert scopes.json()[0]["summary"] == "Default context"
    assert scopes.json()[0]["parent_scope_id"] is None


def test_dashboard_exposes_explicit_insecure_http_enrollment_guidance(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            public_url="http://11.162.218.22:8765",
            allow_insecure_http=True,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dashboard-http.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        skills = client.get("/skills")

    assert skills.status_code == 200
    assert 'data-public-server-url="http://11.162.218.22:8765"' in skills.text
    assert 'data-allow-insecure-http="true"' in skills.text
    assert 'id="skills-insecure-http-warning"' in skills.text


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
            public_url="https://powercontext.example.com/base/",
            auth=BearerAuthConfig(
                enabled=True,
                token=SecretStr("dashboard-secret"),
            ),
            dashboard=DashboardConfig(enabled=True),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dashboard.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        first_scope = client.post(
            "/v1/scopes",
            headers=_AUTH_HEADERS,
            json={"title": "PsiACE", "summary": "Personal context", "idempotency_key": "psiace"},
        ).json()
        second_scope = client.post(
            "/v1/scopes",
            headers=_AUTH_HEADERS,
            json={"title": "PowerContext", "summary": "Repository context", "idempotency_key": "powercontext"},
        ).json()
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
    assert "dashboard.js?v=product-language-v4" in home.text
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
    assert "skills.js?v=remote-target-names-v1" in skills.text
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
    assert "review.js?v=standard-packages-v1" in review.text
    returned = {item["scope_id"]: item for item in scopes.json()}
    assert returned[first_scope["scope_id"]]["display_name"] == "PsiACE"
    assert returned[first_scope["scope_id"]]["summary"] == "Personal context"
    assert returned[second_scope["scope_id"]]["display_name"] == "PowerContext"


def test_publication_status_exposes_a_standard_package_blocker_for_a_legacy_skill() -> None:
    legacy_skill = Skill(
        artifact_id="legacy-release-check",
        revision=1,
        content=SkillContent(
            name="legacy-release-check",
            description="Verify a release created before standard packages were introduced.",
            instructions="Run the release verification.",
            validation=("The release report passes.",),
        ),
    )

    response = asyncio.run(
        _skill_projection_response(
            object(),
            "project:powercontext",
            legacy_skill,
            (
                AgentSkillTarget(
                    target_id="codex-project",
                    agent_kind="codex",
                    installation_scope="project",
                    path=Path(".agents/skills"),
                    allow_managed_publish=True,
                ),
            ),
        )
    )

    assert response.model_dump(mode="json") == {
        "artifact": legacy_skill.as_ref().model_dump(mode="json"),
        "name": "legacy-release-check",
        "blocker": "standard_package_required",
        "targets": [],
    }


def test_skill_library_exposes_external_takeover_machine_through_later_revisions(tmp_path) -> None:
    skill_root = tmp_path / "external" / ".agents" / "skills"
    package = skill_root / "review-origin"
    package.mkdir(parents=True)
    manifest = package / "SKILL.md"
    manifest.write_text(
        "---\nname: review-origin\ndescription: Preserve exact Skill origin.\n---\n\nCheck the persisted origin.\n",
        encoding="utf-8",
    )
    settings = ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(enabled=True),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'skill-origin.db'}"),
        external_skills=ExternalSkillsConfig(
            host_id="build-machine-07",
            codex_roots=(CodexSkillRoot(root_id="origin-test", installation_scope="project", path=skill_root),),
        ),
        mcp=McpConfig(enabled=False),
    )
    app = create_server_app(settings=settings)

    with TestClient(app) as client:
        scope_id = client.post(
            "/v1/scopes",
            headers=_AUTH_HEADERS,
            json={"title": "PowerContext", "summary": "Repository context", "idempotency_key": "powercontext"},
        ).json()["scope_id"]
        scanned = client.post(
            "/v1/external-skills/scan",
            headers=_AUTH_HEADERS,
            json={"scope_id": scope_id},
        ).json()
        registration = scanned["registrations"][0]
        candidate = client.post(
            "/v1/external-skills/import",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "external_skill_id": registration["external_skill_id"],
                "fingerprint": registration["fingerprint"],
                "mode": "import",
            },
        ).json()["candidate"]
        approved = client.post(
            "/v1/artifact-candidates/approve",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        ).json()
        revision_source = client.post(
            "/v1/sources/content",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "source_id": "origin-revision",
                "content": "Keep the original takeover evidence visible after a managed revision.",
            },
        ).json()["source"]
        revision_candidate = client.post(
            "/v1/skill/propose",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "proposal": candidate["proposal"],
                "source_refs": [revision_source],
                "artifact_refs": [approved["result_artifact"]],
                "target": approved["result_artifact"],
            },
        ).json()
        client.post(
            "/v1/artifact-candidates/approve",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "candidate_id": revision_candidate["candidate_id"],
                "expected_version": revision_candidate["version"],
            },
        ).raise_for_status()
        library = client.post(
            "/dashboard/skills/library",
            headers=_AUTH_HEADERS,
            json={"scope_id": scope_id, "include_deprecated": True},
        )

    assert library.status_code == 200
    [entry] = library.json()
    assert entry["artifact"]["revision"] == 2
    assert entry["origin"] == {
        "kind": "external_import",
        "registration": registration,
        "source": {
            "source_type": candidate["source_refs"][0]["name"],
            "source_id": candidate["source_refs"][0]["source_id"],
        },
    }


def test_review_publishes_an_approved_managed_skill_into_default_project_targets(tmp_path) -> None:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    codex_skill_root = workspace / ".agents" / "skills"
    claude_skill_root = workspace / ".claude" / "skills"
    settings = ServerSettings(
        workspace=workspace,
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(enabled=True),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'managed-skill-publish.db'}"),
        mcp=McpConfig(enabled=False),
    )
    app = create_server_app(settings=settings)

    with TestClient(app) as client:
        scope_id = client.post(
            "/v1/scopes",
            headers=_AUTH_HEADERS,
            json={"title": "PowerContext", "summary": "Repository context", "idempotency_key": "powercontext"},
        ).json()["scope_id"]
        source = client.post(
            "/v1/sources/content",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "source_id": "managed-skill-evidence",
                "content": "The contract workflow was reviewed and its validation passed.",
            },
        ).json()["source"]
        candidate = client.post(
            "/v1/skill/propose",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
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
                "scope_id": scope_id,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        ).json()
        selection = {
            "scope_id": scope_id,
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
            json={"scope_id": scope_id, "include_unavailable": False},
        )
        revision_source = client.post(
            "/v1/sources/content",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "source_id": "managed-skill-revision-evidence",
                "content": "The packaged contract must also be verified after regeneration.",
            },
        ).json()["source"]
        revision_candidate = client.post(
            "/v1/skill/propose",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
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
                "scope_id": scope_id,
                "candidate_id": revision_candidate["candidate_id"],
                "expected_version": revision_candidate["version"],
            },
        ).json()
        library = client.post(
            "/dashboard/skills/library",
            headers=_AUTH_HEADERS,
            json={"scope_id": scope_id, "include_deprecated": True},
        )
        revision_selection = {
            "scope_id": scope_id,
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
        unpublished = client.post(
            "/dashboard/skill-projections/unpublish",
            headers=_AUTH_HEADERS,
            json={**revision_selection, "target_id": "codex-project"},
        )
        claude_skill = claude_skill_root / "review-contract-change" / "SKILL.md"
        claude_skill.write_text(claude_skill.read_text(encoding="utf-8") + "\nLocal edit.\n", encoding="utf-8")
        drifted_unpublish = client.post(
            "/dashboard/skill-projections/unpublish",
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
    assert library.status_code == 200
    assert library.json()[0]["origin"] == {"kind": "powercontext", "registration": None, "source": None}
    assert update_available.status_code == 200
    assert update_available.json()["targets"][0]["state"] == "update_available"
    assert updated.status_code == 200
    assert updated.json()["targets"][0]["state"] == "current"
    assert updated.json()["targets"][0]["published_revision"] == 2
    assert claude_updated.status_code == 200
    assert claude_updated.json()["targets"][1]["state"] == "current"
    assert claude_updated.json()["targets"][1]["published_revision"] == 2
    assert unpublished.status_code == 200
    assert unpublished.json()["targets"][0]["state"] == "unpublished"
    assert not codex_destination.exists()
    assert drifted_unpublish.status_code == 409
    assert drifted_unpublish.json()["error"]["details"]["state"] == "drifted"
    assert claude_destination.joinpath("SKILL.md").is_file()
    assert "verify the packaged contract" in claude_destination.joinpath("SKILL.md").read_text(encoding="utf-8")
    assert {path.name for path in claude_destination.iterdir()} == {"SKILL.md"}
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
        dashboard=DashboardConfig(enabled=True),
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
        scope_id = client.get("/v1/scopes/default", headers=_AUTH_HEADERS).json()["scope_id"]
        source = client.post(
            "/v1/sources/content",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "source_id": "managed-skill-evidence",
                "content": "The contract workflow was reviewed and its validation passed.",
            },
        ).json()["source"]
        candidate = client.post(
            "/v1/skill/propose",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
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
                "scope_id": scope_id,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        ).json()
        selection = {
            "scope_id": scope_id,
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
        dashboard=DashboardConfig(enabled=True),
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
        scope_id = client.get("/v1/scopes/default", headers=_AUTH_HEADERS).json()["scope_id"]
        source = client.post(
            "/v1/sources/content",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
                "source_id": "managed-skill-evidence",
                "content": "The contract workflow was reviewed and its validation passed.",
            },
        ).json()["source"]
        candidate = client.post(
            "/v1/skill/propose",
            headers=_AUTH_HEADERS,
            json={
                "scope_id": scope_id,
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
                "scope_id": scope_id,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        ).json()
        selection = {
            "scope_id": scope_id,
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
        scopes = client.get("/dashboard/scopes", headers=_AUTH_HEADERS)

    assert disabled_page.status_code == 404
    assert enabled_page.status_code == 200
    assert scopes.status_code == 200
    assert scopes.json()[0]["display_name"] == "Default"
    assert 'class="server-content" id="handoff-report"' in enabled_page.text
    assert 'id="scope-select"' in enabled_page.text
    assert 'id="scope-report-rows"' in enabled_page.text
    assert 'id="download-report"' in enabled_page.text
    assert 'data-i18n-aria-label="handoffSummary"' in enabled_page.text
    assert '<details class="report-metadata">' in enabled_page.text
    assert "handoff-report.js?v=scope-selection-v2" in enabled_page.text


def _handoff_report_settings(database_path: Path, *, enabled: bool) -> ServerSettings:
    return ServerSettings(
        auth=BearerAuthConfig(enabled=True, token=SecretStr("dashboard-secret")),
        dashboard=DashboardConfig(enabled=False),
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
        mcp=McpConfig(enabled=False),
        handoff_report=HandoffReportConfig(enabled=enabled),
    )
