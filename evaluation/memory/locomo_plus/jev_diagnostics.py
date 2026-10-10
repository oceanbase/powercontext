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

"""Content-free HTTP evidence isolated to the current logical Jev decision."""

from __future__ import annotations

import errno
import hashlib
import socket
import ssl
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

_ERROR_TYPES = frozenset({
    "BaseException",
    "Exception",
    "RuntimeError",
    "ValueError",
    "TypeError",
    "KeyError",
    "TimeoutError",
    "CancelledError",
    "OSError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "BrokenPipeError",
    "PermissionError",
    "gaierror",
    "herror",
    "SSLError",
    "SSLCertVerificationError",
    "SSLEOFError",
    "SSLZeroReturnError",
    "SSLWantReadError",
    "SSLWantWriteError",
    "ConnectError",
    "ReadError",
    "WriteError",
    "CloseError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
    "LocalProtocolError",
    "ProxyError",
    "UnsupportedProtocol",
    "DecodingError",
    "TooManyRedirects",
    "HTTPError",
    "HTTPStatusError",
    "NetworkError",
    "ProtocolError",
    "TimeoutException",
    "TransportError",
    "InferenceUnavailableError",
    "InvalidInferenceOutputError",
})
_DNS_ERRORS = {getattr(socket, name): name for name in dir(socket) if name.startswith("EAI_")}
_SSL_ERRORS = {getattr(ssl, name): name for name in dir(ssl) if name.startswith("SSL_ERROR_")}
_SSL_LIBRARIES = frozenset({"SSL", "PEM", "X509", "ASN1", "BIO", "SYS"})
_SSL_REASONS = frozenset({
    "CERTIFICATE_VERIFY_FAILED",
    "WRONG_VERSION_NUMBER",
    "UNEXPECTED_EOF_WHILE_READING",
    "TLSV1_ALERT_PROTOCOL_VERSION",
    "TLSV1_ALERT_INTERNAL_ERROR",
    "TLSV1_ALERT_DECODE_ERROR",
    "TLSV1_ALERT_UNKNOWN_CA",
    "SSLV3_ALERT_HANDSHAKE_FAILURE",
    "SSLV3_ALERT_BAD_CERTIFICATE",
    "TLSV1_ALERT_ACCESS_DENIED",
    "TLSV1_ALERT_DECRYPT_ERROR",
    "SSLV3_ALERT_CERTIFICATE_EXPIRED",
    "NO_SHARED_CIPHER",
    "UNSUPPORTED_PROTOCOL",
    "VERSION_TOO_LOW",
    "VERSION_TOO_HIGH",
    "DECRYPTION_FAILED_OR_BAD_RECORD_MAC",
    "BAD_RECORD_TYPE",
    "CERTIFICATE_REQUIRED",
})
_TRACE_OPERATIONS = frozenset({
    "connection.connect_tcp",
    "connection.connect_unix_socket",
    "connection.start_tls",
    "connection.close",
    "proxy.start_tls",
    "http11.send_request_headers",
    "http11.send_request_body",
    "http11.receive_response_headers",
    "http11.receive_response_body",
    "http11.response_closed",
    "http2.send_connection_init",
    "http2.send_request_headers",
    "http2.send_request_body",
    "http2.receive_response_headers",
    "http2.receive_response_body",
    "http2.response_closed",
})
_ID_HEADERS = ("x-request-id", "request-id", "x-correlation-id", "cf-ray", "x-amzn-trace-id")


def safe_exception_chain(error: BaseException) -> list[dict[str, Any]]:
    """Retain fixed error categories and codes, never exception messages or arbitrary names."""
    pending: list[tuple[BaseException | None, int | None, str]] = [(error, None, "root")]
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    while pending and len(result) < 12:
        current, parent, relation = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        name = type(current).__name__
        row: dict[str, Any] = {
            "type": name if name in _ERROR_TYPES else "OtherError",
            "relation": relation,
            "parent": parent,
        }
        if isinstance(current, ssl.SSLError) and type(current.errno) is int and current.errno in _SSL_ERRORS:
            row.update(ssl_errno=current.errno, ssl_errno_name=_SSL_ERRORS[current.errno])
        elif (
            isinstance(current, OSError)
            and not isinstance(current, (ssl.SSLError, socket.herror))
            and type(current.errno) is int
        ):
            names = _DNS_ERRORS if isinstance(current, socket.gaierror) else errno.errorcode
            if current.errno in names:
                row.update(errno=current.errno, errno_name=names[current.errno])
        if isinstance(current, ssl.SSLError):
            for field, allowed in (("library", _SSL_LIBRARIES), ("reason", _SSL_REASONS)):
                value = getattr(current, field, None)
                if value is not None:
                    row[field] = value if isinstance(value, str) and value in allowed else "OTHER"
            code = getattr(current, "verify_code", None)
            if type(code) is int and 0 <= code <= 4095:
                row["verify_code"] = code
        index = len(result)
        result.append(row)
        pending.extend([(current.__cause__, index, "cause"), (current.__context__, index, "context")])
    return result


class JevTransportTrace:
    """Record one decision's HTTP invocation and bounded, allowlisted transport events."""

    def __init__(self) -> None:
        self.started = perf_counter()
        self.started_at = datetime.now(UTC).isoformat()
        self.events: list[dict[str, Any]] = []
        self.dropped_events = 0
        self.method: str | None = None
        self.response: dict[str, Any] | None = None
        self.client_error_chain: list[dict[str, Any]] = []
        self.post_invocations = 0

    async def trace(self, name: str, info: dict[str, Any]) -> None:
        operation, _, outcome = name.rpartition(".")
        if operation not in _TRACE_OPERATIONS or outcome not in {"started", "complete", "failed"}:
            return
        if len(self.events) >= 256:
            self.dropped_events += 1
            return
        method = getattr(info.get("request"), "method", None)
        if method in (b"CONNECT", b"POST"):
            self.method = method.decode("ascii")
        event: dict[str, Any] = {
            "event": name,
            "elapsed_ms": (perf_counter() - self.started) * 1000,
            "method": self.method,
        }
        if isinstance(info.get("exception"), BaseException):
            event["exception_chain"] = safe_exception_chain(info["exception"])
        value = info.get("return_value")
        if operation.endswith("receive_response_headers") and outcome == "complete" and isinstance(value, tuple):
            index = 0 if operation.startswith("http2.") else 1
            if len(value) > index and type(value[index]) is int and 100 <= value[index] <= 599:
                event["status"] = value[index]
        self.events.append(event)

    def response_received(self, response: httpx.Response) -> None:
        identifiers = {
            name: hashlib.sha256(response.headers[name].encode()).hexdigest()
            for name in _ID_HEADERS
            if name in response.headers
        }
        self.response = {"status": response.status_code, "request_id_sha256": identifiers}

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "event_clock": "elapsed milliseconds since logical decision start, including slot waiting",
            "post_invocations": self.post_invocations,
            "events": self.events,
            "dropped_events": self.dropped_events,
            "client_error_chain": self.client_error_chain,
            "response": self.response,
        }


CURRENT_JEV_TRACE: ContextVar[JevTransportTrace | None] = ContextVar("locomo_plus_jev_transport", default=None)
