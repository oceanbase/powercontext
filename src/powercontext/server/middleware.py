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

"""ASGI middleware provided by the PowerContext Server."""

from __future__ import annotations

from secrets import compare_digest

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.server.context import is_internal_bridge

_PUBLIC_PATHS = frozenset({"/", "/docs", "/handoff-reports", "/reviews", "/skills", "/health/live", "/health/ready"})
_PUBLIC_PATH_PREFIXES = ("/static/",)


class StaticBearerMiddleware:
    """Require one configured bearer token for external HTTP requests."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        if not token:
            raise ValueError("Bearer token must not be empty")  # noqa: TRY003
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._allows(scope):
            await self.app(scope, receive, send)
            return

        error = ErrorResponse(
            error=ErrorDetail(
                code="unauthorized",
                message="A valid bearer token is required.",
                details=None,
            )
        )
        response = JSONResponse(
            content=error.model_dump(mode="json"),
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    def _allows(self, scope: Scope) -> bool:
        if (
            scope["type"] != "http"
            or scope["path"] in _PUBLIC_PATHS
            or scope["path"].startswith(_PUBLIC_PATH_PREFIXES)
            or is_internal_bridge()
        ):
            return True

        authorization = Headers(scope=scope).get("authorization")
        if authorization is None:
            return False
        scheme, separator, credential = authorization.partition(" ")
        return (
            bool(separator)
            and scheme.casefold() == "bearer"
            and bool(credential)
            and compare_digest(credential.encode(), self._token)
        )


__all__ = ["StaticBearerMiddleware"]
