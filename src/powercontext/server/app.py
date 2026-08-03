"""FastAPI application factory for the PowerContext Server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from copy import deepcopy
from functools import wraps
from re import fullmatch
from time import perf_counter
from typing import Annotated, Any, Protocol, TypeVar
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import Lifespan

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.handoff import (
    HandoffEvidenceUnavailableError,
    HandoffGenerationUnavailableError,
    HandoffScopeMismatchError,
    InvalidHandoffGenerationError,
    InvalidHandoffReferenceError,
)
from powercontext.builtin.artifacts.memory.errors import (
    CapabilityNotSupportedError,
    InvalidMemoryCandidateError,
    InvalidMemoryCitationError,
    InvalidMemoryEvidenceError,
    MemoryEntryInactiveError,
    MemoryEntryNotFoundError,
)
from powercontext.builtin.inference.errors import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.review import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    CandidateNotFoundError,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.runtime import (
    ActivateHandoff,
    CaptureSource,
    ExperienceCandidate,
    ExperienceCandidatePage,
    Handoff,
    HandoffActivation,
    HandoffDraft,
    HandoffResolution,
    InvalidRuntimeRequestError,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryMutationResult,
    MemorySearchPage,
    PreparedHandoff,
    PrepareHandoff,
    SourceReceipt,
)
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest as RuntimeApproveArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    GetArtifactCandidateRequest as RuntimeGetArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    GetExperienceRequest as RuntimeGetExperienceRequest,
)
from powercontext.builtin.runtime import (
    GetMemoryEntryRequest as RuntimeGetMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    ListArtifactCandidatesRequest as RuntimeListArtifactCandidatesRequest,
)
from powercontext.builtin.runtime import (
    PrepareContextRequest as RuntimePrepareContextRequest,
)
from powercontext.builtin.runtime import (
    PreparedContext as RuntimePreparedContext,
)
from powercontext.builtin.runtime import (
    ProposeExperienceRequest as RuntimeProposeExperienceRequest,
)
from powercontext.builtin.runtime import (
    RejectArtifactCandidateRequest as RuntimeRejectArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    RememberMemoryRequest as RuntimeRememberMemoryRequest,
)
from powercontext.builtin.runtime import (
    RetireMemoryEntryRequest as RuntimeRetireMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    ReviseArtifactCandidateRequest as RuntimeReviseArtifactCandidateRequest,
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
    ActivateHandoffRequest,
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    Capabilities,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CommitHandoffRequest,
    CommittedHandoff,
    ContinueHandoffRequest,
    ErrorDetail,
    ErrorResponse,
    ExperienceArtifact,
    FinalizeHandoffRequest,
    FlushMemoryRequest,
    FlushMemoryResponse,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetMemoryEntryRequest,
    HandoffSelection,
    HealthResponse,
    ListArtifactCandidatesRequest,
    ListMemoryChangesRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesRequest,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryMutationResponse,
    PrepareContextRequest,
    PreparedContext,
    PrepareHandoffRequest,
    ProposeExperienceRequest,
    ReadinessResponse,
    ReadinessStatus,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
)
from powercontext.http import (
    HandoffActivation as TransportHandoffActivation,
)
from powercontext.http import (
    HandoffDraft as TransportHandoffDraft,
)
from powercontext.http import (
    HandoffResolution as TransportHandoffResolution,
)
from powercontext.http import (
    PreparedHandoff as TransportPreparedHandoff,
)
from powercontext.http._generated.operations import (
    ACTIVATE_HANDOFF,
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    FINALIZE_HANDOFF,
    FLUSH_MEMORY,
    GET_ARTIFACT_CANDIDATE,
    GET_CAPABILITIES,
    GET_EXPERIENCE,
    GET_LIVENESS,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    LIST_ARTIFACT_CANDIDATES,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    OPENAPI_VERSION,
    PREPARE_CONTEXT,
    PREPARE_HANDOFF,
    PROPOSE_EXPERIENCE,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
    Operation,
)
from powercontext.http._generated.schema import OPENAPI_SCHEMA
from powercontext.server import mapping
from powercontext.server.context import (
    bind_request_id,
    current_request_id,
    reset_request_id,
)

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


class _ScopedExperienceApplication(Protocol):
    async def propose(self, request: RuntimeProposeExperienceRequest, /) -> ExperienceCandidate: ...

    async def get(self, request: RuntimeGetExperienceRequest, /) -> Experience: ...


class _ExperienceApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedExperienceApplication: ...


class _ScopedReviewApplication(Protocol):
    async def list(self, request: RuntimeListArtifactCandidatesRequest, /) -> ExperienceCandidatePage: ...

    async def get(self, request: RuntimeGetArtifactCandidateRequest, /) -> ExperienceCandidate: ...

    async def approve(self, request: RuntimeApproveArtifactCandidateRequest, /) -> ExperienceCandidate: ...

    async def reject(self, request: RuntimeRejectArtifactCandidateRequest, /) -> ExperienceCandidate: ...

    async def revise(self, request: RuntimeReviseArtifactCandidateRequest, /) -> ExperienceCandidate: ...


class _ReviewApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedReviewApplication: ...


class _ScopedHandoffApplication(Protocol):
    async def activate(self, request: ActivateHandoff, /) -> HandoffActivation: ...

    async def prepare(self, request: PrepareHandoff, /) -> HandoffDraft: ...

    async def finalize(self, draft: HandoffDraft, /) -> PreparedHandoff: ...

    async def commit(self, prepared: PreparedHandoff, /) -> Handoff: ...

    async def continue_from(self, handoff: PreparedHandoff | ArtifactRef, /) -> HandoffResolution: ...

    async def continue_latest(self) -> HandoffResolution: ...


class _HandoffApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedHandoffApplication: ...


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
    experience: _ExperienceApplication
    handoff: _HandoffApplication
    memory: _MemoryApplication
    review: _ReviewApplication


class _RuntimeNotReadyError(RuntimeError):
    """Raised when an application operation is called without a Runtime binding."""


def create_app(
    *,
    application: ServerApplication | None = None,
    capability_provider: CapabilityProvider | None = None,
    readiness_probe: ReadinessProbe | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    middleware: Sequence[Middleware] = (),
) -> FastAPI:
    """Build the HTTP adapter around an optional Runtime application binding."""

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        middleware=list(middleware),
    )
    app.openapi_version = OPENAPI_VERSION
    app.state.application = application
    app.state.capability_provider = capability_provider
    app.state.readiness_probe = readiness_probe
    app.state.capabilities = Capabilities(
        source_types=[],
        artifact_families=[],
        memory_extraction=False,
        handoff_generation=False,
        search_modes=[],
        context_versions=[],
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
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
        return _error_response(response_status, code=code, message=message, details=details)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", _request_id(None))
        response = _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="The Server failed.",
            details=None,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    _add_route(app, GET_LIVENESS, get_liveness)
    _add_route(app, GET_READINESS, get_readiness)
    _add_route(app, GET_CAPABILITIES, get_capabilities)
    _add_route(app, CAPTURE_CONTENT_SOURCE, capture_content_source)
    _add_route(app, FLUSH_MEMORY, flush_memory)
    _add_route(app, REMEMBER_MEMORY, remember_memory)
    _add_route(app, SEARCH_MEMORY, search_memory)
    _add_route(app, PREPARE_CONTEXT, prepare_context)
    _add_route(app, ACTIVATE_HANDOFF, activate_handoff)
    _add_route(app, PREPARE_HANDOFF, prepare_handoff)
    _add_route(app, FINALIZE_HANDOFF, finalize_handoff)
    _add_route(app, COMMIT_HANDOFF, commit_handoff)
    _add_route(app, CONTINUE_HANDOFF, continue_handoff)
    _add_route(app, LIST_MEMORY_ENTRIES, list_memory_entries)
    _add_route(app, GET_MEMORY_ENTRY, get_memory_entry)
    _add_route(app, REVISE_MEMORY_ENTRY, revise_memory_entry)
    _add_route(app, RETIRE_MEMORY_ENTRY, retire_memory_entry)
    _add_route(app, LIST_MEMORY_CHANGES, list_memory_changes)
    _add_route(app, PROPOSE_EXPERIENCE, propose_experience)
    _add_route(app, GET_EXPERIENCE, get_experience)
    _add_route(app, LIST_ARTIFACT_CANDIDATES, list_artifact_candidates)
    _add_route(app, GET_ARTIFACT_CANDIDATE, get_artifact_candidate)
    _add_route(app, APPROVE_ARTIFACT_CANDIDATE, approve_artifact_candidate)
    _add_route(app, REJECT_ARTIFACT_CANDIDATE, reject_artifact_candidate)
    _add_route(app, REVISE_ARTIFACT_CANDIDATE, revise_artifact_candidate)

    def canonical_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = deepcopy(OPENAPI_SCHEMA)
        return app.openapi_schema

    app.openapi = canonical_openapi  # ty: ignore[invalid-assignment]
    return app


async def get_liveness() -> HealthResponse:
    return HealthResponse(status="ok")


async def get_readiness(request: Request) -> JSONResponse:
    readiness_probe: ReadinessProbe | None = request.app.state.readiness_probe
    readiness = (
        await readiness_probe() if readiness_probe is not None else _runtime_readiness(request.app.state.application)
    )
    response_status = (
        status.HTTP_200_OK if readiness.status is ReadinessStatus.READY else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(content=readiness.model_dump(mode="json"), status_code=response_status)


async def get_capabilities(request: Request) -> Capabilities:
    capability_provider: CapabilityProvider | None = request.app.state.capability_provider
    if capability_provider is not None:
        return capability_provider()
    return request.app.state.capabilities


async def capture_content_source(
    request: CaptureContentSourceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> CaptureContentSourceResponse:
    result = await application.sources.for_scope(request.scope_id).capture(mapping.capture_request(request))
    return mapping.capture_response(result)


async def flush_memory(
    request: FlushMemoryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> FlushMemoryResponse:
    result = await application.memory.for_scope(request.scope_id).flush()
    return mapping.flush_response(result)


async def remember_memory(
    request: RememberMemoryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> MemoryMutationResponse:
    result = await application.memory.for_scope(request.scope_id).remember(mapping.remember_request(request))
    return mapping.mutation_response(result)


async def search_memory(
    request: SearchMemoryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SearchMemoryResponse:
    result = await application.memory.for_scope(request.scope_id).search(mapping.search_request(request))
    return mapping.search_response(result)


async def prepare_context(
    request: PrepareContextRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> PreparedContext:
    result = await application.context.for_scope(request.scope_id).prepare(mapping.prepare_context_request(request))
    return mapping.prepared_context_response(result)


async def prepare_handoff(
    request: PrepareHandoffRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> TransportHandoffDraft:
    result = await application.handoff.for_scope(request.scope_id).prepare(mapping.prepare_handoff_request(request))
    return mapping.handoff_draft_response(result)


async def activate_handoff(
    request: ActivateHandoffRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> TransportHandoffActivation:
    result = await application.handoff.for_scope(request.scope_id).activate(mapping.activate_handoff_request(request))
    return mapping.handoff_activation_response(result)


async def finalize_handoff(
    request: FinalizeHandoffRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> TransportPreparedHandoff:
    result = await application.handoff.for_scope(request.scope_id).finalize(
        mapping.runtime_handoff_draft(request.draft)
    )
    return mapping.prepared_handoff_response(result)


async def commit_handoff(
    request: CommitHandoffRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> CommittedHandoff:
    result = await application.handoff.for_scope(request.scope_id).commit(
        mapping.runtime_prepared_handoff(request.handoff)
    )
    return mapping.committed_handoff_response(result)


async def continue_handoff(
    request: ContinueHandoffRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> TransportHandoffResolution:
    handoff = application.handoff.for_scope(request.scope_id)
    if request.selection is HandoffSelection.LATEST:
        _require_handoff_selection(request, prepared=False, revision=False)
        result = await handoff.continue_latest()
    elif request.selection is HandoffSelection.PREPARED:
        _require_handoff_selection(request, prepared=True, revision=False)
        prepared = request.prepared
        if prepared is None:
            raise InvalidRuntimeRequestError("handoff-selection")
        result = await handoff.continue_from(mapping.runtime_prepared_handoff(prepared))
    else:
        _require_handoff_selection(request, prepared=False, revision=True)
        revision = request.revision
        if revision is None:
            raise InvalidRuntimeRequestError("handoff-selection")
        result = await handoff.continue_from(mapping.runtime_artifact_reference(revision))
    return mapping.handoff_resolution_response(result)


async def list_memory_entries(
    request: ListMemoryEntriesRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ListMemoryEntriesResponse:
    result = await application.memory.for_scope(request.scope_id).list(
        include_inactive=request.include_inactive,
    )
    return mapping.entries_response(result)


async def get_memory_entry(
    request: GetMemoryEntryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> MemoryEntry:
    result = await application.memory.for_scope(request.scope_id).get(mapping.get_request(request))
    return mapping.memory_entry(result)


async def revise_memory_entry(
    request: ReviseMemoryEntryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> MemoryMutationResponse:
    result = await application.memory.for_scope(request.scope_id).revise(mapping.revise_request(request))
    return mapping.mutation_response(result)


async def retire_memory_entry(
    request: RetireMemoryEntryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> MemoryMutationResponse:
    result = await application.memory.for_scope(request.scope_id).retire(mapping.retire_request(request))
    return mapping.mutation_response(result)


async def list_memory_changes(
    request: ListMemoryChangesRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ListMemoryChangesResponse:
    result = await application.memory.for_scope(request.scope_id).changes(since_revision=request.since_revision)
    return mapping.changes_response(result)


async def propose_experience(
    request: ProposeExperienceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCandidate:
    result = await application.experience.for_scope(request.scope_id).propose(
        mapping.propose_experience_request(request)
    )
    return mapping.candidate_response(result)


async def get_experience(
    request: GetExperienceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ExperienceArtifact:
    result = await application.experience.for_scope(request.scope_id).get(mapping.get_experience_request(request))
    return mapping.experience_response(result)


async def list_artifact_candidates(
    request: ListArtifactCandidatesRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCandidatePage:
    result = await application.review.for_scope(request.scope_id).list(mapping.list_candidates_request(request))
    return mapping.candidate_page_response(result)


async def get_artifact_candidate(
    request: GetArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCandidate:
    result = await application.review.for_scope(request.scope_id).get(mapping.get_candidate_request(request))
    return mapping.candidate_response(result)


async def approve_artifact_candidate(
    request: ApproveArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCandidate:
    result = await application.review.for_scope(request.scope_id).approve(mapping.approve_candidate_request(request))
    return mapping.candidate_response(result)


async def reject_artifact_candidate(
    request: RejectArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCandidate:
    result = await application.review.for_scope(request.scope_id).reject(mapping.reject_candidate_request(request))
    return mapping.candidate_response(result)


async def revise_artifact_candidate(
    request: ReviseArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCandidate:
    result = await application.review.for_scope(request.scope_id).revise(mapping.revise_candidate_request(request))
    return mapping.candidate_response(result)


def _require_handoff_selection(
    request: ContinueHandoffRequest,
    *,
    prepared: bool,
    revision: bool,
) -> None:
    if (request.prepared is not None) != prepared or (request.revision is not None) != revision:
        raise InvalidRuntimeRequestError("handoff-selection")


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


def _require_application(request: Request) -> ServerApplication:
    application: ServerApplication | None = request.app.state.application
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
        _log_application_operation(operation, endpoint),
        methods=[operation.method],
        operation_id=operation.operation_id,
        response_model=operation.response_type,
        status_code=operation.success_status,
        responses=operation.responses,
        summary=operation.summary,
        tags=list(operation.tags),
    )


def _log_application_operation(
    operation: Operation[_RequestT, _ResponseT],
    endpoint: Callable[..., Awaitable[_ResponseT | Response]],
) -> Callable[..., Awaitable[_ResponseT | Response]]:
    @wraps(endpoint)
    async def observed_endpoint(*args: Any, **kwargs: Any) -> _ResponseT | Response:
        started_at = perf_counter()
        try:
            return await endpoint(*args, **kwargs)
        except asyncio.CancelledError:
            _log_operation(
                logging.INFO,
                "PowerContext application operation cancelled",
                operation=operation.operation_id,
                outcome="cancelled",
                started_at=started_at,
            )
            raise
        except Exception as error:
            response_status, error_code, _, _ = _map_error(error)
            _log_operation(
                logging.ERROR if response_status >= status.HTTP_500_INTERNAL_SERVER_ERROR else logging.WARNING,
                "PowerContext application operation failed",
                operation=operation.operation_id,
                outcome="failure",
                started_at=started_at,
                error=error,
                error_code=error_code,
            )
            raise

    return observed_endpoint


def _log_operation(
    level: int,
    message: str,
    *,
    operation: str,
    outcome: str,
    started_at: float,
    error: Exception | None = None,
    error_code: str | None = None,
) -> None:
    extra = {
        "event": "application.operation.completed",
        "operation": operation,
        "outcome": outcome,
        "request_id": current_request_id(),
        "unit": "application",
        "duration_ms": max(perf_counter() - started_at, 0) * 1_000,
    }
    if error_code is not None:
        extra["error_code"] = error_code
    log_safely(logger, level, message, exc_info=error, extra=extra)


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
    candidate_error = _map_candidate_error(error)
    if candidate_error is not None:
        return candidate_error
    availability_error = _map_availability_error(error)
    if availability_error is not None:
        return availability_error
    return _map_domain_error(error)


def _map_candidate_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, CandidateNotFoundError):
        return status.HTTP_404_NOT_FOUND, "candidate_not_found", "The requested Candidate was not found.", None
    if isinstance(error, CandidateConflictError):
        return (
            status.HTTP_409_CONFLICT,
            "candidate_conflict",
            "The Candidate version is stale.",
            {"expected_version": error.expected_version, "current_version": error.current_version},
        )
    if isinstance(error, ArtifactTargetConflictError):
        return (
            status.HTTP_409_CONFLICT,
            "artifact_conflict",
            "The Candidate target Artifact is stale.",
            {"current": error.current.model_dump(mode="json")},
        )
    if isinstance(error, CandidateTerminalError):
        return (
            status.HTTP_409_CONFLICT,
            "candidate_terminal",
            "The Candidate is already terminal.",
            {"status": error.status},
        )
    if isinstance(error, InvalidCandidateError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", "The request is invalid.", None
    return None


def _map_domain_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None]:
    if isinstance(error, ArtifactNotFoundError):
        return status.HTTP_404_NOT_FOUND, "artifact_not_found", "The requested Artifact was not found.", None
    if isinstance(error, MemoryEntryNotFoundError):
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
            HandoffScopeMismatchError,
            InvalidHandoffReferenceError,
            InvalidRuntimeRequestError,
        ),
    ):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", "The request is invalid.", None
    if isinstance(error, InferenceTimeoutError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "inference_timeout", "Memory inference timed out.", None
    if isinstance(error, InferenceUnavailableError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "inference_unavailable", "Memory inference is unavailable.", None
    return status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "The Server failed.", None


def _map_availability_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, _RuntimeNotReadyError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "runtime_not_ready", "The Runtime is not ready.", None
    return _map_handoff_error(error)


def _map_handoff_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, HandoffEvidenceUnavailableError):
        return status.HTTP_404_NOT_FOUND, "handoff_evidence_not_found", "Cited Handoff evidence was not found.", None
    if isinstance(error, HandoffGenerationUnavailableError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "handoff_generation_unavailable",
            "Handoff generation is unavailable.",
            None,
        )
    if isinstance(error, InvalidHandoffGenerationError):
        return (
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "invalid_handoff_generation",
            "Handoff generation violated its contract.",
            {"reason": error.code},
        )
    return None
