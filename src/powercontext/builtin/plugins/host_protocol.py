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

"""Picklable messages between the Server and a plugin host process over a duplex Pipe, and the channel carrying them."""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

HostMethod = Literal[
    "create_artifact",
    "update_artifact",
    "merge_artifacts",
    "forget_artifact",
    "restore_artifact",
    "create_source",
    "render_artifact",
    "search_artifacts",
    "api",
]
HostOutcome = Literal[
    "ok",
    "invalid_content",
    "not_implemented",
    "forbidden",
    "not_found",
    "precondition_failed",
    "conflict",
    "contract_violation",
    "api_error",
    "model_unavailable",
    "error",
]


@dataclass(frozen=True)
class PluginHostSpec:
    """Everything a fresh interpreter needs to become core runtime plus one plugin."""

    database: Any
    directory: str
    manifest: dict[str, Any]
    security: dict[str, Any] | None
    inference: Any = None
    # Every loaded plugin family and Source binding, so reads and Source writes behave as in the Server.
    plugin_families: tuple[str, ...] = ()
    source_bindings: tuple[tuple[str, tuple[str, ...] | None, tuple[str, ...]], ...] = ()
    storage_root: str | None = None


@dataclass(frozen=True)
class HostReady:
    implemented: tuple[str, ...]


@dataclass(frozen=True)
class HostFailed:
    error: str
    # The plugin breaks its contract, for example it lacks a required method; restarting cannot help.
    permanent: bool = False


@dataclass(frozen=True)
class HostRequest:
    id: str
    method: HostMethod
    scope_id: str | None
    # The caller Server authenticated, and the audit context of the HTTP request; None without access control.
    caller: Any
    audit: Any
    payload: dict[str, Any] = field(default_factory=dict)
    body: Any = None


@dataclass(frozen=True)
class HostResponse:
    id: str
    outcome: HostOutcome
    result: Any = None
    message: str | None = None
    field: str | None = None
    status: int | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class HostShutdown:
    pass


@dataclass(frozen=True)
class HostCancel:
    """The request outlived its time limit; cancel its task so its write transaction rolls back."""

    id: str


@dataclass(frozen=True)
class HostHeartbeat:
    """Sent every second by a task on the host's event loop, so a blocked loop stops sending it."""


class _Closed:
    pass


_CLOSED = _Closed()


class PipeChannel:
    """Carry messages over one end of a Pipe on two threads of its own.

    Blocking ``recv`` and ``send`` never borrow the event loop's default executor, which other host
    processes and plugin code also use: a full executor must not delay responses or heartbeats.
    Messages are sent in the order ``send`` is called.
    """

    def __init__(self, connection: Any, *, name: str) -> None:
        self._connection = connection
        self._loop = asyncio.get_running_loop()
        self._incoming: asyncio.Queue[object] = asyncio.Queue()
        self._outgoing: queue.SimpleQueue[tuple[object, asyncio.Future[None]] | None] = queue.SimpleQueue()
        self._read_done = asyncio.Event()
        self._closed = False
        threading.Thread(target=self._read, name=f"{name}-recv", daemon=True).start()
        threading.Thread(target=self._write, name=f"{name}-send", daemon=True).start()

    async def recv(self) -> Any:
        """The next message; ``EOFError`` once the other end is gone."""

        message = await self._incoming.get()
        if message is _CLOSED:
            self._incoming.put_nowait(_CLOSED)
            raise EOFError
        return message

    async def send(self, message: object) -> None:
        """Send a message; ``OSError`` when the Pipe is closed, or the error that sending raised."""

        if self._closed:
            raise OSError("the channel is closed")  # noqa: TRY003
        future = self._loop.create_future()
        self._outgoing.put((message, future))
        await future

    async def close(self, timeout: float = 5.0) -> None:
        """Stop sending and close the Pipe once the reader has stopped, so no thread uses a closed handle."""

        self._closed = True
        self._outgoing.put(None)
        try:
            await asyncio.wait_for(self._read_done.wait(), timeout)
        except TimeoutError:
            return
        self._connection.close()

    def _read(self) -> None:
        try:
            while True:
                message = self._connection.recv()
                self._call(self._incoming.put_nowait, message)
        except BaseException:
            self._call(self._incoming.put_nowait, _CLOSED)
        finally:
            self._call(self._read_done.set)

    def _write(self) -> None:
        while (item := self._outgoing.get()) is not None:
            message, future = item
            try:
                self._connection.send(message)
            except Exception as error:
                self._call(_settle, future, error)
            else:
                self._call(_settle, future, None)

    def _call(self, callback: Any, *args: object) -> None:
        try:
            self._loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            # The event loop has closed; nobody is waiting any more.
            pass


def _settle(future: asyncio.Future[None], error: Exception | None) -> None:
    if future.done():
        return
    if error is None:
        future.set_result(None)
    else:
        future.set_exception(error)
