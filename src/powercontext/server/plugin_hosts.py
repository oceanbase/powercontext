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

Each Server process keeps a group of host processes per plugin; plugins never share one.
A host process is a spawned, fresh interpreter connected by a duplex Pipe. The group
always keeps one process, starts another while every process is full and requests
queue, up to the plugin's limit and the Server's total, and stops extra processes once
they idle. A request past its time limit is cancelled in the host; a host whose
heartbeat stops (its event loop is blocked) is terminated and restarted after a backoff.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time
from contextlib import suppress
from dataclasses import replace
from typing import Any
from uuid import uuid4

from powercontext._logging import log_safely
from powercontext.builtin.plugins.host import run_plugin_host
from powercontext.builtin.plugins.host_protocol import (
    HostCancel,
    HostFailed,
    HostMethod,
    HostReady,
    HostRegistry,
    HostRequest,
    HostResponse,
    HostShutdown,
    HostStarted,
    HostUpgraded,
    PipeChannel,
    PluginHostSpec,
)

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 60.0
# Why a process that never became ready stopped, as reported for the version.
_START_FAILURES = {
    "crash": "start_failed",
    "start_timeout": "start_timeout",
    "upgrade_timeout": "upgrade_timeout",
    "setup_timeout": "setup_timeout",
}
HOST_CONCURRENCY = 8
HEARTBEAT_TIMEOUT_SECONDS = 5.0
IDLE_STOP_SECONDS = 300.0
_WATCH_SECONDS = 1.0
_SHUTDOWN_SECONDS = 10.0
_TERMINATE_SECONDS = 5.0
_BACKOFF_START = 1.0
_BACKOFF_LIMIT = 60.0
_HEALTHY_RESET_SECONDS = 300.0


class PluginHostUnavailable(RuntimeError):
    """No host process can take the call: starting, in restart backoff, or terminated during it."""


class PluginHostTimeout(RuntimeError):
    """The call outlived its time limit and was cancelled in the host."""


class HostProcessBudget:
    """The ceiling on host processes of all plugins in one Server process."""

    def __init__(self, total: int | None = None) -> None:
        self.total = total
        self.running = 0

    def take(self, *, resident: bool) -> bool:
        # The one process each plugin keeps always starts; the ceiling limits growth under load.
        if not resident and self.total is not None and self.running >= self.total:
            return False
        self.running += 1
        return True

    def give_back(self) -> None:
        self.running -= 1


class _HostProcess:
    def __init__(self, group: PluginHost) -> None:
        self.group = group
        self.process: Any = None
        self.channel: PipeChannel | None = None
        self.ready = False
        # The startup phase the Server is timing: core's own startup, then upgrade if declared, then setup.
        self.phase: str = "start"
        self.phase_since = time.monotonic()
        # False once the process is being stopped; it takes no new calls.
        self.accepting = True
        self.in_flight = 0
        self.pending: dict[str, asyncio.Future[HostResponse]] = {}
        self.started_at = time.monotonic()
        self.last_beat = self.started_at
        self.idle_since = self.started_at
        self.reason = "crash"
        self._exited = asyncio.Event()

    async def run(self) -> None:
        group = self.group
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        spawned = group.spec
        self.process = context.Process(
            target=run_plugin_host, args=(child, spawned), name=f"powercontext-plugin-host-{group.name}"
        )
        try:
            await asyncio.to_thread(self.process.start)
            child.close()
            channel = self.channel = PipeChannel(parent, name=f"powercontext-plugin-host-{group.name}")
            current = group.spec
            if (current.plugin_families, current.source_bindings) != (spawned.plugin_families, spawned.source_bindings):
                # Registrations changed while the process was being spawned; it reads this before any call.
                await channel.send(
                    HostRegistry(plugin_families=current.plugin_families, source_bindings=current.source_bindings)
                )
            log_safely(logger, logging.INFO, f"Plugin host {group.name} started")
            while self._received(await channel.recv()):
                pass
        except (EOFError, OSError):
            pass
        except Exception as error:
            log_safely(logger, logging.WARNING, f"Plugin host {group.name} failed ({type(error).__name__})")
        finally:
            await asyncio.shield(self._cleanup(parent))

    def _received(self, message: object) -> bool:
        """Handle one message from the host; False once the host reported that it cannot start."""

        group = self.group
        self.last_beat = time.monotonic()
        if isinstance(message, HostStarted | HostUpgraded):
            self.phase = "upgrade" if isinstance(message, HostStarted) and group.upgrade else "setup"
            self.phase_since = time.monotonic()
        elif isinstance(message, HostReady):
            group.implemented = message.implemented
            self.ready = True
            self.idle_since = time.monotonic()
            group.notify()
        elif isinstance(message, HostFailed):
            log_safely(logger, logging.WARNING, f"Plugin host {group.name} failed to start ({message.error})")
            group.start_failure = message.category or "start_failed"
            if message.permanent:
                group.failure = message.error
            return False
        elif isinstance(message, HostResponse):
            future = self.pending.pop(message.id, None)
            if future is not None and not future.done():
                future.set_result(message)
        return True

    async def _cleanup(self, connection: Any) -> None:
        self.accepting = self.ready = False
        for future in self.pending.values():
            if not future.done():
                future.set_exception(PluginHostUnavailable(self.group.name))
        self.pending.clear()
        if self.process is not None and self.process.pid is not None:
            await asyncio.to_thread(_stop_process, self.process)
        if self.channel is None:
            connection.close()
        else:
            await self.channel.close()
        self._exited.set()
        self.group.exited(self)

    async def send(self, message: object) -> None:
        if self.channel is None:
            raise OSError("the host process has not started")  # noqa: TRY003
        await self.channel.send(message)

    async def request(self, request: HostRequest, deadline: float) -> HostResponse:
        future: asyncio.Future[HostResponse] = asyncio.get_running_loop().create_future()
        self.pending[request.id] = future
        try:
            try:
                await self.send(request)
            except OSError:
                raise PluginHostUnavailable(self.group.name) from None
            try:
                return await asyncio.wait_for(asyncio.shield(future), max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                raise PluginHostTimeout(self.group.name) from None
        finally:
            if not future.done():
                # Timed out or the caller went away: cancel the task so its write transaction rolls back.
                self.pending.pop(request.id, None)
                self.group.spawn_task(self._cancel(request.id))

    async def _cancel(self, request_id: str) -> None:
        with suppress(OSError):
            await self.send(HostCancel(request_id))

    async def stop(self, reason: str) -> None:
        """Terminate the process; ``run`` then sees the Pipe close, reaps it, and cleans up."""

        self.accepting = False
        self.reason = reason
        if self.process is None or self.process.pid is None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self._exited.wait(), _TERMINATE_SECONDS)
        except TimeoutError:
            self.process.kill()

    async def shut_down(self, reason: str) -> None:
        """Ask the host to finish its calls and exit, and terminate it if it does not."""

        self.accepting = False
        self.reason = reason
        with suppress(OSError):
            await self.send(HostShutdown())
        try:
            await asyncio.wait_for(self._exited.wait(), _SHUTDOWN_SECONDS)
        except TimeoutError:
            await self.stop(reason)


class PluginHost:
    """The host processes of one plugin in this Server process."""

    def __init__(
        self, name: str, spec: PluginHostSpec, *, limit: int = 1, budget: HostProcessBudget | None = None
    ) -> None:
        self.name = name
        self.spec = spec
        self.limit = limit
        self.implemented: tuple[str, ...] = ()
        # Why the plugin cannot run at all, such as a missing required method; it is not restarted.
        self.failure: str | None = None
        self._budget = budget or HostProcessBudget()
        self._processes: list[_HostProcess] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._changed = asyncio.Event()
        self._watcher: asyncio.Task[None] | None = None
        self._backoff = 0.0
        self._not_before = 0.0
        self._closing = False
        self._waiting = 0
        # Why the last process exited before it was ready, such as "upgrade_failed".
        self.start_failure: str | None = None
        declared = spec.manifest.get("spec", {})
        self.upgrade = bool(spec.manifest.get("upgrade"))
        self._phase_limits = {
            "start": READY_TIMEOUT_SECONDS,
            "upgrade": float(declared.get("upgrade_timeout_seconds", 600)),
            "setup": float(declared.get("setup_timeout_seconds", 60)),
        }

    async def start(self) -> None:
        """Start the process the plugin always keeps; callers wait for readiness within their own time limit."""

        if self._watcher is None and not self._closing:
            self._watcher = asyncio.create_task(self._watch(), name=f"plugin-host-watch-{self.name}")
        if not self._processes and not self._launch(resident=True):
            raise PluginHostUnavailable(self.name)

    def _launch(self, *, resident: bool) -> bool:
        if self._closing or self.failure is not None or time.monotonic() < self._not_before:
            return False
        if not self._budget.take(resident=resident):
            return False
        process = _HostProcess(self)
        self._processes.append(process)
        self.spawn_task(process.run())
        return True

    def spawn_task(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def notify(self) -> None:
        # Wake every waiter; each takes the current event before it waits.
        self._changed.set()
        self._changed = asyncio.Event()

    def exited(self, process: _HostProcess) -> None:
        if not process.ready and self.start_failure is None and process.reason in _START_FAILURES:
            self.start_failure = _START_FAILURES[process.reason]
        self._processes.remove(process)
        self._budget.give_back()
        self.notify()
        if process.reason == "idle" or self._closing or self.failure is not None:
            return
        log_safely(logger, logging.WARNING, f"Plugin host {self.name} stopped ({process.reason})")
        if self._processes:
            return
        healthy = time.monotonic() - process.started_at >= _HEALTHY_RESET_SECONDS
        self._backoff = _BACKOFF_START if healthy or not self._backoff else min(self._backoff * 2, _BACKOFF_LIMIT)
        self._not_before = time.monotonic() + self._backoff
        asyncio.get_running_loop().call_later(self._backoff, self._restart)

    def _restart(self) -> None:
        if not self._processes:
            self._launch(resident=True)

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(_WATCH_SECONDS)
            now = time.monotonic()
            serving = [process for process in self._processes if process.accepting]
            for process in serving:
                if process.ready and now - process.last_beat > HEARTBEAT_TIMEOUT_SECONDS:
                    self.spawn_task(process.stop("unresponsive"))
                elif not process.ready and now - process.phase_since > self._phase_limits[process.phase]:
                    # Each phase has its own limit, so a long upgrade does not count against setup.
                    self.spawn_task(process.stop(f"{process.phase}_timeout"))
            idle = [
                process
                for process in serving
                if process.ready and process.in_flight == 0 and now - process.idle_since >= IDLE_STOP_SECONDS
            ]
            # The group always keeps one process; stop the newest idle extras first.
            for process in reversed(idle[: max(0, len(serving) - 1)]):
                self.spawn_task(process.shut_down("idle"))

    def _grow(self) -> None:
        serving = [process for process in self._processes if process.accepting]
        if len(serving) < self.limit and all(process.ready for process in serving):
            self._launch(resident=False)

    async def _acquire(self, deadline: float) -> _HostProcess:
        """Take a slot on the least busy ready process, queueing until one frees up."""

        while True:
            if self.failure is not None:
                raise PluginHostUnavailable(self.name)
            serving = [process for process in self._processes if process.accepting]
            ready = [process for process in serving if process.ready]
            free = [process for process in ready if process.in_flight < HOST_CONCURRENCY]
            if free:
                chosen = min(free, key=lambda process: process.in_flight)
                chosen.in_flight += 1
                return chosen
            if ready:
                self._grow()
            elif not serving and not self._launch(resident=True):
                raise PluginHostUnavailable(self.name)
            changed = self._changed
            self._waiting += 1
            try:
                await asyncio.wait_for(changed.wait(), max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                # Queued behind busy processes counts against the call; waiting for a start does not.
                if any(process.ready and process.accepting for process in self._processes):
                    raise PluginHostTimeout(self.name) from None
                raise PluginHostUnavailable(self.name) from None
            finally:
                self._waiting -= 1

    async def call(
        self,
        method: HostMethod,
        *,
        scope_id: str | None,
        caller: Any,
        audit: Any,
        payload: dict[str, Any],
        body: Any = None,
        timeout: float,  # noqa: ASYNC109 - the deadline also decides when the host cancels the call
    ) -> HostResponse:
        deadline = time.monotonic() + timeout
        if self._watcher is None:
            await self.start()
        process = await self._acquire(deadline)
        request = HostRequest(
            id=uuid4().hex,
            method=method,
            scope_id=scope_id,
            caller=caller,
            audit=audit,
            payload=payload,
            body=body,
        )
        try:
            return await process.request(request, deadline)
        finally:
            process.in_flight -= 1
            if process.in_flight == 0:
                process.idle_since = time.monotonic()
            self.notify()

    async def wait_ready(self) -> str | None:
        """Wait for the first process of a new version; return why it failed, or None once one is ready.

        The watchdog stops a process that overruns any startup phase, so this wait always ends.
        """

        while True:
            if any(process.ready for process in self._processes):
                return None
            if self.failure is not None or not self._processes:
                return self.start_failure or "start_failed"
            await self._changed.wait()

    def update_registry(
        self, families: tuple[str, ...], bindings: tuple[tuple[str, tuple[str, ...] | None, tuple[str, ...]], ...]
    ) -> None:
        """New processes start with these registrations; spawned ones replace theirs.

        A process still starting was spawned with earlier registrations. It reads this message
        once it is ready, before the first call, because calls are sent only to ready processes.
        """

        self.spec = replace(self.spec, plugin_families=families, source_bindings=bindings)
        message = HostRegistry(plugin_families=families, source_bindings=bindings)
        for process in self._processes:
            if process.channel is not None and process.accepting:
                self.spawn_task(_send_quietly(process, message))

    async def retire(self) -> None:
        """Stop taking new processes, let accepted and queued calls finish within their limits, then close."""

        self._closing = True
        while any(process.in_flight for process in self._processes) or self._waiting:
            await self._changed.wait()
        await self.close()

    async def close(self) -> None:
        self._closing = True
        if self._watcher is not None:
            self._watcher.cancel()
        await asyncio.gather(
            *(process.shut_down("server_stop") for process in list(self._processes)), return_exceptions=True
        )
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=_SHUTDOWN_SECONDS)


async def _send_quietly(process: _HostProcess, message: object) -> None:
    with suppress(OSError):
        await process.send(message)


def _stop_process(process: Any) -> None:
    if process.is_alive():
        process.terminate()
        process.join(5)
    if process.is_alive():
        process.kill()
        process.join()
