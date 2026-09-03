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
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.cli.app import create_cli
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    CandidateFamily,
    CaptureContentSourceRequest,
    CreateScopeRequest,
    ExperienceProposal,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetSkillRequest,
    ListArtifactCandidatesRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    ReviseArtifactCandidateRequest,
    SkillProposal,
    SkillValidationItem,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

OCEANBASE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")


def _settings(database: Path) -> ServerSettings:
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        mcp=McpConfig(enabled=False),
    )


def _proposal(lesson: str) -> ExperienceProposal:
    return ExperienceProposal(
        situation="The public OpenAPI contract changes.",
        action="Regenerate the Client and run contract tests.",
        outcome="The generated transport matches the contract.",
        lesson=lesson,
    )


def _skill_proposal(
    instructions: str = "Regenerate the Client, inspect the diff, and run contract tests.",
) -> SkillProposal:
    return SkillProposal(
        name="powercontext-openapi-change",
        description="Use when changing PowerContext's public HTTP contract.",
        instructions=instructions,
        validation=[
            SkillValidationItem("make api-generate-check passes"),
            SkillValidationItem("make contract-test passes"),
        ],
    )


@pytest.mark.parametrize("database_kind", ["sqlite", "oceanbase"])
def test_http_sdk_experience_review_vertical_slice(database_kind: str, tmp_path: Path) -> None:
    if database_kind == "oceanbase":
        if OCEANBASE_URL is None:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL to a dedicated OceanBase MySQL-mode test database")
        settings = ServerSettings(
            database=OceanBaseConfig(url=SecretStr(OCEANBASE_URL)),
            mcp=McpConfig(enabled=False),
        )
    else:
        settings = _settings(tmp_path / "review.db")
    app = create_server_app(settings=settings)

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            capabilities = await client.get_capabilities()
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Candidate review",
                    summary="Isolated candidate review acceptance workflow.",
                    idempotency_key=f"candidate-review-{uuid4()}",
                )
            )
            scope_id = scope.scope_id
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="task-1",
                    content="api-generate and contract-test passed",
                )
            )
            candidate = await client.propose_experience(
                ProposeExperienceRequest(
                    scope_id=scope_id,
                    proposal=_proposal("Regenerate the Client before contract tests."),
                    source_refs=[captured.source],
                    artifact_refs=[],
                )
            )
            inbox = await client.list_artifact_candidates(ListArtifactCandidatesRequest(scope_id=scope_id))
            prepared = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope_id,
                    query="Regenerate the Client before contract tests.",
                )
            )
            revised = await client.revise_artifact_candidate(
                ReviseArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=candidate.candidate_id,
                    expected_version=1,
                    proposal=_proposal("Regenerate and inspect the Client before contract tests."),
                    source_refs=[captured.source],
                    artifact_refs=[],
                )
            )
            with pytest.raises(ServerResponseError) as stale:
                await client.approve_artifact_candidate(
                    ApproveArtifactCandidateRequest(
                        scope_id=scope_id,
                        candidate_id=candidate.candidate_id,
                        expected_version=1,
                    )
                )
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=candidate.candidate_id,
                    expected_version=2,
                )
            )
            assert approved.result_artifact is not None
            experience = await client.get_experience(
                GetExperienceRequest(scope_id=scope_id, artifact=approved.result_artifact)
            )
            approved_context = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope_id,
                    query="Regenerate and inspect the Client before contract tests.",
                )
            )
            exact_candidate = await client.get_artifact_candidate(
                GetArtifactCandidateRequest(scope_id=scope_id, candidate_id=candidate.candidate_id)
            )

            skill_candidate = await client.propose_skill(
                ProposeSkillRequest(
                    scope_id=scope_id,
                    proposal=_skill_proposal(),
                    source_refs=[],
                    artifact_refs=[experience.artifact],
                    reason="Incubated from the approved Experience.",
                )
            )
            skill_inbox = await client.list_artifact_candidates(
                ListArtifactCandidatesRequest(scope_id=scope_id, family=CandidateFamily.SKILL)
            )
            skill_approval = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=skill_candidate.candidate_id,
                    expected_version=1,
                )
            )
            assert skill_approval.result_artifact is not None
            first_skill = await client.get_skill(
                GetSkillRequest(scope_id=scope_id, artifact=skill_approval.result_artifact)
            )
            usage = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="task-2",
                    content="The managed Skill was used and validation passed.",
                )
            )
            replacement = await client.propose_skill(
                ProposeSkillRequest(
                    scope_id=scope_id,
                    proposal=_skill_proposal(
                        "Regenerate the Client, inspect the diff, run generation checks, and then run contract tests."
                    ),
                    source_refs=[usage.source],
                    artifact_refs=[first_skill.artifact],
                    target=first_skill.artifact,
                    reason="Usage evidence made the generation check explicit.",
                )
            )
            replacement_approval = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=replacement.candidate_id,
                    expected_version=1,
                )
            )
            assert replacement_approval.result_artifact is not None
            second_skill = await client.get_skill(
                GetSkillRequest(scope_id=scope_id, artifact=replacement_approval.result_artifact)
            )
            historical_skill = await client.get_skill(GetSkillRequest(scope_id=scope_id, artifact=first_skill.artifact))

            assert capabilities.artifact_families == ["memory", "experience", "skill", "handoff"]
            assert inbox.candidates == [candidate]
            assert prepared.status == "empty"
            assert revised.version == 2
            assert (stale.value.status_code, stale.value.code) == (409, "candidate_conflict")
            assert experience.content == revised.proposal
            assert experience.source_refs == [captured.source]
            assert approved_context.status == "ready"
            assert approved_context.content is not None
            assert '"kind":"experience"' in approved_context.content
            assert approved.result_artifact.artifact_id in approved_context.content
            assert exact_candidate == approved
            assert skill_inbox.candidates == [skill_candidate]
            assert first_skill.artifact.family == "skill"
            assert first_skill.artifact_refs == [experience.artifact]
            assert second_skill.artifact.revision == 2
            assert second_skill.source_refs == [usage.source]
            assert second_skill.artifact_refs == [first_skill.artifact]
            assert historical_skill == first_skill

    asyncio.run(scenario())


def test_candidate_cli_lists_shows_revises_approves_and_rejects(  # noqa: C901
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_server_app(settings=_settings(tmp_path / "cli-review.db"))

    class InProcessClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self._transport: httpx.AsyncClient | None = None
            self._client: PowerContextClient | None = None

        async def __aenter__(self):
            self._transport = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            self._client = PowerContextClient(
                "http://testserver", http_client=self._transport, trust_transport_security=True
            )
            return self

        async def __aexit__(self, *_args) -> None:
            assert self._transport is not None
            await self._transport.aclose()

        async def list_artifact_candidates(self, request):
            assert self._client is not None
            return await self._client.list_artifact_candidates(request)

        async def get_artifact_candidate(self, request):
            assert self._client is not None
            return await self._client.get_artifact_candidate(request)

        async def approve_artifact_candidate(self, request):
            assert self._client is not None
            return await self._client.approve_artifact_candidate(request)

        async def reject_artifact_candidate(self, request):
            assert self._client is not None
            return await self._client.reject_artifact_candidate(request)

        async def revise_artifact_candidate(self, request):
            assert self._client is not None
            return await self._client.revise_artifact_candidate(request)

        async def get_skill(self, request):
            assert self._client is not None
            return await self._client.get_skill(request)

        async def download_skill_package(self, request):
            assert self._client is not None
            return await self._client.download_skill_package(request)

    monkeypatch.setattr(client_cli, "PowerContextClient", InProcessClient)
    cli = create_cli([])
    runner = CliRunner()
    with TestClient(app) as transport:
        default_scope = transport.get("/v1/scopes/default")
        default_scope.raise_for_status()
        scope_id = default_scope.json()["scope_id"]
        captured = transport.post(
            "/v1/sources/content",
            json={"scope_id": scope_id, "source_id": "task-1", "content": "bounded evidence"},
        ).json()
        proposal = _proposal("Initial lesson.").model_dump(mode="json")
        first = transport.post(
            "/v1/experience/propose",
            json={
                "scope_id": scope_id,
                "proposal": proposal,
                "source_refs": [captured["source"]],
                "artifact_refs": [],
            },
        ).json()
        second = transport.post(
            "/v1/experience/propose",
            json={
                "scope_id": scope_id,
                "proposal": proposal,
                "source_refs": [captured["source"]],
                "artifact_refs": [],
            },
        ).json()
        listed = runner.invoke(cli, ["candidate", "list", "--scope-id", scope_id])
        shown = runner.invoke(
            cli,
            ["candidate", "show", "--scope-id", scope_id, first["candidate_id"]],
        )
        revised = runner.invoke(
            cli,
            [
                "candidate",
                "revise",
                "experience",
                "--scope-id",
                scope_id,
                "--expected-version",
                "1",
                "--situation",
                "The public OpenAPI contract changes.",
                "--action",
                "Regenerate the Client and run contract tests.",
                "--outcome",
                "The generated transport matches the contract.",
                "--lesson",
                "Revised lesson.",
                "--source-ref",
                "content/task-1",
                first["candidate_id"],
            ],
        )
        approved = runner.invoke(
            cli,
            [
                "candidate",
                "approve",
                "--scope-id",
                scope_id,
                "--expected-version",
                "2",
                first["candidate_id"],
            ],
        )
        rejected = runner.invoke(
            cli,
            [
                "candidate",
                "reject",
                "--scope-id",
                scope_id,
                "--expected-version",
                "1",
                "--reason",
                "unsupported",
                second["candidate_id"],
            ],
        )
        approved_head = transport.post(
            "/v1/artifact-candidates/get",
            json={"scope_id": scope_id, "candidate_id": first["candidate_id"]},
        ).json()
        experience_ref = approved_head["result_artifact"]
        skill_candidate = transport.post(
            "/v1/skill/propose",
            json={
                "scope_id": scope_id,
                "proposal": _skill_proposal().model_dump(mode="json"),
                "source_refs": [],
                "artifact_refs": [experience_ref],
            },
        ).json()
        skill_approved = transport.post(
            "/v1/artifact-candidates/approve",
            json={
                "scope_id": scope_id,
                "candidate_id": skill_candidate["candidate_id"],
                "expected_version": 1,
            },
        ).json()
        skill_ref = skill_approved["result_artifact"]
        projection = tmp_path / "repo" / ".agents" / "skills" / "powercontext-openapi-change"
        skill_shown = runner.invoke(
            cli,
            [
                "skill",
                "show",
                "--scope-id",
                scope_id,
                "--revision",
                str(skill_ref["revision"]),
                skill_ref["artifact_id"],
            ],
        )
        skill_projected = runner.invoke(
            cli,
            [
                "skill",
                "export",
                "--target",
                "codex",
                "--scope-id",
                scope_id,
                "--revision",
                str(skill_ref["revision"]),
                "--destination",
                str(projection),
                skill_ref["artifact_id"],
            ],
        )

    assert all(
        result.exit_code == 0
        for result in (
            listed,
            shown,
            revised,
            approved,
            rejected,
            skill_shown,
            skill_projected,
        )
    )
    assert first["candidate_id"] in listed.output
    assert first["candidate_id"] in shown.output
    assert '"version": 2' in revised.output
    assert '"status": "approved"' in approved.output
    assert '"status": "rejected"' in rejected.output
    assert skill_ref["artifact_id"] in skill_shown.output
    assert "Exported" in skill_projected.output
    assert (projection / "SKILL.md").is_file()
