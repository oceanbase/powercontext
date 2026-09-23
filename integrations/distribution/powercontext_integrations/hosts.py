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

"""Integration target catalog, opt-in multi-host setup, and read-only integration diagnostics."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from powercontext.cli.errors import SetupError

from .host import HOST_ADAPTERS, host_adapter

if TYPE_CHECKING:
    from powercontext.cli.system import Diagnostic


HOST_NAMES: tuple[str, ...] = tuple(host.name for host in HOST_ADAPTERS)
_HOST_INDEX: dict[str, str] = {str(index): host.name for index, host in enumerate(HOST_ADAPTERS, start=1)}
_INTEGRATION_KEYS = frozenset({"plugin", "package", "skill", "settings", "mcp", "client", "transport"})
_PATH_MISSING = "is not installed or is not on PATH"


@dataclass(frozen=True, slots=True)
class HostSetupRow:
    """One catalog row in a setup select report."""

    host: str
    status: str
    error: str | None = None

    def as_json(self) -> dict[str, str]:
        payload = {"host": self.host, "status": self.status}
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True, slots=True)
class SetupSelectReport:
    """Per-host matrix for one setup select run."""

    hosts: tuple[HostSetupRow, ...]

    @property
    def has_failure(self) -> bool:
        return any(row.status == "failed" for row in self.hosts)


@dataclass(frozen=True, slots=True)
class IntegrationRow:
    """One integration target in a doctor integrations report."""

    host: str
    presence: str
    cli_key: str
    cli: Diagnostic
    integrations: tuple[tuple[str, Diagnostic], ...]

    @property
    def failed(self) -> bool:
        return self.presence == "present" and not (
            self.cli.ok and all(diagnostic.ok for _, diagnostic in self.integrations)
        )

    def as_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "presence": self.presence,
            self.cli_key: self.cli.as_json(),
        }
        payload.update({key: diagnostic.as_json() for key, diagnostic in self.integrations})
        return payload


@dataclass(frozen=True, slots=True)
class IntegrationReport:
    """Read-only matrix of integration target CLI and integration status."""

    hosts: tuple[IntegrationRow, ...]

    @property
    def has_present_failure(self) -> bool:
        return any(row.failed for row in self.hosts)

    @property
    def ok(self) -> bool:
        return not self.has_present_failure

    @property
    def status(self) -> str:
        return "failed" if self.has_present_failure else "ok"

    def as_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "status": self.status,
            "hosts": {row.host: row.as_json() for row in self.hosts},
        }


def stdin_is_tty() -> bool:
    """Return True when the current stdin can prompt for a host selection."""

    return sys.stdin.isatty()


def parse_host_selection(text: str) -> tuple[str, ...] | None:
    """Parse a comma-separated catalog selection. Empty input cancels."""

    stripped = text.strip()
    if not stripped:
        return None
    selected: set[str] = set()
    for token in (part.strip() for part in stripped.split(",")):
        if not token:
            continue
        selected.add(_resolve_host_token(token))
    return tuple(host.name for host in HOST_ADAPTERS if host.name in selected)


def normalize_requested_hosts(hosts: Sequence[str]) -> tuple[str, ...]:
    """Validate --host values, drop duplicates, and keep catalog order."""

    selected = {_resolve_host_token(name) for name in hosts}
    return tuple(host.name for host in HOST_ADAPTERS if host.name in selected)


def run_setup_select(
    *,
    hosts: Sequence[str] | None,
    source: str,
    ref: str,
    server_url: str | None,
    capture_prompts: bool,
    json_output: bool,
    allow_insecure_http: bool | None = None,
    python: str | None = None,
    destination: Path | None = None,
) -> None:
    """Resolve a selection, install those hosts, and print the matrix."""

    try:
        selected = resolve_selected_hosts(requested=hosts, json_output=json_output)
    except SetupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    if selected is None:
        return
    report = setup_selected_hosts(
        selected=selected,
        source=source,
        ref=ref,
        server_url=server_url,
        capture_prompts=capture_prompts,
        allow_insecure_http=allow_insecure_http,
        json_output=json_output,
        python=python,
        destination=destination,
    )
    write_setup_select_report(report, json_output=json_output)
    if report.has_failure:
        raise typer.Exit(code=1)


def resolve_selected_hosts(*, requested: Sequence[str] | None, json_output: bool) -> tuple[str, ...] | None:
    """Return catalog names to install, or None when the user cancels."""

    if requested:
        return normalize_requested_hosts(requested)
    if json_output:
        raise SetupError("setup select --json requires --host.")
    if not stdin_is_tty():
        raise SetupError("setup select requires --host when stdin is not a TTY.")
    _write_host_catalog()
    return parse_host_selection(sys.stdin.readline())


def setup_selected_hosts(
    *,
    selected: Sequence[str],
    source: str,
    ref: str,
    server_url: str | None,
    capture_prompts: bool,
    allow_insecure_http: bool | None = None,
    json_output: bool = False,
    python: str | None = None,
    destination: Path | None = None,
) -> SetupSelectReport:
    """Install selected hosts and isolate failures from sibling hosts."""

    from powercontext.cli.system import SetupError

    selected_names = set(selected)
    directory_targets = sum("destination" in host_adapter(name).setup_options for name in selected)
    rows: list[HostSetupRow] = []
    for host in HOST_ADAPTERS:
        if host.name not in selected_names:
            rows.append(HostSetupRow(host=host.name, status="skipped"))
            continue
        try:
            host.install(
                source=source,
                ref=ref,
                server_url=server_url,
                capture_prompts=capture_prompts,
                allow_insecure_http=allow_insecure_http,
                json_output=json_output,
                python=python if "python" in host.setup_options else None,
                destination=(destination / host.name if destination and directory_targets > 1 else destination)
                if "destination" in host.setup_options
                else None,
            )
        except SetupError as error:
            rows.append(HostSetupRow(host=host.name, status="failed", error=str(error)))
            continue
        rows.append(HostSetupRow(host=host.name, status="installed"))
    return SetupSelectReport(hosts=tuple(rows))


def run_host_setup(name: str, *, json_output: bool, **options) -> None:
    """Use the same installation lifecycle as setup select, with a single-host result."""

    from powercontext.cli.system import SetupError

    adapter = host_adapter(name)
    try:
        result = adapter.install(json_output=json_output, **options)
    except SetupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    payload = asdict(result)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"PowerContext {adapter.label} setup complete.")
    labels = {"data_dir": "Data directory", "authorization_state": "Authorization"}
    for key, value in payload.items():
        typer.echo(f"{labels.get(key, key.replace('_', ' ').capitalize())}: {value}")
    write_setup_next_steps((name,))


def write_setup_next_steps(installed: Sequence[str]) -> None:
    if installed:
        typer.echo("Next: run `powercontext server run`, then start a new host session.")
    if "hermes" in installed:
        typer.echo("Hermes: run `hermes memory setup` and select PowerContext before starting Hermes.")


def write_setup_select_report(report: SetupSelectReport, *, json_output: bool) -> None:
    """Write the per-host matrix as JSON or human text."""

    if json_output:
        typer.echo(json.dumps({"hosts": [row.as_json() for row in report.hosts]}, indent=2))
        return
    for row in report.hosts:
        if row.status == "failed" and row.error is not None:
            typer.echo(f"{row.host}: failed - {row.error}")
            continue
        typer.echo(f"{row.host}: {row.status}")
    write_setup_next_steps(tuple(row.host for row in report.hosts if row.status == "installed"))


def _resolve_host_token(token: str) -> str:
    name = _HOST_INDEX.get(token, token)
    if name not in HOST_NAMES:
        raise SetupError(f"unknown host: {token}. Choose from: {', '.join(HOST_NAMES)}.")
    return name


def _write_host_catalog() -> None:
    typer.echo("PowerContext integrations:")
    for index, host in enumerate(HOST_ADAPTERS, start=1):
        typer.echo(f"  {index}) {host.label} ({host.name})")
    typer.echo("Select hosts by number or name (comma-separated), or press Enter to cancel:")


def diagnose_host(name: str) -> dict[str, Diagnostic]:
    return host_adapter(name).diagnose()


def split_host_diagnostics(
    diagnostics: dict[str, Diagnostic],
) -> tuple[str, Diagnostic, tuple[tuple[str, Diagnostic], ...]]:
    """Split one host probe into its CLI check and integration checks."""

    cli_key = next(key for key in diagnostics if key not in _INTEGRATION_KEYS)
    integrations = tuple((key, diagnostic) for key, diagnostic in diagnostics.items() if key in _INTEGRATION_KEYS)
    return cli_key, diagnostics[cli_key], integrations


def classify_host_presence(cli: Diagnostic, integrations: tuple[tuple[str, Diagnostic], ...]) -> str:
    """Mark a host missing only when PATH lookup failed and the integration was skipped."""

    missing_cli = _PATH_MISSING in cli.detail or "WorkBuddy hooks are not installed" in cli.detail
    if missing_cli and all(
        diagnostic.status.value == "skipped" for key, diagnostic in integrations if key not in {"client", "transport"}
    ):
        return "missing"
    return "present"


def build_integration_row(name: str, diagnostics: dict[str, Diagnostic]) -> IntegrationRow:
    """Classify one host diagnostic pair without deciding the command exit code."""

    cli_key, cli, integrations = split_host_diagnostics(diagnostics)
    presence = classify_host_presence(cli, integrations)
    return IntegrationRow(
        host=name,
        presence=presence,
        cli_key=cli_key,
        cli=cli,
        integrations=integrations,
    )


def collect_integration_diagnostics() -> IntegrationReport:
    """Walk the shared catalog and collect a row for every integration target."""

    return IntegrationReport(
        hosts=tuple(build_integration_row(host.name, diagnose_host(host.name)) for host in HOST_ADAPTERS)
    )


def format_integration_row(row: IntegrationRow) -> str:
    integrations = " ".join(f"{key}={diagnostic.status.value}" for key, diagnostic in row.integrations)
    return f"{row.host}: {row.presence} - cli={row.cli.status.value} {integrations}"


def write_integration_report(report: IntegrationReport, *, json_output: bool) -> None:
    """Write the host matrix as JSON or one human line per host."""

    if json_output:
        typer.echo(json.dumps(report.as_json(), indent=2))
        return
    for row in report.hosts:
        typer.echo(format_integration_row(row))


def run_doctor_integrations(*, json_output: bool) -> None:
    """Print the integration target matrix and fail only when a present host is broken."""

    report = collect_integration_diagnostics()
    write_integration_report(report, json_output=json_output)
    if report.has_present_failure:
        raise typer.Exit(code=1)
