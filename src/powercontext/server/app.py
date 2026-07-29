"""FastAPI application factory for the PowerContext Server."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from re import fullmatch
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import Lifespan

from powercontext.builtin.artifacts.memory.errors import (
    CapabilityNotSupportedError,
    InvalidMemoryCandidateError,
    InvalidMemoryCitationError,
    InvalidMemoryEvidenceError,
    MemoryEntryInactiveError,
    MemoryEntryNotFoundError,
)
from powercontext.builtin.inference.errors import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.runtime import (
    CaptureSource,
    InvalidRuntimeRequestError,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryMutationResult,
    MemorySearchPage,
    SourceReceipt,
)
from powercontext.builtin.runtime import (
    GetMemoryEntryRequest as RuntimeGetMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    PrepareContextRequest as RuntimePrepareContextRequest,
)
from powercontext.builtin.runtime import (
    PreparedContext as RuntimePreparedContext,
)
from powercontext.builtin.runtime import (
    RememberMemoryRequest as RuntimeRememberMemoryRequest,
)
from powercontext.builtin.runtime import (
    RetireMemoryEntryRequest as RuntimeRetireMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    ReviseMemoryEntryRequest as RuntimeReviseMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    SearchMemoryRequest as RuntimeSearchMemoryRequest,
)
from powercontext.errors import (
    ArtifactNotFoundError,
    PowerContextError,
    RevisionConflictError,
    SourceConflictError,
)
from powercontext.http import (
    Capabilities,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    ErrorDetail,
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
    PrepareContextRequest,
    PreparedContext,
    ReadinessResponse,
    ReadinessStatus,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
)
from powercontext.http._generated.operations import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    CAPTURE_CONTENT_SOURCE,
    FLUSH_MEMORY,
    GET_CAPABILITIES,
    GET_LIVENESS,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    OPENAPI_VERSION,
    PREPARE_CONTEXT,
    REMEMBER_MEMORY,
    RETIRE_MEMORY_ENTRY,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
    Operation,
)
from powercontext.http._generated.schema import OPENAPI_SCHEMA
from powercontext.server import mapping

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = r"[A-Za-z0-9._:-]{1,128}"
logger = logging.getLogger(__name__)

CapabilityProvider = Callable[[], Capabilities]
ReadinessProbe = Callable[[], Awaitable[ReadinessResponse]]
_RequestT = TypeVar("_RequestT")
_ResponseT = TypeVar("_ResponseT")


class _ScopedSourceApplication(Protocol):
    async def capture(self, value: CaptureSource, /) -> SourceReceipt: ...


class _SourceApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedSourceApplication: ...


class _ScopedContextApplication(Protocol):
    async def prepare(self, request: RuntimePrepareContextRequest, /) -> RuntimePreparedContext: ...


class _ContextApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedContextApplication: ...


class _ScopedMemoryApplication(Protocol):
    async def remember(self, request: RuntimeRememberMemoryRequest, /) -> MemoryMutationResult: ...

    async def search(self, request: RuntimeSearchMemoryRequest, /) -> MemorySearchPage: ...

    async def list(self, *, include_inactive: bool = False) -> MemoryEntriesPage: ...

    async def get(self, request: RuntimeGetMemoryEntryRequest, /) -> MemoryEntryRecord: ...

    async def revise(self, request: RuntimeReviseMemoryEntryRequest, /) -> MemoryMutationResult: ...

    async def retire(self, request: RuntimeRetireMemoryEntryRequest, /) -> MemoryMutationResult: ...

    async def changes(self, *, since_revision: int | None = None) -> MemoryChangesPage: ...

    async def flush(self, /, *, limit: int | None = None) -> MemoryFlushResult: ...


class _MemoryApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedMemoryApplication: ...


class ServerApplication(Protocol):
    sources: _SourceApplication
    context: _ContextApplication
    memory: _MemoryApplication


class _RuntimeNotReadyError(RuntimeError):
    """Raised when an application operation is called without a Runtime binding."""


def create_app(  # noqa: C901
    *,
    application: ServerApplication | None = None,
    capability_provider: CapabilityProvider | None = None,
    readiness_probe: ReadinessProbe | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    """Build the HTTP adapter around an optional Runtime application binding."""

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )
    app.openapi_version = OPENAPI_VERSION
    app.state.application = application
    app.state.capabilities = Capabilities(
        source_types=[],
        artifact_families=[],
        memory_extraction=False,
        search_modes=[],
        context_versions=[],
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_request",
            message="The request violates the API contract.",
            details={"errors": error.errors()},
        )

    @app.exception_handler(_RuntimeNotReadyError)
    @app.exception_handler(PowerContextError)
    async def application_error(request: Request, error: Exception) -> JSONResponse:
        response_status, code, message, details = _map_error(error)
        if response_status == status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.exception(
                "PowerContext request failed",
                exc_info=error,
                extra={"request_id": getattr(request.state, "request_id", None)},
            )
        return _error_response(response_status, code=code, message=message, details=details)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", _request_id(None))
        logger.exception("PowerContext request failed", exc_info=error, extra={"request_id": request_id})
        response = _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="The Server failed.",
            details=None,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    async def get_liveness() -> HealthResponse:
        return HealthResponse(status="ok")

    async def get_readiness() -> JSONResponse:
        readiness = (
            await readiness_probe() if readiness_probe is not None else _runtime_readiness(app.state.application)
        )
        response_status = (
            status.HTTP_200_OK if readiness.status is ReadinessStatus.READY else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return JSONResponse(content=readiness.model_dump(mode="json"), status_code=response_status)

    async def get_capabilities() -> Capabilities:
        if capability_provider is not None:
            return capability_provider()
        return app.state.capabilities

    async def capture_content_source(request: CaptureContentSourceRequest) -> CaptureContentSourceResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.sources.for_scope(request.scope_id).capture(mapping.capture_request(request))
        return mapping.capture_response(result)

    async def flush_memory(request: FlushMemoryRequest) -> FlushMemoryResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).flush()
        return mapping.flush_response(result)

    async def remember_memory(request: RememberMemoryRequest) -> MemoryMutationResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).remember(mapping.remember_request(request))
        return mapping.mutation_response(result)

    async def search_memory(request: SearchMemoryRequest) -> SearchMemoryResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).search(mapping.search_request(request))
        return mapping.search_response(result)

    async def prepare_context(request: PrepareContextRequest) -> PreparedContext:
        runtime = _require_application(app.state.application)
        result = await runtime.context.for_scope(request.scope_id).prepare(mapping.prepare_context_request(request))
        return mapping.prepared_context_response(result)

    async def list_memory_entries(request: ListMemoryEntriesRequest) -> ListMemoryEntriesResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).list(
            include_inactive=request.include_inactive,
        )
        return mapping.entries_response(result)

    async def get_memory_entry(request: GetMemoryEntryRequest) -> MemoryEntry:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).get(mapping.get_request(request))
        return mapping.memory_entry(result)

    async def revise_memory_entry(request: ReviseMemoryEntryRequest) -> MemoryMutationResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).revise(mapping.revise_request(request))
        return mapping.mutation_response(result)

    async def retire_memory_entry(request: RetireMemoryEntryRequest) -> MemoryMutationResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).retire(mapping.retire_request(request))
        return mapping.mutation_response(result)

    async def list_memory_changes(request: ListMemoryChangesRequest) -> ListMemoryChangesResponse:
        runtime = _require_application(app.state.application)
        result = await runtime.memory.for_scope(request.scope_id).changes(since_revision=request.since_revision)
        return mapping.changes_response(result)

    _add_route(app, GET_LIVENESS, get_liveness)
    _add_route(app, GET_READINESS, get_readiness)
    _add_route(app, GET_CAPABILITIES, get_capabilities)
    _add_route(app, CAPTURE_CONTENT_SOURCE, capture_content_source)
    _add_route(app, FLUSH_MEMORY, flush_memory)
    _add_route(app, REMEMBER_MEMORY, remember_memory)
    _add_route(app, SEARCH_MEMORY, search_memory)
    _add_route(app, PREPARE_CONTEXT, prepare_context)
    _add_route(app, LIST_MEMORY_ENTRIES, list_memory_entries)
    _add_route(app, GET_MEMORY_ENTRY, get_memory_entry)
    _add_route(app, REVISE_MEMORY_ENTRY, revise_memory_entry)
    _add_route(app, RETIRE_MEMORY_ENTRY, retire_memory_entry)
    _add_route(app, LIST_MEMORY_CHANGES, list_memory_changes)

    def canonical_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = deepcopy(OPENAPI_SCHEMA)
        return app.openapi_schema

    app.openapi = canonical_openapi  # ty: ignore[invalid-assignment]
    return app


def _runtime_readiness(application: ServerApplication | None) -> ReadinessResponse:
    if application is None:
        return ReadinessResponse(
            status=ReadinessStatus.NOT_READY,
            checks={"runtime": "not_ready"},
        )
    return ReadinessResponse(
        status=ReadinessStatus.READY,
        checks={"runtime": "ready"},
    )


def _require_application(application: ServerApplication | None) -> ServerApplication:
    if application is None:
        raise _RuntimeNotReadyError
    return application


def _request_id(candidate: str | None) -> str:
    if candidate is not None and fullmatch(REQUEST_ID_PATTERN, candidate):
        return candidate
    return str(uuid4())


def _add_route(
    app: FastAPI,
    operation: Operation[_RequestT, _ResponseT],
    endpoint: Callable[..., Awaitable[_ResponseT | Response]],
) -> None:
    app.add_api_route(
        operation.path,
        endpoint,
        methods=[operation.method],
        operation_id=operation.operation_id,
        response_model=operation.response_type,
        status_code=operation.success_status,
        responses=operation.responses,
        summary=operation.summary,
        tags=list(operation.tags),
    )


def _error_response(
    response_status: int,
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    error = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=response_status, content=error.model_dump(mode="json"))


def _map_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None]:
    if isinstance(error, _RuntimeNotReadyError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "runtime_not_ready", "The Runtime is not ready.", None
    if isinstance(error, (ArtifactNotFoundError, MemoryEntryNotFoundError)):
        return status.HTTP_404_NOT_FOUND, "memory_not_found", "The requested Memory value was not found.", None
    if isinstance(error, SourceConflictError):
        return status.HTTP_409_CONFLICT, "source_conflict", "The Source identity has different content.", None
    if isinstance(error, RevisionConflictError):
        return status.HTTP_409_CONFLICT, "revision_conflict", "The Memory Revision is stale.", None
    if isinstance(error, MemoryEntryInactiveError):
        return status.HTTP_409_CONFLICT, "memory_entry_inactive", "The Memory entry is inactive.", None
    if isinstance(error, CapabilityNotSupportedError):
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "capability_not_supported",
            "The requested capability is unavailable.",
            {"capability": error.capability},
        )
    if isinstance(
        error,
        (
            InvalidMemoryCandidateError,
            InvalidMemoryCitationError,
            InvalidMemoryEvidenceError,
            InvalidRuntimeRequestError,
        ),
    ):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", "The request is invalid.", None
    if isinstance(error, InferenceTimeoutError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "inference_timeout", "Memory inference timed out.", None
    if isinstance(error, InferenceUnavailableError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "inference_unavailable", "Memory inference is unavailable.", None
    return status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "The Server failed.", None
