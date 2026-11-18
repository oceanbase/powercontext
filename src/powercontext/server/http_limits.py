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

"""Process-local HTTP admission before downstream parsing and execution."""

from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.server.context import is_internal_bridge

_PLUGIN_API = re.compile(r"^/(?:v1|dashboard)/plugins/[^/]+/api/")


class HttpAdmissionMiddleware:
    """Bound concurrent external response lifetimes."""

    def __init__(self, app: ASGIApp, *, max_concurrent_requests: int) -> None:
        self.app = app
        self._max_requests = max_concurrent_requests
        self._active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # MCP's trusted in-process HTTP projection belongs to its admitted request.
        if scope["type"] != "http" or is_internal_bridge():
            await self.app(scope, receive, send)
            return
        if self._active >= self._max_requests:
            response = JSONResponse(
                ErrorResponse(
                    error=ErrorDetail(code="server_overloaded", message="The Server is at HTTP capacity.", details=None)
                ).model_dump(mode="json"),
                status_code=503,
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return
        # Admission has no await between checking capacity and reserving a slot.
        self._active += 1
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self._active -= 1

        trailers = False

        async def send_response(message: Message) -> None:
            nonlocal trailers
            if message["type"] == "http.response.start":
                trailers = message.get("trailers", False)
            await send(message)
            if (message["type"] == "http.response.body" and not message.get("more_body", False) and not trailers) or (
                message["type"] == "http.response.trailers" and not message.get("more_trailers", False)
            ):
                release()

        try:
            await self.app(scope, receive, send_response)
        except _RequestBodyDisconnected:
            return
        finally:
            release()


class _RequestBodyDisconnected(Exception):
    """The client left before its request body arrived."""


class HttpRequestBodyLimitMiddleware:
    """Bound external request bodies before downstream parsing."""

    def __init__(
        self, app: ASGIApp, *, max_request_body_bytes: int, plugin_api_max_body_bytes: int | None = None
    ) -> None:
        self.app = app
        self._max_body = max_request_body_bytes
        # Plugin routes declare their own limits under a separate deployment ceiling.
        self._plugin_max_body = plugin_api_max_body_bytes or max_request_body_bytes

    def _limit(self, scope: Scope) -> int:
        return self._plugin_max_body if _PLUGIN_API.match(scope.get("path", "")) else self._max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge():
            await self.app(scope, receive, send)
            return
        body = await self._receive_body(scope, receive, send)
        if body is None:
            return
        await self.app(scope, _replay_body(body, receive), send)

    async def _receive_body(self, scope: Scope, receive: Receive, send: Send) -> bytearray | None:
        limit = self._limit(scope)
        if self._declared_too_large(scope, limit):
            await _reject_body(scope, receive, send)
            return None
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                # The BaseHTTPMiddleware layers around this one fail if no response
                # is sent, so admission catches this outside them and ends the request.
                raise _RequestBodyDisconnected
            chunk = message.get("body", b"")
            if len(chunk) > limit - len(body):
                await _reject_body(scope, receive, send)
                return None
            body.extend(chunk)
            if not message.get("more_body", False):
                return body

    @staticmethod
    def _declared_too_large(scope: Scope, limit: int) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        return True
                except ValueError:
                    continue
        return False


def _replay_body(body: bytearray, receive: Receive) -> Receive:
    complete = False

    async def replay() -> Message:
        nonlocal complete
        if complete:
            return await receive()
        complete = True
        chunk = bytes(body)
        body.clear()
        return {"type": "http.request", "body": chunk, "more_body": False}

    return replay


async def _reject_body(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(
        ErrorResponse(
            error=ErrorDetail(
                code="request_too_large", message="The HTTP request body exceeds the limit.", details=None
            )
        ).model_dump(mode="json"),
        status_code=413,
    )
    await response(scope, receive, send)
