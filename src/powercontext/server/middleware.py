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

import re
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from powercontext.builtin.authz import PrincipalRef
from powercontext.http import ErrorDetail, ErrorResponse
from powercontext.http._generated.operations import (
    DOWNLOAD_REMOTE_SKILL_PACKAGE,
    ENROLL_REMOTE_SKILL_TARGET,
    RECONCILE_REMOTE_SKILLS,
    RECORD_REMOTE_SKILL_RECEIPT,
)
from powercontext.server.authentication import (
    AuthenticationProvider,
    AuthenticationRejectedError,
    AuthenticationRequest,
    AuthenticationUnavailableError,
    StaticBearerAuthenticationProvider,
)
from powercontext.server.context import bind_authentication, is_internal_bridge, reset_authentication
from powercontext.server.dashboard.session import authentication_headers, login_response

_BODYLESS_PUBLIC_PATHS = frozenset({"/", "/docs", "/health/live", "/health/ready"})
_REMOTE_SKILL_TARGET_PATHS = frozenset({
    ENROLL_REMOTE_SKILL_TARGET.path,
    RECONCILE_REMOTE_SKILLS.path,
    DOWNLOAD_REMOTE_SKILL_PACKAGE.path,
    RECORD_REMOTE_SKILL_RECEIPT.path,
})


_PLUGIN_ASSET = re.compile(r"^/dashboard/plugins/[a-z][a-z0-9-]*/ui/")


class AuthenticationMiddleware:
    """Authenticate every protected external HTTP request through one Provider."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        provider: AuthenticationProvider | None,
        dashboard_enabled: bool = False,
        authenticate_remote_skill: Callable[[Request], Awaitable[Response | None]] | None = None,
    ) -> None:
        self.app = app
        self._provider = provider
        self._dashboard_enabled = dashboard_enabled
        self._authenticate_remote_skill = authenticate_remote_skill

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge():
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path == ENROLL_REMOTE_SKILL_TARGET.path and scope["method"] != ENROLL_REMOTE_SKILL_TARGET.method:
            response = JSONResponse(
                {"detail": "Method Not Allowed"}, status_code=405, headers={"Allow": ENROLL_REMOTE_SKILL_TARGET.method}
            )
            await response(scope, receive, send)
            return
        if scope["method"] == "POST" and path in _REMOTE_SKILL_TARGET_PATHS:
            if self._authenticate_remote_skill is not None:
                rejection = await self._authenticate_remote_skill(Request(scope))
                if rejection is not None:
                    await rejection(scope, receive, send)
                    return
            await self.app(scope, receive, send)
            return
        if (
            self._provider is None
            or (self._dashboard_enabled and scope["method"] == "POST" and path == "/dashboard/session")
            or is_bodyless_public_request(scope, dashboard_enabled=self._dashboard_enabled)
        ):
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


def is_bodyless_public_request(scope: Scope, *, dashboard_enabled: bool) -> bool:
    if scope["method"] not in {"GET", "HEAD"}:
        return False
    path = scope["path"]
    return path in _BODYLESS_PUBLIC_PATHS or (
        dashboard_enabled
        and (
            path.startswith("/dashboard/static/")
            # Plugin page files are plugin code and assets, never user data.
            or path == "/dashboard/plugins/_client.js"
            or _PLUGIN_ASSET.match(path) is not None
        )
    )


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


__all__ = ["AuthenticationMiddleware", "StaticBearerMiddleware"]
