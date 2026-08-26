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

"""FastAPI application factory for the PowerContext Server."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from typing import TYPE_CHECKING, Annotated, Any, Protocol, TypeVar, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind
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
from powercontext.builtin.artifacts.skill import (
    ExternalSkillNotFoundError,
    ExternalSkillRegistryUnavailableError,
    ExternalSkillSnapshotUnavailableError,
    Skill,
)
from powercontext.builtin.artifacts.skill import (
    ExternalSkillResolution as RuntimeExternalSkillResolution,
)
from powercontext.builtin.handoff_report import (
    HandoffReportApplication,
    HandoffReportBusyError,
    HandoffReportCatalogArgumentError,
    HandoffReportError,
    HandoffReportInconsistentError,
    HandoffReportTooLargeError,
    ProjectConflictError,
    ProjectNotFoundError,
    ReportPeriodInput,
    ScopeAlreadyGroupedError,
    WorkspaceBindingConflictError,
    WorkspaceBindingNotFoundError,
    WorkstreamConflictError,
    WorkstreamNotFoundError,
)
from powercontext.builtin.handoff_report.models import (
    ExternalReference as ReportExternalReference,
)
from powercontext.builtin.handoff_report.models import (
    ProjectDescriptor as DomainProjectDescriptor,
)
from powercontext.builtin.handoff_report.models import ReportActivityEvent as DomainReportActivityEvent
from powercontext.builtin.handoff_report.models import RepositoryRef as DomainRepositoryRef
from powercontext.builtin.handoff_report.models import (
    WorkstreamDescriptor as DomainWorkstreamDescriptor,
)
from powercontext.builtin.handoff_report.repository import (
    ActivityEventConflictError,
    InvalidActivityEventError,
    InvalidActivityRepositoryArgumentError,
)
from powercontext.builtin.inference.errors import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.review import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    CandidateNotFoundError,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.review.generation import (
    GeneratedCandidateResult as RuntimeGeneratedCandidateResult,
)
from powercontext.builtin.review.generation import GenerationCapabilityUnavailableError
from powercontext.builtin.runtime import (
    ActivateHandoff,
    CaptureSource,
    ExperienceCandidate,
    ExternalSkillList,
    ExternalSkillScanResult,
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
    ReviewedCandidate,
    ReviewedCandidatePage,
    SkillCandidate,
    SourceReceipt,
)
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest as RuntimeApproveArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    GenerateExperienceRequest as RuntimeGenerateExperienceRequest,
)
from powercontext.builtin.runtime import (
    GenerateSkillRequest as RuntimeGenerateSkillRequest,
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
from powercontext.builtin.runtime import GetSkillRequest as RuntimeGetSkillRequest
from powercontext.builtin.runtime import (
    ImportExternalSkillRequest as RuntimeImportExternalSkillRequest,
)
from powercontext.builtin.runtime import (
    ListArtifactCandidatesRequest as RuntimeListArtifactCandidatesRequest,
)
from powercontext.builtin.runtime import ListExternalSkillsRequest as RuntimeListExternalSkillsRequest
from powercontext.builtin.runtime import (
    PrepareContextRequest as RuntimePrepareContextRequest,
)
from powercontext.builtin.runtime import (
    PreparedContext as RuntimePreparedContext,
)
from powercontext.builtin.runtime import (
    ProposeExperienceRequest as RuntimeProposeExperienceRequest,
)
from powercontext.builtin.runtime import ProposeSkillRequest as RuntimeProposeSkillRequest
from powercontext.builtin.runtime import (
    RejectArtifactCandidateRequest as RuntimeRejectArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    RememberMemoryRequest as RuntimeRememberMemoryRequest,
)
from powercontext.builtin.runtime import ResolveExternalSkillRequest as RuntimeResolveExternalSkillRequest
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
from powercontext.builtin.runtime import (
    Statistics as RuntimeStatistics,
)
from powercontext.builtin.runtime import (
    StatisticsPeriod as RuntimeStatisticsPeriod,
)
from powercontext.builtin.work import (
    AcknowledgeHandoff as RuntimeAcknowledgeHandoff,
)
from powercontext.builtin.work import (
    CreateWorkContract as RuntimeCreateWorkContract,
)
from powercontext.builtin.work import (
    HandoffAcknowledgement as RuntimeHandoffAcknowledgement,
)
from powercontext.builtin.work import (
    HandoffCurrentWork as RuntimeHandoffCurrentWork,
)
from powercontext.builtin.work import PreparedWorkHandoff as RuntimePreparedWorkHandoff
from powercontext.builtin.work import RecordTaskOutcome as RuntimeRecordTaskOutcome
from powercontext.builtin.work import WorkSourceReceipt as RuntimeWorkSourceReceipt
from powercontext.errors import (
    ArtifactNotFoundError,
    PowerContextError,
    RevisionConflictError,
    SourceConflictError,
)
from powercontext.http import (
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    AttachHandoffReportWorkspaceRequest,
    Capabilities,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CommitHandoffRequest,
    CommittedHandoff,
    ContinueHandoffRequest,
    CreateHandoffReportProjectRequest,
    CreateWorkContractRequest,
    DetachHandoffReportWorkspaceRequest,
    ErrorDetail,
    ErrorResponse,
    ExperienceArtifact,
    ExternalSkillResolution,
    FinalizeHandoffRequest,
    FlushMemoryRequest,
    FlushMemoryResponse,
    GeneratedCandidateResponse,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetHandoffReportProjectRequest,
    GetHandoffReportRequest,
    GetHandoffReportWorkspaceRequest,
    GetMemoryEntryRequest,
    GetSkillRequest,
    GetStatsRequest,
    HandoffAcknowledgement,
    HandoffCurrentWorkRequest,
    HandoffReportActivity,
    HandoffReportActivityPage,
    HandoffReportResponse,
    HandoffReportWorkspaceBinding,
    HandoffSelection,
    HealthResponse,
    ImportExternalSkillRequest,
    KnownHandoffScope,
    KnownHandoffScopePage,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ListHandoffReportActivitiesRequest,
    ListHandoffReportKnownScopesRequest,
    ListHandoffReportProjectsRequest,
    ListHandoffReportWorkstreamsRequest,
    ListMemoryChangesRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesRequest,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryMutationResponse,
    PrepareContextRequest,
    PreparedContext,
    PreparedWorkHandoff,
    PrepareHandoffRequest,
    ProjectDescriptor,
    ProjectPage,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    PurgeHandoffReportActivitiesRequest,
    PurgeHandoffReportActivitiesResponse,
    ReadinessResponse,
    ReadinessStatus,
    RecordHandoffReportActivityRequest,
    RecordTaskOutcomeRequest,
    RegisterHandoffReportWorkstreamRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    ResolveExternalSkillRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
    ScanExternalSkillsRequest,
    ScanExternalSkillsResponse,
    ScopedStats,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SkillArtifact,
    StoredHandoffReportActivity,
    UpdateHandoffReportProjectRequest,
    UpdateHandoffReportWorkstreamRequest,
    WorkSourceReceipt,
    WorkstreamDescriptor,
    WorkstreamPage,
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
    ACKNOWLEDGE_HANDOFF,
    ACTIVATE_HANDOFF,
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    APPROVE_ARTIFACT_CANDIDATE,
    ATTACH_HANDOFF_REPORT_WORKSPACE,
    CAPTURE_CONTENT_SOURCE,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    CREATE_HANDOFF_REPORT_PROJECT,
    CREATE_WORK_CONTRACT,
    DETACH_HANDOFF_REPORT_WORKSPACE,
    FINALIZE_HANDOFF,
    FLUSH_MEMORY,
    GENERATE_EXPERIENCE,
    GENERATE_SKILL,
    GET_ARTIFACT_CANDIDATE,
    GET_CAPABILITIES,
    GET_EXPERIENCE,
    GET_HANDOFF_REPORT,
    GET_HANDOFF_REPORT_PROJECT,
    GET_HANDOFF_REPORT_WORKSPACE,
    GET_LIVENESS,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    GET_SKILL,
    GET_STATS,
    HANDOFF_CURRENT_WORK,
    IMPORT_EXTERNAL_SKILL,
    LIST_ARTIFACT_CANDIDATES,
    LIST_EXTERNAL_SKILLS,
    LIST_HANDOFF_REPORT_ACTIVITIES,
    LIST_HANDOFF_REPORT_KNOWN_SCOPES,
    LIST_HANDOFF_REPORT_PROJECTS,
    LIST_HANDOFF_REPORT_WORKSTREAMS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    OPENAPI_VERSION,
    PREPARE_CONTEXT,
    PREPARE_HANDOFF,
    PROPOSE_EXPERIENCE,
    PROPOSE_SKILL,
    PURGE_HANDOFF_REPORT_ACTIVITIES,
    RECORD_HANDOFF_REPORT_ACTIVITY,
    RECORD_TASK_OUTCOME,
    REGISTER_HANDOFF_REPORT_WORKSTREAM,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RESOLVE_EXTERNAL_SKILL,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    SCAN_EXTERNAL_SKILLS,
    SEARCH_MEMORY,
    UPDATE_HANDOFF_REPORT_PROJECT,
    UPDATE_HANDOFF_REPORT_WORKSTREAM,
    Operation,
)
from powercontext.http._generated.schema import OPENAPI_SCHEMA
from powercontext.server import mapping
from powercontext.server.context import (
    bind_request_id,
    current_request_id,
    reset_request_id,
)
from powercontext.server.tracing import request_id_from_span

if TYPE_CHECKING:
    from powercontext.server.metrics import ServerMetrics
    from powercontext.server.tracing import ServerTracing

REQUEST_ID_HEADER = "X-PowerContext-Request-ID"
REPORT_SELECTION_DIGEST_HEADER = "X-PowerContext-Selection-Digest"
REPORT_DIGEST_HEADER = "X-PowerContext-Report-Digest"
MAX_HANDOFF_REPORT_BYTES = 10 * 1024 * 1024
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

    async def generate(self, request: RuntimeGenerateExperienceRequest, /) -> RuntimeGeneratedCandidateResult: ...

    async def get(self, request: RuntimeGetExperienceRequest, /) -> Experience: ...


class _ExperienceApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedExperienceApplication: ...


class _ScopedSkillApplication(Protocol):
    async def propose(self, request: RuntimeProposeSkillRequest, /) -> SkillCandidate: ...

    async def generate(self, request: RuntimeGenerateSkillRequest, /) -> RuntimeGeneratedCandidateResult: ...

    async def get(self, request: RuntimeGetSkillRequest, /) -> Skill: ...


class _SkillApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedSkillApplication: ...


class _ScopedExternalSkillApplication(Protocol):
    async def scan(self) -> ExternalSkillScanResult: ...

    async def list(self, request: RuntimeListExternalSkillsRequest, /) -> ExternalSkillList: ...

    async def resolve(self, request: RuntimeResolveExternalSkillRequest, /) -> RuntimeExternalSkillResolution: ...

    async def import_managed(
        self, request: RuntimeImportExternalSkillRequest, /
    ) -> RuntimeGeneratedCandidateResult: ...


class _ExternalSkillApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedExternalSkillApplication: ...


class _ScopedReviewApplication(Protocol):
    async def list(self, request: RuntimeListArtifactCandidatesRequest, /) -> ReviewedCandidatePage: ...

    async def get(self, request: RuntimeGetArtifactCandidateRequest, /) -> ReviewedCandidate: ...

    async def approve(self, request: RuntimeApproveArtifactCandidateRequest, /) -> ReviewedCandidate: ...

    async def reject(self, request: RuntimeRejectArtifactCandidateRequest, /) -> ReviewedCandidate: ...

    async def revise(self, request: RuntimeReviseArtifactCandidateRequest, /) -> ReviewedCandidate: ...


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


class _ScopedWorkApplication(Protocol):
    async def create_contract(self, request: RuntimeCreateWorkContract, /) -> RuntimeWorkSourceReceipt: ...

    async def handoff_current(self, request: RuntimeHandoffCurrentWork, /) -> RuntimePreparedWorkHandoff: ...

    async def acknowledge(self, request: RuntimeAcknowledgeHandoff, /) -> RuntimeHandoffAcknowledgement: ...

    async def record_outcome(self, request: RuntimeRecordTaskOutcome, /) -> RuntimeWorkSourceReceipt: ...


class _WorkApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedWorkApplication: ...


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


class _ScopedStatisticsApplication(Protocol):
    async def overview(self, *, period: RuntimeStatisticsPeriod) -> RuntimeStatistics: ...


class _StatisticsApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedStatisticsApplication: ...


class ServerApplication(Protocol):
    sources: _SourceApplication
    context: _ContextApplication
    experience: _ExperienceApplication
    external_skills: _ExternalSkillApplication
    handoff: _HandoffApplication
    work: _WorkApplication
    memory: _MemoryApplication
    review: _ReviewApplication
    skill: _SkillApplication
    statistics: _StatisticsApplication
    handoff_report: HandoffReportApplication | None


class _RuntimeNotReadyError(RuntimeError):
    """Raised when an application operation is called without a Runtime binding."""


def create_app(
    *,
    application: ServerApplication | None = None,
    capability_provider: CapabilityProvider | None = None,
    readiness_probe: ReadinessProbe | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    middleware: Sequence[Middleware] = (),
    metrics: ServerMetrics | None = None,
    tracing: ServerTracing | None = None,
    handoff_report_enabled: bool = False,
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
    app.state.metrics = metrics
    app.state.tracing = tracing
    app.state.capabilities = Capabilities(
        source_types=[],
        artifact_families=[],
        memory_extraction=False,
        experience_generation=False,
        managed_skill_generation=False,
        external_skill_registry=False,
        handoff_generation=False,
        search_modes=[],
        context_versions=[],
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request_id_from_span()
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        try:
            response = await call_next(request)
            request.state.request_id = getattr(request.state, "request_id", current_request_id() or request_id)
            response.headers[REQUEST_ID_HEADER] = request.state.request_id
        finally:
            reset_request_id(token)
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_request",
            message="The request violates the API contract.",
            details={"errors": _validation_error_details(error)},
        )

    @app.exception_handler(_RuntimeNotReadyError)
    @app.exception_handler(PowerContextError)
    async def application_error(request: Request, error: Exception) -> JSONResponse:
        response_status, code, message, details = _map_error(error)
        return _error_response(response_status, code=code, message=message, details=details)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", request_id_from_span())
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
    _add_route(app, GET_STATS, get_stats)
    if handoff_report_enabled:
        _add_route(app, CREATE_HANDOFF_REPORT_PROJECT, create_handoff_report_project)
        _add_route(app, GET_HANDOFF_REPORT_PROJECT, get_handoff_report_project)
        _add_route(app, UPDATE_HANDOFF_REPORT_PROJECT, update_handoff_report_project)
        _add_route(app, LIST_HANDOFF_REPORT_PROJECTS, list_handoff_report_projects)
        _add_route(app, LIST_HANDOFF_REPORT_KNOWN_SCOPES, list_handoff_report_known_scopes)
        _add_route(app, REGISTER_HANDOFF_REPORT_WORKSTREAM, register_handoff_report_workstream)
        _add_route(app, LIST_HANDOFF_REPORT_WORKSTREAMS, list_handoff_report_workstreams)
        _add_route(app, UPDATE_HANDOFF_REPORT_WORKSTREAM, update_handoff_report_workstream)
        _add_route(app, RECORD_HANDOFF_REPORT_ACTIVITY, record_handoff_report_activity)
        _add_route(app, LIST_HANDOFF_REPORT_ACTIVITIES, list_handoff_report_activities)
        _add_route(app, PURGE_HANDOFF_REPORT_ACTIVITIES, purge_handoff_report_activities)
        _add_route(app, GET_HANDOFF_REPORT_WORKSPACE, get_handoff_report_workspace)
        _add_route(app, ATTACH_HANDOFF_REPORT_WORKSPACE, attach_handoff_report_workspace)
        _add_route(app, DETACH_HANDOFF_REPORT_WORKSPACE, detach_handoff_report_workspace)
        _add_route(app, GET_HANDOFF_REPORT, get_handoff_report)
    _add_route(app, CAPTURE_CONTENT_SOURCE, capture_content_source)
    _add_route(app, FLUSH_MEMORY, flush_memory)
    _add_route(app, REMEMBER_MEMORY, remember_memory)
    _add_route(app, SEARCH_MEMORY, search_memory)
    _add_route(app, PREPARE_CONTEXT, prepare_context)
    _add_route(app, CREATE_WORK_CONTRACT, create_work_contract)
    _add_route(app, HANDOFF_CURRENT_WORK, handoff_current_work)
    _add_route(app, ACKNOWLEDGE_HANDOFF, acknowledge_handoff)
    _add_route(app, RECORD_TASK_OUTCOME, record_task_outcome)
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
    _add_route(app, GENERATE_EXPERIENCE, generate_experience)
    _add_route(app, GET_EXPERIENCE, get_experience)
    _add_route(app, PROPOSE_SKILL, propose_skill)
    _add_route(app, GENERATE_SKILL, generate_skill)
    _add_route(app, GET_SKILL, get_skill)
    _add_route(app, SCAN_EXTERNAL_SKILLS, scan_external_skills)
    _add_route(app, LIST_EXTERNAL_SKILLS, list_external_skills)
    _add_route(app, RESOLVE_EXTERNAL_SKILL, resolve_external_skill)
    _add_route(app, IMPORT_EXTERNAL_SKILL, import_external_skill)
    _add_route(app, LIST_ARTIFACT_CANDIDATES, list_artifact_candidates)
    _add_route(app, GET_ARTIFACT_CANDIDATE, get_artifact_candidate)
    _add_route(app, APPROVE_ARTIFACT_CANDIDATE, approve_artifact_candidate)
    _add_route(app, REJECT_ARTIFACT_CANDIDATE, reject_artifact_candidate)
    _add_route(app, REVISE_ARTIFACT_CANDIDATE, revise_artifact_candidate)

    def canonical_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = deepcopy(OPENAPI_SCHEMA)
            if not handoff_report_enabled:
                paths = cast(dict[str, Any], app.openapi_schema["paths"])
                app.openapi_schema["paths"] = {
                    path: value for path, value in paths.items() if not path.startswith("/v1/handoff-reports/")
                }
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
        status.HTTP_503_SERVICE_UNAVAILABLE if readiness.status is ReadinessStatus.NOT_READY else status.HTTP_200_OK
    )
    return JSONResponse(content=readiness.model_dump(mode="json"), status_code=response_status)


async def get_capabilities(request: Request) -> Capabilities:
    capability_provider: CapabilityProvider | None = request.app.state.capability_provider
    if capability_provider is not None:
        return capability_provider()
    return request.app.state.capabilities


async def get_stats(
    request: Annotated[GetStatsRequest, Query()],
    response: Response,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ScopedStats:
    response.headers["Cache-Control"] = "no-store"
    result = await application.statistics.for_scope(request.scope_id).overview(
        period=RuntimeStatisticsPeriod(request.period.value)
    )
    return mapping.statistics_response(result)


async def create_handoff_report_project(
    request: CreateHandoffReportProjectRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> ProjectDescriptor:
    result = await report.create_project(
        project_key=request.project_key,
        title=request.title,
        description=request.description,
        default_locale=request.default_locale.value,
        timezone=request.timezone,
    )
    return _project_descriptor_response(result)


async def get_handoff_report_project(
    request: GetHandoffReportProjectRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> ProjectDescriptor:
    return _project_descriptor_response(await report.get_project(request.project_id))


async def update_handoff_report_project(
    request: UpdateHandoffReportProjectRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> ProjectDescriptor:
    descriptor = DomainProjectDescriptor.model_validate_json(request.project.model_dump_json(by_alias=True))
    return _project_descriptor_response(await report.update_project(descriptor, request.expected_version))


async def list_handoff_report_projects(
    request: ListHandoffReportProjectsRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> ProjectPage:
    result = await report.list_projects(
        cursor=request.cursor,
        limit=request.limit,
        include_archived=request.include_archived,
    )
    return ProjectPage(
        items=[_project_descriptor_response(item) for item in result.items],
        next_cursor=result.next_cursor,
    )


async def list_handoff_report_known_scopes(
    request: ListHandoffReportKnownScopesRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> KnownHandoffScopePage:
    result = await report.list_known_scopes(cursor=request.cursor, limit=request.limit)
    return KnownHandoffScopePage(
        items=[KnownHandoffScope(scope_id=scope_id) for scope_id in result.items],
        next_cursor=result.next_cursor,
    )


async def register_handoff_report_workstream(
    request: RegisterHandoffReportWorkstreamRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> WorkstreamDescriptor:
    values = request.model_dump(mode="json")
    result = await report.register_workstream(
        project_id=request.project_id,
        scope_id=request.scope_id,
        title=request.title,
        kind=request.kind.value,
        key=request.key,
        catalog_state=request.catalog_state.value,
        external_refs=tuple(ReportExternalReference.model_validate(value) for value in values["external_refs"]),
        labels=tuple(str(value) for value in values["labels"]),
    )
    return _workstream_descriptor_response(result)


async def list_handoff_report_workstreams(
    request: ListHandoffReportWorkstreamsRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> WorkstreamPage:
    result = await report.list_workstreams(
        request.project_id,
        cursor=request.cursor,
        limit=request.limit,
        include_archived=request.include_archived,
    )
    return WorkstreamPage(
        items=[_workstream_descriptor_response(item) for item in result.items],
        next_cursor=result.next_cursor,
    )


async def update_handoff_report_workstream(
    request: UpdateHandoffReportWorkstreamRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> WorkstreamDescriptor:
    descriptor = DomainWorkstreamDescriptor.model_validate_json(request.workstream.model_dump_json(by_alias=True))
    return _workstream_descriptor_response(await report.update_workstream(descriptor, request.expected_version))


async def record_handoff_report_activity(
    request: RecordHandoffReportActivityRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> StoredHandoffReportActivity:
    values = request.model_dump(mode="json")
    event = DomainReportActivityEvent.model_validate_json(
        json.dumps({
            **values,
            "event_id": f"evt_{uuid4().hex}",
            "observed_at": datetime.now(UTC).isoformat(),
            "trust": "untrusted_observation",
        })
    )
    stored = await report.record_activity(event)
    return StoredHandoffReportActivity(
        cursor=stored.cursor,
        event=HandoffReportActivity.model_validate(stored.payload),
    )


async def list_handoff_report_activities(
    request: ListHandoffReportActivitiesRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> HandoffReportActivityPage:
    page = await report.list_activities(
        request.project_id,
        period_start=request.period_start,
        period_end=request.period_end,
        sources=None if request.sources is None else tuple(value.value for value in request.sources),
        after_cursor=request.after_cursor,
        through_cursor=request.through_cursor,
        limit=request.limit,
    )
    return HandoffReportActivityPage(
        items=[
            HandoffReportActivity.model_validate(item.model_dump(mode="json", by_alias=True)) for item in page.items
        ],
        next_cursor=page.next_cursor,
        high_watermark=page.high_watermark,
    )


async def purge_handoff_report_activities(
    request: PurgeHandoffReportActivitiesRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> PurgeHandoffReportActivitiesResponse:
    deleted = await report.purge_activities(request.project_id, request.observed_before)
    return PurgeHandoffReportActivitiesResponse(deleted_count=deleted)


async def get_handoff_report_workspace(
    request: GetHandoffReportWorkspaceRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> HandoffReportWorkspaceBinding:
    binding = await report.get_workspace_binding(request.workspace_instance_id)
    return HandoffReportWorkspaceBinding.model_validate(binding.model_dump(mode="json", by_alias=True))


async def attach_handoff_report_workspace(
    request: AttachHandoffReportWorkspaceRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> HandoffReportWorkspaceBinding:
    binding = await report.attach_workspace_binding(
        workspace_instance_id=request.workspace_instance_id,
        project_id=request.project_id,
        repository_ref=DomainRepositoryRef.model_validate(request.repository_ref.model_dump(mode="json")),
        expected_version=request.expected_version,
    )
    return HandoffReportWorkspaceBinding.model_validate(binding.model_dump(mode="json", by_alias=True))


async def detach_handoff_report_workspace(
    request: DetachHandoffReportWorkspaceRequest,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> HandoffReportWorkspaceBinding:
    binding = await report.detach_workspace_binding(request.workspace_instance_id, request.expected_version)
    return HandoffReportWorkspaceBinding.model_validate(binding.model_dump(mode="json", by_alias=True))


async def get_handoff_report(
    request: GetHandoffReportRequest,
    response: Response,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
) -> HandoffReportResponse | Response:
    result = await report.get_report(
        request.scope_id,
        locale=None if request.locale is None else request.locale.value,
        include_evidence_checks=request.include_evidence_checks,
        report_format=request.format.value,
        include_archived=request.include_archived,
        period=(
            None
            if request.period is None
            else ReportPeriodInput(
                start=request.period.start,
                end=request.period.end,
                timezone=request.period.timezone,
                compare_to_previous_period=request.period.compare_to_previous_period,
            )
        ),
    )
    selection_digest = cast(str, result.selection_digest)
    report_digest = cast(str, result.report_digest)
    response.headers["Cache-Control"] = "no-store"
    response.headers[REPORT_SELECTION_DIGEST_HEADER] = selection_digest
    response.headers[REPORT_DIGEST_HEADER] = report_digest
    payload = result.model_dump(mode="json", by_alias=True)
    markdown = None
    if request.format.value == "markdown":
        from powercontext.builtin.handoff_report.rendering import render_markdown

        markdown = render_markdown(result)
        payload = None
    if markdown is not None:
        _require_report_size(len(markdown.encode("utf-8")), result)
        headers = {
            "Cache-Control": "no-store",
            REPORT_SELECTION_DIGEST_HEADER: selection_digest,
            REPORT_DIGEST_HEADER: report_digest,
        }
        if request.download:
            headers["Content-Disposition"] = 'attachment; filename="handoff-report.md"'
        return Response(markdown, media_type="text/markdown; charset=utf-8", headers=headers)
    if request.download:
        headers = {
            "Cache-Control": "no-store",
            REPORT_SELECTION_DIGEST_HEADER: selection_digest,
            REPORT_DIGEST_HEADER: report_digest,
            "Content-Disposition": 'attachment; filename="handoff-report.json"',
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        _require_report_size(len(encoded), result)
        return Response(encoded, media_type="application/json", headers=headers)
    response_payload = HandoffReportResponse(
        format=request.format,
        report=payload,
        markdown=markdown,
        selection_digest=selection_digest,
        report_digest=report_digest,
    )
    encoded_response = response_payload.model_dump_json(by_alias=True).encode("utf-8")
    _require_report_size(len(encoded_response), result)
    return response_payload


def _require_report_size(estimated_bytes: int, report: Any) -> None:
    if estimated_bytes <= MAX_HANDOFF_REPORT_BYTES:
        return
    raise HandoffReportTooLargeError(
        estimated_bytes=estimated_bytes,
        selected_workstreams=report.coverage.selected_workstreams,
        selected_activities=len(report.activity_selection),
    )


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


async def create_work_contract(
    request: CreateWorkContractRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> WorkSourceReceipt:
    result = await application.work.for_scope(request.scope_id).create_contract(
        mapping.create_work_contract_request(request)
    )
    return mapping.work_source_receipt_response(result)


async def handoff_current_work(
    request: HandoffCurrentWorkRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> PreparedWorkHandoff:
    result = await application.work.for_scope(request.scope_id).handoff_current(
        mapping.handoff_current_work_request(request)
    )
    return mapping.prepared_work_handoff_response(result)


async def acknowledge_handoff(
    request: AcknowledgeHandoffRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> HandoffAcknowledgement:
    result = await application.work.for_scope(request.scope_id).acknowledge(
        mapping.acknowledge_handoff_request(request)
    )
    return mapping.handoff_acknowledgement_response(result)


async def record_task_outcome(
    request: RecordTaskOutcomeRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> WorkSourceReceipt:
    result = await application.work.for_scope(request.scope_id).record_outcome(
        mapping.record_task_outcome_request(request)
    )
    return mapping.work_source_receipt_response(result)


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


async def generate_experience(
    request: GenerateExperienceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> GeneratedCandidateResponse:
    result = await application.experience.for_scope(request.scope_id).generate(
        mapping.generate_experience_request(request)
    )
    return mapping.generated_candidate_response(result)


async def get_experience(
    request: GetExperienceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ExperienceArtifact:
    result = await application.experience.for_scope(request.scope_id).get(mapping.get_experience_request(request))
    return mapping.experience_response(result)


async def propose_skill(
    request: ProposeSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCandidate:
    result = await application.skill.for_scope(request.scope_id).propose(mapping.propose_skill_request(request))
    return mapping.candidate_response(result)


async def generate_skill(
    request: GenerateSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> GeneratedCandidateResponse:
    result = await application.skill.for_scope(request.scope_id).generate(mapping.generate_skill_request(request))
    return mapping.generated_candidate_response(result)


async def get_skill(
    request: GetSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SkillArtifact:
    result = await application.skill.for_scope(request.scope_id).get(mapping.get_skill_request(request))
    return mapping.skill_response(result)


async def scan_external_skills(
    request: ScanExternalSkillsRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ScanExternalSkillsResponse:
    result = await application.external_skills.for_scope(request.scope_id).scan()
    return mapping.scan_external_skills_response(result)


async def list_external_skills(
    request: ListExternalSkillsRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ListExternalSkillsResponse:
    result = await application.external_skills.for_scope(request.scope_id).list(
        mapping.list_external_skills_request(request)
    )
    return mapping.list_external_skills_response(result)


async def resolve_external_skill(
    request: ResolveExternalSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ExternalSkillResolution:
    result = await application.external_skills.for_scope(request.scope_id).resolve(
        mapping.resolve_external_skill_request(request)
    )
    return mapping.external_skill_resolution(result)


async def import_external_skill(
    request: ImportExternalSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> GeneratedCandidateResponse:
    result = await application.external_skills.for_scope(request.scope_id).import_managed(
        mapping.import_external_skill_request(request)
    )
    return mapping.generated_candidate_response(result)


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


def _require_handoff_report_application(request: Request) -> HandoffReportApplication:
    application = _require_application(request)
    if application.handoff_report is None:
        raise _RuntimeNotReadyError
    return application.handoff_report


def _project_descriptor_response(value: DomainProjectDescriptor) -> ProjectDescriptor:
    return ProjectDescriptor.model_validate(value.model_dump(mode="json", by_alias=True))


def _workstream_descriptor_response(value: DomainWorkstreamDescriptor) -> WorkstreamDescriptor:
    return WorkstreamDescriptor.model_validate(value.model_dump(mode="json", by_alias=True))


def _add_route(
    app: FastAPI,
    operation: Operation[_RequestT, _ResponseT],
    endpoint: Callable[..., Awaitable[_ResponseT | Response]],
) -> None:
    app.add_api_route(
        operation.path,
        _observe_application_operation(app, operation, endpoint),
        methods=[operation.method],
        operation_id=operation.operation_id,
        response_model=operation.response_type,
        status_code=operation.success_status,
        responses=operation.responses,
        summary=operation.summary,
        tags=list(operation.tags),
    )


def _observe_application_operation(
    app: FastAPI,
    operation: Operation[_RequestT, _ResponseT],
    endpoint: Callable[..., Awaitable[_ResponseT | Response]],
) -> Callable[..., Awaitable[_ResponseT | Response]]:
    @wraps(endpoint)
    async def observed_endpoint(*args: Any, **kwargs: Any) -> _ResponseT | Response:
        started_at = perf_counter()
        span = _start_application_span(app, operation)
        try:
            result = await endpoint(*args, **kwargs)
        except asyncio.CancelledError:
            _observe_application(app, operation, "cancelled", started_at)
            _log_operation(
                logging.INFO,
                "PowerContext application operation cancelled",
                operation=operation.operation_id,
                outcome="cancelled",
                started_at=started_at,
            )
            _finish_span(span, "cancelled")
            raise
        except Exception as error:
            _observe_application(app, operation, "failure", started_at)
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
            _finish_span(span, "failure", error=error)
            raise
        outcome = _application_outcome(result)
        _observe_application(app, operation, outcome, started_at)
        _finish_span(span, outcome)
        return result

    return observed_endpoint


def _start_application_span(app: FastAPI, operation: Operation[Any, Any]) -> Any | None:
    if "health" in operation.tags:
        return None
    tracing = app.state.tracing
    if tracing is None:
        return None
    return tracing.start_span(
        f"powercontext {operation.operation_id}",
        kind=SpanKind.INTERNAL,
        attributes={
            "powercontext.operation.name": operation.operation_id,
            "powercontext.operation.unit": "application",
            **({"powercontext.request.id": request_id} if (request_id := current_request_id()) is not None else {}),
        },
    )


def _finish_span(span: Any | None, outcome: str, *, error: BaseException | None = None) -> None:
    if span is not None:
        with suppress(Exception):
            span.finish(outcome, error=error)


def _observe_application(
    app: FastAPI,
    operation: Operation[Any, Any],
    outcome: str,
    started_at: float,
) -> None:
    if "health" in operation.tags:
        return
    metrics = app.state.metrics
    if metrics is not None:
        with suppress(Exception):
            metrics.observe_application(operation.operation_id, outcome, started_at)


def _application_outcome(result: object) -> str:
    if isinstance(result, FlushMemoryResponse) and result.status.value == "idle":
        return "noop"
    return "success"


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


def _validation_error_details(error: RequestValidationError) -> list[Any]:
    details: list[Any] = []
    for item in error.errors():
        if isinstance(item, dict):
            details.append({key: value for key, value in item.items() if key != "input"})
        else:
            details.append(item)
    return details


def _map_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None]:
    if isinstance(error, _RuntimeNotReadyError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "runtime_not_ready", "The Runtime is not ready.", None
    if isinstance(error, ExternalSkillRegistryUnavailableError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "external_skill_registry_unavailable",
            "The external Skill Registry is unavailable.",
            None,
        )
    if isinstance(error, ExternalSkillNotFoundError):
        return status.HTTP_404_NOT_FOUND, "external_skill_not_found", "The external Skill was not found.", None
    if isinstance(error, ExternalSkillSnapshotUnavailableError):
        return (
            status.HTTP_409_CONFLICT,
            "external_skill_snapshot_unavailable",
            "The exact external Skill snapshot is unavailable.",
            None,
        )
    if isinstance(error, GenerationCapabilityUnavailableError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "generation_unavailable",
            "Artifact generation is not configured.",
            {"family": error.family},
        )
    candidate_error = _map_candidate_error(error)
    if candidate_error is not None:
        return candidate_error
    availability_error = _map_availability_error(error)
    if availability_error is not None:
        return availability_error
    report_error = _map_report_error(error)
    if report_error is not None:
        return report_error
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


def _map_report_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, (ProjectNotFoundError, WorkstreamNotFoundError, WorkspaceBindingNotFoundError)):
        return status.HTTP_404_NOT_FOUND, error.code, "The requested Handoff Report catalog value was not found.", None
    if isinstance(
        error,
        (ProjectConflictError, WorkstreamConflictError, ScopeAlreadyGroupedError, WorkspaceBindingConflictError),
    ):
        details = {
            name: getattr(error, name)
            for name in ("expected_version", "current_version", "project_id", "scope_id", "workspace_instance_id")
            if hasattr(error, name)
        }
        return (
            status.HTTP_409_CONFLICT,
            error.code,
            "The Handoff Report catalog value is stale or conflicting.",
            details,
        )
    if isinstance(error, ActivityEventConflictError):
        return (
            status.HTTP_409_CONFLICT,
            "activity_event_conflict",
            "The Activity idempotency key already identifies different content.",
            {"source": error.source, "source_event_id": error.source_event_id},
        )
    if isinstance(error, HandoffReportBusyError):
        return (
            status.HTTP_409_CONFLICT,
            "handoff_report_busy",
            "Handoff heads changed while the report was being assembled.",
            {"attempts": error.attempts},
        )
    if isinstance(error, HandoffReportTooLargeError):
        return (
            status.HTTP_413_CONTENT_TOO_LARGE,
            "handoff_report_too_large",
            "The Handoff Report is too large; narrow the Workstream or Activity selection.",
            {
                "estimated_bytes": error.estimated_bytes,
                "selected_workstreams": error.selected_workstreams,
                "selected_activities": error.selected_activities,
            },
        )
    if isinstance(error, HandoffReportInconsistentError):
        return (
            status.HTTP_409_CONFLICT,
            "handoff_report_inconsistent",
            "The frozen Handoff selection could not be read consistently.",
            {"scope_id": error.scope_id},
        )
    if isinstance(
        error,
        (HandoffReportCatalogArgumentError, InvalidActivityEventError, InvalidActivityRepositoryArgumentError),
    ):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", "The request is invalid.", None
    if isinstance(error, HandoffReportError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "handoff_report_unavailable", "Handoff Report is unavailable.", None
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
        return status.HTTP_503_SERVICE_UNAVAILABLE, "inference_timeout", "Model inference timed out.", None
    if isinstance(error, InferenceUnavailableError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "inference_unavailable", "Model inference is unavailable.", None
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
