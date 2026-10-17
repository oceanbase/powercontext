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

"""Metrics-only HTTP surface for a separately hosted processing supervisor."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from typing_extensions import override

from powercontext.server.app import _error_response, _map_error, _set_error_headers
from powercontext.server.authz.errors import AccessControlError
from powercontext.server.factory import _MetricsEndpoint, _resolve_security_providers
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.server.settings import (
    _UNSAFE_BIND_MESSAGE,
    UnauthenticatedNonLoopbackBindError,
    is_unauthenticated_non_loopback_bind,
)

if TYPE_CHECKING:
    from powercontext.builtin.runtime import BuiltinRuntime
    from powercontext.server.authz import AccessControlService
    from powercontext.server.metrics import ServerMetrics
    from powercontext.server.settings import ServerSettings


def create_background_metrics_app(
    settings: ServerSettings,
    *,
    runtime: BuiltinRuntime | None,
    metrics: ServerMetrics,
    access_control: AccessControlService | None,
) -> FastAPI:
    """Expose only metrics, with the same authentication and authorization as the API host."""

    _, authentication, access, _ = _resolve_security_providers(
        settings, access_control=access_control, authentication_provider=None
    )
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.state.application = runtime
    app.state.access_control = access
    app.state.access_mode = settings.access.mode
    if authentication is not None:
        app.add_middleware(AuthenticationMiddleware, provider=authentication)

    @app.exception_handler(AccessControlError)
    async def access_error(_request: Request, error: AccessControlError) -> JSONResponse:
        status, code, message, details = _map_error(error)
        response = _error_response(status, code=code, message=message, details=details)
        _set_error_headers(response, error)
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _error: Exception) -> JSONResponse:
        return _error_response(500, code="internal_error", message="The Server failed.", details=None)

    if settings.metrics.enabled:
        app.add_api_route("/metrics", _MetricsEndpoint(metrics), include_in_schema=False)
    return app


class _BackgroundMetricsServer(uvicorn.Server):
    @override
    def capture_signals(self):
        # The background runner owns SIGINT/SIGTERM and closes the runtime after this listener.
        return nullcontext()


async def serve_background_metrics(app: FastAPI, settings: ServerSettings, stopped: asyncio.Event) -> None:
    """Keep the listener and supervisor lifetime aligned, including startup failure and cancellation."""

    if stopped.is_set() or not settings.metrics.enabled:
        return
    if is_unauthenticated_non_loopback_bind(
        host=settings.http.host,
        auth_enabled=settings.access.mode != "disabled",
        allow_unauthenticated_non_loopback=settings.allow_unauthenticated_non_loopback,
    ):
        raise UnauthenticatedNonLoopbackBindError(_UNSAFE_BIND_MESSAGE)
    server = _BackgroundMetricsServer(
        uvicorn.Config(
            app,
            host=settings.http.host,
            port=settings.http.port,
            access_log=False,
            log_config=None,
            lifespan="off",
            timeout_graceful_shutdown=5,
        )
    )

    async def serve() -> None:
        try:
            await server.serve()
        except SystemExit as error:
            # Uvicorn uses SystemExit for bind failures; contain it within the owning runner.
            raise RuntimeError("Background metrics listener failed to start") from error  # noqa: TRY003

    listener = asyncio.create_task(serve())
    stop = asyncio.create_task(stopped.wait())
    interrupted = True
    try:
        await asyncio.wait((listener, stop), return_when=asyncio.FIRST_COMPLETED)
        if listener.done():
            await listener
        else:
            server.should_exit = True
            await listener
        interrupted = False
    finally:
        server.should_exit = True
        stop.cancel()
        if not listener.done():
            listener.cancel()
        await asyncio.gather(listener, stop, return_exceptions=True)
        # A cancelled serve() can bypass Uvicorn's own shutdown path.
        if interrupted and server.started:
            await server.shutdown()
