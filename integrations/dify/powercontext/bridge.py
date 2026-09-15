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

"""PowerContext's typed HTTP boundary for Dify tools.

Identity is supplied by the host through a non-LLM parameter. It selects an
existing binding; it never grants access and never falls back to the default Scope.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from powercontext.client import PowerContextClient
from powercontext.client.capture import render_capture_event
from powercontext.http import (
    CaptureContentSourceRequest,
    FlushMemoryRequest,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    ResolveScopeBindingRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    ScopeBindingKey,
    SearchMemoryRequest,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    base_url: str = Field(min_length=1)
    token: SecretStr = Field(min_length=1)
    namespace: str = Field(min_length=1, max_length=128)
    scope_id: str | None = None
    allow_insecure_http: bool = False


class MemoryIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    app_id: str = Field(min_length=1, max_length=256)
    subject_kind: Literal["user", "business"] = "user"
    subject_id: str = Field(min_length=1, max_length=256)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    event_id: str = Field(min_length=1, max_length=256)
    event: Literal["user_prompt", "model_response", "tool_call", "tool_result", "run_end"]
    sequence: int = Field(ge=1)
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    max_bytes: int = Field(default=8192, ge=512, le=32768)


def binding_key(namespace: str, identity: MemoryIdentity) -> ScopeBindingKey:
    """Return an external binding key, not a synthesized Scope ID."""
    value = [namespace, identity.app_id, identity.subject_kind, identity.subject_id]
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return ScopeBindingKey(integration="dify", kind=identity.subject_kind, external_id=digest)


async def execute(
    client: PowerContextClient,
    connection: Connection,
    identity: MemoryIdentity,
    operation: str,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute one operation after resolving a host-controlled Scope."""
    if "scope_id" in request:
        raise ValueError("scope_id is a host setting, not a tool argument")  # noqa: TRY003
    scope = await client.resolve_scope_binding(
        ResolveScopeBindingRequest(
            explicit_scope_id=connection.scope_id or None,
            binding_keys=[binding_key(connection.namespace, identity)],
            allow_default=False,
        )
    )
    arguments = {**request, "scope_id": scope.scope_id}
    if operation == "prepare_context":
        result = await client.prepare_context(PrepareContextRequest.model_validate(arguments))
    elif operation == "capture_event":
        observation = Observation.model_validate(request)
        source_key = [scope.scope_id, observation.event_id]
        source_id = "dify:" + hashlib.sha256(json.dumps(source_key).encode()).hexdigest()
        content = render_capture_event(
            observation.event,
            observation.sequence,
            redact_token(observation.payload, connection.token.get_secret_value()),
            observation.max_bytes,
            schema="powercontext.dify-capture-event.v1",
        )
        result = await client.capture_content_source(
            CaptureContentSourceRequest(
                scope_id=scope.scope_id,
                source_id=source_id,
                content=content,
                metadata={
                    "origin": "dify",
                    "event": observation.event,
                    "event_id": observation.event_id,
                    "sequence": observation.sequence,
                    "app_id": identity.app_id,
                },
            )
        )
    elif operation == "search_memory":
        result = await client.search_memory(SearchMemoryRequest.model_validate(arguments))
    elif operation == "remember_memory":
        result = await client.remember_memory(RememberMemoryRequest.model_validate(arguments))
    elif operation == "get_memory_entry":
        result = await client.get_memory_entry(GetMemoryEntryRequest.model_validate(arguments))
    elif operation == "revise_memory_entry":
        result = await client.revise_memory_entry(ReviseMemoryEntryRequest.model_validate(arguments))
    elif operation == "retire_memory_entry":
        result = await client.retire_memory_entry(RetireMemoryEntryRequest.model_validate(arguments))
    elif operation == "flush_memory":
        result = await client.flush_memory(FlushMemoryRequest.model_validate(arguments))
    else:
        raise ValueError("unsupported PowerContext operation")  # noqa: TRY003
    return result.model_dump(mode="json", by_alias=True)


def redact_token(value: Any, token: str) -> Any:
    if isinstance(value, str):
        return value.replace(token, "[REDACTED]")
    if isinstance(value, dict):
        return {key: redact_token(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_token(item, token) for item in value]
    return value
