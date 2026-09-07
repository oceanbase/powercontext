# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ASGI authentication middleware provided by the PowerContext Server."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.server.authentication import (
    AuthenticationProvider,
    AuthenticationRejectedError,
    AuthenticationRequest,
    AuthenticationUnavailableError,
    StaticBearerAuthenticationProvider,
)
from powercontext.server.authz import PrincipalRef
from powercontext.server.context import bind_authentication, is_internal_bridge, reset_authentication

_PUBLIC_PATHS = frozenset({
    "/",
    "/docs",
    "/handoff-reports",
    "/reviews",
    "/skills",
    "/shared",
    "/health/live",
    "/health/ready",
    "/v1/skill/remote/target/enroll",
    "/v1/skill/remote/reconcile",
    "/v1/skill/remote/package/download",
    "/v1/skill/remote/receipt",
})
_PUBLIC_PATH_PREFIXES = ("/static/",)


class AuthenticationMiddleware:
    """Authenticate every protected external HTTP request through one Provider."""

    def __init__(self, app: ASGIApp, *, provider: AuthenticationProvider) -> None:
        self.app = app
        self._provider = provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if is_internal_bridge() or _is_public(scope):
            await self.app(scope, receive, send)
            return
        try:
            result = await self._provider.authenticate(
                AuthenticationRequest(
                    transport="http",
                    headers=dict(Headers(scope=scope).items()),
                    client_host=_client_host(scope),
                )
            )
        except AuthenticationRejectedError:
            await _error_response("unauthorized", "A valid credential is required.", 401, scope, receive, send)
            return
        except AuthenticationUnavailableError:
            await _error_response(
                "authentication_unavailable",
                "The authentication service is unavailable.",
                503,
                scope,
                receive,
                send,
            )
            return
        except Exception:
            await _error_response(
                "authentication_unavailable",
                "The authentication service is unavailable.",
                503,
                scope,
                receive,
                send,
            )
            return
        tokens = bind_authentication(result)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_authentication(tokens)


class StaticBearerMiddleware(AuthenticationMiddleware):
    """Convenience composition for a fixed static bearer Principal."""

    def __init__(self, app: ASGIApp, *, token: str, principal: PrincipalRef | None = None) -> None:
        resolved = principal or PrincipalRef(type="service", id="server-token")
        super().__init__(app, provider=StaticBearerAuthenticationProvider(token, resolved))


def _is_public(scope: Scope) -> bool:
    return scope["type"] != "http" or scope["path"] in _PUBLIC_PATHS or scope["path"].startswith(_PUBLIC_PATH_PREFIXES)


def _client_host(scope: Scope) -> str | None:
    client = scope.get("client")
    return None if client is None else str(client[0])


async def _error_response(
    code: str,
    message: str,
    status_code: int,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        content=ErrorResponse(error=ErrorDetail(code=code, message=message, details=None)).model_dump(mode="json"),
        status_code=status_code,
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
    )
    await response(scope, receive, send)


__all__ = ["AuthenticationMiddleware", "StaticBearerMiddleware"]
