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
    from powercontext.cli.authorization import SetupAuthorizationStatus
    from powercontext.cli.dsh_runtime import DshTarget


setup_environment_file: ContextVar[Path | None] = ContextVar("setup_environment_file", default=None)


@dataclass(frozen=True)
class SetupTransport:
    host: str
    server_url: str
    allow_insecure_http: bool
    endpoint_explicit: bool = False


def _setup_endpoint_environment_names(host: str) -> tuple[str, ...]:
    prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
    return (prefix + "BASE_URL", prefix + "SERVER_URL", prefix + "ENDPOINT", "POWERCONTEXT_CLIENT_SERVER_URL")


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
    dsh_target: DshTarget | None = None,
    native_endpoint: str | None = None,
) -> SetupTransport:
    """Resolve and confirm the endpoint before any installation side effects."""

    from powercontext.cli.system import SetupError

    try:
        endpoint_explicit = server_url is not None or any(
            values.get(name)
            for values in (setup_environment(), os.environ)
            for name in _setup_endpoint_environment_names(host)
        )
        native = {}
        if host == "dsh":
            from powercontext.cli.dsh_transport import read_dsh_settings

            native = read_dsh_settings(
                profile=dsh_target.profile if dsh_target else "web",
                prospective=True,
                executable=dsh_target.command if dsh_target else None,
            )
        prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
        server_url = resolve_setup_endpoint(
            host, server_url=server_url, native_endpoint=native_endpoint if host == "minimax" else native.get("baseUrl")
        )
        allow_insecure_http = _setup_consent(host, server_url, allow_insecure_http, native)
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
    if host == "kimi-code":
        from powercontext.cli.kimi_code import _endpoint

        endpoint = _endpoint(endpoint)
    if host == "zcode":
        from powercontext.cli.zcode import _endpoint

        endpoint = _endpoint(endpoint)
    if host == "dsh":
        from powercontext.cli.dsh_transport import validate_dsh_setup_transport

        try:
            validate_dsh_setup_transport(native, endpoint, allowed)
        except ValueError as error:
            raise SetupError(str(error)) from error
    return SetupTransport(host, endpoint, allowed, endpoint_explicit=endpoint_explicit)


def _setup_consent(host: str, endpoint: str, requested: bool | None, native: dict[str, Any]) -> bool | None:
    """Select setup consent without allowing saved consent to override a native refusal."""
    loaded = setup_environment()
    if requested is not None:
        return requested
    prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_")) + "_"
    keys = (prefix + "ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP")
    if any(key in os.environ for key in keys):
        return None  # resolve_client_transport reads the runtime environment.
    value = next((loaded[key] for key in keys if key in loaded), None)
    if value is not None:
        return parse_client_boolean(value)
    if host == "dsh":
        from powercontext.cli.dsh_transport import matching_dsh_consent

        return matching_dsh_consent(native, endpoint)
    return None


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
    keys = _setup_endpoint_environment_names(host)
    if host == "claude-code":
        keys += ("CLAUDE_PLUGIN_OPTION_SERVER_URL",)
    for key in keys:
        if os.environ.get(key) and normalize_client_url(os.environ[key]).removesuffix("/mcp").rstrip("/") != endpoint:
            raise ValueError(  # noqa: TRY003
                f"{key} conflicts with --server-url and would override the installed endpoint at runtime. "
                f"Unset {key} or set it to the selected URL, then rerun setup."
            )
    return endpoint


def _saved_setup_endpoints(host: str, native_endpoint: str | None) -> list[tuple[str, str]]:
    """Read saved endpoint fallbacks, using the supplied native URL for DSH."""
    if host == "minimax":
        return [("native MiniMax settings", native_endpoint)] if native_endpoint else []
    saved = load_client_settings(host).get("server_url")
    native = native_endpoint if host == "dsh" else existing_native_endpoint(host)
    return [
        (name, normalize_client_url(value).removesuffix("/mcp").rstrip("/"))
        for name, value in (("saved client settings", saved), ("native host settings", native))
        if value
    ]


def resolve_setup_endpoint(
    host: str,
    *,
    server_url: str | None = None,
    default: str = "http://127.0.0.1:8000",
    native_endpoint: str | None = None,
) -> str:
    """Choose one endpoint, rejecting ambiguous explicit settings before installation.

    New URL declarations supersede saved installation settings but must agree with
    active runtime overrides. Saved endpoints and the local port are fallbacks.
    """
    if server_url is not None:
        return _explicit_setup_endpoint(host, server_url)
    keys = _setup_endpoint_environment_names(host)
    loaded = setup_environment()
    candidates = [
        (name, normalize_client_url(values[name]).removesuffix("/mcp").rstrip("/"))
        for values in (loaded, os.environ)
        for name in keys
        if values.get(name)
    ]
    if not candidates:
        candidates = _saved_setup_endpoints(host, native_endpoint)
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


def existing_native_endpoint(host: str) -> str | None:
    """Preserve existing native endpoints when setup has no connection override."""

    from powercontext.cli.native_transport import configured_native_endpoint

    if host != "workbuddy":
        return configured_native_endpoint(host)
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


def configure_connection(
    settings: SetupTransport, *, dsh_target: DshTarget | None = None, writes: dict[Path, bytes] | None = None
) -> SetupAuthorizationStatus:
    """Publish the native endpoint, shared settings, and credentials as one connection."""
    from powercontext.cli.authorization import authorization_updates
    from powercontext.cli.installation import publish

    credentials, state = authorization_updates(settings)
    publish(writes={**(writes or {}), **setup_transport_updates(settings, dsh_target=dsh_target), **credentials})
    return state


def setup_transport_updates(settings: SetupTransport, *, dsh_target: DshTarget | None = None) -> dict[Path, bytes]:
    """Prepare the shared nonsecret Client settings for publication."""
    from powercontext.cli.system import SetupError

    path = client_config_file()
    try:
        config: Any = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "hosts": {}}
        if not isinstance(config, dict) or config.get("version") != 1 or not isinstance(config.get("hosts"), dict):
            raise ValueError  # noqa: TRY301 - normalize invalid persisted configuration.
        entry = config["hosts"].setdefault(settings.host, {})
        if not isinstance(entry, dict):
            raise ValueError  # noqa: TRY301, TRY004 - normalize invalid persisted configuration.
        entry.update(server_url=settings.server_url, allow_insecure_http=settings.allow_insecure_http)
        if dsh_target is not None:
            entry.update(profile=dsh_target.profile, dsh_command=dsh_target.command)
        updates: dict[Path, bytes] = {path: (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()}
    except (OSError, ValueError) as error:
        raise SetupError(f"Could not save PowerContext Client settings at {path}") from error  # noqa: TRY003
    return updates
