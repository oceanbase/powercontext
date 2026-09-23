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

"""Bounded operations shared by Python hosts and the TypeScript bridge."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from time import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from powercontext.client import PowerContextClient
from powercontext.client.errors import InvalidResponseError, ResponseReadError, ServerResponseError, TransportError
from powercontext.client.integration.models import HookRequest, HookResult
from powercontext.client.operations import OPERATIONS, WRITE_OPERATIONS

MAX_MESSAGE_BYTES = 1_048_576


async def execute(
    request: HookRequest,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    on_headers: Callable[[int, str | None], None] | None = None,
) -> HookResult:
    """Execute exactly once. Scope and capture consent belong to the adapter."""

    if request.deadline <= time():
        return HookResult(id=request.id, outcome="failed", error="deadline")
    observed: dict[str, Any] = {}
    notify = on_headers or (lambda _status, _request_id: None)

    async def observe(response: httpx.Response) -> None:
        observed.update(status_code=response.status_code, request_id=response.headers.get("X-PowerContext-Request-ID"))
        notify(observed["status_code"], observed["request_id"])
        await _read_response(response)

    try:
        async with asyncio.timeout(min(request.deadline - time(), request.connection.request_timeout)):
            headers = {}
            if request.connection.authorization is not None:
                headers["Authorization"] = request.connection.authorization.get_secret_value()
            async with (
                httpx.AsyncClient(
                    headers=headers,
                    timeout=request.connection.request_timeout,
                    follow_redirects=False,
                    **({"transport": transport} if transport is not None else {}),
                    event_hooks={"response": [observe]},
                ) as http,
                PowerContextClient(
                    request.connection.base_url,
                    http_client=http,
                    timeout=request.connection.request_timeout,
                    allow_insecure_http=request.connection.allow_insecure_http,
                ) as owned,
            ):
                return await _invoke(request, owned)
    except (TimeoutError, TransportError) as error:
        return _transport_failure(request, error, observed)
    except ServerResponseError as error:
        return HookResult(
            id=request.id,
            outcome="unknown" if error.outcome == "unknown" else "failed",
            error="server",
            status_code=error.status_code,
            code=error.code,
            message=error.server_message,
            request_id=error.request_id,
        )
    except InvalidResponseError as error:
        return HookResult(
            id=request.id,
            outcome="unknown" if request.operation in WRITE_OPERATIONS else "failed",
            error="invalid_response",
            status_code=observed.get("status_code"),
            request_id=error.request_id,
        )
    except (ValidationError, KeyError, TypeError):
        return HookResult(id=request.id, outcome="failed", error="invalid_request")
    except ValueError:
        return HookResult(id=request.id, outcome="failed", error="configuration")


async def _read_response(response: httpx.Response) -> None:
    """Bound Hook responses while leaving caller-owned SDK transport policy intact."""
    content = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > MAX_MESSAGE_BYTES:
                raise ResponseReadError(
                    response.url.path,
                    status_code=response.status_code,
                    request_id=response.headers.get("X-PowerContext-Request-ID"),
                    body_error="response_too_large",
                )
            content.extend(chunk)
    except httpx.HTTPError as error:
        raise ResponseReadError(
            response.url.path,
            status_code=response.status_code,
            request_id=response.headers.get("X-PowerContext-Request-ID"),
            body_error="request_timeout" if isinstance(error, httpx.TimeoutException) else "connection_failed",
        ) from error
    response._content = bytes(content)


def _transport_failure(request: HookRequest, error: Exception, observed: dict[str, Any]) -> HookResult:
    status = getattr(error, "status_code", None) or observed.get("status_code")
    body_error = getattr(error, "body_error", None)
    if body_error is None and status is not None:
        body_error = "request_timeout" if isinstance(error, TimeoutError) else "connection_failed"
    return HookResult(
        id=request.id,
        outcome="unknown" if request.operation in WRITE_OPERATIONS and status not in {401, 403} else "failed",
        error=(
            "deadline"
            if isinstance(error, TimeoutError)
            else "invalid_response"
            if body_error == "response_too_large"
            else "transport"
        ),
        status_code=status,
        request_id=getattr(error, "request_id", None) or observed.get("request_id"),
        body_error=body_error,
    )


async def _invoke(request: HookRequest, client: PowerContextClient) -> HookResult:
    metadata = {}
    value = await client.request_operation(
        request.operation,
        request.arguments,
        metadata=metadata,
        readiness_response=request.readiness_response,
    )
    kind = "json"
    if isinstance(value, bytes):
        payload = base64.b64encode(value).decode("ascii")
        kind = "bytes"
    elif isinstance(value, str):
        payload = value
        kind = "text"
    elif isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", by_alias=True)
    else:
        payload = value
    outcome = (
        "empty"
        if request.operation == "prepare_context" and isinstance(payload, dict) and payload.get("status") == "empty"
        else "ok"
    )
    return HookResult(
        id=request.id,
        outcome=outcome,
        value=payload,
        kind=kind,
        status_code=metadata.get("status_code", OPERATIONS[request.operation].success_status),
        request_id=metadata.get("request_id"),
        etag=metadata.get("etag"),
    )
