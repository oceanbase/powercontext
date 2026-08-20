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

from importlib.metadata import version
from pathlib import Path
from types import TracebackType
from typing import Self
from unittest.mock import Mock

import pytest
from click import unstyle
from pydantic import ValidationError
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.client import ServerResponseError
from powercontext.client.settings import ClientSettings
from powercontext.http import (
    ExperienceProposal,
    ExternalSkillImportMode,
    GeneratedCandidateResponse,
    GeneratedCandidateStatus,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetSkillRequest,
    GetStatsRequest,
    HealthResponse,
    ImportExternalSkillRequest,
    ReadinessResponse,
    ReviseArtifactCandidateRequest,
    ScopedStats,
    SkillArtifact,
    SkillGenerationOrigin,
    SkillProposal,
    SkillValidationItem,
)
from powercontext.server.cli import app as server_app


def _empty_inventory() -> dict[str, object]:
    return {
        "sources": {"total": 0, "memory_processed": 0, "memory_pending": 0},
        "artifacts": {"total": 0, "by_family": []},
        "candidates": {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "by_family": []},
        "memory": {"entries": {"total": 0, "active": 0, "inactive": 0, "by_kind": []}},
    }


def _stats_response() -> ScopedStats:
    return ScopedStats.model_validate({
        "scope_id": "project",
        "as_of": "2026-08-04T12:00:00Z",
        "inventory": _empty_inventory(),
        "usage": {
            "period": {
                "preset": "today",
                "start_date": "2026-08-04",
                "end_date": "2026-08-04",
                "timezone": "UTC",
            },
            "totals": {
                "generation": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                "embedding": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
            },
            "by_purpose": [],
            "daily": [
                {
                    "date": "2026-08-04",
                    "generation": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                    "embedding": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                    "by_purpose": [],
                }
            ],
        },
        "recall": {
            "period": {
                "preset": "today",
                "start_date": "2026-08-04",
                "end_date": "2026-08-04",
                "timezone": "UTC",
            },
            "estimator": {"estimator_id": "character:weighted", "version": "1"},
            "totals": {
                "preparations": 3,
                "ready_preparations": 2,
                "comparable_preparations": 1,
                "baseline_tokens": 100,
                "recalled_tokens": 40,
                "token_reduction": 60,
            },
            "daily": [
                {
                    "date": "2026-08-04",
                    "preparations": 3,
                    "ready_preparations": 2,
                    "comparable_preparations": 1,
                    "baseline_tokens": 100,
                    "recalled_tokens": 40,
                    "token_reduction": 60,
                }
            ],
        },
    })


@pytest.mark.parametrize(
    "arguments",
    [
        ["-h"],
        ["--help"],
        ["experience", "--help"],
        ["skill", "--help"],
        ["external-skill", "--help"],
    ],
)
def test_cli_help_exits_successfully(arguments: list[str]) -> None:
    cli = create_cli([server_app])

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0


def test_skill_cli_exposes_the_target_based_export_command() -> None:
    cli = create_cli([])
    runner = CliRunner()

    skill_help = runner.invoke(cli, ["skill", "--help"])
    export_help = runner.invoke(cli, ["skill", "export", "--help"])

    assert skill_help.exit_code == 0
    assert "export" in unstyle(skill_help.output)
    assert export_help.exit_code == 0
    export_help_text = unstyle(export_help.output)
    assert "--target" in export_help_text
    assert "codex" in export_help_text


def test_cli_version_reports_the_installed_distribution() -> None:
    installed_version = CliRunner().invoke(create_cli([]), ["--version"])

    assert installed_version.exit_code == 0
    assert installed_version.output == f"{version('powercontext')}\n"


def test_cli_exposes_installed_role_commands() -> None:
    result = CliRunner().invoke(create_cli(), ["--help"])

    assert result.exit_code == 0
    assert all(command in result.output for command in ("capabilities", "candidate", "stats", "server"))
    assert "builtin" not in result.output
    assert "client" not in result.output


def test_client_settings_load_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://memory.example/api/")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_TIMEOUT", "3.5")

    settings = ClientSettings()

    assert settings.server_url == "https://memory.example/api"
    assert settings.timeout == 3.5
    assert ClientSettings(server_url="https://override.example/").server_url == "https://override.example"


def test_client_settings_reject_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "not-a-url")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_TIMEOUT", "0")

    with pytest.raises(ValidationError):
        ClientSettings()


@pytest.mark.parametrize(
    ("environment", "arguments", "expected_host", "expected_port"),
    [
        (
            {"POWERCONTEXT_SERVER_HTTP_PORT": "8123"},
            ["--host", "192.0.2.1"],
            "192.0.2.1",
            8123,
        ),
        (
            {"POWERCONTEXT_SERVER_HTTP_HOST": "192.0.2.2"},
            ["--port", "8124"],
            "192.0.2.2",
            8124,
        ),
    ],
)
def test_server_command_layers_partial_cli_overrides_over_environment_settings(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    arguments: list[str],
    expected_host: str,
    expected_port: int,
) -> None:
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", *arguments],
    )

    assert result.exit_code == 0
    run_server.assert_called_once()
    assert run_server.call_args.kwargs["host"] == expected_host
    assert run_server.call_args.kwargs["port"] == expected_port
    tracing.shutdown.assert_called_once_with()


def test_server_command_does_not_load_client_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "not-a-url")
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_ENABLED", "false")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "true")
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)

    result = CliRunner().invoke(create_cli([server_app]), ["server", "run"])

    assert result.exit_code == 0
    assert "PowerContext Dashboard: http://127.0.0.1:8000/" in result.stdout


def test_cli_reports_server_errors_with_request_context_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        async def get_readiness(self) -> ReadinessResponse:
            raise ServerResponseError(status_code=503, request_id="request-123")

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: FailingClient())

    result = CliRunner().invoke(create_cli([]), ["ready"])

    assert result.exit_code == 1
    assert result.output == "PowerContext Server returned HTTP 503 (request ID: request-123)\n"


def test_client_command_prints_human_readable_output_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    class HealthyClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        async def get_liveness(self) -> HealthResponse:
            return HealthResponse(status="ok")

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: HealthyClient())

    result = CliRunner().invoke(create_cli([]), ["live"])

    assert result.exit_code == 0
    assert result.output == "Status: ok\n"


def test_stats_command_builds_request_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[GetStatsRequest] = []

    class StatsClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get_stats(self, request: GetStatsRequest) -> ScopedStats:
            received.append(request)
            return _stats_response()

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: StatsClient())

    result = CliRunner().invoke(
        create_cli([]),
        ["stats", "--scope-id", "project", "--period", "today"],
    )

    assert result.exit_code == 0
    assert received[0].model_dump(mode="json") == {"scope_id": "project", "period": "today"}
    assert "Sources: 0 total, 0 memory processed, 0 memory pending" in result.output
    assert "Generation: 0 requests, 0 input tokens, 0 output tokens" in result.output
    assert "Recall token estimator: character:weighted@1" in result.output
    assert (
        "Recall tokens: 3 preparations (2 ready, 1 comparable), 100 baseline, 40 recalled, 60 reduction"
        in result.output
    )


def test_client_generation_commands_build_requests_from_explicit_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[GenerateExperienceRequest | GenerateSkillRequest] = []

    class GeneratingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def generate_experience(self, request: GenerateExperienceRequest) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

        async def generate_skill(self, request: GenerateSkillRequest) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: GeneratingClient())
    cli = create_cli([])

    experience_result = CliRunner().invoke(
        cli,
        [
            "--json",
            "experience",
            "generate",
            "--scope-id",
            "project",
            "--source-ref",
            "content/task-1",
            "--source-ref",
            "content/task-2",
            "--target",
            "experience/exp-1@2",
            "--reason",
            "incorporate the latest result",
        ],
    )
    skill_result = CliRunner().invoke(
        cli,
        [
            "--json",
            "skill",
            "generate",
            "--scope-id",
            "project",
            "--origin",
            "experience",
            "--artifact-ref",
            "experience/exp-2@1",
        ],
    )

    assert experience_result.exit_code == 0
    assert skill_result.exit_code == 0
    assert [type(request) for request in received] == [GenerateExperienceRequest, GenerateSkillRequest]
    experience = received[0]
    assert isinstance(experience, GenerateExperienceRequest)
    assert [(reference.name, reference.source_id) for reference in experience.source_refs] == [
        ("content", "task-1"),
        ("content", "task-2"),
    ]
    assert [reference.model_dump() for reference in experience.artifact_refs] == [
        {"family": "experience", "artifact_id": "exp-1", "revision": 2}
    ]
    assert experience.target == experience.artifact_refs[0]
    assert experience.reason == "incorporate the latest result"
    skill = received[1]
    assert isinstance(skill, GenerateSkillRequest)
    assert skill.origin is SkillGenerationOrigin.EXPERIENCE
    assert [reference.model_dump() for reference in skill.artifact_refs] == [
        {"family": "experience", "artifact_id": "exp-2", "revision": 1}
    ]


def test_client_candidate_revision_commands_build_typed_proposals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[ReviseArtifactCandidateRequest] = []

    class RevisingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def revise_artifact_candidate(
            self,
            request: ReviseArtifactCandidateRequest,
        ) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: RevisingClient())
    instructions_file = tmp_path / "instructions.md"
    instructions_file.write_text("Run both backend acceptance scenarios.", encoding="utf-8")
    cli = create_cli([])

    experience_result = CliRunner().invoke(
        cli,
        [
            "candidate",
            "revise",
            "experience",
            "--scope-id",
            "project",
            "--expected-version",
            "1",
            "--situation",
            "Only one backend was tested.",
            "--action",
            "Run the same scenario on both backends.",
            "--outcome",
            "Both backends passed.",
            "--lesson",
            "Keep acceptance behavior backend-neutral.",
            "--source-ref",
            "content/task-1",
            "candidate-experience",
        ],
    )
    skill_result = CliRunner().invoke(
        cli,
        [
            "candidate",
            "revise",
            "skill",
            "--scope-id",
            "project",
            "--expected-version",
            "2",
            "--name",
            "backend-validation",
            "--description",
            "Validate storage backends consistently.",
            "--instructions-file",
            str(instructions_file),
            "--validation",
            "SQLite passes.",
            "--validation",
            "OceanBase passes.",
            "--target",
            "skill/backend-validation@1",
            "candidate-skill",
        ],
    )

    assert experience_result.exit_code == 0
    assert skill_result.exit_code == 0
    experience = received[0]
    assert isinstance(experience.proposal, ExperienceProposal)
    assert experience.proposal.lesson == "Keep acceptance behavior backend-neutral."
    skill = received[1]
    assert isinstance(skill.proposal, SkillProposal)
    assert skill.proposal.instructions == "Run both backend acceptance scenarios."
    assert [item.root for item in skill.proposal.validation] == ["SQLite passes.", "OceanBase passes."]
    assert skill.target == skill.artifact_refs[0]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["experience", "generate", "--scope-id", "project", "--source-ref", "task-1"],
            "expected TYPE/ID",
        ),
        (
            [
                "skill",
                "generate",
                "--scope-id",
                "project",
                "--origin",
                "source",
                "--artifact-ref",
                "experience/exp-1@1",
            ],
            "source origin requires only Source refs",
        ),
    ],
)
def test_client_generation_commands_reject_invalid_reference_options(
    arguments: list[str],
    message: str,
) -> None:
    result = CliRunner().invoke(create_cli([]), arguments)

    assert result.exit_code == 2
    assert message in result.output


def test_client_external_skill_import_preserves_exact_identity_and_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[ImportExternalSkillRequest] = []

    class ImportingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def import_external_skill(self, request: ImportExternalSkillRequest) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: ImportingClient())

    result = CliRunner().invoke(
        create_cli([]),
        [
            "external-skill",
            "import",
            "--scope-id",
            "project",
            "--fingerprint",
            "a" * 64,
            "--mode",
            "fork",
            "codex:project:repository/friendly-python",
        ],
    )

    assert result.exit_code == 0
    assert received[0].external_skill_id == "codex:project:repository/friendly-python"
    assert received[0].fingerprint == "a" * 64
    assert received[0].mode is ExternalSkillImportMode.FORK


def test_client_skill_export_uses_configured_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_tokens: list[str | None] = []

    class ExportingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get_skill(self, request: GetSkillRequest) -> SkillArtifact:
            return SkillArtifact(
                artifact=request.artifact,
                content=SkillProposal(
                    name="safe-skill",
                    description="Use for a bounded task.",
                    instructions="Perform the bounded task.",
                    validation=[SkillValidationItem("The expected result exists.")],
                ),
                source_refs=[],
                artifact_refs=[],
            )

    def client_factory(_server_url: str, *, token: str | None = None, **_kwargs: object) -> ExportingClient:
        received_tokens.append(token)
        return ExportingClient()

    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "secret-token")
    monkeypatch.setattr(client_cli, "PowerContextClient", client_factory)
    destination = tmp_path / "safe-skill"

    result = CliRunner().invoke(
        create_cli([]),
        [
            "skill",
            "export",
            "--target",
            "codex",
            "--scope-id",
            "project",
            "--revision",
            "1",
            "--destination",
            str(destination),
            "skill-123",
        ],
    )

    assert result.exit_code == 0
    assert received_tokens == ["secret-token"]
    assert "Exported skill-123@1 for codex" in result.output
    assert (destination / "SKILL.md").is_file()
