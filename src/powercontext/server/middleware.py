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

from starlette.requests import Request
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
from powercontext.server.context import (
    bind_authentication,
    bind_principal,
    is_internal_bridge,
    reset_authentication,
    reset_principal,
)
from powercontext.server.dashboard.session import authentication_headers, login_response

_PUBLIC_PATHS = frozenset({
    "/",
    "/docs",
    "/dashboard/session",
    "/health/live",
    "/health/ready",
    "/v1/skill/remote/target/enroll",
    "/v1/skill/remote/reconcile",
    "/v1/skill/remote/package/download",
    "/v1/skill/remote/receipt",
})
_PUBLIC_PATH_PREFIXES = ("/dashboard/static/",)


def is_public_http_path(path: str) -> bool:
    """Return whether a request bypasses Server authentication."""

    return path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PATH_PREFIXES)


class AuthenticationMiddleware:
    """Authenticate every protected external HTTP request through one Provider."""

    def __init__(self, app: ASGIApp, *, provider: AuthenticationProvider, dashboard_enabled: bool = False) -> None:
        self.app = app
        self._provider = provider
        self._dashboard_enabled = dashboard_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge() or is_public_http_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        try:
            result = await self._provider.authenticate(
                AuthenticationRequest(
                    transport="http",
                    headers=authentication_headers(scope),
                    client_host=_client_host(scope),
                )
            )
        except AuthenticationRejectedError:
            await _error_response(
                "unauthorized",
                "A valid credential is required.",
                401,
                scope,
                receive,
                send,
                dashboard_enabled=self._dashboard_enabled,
            )
            return
        except AuthenticationUnavailableError:
            await _error_response(
                "authentication_unavailable",
                "The authentication service is unavailable.",
                503,
                scope,
                receive,
                send,
                dashboard_enabled=self._dashboard_enabled,
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
                dashboard_enabled=self._dashboard_enabled,
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


class LocalPrincipalMiddleware:
    """Bind the implicit service Principal used by local unprotected deployments."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge():
            await self.app(scope, receive, send)
            return
        token = bind_principal(PrincipalRef(type="service", id="local"))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_principal(token)


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
    *,
    dashboard_enabled: bool = False,
) -> None:
    if dashboard_enabled and scope["path"].startswith("/dashboard/"):
        await login_response(
            status_code, rejected="authorization" in authentication_headers(scope), request=Request(scope)
        )(scope, receive, send)
        return
    response = JSONResponse(
        content=ErrorResponse(error=ErrorDetail(code=code, message=message, details=None)).model_dump(mode="json"),
        status_code=status_code,
        headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
    )
    await response(scope, receive, send)


__all__ = [
    "AuthenticationMiddleware",
    "LocalPrincipalMiddleware",
    "StaticBearerMiddleware",
    "is_public_http_path",
]
