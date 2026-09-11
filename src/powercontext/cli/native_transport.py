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
    cache = _home("CODEX_HOME", ".codex") / "plugins" / "cache"
    try:
        paths = list(cache.glob("*/powercontext/*/.mcp.json"))
    except OSError:
        raise ValueError(_UNKNOWN) from None
    urls = set()
    for path in paths:
        entry = _object_at(_read(path), "mcpServers", "powercontext")
        raw = entry.get("url")
        if not isinstance(raw, str) or not raw.rstrip("/").endswith("/mcp"):
            raise ValueError(_UNKNOWN)
        urls.add(_url(raw))
    if len(urls) > 1:
        raise ValueError("Cannot determine the active PowerContext endpoint from multiple Codex cache versions")  # noqa: TRY003
    return next(iter(urls), None)


def _native_settings(host: str) -> tuple[dict[str, Any], str, str]:
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


def _check_dsh_overlays(*, profile: str | None = None) -> None:
    home = _home("DSH_HOME", ".dsh")
    profile = profile or os.environ.get("DSH_PROFILE", "").strip() or "web"
    for path in (home / "cordis.patch.yml", home / "profiles" / profile / "cordis.patch.yml"):
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            raise ValueError(_UNKNOWN) from None
        # DSH initProfile creates comments followed by an empty patch list.
        # Recognize only that inert form; do not evaluate YAML tags or patches.
        content = "\n".join(line.partition("#")[0].strip() for line in content.splitlines()).strip()
        if content in {"", "[]"}:
            continue
        raise ValueError("Cannot determine PowerContext transport with unsupported DSH runtime overlays")  # noqa: TRY003


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
    return environment_url or common_url or native_url or fallback


def validate_dsh_setup_transport() -> None:
    """Require a verifiable DSH patch layer before changing installation state."""
    try:
        _check_dsh_overlays(profile="web")
    except ValueError:
        raise ValueError(  # noqa: TRY003
            "Cannot verify customized DSH runtime overlays. Align the endpoint and HTTP consent manually, "
            "or remove custom overlays before rerunning setup."
        ) from None


def resolve_host_transport(host: str) -> tuple[str, bool]:
    """Resolve the effective endpoint and consent, or report an unknown native configuration.

    Native HTTP consent is endpoint-bound. This is a read-only diagnostic, not
    an HTTP guard; callers must label remote HTTP as degraded or blocked.
    """
    if host == "dsh":
        _check_dsh_overlays()
    native, url_key, consent_key = _native_settings(host)
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
        if host == "openclaw" and not isinstance(native_consent, bool):
            raise ValueError(_UNKNOWN)
        consent = parse_client_boolean(native_consent)
        environment_consent = any(
            name in os.environ for name in (prefix + "_ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP")
        )
        if not environment_consent and (host in {"claude-code", "hermes"} or not consent or native_url == endpoint):
            allowed = consent and native_url == endpoint
    return endpoint, allowed


__all__ = ["resolve_host_transport", "validate_dsh_setup_transport"]
