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

"""The final HTTP send and raw-response release boundary for owned providers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

import httpx
from typing_extensions import override

from powercontext.builtin.inference.carrier_checks import inspect_sse
from powercontext.builtin.inference.errors import InferencePrivacyError
from powercontext.builtin.inference.model_payload import chat_stream_usage, response_usage
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.privacy import (
    InferenceProtectionConfig,
    PrivacyDecision,
    PrivacyPolicy,
)
from powercontext.builtin.inference.usage import record_approved_usage


def _relevant_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value for key, value in headers.items() if key in {"content-type", "anthropic-version", "anthropic-beta"}
    }


class ProtectedAsyncTransport(httpx.AsyncBaseTransport):
    """Check the actual HTTP request and buffer raw output before SDK access.

    A protected client must disable SDK retries. Transport errors after entering
    the delegate are reported as uncertain, never automatically replayed here.
    """

    def __init__(
        self,
        config: InferenceProtectionConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        observer: Callable[[PrivacyDecision], None] | None = None,
    ) -> None:
        self.policy = PrivacyPolicy(config)
        self._transport = transport if transport is not None else httpx.AsyncHTTPTransport(retries=0)
        self._slots = asyncio.Semaphore(config.max_concurrent_responses)
        self._observer = observer

    def _assess(self, decision: PrivacyDecision, *, usage: InferenceUsage | None = None) -> None:
        if self._observer is not None:
            # Optional diagnostics never alter admission or cancellation.
            with suppress(Exception):
                self._observer(decision)
        if self.policy.enforcing and decision.decision != "allow":
            raise InferencePrivacyError(decision.phase, decision.decision, decision.reason_codes, usage=usage)

    async def _request_body(self, request: httpx.Request) -> bytes | None:
        try:
            return request.content
        except httpx.RequestNotRead:
            if not self.policy.enforcing:
                self._assess(self.policy.decision("before_send", "withhold", "request_stream_uninspected"))
                return None
        if not isinstance(request.stream, httpx.AsyncByteStream):
            self._assess(self.policy.decision("before_send", "withhold", "request_stream_unsupported"))
            return None
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in request.stream:
                size += len(chunk)
                if size > self.policy.config.max_payload_bytes:
                    self._assess(self.policy.decision("before_send", "withhold", "payload_too_large"))
                chunks.append(chunk)
        finally:
            await request.stream.aclose()
        body = b"".join(chunks)
        request.stream = httpx.ByteStream(body)
        return body

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - send and release have distinct failure semantics
        if self.policy.config.mode == "disabled":
            return await self._transport.handle_async_request(request)
        body = await self._request_body(request)
        if body is None:
            return await self._transport.handle_async_request(request)
        if len(body) > self.policy.config.max_payload_bytes:
            self._assess(self.policy.decision("before_send", "withhold", "payload_too_large"))
            return await self._transport.handle_async_request(request)
        endpoint = str(request.url)
        selected = self.policy.select_target(body, endpoint)
        if request.method != "POST" or selected is None:
            self._assess(self.policy.decision("before_send", "withhold", "target_unsupported"))
            return await self._transport.handle_async_request(request)
        profile_id, profile = selected
        decision = self.policy.inspect_json(
            body,
            phase="before_send",
            target_profile=profile_id,
            endpoint=endpoint,
            headers=_relevant_headers(request.headers),
        )
        self._assess(decision)
        if not self.policy.enforcing:
            # Observation mode never turns optional diagnosis into an output
            # block. Raw response protection is advertised only for enforcing.
            return await self._transport.handle_async_request(request)
        async with self._slots:
            try:
                async with asyncio.timeout(self.policy.config.response_timeout_seconds):
                    response = await self._transport.handle_async_request(request)
                    try:
                        if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                            self._assess(
                                self.policy.decision("before_release", "withhold", "encoding_unsupported"),
                                usage=InferenceUsage(requests=1, incomplete=True, target_model=profile.model),
                            )
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.policy.config.max_response_bytes:
                                self._assess(
                                    self.policy.decision("before_release", "withhold", "payload_too_large"),
                                    usage=InferenceUsage(requests=1, incomplete=True, target_model=profile.model),
                                )
                                # Even observation mode keeps the buffer bounded;
                                # these modes are not a streaming bypass promise.
                                raise InferencePrivacyError("before_release", "withhold", ("payload_too_large",))
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                    finally:
                        await response.aclose()
            except asyncio.CancelledError:
                raise
            except InferencePrivacyError:
                raise
            except (TimeoutError, httpx.TransportError) as error:
                raise InferencePrivacyError(
                    "before_release",
                    "withhold",
                    ("send_or_response_uncertain",),
                    usage=InferenceUsage(requests=1, incomplete=True, target_model=profile.model),
                ) from error
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type == "text/event-stream":
            assessed = inspect_sse(self.policy, raw, profile_id)
        else:
            assessed = self.policy.inspect_json(
                raw,
                phase="before_release",
                target_profile=profile_id,
                endpoint=endpoint,
                headers={"content-type": content_type},
            )
        usage = (
            chat_stream_usage(raw, profile.model)
            if content_type == "text/event-stream"
            else response_usage(raw, target_model=profile.model, protocol=profile.protocol)
        )
        self._assess(assessed, usage=usage)
        record_approved_usage(usage)
        # The SDK receives only the exact approved decoded response. Headers that
        # described the compressed wire body no longer describe these bytes.
        headers = response.headers.copy()
        for name in ("content-encoding", "content-length", "transfer-encoding"):
            headers.pop(name, None)
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=raw,
            extensions=response.extensions,
            request=request,
        )

    @override
    async def aclose(self) -> None:
        await self._transport.aclose()


__all__ = ["ProtectedAsyncTransport"]
