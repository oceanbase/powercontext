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

"""Read native host transport settings without guessing unsupported configuration."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, cast

from powercontext.cli.dsh_runtime import DshTarget
from powercontext.client.transport_policy import (
    load_client_settings,
    normalize_client_url,
    parse_client_boolean,
    resolve_client_transport,
)

_DEFAULT_URL = "http://127.0.0.1:8000"
_MISSING = object()
_UNKNOWN = "Cannot determine PowerContext transport from unsupported or unreadable native host configuration"


def _home(variable: str, directory: str) -> Path:
    configured = os.environ.get(variable, "").strip()
    return Path(configured).expanduser() if configured else Path.home() / directory


def _object_at(value: object, *keys: str) -> dict[str, Any]:
    for key in (*keys, None):
        if not isinstance(value, dict) or "$include" in value:
            raise ValueError(_UNKNOWN)
        if key is None:
            return cast(dict[str, Any], value)
        value = value.get(key, {})
    return {}  # pragma: no cover


def _read(path: Path) -> dict[str, Any]:
    try:
        return _object_at(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        raise ValueError(_UNKNOWN) from None


def _url(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "${" in value:
        raise ValueError(_UNKNOWN)
    try:
        return normalize_client_url(value).removesuffix("/mcp").rstrip("/")
    except ValueError:
        raise ValueError(_UNKNOWN) from None


def _environment_url(*names: str) -> str | None:
    return next((os.environ[name].strip() for name in names if os.environ.get(name, "").strip()), None)


def _codex_url() -> str | None:
    from powercontext.cli.system import _installed_codex_plugin

    if not (_home("CODEX_HOME", ".codex") / "plugins/cache").is_dir():
        return None
    plugin = _installed_codex_plugin()
    if plugin is None:
        return None
    path = (
        _home("CODEX_HOME", ".codex")
        / "plugins/cache"
        / plugin["marketplaceName"]
        / "powercontext"
        / plugin["version"]
        / ".mcp.json"
    )
    return _url(_object_at(_read(path), "mcpServers", "powercontext").get("url"))


def _native_settings(host: str, dsh_target: DshTarget | None = None) -> tuple[dict[str, Any], str, str]:
    if host == "dsh":
        from powercontext.cli.dsh_transport import read_dsh_settings

        return (
            read_dsh_settings(
                profile=dsh_target.profile if dsh_target else os.environ.get("DSH_PROFILE", "").strip() or "web",
                executable=dsh_target.command if dsh_target else None,
            ),
            "baseUrl",
            "allowInsecureHttp",
        )
    if host == "kimi-code":
        from powercontext.cli.kimi_code import kimi_home

        return (
            _read(kimi_home() / "plugins/managed/powercontext/powercontext.json"),
            "server_url",
            "allow_insecure_http",
        )
    if host == "claude-code":
        settings = _object_at(
            _read(_home("CLAUDE_CONFIG_DIR", ".claude") / "settings.json"),
            "pluginConfigs",
            "powercontext@powercontext",
            "options",
        ).copy()
        for key in ("server_url", "allow_insecure_http"):
            name = "CLAUDE_PLUGIN_OPTION_" + key.upper()
            if name in os.environ:
                settings[key] = os.environ[name]
        return settings, "server_url", "allow_insecure_http"
    if host == "hermes":
        configured = os.environ.get("POWERCONTEXT_HERMES_CONFIG", "").strip()
        path = Path(configured) if configured else _home("HERMES_HOME", ".hermes") / "powercontext" / "config.json"
        return _read(path), "base_url", "allow_insecure_http"
    if host == "openclaw":
        configured = os.environ.get("OPENCLAW_CONFIG_PATH", "").strip()
        path = (
            Path(configured).expanduser() if configured else _home("OPENCLAW_STATE_DIR", ".openclaw") / "openclaw.json"
        )
        return (
            _object_at(_read(path), "plugins", "entries", "memory-powercontext", "config"),
            "endpoint",
            "allowInsecureHttp",
        )
    return {}, "server_url", "allow_insecure_http"


def _workbuddy_mcp_url() -> str | None:
    entry = _object_at(_read(_home("WORKBUDDY_HOME", ".workbuddy") / "mcp.json"), "mcpServers", "powercontext")
    if not entry:
        return None
    raw = entry.get("url")
    if isinstance(raw, str):
        match = re.fullmatch(r"\$\{POWERCONTEXT_WORKBUDDY_SERVER_URL:-(.+)\}/mcp", raw)
        if match:
            raw = os.environ.get("POWERCONTEXT_WORKBUDDY_SERVER_URL") or match.group(1)
    return _url(raw)


def _selected_url(host: str, prefix: str, native_url: str | None) -> str:
    common_url = _environment_url("POWERCONTEXT_CLIENT_SERVER_URL")
    saved_url = load_client_settings(host).get("server_url")
    environment_url = _environment_url(prefix + "_BASE_URL", prefix + "_SERVER_URL", prefix + "_ENDPOINT")
    if host == "openclaw" and not (environment_url or common_url or native_url or saved_url):
        raise ValueError("PowerContext endpoint is not configured in OpenClaw")  # noqa: TRY003
    fallback = saved_url or _DEFAULT_URL
    if host == "codex":
        return _codex_url() or fallback
    if host == "claude-code":
        return _environment_url(prefix + "_SERVER_URL") or native_url or common_url or fallback
    if host == "hermes":
        return _environment_url(prefix + "_BASE_URL") or native_url or common_url or fallback
    if host == "workbuddy":
        return _environment_url(prefix + "_SERVER_URL") or common_url or fallback
    if host in {"zcode", "kimi-code"}:
        return native_url or _environment_url(prefix + "_SERVER_URL") or _DEFAULT_URL
    return environment_url or common_url or native_url or fallback


def configured_native_endpoint(host: str) -> str | None:
    """Read only explicitly configured native endpoints for setup conflict detection."""
    if host == "minimax":
        from powercontext.cli.minimax import MiniMaxInstaller

        return MiniMaxInstaller().native_endpoint()
    if host == "codex":
        return _codex_url()
    if host == "zcode":
        from powercontext.cli.zcode import zcode_plugin_dir

        settings = _read(zcode_plugin_dir() / "powercontext.json")
        return _url(settings["server_url"]) if "server_url" in settings else None
    native, url_key, _ = _native_settings(host)
    return _url(native[url_key]) if url_key in native else None


def resolve_host_transport(host: str, *, dsh_target: DshTarget | None = None) -> tuple[str, bool]:  # noqa: C901 - native transport precedence differs by host.
    """Resolve the effective endpoint and consent, or report an unknown native configuration.

    Native HTTP consent is endpoint-bound. This is a read-only diagnostic, not
    an HTTP guard; callers must label remote HTTP as degraded or blocked.
    """
    if host == "minimax":
        from powercontext.cli.minimax import MiniMaxInstaller

        endpoint = MiniMaxInstaller().native_endpoint()
        if endpoint is None:
            raise ValueError("MiniMax PowerContext MCP endpoint is not configured")  # noqa: TRY003
        from powercontext.client.transport_policy import load_client_settings

        saved = load_client_settings(host)
        return endpoint, saved.get("allow_insecure_http") is True and saved.get("server_url") == endpoint
    if host == "zcode":
        from powercontext.cli.zcode import zcode_plugin_dir

        settings = _read(zcode_plugin_dir() / "powercontext.json")
        native_url = _url(settings["server_url"]) if "server_url" in settings else None
        prefix = "POWERCONTEXT_ZCODE"
        endpoint = _url(_selected_url(host, prefix, native_url))
        if native_url and native_url != endpoint:
            raise ValueError("Cannot determine a single PowerContext transport: ZCode Hook and MCP URLs differ")  # noqa: TRY003
        _, allowed = resolve_client_transport(host, server_url=endpoint)
        native_consent = settings.get("allow_insecure_http", _MISSING)
        if native_consent is not _MISSING and not isinstance(native_consent, bool):
            raise ValueError(_UNKNOWN)
        if native_consent is not _MISSING:
            allowed = native_consent and native_url == endpoint
        return endpoint, allowed
    native, url_key, consent_key = _native_settings(host, dsh_target)
    native_url = _url(native[url_key]) if url_key in native else None
    prefix = "POWERCONTEXT_" + ("CLAUDE" if host == "claude-code" else host.upper().replace("-", "_"))
    endpoint = _url(_selected_url(host, prefix, native_url))
    if host == "workbuddy":
        mcp_url = _workbuddy_mcp_url()
        if mcp_url is not None and mcp_url != endpoint:
            raise ValueError("Cannot determine a single PowerContext transport: WorkBuddy Hook and MCP URLs differ")  # noqa: TRY003
    _, allowed = resolve_client_transport(host, server_url=endpoint)
    native_consent = native.get(consent_key, _MISSING)
    if native_consent is not _MISSING:
        if host in {"openclaw", "dsh"} and not isinstance(native_consent, bool):
            raise ValueError(_UNKNOWN)
        consent = parse_client_boolean(native_consent)
        environment_consent = any(
            name in os.environ for name in (prefix + "_ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP")
        )
        if not environment_consent and (host in {"claude-code", "hermes"} or not consent or native_url == endpoint):
            allowed = consent and native_url == endpoint
    return endpoint, allowed


__all__ = ["resolve_host_transport"]
