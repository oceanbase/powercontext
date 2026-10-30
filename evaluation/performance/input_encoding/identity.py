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

"""Private, per-output configuration identities for resumable experiments.

Call inside ``server_settings_context``, before opening any provider or database.
The returned mapping is safe to place in the public experiment manifest. Keep
``.config-identity.key`` private and out of shared result archives: anyone with
that key could test guesses against the otherwise opaque HMAC digest.

Only the OpenAI and Anthropic environment credential paths inspected in the
current runtime are supported. Cloud credential chains need explicit coverage
before being accepted; a fingerprint of a credential-file path is insufficient.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretBytes, SecretStr
from pydantic_core import to_jsonable_python

from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig

IDENTITY_SCHEMA = "powercontext.private-config-identity/hmac-sha256-v1"
KEY_FILENAME = ".config-identity.key"

_PROVIDER_ENVIRONMENT = {
    "openai": (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "OPENAI_CUSTOM_HEADERS",
    ),
    "anthropic": (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
    ),
}
_TRANSPORT_ENVIRONMENT = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


class ConfigurationIdentityError(ValueError):
    """A safe diagnostic that never includes configuration values."""


def _canonical(value: Any, *, private: bool) -> Any:
    if isinstance(value, (SecretStr, SecretBytes)):
        return _canonical(value.get_secret_value(), private=True) if private else "**********"
    if isinstance(value, BaseModel):
        # Read declared values directly: model serializers may already have
        # replaced secrets before a recursive serializer could see them.
        values = {name: getattr(value, name) for name in type(value).model_fields}
        values.update(value.model_extra or {})
        return _canonical(values, private=private)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ConfigurationIdentityError("Configuration keys must be strings")  # noqa: TRY003
        return {key: _canonical(item, private=private) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item, private=private) for item in value]
    if isinstance(value, Enum):
        return _canonical(value.value, private=private)
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    return to_jsonable_python(value)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _reject_anthropic_discovery(inference: InferenceConfig, environment: Mapping[str, str]) -> None:
    # The SDK discovers profiles only when both static credential arguments and
    # their environment values are None. Profile contents are outside this identity.
    if any(environment.get(name) is not None for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")):
        return
    generation_headers = {
        name.casefold(): value.get_secret_value() for name, value in inference.generation_headers.items()
    }
    for operation in ("generation", "rerank", "decision"):
        model = getattr(inference, f"{operation}_model")
        headers = {
            name.casefold(): value.get_secret_value()
            for name, value in getattr(inference, f"{operation}_headers").items()
        }
        if model is None:
            model = inference.generation_model
            headers = {**generation_headers, **headers}
        if (
            model is not None
            and model.startswith("anthropic:")
            and "authorization" in headers
            and not headers.get("x-api-key")
        ):
            raise ConfigurationIdentityError(  # noqa: TRY003
                "Anthropic profile discovery is not supported; configure a static credential"
            )


def _provider_environment(
    configurations: Mapping[str, BuiltinConfig | InferenceConfig], environment: Mapping[str, str]
) -> dict[str, str | None]:
    names: set[str] = set()
    for config in configurations.values():
        inference = config.inference if isinstance(config, BuiltinConfig) else config
        _reject_anthropic_discovery(inference, environment)
        for model in (
            inference.generation_model,
            inference.embedding_model,
            inference.rerank_model,
            inference.decision_model,
        ):
            if model is None:
                continue
            provider = model.split(":", 1)[0]
            if provider in {"openai-chat", "openai-responses"}:
                provider = "openai"
            if provider not in _PROVIDER_ENVIRONMENT:
                raise ConfigurationIdentityError("Provider credential identity is not supported")  # noqa: TRY003
            names.update(_PROVIDER_ENVIRONMENT[provider])
            names.update(_TRANSPORT_ENVIRONMENT)
    # Presence and absence both matter. Read after the environment overlay has
    # been applied, rather than hashing the original .env file or its filename.
    return {name: environment.get(name) for name in sorted(names)}


def _read_key(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise ConfigurationIdentityError("Configuration identity key must be a private owner-only file")  # noqa: TRY003
        key = stream.read(33)
    if len(key) != 32:
        raise ConfigurationIdentityError("Configuration identity key is invalid")  # noqa: TRY003
    return key


def _key(directory: Path, *, resume: bool) -> bytes:
    path = directory / KEY_FILENAME
    if not resume:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
    return _read_key(path)


def prepare_configuration_identity(
    directory: Path,
    configurations: Mapping[str, BuiltinConfig | InferenceConfig],
    *,
    previous: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Create an identity, or reject a changed/legacy resume before service calls.

    Pass the existing manifest's ``configuration_identity`` as ``previous``.
    A manifest without that field remains reportable but cannot be resumed.
    ``environment`` defaults to the effective process environment; the explicit
    argument allows controlled local verification without reading credentials.
    Values and key material are never returned, written to JSON, or put in errors.
    """
    if previous is None and (directory / "manifest.json").exists():
        raise ConfigurationIdentityError("Existing run lacks private configuration identity; use a new output")  # noqa: TRY003
    if previous is not None and previous.get("schema") != IDENTITY_SCHEMA:
        raise ConfigurationIdentityError("Existing configuration identity is unsupported; use a new output")  # noqa: TRY003
    try:
        provider_environment = _provider_environment(configurations, os.environ if environment is None else environment)
        private_payload = _json_bytes({
            "schema": IDENTITY_SCHEMA,
            "configurations": _canonical(configurations, private=True),
            "provider_environment": provider_environment,
        })
        masked_payload = _json_bytes(_canonical(configurations, private=False))
        key = _key(directory, resume=previous is not None)
        identity = {
            "schema": IDENTITY_SCHEMA,
            "private_config_digest": hmac.digest(key, private_payload, "sha256").hex(),
            "masked_config_hash": hashlib.sha256(masked_payload).hexdigest(),
        }
    except ConfigurationIdentityError:
        raise
    except Exception:
        raise ConfigurationIdentityError("Configuration identity could not be verified") from None  # noqa: TRY003
    if previous is not None and (
        previous.get("masked_config_hash") != identity["masked_config_hash"]
        or not isinstance(previous.get("private_config_digest"), str)
        or not hmac.compare_digest(previous["private_config_digest"], identity["private_config_digest"])
    ):
        raise ConfigurationIdentityError("Private configuration changed; use a new output directory")  # noqa: TRY003
    return identity
