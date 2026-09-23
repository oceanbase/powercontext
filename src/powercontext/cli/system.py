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

"""Shared installed-environment and Server diagnostics."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import version
from time import time
from typing import Annotated
from urllib.parse import urlsplit

import typer

from powercontext.cli.errors import SetupError as SetupError
from powercontext.cli.integrations import IntegrationGroup
from powercontext.client.settings import normalize_server_url
from powercontext.client.transport_policy import resolve_client_transport
from powercontext.transport import canonical_loopback_endpoint, is_loopback_host

HELP_OPTION_NAMES = ("-h", "--help")
doctor_app = typer.Typer(
    cls=IntegrationGroup,
    name="doctor",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Check an installed PowerContext environment.",
    invoke_without_command=True,
)


class DiagnosticStatus(StrEnum):
    """Outcome of one installation diagnostic."""

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    status: DiagnosticStatus
    detail: str
    checks: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        """Return whether this check passed."""

        return self.status is DiagnosticStatus.OK

    def as_json(self) -> dict[str, object]:
        """Return the stable external diagnostic representation."""

        result: dict[str, object] = {
            "ok": self.ok,
            "status": self.status.value,
            "detail": self.detail,
        }
        if self.checks is not None:
            result["checks"] = self.checks
        return result


@doctor_app.callback()
def doctor(
    context: typer.Context,
    server_url: Annotated[
        str | None,
        typer.Option(
            envvar="POWERCONTEXT_CLIENT_SERVER_URL",
            help="PowerContext Server base URL.",
        ),
    ] = None,
    allow_insecure_http: Annotated[
        bool | None,
        typer.Option(
            "--allow-insecure-http/--no-allow-insecure-http", help="Explicitly allow unencrypted remote HTTP."
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the installed package and configured Server."""

    if context.invoked_subcommand is not None:
        return
    diagnostics = run_diagnostics(server_url=server_url, allow_insecure_http=allow_insecure_http)
    _write_diagnostics(diagnostics, json_output=json_output)
    if not _diagnostics_ok(diagnostics):
        raise typer.Exit(code=1)


def run_diagnostics(*, server_url: str | None = None, allow_insecure_http: bool | None = None) -> dict[str, Diagnostic]:
    """Collect installed-environment diagnostics without changing state."""

    package = Diagnostic(status=DiagnosticStatus.OK, detail=f"powercontext {version('powercontext')}")
    service: dict[str, Diagnostic] = {}
    try:
        server_url, allowed = resolve_client_transport(
            "client", server_url=server_url, allow_insecure_http=allow_insecure_http
        )
        server_url = normalize_server_url(server_url, allow_insecure_http=allowed)
    except ValueError as error:
        liveness = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
    else:
        service = _local_service_diagnostics(server_url)
        from pydantic import SecretStr

        from powercontext.client.integration.models import Connection

        service.update(
            server_diagnostics(
                Connection(
                    base_url=server_url,
                    authorization=SecretStr(token)
                    if (token := os.environ.get("POWERCONTEXT_CLIENT_API_TOKEN"))
                    else None,
                    allow_insecure_http=allowed,
                    request_timeout=3,
                )
            )
        )
        liveness = None
    if liveness is not None:
        service["server_liveness"] = liveness
    return {"package": package, **service}


def server_diagnostics(connection) -> dict[str, Diagnostic]:
    from powercontext.client.integration.doctor import diagnose_server

    report = asyncio.run(diagnose_server(connection, deadline=time() + 10))
    return {
        (name if name in {"transport", "configuration"} else "server_" + name): Diagnostic(
            DiagnosticStatus(check["status"]), check["detail"], check.get("checks")
        )
        for name, check in report["checks"].items()
    }


def _local_service_diagnostics(server_url: str) -> dict[str, Diagnostic]:
    """Correlate a loopback diagnostic target with the optional personal service registration."""

    parsed = urlsplit(server_url)
    if not is_loopback_host(parsed.hostname):
        return {}

    try:
        from powercontext.service.controller import ServiceController
    except ModuleNotFoundError:
        return {}
    from powercontext.service.model import (
        DefinitionState,
        ManagerState,
        RegistrationState,
        SupportState,
    )

    controller = ServiceController()
    try:
        status = controller.registration_status()
    except Exception as error:  # Native diagnostics must not hide the Server checks that follow.
        return {
            "service_support": Diagnostic(
                status=DiagnosticStatus.DEGRADED,
                detail=f"personal service status is unavailable: {error}",
            )
        }

    diagnostics: dict[str, Diagnostic] = {}
    if status.support is SupportState.UNSUPPORTED:
        diagnostics["service_support"] = Diagnostic(
            status=DiagnosticStatus.OK,
            detail=f"unsupported (optional): {status.detail or 'no verified native adapter'}",
        )
        return diagnostics
    diagnostics["service_support"] = Diagnostic(
        status=DiagnosticStatus.OK,
        detail="native personal service adapter is supported",
    )

    if status.registration is RegistrationState.NOT_INSTALLED:
        diagnostics["service_registration"] = Diagnostic(
            status=DiagnosticStatus.OK,
            detail="not_installed (optional)",
        )
        return diagnostics
    if status.registration is not RegistrationState.INSTALLED:
        diagnostics["service_registration"] = Diagnostic(
            status=DiagnosticStatus.FAILED,
            detail=status.detail or status.registration.value,
        )
        return diagnostics
    if status.endpoint is None or canonical_loopback_endpoint(status.endpoint) != canonical_loopback_endpoint(
        server_url
    ):
        return {}

    try:
        status = controller.status()
    except Exception as error:  # Manager diagnostics must not hide the Server checks that follow.
        diagnostics["service_registration"] = Diagnostic(
            status=DiagnosticStatus.OK,
            detail="installed",
        )
        diagnostics["service_manager"] = Diagnostic(
            status=DiagnosticStatus.DEGRADED,
            detail=f"personal service manager status is unavailable: {error}",
        )
        return diagnostics

    diagnostics["service_registration"] = Diagnostic(
        status=DiagnosticStatus.OK,
        detail="installed",
    )
    diagnostics["service_definition"] = Diagnostic(
        status=(DiagnosticStatus.OK if status.definition is DefinitionState.CURRENT else DiagnosticStatus.FAILED),
        detail=status.definition.value,
    )
    diagnostics["service_manager"] = Diagnostic(
        status=(DiagnosticStatus.OK if status.manager is ManagerState.ACTIVE else DiagnosticStatus.FAILED),
        detail=(
            f"{status.manager.value}; ownership: {status.manager_ownership.value}"
            if status.log_location is None
            else (f"{status.manager.value}; ownership: {status.manager_ownership.value}; logs: {status.log_location}")
        ),
    )
    return diagnostics


def _diagnostics_ok(diagnostics: dict[str, Diagnostic]) -> bool:
    return _diagnostics_status(diagnostics) is DiagnosticStatus.OK


def _diagnostics_status(diagnostics: dict[str, Diagnostic]) -> DiagnosticStatus:
    statuses = {diagnostic.status for diagnostic in diagnostics.values()}
    for status in (DiagnosticStatus.FAILED, DiagnosticStatus.DEGRADED, DiagnosticStatus.SKIPPED):
        if status in statuses:
            return status
    return DiagnosticStatus.OK


def _write_diagnostics(diagnostics: dict[str, Diagnostic], *, json_output: bool) -> None:
    if json_output:
        status = _diagnostics_status(diagnostics)
        typer.echo(
            json.dumps(
                {
                    "ok": status is DiagnosticStatus.OK,
                    "status": status.value,
                    "checks": {name: diagnostic.as_json() for name, diagnostic in diagnostics.items()},
                },
                indent=2,
            )
        )
        return
    for name, diagnostic in diagnostics.items():
        typer.echo(f"{name.replace('_', ' ')}: {diagnostic.status.value} - {diagnostic.detail}")
        if diagnostic.checks is not None:
            for check, status in diagnostic.checks.items():
                typer.echo(f"  {check}: {status}")
