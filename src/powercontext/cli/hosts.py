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

"""First-class host catalog, opt-in multi-host setup, and read-only integration diagnostics."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from powercontext.cli.system import Diagnostic


@dataclass(frozen=True, slots=True)
class HostSpec:
    """One first-class setup target. PATH is not used to build this catalog."""

    name: str
    label: str


FIRST_CLASS_HOSTS: tuple[HostSpec, ...] = (
    HostSpec("codex", "Codex"),
    HostSpec("claude-code", "Claude Code"),
    HostSpec("dsh", "DeepSeek Harness"),
    HostSpec("openclaw", "OpenClaw"),
    HostSpec("opencode", "OpenCode"),
    HostSpec("pi", "Pi"),
    HostSpec("hermes", "Hermes"),
)
HOST_NAMES: tuple[str, ...] = tuple(host.name for host in FIRST_CLASS_HOSTS)
_HOST_INDEX: dict[str, str] = {str(index): host.name for index, host in enumerate(FIRST_CLASS_HOSTS, start=1)}
_INTEGRATION_KEYS = frozenset({"plugin", "package", "skill"})
_PATH_MISSING = "is not installed or is not on PATH"


class SetupSelectError(RuntimeError):
    """Invalid setup select usage or host selection."""

    @classmethod
    def json_requires_host(cls) -> SetupSelectError:
        return cls("setup select --json requires --host.")

    @classmethod
    def tty_requires_host(cls) -> SetupSelectError:
        return cls("setup select requires --host when stdin is not a TTY.")

    @classmethod
    def unknown_host(cls, token: str) -> SetupSelectError:
        return cls(f"unknown host: {token}. Choose from: {', '.join(HOST_NAMES)}.")


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

    @property
    def has_installed(self) -> bool:
        return any(row.status == "installed" for row in self.hosts)

    def is_installed(self, host: str) -> bool:
        """Return whether one host completed installation and verification."""

        return any(row.host == host and row.status == "installed" for row in self.hosts)


@dataclass(frozen=True, slots=True)
class IntegrationRow:
    """One first-class host in a doctor integrations report."""

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
    """Read-only matrix of first-class host CLI and integration status."""

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
    return tuple(host.name for host in FIRST_CLASS_HOSTS if host.name in selected)


def normalize_requested_hosts(hosts: Sequence[str]) -> tuple[str, ...]:
    """Validate --host values, drop duplicates, and keep catalog order."""

    selected = {_resolve_host_token(name) for name in hosts}
    return tuple(host.name for host in FIRST_CLASS_HOSTS if host.name in selected)


def run_setup_select(
    *,
    hosts: Sequence[str] | None,
    source: str,
    ref: str,
    server_url: str | None,
    scope_mode: str,
    capture_prompts: bool,
    json_output: bool,
) -> None:
    """Resolve a selection, install those hosts, and print the matrix."""

    try:
        selected = resolve_selected_hosts(requested=hosts, json_output=json_output)
    except SetupSelectError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    if selected is None:
        return
    report = setup_selected_hosts(
        selected=selected,
        source=source,
        ref=ref,
        server_url=server_url,
        scope_mode=scope_mode,
        capture_prompts=capture_prompts,
    )
    write_setup_select_report(report, json_output=json_output)
    if report.has_failure:
        raise typer.Exit(code=1)


def resolve_selected_hosts(*, requested: Sequence[str] | None, json_output: bool) -> tuple[str, ...] | None:
    """Return catalog names to install, or None when the user cancels."""

    if requested:
        return normalize_requested_hosts(requested)
    if json_output:
        raise SetupSelectError.json_requires_host()
    if not stdin_is_tty():
        raise SetupSelectError.tty_requires_host()
    _write_host_catalog()
    return parse_host_selection(sys.stdin.readline())


def setup_selected_hosts(
    *,
    selected: Sequence[str],
    source: str,
    ref: str,
    server_url: str | None,
    scope_mode: str,
    capture_prompts: bool,
) -> SetupSelectReport:
    """Install selected hosts and isolate failures from sibling hosts."""

    from powercontext.cli.system import SetupError

    selected_names = set(selected)
    rows: list[HostSetupRow] = []
    for host in FIRST_CLASS_HOSTS:
        if host.name not in selected_names:
            rows.append(HostSetupRow(host=host.name, status="skipped"))
            continue
        try:
            install_host(
                host.name,
                source=source,
                ref=ref,
                server_url=server_url,
                scope_mode=scope_mode,
                capture_prompts=capture_prompts,
            )
            verify_host(host.name)
        except SetupError as error:
            rows.append(HostSetupRow(host=host.name, status="failed", error=str(error)))
            continue
        rows.append(HostSetupRow(host=host.name, status="installed"))
    return SetupSelectReport(hosts=tuple(rows))


def install_host(
    name: str,
    *,
    source: str,
    ref: str,
    server_url: str | None,
    scope_mode: str,
    capture_prompts: bool,
) -> object:
    """Call the existing installer for one first-class host."""

    if name == "codex":
        from powercontext.cli.system import install_codex_plugin

        return install_codex_plugin(source=source, ref=ref)
    if name == "claude-code":
        from powercontext.cli.system import DEFAULT_CLAUDE_CODE_SERVER_URL, install_claude_code_plugin

        return install_claude_code_plugin(
            source=source,
            ref=ref,
            server_url=server_url if server_url is not None else DEFAULT_CLAUDE_CODE_SERVER_URL,
            capture_prompts=capture_prompts,
        )
    if name == "dsh":
        from powercontext.cli.dsh import install_dsh_plugin

        return install_dsh_plugin(source=source, ref=ref)
    if name == "openclaw":
        from powercontext.cli.openclaw import install_openclaw_plugin
        from powercontext.cli.system import DEFAULT_OPENCLAW_SERVER_URL

        return install_openclaw_plugin(
            source=source,
            ref=ref,
            server_url=server_url if server_url is not None else DEFAULT_OPENCLAW_SERVER_URL,
            scope_mode=scope_mode,
        )
    if name == "opencode":
        from powercontext.cli.opencode import install_opencode_plugin

        return install_opencode_plugin(source=source, ref=ref)
    if name == "pi":
        from powercontext.cli.pi import install_pi_plugin

        return install_pi_plugin(source=source, ref=ref)
    if name == "hermes":
        from powercontext.cli.hermes import install_hermes_plugin

        return install_hermes_plugin(source=source, ref=ref)
    raise SetupSelectError.unknown_host(name)


def verify_host(name: str) -> None:
    """Run the post-install diagnostics used by the matching single-host setup command."""

    from powercontext.cli.system import SetupError

    if name == "codex":
        from powercontext.cli.system import run_codex_diagnostics

        diagnostics = run_codex_diagnostics()
    elif name == "dsh":
        from powercontext.cli.dsh import run_dsh_diagnostics

        diagnostics = run_dsh_diagnostics()
    elif name == "pi":
        from powercontext.cli.pi import run_pi_diagnostics

        diagnostics = run_pi_diagnostics()
    elif name == "hermes":
        from powercontext.cli.hermes import run_hermes_diagnostics

        diagnostics = run_hermes_diagnostics()
    elif name == "opencode":
        from powercontext.cli.opencode import run_opencode_diagnostics

        diagnostics = run_opencode_diagnostics()
    elif name in {"claude-code", "openclaw"}:
        return
    else:
        raise SetupSelectError.unknown_host(name)

    failures = [f"{check}: {diagnostic.detail}" for check, diagnostic in diagnostics.items() if not diagnostic.ok]
    if failures:
        raise SetupError.post_install_verification(failures)


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
    if report.has_installed:
        typer.echo("Next: run `powercontext server run`, then start a new host session.")
    if report.is_installed("hermes"):
        typer.echo("Hermes: run `hermes memory setup` and select PowerContext before starting Hermes.")


def _resolve_host_token(token: str) -> str:
    name = _HOST_INDEX.get(token, token)
    if name not in HOST_NAMES:
        raise SetupSelectError.unknown_host(token)
    return name


def _write_host_catalog() -> None:
    typer.echo("Official first-class integrations:")
    for index, host in enumerate(FIRST_CLASS_HOSTS, start=1):
        typer.echo(f"  {index}) {host.label} ({host.name})")
    typer.echo("Select hosts by number or name (comma-separated), or press Enter to cancel:")


def diagnose_host(name: str) -> dict[str, Diagnostic]:
    """Lazily collect diagnostics for one first-class host."""

    if name == "codex":
        from powercontext.cli.system import run_codex_diagnostics

        return run_codex_diagnostics()
    if name == "claude-code":
        from powercontext.cli.system import run_claude_code_diagnostics

        return run_claude_code_diagnostics()
    if name == "dsh":
        from powercontext.cli.dsh import run_dsh_diagnostics

        return run_dsh_diagnostics()
    if name == "openclaw":
        from powercontext.cli.openclaw import run_openclaw_diagnostics

        return run_openclaw_diagnostics()
    if name == "opencode":
        from powercontext.cli.opencode import run_opencode_diagnostics

        return run_opencode_diagnostics()
    if name == "pi":
        from powercontext.cli.pi import run_pi_diagnostics

        return run_pi_diagnostics()
    if name == "hermes":
        from powercontext.cli.hermes import run_hermes_diagnostics

        return run_hermes_diagnostics()
    raise SetupSelectError.unknown_host(name)


def split_host_diagnostics(
    diagnostics: dict[str, Diagnostic],
) -> tuple[str, Diagnostic, tuple[tuple[str, Diagnostic], ...]]:
    """Split one host probe into its CLI check and integration checks."""

    cli_key = next(key for key in diagnostics if key not in _INTEGRATION_KEYS)
    integrations = tuple((key, diagnostic) for key, diagnostic in diagnostics.items() if key in _INTEGRATION_KEYS)
    return cli_key, diagnostics[cli_key], integrations


def classify_host_presence(cli: Diagnostic, integrations: tuple[tuple[str, Diagnostic], ...]) -> str:
    """Mark a host missing only when PATH lookup failed and the integration was skipped."""

    if _PATH_MISSING in cli.detail and all(diagnostic.status.value == "skipped" for _, diagnostic in integrations):
        return "missing"
    return "present"


def build_integration_row(name: str, diagnostics: dict[str, Diagnostic]) -> IntegrationRow:
    """Classify one host diagnostic pair without deciding the command exit code."""

    cli_key, cli, integrations = split_host_diagnostics(diagnostics)
    return IntegrationRow(
        host=name,
        presence=classify_host_presence(cli, integrations),
        cli_key=cli_key,
        cli=cli,
        integrations=integrations,
    )


def collect_integration_diagnostics() -> IntegrationReport:
    """Walk the shared catalog and collect a row for every first-class host."""

    return IntegrationReport(
        hosts=tuple(build_integration_row(host.name, diagnose_host(host.name)) for host in FIRST_CLASS_HOSTS)
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
    """Print the first-class host matrix and fail only when a present host is broken."""

    report = collect_integration_diagnostics()
    write_integration_report(report, json_output=json_output)
    if report.has_present_failure:
        raise typer.Exit(code=1)
