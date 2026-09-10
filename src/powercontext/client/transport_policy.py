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

"""Resolve non-secret Client connection settings and endpoint-bound HTTP consent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import HttpUrl, ModelWrapValidatorHandler, PrivateAttr, TypeAdapter, ValidationError, model_validator
from pydantic_settings import BaseSettings, InitSettingsSource, PydanticBaseSettingsSource
from typing_extensions import override

_HTTP_URL = TypeAdapter(HttpUrl)
_DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


def client_config_file() -> Path:
    """Return the shared, non-secret host settings file."""

    override = os.environ.get("POWERCONTEXT_CLIENT_CONFIG_FILE")
    return Path(override).expanduser() if override else Path.home() / ".config" / "powercontext" / "clients.json"


def normalize_client_url(value: str) -> str:
    """Validate an HTTP(S) endpoint without deciding whether plaintext is allowed."""

    try:
        normalized = str(_HTTP_URL.validate_python(value)).rstrip("/")
    except ValidationError as error:
        raise ValueError("PowerContext Server URL must be a valid HTTP or HTTPS URL") from error  # noqa: TRY003
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PowerContext Server URL must not contain credentials")  # noqa: TRY003
    if parsed.query or parsed.fragment:
        raise ValueError("PowerContext Server URL must not contain a query or fragment")  # noqa: TRY003
    return normalized


def _endpoint(value: str) -> str:
    return normalize_client_url(value).removesuffix("/mcp").rstrip("/")


def parse_client_boolean(value: object) -> bool:
    """Parse an explicit boolean or a conventional environment boolean string."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if value.strip().lower() in {"0", "false", "no", "off"}:
            return False
    raise ValueError("PowerContext allow_insecure_http must be a boolean (true/false, 1/0, yes/no, on/off)")  # noqa: TRY003


def load_client_settings(host: str) -> dict[str, Any]:
    """Read one host's saved endpoint and consent; credentials are never returned."""

    try:
        raw = json.loads(client_config_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError("Cannot read PowerContext Client configuration") from error  # noqa: TRY003
    if (
        not isinstance(raw, dict)
        or type(raw.get("version")) is not int
        or raw["version"] != 1
        or not isinstance(raw.get("hosts"), dict)
    ):
        raise ValueError("PowerContext Client configuration must have version 1 and a hosts object")  # noqa: TRY003
    saved = raw["hosts"].get(host, {})
    if not isinstance(saved, dict):
        raise ValueError("PowerContext Client host configuration must be an object")  # noqa: TRY003, TRY004
    result: dict[str, Any] = {}
    if "server_url" in saved:
        result["server_url"] = normalize_client_url(saved["server_url"])
    if "allow_insecure_http" in saved:
        if not isinstance(saved["allow_insecure_http"], bool):
            raise ValueError("Saved PowerContext allow_insecure_http must be a JSON boolean")  # noqa: TRY003
        result["allow_insecure_http"] = saved["allow_insecure_http"]
    return result


def _prefix(host: str) -> str:
    host = {"claude-code": "claude"}.get(host, host)
    return "POWERCONTEXT_" + host.upper().replace("-", "_") + "_"


def _environment_consent(host: str) -> str | None:
    for key in (_prefix(host) + "ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP"):
        if key in os.environ:
            return os.environ[key]
    return None


def resolve_client_transport(
    host: str,
    *,
    server_url: str | None = None,
    allow_insecure_http: bool | None = None,
) -> tuple[str, bool]:
    """Resolve constructor, host environment, common environment, then saved settings.

    This validates URL syntax and consent values. The caller applies the plaintext
    guard so a separately vouched custom transport can retain its own security policy.
    """

    saved = load_client_settings(host)
    if server_url is None:
        keys = [_prefix(host) + "BASE_URL", _prefix(host) + "SERVER_URL"]
        if host in {"dsh", "pi", "opencode", "openclaw"}:
            keys.append(_prefix(host) + "ENDPOINT")
        keys.append("POWERCONTEXT_CLIENT_SERVER_URL")
        for key in keys:
            if os.environ.get(key):
                server_url = os.environ[key]
                break
    normalized = normalize_client_url(
        server_url if server_url is not None else saved.get("server_url", _DEFAULT_SERVER_URL)
    )
    if allow_insecure_http is not None:
        return normalized, parse_client_boolean(allow_insecure_http)
    environment = _environment_consent(host)
    if environment is not None:
        return normalized, parse_client_boolean(environment)
    if saved.get("server_url") and _endpoint(normalized) == _endpoint(saved["server_url"]):
        return normalized, saved.get("allow_insecure_http", False)
    return normalized, False


class ClientTransportSettings(BaseSettings):
    """Shared settings resolution retaining the endpoint attached to saved consent."""

    transport_host: ClassVar[str] = "client"
    transport_url_field: ClassVar[str] = "base_url"
    allow_insecure_http: bool = False
    _saved_consent_endpoint: str | None = PrivateAttr(default=None)

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Preserve each host's precedence for unrelated settings while keeping
        # explicitly provided transport values ahead of its environment source.
        transport_values = {
            key: value
            for key, value in init_settings().items()
            if key in {cls.transport_url_field, "allow_insecure_http"}
        }
        return (
            InitSettingsSource(settings_cls, init_kwargs=transport_values),
            *super().settings_customise_sources(
                settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
            ),
        )

    @model_validator(mode="wrap")
    @classmethod
    def resolve_transport_settings(cls, values: Any, handler: ModelWrapValidatorHandler[Any]) -> Any:
        if not isinstance(values, dict):
            return handler(values)
        explicit_consent = values.get("allow_insecure_http")
        server_url, allowed = resolve_client_transport(
            cls.transport_host,
            server_url=values.get(cls.transport_url_field),
            allow_insecure_http=None if explicit_consent is None else parse_client_boolean(explicit_consent),
        )
        resolved = handler({**values, cls.transport_url_field: server_url, "allow_insecure_http": allowed})
        if allowed and explicit_consent is None and _environment_consent(cls.transport_host) is None:
            resolved._saved_consent_endpoint = _endpoint(server_url)
        return resolved

    def resolve_transport(
        self, *, server_url: str | None = None, allow_insecure_http: bool | None = None
    ) -> tuple[str, bool]:
        """Apply invocation overrides without transferring saved consent to a new endpoint."""

        normalized = normalize_client_url(
            server_url if server_url is not None else getattr(self, self.transport_url_field)
        )
        allowed = self.allow_insecure_http if allow_insecure_http is None else parse_client_boolean(allow_insecure_http)
        if (
            allow_insecure_http is None
            and self._saved_consent_endpoint is not None
            and _endpoint(normalized) != self._saved_consent_endpoint
        ):
            allowed = False
        return normalized, allowed


__all__ = [
    "ClientTransportSettings",
    "client_config_file",
    "load_client_settings",
    "normalize_client_url",
    "parse_client_boolean",
    "resolve_client_transport",
]
