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
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import typer

from powercontext.client.transport_policy import (
    client_config_file,
    load_client_settings,
    normalize_client_url,
    parse_client_boolean,
    resolve_client_transport,
)
from powercontext.transport import is_loopback_host

if TYPE_CHECKING:
    from powercontext.cli.system import Diagnostic


setup_environment_file: ContextVar[Path | None] = ContextVar("setup_environment_file", default=None)


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
    destination: Path | None = None,
) -> SetupTransport:
    """Resolve and confirm the endpoint before any installation side effects."""

    from powercontext.cli.system import SetupError

    try:
        if host == "dsh":
            from .native_transport import validate_dsh_setup_transport

            validate_dsh_setup_transport()
        prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
        server_url = resolve_setup_endpoint(host, server_url=server_url, destination=destination)
        loaded = setup_environment()
        if allow_insecure_http is None:
            consent_keys = (prefix + "ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP")
            if not any(key in os.environ for key in consent_keys):
                configured_consent = next((loaded[key] for key in consent_keys if key in loaded), None)
                if configured_consent is not None:
                    allow_insecure_http = parse_client_boolean(configured_consent)
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


def setup_environment() -> dict[str, str]:
    """Read setup configuration without executing shell code or changing the process."""
    from powercontext.cli.env_file import read_environment_file

    explicit = setup_environment_file.get()
    path = explicit.expanduser() if explicit is not None else Path.cwd() / ".env"
    try:
        return read_environment_file(path) if explicit is not None or path.is_file() else {}
    except (OSError, ValueError) as error:
        raise ValueError("Cannot read setup environment file; check its path and assignment syntax") from error  # noqa: TRY003


def _explicit_setup_endpoint(host: str, server_url: str) -> str:
    """Honor a chosen URL only when inherited runtime overrides cannot undo it."""
    endpoint = normalize_client_url(server_url).removesuffix("/mcp").rstrip("/")
    prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
    keys = (prefix + "BASE_URL", prefix + "SERVER_URL", prefix + "ENDPOINT", "POWERCONTEXT_CLIENT_SERVER_URL")
    if host == "claude-code":
        keys += ("CLAUDE_PLUGIN_OPTION_SERVER_URL",)
    for key in keys:
        if os.environ.get(key) and normalize_client_url(os.environ[key]).removesuffix("/mcp").rstrip("/") != endpoint:
            raise ValueError(  # noqa: TRY003
                f"{key} conflicts with --server-url and would override the installed endpoint at runtime. "
                f"Unset {key} or set it to the selected URL, then rerun setup."
            )
    return endpoint


def resolve_setup_endpoint(
    host: str,
    *,
    server_url: str | None = None,
    default: str = "http://127.0.0.1:8000",
    destination: Path | None = None,
) -> str:
    """Choose one endpoint, rejecting ambiguous explicit settings before installation.

    A command-line URL is an explicit choice. Otherwise URL declarations must agree;
    the local listening port is only a fallback when no client endpoint exists.
    """
    if server_url is not None:
        return _explicit_setup_endpoint(host, server_url)
    prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
    keys = (prefix + "BASE_URL", prefix + "SERVER_URL", prefix + "ENDPOINT", "POWERCONTEXT_CLIENT_SERVER_URL")
    loaded = setup_environment()
    candidates = [
        (name, normalize_client_url(values[name]).removesuffix("/mcp").rstrip("/"))
        for values in (loaded, os.environ)
        for name in keys
        if values.get(name)
    ]
    saved = load_client_settings(host).get("server_url")
    native = existing_native_endpoint(host, destination=destination)
    for name, value in (("saved client settings", saved), ("native host settings", native)):
        if value:
            candidates.append((name, normalize_client_url(value).removesuffix("/mcp").rstrip("/")))
    if len({value for _, value in candidates}) > 1:
        names = ", ".join(dict.fromkeys(name for name, _ in candidates))
        raise ValueError(  # noqa: TRY003
            f"Conflicting PowerContext endpoints in {names}. Choose --server-url explicitly, "
            "then align or unset conflicting runtime environment overrides before restarting the Agent."
        )
    if candidates:
        return candidates[0][1]
    values = loaded | dict(os.environ)
    if public_url := values.get("POWERCONTEXT_SERVER_PUBLIC_URL"):
        return normalize_client_url(public_url).removesuffix("/mcp").rstrip("/")
    if "POWERCONTEXT_SERVER_HTTP_PORT" not in values:
        return default
    try:
        port = int(values["POWERCONTEXT_SERVER_HTTP_PORT"])
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError("POWERCONTEXT_SERVER_HTTP_PORT must be an integer between 1 and 65535")  # noqa: TRY003
    return f"http://127.0.0.1:{port}"


def existing_native_endpoint(host: str, *, destination: Path | None = None) -> str | None:
    """Preserve existing native endpoints when setup has no connection override."""

    if host == "workbuddy":
        from .workbuddy import workbuddy_home

        path = workbuddy_home() / "mcp.json"
    elif host in {"minimax", "agent-plugin"}:
        from .host import host_adapter
        from .packages import saved_installation

        directory = destination or saved_installation(host_adapter(host).target).get("destination")
        if host == "minimax":
            from .minimax import plugin_directory

            directory = plugin_directory(Path(directory) if directory else None)
        if not directory:
            return None
        path = Path(directory) / ("powercontext.mcp.json" if host == "minimax" else "mcp.json")
    else:
        from .native_transport import configured_native_endpoint

        return configured_native_endpoint(host)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        value = config.get("mcpServers", config).get("powercontext", {}).get("url")
        if isinstance(value, str):
            match = re.fullmatch(r"\$\{POWERCONTEXT_WORKBUDDY_SERVER_URL:-(.+)\}/mcp", value)
            return match.group(1) if match else value
    except (OSError, ValueError, AttributeError):
        pass
    return None


def save_setup_transport(settings: SetupTransport, *, installation: dict[str, str] | None = None) -> None:
    """Save endpoint-bound consent, rolling back paired native writes on failure."""

    from powercontext.cli.system import SetupError

    from .native import _write_bytes_atomically

    path = client_config_file()
    completed: list[tuple[Path, bytes | None]] = []
    try:
        config: Any = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "hosts": {}}
        if (
            not isinstance(config, dict)
            or config.get("version") != 1
            or not isinstance(config.get("hosts"), dict)
            or not isinstance(config["hosts"].get(settings.host, {}), dict)
        ):
            raise ValueError("Client configuration must have version 1 and a hosts object")  # noqa: TRY003, TRY301
        existing = config["hosts"].get(settings.host, {})
        config["hosts"][settings.host] = {
            **existing,
            "server_url": settings.server_url,
            "allow_insecure_http": settings.allow_insecure_http,
        }
        if installation:
            config["hosts"][settings.host]["installation"] = installation
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

    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    from .native_transport import resolve_host_transport

    try:
        endpoint, allowed = resolve_host_transport(host)
    except ValueError as error:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
    from powercontext.client.integration.doctor import transport_check

    check = transport_check(endpoint, allowed=allowed)
    return Diagnostic(status=DiagnosticStatus(check["status"]), detail=check["detail"])


def add_transport_diagnostic(diagnostics: dict[str, Diagnostic], host: str) -> None:
    """Append policy problems without changing healthy installation reports."""

    diagnostic = transport_diagnostic(host)
    if not diagnostic.ok:
        diagnostics["transport"] = diagnostic
