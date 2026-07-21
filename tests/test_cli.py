from importlib.metadata import EntryPoint, version
from unittest.mock import Mock

import httpx
import pytest
import typer
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.client.client import PowerContextClient
from powercontext.server.cli import app as server_app


def test_cli_discovers_declared_component_entry_points() -> None:
    cli = create_cli()

    assert {"client", "server"} <= {group.name for group in cli.registered_groups}
    assert cli.registered_commands == []


def test_cli_accepts_a_component_without_knowing_its_commands() -> None:
    runtime_app = typer.Typer(name="runtime")

    @runtime_app.command()
    def inspect() -> None:
        typer.echo("runtime")

    cli = create_cli([runtime_app])

    result = CliRunner().invoke(cli, ["runtime", "inspect"])

    assert result.exit_code == 0
    assert result.output == "runtime\n"


def test_cli_rejects_duplicate_provider_names() -> None:
    commands = [
        typer.Typer(name="runtime"),
        typer.Typer(name="runtime"),
    ]

    with pytest.raises(ValueError, match="runtime"):
        create_cli(commands)


def test_cli_ignores_components_with_missing_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = EntryPoint(
        name="client",
        value="powercontext.missing.cli:app",
        group="powercontext.cli",
    )

    def find_entry_points(*, group: str) -> list[EntryPoint]:
        return [unavailable]

    monkeypatch.setattr("powercontext.cli.app.entry_points", find_entry_points)

    cli = create_cli()

    assert cli.registered_groups == []


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


def test_server_command_applies_cli_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    run_server = Mock()
    monkeypatch.setattr("powercontext.server.cli.uvicorn.run", run_server)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", "--host", "192.0.2.1", "--port", "9000"],
    )

    assert result.exit_code == 0
    run_server.assert_called_once()
    assert run_server.call_args.kwargs["host"] == "192.0.2.1"
    assert run_server.call_args.kwargs["port"] == 9000


def test_cli_reports_server_errors_with_request_context_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(503, headers={"X-Request-ID": "request-123"})
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        def create_client(base_url: str, *, timeout: float) -> PowerContextClient:
            return client

        monkeypatch.setattr(client_cli, "PowerContextClient", create_client)

        result = CliRunner().invoke(create_cli([client_cli.app]), ["client", "ready"])

    assert result.exit_code == 1
    assert result.output == "PowerContext Server returned HTTP 503 (request ID: request-123)\n"


def test_client_command_prints_human_readable_output_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(200, json={"status": "ok"})
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        def create_client(base_url: str, *, timeout: float) -> PowerContextClient:
            return client

        monkeypatch.setattr(client_cli, "PowerContextClient", create_client)

        result = CliRunner().invoke(create_cli([client_cli.app]), ["client", "live"])

    assert result.exit_code == 0
    assert result.output == "Status: ok\n"
