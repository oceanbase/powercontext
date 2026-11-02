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

"""Explicit Server opt-in to an installed local observability deployment."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from importlib.util import find_spec

from powercontext.cli.env_file import read_environment_file
from powercontext.observability.deployment import ObservabilityError, load_state, state_directory


def profile_environment(existing: Mapping[str, str]) -> dict[str, str]:
    """Load telemetry defaults without replacing deployment-owned business settings."""
    directory = state_directory()
    if (directory / "connection.json").exists():
        from powercontext.observability.connection import load_connection

        if (directory / "state.json").exists():
            raise ObservabilityError(  # noqa: TRY003
                "Managed and external configuration conflict; use separate observability directories"
            )
        load_connection(directory)
    else:
        load_state(directory)
    try:
        exporter_available = find_spec("opentelemetry.exporter.otlp.proto.http") is not None
    except ModuleNotFoundError:
        exporter_available = False
    if not exporter_available:
        raise ObservabilityError("OTLP export is not installed; install powercontext[cli,server,tracing-otlp]")  # noqa: TRY003
    profile = read_environment_file(directory / "server.env")
    file_values = _explicit_telemetry(existing)
    process_values = _explicit_telemetry(os.environ)
    for key, value in profile.items():
        if not key.startswith("OTEL_") and key not in {
            "POWERCONTEXT_SERVER_TRACING_ENABLED",
            "POWERCONTEXT_SERVER_LOGGING_DIRECTORY",
            "POWERCONTEXT_SERVER_LOGGING_FORMAT",
            "POWERCONTEXT_SERVER_LOGGING_OTLP_ENABLED",
        }:
            raise ObservabilityError(  # noqa: TRY003
                "Observability profile contains non-telemetry settings; restore the generated profile"
            )
        if any(
            configured is not None and configured != value
            for configured in (file_values.get(key), process_values.get(key))
        ):
            # Never print configured values; exporter headers may contain credentials.
            raise ObservabilityError(  # noqa: TRY003
                f"Explicit {key} conflicts with the observability profile; remove the conflict or configure telemetry manually"
            )
    explicit_trace_overrides = {
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT",
        "OTEL_SDK_DISABLED",
    }
    if explicit_trace_overrides & (file_values.keys() | process_values.keys()):
        raise ObservabilityError(  # noqa: TRY003
            "Signal-specific OTLP settings or OTEL_SDK_DISABLED conflict with managed observability; configure telemetry manually"
        )
    return {**profile, **existing}


def _explicit_telemetry(environment: Mapping[str, str]) -> dict[str, str]:
    """Recognize both JSON objects and nested env keys accepted by Server settings."""
    result = {key.upper(): value for key, value in environment.items()}
    for parent, child in (
        ("POWERCONTEXT_SERVER_TRACING", "enabled"),
        ("POWERCONTEXT_SERVER_LOGGING", "directory"),
        ("POWERCONTEXT_SERVER_LOGGING", "format"),
        ("POWERCONTEXT_SERVER_LOGGING", "otlp_enabled"),
    ):
        if parent not in result:
            continue
        try:
            settings = json.loads(result[parent])
        except ValueError as error:
            message = f"Invalid {parent} JSON settings; correct the existing configuration before opting in"
            raise ObservabilityError(message) from error
        if isinstance(settings, dict) and child in settings and settings[child] is not None:
            nested_key = parent + "_" + child.upper()
            value = settings[child]
            encoded = str(value).lower() if isinstance(value, bool) else str(value)
            # An explicit nested env key has precedence over a JSON parent object.
            result.setdefault(nested_key, encoded)
    return result
