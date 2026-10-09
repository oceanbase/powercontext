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

"""Exclusive, reusable HTTP clients with cancellation-safe retirement."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Self

import httpx

TRANSPORT_POLICY_VERSION = "exclusive-client-leases-v1"


class JevClientPool:
    """Own at most ``max_clients`` clients, with one request per lease.

    The factory creates fresh clients and controls HTTP configuration, including proxy routing,
    TLS, and timeouts. A failed lease is never retried or reused. Its capacity is
    released only after the retired client has finished closing.
    """

    def __init__(self, max_clients: int, *, client_factory: Callable[[], httpx.AsyncClient]) -> None:
        if type(max_clients) is not int or max_clients < 1:
            raise ValueError("max_clients must be a positive integer")  # noqa: TRY003
        self._factory = client_factory
        self._available: asyncio.Queue[httpx.AsyncClient | None] = asyncio.Queue(max_clients)
        for _ in range(max_clients):
            self._available.put_nowait(None)
        self._clients: set[httpx.AsyncClient] = set()
        self._retiring: set[httpx.AsyncClient] = set()
        self._close_tasks: set[asyncio.Task[None]] = set()
        self._borrowers = 0
        self._borrowers_done = asyncio.Event()
        self._borrowers_done.set()
        self._entered = False
        self._closing = False
        self._cleanup_failed = False
        self._shutdown_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        if self._entered or self._closing:
            raise RuntimeError("Jev client pool cannot be reopened")  # noqa: TRY003
        self._entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[httpx.AsyncClient]:
        """Borrow one client without transferring ownership to the caller."""
        if not self._entered or self._closing:
            raise RuntimeError("Jev client pool is not accepting leases")  # noqa: TRY003
        client = await self._available.get()
        if self._closing:
            self._available.put_nowait(client)
            raise RuntimeError("Jev client pool is not accepting leases")  # noqa: TRY003
        try:
            if client is None:
                client = self._factory()
                self._clients.add(client)
        except BaseException:
            self._available.put_nowait(None)
            raise
        self._borrowers += 1
        self._borrowers_done.clear()
        try:
            try:
                yield client
            except BaseException:
                closing = self._start_close(client, release_capacity=True)
                await asyncio.shield(closing)
                raise
            else:
                self._available.put_nowait(client)
        finally:
            self._borrowers -= 1
            if self._borrowers == 0:
                self._borrowers_done.set()

    def _start_close(self, client: httpx.AsyncClient, *, release_capacity: bool) -> asyncio.Task[None]:
        self._retiring.add(client)
        task = asyncio.create_task(self._close_client(client, release_capacity=release_capacity))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)
        return task

    async def _close_client(self, client: httpx.AsyncClient, *, release_capacity: bool) -> None:
        try:
            await client.aclose()
        except BaseException:
            self._cleanup_failed = True
            self._closing = True
        finally:
            self._retiring.discard(client)
            self._clients.discard(client)
            if release_capacity:
                self._available.put_nowait(None)

    async def _shutdown(self) -> None:
        await self._borrowers_done.wait()
        for client in self._clients - self._retiring:
            self._start_close(client, release_capacity=False)
        await asyncio.gather(*tuple(self._close_tasks))
        if self._cleanup_failed:
            raise RuntimeError("Jev client pool cleanup did not finish successfully")  # noqa: TRY003

    async def aclose(self) -> None:
        """Drain borrowers and close tasks before propagating caller cancellation."""
        self._closing = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        cancellation = None
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError as error:
                cancellation = error
        if cancellation is not None:
            if not self._shutdown_task.cancelled():
                self._shutdown_task.exception()
            raise cancellation
        self._shutdown_task.result()


__all__ = ["TRANSPORT_POLICY_VERSION", "JevClientPool"]
