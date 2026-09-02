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

"""Map HTTP transport values to the runtime application boundary."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.artifacts.handoff import HandoffCitation as RuntimeHandoffCitation
from powercontext.builtin.artifacts.skill import (
    ExternalSkillProviderScan,
    Skill,
    SkillContent,
)
from powercontext.builtin.artifacts.skill import (
    ExternalSkillRegistration as RuntimeExternalSkillRegistration,
)
from powercontext.builtin.artifacts.skill import (
    ExternalSkillResolution as RuntimeExternalSkillResolution,
)
from powercontext.builtin.review import ArtifactCandidate as RuntimeArtifactCandidate
from powercontext.builtin.review import ArtifactCandidatePage as RuntimeArtifactCandidatePage
from powercontext.builtin.review import CandidateStatus as RuntimeCandidateStatus
from powercontext.builtin.review.generation import (
    GeneratedCandidateResult as RuntimeGeneratedCandidateResult,
)
from powercontext.builtin.review.generation import SkillGenerationOrigin as RuntimeSkillGenerationOrigin
from powercontext.builtin.runtime import (
    ActivateHandoff,
    CaptureSource,
    Handoff,
    HandoffActivation,
    HandoffArtifactCitation,
    HandoffContent,
    HandoffDraft,
    HandoffEvidenceCheck,
    HandoffMemoryCitation,
    HandoffOmission,
    HandoffResolution,
    HandoffSourceCitation,
    HandoffStatement,
    InvalidRuntimeRequestError,
    MemoryChange,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryInput,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryHit,
    MemoryMutationResult,
    MemorySearchPage,
    PrepareContextRequest,
    PreparedContext,
    PreparedHandoff,
    PrepareHandoff,
    RememberMemoryRequest,
    SourceReceipt,
)
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest as RuntimeApproveArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    CommitConnectorCheckpoint as RuntimeCommitConnectorCheckpoint,
)
from powercontext.builtin.runtime import (
    ConnectorCheckpointState as RuntimeConnectorCheckpointState,
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
    MemoryCitation as RuntimeMemoryCitation,
)
from powercontext.builtin.runtime import (
    MemoryRevisionChanges as RuntimeMemoryRevisionChanges,
)
from powercontext.builtin.runtime import (
    ProposeExperienceRequest as RuntimeProposeExperienceRequest,
)
from powercontext.builtin.runtime import ProposeSkillRequest as RuntimeProposeSkillRequest
from powercontext.builtin.runtime import (
    RejectArtifactCandidateRequest as RuntimeRejectArtifactCandidateRequest,
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
    SubmitSourceObservation as RuntimeSubmitSourceObservation,
)
from powercontext.builtin.sources import ExternalSkillImportMode as RuntimeExternalSkillImportMode
from powercontext.builtin.work import (
    AcknowledgeHandoff as RuntimeAcknowledgeHandoff,
)
from powercontext.builtin.work import (
    CreateWorkContract as RuntimeCreateWorkContract,
)
from powercontext.builtin.work import (
    CurrentWorkHandoff as RuntimeCurrentWorkHandoff,
)
from powercontext.builtin.work import (
    HandoffAcknowledgement as RuntimeHandoffAcknowledgement,
)
from powercontext.builtin.work import (
    HandoffCurrentWork as RuntimeHandoffCurrentWork,
)
from powercontext.builtin.work import PreparedWorkHandoff as RuntimePreparedWorkHandoff
from powercontext.builtin.work import ReceiverChecks as RuntimeReceiverChecks
from powercontext.builtin.work import RecordTaskOutcome as RuntimeRecordTaskOutcome
from powercontext.builtin.work import TaskCheck as RuntimeTaskCheck
from powercontext.builtin.work import TaskOutcome as RuntimeTaskOutcome
from powercontext.builtin.work import WorkClaim as RuntimeWorkClaim
from powercontext.builtin.work import WorkContract as RuntimeWorkContract
from powercontext.builtin.work import WorkSourceReceipt as RuntimeWorkSourceReceipt
from powercontext.http import (
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    ArtifactReference,
    CandidateFamily,
    CandidateStatus,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CaptureStatus,
    CommitConnectorCheckpointRequest,
    CommittedHandoff,
    ConnectorCheckpointState,
    CreateWorkContractRequest,
    EntryChange,
    EntryChangeOperation,
    ExperienceArtifact,
    ExperienceProposal,
    ExternalSkillRegistration,
    ExternalSkillResolution,
    ExternalSkillResolutionStatus,
    FlushMemoryResponse,
    FlushStatus,
    GeneratedCandidateResponse,
    GeneratedCandidateStatus,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetArtifactCandidateRequest,
    GetConnectorCheckpointRequest,
    GetExperienceRequest,
    GetMemoryEntryRequest,
    GetSkillRequest,
    HandoffAcknowledgement,
    HandoffActivationStatus,
    HandoffClaim,
    HandoffCurrentWorkRequest,
    HandoffDisposition,
    HandoffEvidenceStatus,
    HandoffResolutionStatus,
    HandoffSchema,
    HandoffSelection,
    ImportExternalSkillRequest,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ListMemoryChangesResponse,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryEntryState,
    MemoryMatchedBy,
    MemoryMutationResponse,
    MemoryRevisionChanges,
    MemoryUsedSearchMode,
    PreparedContextSchema,
    PreparedContextStatus,
    PreparedHandoffSchema,
    PreparedWorkHandoff,
    PrepareHandoffRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RecordTaskOutcomeRequest,
    RejectArtifactCandidateRequest,
    ResolveExternalSkillRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
    ScanExternalSkillsResponse,
    ScopedStats,
    SearchMemoryHit,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SkillArtifact,
    SkillProposal,
    SkillValidationItem,
    SourceDefinitionManifest,
    SourceObservationReceipt,
    SourceReference,
    SubmitSourceObservationRequest,
    TaskCheck,
    WorkClaim,
    WorkSourceKind,
    WorkSourceReceipt,
)
from powercontext.http import ConnectorBinding as HttpConnectorBinding
from powercontext.http import (
    HandoffActivation as TransportHandoffActivation,
)
from powercontext.http import (
    HandoffArtifactCitation as TransportHandoffArtifactCitation,
)
from powercontext.http import (
    HandoffCitation as TransportHandoffCitation,
)
from powercontext.http import (
    HandoffContent as TransportHandoffContent,
)
from powercontext.http import (
    HandoffDraft as TransportHandoffDraft,
)
from powercontext.http import (
    HandoffEvidenceCheck as TransportHandoffEvidenceCheck,
)
from powercontext.http import (
    HandoffMemoryCitation as TransportHandoffMemoryCitation,
)
from powercontext.http import (
    HandoffOmission as TransportHandoffOmission,
)
from powercontext.http import (
    HandoffResolution as TransportHandoffResolution,
)
from powercontext.http import (
    HandoffSourceCitation as TransportHandoffSourceCitation,
)
from powercontext.http import (
    HandoffStatement as TransportHandoffStatement,
)
from powercontext.http import (
    MemoryCitation as TransportMemoryCitation,
)
from powercontext.http import (
    PrepareContextRequest as TransportPrepareContextRequest,
)
from powercontext.http import (
    PreparedContext as TransportPreparedContext,
)
from powercontext.http import (
    PreparedHandoff as TransportPreparedHandoff,
)
from powercontext.http import (
    RememberMemoryRequest as TransportRememberMemoryRequest,
)
from powercontext.sources import (
    ConnectorBinding as RuntimeConnectorBinding,
)
from powercontext.sources import (
    SourceDefinitionManifest as RuntimeSourceDefinitionManifest,
)
from powercontext.sources import (
    SourceObservation as RuntimeSourceObservation,
)
from powercontext.sources import SourceRef


def capture_request(value: CaptureContentSourceRequest) -> CaptureSource:
    return CaptureSource(
        source_id=value.source_id,
        content=value.content,
        metadata={} if value.metadata is None else value.metadata,
    )


def create_work_contract_request(value: CreateWorkContractRequest) -> RuntimeCreateWorkContract:
    try:
        return RuntimeCreateWorkContract(
            source_id=value.source_id,
            contract=RuntimeWorkContract(
                objective=value.contract.objective,
                facts=tuple(_runtime_work_claim(claim) for claim in value.contract.facts),
                in_scope=tuple(item.root for item in value.contract.in_scope),
                exclusions=tuple(item.root for item in value.contract.exclusions),
                completion_criteria=tuple(item.root for item in value.contract.completion_criteria),
                authorization_notes=tuple(item.root for item in value.contract.authorization_notes),
                open_questions=tuple(item.root for item in value.contract.open_questions),
            ),
        )
    except ValidationError as error:
        raise InvalidRuntimeRequestError("work-contract") from error


def handoff_current_work_request(value: HandoffCurrentWorkRequest) -> RuntimeHandoffCurrentWork:
    try:
        return RuntimeHandoffCurrentWork(
            source_id=value.source_id,
            handoff=RuntimeCurrentWorkHandoff(
                objective=value.handoff.objective,
                state=tuple(_runtime_work_claim(claim) for claim in value.handoff.state),
                disposition=value.handoff.disposition.value,
                next_action=(
                    None if value.handoff.next_action is None else _runtime_work_claim(value.handoff.next_action)
                ),
                omissions=tuple(item.root for item in value.handoff.omissions),
            ),
        )
    except ValidationError as error:
        raise InvalidRuntimeRequestError("current-work-handoff") from error


def record_task_outcome_request(value: RecordTaskOutcomeRequest) -> RuntimeRecordTaskOutcome:
    try:
        return RuntimeRecordTaskOutcome(
            source_id=value.source_id,
            outcome=RuntimeTaskOutcome(
                objective=value.outcome.objective,
                status=value.outcome.status.value,
                summary=value.outcome.summary,
                handoff_receipt_ref=(
                    None
                    if value.outcome.handoff_receipt_ref is None
                    else runtime_source_reference(value.outcome.handoff_receipt_ref)
                ),
                observations=tuple(_runtime_work_claim(claim) for claim in value.outcome.observations),
                checks=tuple(_runtime_task_check(check) for check in value.outcome.checks),
                produced_artifacts=tuple(runtime_artifact_reference(ref) for ref in value.outcome.produced_artifacts),
                remaining_work=tuple(item.root for item in value.outcome.remaining_work),
            ),
        )
    except ValidationError as error:
        raise InvalidRuntimeRequestError("task-outcome") from error


def acknowledge_handoff_request(value: AcknowledgeHandoffRequest) -> RuntimeAcknowledgeHandoff:
    try:
        return RuntimeAcknowledgeHandoff(
            source_id=value.source_id,
            receiver=value.receiver,
            status=value.status.value,
            selection=value.selection.value,
            receiver_checks=(
                None
                if value.receiver_checks is None
                else RuntimeReceiverChecks(
                    live_state=value.receiver_checks.live_state.value,
                    capability=value.receiver_checks.capability.value,
                    authorization=value.receiver_checks.authorization.value,
                )
            ),
            prepared=None if value.prepared is None else runtime_prepared_handoff(value.prepared),
            revision=None if value.revision is None else runtime_artifact_reference(value.revision),
            message=value.message,
        )
    except ValidationError as error:
        raise InvalidRuntimeRequestError("handoff-acknowledgement") from error


def work_source_receipt_response(value: RuntimeWorkSourceReceipt) -> WorkSourceReceipt:
    return WorkSourceReceipt(
        kind=WorkSourceKind(value.kind),
        source=source_reference(value.source_ref),
        position=value.position,
        content_digest=value.content_digest,
    )


def prepared_work_handoff_response(value: RuntimePreparedWorkHandoff) -> PreparedWorkHandoff:
    return PreparedWorkHandoff(
        boundary=work_source_receipt_response(value.boundary),
        handoff=prepared_handoff_response(value.handoff),
    )


def handoff_acknowledgement_response(value: RuntimeHandoffAcknowledgement) -> HandoffAcknowledgement:
    return HandoffAcknowledgement(
        resolution=handoff_resolution_response(value.resolution),
        receipt=work_source_receipt_response(value.receipt),
    )


def _runtime_work_claim(value: WorkClaim) -> RuntimeWorkClaim:
    return RuntimeWorkClaim(
        text=value.text,
        basis=value.basis.value,
        evidence=tuple(runtime_handoff_citation(citation) for citation in value.evidence),
    )


def _runtime_task_check(value: TaskCheck) -> RuntimeTaskCheck:
    return RuntimeTaskCheck(
        name=value.name,
        status=value.status.value,
        details=value.details,
        basis=value.basis.value,
        evidence=tuple(runtime_handoff_citation(citation) for citation in value.evidence),
    )


def capture_response(value: SourceReceipt) -> CaptureContentSourceResponse:
    return CaptureContentSourceResponse(
        status=CaptureStatus.ACCEPTED,
        source=SourceReference(name=value.source_ref.source_type, source_id=value.source_ref.source_id),
        position=value.sequence,
    )


def runtime_source_definition_manifest(value: SourceDefinitionManifest) -> RuntimeSourceDefinitionManifest:
    try:
        return RuntimeSourceDefinitionManifest.model_validate(value.model_dump(mode="json", by_alias=True))
    except ValidationError as error:
        raise InvalidRuntimeRequestError("source-definition-manifest") from error


def source_definition_manifest_response(value: RuntimeSourceDefinitionManifest) -> SourceDefinitionManifest:
    return SourceDefinitionManifest.model_validate(value.model_dump(mode="json", by_alias=True))


def runtime_connector_binding(value: HttpConnectorBinding) -> RuntimeConnectorBinding:
    try:
        return RuntimeConnectorBinding.model_validate(value.model_dump(mode="json"))
    except ValidationError as error:
        raise InvalidRuntimeRequestError("connector-binding") from error


def connector_checkpoint_request(value: GetConnectorCheckpointRequest) -> RuntimeConnectorBinding:
    return runtime_connector_binding(value.binding)


def submit_source_observation_request(value: SubmitSourceObservationRequest) -> RuntimeSubmitSourceObservation:
    try:
        return RuntimeSubmitSourceObservation(
            scope_id=value.scope_id,
            observation=RuntimeSourceObservation.model_validate(value.observation.model_dump(mode="json")),
        )
    except ValidationError as error:
        raise InvalidRuntimeRequestError("source-observation") from error


def commit_connector_checkpoint_request(
    value: CommitConnectorCheckpointRequest,
) -> RuntimeCommitConnectorCheckpoint:
    try:
        return RuntimeCommitConnectorCheckpoint(
            binding=runtime_connector_binding(value.binding),
            expected=value.expected,
            checkpoint=value.checkpoint,
        )
    except ValidationError as error:
        raise InvalidRuntimeRequestError("connector-checkpoint") from error


def connector_checkpoint_response(value: RuntimeConnectorCheckpointState) -> ConnectorCheckpointState:
    return ConnectorCheckpointState.model_validate(value.model_dump(mode="json"))


def source_observation_receipt_response(value: SourceReceipt) -> SourceObservationReceipt:
    return SourceObservationReceipt(
        source=source_reference(value.source_ref),
        position=value.sequence,
    )


def statistics_response(value: RuntimeStatistics) -> ScopedStats:
    return ScopedStats.model_validate(value.model_dump(mode="json"))


def flush_response(value: MemoryFlushResult) -> FlushMemoryResponse:
    return FlushMemoryResponse(
        status=FlushStatus.PROCESSED if value.processed else FlushStatus.IDLE,
        previous_cursor=value.previous_cursor,
        current_cursor=value.current_cursor,
        high_watermark=value.high_watermark,
        processed_source_count=value.source_count,
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
    )


def remember_request(value: TransportRememberMemoryRequest) -> RememberMemoryRequest:
    return RememberMemoryRequest(
        entries=(MemoryEntryInput(kind=value.kind, text=value.text, reason=value.reason),),
        expected_revision=value.expected_revision,
    )


def propose_experience_request(value: ProposeExperienceRequest) -> RuntimeProposeExperienceRequest:
    return RuntimeProposeExperienceRequest(
        proposal=experience_content(value.proposal),
        sources=tuple(runtime_source_reference(source) for source in value.source_refs),
        artifacts=tuple(runtime_artifact_reference(artifact) for artifact in value.artifact_refs),
        target=None if value.target is None else runtime_artifact_reference(value.target),
        reason=value.reason,
    )


def generate_experience_request(value: GenerateExperienceRequest) -> RuntimeGenerateExperienceRequest:
    return RuntimeGenerateExperienceRequest(
        sources=tuple(runtime_source_reference(source) for source in value.source_refs),
        artifacts=tuple(runtime_artifact_reference(artifact) for artifact in value.artifact_refs),
        target=None if value.target is None else runtime_artifact_reference(value.target),
        reason=value.reason,
    )


def get_experience_request(value: GetExperienceRequest) -> RuntimeGetExperienceRequest:
    return RuntimeGetExperienceRequest(artifact=runtime_artifact_reference(value.artifact))


def propose_skill_request(value: ProposeSkillRequest) -> RuntimeProposeSkillRequest:
    return RuntimeProposeSkillRequest(
        proposal=skill_content(value.proposal),
        sources=tuple(runtime_source_reference(source) for source in value.source_refs),
        artifacts=tuple(runtime_artifact_reference(artifact) for artifact in value.artifact_refs),
        target=None if value.target is None else runtime_artifact_reference(value.target),
        reason=value.reason,
    )


def generate_skill_request(value: GenerateSkillRequest) -> RuntimeGenerateSkillRequest:
    return RuntimeGenerateSkillRequest(
        origin=RuntimeSkillGenerationOrigin(value.origin.value),
        sources=tuple(runtime_source_reference(source) for source in value.source_refs),
        artifacts=tuple(runtime_artifact_reference(artifact) for artifact in value.artifact_refs),
        target=None if value.target is None else runtime_artifact_reference(value.target),
        reason=value.reason,
    )


def get_skill_request(value: GetSkillRequest) -> RuntimeGetSkillRequest:
    return RuntimeGetSkillRequest(artifact=runtime_artifact_reference(value.artifact))


def list_candidates_request(value: ListArtifactCandidatesRequest) -> RuntimeListArtifactCandidatesRequest:
    return RuntimeListArtifactCandidatesRequest(
        status=RuntimeCandidateStatus(value.status.value),
        family=None if value.family is None else value.family.value,
        cursor=value.cursor,
        limit=value.limit,
    )


def get_candidate_request(value: GetArtifactCandidateRequest) -> RuntimeGetArtifactCandidateRequest:
    return RuntimeGetArtifactCandidateRequest(candidate_id=value.candidate_id)


def approve_candidate_request(value: ApproveArtifactCandidateRequest) -> RuntimeApproveArtifactCandidateRequest:
    return RuntimeApproveArtifactCandidateRequest(
        candidate_id=value.candidate_id,
        expected_version=value.expected_version,
    )


def reject_candidate_request(value: RejectArtifactCandidateRequest) -> RuntimeRejectArtifactCandidateRequest:
    return RuntimeRejectArtifactCandidateRequest(
        candidate_id=value.candidate_id,
        expected_version=value.expected_version,
        reason=value.reason,
    )


def revise_candidate_request(value: ReviseArtifactCandidateRequest) -> RuntimeReviseArtifactCandidateRequest:
    return RuntimeReviseArtifactCandidateRequest(
        candidate_id=value.candidate_id,
        expected_version=value.expected_version,
        proposal=reviewed_content(value.proposal),
        sources=tuple(runtime_source_reference(source) for source in value.source_refs),
        artifacts=tuple(runtime_artifact_reference(artifact) for artifact in value.artifact_refs),
        target=None if value.target is None else runtime_artifact_reference(value.target),
        reason=value.reason,
    )


def search_request(value: SearchMemoryRequest) -> RuntimeSearchMemoryRequest:
    return RuntimeSearchMemoryRequest(query=value.query, limit=value.limit, mode=value.mode.value)


def prepare_context_request(value: TransportPrepareContextRequest) -> PrepareContextRequest:
    return PrepareContextRequest(query=value.query, max_bytes=value.max_bytes)


def activate_handoff_request(value: ActivateHandoffRequest) -> ActivateHandoff:
    return ActivateHandoff(
        boundary_source=runtime_source_reference(value.boundary_source),
        objective=value.objective,
        evidence=tuple(runtime_handoff_citation(citation) for citation in value.evidence),
        max_bytes=value.max_bytes,
    )


def handoff_activation_response(value: HandoffActivation) -> TransportHandoffActivation:
    return TransportHandoffActivation(
        status=HandoffActivationStatus(value.status),
        boundary_source=source_reference(value.boundary_source),
        previous_position=value.previous_position,
        current_position=value.current_position,
        draft=None if value.draft is None else handoff_draft_response(value.draft),
    )


def prepare_handoff_request(value: PrepareHandoffRequest) -> PrepareHandoff:
    return PrepareHandoff(
        objective=value.objective,
        evidence=tuple(runtime_handoff_citation(citation) for citation in value.evidence),
        max_bytes=value.max_bytes,
    )


def runtime_handoff_draft(value: TransportHandoffDraft) -> HandoffDraft:
    return HandoffDraft(
        objective=value.objective,
        state=tuple(runtime_handoff_statement(statement) for statement in value.state),
        disposition=value.disposition.value,
        next_action=None if value.next_action is None else runtime_handoff_statement(value.next_action),
        omissions=tuple(runtime_handoff_omission(omission) for omission in value.omissions),
    )


def runtime_prepared_handoff(value: TransportPreparedHandoff) -> PreparedHandoff:
    return PreparedHandoff(
        scope_id=value.scope_id,
        base=None if value.base is None else runtime_artifact_reference(value.base),
        content=runtime_handoff_content(value.content),
    )


def handoff_draft_response(value: HandoffDraft) -> TransportHandoffDraft:
    return TransportHandoffDraft(
        objective=value.objective,
        state=[handoff_statement(statement) for statement in value.state],
        disposition=HandoffDisposition(value.disposition),
        next_action=None if value.next_action is None else handoff_statement(value.next_action),
        omissions=[handoff_omission(omission) for omission in value.omissions],
    )


def prepared_handoff_response(value: PreparedHandoff) -> TransportPreparedHandoff:
    return TransportPreparedHandoff.model_validate({
        "schema": PreparedHandoffSchema(value.schema_version),
        "scope_id": value.scope_id,
        "base": None if value.base is None else artifact_reference(value.base),
        "content": handoff_content(value.content),
    })


def committed_handoff_response(value: Handoff) -> CommittedHandoff:
    return CommittedHandoff(
        reference=artifact_reference(value.as_ref()),
        content=handoff_content(value.content),
        source_refs=[source_reference(reference) for reference in value.lineage.sources],
        artifact_refs=[artifact_reference(reference) for reference in value.lineage.artifacts],
    )


def handoff_resolution_response(value: HandoffResolution) -> TransportHandoffResolution:
    return TransportHandoffResolution.model_validate({
        "trust": value.trust,
        "status": HandoffResolutionStatus(value.status),
        "scope_id": value.scope_id,
        "content": None if value.content is None else handoff_content(value.content),
        "selection": None if value.selection is None else HandoffSelection(value.selection),
        "selected_revision": (None if value.selected_revision is None else artifact_reference(value.selected_revision)),
        "current_revision": None if value.current_revision is None else artifact_reference(value.current_revision),
        "evidence_checks": [handoff_evidence_check(check) for check in value.evidence_checks],
    })


def get_request(value: GetMemoryEntryRequest) -> RuntimeGetMemoryEntryRequest:
    return RuntimeGetMemoryEntryRequest(citation=runtime_citation(value.citation))


def revise_request(value: ReviseMemoryEntryRequest) -> RuntimeReviseMemoryEntryRequest:
    return RuntimeReviseMemoryEntryRequest(
        citation=runtime_citation(value.citation),
        kind=value.kind,
        text=value.text,
        reason=value.reason,
    )


def retire_request(value: RetireMemoryEntryRequest) -> RuntimeRetireMemoryEntryRequest:
    return RuntimeRetireMemoryEntryRequest(citation=runtime_citation(value.citation), reason=value.reason)


def search_response(value: MemorySearchPage) -> SearchMemoryResponse:
    return SearchMemoryResponse(
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
        mode=None if value.mode is None else MemoryUsedSearchMode(value.mode),
        hits=[search_hit(hit) for hit in value.hits],
    )


def prepared_context_response(value: PreparedContext) -> TransportPreparedContext:
    return TransportPreparedContext.model_validate({
        "schema": PreparedContextSchema(value.schema_version),
        "status": PreparedContextStatus(value.status),
        "content": value.content,
        "content_bytes": value.content_bytes,
    })


def entries_response(value: MemoryEntriesPage) -> ListMemoryEntriesResponse:
    return ListMemoryEntriesResponse(
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
        entries=[memory_entry(item) for item in value.entries],
    )


def mutation_response(value: MemoryMutationResult) -> MemoryMutationResponse:
    return MemoryMutationResponse(
        memory=artifact_reference(value.memory_ref),
        entry=None if value.entry is None else memory_entry(value.entry),
    )


def changes_response(value: MemoryChangesPage) -> ListMemoryChangesResponse:
    return ListMemoryChangesResponse(
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
        revisions=[revision_changes(revision) for revision in value.revisions],
    )


def candidate_response(value: RuntimeArtifactCandidate[Any]) -> ArtifactCandidate:
    return ArtifactCandidate(
        candidate_id=value.candidate_id,
        version=value.version,
        family=CandidateFamily(value.family),
        status=CandidateStatus(value.status.value),
        proposal=reviewed_proposal(value.proposal),
        source_refs=[source_reference(source) for source in value.sources],
        artifact_refs=[artifact_reference(artifact) for artifact in value.artifacts],
        target=None if value.target is None else artifact_reference(value.target),
        reason=value.reason,
        result_artifact=None if value.result_artifact is None else artifact_reference(value.result_artifact),
        decision_reason=value.decision_reason,
    )


def generated_candidate_response(value: RuntimeGeneratedCandidateResult) -> GeneratedCandidateResponse:
    return GeneratedCandidateResponse(
        status=GeneratedCandidateStatus.PENDING if value.generated else GeneratedCandidateStatus.NO_OP,
        candidate=None if value.candidate is None else candidate_response(value.candidate),
    )


def candidate_page_response(value: RuntimeArtifactCandidatePage[Any]) -> ArtifactCandidatePage:
    return ArtifactCandidatePage(
        candidates=[candidate_response(candidate) for candidate in value.candidates],
        next_cursor=value.next_cursor,
    )


def experience_response(value: Experience) -> ExperienceArtifact:
    return ExperienceArtifact(
        artifact=artifact_reference(value.as_ref()),
        content=experience_proposal(value.content),
        source_refs=[source_reference(source) for source in value.lineage.sources],
        artifact_refs=[artifact_reference(artifact) for artifact in value.lineage.artifacts],
    )


def skill_response(value: Skill) -> SkillArtifact:
    return SkillArtifact(
        artifact=artifact_reference(value.as_ref()),
        content=skill_proposal(value.content),
        source_refs=[source_reference(source) for source in value.lineage.sources],
        artifact_refs=[artifact_reference(artifact) for artifact in value.lineage.artifacts],
    )


def list_external_skills_request(value: ListExternalSkillsRequest) -> RuntimeListExternalSkillsRequest:
    return RuntimeListExternalSkillsRequest(include_unavailable=value.include_unavailable)


def resolve_external_skill_request(value: ResolveExternalSkillRequest) -> RuntimeResolveExternalSkillRequest:
    return RuntimeResolveExternalSkillRequest(
        external_skill_id=value.external_skill_id,
        fingerprint=value.fingerprint,
    )


def import_external_skill_request(value: ImportExternalSkillRequest) -> RuntimeImportExternalSkillRequest:
    return RuntimeImportExternalSkillRequest(
        external_skill_id=value.external_skill_id,
        fingerprint=value.fingerprint,
        mode=RuntimeExternalSkillImportMode(value.mode.value),
        reason=value.reason,
    )


def scan_external_skills_response(value: ExternalSkillProviderScan) -> ScanExternalSkillsResponse:
    return ScanExternalSkillsResponse(
        registrations=[external_skill_registration(registration) for registration in value.registrations],
        skipped=value.skipped,
    )


def list_external_skills_response(
    values: tuple[RuntimeExternalSkillResolution, ...],
) -> ListExternalSkillsResponse:
    return ListExternalSkillsResponse(skills=[external_skill_resolution(value) for value in values])


def external_skill_resolution(value: RuntimeExternalSkillResolution) -> ExternalSkillResolution:
    return ExternalSkillResolution(
        registration=external_skill_registration(value.registration),
        status=ExternalSkillResolutionStatus(value.status.value),
        entrypoint=value.entrypoint,
    )


def external_skill_registration(value: RuntimeExternalSkillRegistration) -> ExternalSkillRegistration:
    return ExternalSkillRegistration.model_validate(value.model_dump(mode="json"))


def experience_content(value: ExperienceProposal) -> ExperienceContent:
    return ExperienceContent(
        situation=value.situation,
        action=value.action,
        outcome=value.outcome,
        lesson=value.lesson,
    )


def experience_proposal(value: ExperienceContent) -> ExperienceProposal:
    return ExperienceProposal(
        situation=value.situation,
        action=value.action,
        outcome=value.outcome,
        lesson=value.lesson,
    )


def skill_content(value: SkillProposal) -> SkillContent:
    return SkillContent(
        name=value.name,
        description=value.description,
        instructions=value.instructions,
        validation=tuple(item.root for item in value.validation),
    )


def skill_proposal(value: SkillContent) -> SkillProposal:
    return SkillProposal(
        name=value.name,
        description=value.description,
        instructions=value.instructions,
        validation=[SkillValidationItem(item) for item in value.validation],
    )


def reviewed_content(value: ExperienceProposal | SkillProposal) -> ExperienceContent | SkillContent:
    if isinstance(value, ExperienceProposal):
        return experience_content(value)
    return skill_content(value)


def reviewed_proposal(value: object) -> ExperienceProposal | SkillProposal:
    if isinstance(value, ExperienceContent):
        return experience_proposal(value)
    if isinstance(value, SkillContent):
        return skill_proposal(value)
    raise TypeError("Candidate proposal belongs to an unsupported Artifact Family")  # noqa: TRY003


def artifact_reference(value: ArtifactRef) -> ArtifactReference:
    return ArtifactReference(family=value.family, artifact_id=value.artifact_id, revision=value.revision)


def runtime_artifact_reference(value: ArtifactReference) -> ArtifactRef:
    return ArtifactRef(
        family=value.family,
        artifact_id=value.artifact_id,
        revision=value.revision,
    )


def source_reference(value: SourceRef) -> SourceReference:
    return SourceReference(name=value.source_type, source_id=value.source_id)


def runtime_source_reference(value: SourceReference) -> SourceRef:
    return SourceRef(source_type=value.name, source_id=value.source_id)


def runtime_citation(value: TransportMemoryCitation) -> RuntimeMemoryCitation:
    return RuntimeMemoryCitation(
        memory_ref=ArtifactRef(
            family=value.memory_ref.family,
            artifact_id=value.memory_ref.artifact_id,
            revision=value.memory_ref.revision,
        ),
        entry_id=value.entry_id,
        entry_version_id=value.entry_version_id,
    )


def transport_citation(value: RuntimeMemoryCitation) -> TransportMemoryCitation:
    return TransportMemoryCitation(
        memory_ref=artifact_reference(value.memory_ref),
        entry_id=value.entry_id,
        entry_version_id=value.entry_version_id,
    )


def runtime_handoff_citation(value: TransportHandoffCitation) -> RuntimeHandoffCitation:
    citation = value.root
    if isinstance(citation, TransportHandoffSourceCitation):
        return HandoffSourceCitation(source_ref=runtime_source_reference(citation.source_ref))
    if isinstance(citation, TransportHandoffArtifactCitation):
        return HandoffArtifactCitation(artifact_ref=runtime_artifact_reference(citation.artifact_ref))
    if isinstance(citation, TransportHandoffMemoryCitation):
        return HandoffMemoryCitation(memory_citation=runtime_citation(citation.memory_citation))
    raise TypeError(f"unsupported Handoff citation: {type(citation).__name__}")  # noqa: TRY003


def handoff_citation(value: RuntimeHandoffCitation) -> TransportHandoffCitation:
    if isinstance(value, HandoffSourceCitation):
        citation = TransportHandoffSourceCitation(
            kind="source",
            source_ref=source_reference(value.source_ref),
        )
    elif isinstance(value, HandoffArtifactCitation):
        citation = TransportHandoffArtifactCitation(
            kind="artifact",
            artifact_ref=artifact_reference(value.artifact_ref),
        )
    elif isinstance(value, HandoffMemoryCitation):
        citation = TransportHandoffMemoryCitation(
            kind="memory",
            memory_citation=transport_citation(value.memory_citation),
        )
    else:
        raise TypeError(f"unsupported Handoff citation: {type(value).__name__}")  # noqa: TRY003
    return TransportHandoffCitation(root=citation)


def runtime_handoff_statement(value: TransportHandoffStatement) -> HandoffStatement:
    return HandoffStatement(
        text=value.text,
        citations=tuple(runtime_handoff_citation(citation) for citation in value.citations),
    )


def handoff_statement(value: HandoffStatement) -> TransportHandoffStatement:
    return TransportHandoffStatement(
        text=value.text,
        citations=[handoff_citation(citation) for citation in value.citations],
    )


def runtime_handoff_omission(value: TransportHandoffOmission) -> HandoffOmission:
    return HandoffOmission(
        text=value.text,
        citation=None if value.citation is None else runtime_handoff_citation(value.citation),
    )


def handoff_omission(value: HandoffOmission) -> TransportHandoffOmission:
    return TransportHandoffOmission(
        text=value.text,
        citation=None if value.citation is None else handoff_citation(value.citation),
    )


def runtime_handoff_content(value: TransportHandoffContent) -> HandoffContent:
    return HandoffContent(
        objective=value.objective,
        state=tuple(runtime_handoff_statement(statement) for statement in value.state),
        disposition=value.disposition.value,
        next_action=None if value.next_action is None else runtime_handoff_statement(value.next_action),
        omissions=tuple(runtime_handoff_omission(omission) for omission in value.omissions),
    )


def handoff_content(value: HandoffContent) -> TransportHandoffContent:
    return TransportHandoffContent.model_validate({
        "schema": HandoffSchema(value.schema_version),
        "objective": value.objective,
        "state": [handoff_statement(statement) for statement in value.state],
        "disposition": HandoffDisposition(value.disposition),
        "next_action": None if value.next_action is None else handoff_statement(value.next_action),
        "omissions": [handoff_omission(omission) for omission in value.omissions],
    })


def handoff_evidence_check(value: HandoffEvidenceCheck) -> TransportHandoffEvidenceCheck:
    return TransportHandoffEvidenceCheck(
        claim=HandoffClaim(value.claim),
        state_index=value.state_index,
        status=HandoffEvidenceStatus(value.status),
        unavailable_evidence=[handoff_citation(citation) for citation in value.unavailable_evidence],
    )


def entry_change(value: MemoryChange) -> EntryChange:
    return EntryChange(
        op=EntryChangeOperation(value.op),
        entry_id=value.entry_id,
        from_entry_version_id=value.from_entry_version_id,
        to_entry_version_id=value.to_entry_version_id,
        reason=value.reason,
    )


def revision_changes(value: RuntimeMemoryRevisionChanges) -> MemoryRevisionChanges:
    return MemoryRevisionChanges(
        memory_ref=artifact_reference(value.memory_ref),
        changes=[entry_change(change) for change in value.changes],
    )


def search_hit(value: MemoryHit) -> SearchMemoryHit:
    return SearchMemoryHit(
        citation=transport_citation(
            RuntimeMemoryCitation(
                memory_ref=value.memory_ref,
                entry_id=value.entry_id,
                entry_version_id=value.entry_version_id,
            )
        ),
        text=value.text,
        score=value.score,
        matched_by=[MemoryMatchedBy(channel) for channel in value.matched_by],
    )


def memory_entry(value: MemoryEntryRecord) -> MemoryEntry:
    entry = value.entry
    return MemoryEntry(
        citation=transport_citation(value.citation),
        version=entry.version,
        kind=entry.kind,
        text=entry.text,
        state=MemoryEntryState(value.state),
        source_refs=[SourceReference(name=source.source_type, source_id=source.source_id) for source in entry.sources],
        artifact_refs=[artifact_reference(reference) for reference in entry.artifacts],
    )
