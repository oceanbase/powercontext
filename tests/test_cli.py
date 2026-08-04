import json
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
from powercontext.builtin.runtime.cli import app as builtin_app
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
    HealthResponse,
    ImportExternalSkillRequest,
    ReadinessResponse,
    ReviseArtifactCandidateRequest,
    SkillArtifact,
    SkillGenerationOrigin,
    SkillProposal,
    SkillValidationItem,
)
from powercontext.server.cli import app as server_app


@pytest.mark.parametrize(
    "arguments",
    [
        ["-h"],
        ["--help"],
        ["client", "-h"],
        ["client", "--help"],
        ["client", "experience", "--help"],
        ["client", "skill", "--help"],
        ["client", "external-skill", "--help"],
    ],
)
def test_cli_help_exits_successfully(arguments: list[str]) -> None:
    cli = create_cli([client_cli.app, server_app])

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0


def test_skill_cli_exposes_the_target_based_export_command() -> None:
    cli = create_cli([client_cli.app])
    runner = CliRunner()

    skill_help = runner.invoke(cli, ["client", "skill", "--help"])
    export_help = runner.invoke(cli, ["client", "skill", "export", "--help"])

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
    assert all(command in result.output for command in ("builtin", "client", "server"))


def test_builtin_cli_reports_the_configured_instance_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "POWERCONTEXT_BUILTIN_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'builtin.db'}",
    )

    result = CliRunner().invoke(
        create_cli([builtin_app]),
        ["builtin", "capabilities", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "database": "sqlite",
        "memory_extraction": False,
        "experience_generation": False,
        "managed_skill_generation": False,
        "external_skill_registry": False,
        "handoff_generation": False,
        "memory_search_modes": ["auto", "fts"],
        "context_versions": ["powercontext.prepared-context.v1"],
    }


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

    result = CliRunner().invoke(create_cli([client_cli.app]), ["client", "ready"])

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

    result = CliRunner().invoke(create_cli([client_cli.app]), ["client", "live"])

    assert result.exit_code == 0
    assert result.output == "Status: ok\n"


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
    cli = create_cli([client_cli.app])

    experience_result = CliRunner().invoke(
        cli,
        [
            "client",
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
            "client",
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
    cli = create_cli([client_cli.app])

    experience_result = CliRunner().invoke(
        cli,
        [
            "client",
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
            "client",
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
            ["client", "experience", "generate", "--scope-id", "project", "--source-ref", "task-1"],
            "expected TYPE/ID",
        ),
        (
            [
                "client",
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
    result = CliRunner().invoke(create_cli([client_cli.app]), arguments)

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
        create_cli([client_cli.app]),
        [
            "client",
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
        create_cli([client_cli.app]),
        [
            "client",
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
