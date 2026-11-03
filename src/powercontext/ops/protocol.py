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

"""Versioned, bounded JSON messages for trusted native and command-line callers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, cast

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
OPERATIONS = {"register", "start", "stop", "restart", "remove_registration", "config_apply", "install", "repair"}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_REQUEST_FIELDS = {
    "inspect": set(),
    "config_validate": set(),
    "preflight": {"operation", "options"},
    "apply": {"operation_id", "plan", "authorization"},
    "resume": {"operation_id", "expected_attempt", "plan", "authorization"},
    "operation_status": {"operation_id"},
    "cancel": {"operation_id", "attempt"},
}


class OpsError(ValueError):
    """A stable code safe for machine responses, without exception interpolation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def decode(data: bytes) -> object:
    """Reject ambiguous duplicate keys and require UTF-8 on the wire."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise OpsError("invalid_request")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except RecursionError as error:
        raise OpsError("invalid_request") from error


def identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise OpsError("invalid_request")
    return value


def integer(value: object) -> int:
    if type(value) is not int or value < 1:
        raise OpsError("invalid_request")
    return value


def object_fields(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise OpsError("invalid_request")
    return cast(dict[str, Any], value)


def request(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpsError("invalid_request")
    value = cast(dict[str, Any], value)
    if type(value.get("protocol_version")) is not int or value.get("protocol_version") != PROTOCOL_VERSION:
        raise OpsError("unsupported_protocol")
    action = value.get("action")
    if not isinstance(action, str) or action not in _REQUEST_FIELDS:
        raise OpsError("unsupported_action")
    object_fields(value, {"protocol_version", "request_id", "action"} | _REQUEST_FIELDS[action])
    identifier(value["request_id"])
    if "operation_id" in value:
        identifier(value["operation_id"])
    for field in ("attempt", "expected_attempt"):
        if field in value:
            integer(value[field])
    if len(canonical(value)) > MAX_MESSAGE_BYTES:
        raise OpsError("request_too_large")
    return value


def options(operation: object, value: object) -> dict[str, Any]:
    if not isinstance(operation, str) or operation not in OPERATIONS or not isinstance(value, dict):
        raise OpsError("invalid_request")
    value = cast(dict[str, Any], value)
    allowed = {"start_on_login"} if operation == "register" else set()
    if operation == "config_apply":
        allowed = {"http_port", "dashboard_enabled", "start_on_login", "restart"}
    elif operation == "install":
        allowed = {"http_port", "start_on_login"}
    if not set(value) <= allowed:
        raise OpsError("invalid_request")
    for name, item in value.items():
        if name == "http_port":
            if type(item) is not int or not 1 <= item <= 65535:
                raise OpsError("invalid_request")
        elif type(item) is not bool:
            raise OpsError("invalid_request")
    return dict(value)


def plan(value: object) -> dict[str, Any]:
    installation = isinstance(value, dict) and value.get("operation") in ("install", "repair")
    result = object_fields(
        value,
        {
            "protocol_version",
            "target_id",
            "binding_id",
            "operation",
            "options",
            "before",
            "effects",
            "target_summary",
            "plan_id",
        }
        | ({"installation"} if installation else set()),
    )
    if type(result["protocol_version"]) is not int or result["protocol_version"] != PROTOCOL_VERSION:
        raise OpsError("unsupported_protocol")
    identifier(result["target_id"])
    options(result["operation"], result["options"])
    for field in ("binding_id", "before", "plan_id"):
        if not isinstance(result[field], str) or re.fullmatch(r"[a-f0-9]{64}", result[field]) is None:
            raise OpsError("invalid_request")
    effects = object_fields(result["effects"], {"may_start", "preserves_data"})
    if type(effects["may_start"]) is not bool or effects["preserves_data"] is not True:
        raise OpsError("invalid_request")
    _validate_target_summary(result["target_summary"])
    if installation:
        _validate_installation_plan(result["installation"])
    if result["plan_id"] != digest({key: item for key, item in result.items() if key != "plan_id"}):
        raise OpsError("plan_invalid")
    return result


def _validate_installation_plan(value: object) -> None:
    facts = object_fields(
        value,
        {
            "catalog_sha256",
            "resource",
            "layout",
            "installation_id",
            "classification",
            "resources",
            "resource_identity",
            "configuration",
            "database",
            "service_before",
            "space_required",
            "verify_running",
            "preserve_stopped",
            "repair_owner",
            "registration_present",
        }
        | ({"registration_removal"} if isinstance(value, dict) and "registration_removal" in value else set()),
    )
    for field in ("catalog_sha256", "service_before"):
        if not isinstance(facts[field], str) or re.fullmatch(r"[a-f0-9]{64}", facts[field]) is None:
            raise OpsError("invalid_request")
    for field in ("repair_owner", "installation_id", "registration_removal"):
        if facts.get(field) is not None:
            identifier(facts[field])
    if facts["classification"] not in ("missing", "owned_current", "owned_repairable", "pending_operation") or facts[
        "resources"
    ] not in ("missing", "damaged", "verified"):
        raise OpsError("invalid_request")
    if (
        type(facts["space_required"]) is not int
        or facts["space_required"] < 0
        or type(facts["verify_running"]) is not bool
        or type(facts["preserve_stopped"]) is not bool
        or type(facts["registration_present"]) is not bool
    ):
        raise OpsError("invalid_request")
    for field in ("resource", "layout", "configuration", "database"):
        if not isinstance(facts[field], dict):
            raise OpsError("invalid_request")
    if facts["resource_identity"] is not None and not isinstance(facts["resource_identity"], dict):
        raise OpsError("invalid_request")


def _validate_target_summary(value: object) -> None:
    summary = object_fields(
        value,
        {"python_executable", "package_version", "env_file", "endpoint", "data_dir", "start_on_login"},
    )
    for name in ("python_executable", "package_version", "env_file"):
        if not isinstance(summary[name], str) or not summary[name]:
            raise OpsError("invalid_request")
    for name in ("endpoint", "data_dir"):
        if summary[name] is not None and not isinstance(summary[name], str):
            raise OpsError("invalid_request")
    if type(summary["start_on_login"]) is not bool:
        raise OpsError("invalid_request")


def authorize(value: object, approved_plan: dict[str, Any]) -> None:
    approval = object_fields(value, {"plan_id", "approved"})
    if approval["approved"] is not True or approval["plan_id"] != approved_plan["plan_id"]:
        raise OpsError("authorization_required")
