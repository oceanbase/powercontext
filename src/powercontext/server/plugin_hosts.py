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

"""Server-side management of plugin host processes.

Each Server process keeps at most one host process per plugin. It is a spawned,
fresh interpreter connected by a duplex Pipe; the Server forwards authenticated
requests to it, terminates it when a request outlives its time limit, and restarts
it after a backoff.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time
from contextlib import suppress
from typing import Any
from uuid import uuid4

from powercontext._logging import log_safely
from powercontext.builtin.plugins.host import run_plugin_host
from powercontext.builtin.plugins.host_protocol import (
    HostFailed,
    HostMethod,
    HostReady,
    HostRequest,
    HostResponse,
    HostShutdown,
    PluginHostSpec,
)

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 60.0
HOST_CONCURRENCY = 8
_BACKOFF_START = 1.0
_BACKOFF_LIMIT = 60.0
_HEALTHY_RESET_SECONDS = 300.0


class PluginHostUnavailable(RuntimeError):
    """The host process is starting, in restart backoff, or was terminated during the call."""


class PluginHost:
    def __init__(self, name: str, spec: PluginHostSpec) -> None:
        self.name = name
        self.spec = spec
        self.implemented: tuple[str, ...] = ()
        # Why the plugin cannot run at all, such as a missing required method; it is not restarted.
        self.failure: str | None = None
        self._process: Any = None
        self._connection: Any = None
        self._ready: asyncio.Future[None] | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[HostResponse]] = {}
        self._slots = asyncio.Semaphore(HOST_CONCURRENCY)
        self._send_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._backoff = 0.0
        self._not_before = 0.0
        self._started_at = 0.0
        self._closing = False

    async def start(self) -> None:
        """Start in the background; callers wait for readiness within their own time limit."""

        async with self._start_lock:
            if self._ready is not None or self._closing:
                return
            if self.failure is not None:
                raise PluginHostUnavailable(self.name)
            if time.monotonic() < self._not_before:
                raise PluginHostUnavailable(self.name)
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe(duplex=True)
            process = context.Process(
                target=run_plugin_host, args=(child, self.spec), name=f"powercontext-plugin-host-{self.name}"
            )
            await asyncio.to_thread(process.start)
            child.close()
            self._process, self._connection = process, parent
            self._ready = asyncio.get_running_loop().create_future()
            self._started_at = time.monotonic()
            self._reader = asyncio.create_task(self._read(parent), name=f"plugin-host-{self.name}")
            log_safely(logger, logging.INFO, f"Plugin host {self.name} started")

    async def _read(self, connection: Any) -> None:
        ready = self._ready
        try:
            while True:
                message = await asyncio.to_thread(connection.recv)
                if isinstance(message, HostReady):
                    self.implemented = message.implemented
                    if ready is not None and not ready.done():
                        ready.set_result(None)
                elif isinstance(message, HostFailed):
                    log_safely(logger, logging.WARNING, f"Plugin host {self.name} failed to start ({message.error})")
                    if message.permanent:
                        self.failure = message.error
                    break
                elif isinstance(message, HostResponse):
                    future = self._pending.pop(message.id, None)
                    if future is not None and not future.done():
                        future.set_result(message)
        except (EOFError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        await self._terminate("crash")

    async def _terminate(self, reason: str) -> None:
        process, connection, ready = self._process, self._connection, self._ready
        if process is None:
            return
        self._process = self._connection = self._ready = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(PluginHostUnavailable(self.name))
        self._pending.clear()
        if ready is not None and not ready.done():
            ready.set_exception(PluginHostUnavailable(self.name))
            ready.exception()
        if connection is not None:
            connection.close()
        await asyncio.to_thread(_stop_process, process)
        if not self._closing and self.failure is None:
            healthy = time.monotonic() - self._started_at >= _HEALTHY_RESET_SECONDS
            self._backoff = _BACKOFF_START if healthy or not self._backoff else min(self._backoff * 2, _BACKOFF_LIMIT)
            self._not_before = time.monotonic() + self._backoff
            log_safely(logger, logging.WARNING, f"Plugin host {self.name} stopped ({reason}); restarting later")
            asyncio.get_running_loop().call_later(self._backoff, self._restart)

    def _restart(self) -> None:
        if not self._closing:
            task = asyncio.ensure_future(self._restart_quietly())
            task.add_done_callback(lambda done: done.exception())

    async def _restart_quietly(self) -> None:
        with suppress(PluginHostUnavailable):
            await self.start()

    async def call(
        self,
        method: HostMethod,
        *,
        scope_id: str | None,
        caller: Any,
        audit: Any,
        payload: dict[str, Any],
        body: Any = None,
        timeout: float,  # noqa: ASYNC109 - the deadline also decides when to replace the process
    ) -> HostResponse:
        deadline = time.monotonic() + timeout
        if self._ready is None:
            await self.start()
        ready = self._ready
        if ready is None:
            raise PluginHostUnavailable(self.name)
        try:
            await asyncio.wait_for(asyncio.shield(ready), max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            raise PluginHostUnavailable(self.name) from None
        async with self._slots:
            connection = self._connection
            if connection is None:
                raise PluginHostUnavailable(self.name)
            request = HostRequest(
                id=uuid4().hex,
                method=method,
                scope_id=scope_id,
                caller=caller,
                audit=audit,
                payload=payload,
                body=body,
            )
            future: asyncio.Future[HostResponse] = asyncio.get_running_loop().create_future()
            self._pending[request.id] = future
            async with self._send_lock:
                await asyncio.to_thread(connection.send, request)
            try:
                return await asyncio.wait_for(asyncio.shield(future), max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                # A blocked plugin cannot be interrupted reliably; replace the whole process.
                await self._terminate("timeout")
                raise PluginHostUnavailable(self.name) from None

    async def close(self) -> None:
        self._closing = True
        connection = self._connection
        if connection is not None:
            with suppress(OSError):
                connection.send(HostShutdown())
            process = self._process
            if process is not None:
                await asyncio.to_thread(process.join, 10)
        await self._terminate("server_stop")
        if self._reader is not None:
            self._reader.cancel()


def _stop_process(process: Any) -> None:
    if process.is_alive():
        process.terminate()
        process.join(5)
    if process.is_alive():
        process.kill()
        process.join()
