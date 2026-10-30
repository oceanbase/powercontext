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

"""Reference final-boundary adapter for the public context protection contract.

The embedding application supplies a trusted target matching the Server's profile,
an authenticated PowerContextClient, and already-rendered JSON plus exact prepared
block locations. Native cache settings must be rendered before this function.
This is a single-request example, not a gateway implementation or compatibility claim.
It does not mask or restore values. Returned bytes still need application-level
validation before their contents become tool actions or persisted facts.

``await execute_checked_json(powercontext=client, target=target, scope_id=scope,
payload=payload, prepared_contexts=locations)`` returns only approved response bytes.
Use HTTPS for a remote Server; a separately authenticated UDS client is also supported.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr, ValidationError

from powercontext.client import PowerContextClient
from powercontext.client.errors import ClientError
from powercontext.http import CheckContextRequest, ContextCheckResult, ModelJsonPayload, PreparedContextLocation


class ExecutionRefused(RuntimeError):
    """A content-free failure; a possible upstream attempt must not be blindly retried."""


@dataclass(frozen=True)
class TrustedTarget:
    """Deployment configuration, never populated from a model-request body.

    Endpoint / model / credentials / region must match the Server's approved profile.
    This example permits plaintext only on loopback for controlled local tests.
    """

    profile_id: str
    endpoint: str
    bearer: SecretStr | None = None
    max_request_bytes: int = 1_048_576
    max_response_bytes: int = 1_048_576
    timeout_seconds: float = 30

    def __post_init__(self) -> None:
        url = urlsplit(self.endpoint)
        try:
            local = ip_address(url.hostname or "").is_loopback
        except ValueError:
            local = url.hostname == "localhost"
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.scheme not in {"https", "http"}
            or (url.scheme == "http" and not local)
            or not self.profile_id
            or not 0 < self.timeout_seconds <= 600
            or not 0 < self.max_request_bytes <= 1_048_576
            or not 0 < self.max_response_bytes <= 1_048_576
        ):
            raise ValueError("invalid trusted execution target")  # noqa: TRY003


def _require_allow(result: ContextCheckResult, phase: str) -> str:
    if (
        result.phase != phase
        or result.decision != "allow"
        or not result.coverage.complete
        or result.binding is None
        or result.valid_until.tzinfo is None
        or result.valid_until <= datetime.now(UTC)
    ):
        raise ExecutionRefused("context_admission_refused")
    return result.binding


async def _exchange(
    target: TrustedTarget, raw_request: bytes, headers: dict[str, str], admission: ContextCheckResult
) -> tuple[int, dict[str, str], bytes]:
    if admission.correlate_until is None or admission.correlate_until.tzinfo is None:
        raise ExecutionRefused("context_admission_refused")
    loop = asyncio.get_running_loop()
    now, started = datetime.now(UTC), loop.time()
    send_deadline = started + (admission.valid_until - now).total_seconds()
    response_deadline = started + (admission.correlate_until - now).total_seconds()
    sent = False
    async with (
        asyncio.timeout_at(send_deadline) as deadline,
        httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0),
            follow_redirects=False,
            timeout=target.timeout_seconds,
            trust_env=False,
        ) as upstream,
    ):

        async def trace_send(name: str, _info: dict[str, object]) -> None:
            nonlocal sent
            if name in {"http11.send_request_body.complete", "http2.send_request_body.complete"}:
                _require_allow(admission, "before_send")
                if loop.time() >= response_deadline:
                    raise ExecutionRefused("context_admission_refused")
                sent = True
                # Only completed writes switch to the independent response budget.
                # Connection and every request write remain under the send permit.
                deadline.reschedule(response_deadline)

        _require_allow(admission, "before_send")
        async with upstream.stream(
            "POST", target.endpoint, content=raw_request, headers=headers, extensions={"trace": trace_send}
        ) as response:
            if not sent:
                raise ExecutionRefused("request_completion_unconfirmed")
            if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                raise ExecutionRefused("response_encoding_unsupported")
            raw_response = bytearray()
            async for chunk in response.aiter_raw():
                if len(raw_response) + len(chunk) > target.max_response_bytes:
                    raise ExecutionRefused("response_too_large")
                raw_response.extend(chunk)
            return response.status_code, {"content-type": response.headers.get("content-type", "")}, bytes(raw_response)


async def execute_checked_json(
    *,
    powercontext: PowerContextClient,
    target: TrustedTarget,
    scope_id: str,
    payload: ModelJsonPayload,
    prepared_contexts: tuple[PreparedContextLocation, ...] = (),
) -> bytes:
    """Check final input, send once, then check the complete raw response before release.

    Keep the returned request binding local to this invocation. Caller cancellation
    propagates. Transport uncertainty never authorizes an automatic paid retry.
    Connection and writes must finish within the send permit; response collection
    uses its separate correlation deadline. Raw errors, partial responses, unknown
    coverage and expired approvals are withheld.
    """
    # Snapshot mutable DTO children before the first await; later caller mutations
    # cannot change the bytes or locations already submitted for admission.
    request = CheckContextRequest.model_validate({
        "scope_id": scope_id,
        "phase": "before_send",
        "target_profile": target.profile_id,
        "payload": payload.model_dump(mode="json", by_alias=True),
        "prepared_contexts": [item.model_dump(mode="json", by_alias=True) for item in prepared_contexts],
    })
    raw_request = request.root.payload.body.encode("utf-8")
    if len(raw_request) > target.max_request_bytes:
        raise ExecutionRefused("request_too_large")
    headers = dict(request.root.payload.headers)
    headers["accept-encoding"] = "identity"
    if target.bearer is not None:
        headers["authorization"] = "Bearer " + target.bearer.get_secret_value()
    try:
        async with asyncio.timeout(target.timeout_seconds):
            admission = await powercontext.check_context(request)
            request_binding = _require_allow(admission, "before_send")
            # No representation changes, fallback or retry occur after approval.
            response_status, response_headers, raw_response = await _exchange(target, raw_request, headers, admission)
            checked = await powercontext.check_context(
                CheckContextRequest.model_validate({
                    "scope_id": scope_id,
                    "phase": "before_release",
                    "target_profile": target.profile_id,
                    "request_binding": request_binding,
                    "completion": "complete",
                    "payload": {
                        "schema": "powercontext.model-json-payload.v1",
                        "headers": response_headers,
                        "body": raw_response.decode("utf-8", errors="strict"),
                    },
                })
            )
            _require_allow(checked, "before_release")
            if not 200 <= response_status < 300:
                raise ExecutionRefused("upstream_http_error")
            return raw_response
    except (TimeoutError, httpx.HTTPError, UnicodeError, ValidationError, ClientError):
        raise ExecutionRefused("execution_unavailable_or_uncertain") from None
