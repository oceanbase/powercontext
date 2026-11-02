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

"""Infrastructure HTTP listener for a separately hosted processing supervisor."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI
from typing_extensions import override

from powercontext.server.factory import create_background_metrics_app
from powercontext.server.settings import (
    _UNSAFE_BIND_MESSAGE,
    UnauthenticatedNonLoopbackBindError,
    is_unauthenticated_non_loopback_bind,
)

if TYPE_CHECKING:
    from powercontext.server.settings import ServerSettings

__all__ = ["create_background_metrics_app", "serve_background_metrics"]


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
            port=settings.metrics.background_port,
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
