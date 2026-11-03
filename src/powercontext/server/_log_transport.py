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

"""Dedicated Worker log sockets multiplexed by their owning Server."""

from __future__ import annotations

import json
import selectors
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from queue import Empty, Queue
from threading import Event, Lock, Thread

_MAX_RECORD_BYTES = 256 * 1024
# Stay below Windows select()'s socket limit, including the wakeup socket.
_MAX_CHANNELS = 256
_DRAIN_TIMEOUT = 1.0


class LogSender:
    """Send JSON lines; a partial send retires only this Worker's socket."""

    def __init__(self, channel: socket.socket) -> None:
        self._channel: socket.socket | None = channel
        channel.settimeout(0.05)

    def send(self, message: str) -> None:
        # JSON quoting preserves multiline console messages as a single transport record.
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(payload) > _MAX_RECORD_BYTES:
            raise ValueError("Worker log record exceeds the transport limit")  # noqa: TRY003
        if self._channel is None:
            raise OSError("Worker log stream is unavailable")  # noqa: TRY003
        try:
            self._channel.sendall(payload)
        except OSError:
            self.close()
            raise

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None


class LogReceiver:
    """Multiplex dedicated Worker sockets without owning task execution."""

    def __init__(
        self, deliver: Callable[[str], None], on_failure: Callable[[], None], on_drop: Callable[[int], None]
    ) -> None:
        self._deliver = deliver
        self._on_failure = on_failure
        self._on_drop = on_drop
        self._stopped = Event()
        self._lock = Lock()
        self._pending: Queue[socket.socket] = Queue(maxsize=_MAX_CHANNELS)
        self._buffers: dict[socket.socket, bytearray] = {}
        self._resources = ExitStack()
        try:
            self._selector = self._resources.enter_context(selectors.DefaultSelector())
            self._wake_read, self._wake_write = socket.socketpair()
            self._resources.callback(self._wake_read.close)
            self._resources.callback(self._wake_write.close)
            self._wake_read.setblocking(False)
            self._wake_write.setblocking(False)
            self._selector.register(self._wake_read, selectors.EVENT_READ)
            self._thread = Thread(target=self._run, name="powercontext-worker-logs", daemon=True)
            self._thread.start()
        except BaseException:
            self._resources.close()
            raise

    @contextmanager
    def channel(self) -> Iterator[socket.socket]:
        """Create before spawn and close the parent's sending end after spawn settles."""

        receiver, sender = socket.socketpair()
        try:
            receiver.setblocking(False)
            with self._lock:
                if self._stopped.is_set():
                    raise RuntimeError("Server log receiver is closed")  # noqa: TRY003, TRY301
                self._pending.put_nowait(receiver)
        except BaseException:
            receiver.close()
            sender.close()
            raise
        self._wake()
        try:
            yield sender
        finally:
            sender.close()

    def _wake(self) -> None:
        with suppress(OSError):
            self._wake_write.send(b"\0")

    def close(self) -> bool:
        with self._lock:
            self._stopped.set()
        self._wake()
        self._thread.join(timeout=_DRAIN_TIMEOUT + 0.5)
        if self._thread.is_alive():
            self._on_failure()
            return False
        return True

    def _register_pending(self) -> None:
        while True:
            try:
                connection = self._pending.get_nowait()
            except Empty:
                return
            if len(self._buffers) >= _MAX_CHANNELS:
                connection.close()
                self._on_failure()
                continue
            try:
                self._selector.register(connection, selectors.EVENT_READ)
                self._buffers[connection] = bytearray()
            except Exception:
                connection.close()
                self._on_failure()

    def _close_channel(self, connection: socket.socket) -> None:
        buffer = self._buffers.pop(connection)
        if buffer:
            self._on_drop(buffer.count(b"\n") + int(not buffer.endswith(b"\n")))
        self._selector.unregister(connection)
        connection.close()

    def _read(self, connection: socket.socket) -> None:
        buffer = self._buffers[connection]
        try:
            data = connection.recv(64 * 1024)
            if not data:
                self._close_channel(connection)
                return
            buffer.extend(data)
            self._decode(buffer)
        except BlockingIOError:
            return
        except (ConnectionResetError, ConnectionAbortedError):
            # Windows reports terminated peers as resets rather than EOF.
            self._close_channel(connection)
        except Exception:
            self._on_failure()
            self._close_channel(connection)

    def _decode(self, buffer: bytearray) -> None:
        while (newline := buffer.find(b"\n")) != -1:
            if newline + 1 > _MAX_RECORD_BYTES:
                raise ValueError("Worker log record exceeds the transport limit")  # noqa: TRY003
            message = json.loads(buffer[:newline])
            if not isinstance(message, str):
                raise TypeError("Worker log record must contain formatted text")  # noqa: TRY003
            del buffer[: newline + 1]
            self._deliver(message)
        if len(buffer) >= _MAX_RECORD_BYTES:
            raise ValueError("Worker log record exceeds the transport limit")  # noqa: TRY003

    def _run(self) -> None:
        deadline: float | None = None
        try:
            while True:
                self._register_pending()
                if self._stopped.is_set() and deadline is None:
                    deadline = time.monotonic() + _DRAIN_TIMEOUT
                    self._register_pending()
                if deadline is not None and (not self._buffers or time.monotonic() >= deadline):
                    break
                for key, _ in self._selector.select(timeout=0.05):
                    connection = key.fileobj
                    if connection is self._wake_read:
                        with suppress(BlockingIOError):
                            self._wake_read.recv(1024)
                    elif isinstance(connection, socket.socket):
                        self._read(connection)
        except Exception:
            self._on_failure()
        finally:
            # Closing admission before cleanup also covers an unexpected receiver failure.
            with self._lock:
                self._stopped.set()
            self._register_pending()
            for connection in list(self._buffers):
                self._close_channel(connection)
            self._resources.close()
