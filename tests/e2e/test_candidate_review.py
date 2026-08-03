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
    CaptureContentSourceRequest,
    ExperienceProposal,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    ListArtifactCandidatesRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    ReviseArtifactCandidateRequest,
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
    scope_id = f"candidate-review-{uuid4()}"

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            capabilities = await client.get_capabilities()
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
            exact_candidate = await client.get_artifact_candidate(
                GetArtifactCandidateRequest(scope_id=scope_id, candidate_id=candidate.candidate_id)
            )

            assert capabilities.artifact_families == ["memory", "experience", "handoff"]
            assert inbox.candidates == [candidate]
            assert prepared.status == "empty"
            assert revised.version == 2
            assert (stale.value.status_code, stale.value.code) == (409, "candidate_conflict")
            assert experience.content == revised.proposal
            assert experience.source_refs == [captured.source]
            assert exact_candidate == approved

    asyncio.run(scenario())


def test_candidate_cli_lists_shows_revises_approves_and_rejects(
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
            self._client = PowerContextClient("http://testserver", http_client=self._transport)
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

    monkeypatch.setattr(client_cli, "PowerContextClient", InProcessClient)
    cli = create_cli([client_cli.app])
    runner = CliRunner()
    with TestClient(app) as transport:
        captured = transport.post(
            "/v1/sources/content",
            json={"scope_id": "project", "source_id": "task-1", "content": "bounded evidence"},
        ).json()
        proposal = _proposal("Initial lesson.").model_dump(mode="json")
        first = transport.post(
            "/v1/experience/propose",
            json={
                "scope_id": "project",
                "proposal": proposal,
                "source_refs": [captured["source"]],
                "artifact_refs": [],
            },
        ).json()
        second = transport.post(
            "/v1/experience/propose",
            json={
                "scope_id": "project",
                "proposal": proposal,
                "source_refs": [captured["source"]],
                "artifact_refs": [],
            },
        ).json()
        revision_file = tmp_path / "revision.json"
        revision_file.write_text(
            ReviseArtifactCandidateRequest(
                scope_id="project",
                candidate_id=first["candidate_id"],
                expected_version=1,
                proposal=_proposal("Revised lesson."),
                source_refs=[captured["source"]],
                artifact_refs=[],
            ).model_dump_json(),
            encoding="utf-8",
        )

        listed = runner.invoke(cli, ["client", "candidate", "list", "--scope-id", "project"])
        shown = runner.invoke(
            cli,
            ["client", "candidate", "show", "--scope-id", "project", first["candidate_id"]],
        )
        revised = runner.invoke(cli, ["client", "candidate", "revise", str(revision_file)])
        approved = runner.invoke(
            cli,
            [
                "client",
                "candidate",
                "approve",
                "--scope-id",
                "project",
                "--expected-version",
                "2",
                first["candidate_id"],
            ],
        )
        rejected = runner.invoke(
            cli,
            [
                "client",
                "candidate",
                "reject",
                "--scope-id",
                "project",
                "--expected-version",
                "1",
                "--reason",
                "unsupported",
                second["candidate_id"],
            ],
        )

    assert all(result.exit_code == 0 for result in (listed, shown, revised, approved, rejected))
    assert first["candidate_id"] in listed.output
    assert first["candidate_id"] in shown.output
    assert '"version": 2' in revised.output
    assert '"status": "approved"' in approved.output
    assert '"status": "rejected"' in rejected.output
