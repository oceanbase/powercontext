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

"""Bounded synchronous transport and validation of the selected public contract."""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft7Validator, FormatChecker
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator

CONTRACT = json.loads(Path(__file__).with_name("contract.json").read_text(encoding="utf-8"))
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
GENERATION_OPERATIONS = {"activate_handoff", "prepare_handoff", "generate_experience", "generate_skill"}
WRITE_OPERATIONS = {
    "remember_memory",
    "revise_memory_entry",
    "retire_memory_entry",
    "capture_content_source",
    "activate_handoff",
    "commit_handoff",
    "generate_experience",
    "generate_skill",
    "replace_artifact",
    "change_atomic_memory_lifecycle",
}
MESSAGES = {
    "invalid_configuration": "Check the Server URL, token, Scope/binding choice, and configured limits.",
    "invalid_request": "Parameters do not match this tool's public contract.",
    "sensitive_content": "Remove credentials or other detected secrets before capturing this Source.",
    "authentication_failed": "The Server rejected this credential.",
    "forbidden": "This credential cannot access the requested Scope or resource.",
    "not_found": "The configured Scope, binding, or referenced resource was not found.",
    "conflict": "The exact reference/version conflicts. Read the current state and verify the intended change.",
    "server_unavailable": "The Server request could not be completed.",
    "invalid_response": "The Server returned an unexpected status or response structure.",
    "response_too_large": "The complete response exceeds the plugin transport limit.",
    "unconfirmed_write": "The write outcome cannot be confirmed. Check Server state before deciding to retry.",
}


class PluginError(Exception):
    def __init__(
        self,
        code: str,
        *,
        http_status: int | None = None,
        request_id: str | None = None,
        unconfirmed: bool = False,
        data: dict | None = None,
    ):
        self.code = code
        self.http_status = http_status
        self.request_id = request_id
        self.unconfirmed = unconfirmed
        self.data = data
        super().__init__(MESSAGES[code])


def decode_json(value: str | bytes):
    def reject_constant(_value):
        raise ValueError("Non-finite JSON number")

    return json.loads(value, parse_constant=reject_constant)


def validate(schema: dict, value: Any, *, code: str = "invalid_request"):
    root = {**schema, "components": CONTRACT["components"]}
    if not Draft7Validator(root, format_checker=FormatChecker()).is_valid(value):
        raise PluginError(code)
    # JSON Schema's number type alone does not reject NaN/Infinity.
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise PluginError(code) from error


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_url: str
    api_token: SecretStr
    scope_id: str | None = None
    binding_external_id: str | None = None
    max_bytes: int = Field(default=8000, ge=512, le=32768)
    timeout_seconds: float = Field(default=10, ge=0.1, le=120, allow_inf_nan=False)
    generation_timeout_seconds: float = Field(default=120, ge=0.1, le=600, allow_inf_nan=False)
    allow_insecure_http: bool = False
    context_assembly: str | dict | None = None

    @model_validator(mode="after")
    def check_configuration(self):
        self.scope_id = self.scope_id.strip() if self.scope_id else None
        self.binding_external_id = self.binding_external_id.strip() if self.binding_external_id else None
        if bool(self.scope_id) == bool(self.binding_external_id):
            raise ValueError("Choose one Scope configuration")
        if not self.api_token.get_secret_value().strip():
            raise ValueError("Token required")
        normalized_url = str(AnyHttpUrl(self.server_url))
        parsed = urlsplit(normalized_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid Server URL")
        _ = parsed.port
        loopback = parsed.hostname == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme == "http" and not (loopback or self.allow_insecure_http):
            raise ValueError("Explicit HTTP opt-in required")
        self.server_url = normalized_url.rstrip("/")
        if self.context_assembly == "":
            self.context_assembly = None
        if isinstance(self.context_assembly, str):
            self.context_assembly = decode_json(self.context_assembly)
            if self.context_assembly is None:
                raise ValueError("Context assembly must be an object")
        if self.context_assembly is not None:
            validate({"$ref": "#/components/schemas/ContextAssembly"}, self.context_assembly)
        return self

    @classmethod
    def from_credentials(cls, credentials: dict):
        try:
            return cls.model_validate(credentials)
        except (ValueError, TypeError, PluginError) as error:
            raise PluginError("invalid_configuration") from error


class Client:
    def __init__(self, connection: Connection):
        self.connection = connection

    def call(
        self,
        operation: str,
        payload: dict | None = None,
        *,
        scope_id: str | None = None,
        headers: dict[str, str] | None = None,
        include_etag: bool = False,
    ):
        definition = CONTRACT["operations"][operation]
        path = definition["path"]
        request = dict(payload or {})
        for parameter in definition["parameters"]:
            if parameter["in"] != "path":
                continue
            name = parameter["name"]
            value = request.pop(name, scope_id if name == "scope_id" else None)
            validate(parameter["schema"], value)
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        for parameter in definition["parameters"]:
            if parameter["in"] == "header" and headers is not None and parameter["name"] in headers:
                validate(parameter["schema"], headers[parameter["name"]])
        if definition["request"] is not None:
            validate(definition["request"], request)
        timeout = (
            self.connection.generation_timeout_seconds
            if operation in GENERATION_OPERATIONS
            else self.connection.timeout_seconds
        )
        request_id = None
        status = None
        unconfirmed = operation in WRITE_OPERATIONS
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                headers={
                    "Authorization": "Bearer " + self.connection.api_token.get_secret_value(),
                    "Accept": "application/json",
                    **(headers or {}),
                },
            ) as transport:
                with transport.stream(
                    definition["method"],
                    self.connection.server_url + path,
                    json=request if definition["request"] is not None else None,
                ) as response:
                    status = response.status_code
                    raw_id = response.headers.get("x-powercontext-request-id")
                    etag = response.headers.get("ETag")
                    if raw_id and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", raw_id):
                        request_id = raw_id
                    chunks = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise PluginError(
                                "response_too_large", unconfirmed=unconfirmed, http_status=status, request_id=request_id
                            )
                        chunks.append(chunk)
                    try:
                        data = decode_json(b"".join(chunks))
                    except (ValueError, UnicodeError) as error:
                        raise PluginError(
                            "invalid_response", unconfirmed=unconfirmed, http_status=status, request_id=request_id
                        ) from error
            if str(status) not in definition["responses"]:
                code = {
                    401: "authentication_failed",
                    403: "forbidden",
                    404: "not_found",
                    409: "conflict",
                    412: "conflict",
                    422: "invalid_request",
                    503: "server_unavailable",
                }.get(status, "invalid_response")
                # A valid explicit rejection confirms failure. A corrupt/5xx response may follow a write.
                valid_error = Draft7Validator(
                    {
                        "$ref": "#/components/schemas/ErrorResponse",
                        "components": CONTRACT["components"],
                    }
                ).is_valid(data)
                partial = partial_evidence(data) if valid_error else None
                raise PluginError(
                    code,
                    http_status=status,
                    request_id=request_id,
                    data=partial,
                    unconfirmed=unconfirmed and (not valid_error or status >= 500),
                )
            validate(definition["responses"][str(status)], data, code="invalid_response")
            if include_etag and etag is not None:
                data = {**data, "etag": etag}
            if operation == "prepare_context":
                content = data["content"]
                if (
                    data["content_bytes"] != len((content or "").encode("utf-8"))
                    or data["content_bytes"] > self.connection.max_bytes
                    or (data["status"] == "ready" and not content)
                    or (data["status"] == "empty" and (content is not None or data["content_bytes"] != 0))
                ):
                    raise PluginError("invalid_response")
            return data
        except PluginError as error:
            if error.code == "invalid_response" and status is not None and str(status) in definition["responses"]:
                error.unconfirmed = unconfirmed
                error.http_status = status
                error.request_id = request_id
            raise
        except httpx.HTTPError as error:
            raise PluginError(
                "server_unavailable", unconfirmed=unconfirmed, http_status=status, request_id=request_id
            ) from error

    def resolve_scope(self):
        payload = {"allow_default": False}
        if self.connection.scope_id:
            payload["explicit_scope_id"] = self.connection.scope_id
        else:
            payload["binding_keys"] = [
                {
                    "integration": "dify",
                    "kind": "configured-scope",
                    "external_id": self.connection.binding_external_id,
                }
            ]
        scope_id = self.call("resolve_scope_binding", payload)["scope_id"]
        if self.connection.scope_id is not None and scope_id != self.connection.scope_id:
            raise PluginError("invalid_response")
        return scope_id

    def call_memory(self, operation: str, request: dict):
        """Translate maintained Memory tool names at their identity and CAS boundary."""
        scope_id = request["scope_id"]
        if operation == "list_memory_entries":
            return self.call(
                "list_atomic_memories",
                {
                    "scope_id": scope_id,
                    "states": request["states"]
                    if "states" in request
                    else (["active", "forgotten", "merged", "retired"] if request["include_inactive"] else ["active"]),
                    "limit": request["limit"],
                    "cursor": request.get("cursor"),
                },
            )
        if operation not in {"get_memory_entry", "revise_memory_entry", "retire_memory_entry"}:
            return self.call(operation, request)
        artifact = request["artifact"]
        identity = {"scope_id": scope_id, "family": artifact["family"], "artifact_id": artifact["artifact_id"]}
        if operation == "get_memory_entry":
            head = self.call("get_artifact", identity, include_etag=True)
            if head["revision"] == artifact["revision"]:
                return head
            return self.call("get_artifact_revision", {**identity, "revision": artifact["revision"]})
        if operation == "revise_memory_entry":
            if request["if_match"] != f'"revision:{artifact["revision"]}"':
                raise PluginError("invalid_request")
            return self.call(
                "replace_artifact",
                {
                    **identity,
                    "content": {"kind": request["kind"], "text": request["text"]},
                },
                headers={"If-Match": request["if_match"]},
            )
        return self.call(
            "change_atomic_memory_lifecycle",
            {
                "scope_id": scope_id,
                "target": {"artifact": artifact, "state_version": request["state_version"]},
                "state": "forgotten",
            },
        )

    def validate_credentials(self):
        scope_id = self.resolve_scope()
        self.call("get_scope", scope_id=scope_id)


def partial_evidence(data):
    """Retain only known, structurally valid resource receipts from an error."""
    details = data.get("error", {}).get("details")
    if not isinstance(details, dict):
        return None
    result = {}
    for key, schema in {
        "source": "SourceReference",
        "revision": "ArtifactReference",
        "artifact": "ArtifactReference",
    }.items():
        if key in details:
            try:
                validate({"$ref": "#/components/schemas/" + schema}, details[key])
            except PluginError:
                continue
            result[key] = details[key]
    if isinstance(details.get("candidate_id"), str) and re.fullmatch(r"[\x21-\x7e]{1,128}", details["candidate_id"]):
        result["candidate_id"] = details["candidate_id"]
    return result or None
