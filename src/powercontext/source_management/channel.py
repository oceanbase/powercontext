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

"""Authenticated reverse request/reply channel with a single expiring lease."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from uuid import uuid4

from powercontext.source_management.models import MAX_MESSAGE_BYTES, ManagementError
from powercontext.source_management.storage import canonical


@dataclass
class Connection:
    generation: str
    owner: str
    seen: float
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=32))
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    polling: bool = False


class ReverseChannel:
    def __init__(self, *, lease_seconds: float = 30, request_seconds: float = 10) -> None:
        self.lease_seconds = lease_seconds
        self.request_seconds = request_seconds
        self.connection: Connection | None = None
        self.catalog: dict[str, Any] = {"revision": 0, "capabilities": [], "instances": []}

    @property
    def online(self) -> bool:
        connection = self.connection
        return connection is not None and monotonic() - connection.seen <= self.lease_seconds

    def open(self, owner: str) -> str:
        if self.online:
            raise ManagementError("connection-active", "A live connection already owns this identity", 409)
        self.close()
        generation = uuid4().hex
        self.connection = Connection(generation, owner, monotonic())
        return generation

    def check(self, owner: str, generation: str) -> Connection:
        connection = self.connection
        if not self.online or connection is None or connection.owner != owner or connection.generation != generation:
            raise ManagementError("connection-expired", "Reconnect and synchronize the current state", 409)
        connection.seen = monotonic()
        return connection

    async def poll(self, owner: str, generation: str) -> list[dict[str, Any]]:
        connection = self.check(owner, generation)
        if connection.polling:
            raise ManagementError("poll-active", "Only one poll may be active on a connection", 409)
        connection.polling = True
        try:
            async with asyncio.timeout(min(self.lease_seconds / 3, 5)):
                while True:
                    message = await connection.queue.get()
                    future = connection.pending.get(message["message_id"])
                    if future is not None and not future.done():
                        break
        except TimeoutError:
            return []
        finally:
            connection.polling = False
        return [message]

    async def request(
        self, operation: str, arguments: dict[str, Any], *, wait_seconds: float | None = None
    ) -> dict[str, Any]:
        if len(canonical(arguments).encode()) > MAX_MESSAGE_BYTES - 1024:
            raise ManagementError("request-too-large", "The management message exceeds its size limit")
        connection = self.connection
        if not self.online or connection is None:
            raise ManagementError("management-unavailable", "Source management is unavailable", 503)
        if len(connection.pending) >= 32:
            raise ManagementError("management-busy", "The management connection has reached its request limit", 503)
        identifier = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        connection.pending[identifier] = future
        try:
            try:
                connection.queue.put_nowait({
                    "generation": connection.generation,
                    "message_id": identifier,
                    "operation": operation,
                    "arguments": arguments,
                })
            except asyncio.QueueFull as error:
                raise ManagementError("management-busy", "The management request was not sent", 503) from error
            async with asyncio.timeout(self.request_seconds if wait_seconds is None else wait_seconds):
                result = await future
            if "error" in result:
                error = result["error"]
                raise ManagementError(error["code"], error["message"], error.get("status", 400))
            return result["result"]
        except (TimeoutError, ConnectionError) as error:
            raise ManagementError(
                "result-unconfirmed", "The result is unconfirmed; query or retry the original request", 503
            ) from error
        finally:
            connection.pending.pop(identifier, None)
            if not future.done():
                future.cancel()

    def reply(self, owner: str, generation: str, message_id: str, result: dict[str, Any]) -> None:
        if len(canonical(result).encode()) > MAX_MESSAGE_BYTES - 1024:
            raise ManagementError("response-too-large", "The management reply exceeds its size limit")
        connection = self.check(owner, generation)
        future = connection.pending.get(message_id)
        if future is not None and not future.done():
            future.set_result(result)

    def close(self, owner: str | None = None, generation: str | None = None) -> None:
        connection = self.connection
        if connection is None:
            return
        if owner is not None and (connection.owner != owner or connection.generation != generation):
            raise ManagementError("connection-expired", "Connection ownership changed", 409)
        self.connection = None
        for future in connection.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("channel closed"))
        with suppress(asyncio.QueueEmpty):
            while True:
                connection.queue.get_nowait()
