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

"""Endpoint-bound transport settings for an independently installed plugin.

This standard-library helper is distributed with each Python host plugin.
It must not import the PowerContext package, which need not be installed.
"""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def normalize_server_url(value: str, *, allow_insecure_http: bool = False) -> str:
    """Validate a Server endpoint and remove the optional MCP suffix."""
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PowerContext Server URL must not contain credentials")  # noqa: TRY003
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("PowerContext Server URL must use HTTP or HTTPS")  # noqa: TRY003
    if parsed.query or parsed.fragment:
        raise ValueError("PowerContext Server URL must not contain a query or fragment")  # noqa: TRY003
    # Accessing port also rejects malformed and out-of-range ports.
    port = parsed.port
    host = parsed.hostname.lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parsed.scheme == "http" and not loopback and not allow_insecure_http:
        raise ValueError(  # noqa: TRY003
            "unencrypted PowerContext URLs must be loopback addresses unless allow_insecure_http is explicitly enabled"
        )
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        netloc += f":{port}"
    path = parsed.path.rstrip("/").removesuffix("/mcp")
    return urlunsplit((parsed.scheme, netloc, path, "", "")).rstrip("/")


def load_client_settings(host: str) -> dict[str, Any]:
    """Read only the nonsecret settings owned by this host."""
    configured = os.environ.get("POWERCONTEXT_CLIENT_CONFIG_FILE")
    path = Path(configured).expanduser() if configured else Path.home() / ".config" / "powercontext" / "clients.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        raise ValueError("invalid PowerContext client settings file") from None  # noqa: TRY003
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("invalid PowerContext client settings version")  # noqa: TRY003
    hosts = data.get("hosts")
    if not isinstance(hosts, dict):
        raise ValueError("invalid PowerContext client host settings")  # noqa: TRY003, TRY004
    entry = hosts.get(host, {})
    if not isinstance(entry, dict):
        raise ValueError("invalid PowerContext client host settings")  # noqa: TRY003, TRY004
    result: dict[str, Any] = {}
    if "server_url" in entry:
        if not isinstance(entry["server_url"], str):
            raise ValueError("invalid PowerContext client server URL")  # noqa: TRY003
        result["server_url"] = normalize_server_url(entry["server_url"], allow_insecure_http=True)
    if "allow_insecure_http" in entry:
        if not isinstance(entry["allow_insecure_http"], bool):
            raise ValueError("PowerContext allow_insecure_http must be a JSON boolean")  # noqa: TRY003
        result["allow_insecure_http"] = entry["allow_insecure_http"]
    return result


def parse_boolean(value: object) -> bool:
    """Reject misspellings instead of interpreting nonempty strings as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("invalid boolean PowerContext allow_insecure_http configuration")  # noqa: TRY003


def resolve_allow_insecure_http(
    server_url: str,
    *,
    host: str,
    host_environment: str,
    explicit: bool | None = None,
    saved: dict[str, Any] | None = None,
) -> bool:
    """Resolve consent without transferring saved consent to another endpoint."""
    settings = load_client_settings(host) if saved is None else saved
    if explicit is not None:
        return parse_boolean(explicit)
    names = [host_environment, "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP"]
    for name in names:
        if name in os.environ:
            return parse_boolean(os.environ[name])
    consent = settings.get("allow_insecure_http", False)
    if not isinstance(consent, bool):
        raise ValueError("PowerContext allow_insecure_http must be a JSON boolean")  # noqa: TRY003, TRY004
    saved_url = settings.get("server_url")
    return bool(
        consent
        and isinstance(saved_url, str)
        and normalize_server_url(saved_url, allow_insecure_http=True)
        == normalize_server_url(server_url, allow_insecure_http=True)
    )
