"""Small handwritten facade over the public HTTP contract."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from powercontext.api import (
    Capabilities,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    ErrorResponse,
    FlushMemoryRequest,
    FlushMemoryResponse,
    GetMemoryEntryRequest,
    HealthResponse,
    ListMemoryChangesRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesRequest,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryMutationResponse,
    ReadinessResponse,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
)
from powercontext.api.generated.operations import (
    CAPTURE_CONTENT_SOURCE,
    FLUSH_MEMORY,
    GET_CAPABILITIES,
    GET_LIVENESS,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    REMEMBER_MEMORY,
    RETIRE_MEMORY_ENTRY,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
    Operation,
)
from powercontext.client.errors import InvalidResponseError, ServerResponseError, TransportError

REQUEST_ID_HEADER = "X-Request-ID"
_RequestT = TypeVar("_RequestT")
_ResponseT = TypeVar("_ResponseT")


class _Headers(Protocol):
    def get(self, key: str) -> str | None: ...


class _HttpResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> _Headers: ...

    @property
    def content(self) -> bytes: ...


class _AsyncHttpClient(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        json: object | None = None,
    ) -> _HttpResponse: ...


class PowerContextClient:
    """Async Python facade for transport-level Server operations."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        http_client: _AsyncHttpClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owned_http_client: httpx.AsyncClient | None = None
        if http_client is None:
            self._owned_http_client = httpx.AsyncClient(timeout=timeout)
            self._http_client = self._owned_http_client
        else:
            self._http_client = http_client

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only the HTTP client created by this facade."""

        if self._owned_http_client is not None:
            await self._owned_http_client.aclose()

    async def get_liveness(self) -> HealthResponse:
        """Read process liveness."""

        return await self._request(GET_LIVENESS)

    async def get_readiness(self) -> ReadinessResponse:
        """Read deployment readiness checks."""

        return await self._request(GET_READINESS)

    async def get_capabilities(self) -> Capabilities:
        """Read behavior enabled by the assembled runtime."""

        return await self._request(GET_CAPABILITIES)

    async def capture_content_source(self, request: CaptureContentSourceRequest) -> CaptureContentSourceResponse:
        """Capture raw content as durable Source evidence."""

        return await self._request(CAPTURE_CONTENT_SOURCE, request)

    async def flush_memory(self, request: FlushMemoryRequest) -> FlushMemoryResponse:
        """Run one bounded Source-to-Memory activation."""

        return await self._request(FLUSH_MEMORY, request)

    async def remember_memory(self, request: RememberMemoryRequest) -> MemoryMutationResponse:
        """Save one explicit Memory entry without creating a Source."""

        return await self._request(REMEMBER_MEMORY, request)

    async def search_memory(self, request: SearchMemoryRequest) -> SearchMemoryResponse:
        """Search active Memory entries in one scope."""

        return await self._request(SEARCH_MEMORY, request)

    async def list_memory_entries(self, request: ListMemoryEntriesRequest) -> ListMemoryEntriesResponse:
        """List the entry snapshot from the current Memory head."""

        return await self._request(LIST_MEMORY_ENTRIES, request)

    async def get_memory_entry(self, request: GetMemoryEntryRequest) -> MemoryEntry:
        """Read one exact Memory entry version."""

        return await self._request(GET_MEMORY_ENTRY, request)

    async def revise_memory_entry(self, request: ReviseMemoryEntryRequest) -> MemoryMutationResponse:
        """Revise one exact active Memory entry."""

        return await self._request(REVISE_MEMORY_ENTRY, request)

    async def retire_memory_entry(self, request: RetireMemoryEntryRequest) -> MemoryMutationResponse:
        """Deactivate one exact Memory entry without deleting history."""

        return await self._request(RETIRE_MEMORY_ENTRY, request)

    async def list_memory_changes(self, request: ListMemoryChangesRequest) -> ListMemoryChangesResponse:
        """Read compact Memory Revision changes."""

        return await self._request(LIST_MEMORY_CHANGES, request)

    async def _request(
        self,
        operation: Operation[_RequestT, _ResponseT],
        request: _RequestT | None = None,
    ) -> _ResponseT:
        json_payload = None
        if request is not None:
            if operation.request_type is None:
                message = f"{operation.operation_id} does not accept a request body"
                raise TypeError(message)
            json_payload = TypeAdapter(operation.request_type).dump_python(
                request,
                mode="json",
                exclude_none=True,
            )

        try:
            response = await self._http_client.request(
                operation.method,
                f"{self._base_url}{operation.path}",
                json=json_payload,
            )
        except httpx.HTTPError as exc:
            raise TransportError(operation.path) from exc

        request_id = response.headers.get(REQUEST_ID_HEADER)
        if response.status_code != operation.success_status:
            error = _decode_error(response.content)
            raise ServerResponseError(
                status_code=response.status_code,
                request_id=request_id,
                code=None if error is None else error.error.code,
                message=None if error is None else error.error.message,
                details=None if error is None else error.error.details,
            )

        try:
            return TypeAdapter(operation.response_type).validate_json(response.content)
        except ValidationError as exc:
            raise InvalidResponseError(
                operation.path,
                request_id=request_id,
            ) from exc


def _decode_error(content: bytes) -> ErrorResponse | None:
    try:
        return ErrorResponse.model_validate_json(content)
    except ValidationError:
        return None
