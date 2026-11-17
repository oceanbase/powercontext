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

"""Bounded HTTP for plugin code that the host runs under a time budget."""

from __future__ import annotations

import socket
import threading
from http.client import IncompleteRead
from time import monotonic
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RejectRedirects(HTTPRedirectHandler):
    # typing.override needs Python 3.12, and these modules may import only the standard library.
    def redirect_request(  # ty: ignore[missing-override-decorator]
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        return None


_URL_OPENER = build_opener(_RejectRedirects)


def open_bounded(request: Request, *, timeout: float) -> Any:
    """Open one request under a hard wall-clock bound, response headers included.

    urllib applies its timeout to each individual socket read, so a server that
    trickles headers can outlive the caller's deadline. Running the open in a
    daemon worker and abandoning it on expiry keeps the caller inside its budget.
    """

    outcome: list[Any] = []

    def _open() -> None:
        try:
            outcome.append(_URL_OPENER.open(request, timeout=timeout))
        except BaseException as error:
            outcome.append(error)

    worker = threading.Thread(target=_open, name="powercontext-http", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError
    result = outcome[0] if outcome else TimeoutError()
    if isinstance(result, BaseException):
        raise result
    return result


class _DeadlineSocket:
    """Enforce one absolute deadline on every response socket read.

    ``http.client`` can consume many socket reads inside a single ``read`` call
    while it parses chunk framing, so tightening the socket timeout once per
    bounded read cannot stop a server that trickles chunk extensions. Recomputing
    the timeout before every receive keeps the caller's absolute deadline,
    framing included.
    """

    def __init__(self, sock: Any, deadline: float) -> None:
        self._sock = sock
        self._deadline = deadline

    def _remaining_time(self) -> float:
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            # Raise what the socket itself raises: on Python 3.9 socket.timeout is not TimeoutError, and SocketIO
            # fails with IndexError on any other OSError that carries no errno.
            raise socket.timeout
        return remaining

    def recv(self, *args: Any) -> Any:
        self._sock.settimeout(self._remaining_time())
        return self._sock.recv(*args)

    def recv_into(self, *args: Any) -> Any:
        self._sock.settimeout(self._remaining_time())
        return self._sock.recv_into(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


def bind_response_deadline(response: object, deadline: float) -> None:
    """Keep every socket read of one open response inside the absolute deadline."""

    if isinstance(response, HTTPError):
        response = response.fp
    raw: Any = getattr(getattr(response, "fp", None), "raw", None)
    if raw is None:
        return
    sock = getattr(raw, "_sock", None)
    if sock is None or isinstance(sock, _DeadlineSocket) or not hasattr(sock, "recv_into"):
        return
    raw._sock = _DeadlineSocket(sock, deadline)


def complete_body(response: object, body: bytes) -> bytes:
    """Return a body read to its end, failing one that ended before its announced Content-Length.

    ``http.client`` reports a truncated chunked body as ``IncompleteRead`` but returns a Content-Length body that the
    connection cut short as if it were complete, leaving the missing byte count in ``length``.
    """

    if isinstance(response, HTTPError):
        response = response.fp
    missing = getattr(response, "length", None)
    if missing:
        raise IncompleteRead(body, missing)
    return body
