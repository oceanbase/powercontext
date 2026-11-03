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

"""Lifecycle commands for an optional observability stack."""

from __future__ import annotations

import json
import secrets
import shlex
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
import typer

from powercontext.observability.connection import create_connection, load_connection
from powercontext.observability.connection_checks import exercise_connection, inspect_connection
from powercontext.observability.deployment import (
    BACKEND,
    ObservabilityError,
    _private_write,
    backend_client,
    compose,
    deployment_lock,
    docker_command,
    initialize,
    load_state,
    state_directory,
    validate_backend,
    wait_for_backends,
)
from powercontext.observability.phoenix import phoenix_port, wait_for_phoenix

app = typer.Typer(
    name="observability",
    help="Manage optional telemetry with Docker Compose or connect existing components.",
    no_args_is_help=True,
)
Backend = Annotated[str | None, typer.Option(help="Deployment backend; only docker-compose is currently supported.")]


def _failure(error: Exception) -> None:
    # HTTP errors can include URLs; provider bodies/configuration values are never displayed.
    message = (
        str(error)
        if isinstance(error, ObservabilityError)
        else "Operation failed; check Docker and local configuration permissions"
    )
    if isinstance(error, ImportError):
        message = "Server configuration dependencies are missing; install powercontext[cli,server,tracing-otlp]"
    if isinstance(error, httpx.HTTPError):
        message = (
            f"HTTP query failed ({type(error).__name__}); check backend readiness, Server address and network access"
        )
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


@app.command()
def up(
    backend: Backend = None,
    server_url: Annotated[
        str | None, typer.Option(help="Existing Server origin, default http://127.0.0.1:8000.")
    ] = None,
    server_env: Annotated[
        Path | None, typer.Option(help="Read existing Server bind/token settings without changing the file.")
    ] = None,
    metrics_target: Annotated[
        str | None, typer.Option(help="host:port reachable from the Prometheus container.")
    ] = None,
    background_target: Annotated[
        str | None, typer.Option(help="Optional standalone background metrics host:port.")
    ] = None,
    server_mode: Annotated[
        str | None, typer.Option(help="Existing Server deployment: local (default) or docker.")
    ] = None,
    server_uid: Annotated[int | None, typer.Option(min=0, help="Docker Server log owner UID, default 10001.")] = None,
    server_gid: Annotated[int | None, typer.Option(min=0, help="Docker Server log owner GID, default 10001.")] = None,
    grafana_port: Annotated[int | None, typer.Option(min=1, max=65535)] = None,
    gateway_port: Annotated[int | None, typer.Option(min=1, max=65535)] = None,
    phoenix: Annotated[
        int | None,
        typer.Option("--phoenix-port", "--phoenix", min=1, max=65535, help="Local Phoenix UI port; default 6006."),
    ] = None,
) -> None:
    """Create or restart a deployment; never restart or reconfigure a business Server."""
    try:
        validate_backend(backend)
        directory = state_directory()
        _require_managed_mode(directory)
        with deployment_lock(directory):
            # Check the engine/Compose before generating persistent configuration.
            docker = docker_command()
            for arguments in (["info", "--format", "{{.ServerVersion}}"], ["compose", "version"]):
                result = subprocess.run([docker, *arguments], capture_output=True, timeout=15, check=False)  # noqa: S603
                if result.returncode:
                    raise ObservabilityError("Docker engine or Compose is unavailable; start Docker and retry")  # noqa: TRY003, TRY301
            if (directory / "state.json").exists():
                state = load_state(directory)
                if any(
                    value is not None
                    for value in (
                        server_url,
                        server_env,
                        metrics_target,
                        background_target,
                        grafana_port,
                        gateway_port,
                        phoenix,
                        server_mode,
                        server_uid,
                        server_gid,
                    )
                ):
                    raise ObservabilityError(  # noqa: TRY003, TRY301
                        "Deployment already exists; up reuses its saved settings. Do not pass initialization options again"
                    )
            else:
                _require_unmanaged_files_absent(directory)
                effective_url, token = _server_connection(server_env, server_url)
                state = initialize(
                    directory,
                    server_url=effective_url,
                    token=token,
                    grafana_port=grafana_port or 13000,
                    gateway_port=gateway_port or 14318,
                    phoenix_port=phoenix or 6006,
                    target=metrics_target,
                    background_target=background_target,
                    server_mode=server_mode or "local",
                    server_uid=10001 if server_uid is None else server_uid,
                    server_gid=10001 if server_gid is None else server_gid,
                )
            if server_env is not None:
                state["server_env"] = str(server_env.expanduser().resolve())
                _private_write(directory / "state.json", json.dumps(state, indent=2) + "\n")
            _progress("Starting Docker Compose components (image downloads may take several minutes)...")
            compose(directory, state, "up", "-d", timeout=600)
            _progress("Waiting for monitoring backends to accept queries (up to 60 seconds)...")
            wait_for_backends(directory, state)
        _heading("Observability stack is running")
        _row("Grafana", f"http://127.0.0.1:{state['grafana_port']}", "open in a browser")
        _row("Loki logs", "Explore", "URL with prefilled query and log fields:")
        _detail(explore_logs_url(state))
        _row("Tempo traces", "Explore", "URL with the Search tab selected by default:")
        _detail(explore_traces_url(state))
        _row("Phoenix", f"http://127.0.0.1:{phoenix_port(state)}", "traces; may still be starting")
        _row("User", "admin", "password in:")
        _detail(directory / ".local" / "grafana-password")
        _row("Config", BACKEND, "deployment files in:")
        _detail(directory)
        _server_instructions(directory, state)
    except (ObservabilityError, OSError, ValueError, subprocess.TimeoutExpired, httpx.HTTPError, ImportError) as error:
        _failure(error)


def explore_logs_url(state: dict[str, Any]) -> str:
    """Explore deep link; Grafana provisioning cannot set Explore default queries or fields."""
    pane = {
        "datasource": "loki",
        "queries": [
            {
                "refId": "A",
                "expr": '{service_name=~".+"}',
                "queryType": "range",
                "datasource": {"type": "loki", "uid": "loki"},
                "editorMode": "code",
                "direction": "backward",
            }
        ],
        "range": {"from": "now-1h", "to": "now"},
        "panelsState": {
            "logs": {
                "visualisationType": "logs",
                "displayedFields": ["powercontext_service_role", "trace_id", "___LOG_LINE_BODY___"],
            }
        },
        "compact": False,
    }
    panes = {"l0g": pane}
    query = urlencode({"schemaVersion": "1", "panes": json.dumps(panes, separators=(",", ":")), "orgId": "1"})
    return f"http://127.0.0.1:{state['grafana_port']}/explore?{query}"


def explore_traces_url(state: dict[str, Any]) -> str:
    """TraceQL deep link; search-tab filters lose string quoting when rebuilt from the URL and Tempo rejects them."""
    pane = {
        "datasource": "tempo",
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "tempo", "uid": "tempo"},
                "queryType": "traceql",
                "limit": 20,
                "query": '{resource.service.name="powercontext-server"}',
            }
        ],
        "range": {"from": "now-1h", "to": "now"},
        "compact": False,
    }
    panes = {"trc": pane}
    query = urlencode({"schemaVersion": "1", "panes": json.dumps(panes, separators=(",", ":")), "orgId": "1"})
    return f"http://127.0.0.1:{state['grafana_port']}/explore?{query}"


def _server_instructions(directory: Path, state: dict[str, Any]) -> None:
    docker = state.get("server_mode") == "docker"
    _heading("Next: connect your Server")
    _step("1", "Stop the old Server, then restart it with the generated telemetry profile:")
    _detail(directory / ("container.env" if docker else "server.env"))
    _command(_server_command(directory, state), env={"POWERCONTEXT_OBSERVABILITY_HOME": str(directory)})
    typer.secho(
        "     Keep your existing business options and --env-file; "
        + (
            "the override only adds container.env and the log volume."
            if docker
            else "the Server environment needs powercontext[cli,server,tracing-otlp]."
        ),
        fg=typer.colors.WHITE,
        dim=True,
        color=True,
    )
    _step("2", "Verify one new request after the Server comes back up.")
    _command(
        ["powercontext", "observability", "check", "--exercise"],
        env={"POWERCONTEXT_OBSERVABILITY_HOME": str(directory)},
    )


def _server_command(directory: Path, state: dict[str, Any]) -> list[str]:
    if state.get("server_mode") == "docker":
        return [
            "docker",
            "compose",
            "-f",
            "/path/to/your-compose.yaml",
            "-f",
            str(directory / "server.compose.yaml"),
            "up",
            "-d",
            "--no-deps",
            "powercontext",
        ]
    command = ["powercontext", "server", "run"]
    if state.get("server_env"):
        command.extend(["--env-file", state["server_env"]])
    command.append("--observability")
    return command


def _heading(text: str) -> None:
    typer.secho(f"\n{text}", bold=True, fg=typer.colors.CYAN, color=True)


def _step(number: str, text: str) -> None:
    typer.secho(f"\n  {number}. {text}", bold=True, fg=typer.colors.CYAN, color=True)


def _row(label: str, value: str, note: str) -> None:
    typer.secho(f"  {label:<9}", fg=typer.colors.CYAN, bold=True, nl=False, color=True)
    typer.secho(f"{value:<33}", fg=typer.colors.GREEN, nl=False, color=True)
    typer.secho(note, dim=True, color=True)


def _status_row(label: str, value: str) -> None:
    """One colored entry per line; long Explore URLs must not wrap into the label column."""
    typer.secho(f"  {label:<13}", fg=typer.colors.CYAN, bold=True, nl=False, color=True)
    typer.secho(value, fg=typer.colors.GREEN, color=True)


def _detail(path: Path | str) -> None:
    """Print a long value on its own aligned line; wrapping must not disturb the columns."""
    typer.secho(f"  {'':<9} {path}", dim=True, color=True)


def _progress(text: str) -> None:
    typer.secho(f"... {text}", fg=typer.colors.WHITE, dim=True, color=True)


def _command(parts: list[str], env: dict[str, str] | None = None) -> None:
    prefix = "".join(f"{key}={shlex.quote(value)} " for key, value in (env or {}).items())
    typer.secho(f"     {prefix}{shlex.join(parts)}", fg=typer.colors.GREEN, bold=True, color=True)


def _require_unmanaged_files_absent(directory: Path) -> None:
    if (directory / "compose.yaml").exists():
        message = "An unmanaged compose.yaml already exists here; restore state.json or choose another POWERCONTEXT_OBSERVABILITY_HOME"
        raise ObservabilityError(message)


def _server_connection(server_env: Path | None, server_url: str | None) -> tuple[str, str]:
    # Reuse the Server configuration contract, including nested/case-insensitive settings and env precedence.
    from powercontext.server.configuration import server_settings_context

    with server_settings_context(env_file=server_env, process_environment_overrides=True) as settings:
        host = settings.http.host
        if host in {"0.0.0.0", "::"}:  # noqa: S104 - convert an existing bind to a client address.
            host = "127.0.0.1"
        if ":" in host:
            host = f"[{host}]"
        token = "" if settings.auth.token is None else settings.auth.token.get_secret_value()
        return server_url or f"http://{host}:{settings.http.port}", token


@app.command()
def status(backend: Backend = None) -> None:
    """Show container state without generating traffic or changing configuration."""
    try:
        validate_backend(backend)
        directory = state_directory()
        if _external_status(directory, backend=backend):
            return
        state = load_state(directory)
        typer.echo(compose(directory, state, "ps", "--all", timeout=20))
        _status_row("Backend", state["backend"])
        _status_row("Grafana", f"http://127.0.0.1:{state['grafana_port']}")
        _status_row("Loki logs", explore_logs_url(state))
        _status_row("Tempo traces", explore_traces_url(state))
        _status_row("Phoenix", f"http://127.0.0.1:{phoenix_port(state)}")
    except (ObservabilityError, OSError) as error:
        _failure(error)


@app.command()
def down(backend: Backend = None) -> None:
    """Stop only this deployment; retain monitoring volumes, credentials and business data."""
    try:
        validate_backend(backend)
        directory = state_directory()
        _require_managed_mode(directory)
        with deployment_lock(directory):
            state = load_state(directory)
            compose(directory, state, "down")
        typer.echo("Monitoring stopped. Data volumes and configuration were retained. Business Server was not stopped.")
    except (ObservabilityError, OSError) as error:
        _failure(error)


@app.command()
def check(
    backend: Backend = None,
    exercise: Annotated[
        bool, typer.Option(help="Send one sampled read-only capabilities request and verify all three signals.")
    ] = False,
) -> None:
    """Query real backends; distinguish idle signals from an end-to-end delivery failure."""
    try:
        validate_backend(backend)
        directory = state_directory()
        if _external_check(directory, backend=backend, exercise=exercise):
            return
        state = load_state(directory)
        with backend_client(directory, state) as client:
            if exercise:
                trace_id = secrets.token_hex(16)
                token = (directory / ".local" / "observe-token").read_text().strip()
                headers = {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                # The request is opt-in and read-only; it never creates Source/Memory records.
                _sampled_request(state["server_url"], headers)
                _verify_delivery(client, state, trace_id)
                wait_for_phoenix(state, trace_id)
                typer.echo("PASS: matching trace stored in Phoenix")
                typer.echo(f"PASS: fresh request metric, trace and matching log stored. Trace ID: {trace_id}")
            else:
                _inspect(client, state)
                wait_for_phoenix(state)
                typer.echo("Phoenix: default project is queryable")
    except (ObservabilityError, OSError, ValueError, httpx.HTTPError) as error:
        _failure(error)


def _require_managed_mode(directory: Path) -> None:
    if (directory / "connection.json").exists():
        message = "External components are not managed by PowerContext; up/down cannot control them. Use status/check or a separate POWERCONTEXT_OBSERVABILITY_HOME"
        raise ObservabilityError(message)


def _external_config(directory: Path, backend: str | None) -> dict[str, Any] | None:
    if not (directory / "connection.json").exists():
        return None
    if backend is not None or (directory / "state.json").exists():
        message = "External connection has no deployment backend; omit --backend and keep managed/external configurations separate"
        raise ObservabilityError(message)
    return load_connection(directory)


def _external_status(directory: Path, *, backend: str | None) -> bool:
    config = _external_config(directory, backend)
    if config is None:
        return False
    typer.echo("Mode: external; no components are owned or controlled by PowerContext")
    typer.echo(f"Configuration: {directory}; Server: {config['server_url']}")
    typer.echo(f"OTLP/HTTP export: {config['otlp_endpoint']}")
    for signal in ("prometheus", "tempo", "loki"):
        typer.echo(f"{signal}: " + config["urls"].get(signal, "query URL not configured"))
    typer.echo("Saved configuration only; use check to query backends.")
    return True


def _external_check(directory: Path, *, backend: str | None, exercise: bool) -> bool:
    config = _external_config(directory, backend)
    if config is None:
        return False
    if exercise:
        try:
            trace_id = exercise_connection(directory, config)
        except (KeyError, TypeError, AttributeError) as error:
            message = "Unexpected external backend response; check query API compatibility"
            raise ObservabilityError(message) from error
        typer.echo(f"PASS: new trace, matching log and metric exemplar stored. Trace ID: {trace_id}")
    else:
        lines, healthy = inspect_connection(directory, config)
        for line in lines:
            typer.echo(line)
        if not healthy:
            raise typer.Exit(code=1)
    return True


@app.command()
def connect(
    otlp_endpoint: Annotated[
        str | None, typer.Option(help="Existing Collector OTLP/HTTP base URL, not its gRPC port.")
    ] = None,
    server_env: Annotated[Path | None, typer.Option(help="Read existing Server settings without editing them.")] = None,
    server_url: Annotated[
        str | None, typer.Option(help="Existing Server origin used for explicit delivery checks.")
    ] = None,
    metrics_target: Annotated[
        str | None, typer.Option(help="host:port reachable from the existing Prometheus.")
    ] = None,
    prometheus_url: Annotated[str | None, typer.Option(help="Optional existing Prometheus query base URL.")] = None,
    tempo_url: Annotated[str | None, typer.Option(help="Optional existing Tempo query base URL.")] = None,
    loki_url: Annotated[str | None, typer.Option(help="Optional existing Loki query base URL.")] = None,
    otlp_headers_file: Annotated[Path | None, typer.Option(help="Private JSON headers for OTLP export.")] = None,
    prometheus_headers_file: Annotated[
        Path | None, typer.Option(help="Private JSON headers for Prometheus queries.")
    ] = None,
    tempo_headers_file: Annotated[Path | None, typer.Option(help="Private JSON headers for Tempo queries.")] = None,
    loki_headers_file: Annotated[Path | None, typer.Option(help="Private JSON headers for Loki queries.")] = None,
    log_directory: Annotated[
        Path | None, typer.Option(help="Optional Server JSON log directory; otherwise use JSON stdout.")
    ] = None,
    service_name: Annotated[
        str, typer.Option(help="Server service identity used by traces, logs and checks.")
    ] = "powercontext-server",
) -> None:
    """Generate connection/import files for existing components; never run Docker or change remote configuration."""
    try:
        directory = state_directory()
        if (directory / "state.json").exists() or (directory / "compose.yaml").exists():
            message = "Managed configuration already exists; use a separate POWERCONTEXT_OBSERVABILITY_HOME for external components"
            raise ObservabilityError(message)  # noqa: TRY301
        if (directory / "connection.json").exists() and not any((
            otlp_endpoint,
            server_env,
            server_url,
            metrics_target,
            prometheus_url,
            tempo_url,
            loki_url,
            otlp_headers_file,
            prometheus_headers_file,
            tempo_headers_file,
            loki_headers_file,
            log_directory,
            service_name != "powercontext-server",
        )):
            _external_status(directory, backend=None)
            return
        if otlp_endpoint is None:
            otlp_endpoint = typer.prompt("Existing Collector OTLP/HTTP base URL")
        effective_url, token = _server_connection(server_env, server_url)
        with deployment_lock(directory):
            create_connection(
                directory,
                otlp_endpoint=otlp_endpoint,
                server_url=effective_url,
                token=token,
                urls={
                    key: value
                    for key, value in {"prometheus": prometheus_url, "tempo": tempo_url, "loki": loki_url}.items()
                    if value is not None
                },
                header_files={
                    key: value
                    for key, value in {
                        "otlp": otlp_headers_file,
                        "prometheus": prometheus_headers_file,
                        "tempo": tempo_headers_file,
                        "loki": loki_headers_file,
                    }.items()
                    if value is not None
                },
                metrics_target=metrics_target,
                log_directory=log_directory,
                service_name=service_name,
            )
        typer.echo(f"External connection saved: {directory}")
        typer.echo(f"Import files and instructions: {directory / 'external' / 'README.md'}")
        typer.echo(
            "Apply the scrape/log fragments to your existing platform, import dashboards and restart the same Server with --observability."
        )
        typer.echo(
            "No remote configuration changed. No components started or stopped. Run observability check after integration."
        )
    except (ObservabilityError, OSError, ValueError, ImportError) as error:
        _failure(error)


def _sampled_request(server_url: str, headers: dict[str, str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            response = httpx.get(server_url + "/v1/capabilities", headers=headers, timeout=3, trust_env=False)
        except httpx.TransportError:
            time.sleep(1)
            continue
        if response.status_code == 200:
            return
        if response.status_code not in {502, 503, 504}:
            raise ObservabilityError(  # noqa: TRY003
                "Server capabilities request failed; check Server address and observation credentials"
            )
        time.sleep(1)
    raise ObservabilityError("Server did not become available within 20 seconds; check its startup and address")  # noqa: TRY003


def _json(client: httpx.Client, path: str, **params):
    response = client.get(path, params=params)
    if response.status_code != 200:
        raise ObservabilityError("Backend query failed; check component health and gateway credentials")  # noqa: TRY003
    return response.json()


def _inspect(client: httpx.Client, state: dict[str, Any]) -> None:
    query = f'up{{job="powercontext",instance="{state["metrics_target"]}"}} and (time() - timestamp(up{{job="powercontext",instance="{state["metrics_target"]}"}}) < 45)'
    result = _json(client, "/prometheus/api/v1/query", query=query)["data"]["result"]
    if not result or any(float(row["value"][1]) != 1 or time.time() - float(row["value"][0]) > 45 for row in result):
        raise ObservabilityError(  # noqa: TRY003
            "Metrics target is unavailable or stale; check credentials, address and Docker-to-host routing"
        )
    traces = _json(client, "/tempo/api/search", start=int(time.time() - 900), end=int(time.time()), limit=1)
    logs = _json(
        client,
        "/loki/loki/api/v1/query_range",
        query='{service_name=~".+"}',
        start=str(int((time.time() - 900) * 1e9)),
        limit=1,
    )
    typer.echo("Metrics: current scrape is healthy")
    typer.echo("Traces (15 minutes): " + ("present" if traces.get("traces") else "none; idle or not delivered"))
    typer.echo("Logs (15 minutes): " + ("present" if logs["data"]["result"] else "none; idle or not delivered"))
    typer.echo("Use check --exercise to verify delivery for one new sampled request.")


def _verify_delivery(client: httpx.Client, state: dict[str, Any], trace_id: str) -> None:
    started = time.time()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        trace = client.get("/tempo/api/traces/" + trace_id, headers={"Accept": "application/json"})
        logs = _json(
            client, "/loki/loki/api/v1/query_range", query='{service_name=~".+"} |= "' + trace_id + '"', limit=5
        )
        exemplars = _json(
            client,
            "/prometheus/api/v1/query_exemplars",
            query=f'powercontext_server_application_operation_duration_seconds_bucket{{instance="{state["metrics_target"]}",operation="get_capabilities"}}',
            start=started - 5,
            end=time.time(),
        )
        matching_exemplar = any(
            exemplar.get("labels", {}).get("trace_id") == trace_id
            for series in exemplars.get("data", [])
            for exemplar in series.get("exemplars", [])
        )
        if trace.status_code == 200 and logs["data"]["result"] and matching_exemplar:
            return
        time.sleep(1)
    raise ObservabilityError(  # noqa: TRY003
        "End-to-end delivery timed out; inspect Server tracing/export dependency, Collector, log directory and scrape target"
    )
