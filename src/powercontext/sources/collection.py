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

"""Collection target keys and parameter validation, shared by the central Server and every runner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from pydantic import JsonValue

# Checkpoints of collection targets are keyed by target; the plugin version is not part of their identity.
TARGET_CHECKPOINT_VERSION = "target"


class InvalidTargetParameters(ValueError):
    """Target parameters do not match the capability's schema or identity."""


def normalize(value: str, steps: Sequence[str]) -> str:
    for step in steps:
        if step == "strip":
            value = value.strip()
        elif step == "lower":
            value = value.lower()
        elif step == "collapse_spaces":
            value = " ".join(value.split())
        elif step.startswith("remove_prefix:"):
            value = value.removeprefix(step.split(":", 1)[1])
        elif step.startswith("remove_suffix:"):
            value = value.removesuffix(step.split(":", 1)[1])
        else:
            raise InvalidTargetParameters(f"unknown normalization step {step}")  # noqa: TRY003
    return value


def target_key(capability: str, parameters: Mapping[str, JsonValue], identity: Mapping[str, Sequence[str]]) -> str:
    """SHA-256 of the capability and each identity parameter's normalized value, ordered by parameter name."""

    values = {}
    for name in sorted(identity):
        value = parameters.get(name)
        if not isinstance(value, str):
            raise InvalidTargetParameters(f"identity parameter {name} must be a string")  # noqa: TRY003
        values[name] = normalize(value, identity[name])
    encoded = json.dumps([capability, values], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_parameters(schema: Mapping[str, JsonValue], parameters: Mapping[str, JsonValue]) -> None:
    from jsonschema import Draft202012Validator

    error = next(Draft202012Validator(dict(schema)).iter_errors(dict(parameters)), None)
    if error is not None:
        field = ".".join(str(part) for part in error.absolute_path) or "parameters"
        raise InvalidTargetParameters(f"{field}: {error.message}")  # noqa: TRY003
