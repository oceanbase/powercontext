# generated from openapi/powercontext.yaml; do not edit.

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, JsonValue

from powercontext.http._generated.models import (
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    ArtifactPublication,
    Capabilities,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    ClearScopeBindingRequest,
    ClearScopeBindingResponse,
    CommitConnectorCheckpointRequest,
    CommitHandoffRequest,
    CommittedHandoff,
    ConnectorCheckpointState,
    ContinueHandoffRequest,
    CreateRemoteSkillTargetRequest,
    CreateScopeRequest,
    CreateWorkContractRequest,
    DownloadRemoteSkillPackageRequest,
    EnrollRemoteSkillTargetRequest,
    ExperienceArtifact,
    ExternalSkillResolution,
    FinalizeHandoffRequest,
    FlushMemoryRequest,
    FlushMemoryResponse,
    GeneratedCandidateResponse,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetArtifactCandidateRequest,
    GetConnectorCheckpointRequest,
    GetExperienceRequest,
    GetHandoffReportRequest,
    GetMemoryEntryRequest,
    GetSkillPackageRequest,
    GetSkillRequest,
    GetStatsRequest,
    HandoffAcknowledgement,
    HandoffActivation,
    HandoffCurrentWorkRequest,
    HandoffDraft,
    HandoffReportResponse,
    HandoffResolution,
    HealthResponse,
    ImportExternalSkillRequest,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ListManagedSkillsRequest,
    ListManagedSkillsResponse,
    ListMemoryChangesRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesRequest,
    ListMemoryEntriesResponse,
    ListRemoteSkillTargetsRequest,
    ListRemoteSkillTargetsResponse,
    MemoryEntry,
    MemoryMutationResponse,
    PrepareContextRequest,
    PreparedContext,
    PreparedHandoff,
    PreparedWorkHandoff,
    PrepareHandoffRequest,
    ProposeExperienceRequest,
    ProposeSkillPackageRequest,
    ProposeSkillRequest,
    PublishArtifactRequest,
    PublishRemoteSkillRequest,
    ReadinessResponse,
    ReconcileRemoteSkillsRequest,
    ReconcileRemoteSkillsResponse,
    RecordRemoteSkillReceiptRequest,
    RecordSkillUsageRequest,
    RecordTaskOutcomeRequest,
    RegisterSourceDefinitionRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    RemoteSkillPublication,
    RemoteSkillReceiptResponse,
    RemoteSkillTarget,
    RemoteSkillTargetCredential,
    RemoteSkillTargetEnrollment,
    RenameRemoteSkillTargetRequest,
    ResolveExternalSkillRequest,
    ResolveScopeBindingRequest,
    ResolveScopeSelectionRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
    RevokeRemoteSkillTargetRequest,
    ScanExternalSkillsRequest,
    ScanExternalSkillsResponse,
    ScopeBinding,
    ScopeDescriptor,
    ScopedStats,
    ScopePage,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SetDefaultScopeRequest,
    SetScopeBindingRequest,
    SkillArtifact,
    SkillGovernance,
    SkillPackageDownload,
    SkillPackageManifest,
    SourceDefinitionManifest,
    SourceObservationReceipt,
    SubmitSourceObservationRequest,
    UnpublishRemoteSkillRequest,
    UpdateScopeRequest,
    UpdateSkillLifecycleRequest,
    WorkSourceReceipt,
)

OPENAPI_VERSION = "3.0.3"
API_TITLE = "PowerContext API"
API_DESCRIPTION = "Remote PowerContext transport. Runtime behavior is reported by /v1/capabilities."
API_VERSION = "0.1.0"

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class Operation(BaseModel, Generic[RequestT, ResponseT]):
    method: str
    path: str
    operation_id: str
    request_type: type[RequestT] | None
    request_location: Literal["body", "query"] | None
    path_parameters: tuple[str, ...]
    response_type: type[ResponseT]
    success_status: int
    summary: str
    tags: tuple[str, ...]
    scope_mode: Literal["none", "current", "selection"]
    responses: dict[int | str, dict[str, JsonValue]]


GET_LIVENESS = Operation[None, HealthResponse](
    method="GET",
    path="/health/live",
    operation_id="get_liveness",
    request_type=None,
    request_location=None,
    path_parameters=(),
    response_type=HealthResponse,
    success_status=200,
    summary="Get process liveness",
    tags=("health",),
    scope_mode="none",
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
    path_parameters=(),
    response_type=ReadinessResponse,
    success_status=200,
    summary="Get deployment readiness",
    tags=("health",),
    scope_mode="none",
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
    path_parameters=(),
    response_type=Capabilities,
    success_status=200,
    summary="Get runtime capabilities",
    tags=("capabilities",),
    scope_mode="none",
    responses={
        200: {
            "description": "Behavior enabled by the assembled runtime.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
    },
)

LIST_SCOPES = Operation[None, ScopePage](
    method="GET",
    path="/v1/scopes",
    operation_id="list_scopes",
    request_type=None,
    request_location=None,
    path_parameters=(),
    response_type=ScopePage,
    success_status=200,
    summary="List observable Scopes",
    tags=("scopes",),
    scope_mode="none",
    responses={
        200: {"description": "Durable Scope metadata in deterministic identity order."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        503: {"$ref": "#/components/responses/Unavailable"},
    },
)

CREATE_SCOPE = Operation[CreateScopeRequest, ScopeDescriptor](
    method="POST",
    path="/v1/scopes",
    operation_id="create_scope",
    request_type=CreateScopeRequest,
    request_location="body",
    path_parameters=(),
    response_type=ScopeDescriptor,
    success_status=201,
    summary="Create an independent Scope boundary",
    tags=("scopes",),
    scope_mode="none",
    responses={
        201: {"description": "The durable Scope descriptor."},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

PUBLISH_ARTIFACT = Operation[PublishArtifactRequest, ArtifactPublication](
    method="POST",
    path="/v1/artifact-publications",
    operation_id="publish_artifact",
    request_type=PublishArtifactRequest,
    request_location="body",
    path_parameters=(),
    response_type=ArtifactPublication,
    success_status=201,
    summary="Publish one exact Artifact revision into another Scope",
    tags=("scopes",),
    scope_mode="none",
    responses={
        201: {"description": "Independent target Artifact and its exact source provenance."},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

GET_SCOPE = Operation[None, ScopeDescriptor](
    method="GET",
    path="/v1/scopes/{scope_id}",
    operation_id="get_scope",
    request_type=None,
    request_location=None,
    path_parameters=("scope_id",),
    response_type=ScopeDescriptor,
    success_status=200,
    summary="Get one Scope descriptor",
    tags=("scopes",),
    scope_mode="none",
    responses={
        200: {"description": "The exact Scope descriptor."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
    },
)

UPDATE_SCOPE = Operation[UpdateScopeRequest, ScopeDescriptor](
    method="PUT",
    path="/v1/scopes/{scope_id}",
    operation_id="update_scope",
    request_type=UpdateScopeRequest,
    request_location="body",
    path_parameters=("scope_id",),
    response_type=ScopeDescriptor,
    success_status=200,
    summary="Replace mutable Scope metadata and relationships",
    tags=("scopes",),
    scope_mode="none",
    responses={
        200: {"description": "The updated Scope descriptor."},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

GET_DEFAULT_SCOPE = Operation[None, ScopeDescriptor](
    method="GET",
    path="/v1/scopes/default",
    operation_id="get_default_scope",
    request_type=None,
    request_location=None,
    path_parameters=(),
    response_type=ScopeDescriptor,
    success_status=200,
    summary="Get the default Scope binding target",
    tags=("scopes",),
    scope_mode="none",
    responses={
        200: {"description": "The ordinary Scope selected by the host default pointer."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
    },
)

SET_DEFAULT_SCOPE = Operation[SetDefaultScopeRequest, ScopeDescriptor](
    method="PUT",
    path="/v1/scopes/default",
    operation_id="set_default_scope",
    request_type=SetDefaultScopeRequest,
    request_location="body",
    path_parameters=(),
    response_type=ScopeDescriptor,
    success_status=200,
    summary="Change the default Scope binding target",
    tags=("scopes",),
    scope_mode="none",
    responses={
        200: {"description": "The selected ordinary Scope."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
    },
)

RESOLVE_SCOPE_SELECTION = Operation[ResolveScopeSelectionRequest, ScopePage](
    method="POST",
    path="/v1/scopes/selection/resolve",
    operation_id="resolve_scope_selection",
    request_type=ResolveScopeSelectionRequest,
    request_location="body",
    path_parameters=(),
    response_type=ScopePage,
    success_status=200,
    summary="Resolve an observation selection to a frozen Scope set",
    tags=("scopes",),
    scope_mode="none",
    responses={
        200: {"description": "The selected Scope descriptors in deterministic order."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

RESOLVE_SCOPE_BINDING = Operation[ResolveScopeBindingRequest, ScopeDescriptor](
    method="POST",
    path="/v1/scope-bindings/resolve",
    operation_id="resolve_scope_binding",
    request_type=ResolveScopeBindingRequest,
    request_location="body",
    path_parameters=(),
    response_type=ScopeDescriptor,
    success_status=200,
    summary="Resolve an explicit durable or default Scope binding",
    tags=("scope-bindings",),
    scope_mode="none",
    responses={
        200: {"description": "The resolved Scope descriptor."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

SET_SCOPE_BINDING = Operation[SetScopeBindingRequest, ScopeBinding](
    method="PUT",
    path="/v1/scope-bindings",
    operation_id="set_scope_binding",
    request_type=SetScopeBindingRequest,
    request_location="body",
    path_parameters=(),
    response_type=ScopeBinding,
    success_status=200,
    summary="Persist an external identity to Scope binding",
    tags=("scope-bindings",),
    scope_mode="none",
    responses={
        200: {"description": "The durable external binding."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

CLEAR_SCOPE_BINDING = Operation[ClearScopeBindingRequest, ClearScopeBindingResponse](
    method="POST",
    path="/v1/scope-bindings/clear",
    operation_id="clear_scope_binding",
    request_type=ClearScopeBindingRequest,
    request_location="body",
    path_parameters=(),
    response_type=ClearScopeBindingResponse,
    success_status=200,
    summary="Remove one durable external Scope binding",
    tags=("scope-bindings",),
    scope_mode="none",
    responses={
        200: {"description": "Whether a durable binding was removed."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

CAPTURE_CONTENT_SOURCE = Operation[CaptureContentSourceRequest, CaptureContentSourceResponse](
    method="POST",
    path="/v1/sources/content",
    operation_id="capture_content_source",
    request_type=CaptureContentSourceRequest,
    request_location="body",
    path_parameters=(),
    response_type=CaptureContentSourceResponse,
    success_status=202,
    summary="Capture durable ContentSource evidence",
    tags=("sources",),
    scope_mode="current",
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

REGISTER_SOURCE_DEFINITION = Operation[RegisterSourceDefinitionRequest, SourceDefinitionManifest](
    method="POST",
    path="/v1/source-definitions/register",
    operation_id="register_source_definition",
    request_type=RegisterSourceDefinitionRequest,
    request_location="body",
    path_parameters=(),
    response_type=SourceDefinitionManifest,
    success_status=200,
    summary="Register a worker-owned Source Definition manifest",
    tags=("source-ingestion",),
    scope_mode="none",
    responses={
        200: {"description": "The exact manifest is registered or was already registered identically."},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

GET_CONNECTOR_CHECKPOINT = Operation[GetConnectorCheckpointRequest, ConnectorCheckpointState](
    method="POST",
    path="/v1/connector-checkpoints/get",
    operation_id="get_connector_checkpoint",
    request_type=GetConnectorCheckpointRequest,
    request_location="body",
    path_parameters=(),
    response_type=ConnectorCheckpointState,
    success_status=200,
    summary="Read a Connector binding checkpoint",
    tags=("source-ingestion",),
    scope_mode="none",
    responses={
        200: {"description": "The current opaque checkpoint, including a normal null initial value."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

SUBMIT_SOURCE_OBSERVATION = Operation[SubmitSourceObservationRequest, SourceObservationReceipt](
    method="POST",
    path="/v1/source-observations",
    operation_id="submit_source_observation",
    request_type=SubmitSourceObservationRequest,
    request_location="body",
    path_parameters=(),
    response_type=SourceObservationReceipt,
    success_status=202,
    summary="Submit a worker-materialized Source observation",
    tags=("source-ingestion",),
    scope_mode="none",
    responses={
        202: {"description": "The observation is durably accepted and can be referenced exactly."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        409: {"$ref": "#/components/responses/Conflict"},
        404: {"$ref": "#/components/responses/NotFound"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

COMMIT_CONNECTOR_CHECKPOINT = Operation[CommitConnectorCheckpointRequest, ConnectorCheckpointState](
    method="POST",
    path="/v1/connector-checkpoints/commit",
    operation_id="commit_connector_checkpoint",
    request_type=CommitConnectorCheckpointRequest,
    request_location="body",
    path_parameters=(),
    response_type=ConnectorCheckpointState,
    success_status=200,
    summary="Commit a Connector binding checkpoint",
    tags=("source-ingestion",),
    scope_mode="none",
    responses={
        200: {"description": "The new opaque checkpoint is durable."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
    },
)

PREPARE_CONTEXT = Operation[PrepareContextRequest, PreparedContext](
    method="POST",
    path="/v1/context/prepare",
    operation_id="prepare_context",
    request_type=PrepareContextRequest,
    request_location="body",
    path_parameters=(),
    response_type=PreparedContext,
    success_status=200,
    summary="Prepare bounded context for an Agent turn",
    tags=("context",),
    scope_mode="current",
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

CREATE_WORK_CONTRACT = Operation[CreateWorkContractRequest, WorkSourceReceipt](
    method="POST",
    path="/v1/work/contracts/create",
    operation_id="create_work_contract",
    request_type=CreateWorkContractRequest,
    request_location="body",
    path_parameters=(),
    response_type=WorkSourceReceipt,
    success_status=202,
    summary="Create a grounded Work Contract",
    tags=("work",),
    scope_mode="current",
    responses={
        202: {
            "description": "The Work Contract is durably captured as exact Source evidence.",
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

HANDOFF_CURRENT_WORK = Operation[HandoffCurrentWorkRequest, PreparedWorkHandoff](
    method="POST",
    path="/v1/work/handoffs/prepare-current",
    operation_id="handoff_current_work",
    request_type=HandoffCurrentWorkRequest,
    request_location="body",
    path_parameters=(),
    response_type=PreparedWorkHandoff,
    success_status=200,
    summary="Hand off current work in one high-level operation",
    tags=("work",),
    scope_mode="current",
    responses={
        200: {
            "description": "The captured boundary and Prepared Handoff ready for explicit transfer.",
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

ACKNOWLEDGE_HANDOFF = Operation[AcknowledgeHandoffRequest, HandoffAcknowledgement](
    method="POST",
    path="/v1/work/handoffs/acknowledge",
    operation_id="acknowledge_handoff",
    request_type=AcknowledgeHandoffRequest,
    request_location="body",
    path_parameters=(),
    response_type=HandoffAcknowledgement,
    success_status=200,
    summary="Resolve and acknowledge a Handoff",
    tags=("work",),
    scope_mode="current",
    responses={
        200: {
            "description": "The resolved Handoff and durable receiver acknowledgement.",
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

RECORD_TASK_OUTCOME = Operation[RecordTaskOutcomeRequest, WorkSourceReceipt](
    method="POST",
    path="/v1/work/outcomes/record",
    operation_id="record_task_outcome",
    request_type=RecordTaskOutcomeRequest,
    request_location="body",
    path_parameters=(),
    response_type=WorkSourceReceipt,
    success_status=202,
    summary="Record a completion-aware Task Outcome",
    tags=("work",),
    scope_mode="current",
    responses={
        202: {
            "description": "The Task Outcome is durably captured for Handoff evidence and reviewed "
            "Experience incubation.",
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

ACTIVATE_HANDOFF = Operation[ActivateHandoffRequest, HandoffActivation](
    method="POST",
    path="/v1/handoff/activate",
    operation_id="activate_handoff",
    request_type=ActivateHandoffRequest,
    request_location="body",
    path_parameters=(),
    response_type=HandoffActivation,
    success_status=200,
    summary="Activate Handoff generation at a Source boundary",
    tags=("handoff",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=HandoffDraft,
    success_status=200,
    summary="Generate an inspectable Handoff Draft",
    tags=("handoff",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=PreparedHandoff,
    success_status=200,
    summary="Finalize an inspected Handoff Draft",
    tags=("handoff",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=CommittedHandoff,
    success_status=200,
    summary="Commit an explicit Handoff milestone",
    tags=("handoff",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=HandoffResolution,
    success_status=200,
    summary="Resolve a Handoff as untrusted historical input",
    tags=("handoff",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=FlushMemoryResponse,
    success_status=200,
    summary="Process the pending Source window into Memory",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Remember explicit Memory content",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=SearchMemoryResponse,
    success_status=200,
    summary="Search active Memory entries",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ListMemoryEntriesResponse,
    success_status=200,
    summary="List Memory entries",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=MemoryEntry,
    success_status=200,
    summary="Get an exact Memory entry version",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Revise an exact Memory entry",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Retire an exact Memory entry",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ListMemoryChangesResponse,
    success_status=200,
    summary="List Memory Revision changes",
    tags=("memory",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ArtifactCandidate,
    success_status=201,
    summary="Propose Experience content",
    tags=("experience",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=GeneratedCandidateResponse,
    success_status=200,
    summary="Generate an Experience Candidate",
    tags=("experience",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ExperienceArtifact,
    success_status=200,
    summary="Get an exact Experience Revision",
    tags=("experience",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ArtifactCandidate,
    success_status=201,
    summary="Propose managed Skill content",
    tags=("skill",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=GeneratedCandidateResponse,
    success_status=200,
    summary="Generate a managed Skill Candidate",
    tags=("skill",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=SkillArtifact,
    success_status=200,
    summary="Get an exact managed Skill Revision",
    tags=("skill",),
    scope_mode="current",
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

LIST_MANAGED_SKILLS = Operation[ListManagedSkillsRequest, ListManagedSkillsResponse](
    method="POST",
    path="/v1/skill/library",
    operation_id="list_managed_skills",
    request_type=ListManagedSkillsRequest,
    request_location="body",
    path_parameters=(),
    response_type=ListManagedSkillsResponse,
    success_status=200,
    summary="List or search current managed Skills",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {
            "description": "Current managed Skill Library rows.",
            "headers": {"X-PowerContext-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

UPDATE_SKILL_LIFECYCLE = Operation[UpdateSkillLifecycleRequest, SkillGovernance](
    method="POST",
    path="/v1/skill/lifecycle",
    operation_id="update_skill_lifecycle",
    request_type=UpdateSkillLifecycleRequest,
    request_location="body",
    path_parameters=(),
    response_type=SkillGovernance,
    success_status=200,
    summary="Update managed Skill lifecycle",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {
            "description": "Updated managed Skill governance.",
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

GET_SKILL_PACKAGE_MANIFEST = Operation[GetSkillPackageRequest, SkillPackageManifest](
    method="POST",
    path="/v1/skill/package/manifest",
    operation_id="get_skill_package_manifest",
    request_type=GetSkillPackageRequest,
    request_location="body",
    path_parameters=(),
    response_type=SkillPackageManifest,
    success_status=200,
    summary="Get an exact managed Skill package manifest",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {"description": "Verified exact package manifest."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

DOWNLOAD_SKILL_PACKAGE = Operation[GetSkillPackageRequest, SkillPackageDownload](
    method="POST",
    path="/v1/skill/package/download",
    operation_id="download_skill_package",
    request_type=GetSkillPackageRequest,
    request_location="body",
    path_parameters=(),
    response_type=SkillPackageDownload,
    success_status=200,
    summary="Download an exact managed Skill package",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {"description": "Canonical exact package archive."},
        404: {"$ref": "#/components/responses/NotFound"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

PROPOSE_SKILL_PACKAGE = Operation[ProposeSkillPackageRequest, ArtifactCandidate](
    method="POST",
    path="/v1/skill/package/propose",
    operation_id="propose_skill_package",
    request_type=ProposeSkillPackageRequest,
    request_location="body",
    path_parameters=(),
    response_type=ArtifactCandidate,
    success_status=201,
    summary="Propose an uploaded standard Skill package",
    tags=("skill",),
    scope_mode="current",
    responses={
        201: {"description": "Pending exact package Candidate."},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RECORD_SKILL_USAGE = Operation[RecordSkillUsageRequest, CaptureContentSourceResponse](
    method="POST",
    path="/v1/skill/usage",
    operation_id="record_skill_usage",
    request_type=RecordSkillUsageRequest,
    request_location="body",
    path_parameters=(),
    response_type=CaptureContentSourceResponse,
    success_status=201,
    summary="Record a bounded Skill usage observation",
    tags=("skill",),
    scope_mode="current",
    responses={
        201: {"description": "Accepted immutable usage Source evidence."},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_REMOTE_SKILL_TARGETS = Operation[ListRemoteSkillTargetsRequest, ListRemoteSkillTargetsResponse](
    method="POST",
    path="/v1/skill/remote/targets",
    operation_id="list_remote_skill_targets",
    request_type=ListRemoteSkillTargetsRequest,
    request_location="body",
    path_parameters=(),
    response_type=ListRemoteSkillTargetsResponse,
    success_status=200,
    summary="List remote Agent Skill target status",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {"description": "Remote target status rows visible to the administrative caller."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

CREATE_REMOTE_SKILL_TARGET = Operation[CreateRemoteSkillTargetRequest, RemoteSkillTargetEnrollment](
    method="POST",
    path="/v1/skill/remote/target/create",
    operation_id="create_remote_skill_target",
    request_type=CreateRemoteSkillTargetRequest,
    request_location="body",
    path_parameters=(),
    response_type=RemoteSkillTargetEnrollment,
    success_status=201,
    summary="Create a remote Agent Skill target enrollment",
    tags=("skill",),
    scope_mode="current",
    responses={
        201: {"description": "Pending remote target enrollment."},
        409: {"$ref": "#/components/responses/Conflict"},
        401: {"$ref": "#/components/responses/Unauthorized"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

ENROLL_REMOTE_SKILL_TARGET = Operation[EnrollRemoteSkillTargetRequest, RemoteSkillTargetCredential](
    method="POST",
    path="/v1/skill/remote/target/enroll",
    operation_id="enroll_remote_skill_target",
    request_type=EnrollRemoteSkillTargetRequest,
    request_location="body",
    path_parameters=(),
    response_type=RemoteSkillTargetCredential,
    success_status=200,
    summary="Enroll a remote Agent Skill Receiver",
    tags=("skill",),
    scope_mode="none",
    responses={
        200: {"description": "Activated remote target credential."},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RENAME_REMOTE_SKILL_TARGET = Operation[RenameRemoteSkillTargetRequest, RemoteSkillTarget](
    method="POST",
    path="/v1/skill/remote/target/rename",
    operation_id="rename_remote_skill_target",
    request_type=RenameRemoteSkillTargetRequest,
    request_location="body",
    path_parameters=(),
    response_type=RemoteSkillTarget,
    success_status=200,
    summary="Rename a remote Agent Skill target",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {"description": "Renamed remote target."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REVOKE_REMOTE_SKILL_TARGET = Operation[RevokeRemoteSkillTargetRequest, RemoteSkillTarget](
    method="POST",
    path="/v1/skill/remote/target/revoke",
    operation_id="revoke_remote_skill_target",
    request_type=RevokeRemoteSkillTargetRequest,
    request_location="body",
    path_parameters=(),
    response_type=RemoteSkillTarget,
    success_status=200,
    summary="Revoke a remote Agent Skill target",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {"description": "Revoked remote target."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

PUBLISH_REMOTE_SKILL = Operation[PublishRemoteSkillRequest, RemoteSkillPublication](
    method="POST",
    path="/v1/skill/remote/publication/publish",
    operation_id="publish_remote_skill",
    request_type=PublishRemoteSkillRequest,
    request_location="body",
    path_parameters=(),
    response_type=RemoteSkillPublication,
    success_status=200,
    summary="Set a remote target Skill desired Revision",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {"description": "Latest remote publication desired state."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

UNPUBLISH_REMOTE_SKILL = Operation[UnpublishRemoteSkillRequest, RemoteSkillPublication](
    method="POST",
    path="/v1/skill/remote/publication/unpublish",
    operation_id="unpublish_remote_skill",
    request_type=UnpublishRemoteSkillRequest,
    request_location="body",
    path_parameters=(),
    response_type=RemoteSkillPublication,
    success_status=200,
    summary="Set remote target Skill desired absence",
    tags=("skill",),
    scope_mode="current",
    responses={
        200: {"description": "Latest remote publication desired state."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RECONCILE_REMOTE_SKILLS = Operation[ReconcileRemoteSkillsRequest, ReconcileRemoteSkillsResponse](
    method="POST",
    path="/v1/skill/remote/reconcile",
    operation_id="reconcile_remote_skills",
    request_type=ReconcileRemoteSkillsRequest,
    request_location="body",
    path_parameters=(),
    response_type=ReconcileRemoteSkillsResponse,
    success_status=200,
    summary="Reconcile a remote Agent Skill target",
    tags=("skill",),
    scope_mode="none",
    responses={
        200: {"description": "Latest desired-state actions for this target only."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

DOWNLOAD_REMOTE_SKILL_PACKAGE = Operation[DownloadRemoteSkillPackageRequest, SkillPackageDownload](
    method="POST",
    path="/v1/skill/remote/package/download",
    operation_id="download_remote_skill_package",
    request_type=DownloadRemoteSkillPackageRequest,
    request_location="body",
    path_parameters=(),
    response_type=SkillPackageDownload,
    success_status=200,
    summary="Download the exact package desired by a remote target",
    tags=("skill",),
    scope_mode="none",
    responses={
        200: {"description": "Canonical exact package archive."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RECORD_REMOTE_SKILL_RECEIPT = Operation[RecordRemoteSkillReceiptRequest, RemoteSkillReceiptResponse](
    method="POST",
    path="/v1/skill/remote/receipt",
    operation_id="record_remote_skill_receipt",
    request_type=RecordRemoteSkillReceiptRequest,
    request_location="body",
    path_parameters=(),
    response_type=RemoteSkillReceiptResponse,
    success_status=200,
    summary="Record an exact remote Skill delivery Receipt",
    tags=("skill",),
    scope_mode="none",
    responses={
        200: {"description": "Receipt acceptance and latest publication observation."},
        401: {"$ref": "#/components/responses/Unauthorized"},
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

SCAN_EXTERNAL_SKILLS = Operation[ScanExternalSkillsRequest, ScanExternalSkillsResponse](
    method="POST",
    path="/v1/external-skills/scan",
    operation_id="scan_external_skills",
    request_type=ScanExternalSkillsRequest,
    request_location="body",
    path_parameters=(),
    response_type=ScanExternalSkillsResponse,
    success_status=200,
    summary="Scan configured external Skill roots",
    tags=("skill",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ListExternalSkillsResponse,
    success_status=200,
    summary="List external Skills visible on this host",
    tags=("skill",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ExternalSkillResolution,
    success_status=200,
    summary="Resolve an exact external Skill fingerprint",
    tags=("skill",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=GeneratedCandidateResponse,
    success_status=200,
    summary="Import or fork an external Skill into Review",
    tags=("skill",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ArtifactCandidatePage,
    success_status=200,
    summary="List Artifact Candidates",
    tags=("review",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Get an Artifact Candidate",
    tags=("review",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Approve an Artifact Candidate",
    tags=("review",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Reject an Artifact Candidate",
    tags=("review",),
    scope_mode="current",
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
    path_parameters=(),
    response_type=ArtifactCandidate,
    success_status=200,
    summary="Revise an Artifact Candidate",
    tags=("review",),
    scope_mode="current",
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
    method="POST",
    path="/v1/stats",
    operation_id="get_stats",
    request_type=GetStatsRequest,
    request_location="body",
    path_parameters=(),
    response_type=ScopedStats,
    success_status=200,
    summary="Aggregate product statistics over a Scope selection",
    tags=("stats",),
    scope_mode="selection",
    responses={
        200: {
            "description": "Current inventory, model usage, and recall token estimates for the frozen Scope set.",
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

GET_HANDOFF_REPORT = Operation[GetHandoffReportRequest, HandoffReportResponse](
    method="POST",
    path="/v1/handoff-reports/get",
    operation_id="get_handoff_report",
    request_type=GetHandoffReportRequest,
    request_location="body",
    path_parameters=(),
    response_type=HandoffReportResponse,
    success_status=200,
    summary="Generate a Handoff Report",
    tags=("handoff-reports",),
    scope_mode="selection",
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
