import json
from importlib.metadata import version
from pathlib import Path
from types import TracebackType
from typing import Self
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.builtin.runtime.cli import app as builtin_app
from powercontext.cli.app import create_cli
from powercontext.client import ServerResponseError
from powercontext.client.settings import ClientSettings
from powercontext.http import HealthResponse, ReadinessResponse
from powercontext.server.cli import app as server_app


@pytest.mark.parametrize(
    "arguments",
    [
        ["-h"],
        ["--help"],
        ["client", "-h"],
        ["client", "--help"],
    ],
)
def test_cli_help_exits_successfully(arguments: list[str]) -> None:
    cli = create_cli([client_cli.app, server_app])

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0


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
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
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
