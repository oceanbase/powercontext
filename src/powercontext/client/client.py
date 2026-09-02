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

"""Small handwritten facade over the public HTTP contract."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import TracebackType
from typing import Self, TypeVar
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from powercontext.client.errors import InvalidResponseError, ServerResponseError, TransportError
from powercontext.client.tracing import ClientSpan
from powercontext.http import (
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
    CommitHandoffRequest,
    CommittedHandoff,
    ContinueHandoffRequest,
    CreateScopeRequest,
    CreateWorkContractRequest,
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
    GetHandoffReportRequest,
    GetMemoryEntryRequest,
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
    ListMemoryChangesRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesRequest,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryMutationResponse,
    PrepareContextRequest,
    PreparedContext,
    PreparedHandoff,
    PreparedWorkHandoff,
    PrepareHandoffRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    PublishArtifactRequest,
    ReadinessResponse,
    RecordTaskOutcomeRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    ResolveExternalSkillRequest,
    ResolveScopeBindingRequest,
    ResolveScopeSelectionRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
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
    UpdateScopeRequest,
    WorkSourceReceipt,
)
from powercontext.http._generated.operations import (
    ACKNOWLEDGE_HANDOFF,
    ACTIVATE_HANDOFF,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    CLEAR_SCOPE_BINDING,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    CREATE_SCOPE,
    CREATE_WORK_CONTRACT,
    FINALIZE_HANDOFF,
    FLUSH_MEMORY,
    GENERATE_EXPERIENCE,
    GENERATE_SKILL,
    GET_ARTIFACT_CANDIDATE,
    GET_CAPABILITIES,
    GET_DEFAULT_SCOPE,
    GET_EXPERIENCE,
    GET_HANDOFF_REPORT,
    GET_LIVENESS,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    GET_SCOPE,
    GET_SKILL,
    GET_STATS,
    HANDOFF_CURRENT_WORK,
    IMPORT_EXTERNAL_SKILL,
    LIST_ARTIFACT_CANDIDATES,
    LIST_EXTERNAL_SKILLS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    LIST_SCOPES,
    PREPARE_CONTEXT,
    PREPARE_HANDOFF,
    PROPOSE_EXPERIENCE,
    PROPOSE_SKILL,
    PUBLISH_ARTIFACT,
    RECORD_TASK_OUTCOME,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RESOLVE_EXTERNAL_SKILL,
    RESOLVE_SCOPE_BINDING,
    RESOLVE_SCOPE_SELECTION,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    SCAN_EXTERNAL_SKILLS,
    SEARCH_MEMORY,
    SET_DEFAULT_SCOPE,
    SET_SCOPE_BINDING,
    UPDATE_SCOPE,
    Operation,
)
from powercontext.transport import is_plaintext_non_loopback

REQUEST_ID_HEADER = "X-PowerContext-Request-ID"
_RequestT = TypeVar("_RequestT")
_ResponseT = TypeVar("_ResponseT")


class PowerContextClient:
    """Async Python facade for transport-level Server operations."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
        trust_transport_security: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # Plaintext HTTP is only trusted on loopback -- for *any* request, not just an authenticated
        # one. The request body itself carries Memory content, so a missing bearer token does not make
        # an unencrypted non-loopback request safe. When this facade opens the transport itself,
        # ``base_url``'s scheme accurately reflects what crosses the wire. A caller-supplied
        # ``http_client`` *may* instead own a transport whose ``http://`` label is only a routing
        # token -- an in-process ASGI app, a Unix socket, or a TLS-terminating proxy -- but a plain
        # pooling ``httpx.AsyncClient`` (e.g. the shared client the LangGraph adapter installs) is
        # exactly as exposed as one we would open ourselves. Supplying a transport is therefore not
        # evidence of safety: the guard stays on for caller-supplied transports too, and a caller that
        # knows its transport is secure must say so explicitly via ``trust_transport_security`` rather
        # than have safety inferred from the argument being set.
        transport_trusted = http_client is not None and trust_transport_security
        if not transport_trusted and is_plaintext_non_loopback(self._base_url):
            raise ValueError("refusing to send requests over unencrypted non-loopback HTTP")  # noqa: TRY003
        self._headers = {"Authorization": f"Bearer {token}"} if token else None
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

    async def list_scopes(self) -> ScopePage:
        """List durable Scope descriptors."""

        return await self._request(LIST_SCOPES)

    async def create_scope(self, request: CreateScopeRequest) -> ScopeDescriptor:
        """Create one independent Scope boundary."""

        return await self._request(CREATE_SCOPE, request)

    async def get_scope(self, scope_id: str) -> ScopeDescriptor:
        """Read one exact Scope descriptor."""

        return await self._request(GET_SCOPE, path_parameters={"scope_id": scope_id})

    async def update_scope(self, scope_id: str, request: UpdateScopeRequest) -> ScopeDescriptor:
        """Replace mutable Scope metadata and relationships."""

        return await self._request(UPDATE_SCOPE, request, path_parameters={"scope_id": scope_id})

    async def get_default_scope(self) -> ScopeDescriptor:
        """Read the host's default Scope target."""

        return await self._request(GET_DEFAULT_SCOPE)

    async def set_default_scope(self, request: SetDefaultScopeRequest) -> ScopeDescriptor:
        """Change the host's default Scope target."""

        return await self._request(SET_DEFAULT_SCOPE, request)

    async def resolve_scope_selection(self, request: ResolveScopeSelectionRequest) -> ScopePage:
        """Resolve all, exact, or subtree into exact Scope descriptors."""

        return await self._request(RESOLVE_SCOPE_SELECTION, request)

    async def resolve_scope_binding(self, request: ResolveScopeBindingRequest) -> ScopeDescriptor:
        """Resolve explicit and external host bindings to one Scope."""

        return await self._request(RESOLVE_SCOPE_BINDING, request)

    async def set_scope_binding(self, request: SetScopeBindingRequest) -> ScopeBinding:
        """Bind one external integration identity to a Scope."""

        return await self._request(SET_SCOPE_BINDING, request)

    async def clear_scope_binding(self, request: ClearScopeBindingRequest) -> ClearScopeBindingResponse:
        """Clear one external integration binding."""

        return await self._request(CLEAR_SCOPE_BINDING, request)

    async def publish_artifact(self, request: PublishArtifactRequest) -> ArtifactPublication:
        """Deliver one exact Artifact revision into another Scope."""

        return await self._request(PUBLISH_ARTIFACT, request)

    async def get_stats(self, request: GetStatsRequest) -> ScopedStats:
        """Read current inventory and bounded usage for one scope."""

        return await self._request(GET_STATS, request)

    async def get_handoff_report(self, request: GetHandoffReportRequest) -> HandoffReportResponse | str:
        """Generate the current canonical Handoff Report projection."""

        if request.download:
            raise ValueError("use download_handoff_report when download is true")  # noqa: TRY003
        if request.format.value == "markdown":
            return (await self._request_handoff_report_content(request)).decode("utf-8")
        return await self._request(GET_HANDOFF_REPORT, request)

    async def download_handoff_report(self, request: GetHandoffReportRequest) -> bytes:
        """Download a Markdown or canonical JSON report file."""

        prepared = request.model_copy(update={"download": True})
        return await self._request_handoff_report_content(prepared)

    async def _request_handoff_report_content(self, request: GetHandoffReportRequest) -> bytes:
        payload = TypeAdapter(GET_HANDOFF_REPORT.request_type).dump_python(
            request,
            mode="json",
            by_alias=True,
        )
        span = ClientSpan.start(GET_HANDOFF_REPORT.operation_id)
        try:
            headers = {} if self._headers is None else dict(self._headers)
            span.inject(headers)
            response = await self._http_client.request(
                GET_HANDOFF_REPORT.method,
                f"{self._base_url}{GET_HANDOFF_REPORT.path}",
                json=payload,
                headers=headers,
            )
        except asyncio.CancelledError as error:
            span.finish("cancelled", error=error)
            raise
        except httpx.HTTPError as exc:
            span.finish("failure", error=exc)
            raise TransportError(GET_HANDOFF_REPORT.path) from exc
        except BaseException as error:
            span.finish("failure", error=error)
            raise
        span.finish(
            "success" if response.status_code == GET_HANDOFF_REPORT.success_status else "failure",
            status_code=response.status_code,
        )
        if response.status_code != GET_HANDOFF_REPORT.success_status:
            error = _decode_error(response.content)
            raise ServerResponseError(
                status_code=response.status_code,
                request_id=response.headers.get(REQUEST_ID_HEADER),
                code=None if error is None else error.error.code,
                message=None if error is None else error.error.message,
                details=None if error is None else error.error.details,
            )
        return response.content

    async def capture_content_source(self, request: CaptureContentSourceRequest) -> CaptureContentSourceResponse:
        """Capture raw content as durable Source evidence."""

        return await self._request(CAPTURE_CONTENT_SOURCE, request)

    async def create_work_contract(self, request: CreateWorkContractRequest) -> WorkSourceReceipt:
        """Create one grounded delegation baseline as durable Source evidence."""

        return await self._request(CREATE_WORK_CONTRACT, request)

    async def handoff_current_work(self, request: HandoffCurrentWorkRequest) -> PreparedWorkHandoff:
        """Capture inspected current state and prepare a temporary Handoff."""

        return await self._request(HANDOFF_CURRENT_WORK, request)

    async def acknowledge_handoff(self, request: AcknowledgeHandoffRequest) -> HandoffAcknowledgement:
        """Resolve a Handoff and durably record the receiver's acknowledgement."""

        return await self._request(ACKNOWLEDGE_HANDOFF, request)

    async def record_task_outcome(self, request: RecordTaskOutcomeRequest) -> WorkSourceReceipt:
        """Record one completion-aware attempt outcome without erasing uncertainty."""

        return await self._request(RECORD_TASK_OUTCOME, request)

    async def flush_memory(self, request: FlushMemoryRequest) -> FlushMemoryResponse:
        """Run one bounded Source-to-Memory activation."""

        return await self._request(FLUSH_MEMORY, request)

    async def remember_memory(self, request: RememberMemoryRequest) -> MemoryMutationResponse:
        """Save one explicit Memory entry without creating a Source."""

        return await self._request(REMEMBER_MEMORY, request)

    async def search_memory(self, request: SearchMemoryRequest) -> SearchMemoryResponse:
        """Search active Memory entries in one scope."""

        return await self._request(SEARCH_MEMORY, request)

    async def prepare_context(self, request: PrepareContextRequest) -> PreparedContext:
        """Prepare final bounded context for one Agent turn."""

        return await self._request(PREPARE_CONTEXT, request)

    async def prepare_handoff(self, request: PrepareHandoffRequest) -> HandoffDraft:
        """Generate one inspectable Handoff Draft from exact evidence."""

        return await self._request(PREPARE_HANDOFF, request)

    async def activate_handoff(self, request: ActivateHandoffRequest) -> HandoffActivation:
        """Evaluate the standard Handoff Trigger at one Source boundary."""

        return await self._request(ACTIVATE_HANDOFF, request)

    async def finalize_handoff(self, request: FinalizeHandoffRequest) -> PreparedHandoff:
        """Finalize an inspected Handoff Draft for direct transfer."""

        return await self._request(FINALIZE_HANDOFF, request)

    async def commit_handoff(self, request: CommitHandoffRequest) -> CommittedHandoff:
        """Commit one finalized Handoff as a durable milestone."""

        return await self._request(COMMIT_HANDOFF, request)

    async def continue_handoff(self, request: ContinueHandoffRequest) -> HandoffResolution:
        """Resolve temporary or committed Handoff content as untrusted history."""

        return await self._request(CONTINUE_HANDOFF, request)

    async def list_memory_entries(self, request: ListMemoryEntriesRequest) -> ListMemoryEntriesResponse:
        """List active entries, optionally including inactive entries for audit."""

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

    async def propose_experience(self, request: ProposeExperienceRequest) -> ArtifactCandidate:
        """Submit complete Experience content as a pending Candidate."""

        return await self._request(PROPOSE_EXPERIENCE, request)

    async def generate_experience(self, request: GenerateExperienceRequest) -> GeneratedCandidateResponse:
        """Generate a reviewed Experience Candidate from exact evidence."""

        return await self._request(GENERATE_EXPERIENCE, request)

    async def get_experience(self, request: GetExperienceRequest) -> ExperienceArtifact:
        """Read one exact approved Experience Revision."""

        return await self._request(GET_EXPERIENCE, request)

    async def propose_skill(self, request: ProposeSkillRequest) -> ArtifactCandidate:
        """Submit complete managed Skill content as a pending Candidate."""

        return await self._request(PROPOSE_SKILL, request)

    async def generate_skill(self, request: GenerateSkillRequest) -> GeneratedCandidateResponse:
        """Generate a reviewed managed Skill Candidate from explicit provenance."""

        return await self._request(GENERATE_SKILL, request)

    async def get_skill(self, request: GetSkillRequest) -> SkillArtifact:
        """Read one exact approved managed Skill Revision."""

        return await self._request(GET_SKILL, request)

    async def scan_external_skills(self, request: ScanExternalSkillsRequest) -> ScanExternalSkillsResponse:
        """Refresh the configured host-local external Skill Registry."""

        return await self._request(SCAN_EXTERNAL_SKILLS, request)

    async def list_external_skills(self, request: ListExternalSkillsRequest) -> ListExternalSkillsResponse:
        """List external Skills after live local availability checks."""

        return await self._request(LIST_EXTERNAL_SKILLS, request)

    async def resolve_external_skill(self, request: ResolveExternalSkillRequest) -> ExternalSkillResolution:
        """Resolve one exact local external Skill fingerprint without fallback."""

        return await self._request(RESOLVE_EXTERNAL_SKILL, request)

    async def import_external_skill(self, request: ImportExternalSkillRequest) -> GeneratedCandidateResponse:
        """Snapshot an exact external package and propose a new managed Skill."""

        return await self._request(IMPORT_EXTERNAL_SKILL, request)

    async def list_artifact_candidates(self, request: ListArtifactCandidatesRequest) -> ArtifactCandidatePage:
        """Page current Candidate heads in the Review Inbox."""

        return await self._request(LIST_ARTIFACT_CANDIDATES, request)

    async def get_artifact_candidate(self, request: GetArtifactCandidateRequest) -> ArtifactCandidate:
        """Read the current head of one Candidate."""

        return await self._request(GET_ARTIFACT_CANDIDATE, request)

    async def approve_artifact_candidate(self, request: ApproveArtifactCandidateRequest) -> ArtifactCandidate:
        """Approve the exact current Candidate version."""

        return await self._request(APPROVE_ARTIFACT_CANDIDATE, request)

    async def reject_artifact_candidate(self, request: RejectArtifactCandidateRequest) -> ArtifactCandidate:
        """Reject the exact current Candidate version."""

        return await self._request(REJECT_ARTIFACT_CANDIDATE, request)

    async def revise_artifact_candidate(self, request: ReviseArtifactCandidateRequest) -> ArtifactCandidate:
        """Append a complete replacement Candidate proposal."""

        return await self._request(REVISE_ARTIFACT_CANDIDATE, request)

    async def _request(
        self,
        operation: Operation[_RequestT, _ResponseT],
        request: _RequestT | None = None,
        *,
        path_parameters: Mapping[str, str] | None = None,
    ) -> _ResponseT:
        operation_path = _bind_operation_path(operation, path_parameters)
        json_payload = None
        query_parameters = None
        if request is not None:
            if operation.request_type is None:
                message = f"{operation.operation_id} does not accept a request"
                raise TypeError(message)
            payload = TypeAdapter(operation.request_type).dump_python(
                request,
                mode="json",
                by_alias=True,
            )
            if operation.request_location == "query":
                query_parameters = {key: value for key, value in payload.items() if value is not None}
            else:
                json_payload = payload

        span = ClientSpan.start(operation.operation_id)
        try:
            headers = {} if self._headers is None else dict(self._headers)
            span.inject(headers)
            response = await self._http_client.request(
                operation.method,
                f"{self._base_url}{operation_path}",
                json=json_payload,
                headers=headers,
                params=query_parameters,
            )
        except asyncio.CancelledError as error:
            span.finish("cancelled", error=error)
            raise
        except httpx.HTTPError as exc:
            span.finish("failure", error=exc)
            raise TransportError(operation_path) from exc
        except BaseException as error:
            span.finish("failure", error=error)
            raise
        span.finish(
            "success" if response.status_code == operation.success_status else "failure",
            status_code=response.status_code,
        )

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
                operation_path,
                request_id=request_id,
            ) from exc


def _bind_operation_path(
    operation: Operation[_RequestT, _ResponseT],
    path_parameters: Mapping[str, str] | None,
) -> str:
    values = {} if path_parameters is None else dict(path_parameters)
    expected = set(operation.path_parameters)
    provided = set(values)
    if provided != expected:
        missing = sorted(expected - provided)
        unexpected = sorted(provided - expected)
        message = f"{operation.operation_id} path parameters do not match"
        if missing:
            message += f"; missing: {', '.join(missing)}"
        if unexpected:
            message += f"; unexpected: {', '.join(unexpected)}"
        raise TypeError(message)

    path = operation.path
    for name in operation.path_parameters:
        value = values[name]
        if not isinstance(value, str):
            message = f"{operation.operation_id} path parameter {name} must be a string"
            raise TypeError(message)
        path = path.replace(f"{{{name}}}", quote(value, safe=""))
    return path


def _decode_error(content: bytes) -> ErrorResponse | None:
    try:
        return ErrorResponse.model_validate_json(content)
    except ValidationError:
        return None
