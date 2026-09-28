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

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"request to {path} failed")


class InvalidResponseError(ClientError):
    """Raised when a successful response violates the public schema."""

    def __init__(self, path: str, *, request_id: str | None) -> None:
        self.path = path
        self.request_id = request_id
        super().__init__(f"response from {path} violated the API schema")


class OperationPendingError(ClientError):
    """Raised when a durable operation outlives the Client deadline."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"operation {operation_id} is still pending")


class OperationFailedError(ClientError):
    """Raised when a durable operation reaches failed or cancelled."""

    def __init__(self, operation_id: str, *, code: str | None) -> None:
        self.operation_id = operation_id
        self.code = code
        suffix = "" if code is None else f" ({code})"
        super().__init__(f"operation {operation_id} did not succeed{suffix}")


class ServerResponseError(ClientError):
    """Raised when the Server returns a non-success status."""

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
    "OperationFailedError",
    "OperationPendingError",
    "ServerResponseError",
    "TransportError",
    "UnauthorizedResponseError",
    "UnavailableResponseError",
    "server_response_error",
)
