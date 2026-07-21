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


class ServerResponseError(ClientError):
    """Raised when the Server returns a non-success status."""

    def __init__(self, *, status_code: int, request_id: str | None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(f"PowerContext Server returned HTTP {status_code}")
