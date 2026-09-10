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

"""Shared HTTP rate-limit adapter."""

from __future__ import annotations

import hashlib

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.rate_limit import RateLimitRepository
from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.server.authz import PrincipalRef
from powercontext.server.context import current_principal, is_internal_bridge
from powercontext.server.middleware import is_public_http_path

_POLICY_ID = "http.default.v1"
_RATE_LIMIT_EXEMPT_PATHS = frozenset({"/metrics"})


class SharedRateLimiter:
    """Lifecycle-bound database counter used by every API replica."""

    def __init__(self, *, requests: int, window_seconds: int) -> None:
        self._requests = requests
        self._window_seconds = window_seconds
        self._database: AsyncDatabase | None = None
        self._repository = RateLimitRepository()

    def bind(self, database: AsyncDatabase) -> None:
        self._database = database

    def unbind(self) -> None:
        self._database = None

    async def consume(self, principal: PrincipalRef) -> tuple[bool, int]:
        database = self._database
        if database is None:
            raise RuntimeError("rate limiter must be bound before handling requests")  # noqa: TRY003
        async with database.transaction() as connection:
            decision = await self._repository.consume(
                connection,
                principal_key=_principal_key(principal),
                policy_id=_POLICY_ID,
                limit=self._requests,
                window_seconds=self._window_seconds,
            )
        return decision.allowed, decision.retry_after_seconds


class SharedRateLimitMiddleware:
    """Reject protected requests after their shared fixed window is exhausted."""

    def __init__(self, app: ASGIApp, *, limiter: SharedRateLimiter) -> None:
        self.app = app
        self._limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._skip(scope):
            await self.app(scope, receive, send)
            return
        principal = current_principal()
        if principal is None:
            raise RuntimeError("principal middleware must run before rate limiting")  # noqa: TRY003
        allowed, retry_after = await self._limiter.consume(principal)
        if allowed:
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            content=ErrorResponse(
                error=ErrorDetail(
                    code="rate_limited",
                    message="The request rate limit was exceeded.",
                    details=None,
                )
            ).model_dump(mode="json"),
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)

    @staticmethod
    def _skip(scope: Scope) -> bool:
        return (
            scope["type"] != "http"
            or is_internal_bridge()
            or is_public_http_path(scope["path"])
            or scope["path"] in _RATE_LIMIT_EXEMPT_PATHS
        )


def _principal_key(principal: PrincipalRef) -> str:
    return hashlib.sha256(f"{principal.type}\0{principal.id}".encode()).hexdigest()


__all__ = ["SharedRateLimitMiddleware", "SharedRateLimiter"]
