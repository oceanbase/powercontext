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

"""Shared setup consent and non-secret per-host connection persistence."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import typer

from powercontext.client.transport_policy import (
    client_config_file,
    load_client_settings,
    parse_client_boolean,
    resolve_client_transport,
)
from powercontext.transport import is_loopback_host

if TYPE_CHECKING:
    from powercontext.cli.system import Diagnostic


@dataclass(frozen=True)
class SetupTransport:
    host: str
    server_url: str
    allow_insecure_http: bool


def is_remote_http(server_url: str) -> bool:
    """Classify plaintext endpoints without resolving DNS or making a request."""

    parsed = urlsplit(server_url)
    return parsed.scheme == "http" and not is_loopback_host(parsed.hostname)


def prepare_setup_transport(
    host: str,
    *,
    server_url: str | None = None,
    allow_insecure_http: bool | None = None,
    json_output: bool = False,
) -> SetupTransport:
    """Resolve and confirm the endpoint before any installation side effects."""

    from powercontext.cli.system import SetupError

    try:
        if host == "dsh":
            from powercontext.cli.native_transport import validate_dsh_setup_transport

            validate_dsh_setup_transport()
        prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
        has_environment_url = any(
            os.environ.get(key)
            for key in (
                prefix + "BASE_URL",
                prefix + "SERVER_URL",
                prefix + "ENDPOINT",
                "POWERCONTEXT_CLIENT_SERVER_URL",
            )
        )
        if server_url is None and not has_environment_url and not load_client_settings(host).get("server_url"):
            server_url = existing_native_endpoint(host)
        endpoint, allowed = resolve_client_transport(
            host, server_url=server_url, allow_insecure_http=allow_insecure_http
        )
        environment_consent = next(
            (
                os.environ[key]
                for key in (prefix + "ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP")
                if key in os.environ
            ),
            None,
        )
        environment_refused = environment_consent is not None and not parse_client_boolean(environment_consent)
    except ValueError as error:
        raise SetupError(str(error)) from error
    endpoint = endpoint.removesuffix("/mcp").rstrip("/")
    if is_remote_http(endpoint):
        if not allowed:
            if (
                allow_insecure_http is None
                and not environment_refused
                and not json_output
                and sys.stdin.isatty()
                and typer.confirm(
                    f"{host}: {endpoint} uses unencrypted HTTP. Tokens and conversation content can be "
                    "read or modified in transit. Allow this endpoint?",
                    default=False,
                    err=True,
                )
            ):
                allowed = True
            else:
                raise SetupError(  # noqa: TRY003
                    "Remote HTTP requires explicit consent. Prefer HTTPS or an SSH tunnel, or use "
                    "--allow-insecure-http (POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP=true)."
                )
        typer.echo(
            f"WARNING: {host} uses unencrypted HTTP at {endpoint}; credentials and content are not "
            "protected in transit. TLS verification and Server authentication remain unchanged.",
            err=True,
        )
    return SetupTransport(host, endpoint, allowed)


def existing_native_endpoint(host: str) -> str | None:
    """Preserve existing native endpoints when setup has no connection override."""

    if host != "workbuddy":
        return None
    from powercontext.cli.workbuddy import workbuddy_home

    path = workbuddy_home() / "mcp.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        value = config.get("mcpServers", {}).get("powercontext", {}).get("url")
        if isinstance(value, str):
            match = re.fullmatch(r"\$\{POWERCONTEXT_WORKBUDDY_SERVER_URL:-(.+)\}/mcp", value)
            return match.group(1) if match else value
    except (OSError, ValueError, AttributeError):
        pass
    return None


def save_setup_transport(settings: SetupTransport) -> None:
    """Save endpoint-bound consent, rolling back paired native writes on failure."""

    from powercontext.cli.system import SetupError, _write_bytes_atomically

    path = client_config_file()
    completed: list[tuple[Path, bytes | None]] = []
    try:
        config: Any = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "hosts": {}}
        if not isinstance(config, dict) or config.get("version") != 1 or not isinstance(config.get("hosts"), dict):
            raise ValueError("Client configuration must have version 1 and a hosts object")  # noqa: TRY003, TRY301
        config["hosts"][settings.host] = {
            "server_url": settings.server_url,
            "allow_insecure_http": settings.allow_insecure_http,
        }
        updates: list[tuple[Path, dict[str, Any]]] = []
        if settings.host == "hermes":
            native_path = hermes_config_file()
            native = json.loads(native_path.read_text(encoding="utf-8")) if native_path.exists() else {}
            if not isinstance(native, dict):
                raise ValueError("Hermes client configuration must be an object")  # noqa: TRY003, TRY301
            native.update(base_url=settings.server_url, allow_insecure_http=settings.allow_insecure_http)
            updates.append((native_path, native))
        updates.append((path, config))
        # Validate every destination before writing either half of a native/shared pair.
        writes = [
            (destination, destination.read_bytes() if destination.exists() else None, payload)
            for destination, payload in updates
        ]
        for destination, snapshot, payload in writes:
            _write_bytes_atomically(destination, (json.dumps(payload, indent=2) + "\n").encode())
            completed.append((destination, snapshot))
    except (OSError, ValueError) as error:
        for destination, snapshot in reversed(completed):
            try:
                if snapshot is None:
                    destination.unlink(missing_ok=True)
                else:
                    _write_bytes_atomically(destination, snapshot)
            except OSError:
                typer.echo(
                    f"WARNING: could not restore {destination}; check its configuration before restarting.", err=True
                )
        raise SetupError(f"Could not save PowerContext Client settings at {path}") from error  # noqa: TRY003


def hermes_config_file() -> Path:
    """Locate the same native configuration file used by the Hermes provider."""

    override = os.environ.get("POWERCONTEXT_HERMES_CONFIG")
    if override:
        return Path(override)
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "powercontext" / "config.json"


def transport_diagnostic(host: str) -> Diagnostic:
    """Report explicitly insecure transport as degraded rather than silently green."""

    from powercontext.cli.native_transport import resolve_host_transport
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    try:
        endpoint, allowed = resolve_host_transport(host)
    except ValueError as error:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
    if is_remote_http(endpoint):
        return Diagnostic(
            status=DiagnosticStatus.DEGRADED if allowed else DiagnosticStatus.FAILED,
            detail=(
                f"{endpoint}: insecure HTTP explicitly enabled; credentials and content are unencrypted"
                if allowed
                else f"{endpoint}: remote HTTP blocked; use HTTPS or explicitly allow insecure HTTP"
            ),
        )
    return Diagnostic(status=DiagnosticStatus.OK, detail=endpoint)


def add_transport_diagnostic(diagnostics: dict[str, Diagnostic], host: str) -> None:
    """Append policy problems without changing healthy installation reports."""

    diagnostic = transport_diagnostic(host)
    if not diagnostic.ok:
        diagnostics["transport"] = diagnostic
