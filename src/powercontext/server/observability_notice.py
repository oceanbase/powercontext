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

"""Point operators at the opt-in local observability stack before a foreground run."""

from __future__ import annotations

import shlex

import typer

_OPTIONS = (
    ("--env-file", "Server settings file", "keep the one used by this run"),
    ("--host", "listen address", "keep the bind used by this run"),
    ("--port", "listen port", "keep the port used by this run"),
    ("--observability", "telemetry profile", "adds only tracing, logging and metrics settings"),
)


def write_observability_notice(
    *,
    enabled: bool,
    server_env: str | None = None,
    host: str | None = None,
    port: int | None = None,
    role: str | None = None,
) -> None:
    """Print the two commands that enable local traces, metrics and logs.

    Nothing is started here: the notice only explains the opt-in flow, so a foreground
    Server run keeps working without Docker.
    """

    if enabled:
        return

    typer.secho("\nObservability is opt-in", bold=True, fg=typer.colors.CYAN, color=True)
    typer.echo("To collect this Server's telemetry in the local monitoring stack:")

    _step("1", "Start the local Docker Compose stack once; it prints the Grafana and Phoenix URLs.")
    command = ["powercontext", "observability", "up"]
    if server_env is not None:
        command.extend(["--server-env", server_env])
    client_host = host or "127.0.0.1"
    if client_host in {"0.0.0.0", "::"}:  # noqa: S104 - turn a wildcard bind into a client address.
        client_host = "127.0.0.1"
    if ":" in client_host:
        client_host = f"[{client_host}]"
    command.extend(["--server-url", f"http://{client_host}:{port or 8000}"])
    _command(command)

    _step("2", "Stop this Server, then restart it with the generated telemetry profile.")
    _command(_restart_command(server_env=server_env, host=host, port=port, role=role))

    typer.secho("\n  In that command:", fg=typer.colors.CYAN, color=True)
    options = list(_OPTIONS)
    if server_env is None:
        options[0] = ("--no-env-file", "skip env file load", "keep this run's environment-only configuration")
    if role is not None:
        options.append(("--role", "process role", "keep api, background or all from this run"))
    for option, meaning, note in options:
        typer.secho(f"    {option:<17}", fg=typer.colors.CYAN, bold=True, nl=False, color=True)
        typer.echo(f"{meaning:<20} {note}")

    typer.echo("\n  Verify delivery afterwards:")
    _command(["powercontext", "observability", "check", "--exercise"])
    typer.echo("  Details: https://powercontext.oceanbase.io/en/docs/operate/reference-observability-stack/")


def _step(number: str, text: str) -> None:
    typer.secho(f"\n  Step {number} - {text}", bold=True, fg=typer.colors.CYAN, color=True)


def _command(parts: list[str]) -> None:
    typer.secho(f"    {shlex.join(parts)}", fg=typer.colors.GREEN, bold=True, color=True)


def _restart_command(*, server_env: str | None, host: str | None, port: int | None, role: str | None) -> list[str]:
    command = ["powercontext", "server", "run"]
    command.extend(["--env-file", server_env] if server_env is not None else ["--no-env-file"])
    command.extend([
        "--host",
        host or "127.0.0.1",
        "--port",
        str(port or 8000),
    ])
    if role is not None:
        command.extend(["--role", role])
    command.append("--observability")
    return command


__all__ = ["write_observability_notice"]
