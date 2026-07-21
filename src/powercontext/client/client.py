"""Small handwritten facade over the public HTTP contract."""

from __future__ import annotations

from types import TracebackType
from typing import Self, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from powercontext.api import Capabilities, HealthResponse, ReadinessResponse
from powercontext.api.generated.operations import GET_CAPABILITIES, GET_LIVENESS, GET_READINESS, Operation
from powercontext.client.errors import InvalidResponseError, ServerResponseError, TransportError

REQUEST_ID_HEADER = "X-Request-ID"
_ResponseT = TypeVar("_ResponseT")


class PowerContextClient:
    """Synchronous Python facade for transport-level Server operations."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owned_http_client: httpx.Client | None = None
        if http_client is None:
            self._owned_http_client = httpx.Client(timeout=timeout)
            self._http_client = self._owned_http_client
        else:
            self._http_client = http_client

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close only the HTTP client created by this facade."""

        if self._owned_http_client is not None:
            self._owned_http_client.close()

    def get_liveness(self) -> HealthResponse:
        """Read process liveness."""

        return self._get(GET_LIVENESS)

    def get_readiness(self) -> ReadinessResponse:
        """Read deployment readiness checks."""

        return self._get(GET_READINESS)

    def get_capabilities(self) -> Capabilities:
        """Read behavior enabled by the assembled runtime."""

        return self._get(GET_CAPABILITIES)

    def _get(
        self,
        operation: Operation[_ResponseT],
    ) -> _ResponseT:
        try:
            response = self._http_client.request(operation.method, f"{self._base_url}{operation.path}")
        except httpx.HTTPError as exc:
            raise TransportError(operation.path) from exc

        if not response.is_success:
            raise ServerResponseError(
                status_code=response.status_code,
                request_id=response.headers.get(REQUEST_ID_HEADER),
            )

        try:
            return TypeAdapter(operation.response_type).validate_json(response.content)
        except ValidationError as exc:
            raise InvalidResponseError(
                operation.path,
                request_id=response.headers.get(REQUEST_ID_HEADER),
            ) from exc
