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

"""Stable Client SDK failures."""

from __future__ import annotations

from powercontext.errors import PowerContextError


class ClientError(PowerContextError):
    """Base exception for remote Client SDK failures."""

    request_id: str | None = None


class TransportError(ClientError):
    """Raised when no valid HTTP response was received."""

    outcome = "failed"
    status_code: int | None = None
    body_error: str | None = None

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"request to {path} failed")


class ResponseReadError(TransportError):
    """Response headers arrived, but its body was not acknowledged completely."""

    def __init__(self, path: str, *, status_code: int, request_id: str | None, body_error: str) -> None:
        super().__init__(path)
        self.status_code = status_code
        self.request_id = request_id
        self.body_error = body_error


class InvalidResponseError(ClientError):
    """Raised when a successful response violates the public schema."""

    def __init__(self, path: str, *, request_id: str | None) -> None:
        self.path = path
        self.request_id = request_id
        super().__init__(f"response from {path} violated the API schema")


class UnknownOutcomeError(TransportError):
    """A submitted mutation has no valid acknowledgement; never replay it automatically."""

    outcome = "unknown"


class ServerResponseError(ClientError):
    """Raised when the Server returns a non-success status."""

    outcome: str = "failed"

    def __init__(
        self,
        *,
        status_code: int,
        request_id: str | None,
        code: str | None = None,
        message: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self.request_id = request_id
        self.code = code
        self.server_message = message
        self.details = details
        suffix = "" if code is None else f" ({code})"
        super().__init__(f"PowerContext Server returned HTTP {status_code}{suffix}")


class UnauthorizedResponseError(ServerResponseError):
    """Raised when the Server cannot authenticate the request (HTTP 401)."""


class ForbiddenResponseError(ServerResponseError):
    """Raised when the authenticated Principal is not authorized (HTTP 403)."""


class UnavailableResponseError(ServerResponseError):
    """Raised when a required Server dependency is unavailable (HTTP 503)."""


def server_response_error(
    *,
    status_code: int,
    request_id: str | None,
    code: str | None = None,
    message: str | None = None,
    details: dict[str, object] | None = None,
) -> ServerResponseError:
    """Build the stable status-specific Client failure for one error response."""

    error_type = {
        401: UnauthorizedResponseError,
        403: ForbiddenResponseError,
        503: UnavailableResponseError,
    }.get(status_code, ServerResponseError)
    return error_type(
        status_code=status_code,
        request_id=request_id,
        code=code,
        message=message,
        details=details,
    )


__all__ = (
    "ClientError",
    "ForbiddenResponseError",
    "InvalidResponseError",
    "ServerResponseError",
    "TransportError",
    "UnauthorizedResponseError",
    "UnavailableResponseError",
    "UnknownOutcomeError",
    "server_response_error",
)
