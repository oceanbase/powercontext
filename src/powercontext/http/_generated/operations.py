# generated from openapi/powercontext.yaml; do not edit.

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, JsonValue

from powercontext.http._generated.models import (
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
    DetachHandoffReportWorkspaceRequest,
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
    HandoffActivation,
    HandoffDraft,
    HandoffReportActivityPage,
    HandoffReportResponse,
    HandoffReportWorkspaceBinding,
    HandoffResolution,
    HealthResponse,
    ImportExternalSkillRequest,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ListHandoffReportActivitiesRequest,
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
    PreparedHandoff,
    PrepareHandoffRequest,
    ProjectDescriptor,
    ProjectPage,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    PurgeHandoffReportActivitiesRequest,
    PurgeHandoffReportActivitiesResponse,
    ReadinessResponse,
    RecordHandoffReportActivityRequest,
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
    WorkstreamDescriptor,
    WorkstreamPage,
)

OPENAPI_VERSION = "3.0.3"
API_TITLE = "PowerContext API"
API_DESCRIPTION = "Remote PowerContext transport. Runtime behavior is reported by /v1/capabilities."
API_VERSION = "0.0.1"

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class Operation(BaseModel, Generic[RequestT, ResponseT]):
    method: str
    path: str
    operation_id: str
    request_type: type[RequestT] | None
    request_location: Literal["body", "query"] | None
    response_type: type[ResponseT]
    success_status: int
    summary: str
    tags: tuple[str, ...]
    responses: dict[int | str, dict[str, JsonValue]]


GET_LIVENESS = Operation[None, HealthResponse](
    method="GET",
    path="/health/live",
    operation_id="get_liveness",
    request_type=None,
    request_location=None,
    response_type=HealthResponse,
    success_status=200,
    summary="Get process liveness",
    tags=("health",),
    responses={
        200: {
            "description": "The API process is alive.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        }
    },
)

GET_READINESS = Operation[None, ReadinessResponse](
    method="GET",
    path="/health/ready",
    operation_id="get_readiness",
    request_type=None,
    request_location=None,
    response_type=ReadinessResponse,
    success_status=200,
    summary="Get deployment readiness",
    tags=("health",),
    responses={
        200: {
            "description": "Required Server bindings are ready; optional capabilities may be degraded.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        503: {
            "description": "Required Server bindings are not ready.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
    },
)

GET_CAPABILITIES = Operation[None, Capabilities](
    method="GET",
    path="/v1/capabilities",
    operation_id="get_capabilities",
    request_type=None,
    request_location=None,
    response_type=Capabilities,
    success_status=200,
    summary="Get runtime capabilities",
    tags=("capabilities",),
    responses={
        200: {
            "description": "Behavior enabled by the assembled runtime.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
    },
)

CAPTURE_CONTENT_SOURCE = Operation[CaptureContentSourceRequest, CaptureContentSourceResponse](
    method="POST",
    path="/v1/sources/content",
    operation_id="capture_content_source",
    request_type=CaptureContentSourceRequest,
    request_location="body",
    response_type=CaptureContentSourceResponse,
    success_status=202,
    summary="Capture durable ContentSource evidence",
    tags=("sources",),
    responses={
        202: {
            "description": "The Source is durably stored for later processing.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

PREPARE_CONTEXT = Operation[PrepareContextRequest, PreparedContext](
    method="POST",
    path="/v1/context/prepare",
    operation_id="prepare_context",
    request_type=PrepareContextRequest,
    request_location="body",
    response_type=PreparedContext,
    success_status=200,
    summary="Prepare bounded context for an Agent turn",
    tags=("context",),
    responses={
        200: {
            "description": "Final context ready for direct injection, or a normal empty result.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

ACTIVATE_HANDOFF = Operation[ActivateHandoffRequest, HandoffActivation](
    method="POST",
    path="/v1/handoff/activate",
    operation_id="activate_handoff",
    request_type=ActivateHandoffRequest,
    request_location="body",
    response_type=HandoffActivation,
    success_status=200,
    summary="Activate Handoff generation at a Source boundary",
    tags=("handoff",),
    responses={
        200: {
            "description": "A generated inspectable Draft, or an ignored boundary that was already consumed.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

PREPARE_HANDOFF = Operation[PrepareHandoffRequest, HandoffDraft](
    method="POST",
    path="/v1/handoff/prepare",
    operation_id="prepare_handoff",
    request_type=PrepareHandoffRequest,
    request_location="body",
    response_type=HandoffDraft,
    success_status=200,
    summary="Generate an inspectable Handoff Draft",
    tags=("handoff",),
    responses={
        200: {
            "description": "An uncommitted Draft generated from the selected exact evidence.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

FINALIZE_HANDOFF = Operation[FinalizeHandoffRequest, PreparedHandoff](
    method="POST",
    path="/v1/handoff/finalize",
    operation_id="finalize_handoff",
    request_type=FinalizeHandoffRequest,
    request_location="body",
    response_type=PreparedHandoff,
    success_status=200,
    summary="Finalize an inspected Handoff Draft",
    tags=("handoff",),
    responses={
        200: {
            "description": "A temporary Handoff ready for direct transfer or explicit commit.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

COMMIT_HANDOFF = Operation[CommitHandoffRequest, CommittedHandoff](
    method="POST",
    path="/v1/handoff/commit",
    operation_id="commit_handoff",
    request_type=CommitHandoffRequest,
    request_location="body",
    response_type=CommittedHandoff,
    success_status=200,
    summary="Commit an explicit Handoff milestone",
    tags=("handoff",),
    responses={
        200: {
            "description": "The committed immutable Handoff Revision.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

CONTINUE_HANDOFF = Operation[ContinueHandoffRequest, HandoffResolution](
    method="POST",
    path="/v1/handoff/continue",
    operation_id="continue_handoff",
    request_type=ContinueHandoffRequest,
    request_location="body",
    response_type=HandoffResolution,
    success_status=200,
    summary="Resolve a Handoff as untrusted historical input",
    tags=("handoff",),
    responses={
        200: {
            "description": "Resolved content and per-statement evidence availability.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

FLUSH_MEMORY = Operation[FlushMemoryRequest, FlushMemoryResponse](
    method="POST",
    path="/v1/memory/flush",
    operation_id="flush_memory",
    request_type=FlushMemoryRequest,
    request_location="body",
    response_type=FlushMemoryResponse,
    success_status=200,
    summary="Process the pending Source window into Memory",
    tags=("memory",),
    responses={
        200: {
            "description": "The activation completed or found no pending Sources.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REMEMBER_MEMORY = Operation[RememberMemoryRequest, MemoryMutationResponse](
    method="POST",
    path="/v1/memory/remember",
    operation_id="remember_memory",
    request_type=RememberMemoryRequest,
    request_location="body",
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Remember explicit Memory content",
    tags=("memory",),
    responses={
        200: {
            "description": "The explicit Memory mutation completed.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

SEARCH_MEMORY = Operation[SearchMemoryRequest, SearchMemoryResponse](
    method="POST",
    path="/v1/memory/search",
    operation_id="search_memory",
    request_type=SearchMemoryRequest,
    request_location="body",
    response_type=SearchMemoryResponse,
    success_status=200,
    summary="Search active Memory entries",
    tags=("memory",),
    responses={
        200: {
            "description": "Matching Memory entries, or an empty result when the scope has no Memory.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_MEMORY_ENTRIES = Operation[ListMemoryEntriesRequest, ListMemoryEntriesResponse](
    method="POST",
    path="/v1/memory/entries/list",
    operation_id="list_memory_entries",
    request_type=ListMemoryEntriesRequest,
    request_location="body",
    response_type=ListMemoryEntriesResponse,
    success_status=200,
    summary="List Memory entries",
    tags=("memory",),
    responses={
        200: {
            "description": "The selected entries from the current Memory head.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_MEMORY_ENTRY = Operation[GetMemoryEntryRequest, MemoryEntry](
    method="POST",
    path="/v1/memory/entries/get",
    operation_id="get_memory_entry",
    request_type=GetMemoryEntryRequest,
    request_location="body",
    response_type=MemoryEntry,
    success_status=200,
    summary="Get an exact Memory entry version",
    tags=("memory",),
    responses={
        200: {
            "description": "The exact Memory entry version.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REVISE_MEMORY_ENTRY = Operation[ReviseMemoryEntryRequest, MemoryMutationResponse](
    method="POST",
    path="/v1/memory/entries/revise",
    operation_id="revise_memory_entry",
    request_type=ReviseMemoryEntryRequest,
    request_location="body",
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Revise an exact Memory entry",
    tags=("memory",),
    responses={
        200: {
            "description": "The Memory entry revision completed.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RETIRE_MEMORY_ENTRY = Operation[RetireMemoryEntryRequest, MemoryMutationResponse](
    method="POST",
    path="/v1/memory/entries/retire",
    operation_id="retire_memory_entry",
    request_type=RetireMemoryEntryRequest,
    request_location="body",
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Retire an exact Memory entry",
    tags=("memory",),
    responses={
        200: {
            "description": "The Memory entry retirement completed.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_MEMORY_CHANGES = Operation[ListMemoryChangesRequest, ListMemoryChangesResponse](
    method="POST",
    path="/v1/memory/changes",
    operation_id="list_memory_changes",
    request_type=ListMemoryChangesRequest,
    request_location="body",
    response_type=ListMemoryChangesResponse,
    success_status=200,
    summary="List Memory Revision changes",
    tags=("memory",),
    responses={
        200: {
            "description": "Compact changes through the selected Memory Revision.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

PROPOSE_EXPERIENCE = Operation[ProposeExperienceRequest, ArtifactCandidate](
    method="POST",
    path="/v1/experience/propose",
    operation_id="propose_experience",
    request_type=ProposeExperienceRequest,
    request_location="body",
    response_type=ArtifactCandidate,
    success_status=201,
    summary="Propose Experience content",
    tags=("experience",),
    responses={
        201: {
            "description": "The pending Experience Candidate.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GENERATE_EXPERIENCE = Operation[GenerateExperienceRequest, GeneratedCandidateResponse](
    method="POST",
    path="/v1/experience/generate",
    operation_id="generate_experience",
    request_type=GenerateExperienceRequest,
    request_location="body",
    response_type=GeneratedCandidateResponse,
    success_status=200,
    summary="Generate an Experience Candidate",
    tags=("experience",),
    responses={
        200: {
            "description": "A pending Candidate or an explicit semantic no-op.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_EXPERIENCE = Operation[GetExperienceRequest, ExperienceArtifact](
    method="POST",
    path="/v1/experience/get",
    operation_id="get_experience",
    request_type=GetExperienceRequest,
    request_location="body",
    response_type=ExperienceArtifact,
    success_status=200,
    summary="Get an exact Experience Revision",
    tags=("experience",),
    responses={
        200: {
            "description": "The exact approved Experience Revision.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

PROPOSE_SKILL = Operation[ProposeSkillRequest, ArtifactCandidate](
    method="POST",
    path="/v1/skill/propose",
    operation_id="propose_skill",
    request_type=ProposeSkillRequest,
    request_location="body",
    response_type=ArtifactCandidate,
    success_status=201,
    summary="Propose managed Skill content",
    tags=("skill",),
    responses={
        201: {
            "description": "The pending managed Skill Candidate.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GENERATE_SKILL = Operation[GenerateSkillRequest, GeneratedCandidateResponse](
    method="POST",
    path="/v1/skill/generate",
    operation_id="generate_skill",
    request_type=GenerateSkillRequest,
    request_location="body",
    response_type=GeneratedCandidateResponse,
    success_status=200,
    summary="Generate a managed Skill Candidate",
    tags=("skill",),
    responses={
        200: {
            "description": "A pending Candidate or an explicit semantic no-op.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_SKILL = Operation[GetSkillRequest, SkillArtifact](
    method="POST",
    path="/v1/skill/get",
    operation_id="get_skill",
    request_type=GetSkillRequest,
    request_location="body",
    response_type=SkillArtifact,
    success_status=200,
    summary="Get an exact managed Skill Revision",
    tags=("skill",),
    responses={
        200: {
            "description": "The exact approved managed Skill Revision.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

SCAN_EXTERNAL_SKILLS = Operation[ScanExternalSkillsRequest, ScanExternalSkillsResponse](
    method="POST",
    path="/v1/external-skills/scan",
    operation_id="scan_external_skills",
    request_type=ScanExternalSkillsRequest,
    request_location="body",
    response_type=ScanExternalSkillsResponse,
    success_status=200,
    summary="Scan configured external Skill roots",
    tags=("skill",),
    responses={
        200: {
            "description": "The rebuildable provider snapshot.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_EXTERNAL_SKILLS = Operation[ListExternalSkillsRequest, ListExternalSkillsResponse](
    method="POST",
    path="/v1/external-skills/list",
    operation_id="list_external_skills",
    request_type=ListExternalSkillsRequest,
    request_location="body",
    response_type=ListExternalSkillsResponse,
    success_status=200,
    summary="List external Skills visible on this host",
    tags=("skill",),
    responses={
        200: {
            "description": "External Skills resolved against the current Agent, host, scope, and fingerprint.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RESOLVE_EXTERNAL_SKILL = Operation[ResolveExternalSkillRequest, ExternalSkillResolution](
    method="POST",
    path="/v1/external-skills/resolve",
    operation_id="resolve_external_skill",
    request_type=ResolveExternalSkillRequest,
    request_location="body",
    response_type=ExternalSkillResolution,
    success_status=200,
    summary="Resolve an exact external Skill fingerprint",
    tags=("skill",),
    responses={
        200: {
            "description": "The live exact-resolution result, which may be unavailable.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

IMPORT_EXTERNAL_SKILL = Operation[ImportExternalSkillRequest, GeneratedCandidateResponse](
    method="POST",
    path="/v1/external-skills/import",
    operation_id="import_external_skill",
    request_type=ImportExternalSkillRequest,
    request_location="body",
    response_type=GeneratedCandidateResponse,
    success_status=200,
    summary="Import or fork an external Skill into Review",
    tags=("skill",),
    responses={
        200: {
            "description": "A pending managed Skill Candidate or an explicit semantic no-op.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_ARTIFACT_CANDIDATES = Operation[ListArtifactCandidatesRequest, ArtifactCandidatePage](
    method="POST",
    path="/v1/artifact-candidates/list",
    operation_id="list_artifact_candidates",
    request_type=ListArtifactCandidatesRequest,
    request_location="body",
    response_type=ArtifactCandidatePage,
    success_status=200,
    summary="List Artifact Candidates",
    tags=("review",),
    responses={
        200: {
            "description": "The selected current Candidate heads.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_ARTIFACT_CANDIDATE = Operation[GetArtifactCandidateRequest, ArtifactCandidate](
    method="POST",
    path="/v1/artifact-candidates/get",
    operation_id="get_artifact_candidate",
    request_type=GetArtifactCandidateRequest,
    request_location="body",
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Get an Artifact Candidate",
    tags=("review",),
    responses={
        200: {
            "description": "The current Candidate head.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

APPROVE_ARTIFACT_CANDIDATE = Operation[ApproveArtifactCandidateRequest, ArtifactCandidate](
    method="POST",
    path="/v1/artifact-candidates/approve",
    operation_id="approve_artifact_candidate",
    request_type=ApproveArtifactCandidateRequest,
    request_location="body",
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Approve an Artifact Candidate",
    tags=("review",),
    responses={
        200: {
            "description": "The approved Candidate and exact result Artifact.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REJECT_ARTIFACT_CANDIDATE = Operation[RejectArtifactCandidateRequest, ArtifactCandidate](
    method="POST",
    path="/v1/artifact-candidates/reject",
    operation_id="reject_artifact_candidate",
    request_type=RejectArtifactCandidateRequest,
    request_location="body",
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Reject an Artifact Candidate",
    tags=("review",),
    responses={
        200: {
            "description": "The rejected Candidate.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REVISE_ARTIFACT_CANDIDATE = Operation[ReviseArtifactCandidateRequest, ArtifactCandidate](
    method="POST",
    path="/v1/artifact-candidates/revise",
    operation_id="revise_artifact_candidate",
    request_type=ReviseArtifactCandidateRequest,
    request_location="body",
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Revise an Artifact Candidate",
    tags=("review",),
    responses={
        200: {
            "description": "The next pending Candidate version.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_STATS = Operation[GetStatsRequest, ScopedStats](
    method="GET",
    path="/v1/stats",
    operation_id="get_stats",
    request_type=GetStatsRequest,
    request_location="query",
    response_type=ScopedStats,
    success_status=200,
    summary="Get scoped product statistics",
    tags=("stats",),
    responses={
        200: {
            "description": "Current inventory, model usage, and recall token estimates for the scope.",
            "headers": {
                "X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"},
                "Cache-Control": {
                    "description": "Prevent caches from retaining scoped statistics.",
                    "schema": {"type": "string", "enum": ["no-store"]},
                },
            },
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

CREATE_HANDOFF_REPORT_PROJECT = Operation[CreateHandoffReportProjectRequest, ProjectDescriptor](
    method="POST",
    path="/v1/handoff-reports/projects/create",
    operation_id="create_handoff_report_project",
    request_type=CreateHandoffReportProjectRequest,
    request_location="body",
    response_type=ProjectDescriptor,
    success_status=201,
    summary="Create a Handoff Report Project",
    tags=("handoff-reports",),
    responses={
        201: {
            "description": "The created Report Project.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_HANDOFF_REPORT_PROJECTS = Operation[ListHandoffReportProjectsRequest, ProjectPage](
    method="POST",
    path="/v1/handoff-reports/projects/list",
    operation_id="list_handoff_report_projects",
    request_type=ListHandoffReportProjectsRequest,
    request_location="body",
    response_type=ProjectPage,
    success_status=200,
    summary="List Handoff Report Projects",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "A cursor-paginated page of Report Projects.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_HANDOFF_REPORT_PROJECT = Operation[GetHandoffReportProjectRequest, ProjectDescriptor](
    method="POST",
    path="/v1/handoff-reports/projects/get",
    operation_id="get_handoff_report_project",
    request_type=GetHandoffReportProjectRequest,
    request_location="body",
    response_type=ProjectDescriptor,
    success_status=200,
    summary="Get a Handoff Report Project",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "The exact current Report Project descriptor.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

UPDATE_HANDOFF_REPORT_PROJECT = Operation[UpdateHandoffReportProjectRequest, ProjectDescriptor](
    method="POST",
    path="/v1/handoff-reports/projects/update",
    operation_id="update_handoff_report_project",
    request_type=UpdateHandoffReportProjectRequest,
    request_location="body",
    response_type=ProjectDescriptor,
    success_status=200,
    summary="Update a Handoff Report Project",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "The updated Report Project descriptor.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REGISTER_HANDOFF_REPORT_WORKSTREAM = Operation[RegisterHandoffReportWorkstreamRequest, WorkstreamDescriptor](
    method="POST",
    path="/v1/handoff-reports/workstreams/register",
    operation_id="register_handoff_report_workstream",
    request_type=RegisterHandoffReportWorkstreamRequest,
    request_location="body",
    response_type=WorkstreamDescriptor,
    success_status=201,
    summary="Register a Handoff Report Workstream",
    tags=("handoff-reports",),
    responses={
        201: {
            "description": "The registered Report Workstream.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_HANDOFF_REPORT_WORKSTREAMS = Operation[ListHandoffReportWorkstreamsRequest, WorkstreamPage](
    method="POST",
    path="/v1/handoff-reports/workstreams/list",
    operation_id="list_handoff_report_workstreams",
    request_type=ListHandoffReportWorkstreamsRequest,
    request_location="body",
    response_type=WorkstreamPage,
    success_status=200,
    summary="List Handoff Report Workstreams",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "A cursor-paginated page of Report Workstreams.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

UPDATE_HANDOFF_REPORT_WORKSTREAM = Operation[UpdateHandoffReportWorkstreamRequest, WorkstreamDescriptor](
    method="POST",
    path="/v1/handoff-reports/workstreams/update",
    operation_id="update_handoff_report_workstream",
    request_type=UpdateHandoffReportWorkstreamRequest,
    request_location="body",
    response_type=WorkstreamDescriptor,
    success_status=200,
    summary="Update a Handoff Report Workstream",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "The updated Report Workstream descriptor.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_HANDOFF_REPORT = Operation[GetHandoffReportRequest, HandoffReportResponse](
    method="POST",
    path="/v1/handoff-reports/get",
    operation_id="get_handoff_report",
    request_type=GetHandoffReportRequest,
    request_location="body",
    response_type=HandoffReportResponse,
    success_status=200,
    summary="Generate a Handoff Report",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "A canonical JSON report, optionally accompanied by Markdown.",
            "headers": {
                "X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"},
                "Cache-Control": {
                    "description": "Prevent caches from retaining scoped report data.",
                    "schema": {"type": "string", "enum": ["no-store"]},
                },
                "X-PowerContext-Selection-Digest": {
                    "description": "Digest of the exact report selection.",
                    "schema": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                },
                "X-PowerContext-Report-Digest": {
                    "description": "Digest of the selected output projection.",
                    "schema": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                },
                "Content-Disposition": {
                    "description": "Safe attachment filename when download is true.",
                    "schema": {"type": "string"},
                },
            },
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        413: {"$ref": "#/components/responses/ReportTooLarge"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RECORD_HANDOFF_REPORT_ACTIVITY = Operation[RecordHandoffReportActivityRequest, StoredHandoffReportActivity](
    method="POST",
    path="/v1/handoff-reports/activities/record",
    operation_id="record_handoff_report_activity",
    request_type=RecordHandoffReportActivityRequest,
    request_location="body",
    response_type=StoredHandoffReportActivity,
    success_status=201,
    summary="Record a Handoff Report Activity",
    tags=("handoff-reports",),
    responses={
        201: {
            "description": "The idempotently recorded Report Activity.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_HANDOFF_REPORT_ACTIVITIES = Operation[ListHandoffReportActivitiesRequest, HandoffReportActivityPage](
    method="POST",
    path="/v1/handoff-reports/activities/list",
    operation_id="list_handoff_report_activities",
    request_type=ListHandoffReportActivitiesRequest,
    request_location="body",
    response_type=HandoffReportActivityPage,
    success_status=200,
    summary="List Handoff Report Activities",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "A frozen cursor page of Report Activities.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

PURGE_HANDOFF_REPORT_ACTIVITIES = Operation[PurgeHandoffReportActivitiesRequest, PurgeHandoffReportActivitiesResponse](
    method="POST",
    path="/v1/handoff-reports/activities/purge",
    operation_id="purge_handoff_report_activities",
    request_type=PurgeHandoffReportActivitiesRequest,
    request_location="body",
    response_type=PurgeHandoffReportActivitiesResponse,
    success_status=200,
    summary="Purge Handoff Report Activities",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "The number of deleted Report-owned Activity rows.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_HANDOFF_REPORT_WORKSPACE = Operation[GetHandoffReportWorkspaceRequest, HandoffReportWorkspaceBinding](
    method="POST",
    path="/v1/handoff-reports/workspace-bindings/get",
    operation_id="get_handoff_report_workspace",
    request_type=GetHandoffReportWorkspaceRequest,
    request_location="body",
    response_type=HandoffReportWorkspaceBinding,
    success_status=200,
    summary="Get a Handoff Report Workspace Binding",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "The confirmed Workspace binding.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

ATTACH_HANDOFF_REPORT_WORKSPACE = Operation[AttachHandoffReportWorkspaceRequest, HandoffReportWorkspaceBinding](
    method="POST",
    path="/v1/handoff-reports/workspace-bindings/attach",
    operation_id="attach_handoff_report_workspace",
    request_type=AttachHandoffReportWorkspaceRequest,
    request_location="body",
    response_type=HandoffReportWorkspaceBinding,
    success_status=200,
    summary="Attach a Handoff Report Workspace Binding",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "The confirmed Workspace binding.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

DETACH_HANDOFF_REPORT_WORKSPACE = Operation[DetachHandoffReportWorkspaceRequest, HandoffReportWorkspaceBinding](
    method="POST",
    path="/v1/handoff-reports/workspace-bindings/detach",
    operation_id="detach_handoff_report_workspace",
    request_type=DetachHandoffReportWorkspaceRequest,
    request_location="body",
    response_type=HandoffReportWorkspaceBinding,
    success_status=200,
    summary="Detach a Handoff Report Workspace Binding",
    tags=("handoff-reports",),
    responses={
        200: {
            "description": "The detached Workspace binding record.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)
