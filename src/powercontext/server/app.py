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
import base64
import binascii
import ipaddress
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from time import perf_counter
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, TypeVar, cast
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response, status
from fastapi import Path as PathParameter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from opentelemetry.trace import SpanKind
from pydantic import JsonValue
from pydantic import ValidationError as PydanticValidationError
from scalar_fastapi import AgentScalarConfig, get_scalar_api_reference
from starlette.middleware import Middleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.routing import Match
from starlette.types import Lifespan, Scope
from typing_extensions import override

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.handoff import (
    HandoffCitation,
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
    AgentKind,
    AgentSkillTarget,
    ExternalSkillNotFoundError,
    ExternalSkillRegistryUnavailableError,
    ExternalSkillSnapshotUnavailableError,
    Skill,
    SkillPackageRef,
    SkillPackageSnapshot,
    SkillSearchHit,
)
from powercontext.builtin.artifacts.skill import (
    ExternalSkillResolution as RuntimeExternalSkillResolution,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemotePublicationGenerationError,
    RemoteSkillDistributionError,
    RemoteSkillLifecycleError,
    RemoteTargetAuthenticationError,
    RemoteTargetEnrollmentError,
    RemoteTargetStateError,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteSkillObservation as DomainRemoteSkillObservation,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteSkillReceipt as DomainRemoteSkillReceipt,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteSkillReceiptResult as DomainRemoteSkillReceiptResult,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteSkillReconcileResult as DomainRemoteSkillReconcileResult,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteSkillTargetStatus as DomainRemoteSkillTargetStatus,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteTargetCredential as DomainRemoteTargetCredential,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteTargetEnrollment as DomainRemoteTargetEnrollment,
)
from powercontext.builtin.artifacts.skill.publication import ManagedSkillPublicationStatus
from powercontext.builtin.handoff_report import (
    HandoffReportApplication,
    HandoffReportError,
    HandoffReportInconsistentError,
    HandoffReportTooLargeError,
)
from powercontext.builtin.inference.errors import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.persistence.agent_skill_targets import RemoteAgentSkillTarget
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactLifecycleState,
    InvalidArtifactLifecycleError,
)
from powercontext.builtin.persistence.errors import (
    PersistenceError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.skill_publications import SkillPublication
from powercontext.builtin.publication import (
    ArtifactPublicationApplication,
    ArtifactPublicationConflictError,
    ArtifactPublicationUnsupportedError,
)
from powercontext.builtin.publication import (
    ArtifactPublicationRequest as DomainArtifactPublicationRequest,
)
from powercontext.builtin.records import (
    ArtifactAlreadyExistsError,
    ArtifactRevisionPreconditionError,
    BaseAccessError,
    BaseValueConflictError,
    BaseValueNotFoundError,
    CursorExpiredError,
    InvalidBaseAccessRequestError,
    InvalidCursorError,
    LogicalArtifactRecord,
)
from powercontext.builtin.records import (
    ArtifactCollectionItem as RuntimeArtifactCollectionItem,
)
from powercontext.builtin.records import (
    ArtifactCreated as RuntimeArtifactCreated,
)
from powercontext.builtin.records import (
    ArtifactRecord as RuntimeArtifactRecord,
)
from powercontext.builtin.records import (
    ArtifactRecordPage as RuntimeArtifactRecordPage,
)
from powercontext.builtin.records import (
    ArtifactWrite as RuntimeArtifactWrite,
)
from powercontext.builtin.records import (
    SourceRecord as RuntimeSourceRecord,
)
from powercontext.builtin.review import ArtifactCandidate as RuntimeArtifactCandidate
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
from powercontext.builtin.runtime import (
    SubmitSourceObservation as RuntimeSubmitSourceObservation,
)
from powercontext.builtin.scope import (
    ScopeApplication,
    ScopeBindingNotFoundError,
    ScopeDraft,
    ScopeIdempotencyConflictError,
    ScopeMutation,
    ScopeNotFoundError,
    ScopeRelationshipError,
    ScopeVersionConflictError,
)
from powercontext.builtin.scope import (
    ScopeBindingKey as DomainScopeBindingKey,
)
from powercontext.builtin.scope import (
    ScopeDescriptor as DomainScopeDescriptor,
)
from powercontext.builtin.scope import (
    ScopeExternalReference as DomainScopeExternalReference,
)
from powercontext.builtin.scope import (
    ScopeSelection as DomainScopeSelection,
)
from powercontext.builtin.source_eligibility import SourceNotEligibleError
from powercontext.builtin.sources import (
    ObservedInvocation,
    ObservedOutcome,
    ObservedValidation,
    SkillUsageCapture,
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
    InvalidConnectorRunError,
    InvalidSourceDefinitionError,
    InvalidSourceObservationError,
    PowerContextError,
    RevisionConflictError,
    SourceConflictError,
    SourceDefinitionNotFoundError,
)
from powercontext.http import (
    AccessAction as TransportAccessAction,
)
from powercontext.http import (
    AccessArtifactIdentity as TransportAccessArtifactIdentity,
)
from powercontext.http import (
    AccessAuditEvent as TransportAccessAuditEvent,
)
from powercontext.http import (
    AccessAuditPage,
    AccessBindingPage,
    AccessCheckRequest,
    AccessCheckResponse,
    AccessMeResponse,
    AccessProviderCapabilities,
    AccessResourcePage,
    AccessRolePage,
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    ApproveArtifactCandidateRequest,
    ArtifactAccessResource,
    ArtifactCandidate,
    ArtifactCandidatePage,
    ArtifactCollectionItem,
    ArtifactCreated,
    ArtifactFamilyAccessCapability,
    ArtifactPage,
    ArtifactRevision,
    BaseArtifactFamily,
    CandidatePermissions,
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
    CreateAccessBindingRequest,
    CreateArtifactRequest,
    CreateRemoteSkillTargetRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    CreateWorkContractRequest,
    DownloadRemoteSkillPackageRequest,
    EnrollRemoteSkillTargetRequest,
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
    GetConnectorCheckpointRequest,
    GetExperienceRequest,
    GetHandoffReportRequest,
    GetMemoryEntryRequest,
    GetSkillPackageRequest,
    GetSkillRequest,
    GetStatsRequest,
    HandoffAcknowledgement,
    HandoffCurrentWorkRequest,
    HandoffReportResponse,
    HandoffSelection,
    HealthResponse,
    ImportExternalSkillRequest,
    ListAccessAuditRequest,
    ListAccessBindingsRequest,
    ListAccessResourcesRequest,
    ListAccessRolesRequest,
    ListArtifactCandidatesRequest,
    ListArtifactsRequest,
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
    MemoryEntryAccessSelector,
    MemoryMutationResponse,
    PrepareContextRequest,
    PreparedContext,
    PreparedWorkHandoff,
    PrepareHandoffRequest,
    ProposeExperienceRequest,
    ProposeSkillPackageRequest,
    ProposeSkillRequest,
    PublishArtifactRequest,
    PublishRemoteSkillRequest,
    ReadinessResponse,
    ReadinessStatus,
    ReconcileRemoteSkillsRequest,
    ReconcileRemoteSkillsResponse,
    RecordRemoteSkillReceiptRequest,
    RecordSkillUsageRequest,
    RecordTaskOutcomeRequest,
    RegisterSourceDefinitionRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    RemoteSkillAction,
    RemoteSkillPublication,
    RemoteSkillReceiptResponse,
    RemoteSkillTarget,
    RemoteSkillTargetCredential,
    RemoteSkillTargetEnrollment,
    RemoteSkillTargetStatus,
    RenameRemoteSkillTargetRequest,
    ReplaceAccessBindingRequest,
    ReplaceArtifactRequest,
    ResolveExternalSkillRequest,
    ResolveScopeBindingRequest,
    ResolveScopeSelectionRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
    RevokeAccessBindingRequest,
    RevokeRemoteSkillTargetRequest,
    ScanExternalSkillsRequest,
    ScanExternalSkillsResponse,
    ScopeAccessResource,
    ScopeBinding,
    ScopeBindingKey,
    ScopeDescriptor,
    ScopedStats,
    ScopePage,
    ScopeSelection,
    SearchMemoryRequest,
    SearchMemoryResponse,
    ServerAccessResource,
    SetDefaultScopeRequest,
    SetScopeBindingRequest,
    SkillArtifact,
    SkillGovernance,
    SkillPackageDownload,
    SkillPackageFile,
    SkillPackageManifest,
    SourceDefinitionManifest,
    SourceObservationReceipt,
    SourceRecord,
    SourceType,
    SubmitSourceObservationRequest,
    UnpublishRemoteSkillRequest,
    UpdateScopeRequest,
    UpdateSkillLifecycleRequest,
    WorkSourceReceipt,
)
from powercontext.http import (
    AccessBinding as TransportAccessBinding,
)
from powercontext.http import (
    AccessBindingReplacement as TransportAccessBindingReplacement,
)
from powercontext.http import (
    AccessBindingState as TransportAccessBindingState,
)
from powercontext.http import (
    AccessDecision as TransportAccessDecision,
)
from powercontext.http import (
    AccessGroup as TransportAccessGroup,
)
from powercontext.http import (
    AccessPrincipal as TransportAccessPrincipal,
)
from powercontext.http import (
    AccessResource as TransportAccessResource,
)
from powercontext.http import (
    AccessResourceType as TransportAccessResourceType,
)
from powercontext.http import (
    AccessRole as TransportAccessRole,
)
from powercontext.http import (
    AccessRoleCardinality as TransportAccessRoleCardinality,
)
from powercontext.http import (
    AccessRoleDescriptor as TransportAccessRoleDescriptor,
)
from powercontext.http import (
    AccessSubject as TransportAccessSubject,
)
from powercontext.http import (
    ArtifactPublication as TransportArtifactPublication,
)
from powercontext.http import (
    AssignableSubjectType as TransportAssignableSubjectType,
)
from powercontext.http import (
    HandoffActivation as TransportHandoffActivation,
)
from powercontext.http import HandoffContent as TransportHandoffContent
from powercontext.http import (
    HandoffDraft as TransportHandoffDraft,
)
from powercontext.http import (
    HandoffReceiptIdentity as TransportHandoffReceiptIdentity,
)
from powercontext.http import (
    HandoffResolution as TransportHandoffResolution,
)
from powercontext.http import (
    PreparedHandoff as TransportPreparedHandoff,
)
from powercontext.http._generated.models import (
    AccessControlMode as TransportAccessControlMode,
)
from powercontext.http._generated.models import (
    ArtifactFamily as TransportArtifactFamily,
)
from powercontext.http._generated.models import (
    ShareUnit as TransportShareUnit,
)
from powercontext.http._generated.models import Type4 as TransportMemoryEntrySelectorType
from powercontext.http._generated.operations import (
    ACKNOWLEDGE_HANDOFF,
    ACTIVATE_HANDOFF,
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    CHECK_ACCESS,
    CLEAR_SCOPE_BINDING,
    COMMIT_CONNECTOR_CHECKPOINT,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    CREATE_ACCESS_BINDING,
    CREATE_ARTIFACT,
    CREATE_REMOTE_SKILL_TARGET,
    CREATE_SCOPE,
    CREATE_SOURCE,
    CREATE_WORK_CONTRACT,
    DOWNLOAD_REMOTE_SKILL_PACKAGE,
    DOWNLOAD_SKILL_PACKAGE,
    ENROLL_REMOTE_SKILL_TARGET,
    FINALIZE_HANDOFF,
    FLUSH_MEMORY,
    GENERATE_EXPERIENCE,
    GENERATE_SKILL,
    GET_ACCESS_PRINCIPAL,
    GET_ARTIFACT,
    GET_ARTIFACT_CANDIDATE,
    GET_ARTIFACT_REVISION,
    GET_CAPABILITIES,
    GET_CONNECTOR_CHECKPOINT,
    GET_DEFAULT_SCOPE,
    GET_EXPERIENCE,
    GET_HANDOFF_REPORT,
    GET_LIVENESS,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    GET_SCOPE,
    GET_SKILL,
    GET_SKILL_PACKAGE_MANIFEST,
    GET_SOURCE,
    GET_STATS,
    HANDOFF_CURRENT_WORK,
    IMPORT_EXTERNAL_SKILL,
    LIST_ACCESS_AUDIT,
    LIST_ACCESS_BINDINGS,
    LIST_ACCESS_RESOURCES,
    LIST_ACCESS_ROLES,
    LIST_ARTIFACT_CANDIDATES,
    LIST_ARTIFACTS,
    LIST_EXTERNAL_SKILLS,
    LIST_MANAGED_SKILLS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    LIST_REMOTE_SKILL_TARGETS,
    LIST_SCOPES,
    OPENAPI_VERSION,
    PREPARE_CONTEXT,
    PREPARE_HANDOFF,
    PROPOSE_EXPERIENCE,
    PROPOSE_SKILL,
    PROPOSE_SKILL_PACKAGE,
    PUBLISH_ARTIFACT,
    PUBLISH_REMOTE_SKILL,
    RECONCILE_REMOTE_SKILLS,
    RECORD_REMOTE_SKILL_RECEIPT,
    RECORD_SKILL_USAGE,
    RECORD_TASK_OUTCOME,
    REGISTER_SOURCE_DEFINITION,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RENAME_REMOTE_SKILL_TARGET,
    REPLACE_ACCESS_BINDING,
    REPLACE_ARTIFACT,
    RESOLVE_EXTERNAL_SKILL,
    RESOLVE_SCOPE_BINDING,
    RESOLVE_SCOPE_SELECTION,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    REVOKE_ACCESS_BINDING,
    REVOKE_REMOTE_SKILL_TARGET,
    SCAN_EXTERNAL_SKILLS,
    SEARCH_MEMORY,
    SET_DEFAULT_SCOPE,
    SET_SCOPE_BINDING,
    SUBMIT_SOURCE_OBSERVATION,
    UNPUBLISH_REMOTE_SKILL,
    UPDATE_SCOPE,
    UPDATE_SKILL_LIFECYCLE,
    AccessRequirement,
    Operation,
)
from powercontext.http._generated.schema import OPENAPI_SCHEMA
from powercontext.server import mapping
from powercontext.server.authentication import AuthenticationProvider
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessAuditEvent,
    AccessBinding,
    AccessBindingNotFoundError,
    AccessBindingState,
    AccessConflictError,
    AccessControlError,
    AccessControlService,
    AccessDecision,
    AccessDeniedError,
    AccessIdentityRequiredError,
    AccessInvalidRequestError,
    AccessResourceType,
    AccessRole,
    AccessSubjectRef,
    AccessUnavailableError,
    AuditSearchRequest,
    AuthorizedResourceFilter,
    BindingSearchRequest,
    CreateBinding,
    GroupRef,
    MemoryEntrySelector,
    PrincipalRef,
    ReplaceBinding,
    ResourceRef,
    access_control_for_mode,
)
from powercontext.server.authz.models import (
    ROLE_ACTIONS,
    ROLE_CARDINALITIES,
    ROLE_RESOURCE_TYPES,
    ROLE_SUBJECT_TYPES,
    HandoffReceiptIdentity,
)
from powercontext.server.authz.profiles import ARTIFACT_FAMILY_PROFILES, artifact_family_profile
from powercontext.server.context import (
    bind_request_id,
    current_authentication,
    current_principal,
    current_request_id,
    is_internal_bridge,
    reset_request_id,
)
from powercontext.server.tracing import request_id_from_span
from powercontext.sources import ConnectorBinding as RuntimeConnectorBinding
from powercontext.sources import SourceDefinitionManifest as RuntimeSourceDefinitionManifest

if TYPE_CHECKING:
    from powercontext.server.metrics import ServerMetrics
    from powercontext.server.tracing import ServerTracing

_SCALAR_JS_URL = "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.66.1"
REQUEST_ID_HEADER = "X-PowerContext-Request-ID"
REPORT_SELECTION_DIGEST_HEADER = "X-PowerContext-Selection-Digest"
REPORT_DIGEST_HEADER = "X-PowerContext-Report-Digest"
MAX_HANDOFF_REPORT_BYTES = 10 * 1024 * 1024
logger = logging.getLogger(__name__)

CapabilityProvider = Callable[[], Capabilities]
ReadinessProbe = Callable[[], Awaitable[ReadinessResponse]]
_RequestT = TypeVar("_RequestT")
_ResponseT = TypeVar("_ResponseT")
_ScopePathId = Annotated[str, PathParameter(min_length=1, max_length=256, pattern=r".*\S.*")]


class _ScopedSourceApplication(Protocol):
    async def capture(self, value: CaptureSource, /) -> SourceReceipt: ...


class _SourceApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedSourceApplication: ...


class _ScopedRecordApplication(Protocol):
    async def create_source(
        self,
        source_type: str,
        content: JsonValue,
        /,
    ) -> RuntimeSourceRecord: ...

    async def get_source(self, source_type: str, source_id: str, /) -> RuntimeSourceRecord: ...

    async def create_artifact(
        self,
        family: str,
        write: RuntimeArtifactWrite,
        /,
    ) -> RuntimeArtifactCreated: ...

    async def get_artifact(self, family: str, artifact_id: str, /) -> RuntimeArtifactRecord: ...

    async def get_artifact_revision(
        self,
        family: str,
        artifact_id: str,
        revision: int,
        /,
    ) -> RuntimeArtifactRecord: ...

    async def logical_artifacts(self) -> tuple[LogicalArtifactRecord, ...]: ...

    async def query_artifacts(
        self,
        family: str,
        /,
        *,
        limit: int,
        cursor: str | None,
    ) -> RuntimeArtifactRecordPage: ...

    async def replace_artifact(
        self,
        family: str,
        artifact_id: str,
        expected_etag: str,
        write: RuntimeArtifactWrite,
        /,
    ) -> RuntimeArtifactRecord: ...


class _RecordApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedRecordApplication: ...


class _RemoteIngestionApplication(Protocol):
    async def register(self, manifest: RuntimeSourceDefinitionManifest, /) -> RuntimeSourceDefinitionManifest: ...

    async def checkpoint(self, binding: RuntimeConnectorBinding, /) -> RuntimeConnectorCheckpointState: ...

    async def submit(self, request: RuntimeSubmitSourceObservation, /) -> SourceReceipt: ...

    async def commit(self, request: RuntimeCommitConnectorCheckpoint, /) -> RuntimeConnectorCheckpointState: ...


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

    async def search(self, query: str, limit: int, /) -> tuple[SkillSearchHit, ...]: ...

    async def list(
        self, *, include_deprecated: bool = False, limit: int = 100
    ) -> tuple[tuple[Skill, ArtifactGovernance], ...]: ...

    async def package(self, artifact: ArtifactRef, /) -> SkillPackageSnapshot: ...

    async def package_snapshot(self, package: SkillPackageRef, /) -> SkillPackageSnapshot: ...

    async def upload_package(
        self,
        archive_bytes: bytes,
        reason: str | None,
        target: ArtifactRef | None,
        /,
    ) -> SkillCandidate: ...

    async def record_usage(self, observation: SkillUsageCapture, /) -> SourceReceipt: ...

    async def governance(self, artifact_id: str, /) -> ArtifactGovernance: ...

    async def update_lifecycle(
        self,
        artifact_id: str,
        expected_generation: int,
        lifecycle_state: ArtifactLifecycleState,
        replacement_artifact_id: str | None,
        /,
    ) -> ArtifactGovernance: ...

    async def inspect_publication(
        self, artifact: ArtifactRef, target: AgentSkillTarget, /
    ) -> ManagedSkillPublicationStatus: ...

    async def publish(
        self,
        artifact: ArtifactRef,
        target: AgentSkillTarget,
        /,
        *,
        allow_deprecated: bool = False,
    ) -> ManagedSkillPublicationStatus: ...

    async def unpublish(self, artifact: ArtifactRef, target: AgentSkillTarget, /) -> ManagedSkillPublicationStatus: ...


class _SkillApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> _ScopedSkillApplication: ...


class _RemoteSkillApplication(Protocol):
    async def list_targets(
        self,
        scope_id: str,
        /,
        *,
        target_id: str | None = None,
        limit: int = 100,
    ) -> tuple[DomainRemoteSkillTargetStatus, ...]: ...

    async def create_target(
        self,
        scope_id: str,
        agent_kind: AgentKind,
        display_name: str,
        /,
    ) -> DomainRemoteTargetEnrollment: ...

    async def enroll(
        self,
        enrollment_code: str,
        installation_id: str,
        receiver_version: str,
        environment_fingerprint: str | None,
        machine_hostname: str | None,
        workspace_name: str | None,
        /,
    ) -> DomainRemoteTargetCredential: ...

    async def rename_target(
        self,
        scope_id: str,
        target_id: str,
        expected_generation: int,
        display_name: str,
        /,
    ) -> RemoteAgentSkillTarget: ...

    async def revoke_target(
        self, scope_id: str, target_id: str, expected_generation: int, /
    ) -> RemoteAgentSkillTarget: ...

    async def publish(
        self,
        scope_id: str,
        target_id: str,
        artifact: ArtifactRef,
        expected_generation: int | None,
        /,
        *,
        allow_deprecated: bool = False,
    ) -> SkillPublication: ...

    async def unpublish(
        self, scope_id: str, target_id: str, artifact_id: str, expected_generation: int, /
    ) -> SkillPublication: ...

    async def reconcile(
        self,
        credential: str,
        observations: tuple[DomainRemoteSkillObservation, ...],
        receiver_version: str,
        environment_fingerprint: str | None,
        /,
    ) -> DomainRemoteSkillReconcileResult: ...

    async def download(
        self,
        credential: str,
        generation: int,
        artifact: ArtifactRef,
        package: SkillPackageRef,
        /,
    ) -> SkillPackageSnapshot: ...

    async def receipt(
        self, credential: str, receipt: DomainRemoteSkillReceipt, /
    ) -> DomainRemoteSkillReceiptResult: ...


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

    async def continue_from(
        self,
        handoff: PreparedHandoff | ArtifactRef,
        /,
        *,
        evidence_authorizer: Callable[[HandoffCitation], Awaitable[bool]] | None = None,
    ) -> HandoffResolution: ...

    async def continue_latest(
        self,
        *,
        evidence_authorizer: Callable[[HandoffCitation], Awaitable[bool]] | None = None,
    ) -> HandoffResolution: ...

    async def revision(self, reference: ArtifactRef, /) -> Handoff: ...

    async def revisions(self) -> tuple[Handoff, ...]: ...


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

    async def overview(
        self,
        selection: DomainScopeSelection,
        *,
        period: RuntimeStatisticsPeriod,
    ) -> RuntimeStatistics: ...


class ServerApplication(Protocol):
    scopes: ScopeApplication | None
    publications: ArtifactPublicationApplication | None
    sources: _SourceApplication
    records: _RecordApplication
    ingestion: _RemoteIngestionApplication
    context: _ContextApplication
    experience: _ExperienceApplication
    external_skills: _ExternalSkillApplication
    handoff: _HandoffApplication
    work: _WorkApplication
    memory: _MemoryApplication
    review: _ReviewApplication
    skill: _SkillApplication
    remote_skills: _RemoteSkillApplication
    statistics: _StatisticsApplication
    handoff_report: HandoffReportApplication | None


class _RuntimeNotReadyError(RuntimeError):
    """Raised when an application operation is called without a Runtime binding."""


class _PreconditionRequiredError(RuntimeError):
    """Raised when an Artifact mutation omits If-Match."""


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
    access_control: AccessControlService | None = None,
    access_mode: Literal["disabled", "enforced"] | None = None,
    authentication_provider: AuthenticationProvider | None = None,
    allow_insecure_remote_http: bool = False,
) -> FastAPI:
    """Build the HTTP adapter around an optional Runtime application binding."""

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        middleware=list(middleware),
    )
    app.openapi_version = OPENAPI_VERSION
    app.state.application = application
    app.state.capability_provider = capability_provider
    app.state.readiness_probe = readiness_probe
    app.state.access_control = access_control
    app.state.authentication_provider = authentication_provider
    app.state.access_mode = (
        ("disabled" if access_control is None else access_control.mode) if access_mode is None else access_mode
    )
    app.state.metrics = metrics
    app.state.tracing = tracing
    app.state.allow_insecure_remote_http = allow_insecure_remote_http
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
    @app.exception_handler(PydanticValidationError)
    async def invalid_request(
        request: Request,
        error: RequestValidationError | PydanticValidationError,
    ) -> JSONResponse:
        errors = _validation_error_details(error)
        scoped_resource_syntax_error = request.url.path.startswith("/v1/scopes/") and any(
            isinstance(item, dict)
            and isinstance(item.get("loc"), (list, tuple))
            and item["loc"]
            and item["loc"][0] == "query"
            for item in errors
        )
        return _error_response(
            status.HTTP_400_BAD_REQUEST if scoped_resource_syntax_error else status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_request",
            message="The request violates the API contract.",
            details={"errors": errors},
        )

    @app.exception_handler(_RuntimeNotReadyError)
    @app.exception_handler(_PreconditionRequiredError)
    @app.exception_handler(BaseAccessError)
    @app.exception_handler(SourceNotEligibleError)
    @app.exception_handler(PowerContextError)
    @app.exception_handler(PersistenceError)
    async def application_error(request: Request, error: Exception) -> JSONResponse:
        response_status, code, message, details = _map_error(error)
        response = _error_response(response_status, code=code, message=message, details=details)
        if isinstance(error, RemoteTargetAuthenticationError):
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

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
    _add_route(app, LIST_SCOPES, list_scopes)
    _add_route(app, CREATE_SCOPE, create_scope)
    _add_route(app, GET_DEFAULT_SCOPE, get_default_scope)
    _add_route(app, SET_DEFAULT_SCOPE, set_default_scope)
    _add_route(app, GET_SCOPE, get_scope)
    _add_route(app, UPDATE_SCOPE, update_scope)
    _add_route(app, RESOLVE_SCOPE_SELECTION, resolve_scope_selection)
    _add_route(app, RESOLVE_SCOPE_BINDING, resolve_scope_binding)
    _add_route(app, SET_SCOPE_BINDING, set_scope_binding)
    _add_route(app, CLEAR_SCOPE_BINDING, clear_scope_binding)
    _add_route(app, PUBLISH_ARTIFACT, publish_artifact)
    _add_route(app, GET_STATS, get_stats)
    _add_route(app, GET_ACCESS_PRINCIPAL, get_access_principal)
    _add_route(app, CHECK_ACCESS, check_access)
    _add_route(app, LIST_ACCESS_RESOURCES, list_access_resources)
    _add_route(app, LIST_ACCESS_ROLES, list_access_roles)
    _add_route(app, LIST_ACCESS_BINDINGS, list_access_bindings)
    _add_route(app, CREATE_ACCESS_BINDING, create_access_binding)
    _add_route(app, REVOKE_ACCESS_BINDING, revoke_access_binding)
    _add_route(app, REPLACE_ACCESS_BINDING, replace_access_binding)
    _add_route(app, LIST_ACCESS_AUDIT, list_access_audit)
    if handoff_report_enabled:
        _add_route(app, GET_HANDOFF_REPORT, get_handoff_report)
    _add_route(app, CREATE_SOURCE, create_source)
    _add_route(app, GET_SOURCE, get_source)
    _add_route(app, CREATE_ARTIFACT, create_artifact)
    _add_route(app, GET_ARTIFACT_REVISION, get_artifact_revision)
    _add_route(app, GET_ARTIFACT, get_artifact)
    _add_route(app, LIST_ARTIFACTS, list_artifacts)
    _add_route(app, REPLACE_ARTIFACT, replace_artifact)
    _add_route(app, CAPTURE_CONTENT_SOURCE, capture_content_source)
    _add_route(app, REGISTER_SOURCE_DEFINITION, register_source_definition)
    _add_route(app, GET_CONNECTOR_CHECKPOINT, get_connector_checkpoint)
    _add_route(app, SUBMIT_SOURCE_OBSERVATION, submit_source_observation)
    _add_route(app, COMMIT_CONNECTOR_CHECKPOINT, commit_connector_checkpoint)
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
    _add_route(app, LIST_MANAGED_SKILLS, list_managed_skills)
    _add_route(app, UPDATE_SKILL_LIFECYCLE, update_skill_lifecycle)
    _add_route(app, GET_SKILL_PACKAGE_MANIFEST, get_skill_package_manifest)
    _add_route(app, DOWNLOAD_SKILL_PACKAGE, download_skill_package)
    _add_route(app, PROPOSE_SKILL_PACKAGE, propose_skill_package)
    _add_route(app, RECORD_SKILL_USAGE, record_skill_usage)
    _add_route(app, LIST_REMOTE_SKILL_TARGETS, list_remote_skill_targets)
    _add_route(app, CREATE_REMOTE_SKILL_TARGET, create_remote_skill_target)
    _add_route(app, ENROLL_REMOTE_SKILL_TARGET, enroll_remote_skill_target)
    _add_route(app, RENAME_REMOTE_SKILL_TARGET, rename_remote_skill_target)
    _add_route(app, REVOKE_REMOTE_SKILL_TARGET, revoke_remote_skill_target)
    _add_route(app, PUBLISH_REMOTE_SKILL, publish_remote_skill)
    _add_route(app, UNPUBLISH_REMOTE_SKILL, unpublish_remote_skill)
    _add_route(app, RECONCILE_REMOTE_SKILLS, reconcile_remote_skills)
    _add_route(app, DOWNLOAD_REMOTE_SKILL_PACKAGE, download_remote_skill_package)
    _add_route(app, RECORD_REMOTE_SKILL_RECEIPT, record_remote_skill_receipt)
    _add_route(app, SCAN_EXTERNAL_SKILLS, scan_external_skills)
    _add_route(app, LIST_EXTERNAL_SKILLS, list_external_skills)
    _add_route(app, RESOLVE_EXTERNAL_SKILL, resolve_external_skill)
    _add_route(app, IMPORT_EXTERNAL_SKILL, import_external_skill)
    _add_route(app, LIST_ARTIFACT_CANDIDATES, list_artifact_candidates)
    _add_route(app, GET_ARTIFACT_CANDIDATE, get_artifact_candidate)
    _add_route(app, APPROVE_ARTIFACT_CANDIDATE, approve_artifact_candidate)
    _add_route(app, REJECT_ARTIFACT_CANDIDATE, reject_artifact_candidate)
    _add_route(app, REVISE_ARTIFACT_CANDIDATE, revise_artifact_candidate)
    app.add_api_route(
        "/docs",
        scalar_api_reference,
        include_in_schema=False,
        methods=["GET"],
    )

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


async def scalar_api_reference(request: Request) -> Response:
    """Render the runtime OpenAPI contract with Scalar."""

    return get_scalar_api_reference(
        content=request.app.openapi(),
        title=f"{API_TITLE} Reference",
        scalar_js_url=_SCALAR_JS_URL,
        scalar_favicon_url="data:,",
        with_default_fonts=False,
        show_developer_tools="never",
        telemetry=False,
        agent=AgentScalarConfig(disabled=True),
    )


async def get_liveness() -> HealthResponse:
    return HealthResponse(status="ok")


async def get_readiness(request: Request) -> JSONResponse:
    readiness_probe: ReadinessProbe | None = request.app.state.readiness_probe
    readiness = (
        await readiness_probe() if readiness_probe is not None else _runtime_readiness(request.app.state.application)
    )
    checks = {**readiness.checks, **await _access_readiness_checks(request)}
    response_status = status.HTTP_200_OK
    readiness_status = readiness.status
    if readiness.status is ReadinessStatus.NOT_READY or any(
        checks[name] == "not_ready" for name in ("authentication_provider", "access_provider")
    ):
        readiness_status = ReadinessStatus.NOT_READY
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE
    response = ReadinessResponse(status=readiness_status, checks=checks)
    return JSONResponse(content=response.model_dump(mode="json"), status_code=response_status)


async def _access_readiness_checks(request: Request) -> dict[str, str]:
    mode: str = request.app.state.access_mode
    access: AccessControlService | None = request.app.state.access_control
    provider = (
        ("ready" if await access.readiness() else "not_ready")
        if access is not None
        else ("not_ready" if mode == "enforced" else "disabled")
    )
    authentication: AuthenticationProvider | None = request.app.state.authentication_provider
    if mode == "disabled":
        authentication_status = "disabled"
    elif authentication is None:
        authentication_status = "not_ready"
    else:
        try:
            authentication_status = "ready" if (await authentication.readiness()).ready else "not_ready"
        except Exception:
            authentication_status = "not_ready"
    family_capabilities = ",".join(
        f"{profile.family}:{'enabled' if profile.enabled else 'disabled'}"
        for profile in sorted(ARTIFACT_FAMILY_PROFILES.values(), key=lambda item: item.family)
    )
    return {
        "access_mode": mode,
        "authentication_provider": authentication_status,
        "access_provider": provider,
        "access_resource_kinds": ",".join(resource_type.value for resource_type in AccessResourceType),
        "access_artifact_families": family_capabilities,
    }


async def get_capabilities(request: Request) -> Capabilities:
    capability_provider: CapabilityProvider | None = request.app.state.capability_provider
    if capability_provider is not None:
        return capability_provider()
    return request.app.state.capabilities


async def get_access_principal(request: Request) -> AccessMeResponse:
    access = _require_access_control(request)
    provider = access.provider_capabilities
    return AccessMeResponse(
        principal=_access_principal_response(_require_principal()),
        mode=TransportAccessControlMode(access.mode),
        resource_kinds=[TransportAccessResourceType(resource_type.value) for resource_type in AccessResourceType],
        provider_capabilities=AccessProviderCapabilities(
            safe_resource_filtering=provider.safe_resource_filtering,
            multi_requirement_check=provider.multi_requirement_check,
            relationship_management=provider.relationship_management,
            group_subjects=provider.group_subjects,
            multi_principal=provider.multi_principal,
            max_direct_resource_keys=provider.max_direct_resource_keys,
        ),
        artifact_families=[
            ArtifactFamilyAccessCapability(
                family=profile.family,
                enabled=profile.enabled,
                share_unit=TransportShareUnit(profile.share_unit),
                actions=[TransportAccessAction(action.value) for action in sorted(profile.actions, key=str)],
                grantable_roles=[TransportAccessRole(role.value) for role in sorted(profile.grantable_roles, key=str)],
            )
            for profile in ARTIFACT_FAMILY_PROFILES.values()
        ],
    )


async def check_access(payload: AccessCheckRequest, request: Request) -> AccessCheckResponse:
    access = _require_access_control(request)
    requirements = tuple(
        (AccessAction(requirement.action.value), _access_resource(requirement.resource))
        for requirement in payload.requirements
    )
    decisions = await access.check_batch(
        _require_principal(),
        requirements,
        context=_access_audit_context(CHECK_ACCESS.operation_id),
    )
    allowed = (
        all(decision.allowed for decision in decisions)
        if payload.match.value == "all"
        else any(decision.allowed for decision in decisions)
    )
    return AccessCheckResponse(
        allowed=allowed,
        decisions=[_access_decision_response(decision) for decision in decisions],
    )


async def list_access_resources(payload: ListAccessResourcesRequest, request: Request) -> AccessResourcePage:
    access = _require_access_control(request)
    resource_type = AccessResourceType(payload.resource_type.value)
    page = await access.list_resources(
        _require_principal(),
        action=AccessAction(payload.action.value),
        resource_type=resource_type,
        family=payload.family,
        cursor=payload.cursor,
        limit=payload.limit,
        context=_access_audit_context(LIST_ACCESS_RESOURCES.operation_id),
        query_resources=lambda authorized: _query_authorized_resources(
            request,
            authorized,
            resource_type=resource_type,
            family=payload.family,
        ),
    )
    return AccessResourcePage(
        items=[_access_resource_response(resource) for resource in page.items],
        total=page.total,
        next_cursor=page.next_cursor,
    )


async def _query_authorized_resources(
    request: Request,
    authorized: AuthorizedResourceFilter,
    *,
    resource_type: AccessResourceType,
    family: str | None,
) -> tuple[ResourceRef, ...]:
    resources = {resource.key: resource for resource in authorized.exact_resources}
    if not authorized.parent_constraints:
        return tuple(resources.values())
    if resource_type not in {AccessResourceType.SCOPE, AccessResourceType.ARTIFACT}:
        raise AccessUnavailableError("safe_resource_filtering_unavailable")

    application = _require_application(request)
    access = _require_access_control(request)
    scope_ids = await _authorized_scope_ids(request, authorized.parent_constraints)
    families = (family,) if family is not None else ("handoff", "memory", "experience", "skill")
    for scope_id in scope_ids:
        if resource_type is AccessResourceType.SCOPE:
            resource = ResourceRef.scope(scope_id)
            resources[resource.key] = resource
        else:
            for selected_family in families:
                discovered = await _discover_scope_artifact_resources(application, scope_id, selected_family)
                for resource in discovered:
                    if await access.artifact_owner(resource) is not None:
                        resources[resource.key] = resource
                    if len(resources) > authorized.max_direct_resource_keys:
                        raise AccessUnavailableError("resource_filter_limit_exceeded")
        if len(resources) > authorized.max_direct_resource_keys:
            raise AccessUnavailableError("resource_filter_limit_exceeded")
    return tuple(resources.values())


async def _authorized_scope_ids(request: Request, parents: Sequence[ResourceRef]) -> tuple[str, ...]:
    access = _require_access_control(request)
    scope_ids: set[str] = set()
    for parent in parents:
        if parent.type is AccessResourceType.SERVER and parent.deployment_id == access.deployment_id:
            scope_ids.update(scope.scope_id for scope in await _require_scope_application(request).list())
        elif parent.type is AccessResourceType.SCOPE and parent.scope_id is not None:
            scope_ids.add(parent.scope_id)
        else:
            raise AccessUnavailableError("safe_resource_filtering_unavailable")
    return tuple(sorted(scope_ids))


async def _discover_scope_artifact_resources(
    application: ServerApplication,
    scope_id: str,
    family: str,
) -> tuple[ResourceRef, ...]:
    if family == "handoff":
        if not await application.handoff.for_scope(scope_id).revisions():
            return ()
        return (ResourceRef.artifact(scope_id, family="handoff", artifact_id="handoff"),)
    if family == "memory":
        entries = await application.memory.for_scope(scope_id).list(include_inactive=True)
        return tuple(
            ResourceRef.artifact(
                scope_id,
                family="memory",
                artifact_id=entry.citation.memory_ref.artifact_id,
                selector=MemoryEntrySelector(entry_id=entry.citation.entry_id),
            )
            for entry in entries.entries
        )
    if family in {"experience", "skill"}:
        return await _committed_artifact_resources(
            application,
            scope_id,
            cast(Literal["experience", "skill"], family),
        )
    raise AccessInvalidRequestError("artifact-family")


async def _committed_artifact_resources(
    application: ServerApplication,
    scope_id: str,
    family: Literal["experience", "skill"],
) -> tuple[ResourceRef, ...]:
    resources: list[ResourceRef] = []
    cursor: str | None = None
    while True:
        page = await application.records.for_scope(scope_id).query_artifacts(
            family,
            cursor=cursor,
            limit=100,
        )
        resources.extend(
            ResourceRef.artifact(scope_id, family=artifact.family, artifact_id=artifact.artifact_id)
            for artifact in page.items
        )
        cursor = page.next_cursor
        if cursor is None:
            return tuple(resources)


async def list_access_roles(payload: ListAccessRolesRequest, request: Request) -> AccessRolePage:
    _require_access_control(request)
    resource_type = None if payload.resource_type is None else AccessResourceType(payload.resource_type.value)
    selected_profile = None
    if payload.family is not None:
        if resource_type not in {None, AccessResourceType.ARTIFACT}:
            raise AccessInvalidRequestError("action-resource")
        selected_profile = ARTIFACT_FAMILY_PROFILES.get(payload.family)
        if selected_profile is None:
            raise AccessInvalidRequestError("artifact-family")
        if not selected_profile.enabled:
            raise AccessInvalidRequestError("artifact-family-disabled")
    roles = [
        role
        for role in AccessRole
        if (resource_type is None or ROLE_RESOURCE_TYPES[role] is resource_type)
        and (
            ROLE_RESOURCE_TYPES[role] is not AccessResourceType.ARTIFACT
            or role is AccessRole.ARTIFACT_OWNER
            or (
                role in selected_profile.grantable_roles
                if selected_profile is not None
                else any(
                    profile.enabled and role in profile.grantable_roles for profile in ARTIFACT_FAMILY_PROFILES.values()
                )
            )
        )
    ]
    return AccessRolePage(
        items=[
            TransportAccessRoleDescriptor(
                role=TransportAccessRole(role.value),
                resource_type=TransportAccessResourceType(ROLE_RESOURCE_TYPES[role].value),
                cardinality=TransportAccessRoleCardinality(ROLE_CARDINALITIES[role].value),
                actions=[
                    TransportAccessAction(action.value)
                    for action in sorted(ROLE_ACTIONS[role], key=str)
                    if action is not AccessAction.ACCESS_SELF
                ],
                artifact_families=[
                    TransportArtifactFamily(root=profile.family)
                    for profile in ARTIFACT_FAMILY_PROFILES.values()
                    if profile.enabled
                    and (selected_profile is None or profile.family == selected_profile.family)
                    and (role is AccessRole.ARTIFACT_OWNER or role in profile.grantable_roles)
                ],
                assignable_subject_types=[
                    TransportAssignableSubjectType(subject_type)
                    for subject_type in sorted(ROLE_SUBJECT_TYPES[role])
                    if role is not AccessRole.ARTIFACT_OWNER
                ],
                system_managed=role is AccessRole.ARTIFACT_OWNER,
            )
            for role in roles
        ]
    )


async def list_access_bindings(payload: ListAccessBindingsRequest, request: Request) -> AccessBindingPage:
    access = _require_access_control(request)
    page = await access.list_bindings(
        _require_principal(),
        BindingSearchRequest(
            management_resource=_access_resource(payload.management_resource),
            subject=None if payload.subject is None else _access_subject(payload.subject),
            role=None if payload.role is None else AccessRole(payload.role.value),
            state=None if payload.state is None else AccessBindingState(payload.state.value),
            cursor=payload.cursor,
            limit=payload.limit,
        ),
        context=_access_audit_context(LIST_ACCESS_BINDINGS.operation_id),
    )
    return AccessBindingPage(
        items=[_access_binding_response(binding) for binding in page.items],
        next_cursor=page.next_cursor,
    )


async def create_access_binding(payload: CreateAccessBindingRequest, request: Request) -> TransportAccessBinding:
    access = _require_access_control(request)
    binding = await access.create_binding(
        _require_principal(),
        CreateBinding(
            subject=_access_subject(payload.subject),
            resource=_access_resource(payload.resource),
            role=AccessRole(payload.role.value),
            idempotency_key=payload.idempotency_key,
            reason=payload.reason,
            expires_at=payload.expires_at,
        ),
        context=_access_audit_context(CREATE_ACCESS_BINDING.operation_id),
        validate_resource=lambda resource: _validate_shareable_resource(request.app.state.application, resource),
    )
    return _access_binding_response(binding)


async def revoke_access_binding(payload: RevokeAccessBindingRequest, request: Request) -> TransportAccessBinding:
    access = _require_access_control(request)
    binding = await access.revoke_binding(
        _require_principal(),
        payload.binding_id,
        expected_version=payload.expected_version,
        idempotency_key=payload.idempotency_key,
        context=_access_audit_context(REVOKE_ACCESS_BINDING.operation_id),
    )
    return _access_binding_response(binding)


async def replace_access_binding(
    payload: ReplaceAccessBindingRequest,
    request: Request,
) -> TransportAccessBindingReplacement:
    access = _require_access_control(request)
    result = await access.replace_binding(
        _require_principal(),
        ReplaceBinding(
            binding_id=payload.binding_id,
            expected_version=payload.expected_version,
            subject=_access_subject(payload.replacement.subject),
            idempotency_key=payload.idempotency_key,
            reason=payload.replacement.reason,
            expires_at=payload.replacement.expires_at,
        ),
        context=_access_audit_context(REPLACE_ACCESS_BINDING.operation_id),
    )
    return TransportAccessBindingReplacement(
        previous=_access_binding_response(result.previous),
        current=_access_binding_response(result.current),
    )


async def list_access_audit(payload: ListAccessAuditRequest, request: Request) -> AccessAuditPage:
    access = _require_access_control(request)
    resource = (
        ResourceRef.server(payload.resource.deployment_id)
        if isinstance(payload.resource, ServerAccessResource)
        else ResourceRef.scope(payload.resource.scope_id)
    )
    page = await access.list_audit(
        _require_principal(),
        AuditSearchRequest(
            resource=resource,
            action=None if payload.action is None else AccessAction(payload.action.value),
            subject=None if payload.subject is None else _access_subject(payload.subject),
            allowed=None if payload.result is None else payload.result.value == "allowed",
            occurred_after=None if payload.time_range is None else payload.time_range.start,
            occurred_before=None if payload.time_range is None else payload.time_range.end,
            cursor=payload.cursor,
            limit=payload.limit,
        ),
        context=_access_audit_context(LIST_ACCESS_AUDIT.operation_id),
    )
    return AccessAuditPage(
        items=[_access_audit_response(event) for event in page.items],
        next_cursor=page.next_cursor,
    )


async def list_scopes(
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopePage:
    return ScopePage(items=[_scope_descriptor_response(scope) for scope in await scopes.list()])


async def create_scope(
    request: CreateScopeRequest,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopeDescriptor:
    created = await scopes.create(
        ScopeDraft(
            title=request.title,
            summary=request.summary,
            parent_scope_id=request.parent_scope_id,
            context_references=tuple(reference.root for reference in request.context_references),
            external_references=tuple(
                DomainScopeExternalReference(kind=reference.kind, value=reference.value)
                for reference in request.external_references
            ),
            idempotency_key=request.idempotency_key,
        )
    )
    return _scope_descriptor_response(created)


async def get_scope(
    scope_id: _ScopePathId,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopeDescriptor:
    return _scope_descriptor_response(await scopes.get(scope_id))


async def update_scope(
    scope_id: _ScopePathId,
    request: UpdateScopeRequest,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopeDescriptor:
    updated = await scopes.update(
        scope_id,
        ScopeMutation(
            expected_version=request.expected_version,
            title=request.title,
            summary=request.summary,
            parent_scope_id=request.parent_scope_id,
            context_references=tuple(reference.root for reference in request.context_references),
            external_references=tuple(
                DomainScopeExternalReference(kind=reference.kind, value=reference.value)
                for reference in request.external_references
            ),
        ),
    )
    return _scope_descriptor_response(updated)


async def get_default_scope(
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopeDescriptor:
    current = await scopes.default_scope()
    if current is None:
        raise ScopeBindingNotFoundError
    return _scope_descriptor_response(current)


async def set_default_scope(
    request: SetDefaultScopeRequest,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopeDescriptor:
    return _scope_descriptor_response(await scopes.set_default(request.scope_id))


async def resolve_scope_selection(
    request: ResolveScopeSelectionRequest,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopePage:
    selection = _domain_scope_selection(request.selection)
    return ScopePage(items=[_scope_descriptor_response(scope) for scope in await scopes.resolve_selection(selection)])


async def resolve_scope_binding(
    request: ResolveScopeBindingRequest,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopeDescriptor:
    resolved = await scopes.resolve_binding(
        explicit_scope_id=request.explicit_scope_id,
        binding_keys=tuple(_domain_binding_key(key) for key in request.binding_keys),
    )
    return _scope_descriptor_response(resolved)


async def set_scope_binding(
    request: SetScopeBindingRequest,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ScopeBinding:
    binding = await scopes.bind(_domain_binding_key(request.root.key), request.root.scope_id)
    return ScopeBinding(
        key=_transport_binding_key(binding.key),
        scope_id=binding.scope_id,
    )


async def clear_scope_binding(
    request: ClearScopeBindingRequest,
    scopes: Annotated[ScopeApplication, Depends(_require_scope_application)],
) -> ClearScopeBindingResponse:
    return ClearScopeBindingResponse(cleared=await scopes.clear_binding(_domain_binding_key(request.key)))


async def publish_artifact(
    request: PublishArtifactRequest,
    publications: Annotated[ArtifactPublicationApplication, Depends(_require_publication_application)],
    http_request: Request,
) -> TransportArtifactPublication:
    result = await publications.publish(
        DomainArtifactPublicationRequest(
            source=ArtifactAddress(
                scope_id=request.source.scope_id,
                artifact=ArtifactRef.model_validate(request.source.artifact.model_dump(mode="json")),
            ),
            target_scope_id=request.target_scope_id,
            idempotency_key=request.idempotency_key,
        )
    )
    await _establish_created_owner(
        http_request,
        ResourceRef.artifact(
            result.target.scope_id,
            family=result.target.artifact.family,
            artifact_id=result.target.artifact.artifact_id,
        ),
        idempotency_key=f"artifact-publication-owner:{result.target.artifact.artifact_id}",
        operation=PUBLISH_ARTIFACT.operation_id,
    )
    return TransportArtifactPublication.model_validate(result.model_dump(mode="json"))


async def get_stats(
    request: GetStatsRequest,
    response: Response,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ScopedStats:
    response.headers["Cache-Control"] = "no-store"
    await _require_selection_content_ready(http_request, request.selection)
    result = await application.statistics.overview(
        _domain_scope_selection(request.selection), period=RuntimeStatisticsPeriod(request.period.value)
    )
    return mapping.statistics_response(result)


async def get_handoff_report(
    request: GetHandoffReportRequest,
    response: Response,
    report: Annotated[HandoffReportApplication, Depends(_require_handoff_report_application)],
    http_request: Request,
) -> HandoffReportResponse | Response:
    await _require_selection_content_ready(http_request, request.selection)
    result = await report.get_report(_domain_scope_selection(request.selection))
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


async def create_source(
    scope_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r".*\S.*")],
    request: CreateSourceRequest,
    response: Response,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SourceRecord:
    result = await application.records.for_scope(scope_id).create_source(
        request.source_type.value,
        request.content,
    )
    response.headers["Location"] = _source_location(result)
    return _source_record_response(result)


async def get_source(
    scope_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r".*\S.*")],
    source_type: Annotated[Literal["content"], Path()],
    source_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r"^[\x21-\x7E]+$")],
    http_request: Request,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SourceRecord:
    result = await application.records.for_scope(scope_id).get_source(source_type, source_id)
    response = _source_record_response(result)
    if http_request.app.state.access_mode == "enforced" and _is_handoff_receipt_content(result.content):
        identity = await _require_access_control(http_request).receipt_identity(scope_id, source_id)
        if identity is None:
            raise AccessUnavailableError("receipt_identity_pending")
        response.receipt_identity = _receipt_identity_response(identity)
    return response


async def create_artifact(
    scope_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r".*\S.*")],
    request: CreateArtifactRequest,
    response: Response,
    http_request: Request,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactCreated:
    result = await application.records.for_scope(scope_id).create_artifact(
        request.root.family,
        _artifact_write(request),
    )
    await _establish_base_artifact_owners(
        http_request,
        application,
        result,
    )
    response.headers["Location"] = _artifact_location(result)
    response.headers["ETag"] = _artifact_etag(result.revision)
    return _artifact_created_response(result)


def _list_artifacts_query(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
) -> ListArtifactsRequest:
    return ListArtifactsRequest(limit=limit, cursor=cursor)


async def list_artifacts(
    scope_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r".*\S.*")],
    family: Annotated[BaseArtifactFamily, Path()],
    request: Annotated[ListArtifactsRequest, Depends(_list_artifacts_query)],
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactPage:
    result = await application.records.for_scope(scope_id).query_artifacts(
        family.value,
        limit=request.limit,
        cursor=request.cursor,
    )
    return ArtifactPage(
        items=[_artifact_collection_item_response(item) for item in result.items],
        next_cursor=result.next_cursor,
    )


async def get_artifact(
    scope_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r".*\S.*")],
    family: Annotated[BaseArtifactFamily, Path()],
    artifact_id: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[\x21-\x7E]+$")],
    response: Response,
    application: Annotated[ServerApplication, Depends(_require_application)],
    if_none_match: Annotated[
        str | None,
        Header(alias="If-None-Match", min_length=1),
    ] = None,
) -> ArtifactRevision | Response:
    result = await application.records.for_scope(scope_id).get_artifact(family.value, artifact_id)
    etag = _artifact_etag(result.revision)
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return _artifact_revision_response(result)


async def replace_artifact(
    scope_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r".*\S.*")],
    family: Annotated[BaseArtifactFamily, Path()],
    artifact_id: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[\x21-\x7E]+$")],
    request: ReplaceArtifactRequest,
    response: Response,
    http_request: Request,
    application: Annotated[ServerApplication, Depends(_require_application)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ArtifactRevision:
    expected_etag = _require_artifact_etag(if_match)
    previous_memory_entries: frozenset[str] = frozenset()
    if family is BaseArtifactFamily.MEMORY:
        current = await application.records.for_scope(scope_id).get_artifact(family.value, artifact_id)
        previous_memory_entries = _memory_manifest_entry_ids(current)
    result = await application.records.for_scope(scope_id).replace_artifact(
        family.value,
        artifact_id,
        expected_etag,
        _artifact_write(request),
    )
    await _establish_new_memory_entry_owners(
        http_request,
        result,
        previous_entry_ids=previous_memory_entries,
        operation=REPLACE_ARTIFACT.operation_id,
    )
    response.headers["ETag"] = _artifact_etag(result.revision)
    return _artifact_revision_response(result)


async def get_artifact_revision(
    scope_id: Annotated[str, Path(min_length=1, max_length=256, pattern=r".*\S.*")],
    family: Annotated[BaseArtifactFamily, Path()],
    artifact_id: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[\x21-\x7E]+$")],
    revision: Annotated[int, Path(ge=1)],
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ArtifactRevision:
    result = await application.records.for_scope(scope_id).get_artifact_revision(
        family.value,
        artifact_id,
        revision,
    )
    return _artifact_revision_response(result)


def _source_record_response(value: RuntimeSourceRecord) -> SourceRecord:
    return SourceRecord(
        scope_id=value.scope_id,
        source_type=SourceType(value.source_type),
        source_id=value.source_id,
        content=value.content,
        position=value.position,
        content_digest=value.content_digest,
    )


def _artifact_revision_response(value: RuntimeArtifactRecord) -> ArtifactRevision:
    return ArtifactRevision(
        scope_id=value.scope_id,
        family=BaseArtifactFamily(value.family),
        artifact_id=value.artifact_id,
        revision=value.revision,
        content=value.content,
        sources=[mapping.source_type_reference(ref) for ref in value.sources],
        artifacts=[mapping.artifact_reference(ref) for ref in value.artifacts],
        content_digest=value.content_digest,
    )


def _artifact_created_response(value: RuntimeArtifactCreated) -> ArtifactCreated:
    return ArtifactCreated(
        scope_id=value.scope_id,
        family=BaseArtifactFamily(value.family),
        artifact_id=value.artifact_id,
        revision=value.revision,
        sources=[mapping.source_type_reference(ref) for ref in value.sources],
        artifacts=[mapping.artifact_reference(ref) for ref in value.artifacts],
    )


def _artifact_collection_item_response(value: RuntimeArtifactCollectionItem) -> ArtifactCollectionItem:
    return ArtifactCollectionItem(
        scope_id=value.scope_id,
        family=BaseArtifactFamily(value.family),
        artifact_id=value.artifact_id,
        revision=value.revision,
        sources=[mapping.source_type_reference(ref) for ref in value.sources],
        artifacts=[mapping.artifact_reference(ref) for ref in value.artifacts],
        content_digest=value.content_digest,
    )


def _artifact_write(value: CreateArtifactRequest | ReplaceArtifactRequest) -> RuntimeArtifactWrite:
    content = value.root.content
    if isinstance(content, TransportHandoffContent):
        content = mapping.runtime_handoff_content(content)
    return RuntimeArtifactWrite(
        content=cast(
            dict[str, JsonValue],
            content.model_dump(mode="json", by_alias=True, exclude_none=True),
        ),
    )


def _source_location(value: RuntimeSourceRecord) -> str:
    source_id = quote(value.source_id, safe="")
    scope_id = quote(value.scope_id, safe="")
    source_type = quote(value.source_type, safe="")
    return f"/v1/scopes/{scope_id}/sources/{source_type}/{source_id}"


def _artifact_location(value: RuntimeArtifactCreated) -> str:
    artifact_id = quote(value.artifact_id, safe="")
    scope_id = quote(value.scope_id, safe="")
    family = quote(value.family, safe="")
    return f"/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}"


def _artifact_etag(revision: int) -> str:
    return f'"revision:{revision}"'


def _require_artifact_etag(value: str | None) -> str:
    if value is None:
        raise _PreconditionRequiredError
    return value


def _require_report_size(estimated_bytes: int, report: Any) -> None:
    if estimated_bytes <= MAX_HANDOFF_REPORT_BYTES:
        return
    raise HandoffReportTooLargeError(
        estimated_bytes=estimated_bytes,
        selected_scopes=len(report.scopes),
    )


async def capture_content_source(
    request: CaptureContentSourceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> CaptureContentSourceResponse:
    result = await application.sources.for_scope(request.scope_id).capture(mapping.capture_request(request))
    return mapping.capture_response(result)


async def register_source_definition(
    request: RegisterSourceDefinitionRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SourceDefinitionManifest:
    result = await application.ingestion.register(mapping.runtime_source_definition_manifest(request.manifest))
    return mapping.source_definition_manifest_response(result)


async def get_connector_checkpoint(
    request: GetConnectorCheckpointRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ConnectorCheckpointState:
    result = await application.ingestion.checkpoint(mapping.connector_checkpoint_request(request))
    return mapping.connector_checkpoint_response(result)


async def submit_source_observation(
    request: SubmitSourceObservationRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SourceObservationReceipt:
    result = await application.ingestion.submit(mapping.submit_source_observation_request(request))
    return mapping.source_observation_receipt_response(result)


async def commit_connector_checkpoint(
    request: CommitConnectorCheckpointRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ConnectorCheckpointState:
    result = await application.ingestion.commit(mapping.commit_connector_checkpoint_request(request))
    return mapping.connector_checkpoint_response(result)


async def flush_memory(
    request: FlushMemoryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> FlushMemoryResponse:
    memory = application.memory.for_scope(request.scope_id)
    access = access_control_for_mode(
        http_request.app.state.access_control,
        mode=http_request.app.state.access_mode,
    )
    principal = _require_principal() if access is not None else None
    if access is not None:
        current = await memory.list(include_inactive=True)
        await access.require_all(
            principal,
            tuple(
                (AccessAction.ARTIFACT_WRITE, _memory_entry_resource(request.scope_id, entry))
                for entry in current.entries
            ),
            context=_access_audit_context(FLUSH_MEMORY.operation_id),
        )
    result = await memory.flush()
    if access is not None and result.memory_ref is not None:
        current = await memory.list(include_inactive=True)
        for entry in current.entries:
            resource = _memory_entry_resource(request.scope_id, entry)
            if await access.artifact_owner(resource) is None:
                await access.establish_artifact_owner(
                    resource,
                    cast(PrincipalRef, principal),
                    idempotency_key=f"memory-owner:{request.scope_id}:{entry.entry.entry_id}",
                    context=_access_audit_context(FLUSH_MEMORY.operation_id),
                )
    return mapping.flush_response(result)


def _memory_entry_resource(scope_id: str, entry: MemoryEntryRecord) -> ResourceRef:
    return ResourceRef.artifact(
        scope_id,
        family="memory",
        artifact_id=entry.memory_ref.artifact_id,
        selector=MemoryEntrySelector(entry_id=entry.entry.entry_id),
    )


async def remember_memory(
    request: RememberMemoryRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> MemoryMutationResponse:
    result = await application.memory.for_scope(request.scope_id).remember(mapping.remember_request(request))
    if result.entry is not None:
        await _establish_created_owner(
            http_request,
            ResourceRef.artifact(
                request.scope_id,
                family="memory",
                artifact_id=result.memory_ref.artifact_id,
                selector=MemoryEntrySelector(entry_id=result.entry.entry.entry_id),
            ),
            idempotency_key=f"memory-owner:{request.scope_id}:{result.entry.entry.entry_id}",
            operation=REMEMBER_MEMORY.operation_id,
        )
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
    http_request: Request,
) -> HandoffAcknowledgement:
    principal = current_principal()
    if (
        http_request.app.state.access_control is not None
        and request.status.value == "accepted"
        and principal is not None
        and request.receiver != principal.id
    ):
        raise AccessInvalidRequestError("receiver-principal")
    access = access_control_for_mode(http_request.app.state.access_control, mode=http_request.app.state.access_mode)
    identity = None
    if access is not None:
        # Reserve attribution before capturing the Source so a failed write or a
        # concurrent replay cannot attach another Principal to this receipt ID.
        identity = await access.record_receipt_identity(
            HandoffReceiptIdentity(
                request.scope_id, request.source_id, _require_principal(), request.receiver == _require_principal().id
            )
        )
    result = await application.work.for_scope(request.scope_id).acknowledge(
        mapping.acknowledge_handoff_request(request)
    )
    response = mapping.handoff_acknowledgement_response(result)
    if identity is not None:
        response.receipt_identity = _receipt_identity_response(identity)
    return response


def _receipt_identity_response(identity: HandoffReceiptIdentity) -> TransportHandoffReceiptIdentity:
    return TransportHandoffReceiptIdentity(
        principal=_access_principal_response(identity.principal),
        receiver_identity_matches=identity.receiver_identity_matches,
    )


def _is_handoff_receipt_content(content: JsonValue) -> bool:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return False
    return isinstance(content, dict) and content.get("schema") == "powercontext.handoff-receipt.v1"


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
    http_request: Request,
) -> CommittedHandoff:
    result = await application.handoff.for_scope(request.scope_id).commit(
        mapping.runtime_prepared_handoff(request.handoff)
    )
    if result.revision == 1:
        await _establish_created_owner(
            http_request,
            ResourceRef.artifact(
                request.scope_id,
                family="handoff",
                artifact_id=result.artifact_id,
            ),
            idempotency_key=f"handoff-owner:{request.scope_id}:{result.artifact_id}",
            operation=COMMIT_HANDOFF.operation_id,
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
    http_request: Request,
) -> ArtifactCandidate:
    result = await application.experience.for_scope(request.scope_id).propose(
        mapping.propose_experience_request(request)
    )
    await _attest_candidate_owner(
        http_request,
        scope_id=request.scope_id,
        candidate_id=result.candidate_id,
        family=result.family,
        target=result.target,
    )
    return await _candidate_response(http_request, request.scope_id, result)


async def generate_experience(
    request: GenerateExperienceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> GeneratedCandidateResponse:
    result = await application.experience.for_scope(request.scope_id).generate(
        mapping.generate_experience_request(request)
    )
    if result.candidate is not None:
        await _attest_candidate_owner(
            http_request,
            scope_id=request.scope_id,
            candidate_id=result.candidate.candidate_id,
            family=result.candidate.family,
            target=result.candidate.target,
        )
    response = mapping.generated_candidate_response(result)
    if result.candidate is not None:
        response.candidate = await _candidate_response(http_request, request.scope_id, result.candidate)
    return response


async def get_experience(
    request: GetExperienceRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ExperienceArtifact:
    result = await application.experience.for_scope(request.scope_id).get(mapping.get_experience_request(request))
    return mapping.experience_response(result)


async def propose_skill(
    request: ProposeSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ArtifactCandidate:
    result = await application.skill.for_scope(request.scope_id).propose(mapping.propose_skill_request(request))
    await _attest_candidate_owner(
        http_request,
        scope_id=request.scope_id,
        candidate_id=result.candidate_id,
        family=result.family,
        target=result.target,
    )
    return await _candidate_response(http_request, request.scope_id, result)


async def generate_skill(
    request: GenerateSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> GeneratedCandidateResponse:
    result = await application.skill.for_scope(request.scope_id).generate(mapping.generate_skill_request(request))
    if result.candidate is not None:
        await _attest_candidate_owner(
            http_request,
            scope_id=request.scope_id,
            candidate_id=result.candidate.candidate_id,
            family=result.candidate.family,
            target=result.candidate.target,
        )
    response = mapping.generated_candidate_response(result)
    if result.candidate is not None:
        response.candidate = await _candidate_response(http_request, request.scope_id, result.candidate)
    return response


async def get_skill(
    request: GetSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SkillArtifact:
    result = await application.skill.for_scope(request.scope_id).get(mapping.get_skill_request(request))
    return mapping.skill_response(result)


async def list_managed_skills(
    request: ListManagedSkillsRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ListManagedSkillsResponse:
    scoped = application.skill.for_scope(request.scope_id)
    values: list[tuple[Skill, ArtifactGovernance]] = []
    query = "" if request.query is None else request.query.strip()
    if query:
        for hit in await scoped.search(query, request.limit):
            skill = await scoped.get(RuntimeGetSkillRequest(artifact=hit.artifact_ref))
            values.append((skill, await scoped.governance(skill.artifact_id)))
    else:
        values.extend(await scoped.list(include_deprecated=request.include_deprecated, limit=request.limit))
    if query and request.include_deprecated:
        seen = {skill.artifact_id for skill, _governance in values}
        for skill, governance in await scoped.list(include_deprecated=True, limit=request.limit):
            search_text = "\n".join((
                skill.content.name,
                skill.content.description,
                skill.content.instructions,
                *skill.content.metadata.values(),
            ))
            if (
                governance.lifecycle_state is ArtifactLifecycleState.DEPRECATED
                and skill.artifact_id not in seen
                and query.casefold() in search_text.casefold()
            ):
                values.append((skill, governance))
    return ListManagedSkillsResponse(
        skills=[mapping.managed_skill_library_entry(skill, governance) for skill, governance in values[: request.limit]]
    )


async def update_skill_lifecycle(
    request: UpdateSkillLifecycleRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SkillGovernance:
    result = await application.skill.for_scope(request.scope_id).update_lifecycle(
        request.artifact_id,
        request.expected_generation,
        ArtifactLifecycleState(request.lifecycle_state.value),
        request.replacement_artifact_id,
    )
    return mapping.skill_governance(result)


async def get_skill_package_manifest(
    request: GetSkillPackageRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SkillPackageManifest:
    package = await application.skill.for_scope(request.scope_id).package(
        mapping.runtime_artifact_reference(request.artifact)
    )
    return _skill_package_manifest(package)


async def download_skill_package(
    request: GetSkillPackageRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SkillPackageDownload:
    package = await application.skill.for_scope(request.scope_id).package(
        mapping.runtime_artifact_reference(request.artifact)
    )
    return SkillPackageDownload(
        package=package.reference.model_dump(mode="json"),
        archive_base64=base64.b64encode(package.archive_bytes).decode("ascii"),
    )


async def propose_skill_package(
    request: ProposeSkillPackageRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ArtifactCandidate:
    try:
        archive_bytes = base64.b64decode(request.archive_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidRuntimeRequestError("skill-package-base64") from error
    try:
        candidate = await application.skill.for_scope(request.scope_id).upload_package(
            archive_bytes,
            request.reason,
            None if request.target is None else mapping.runtime_artifact_reference(request.target),
        )
    except ValueError as error:
        raise InvalidRuntimeRequestError("skill-package") from error
    await _attest_candidate_owner(
        http_request,
        scope_id=request.scope_id,
        candidate_id=candidate.candidate_id,
        family=candidate.family,
        target=candidate.target,
    )
    return await _candidate_response(http_request, request.scope_id, candidate)


async def record_skill_usage(
    request: RecordSkillUsageRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> CaptureContentSourceResponse:
    try:
        receipt = await application.skill.for_scope(request.scope_id).record_usage(
            SkillUsageCapture(
                observation_id=request.observation_id,
                skill_ref=mapping.runtime_artifact_reference(request.skill_ref),
                package_digest=request.package_digest,
                target_id=request.target_id,
                selected=request.selected,
                invoked=ObservedInvocation(request.invoked.value),
                validation=ObservedValidation(request.validation.value),
                outcome=ObservedOutcome(request.outcome.value),
                task_source=(
                    None if request.task_source is None else mapping.runtime_source_reference(request.task_source)
                ),
                environment_fingerprint=request.environment_fingerprint,
            )
        )
    except ValueError as error:
        raise InvalidRuntimeRequestError("skill-usage") from error
    return mapping.capture_response(receipt)


async def create_remote_skill_target(
    request: CreateRemoteSkillTargetRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> RemoteSkillTargetEnrollment:
    enrollment = await application.remote_skills.create_target(
        request.scope_id,
        request.agent_kind.value,
        request.display_name,
    )
    expires_at = enrollment.target.enrollment_expires_at
    if expires_at is None:
        raise RuntimeError("pending remote target is missing enrollment expiry")  # noqa: TRY003
    return RemoteSkillTargetEnrollment(
        target=_remote_skill_target(enrollment.target),
        enrollment_code=enrollment.enrollment_code.get_secret_value(),
        enrollment_expires_at=_aware_datetime(expires_at),
    )


async def list_remote_skill_targets(
    request: ListRemoteSkillTargetsRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ListRemoteSkillTargetsResponse:
    statuses = await application.remote_skills.list_targets(
        request.scope_id,
        target_id=request.target_id,
        limit=request.limit,
    )
    return ListRemoteSkillTargetsResponse(targets=[_remote_skill_target_status(value) for value in statuses])


async def enroll_remote_skill_target(
    request: EnrollRemoteSkillTargetRequest,
    http_request: Request,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> RemoteSkillTargetCredential:
    _require_secure_remote_transport(http_request)
    credential = await application.remote_skills.enroll(
        request.enrollment_code,
        request.installation_id,
        request.receiver_version,
        request.environment_fingerprint,
        request.machine_hostname,
        request.workspace_name,
    )
    return RemoteSkillTargetCredential.model_validate({
        "scope_id": credential.scope_id,
        "target_id": credential.target_id,
        "agent_kind": credential.agent_kind,
        "credential": credential.credential.get_secret_value(),
    })


async def rename_remote_skill_target(
    request: RenameRemoteSkillTargetRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> RemoteSkillTarget:
    target = await application.remote_skills.rename_target(
        request.scope_id,
        request.target_id,
        request.expected_generation,
        request.display_name,
    )
    return _remote_skill_target(target)


async def revoke_remote_skill_target(
    request: RevokeRemoteSkillTargetRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> RemoteSkillTarget:
    target = await application.remote_skills.revoke_target(
        request.scope_id,
        request.target_id,
        request.expected_generation,
    )
    return _remote_skill_target(target)


async def publish_remote_skill(
    request: PublishRemoteSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> RemoteSkillPublication:
    publication = await application.remote_skills.publish(
        request.scope_id,
        request.target_id,
        mapping.runtime_artifact_reference(request.artifact),
        request.expected_generation,
        allow_deprecated=request.allow_deprecated,
    )
    return _remote_skill_publication(publication)


async def unpublish_remote_skill(
    request: UnpublishRemoteSkillRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> RemoteSkillPublication:
    publication = await application.remote_skills.unpublish(
        request.scope_id,
        request.target_id,
        request.artifact_id,
        request.expected_generation,
    )
    return _remote_skill_publication(publication)


async def reconcile_remote_skills(
    request: ReconcileRemoteSkillsRequest,
    http_request: Request,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> ReconcileRemoteSkillsResponse:
    _require_secure_remote_transport(http_request)
    credential = _target_credential(http_request)
    observations = tuple(
        DomainRemoteSkillObservation.model_validate(observation.model_dump(mode="json"))
        for observation in request.observations
    )
    result = await application.remote_skills.reconcile(
        credential,
        observations,
        request.receiver_version,
        request.environment_fingerprint,
    )
    return ReconcileRemoteSkillsResponse(
        scope_id=result.scope_id,
        target_id=result.target_id,
        actions=[RemoteSkillAction.model_validate(action.model_dump(mode="json")) for action in result.actions],
    )


async def download_remote_skill_package(
    request: DownloadRemoteSkillPackageRequest,
    http_request: Request,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> SkillPackageDownload:
    _require_secure_remote_transport(http_request)
    package = await application.remote_skills.download(
        _target_credential(http_request),
        request.generation,
        mapping.runtime_artifact_reference(request.artifact),
        SkillPackageRef.model_validate(request.package.model_dump(mode="json")),
    )
    return SkillPackageDownload(
        package=package.reference.model_dump(mode="json"),
        archive_base64=base64.b64encode(package.archive_bytes).decode("ascii"),
    )


async def record_remote_skill_receipt(
    request: RecordRemoteSkillReceiptRequest,
    http_request: Request,
    application: Annotated[ServerApplication, Depends(_require_application)],
) -> RemoteSkillReceiptResponse:
    _require_secure_remote_transport(http_request)
    try:
        receipt = DomainRemoteSkillReceipt.model_validate(request.model_dump(mode="json"))
    except ValueError as error:
        raise InvalidRuntimeRequestError("remote-skill-receipt") from error
    result = await application.remote_skills.receipt(_target_credential(http_request), receipt)
    return RemoteSkillReceiptResponse(
        accepted=result.accepted,
        stale=result.stale,
        publication=_remote_skill_publication(result.publication),
    )


def _remote_skill_target(target: RemoteAgentSkillTarget) -> RemoteSkillTarget:
    return RemoteSkillTarget.model_validate({
        "scope_id": target.scope_id,
        "target_id": target.target_id,
        "display_name": target.display_name,
        "agent_kind": target.agent_kind,
        "installation_scope": target.installation_scope,
        "delivery_mode": target.delivery_mode,
        "installation_id": target.installation_id,
        "state": target.state.value,
        "receiver_version": target.receiver_version,
        "environment_fingerprint": target.environment_fingerprint,
        "machine_hostname": target.machine_hostname,
        "workspace_name": target.workspace_name,
        "last_seen_at": None if target.last_seen_at is None else _aware_datetime(target.last_seen_at),
        "generation": target.generation,
    })


def _remote_skill_target_status(status: DomainRemoteSkillTargetStatus) -> RemoteSkillTargetStatus:
    return RemoteSkillTargetStatus(
        target=_remote_skill_target(status.target),
        publications=[_remote_skill_publication(publication) for publication in status.publications],
    )


def _remote_skill_publication(publication: SkillPublication) -> RemoteSkillPublication:
    return RemoteSkillPublication.model_validate({
        "scope_id": publication.scope_id,
        "target_id": publication.target_id,
        "artifact_id": publication.artifact_id,
        "desired_state": publication.desired_state.value,
        "desired_revision": publication.desired_revision,
        "desired_tree_digest": publication.desired_tree_digest,
        "observed_revision": publication.observed_revision,
        "observed_tree_digest": publication.observed_tree_digest,
        "observed_generation": publication.observed_generation,
        "state": publication.state.value,
        "last_error_code": publication.last_error_code,
        "observed_at": None if publication.observed_at is None else _aware_datetime(publication.observed_at),
        "generation": publication.generation,
    })


def _target_credential(request: Request) -> str:
    authorization = request.headers.get("authorization")
    if authorization is None:
        raise RemoteTargetAuthenticationError("the target credential is missing")  # noqa: TRY003
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not credential:
        raise RemoteTargetAuthenticationError("the target credential is invalid")  # noqa: TRY003
    return credential


def _require_secure_remote_transport(request: Request) -> None:
    if request.url.scheme.casefold() == "https":
        return
    peer = request.client
    if peer is not None and _loopback_peer(peer.host):
        return
    if request.app.state.allow_insecure_remote_http:
        return
    raise InvalidRuntimeRequestError("remote-skill-https")


def _loopback_peer(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _aware_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _skill_package_manifest(package: SkillPackageSnapshot) -> SkillPackageManifest:
    return SkillPackageManifest(
        package=package.reference.model_dump(mode="json"),
        name=package.metadata.name,
        description=package.metadata.description,
        license=package.metadata.license,
        compatibility=package.metadata.compatibility,
        metadata=package.metadata.metadata,
        allowed_tools=package.metadata.allowed_tools,
        files=[
            SkillPackageFile(
                path=entry.path,
                digest=entry.digest,
                size=entry.size,
                media_type=entry.media_type,
                executable=bool(entry.mode & 0o111),
            )
            for entry in package.entries
        ],
    )


async def _validate_shareable_resource(application: ServerApplication | None, resource: ResourceRef) -> None:
    if resource.type is not AccessResourceType.ARTIFACT:
        return
    if application is None:
        raise _RuntimeNotReadyError
    profile = artifact_family_profile(resource)
    identity = resource.identity
    if identity is None or resource.scope_id is None:
        raise AccessInvalidRequestError("artifact-identity")
    artifact = ArtifactRef(
        family=identity.family,
        artifact_id=identity.artifact_id,
        revision=1,
    )
    if profile.family == "handoff":
        await application.handoff.for_scope(resource.scope_id).revision(artifact)
        return
    if profile.family == "memory":
        selector = resource.selector
        if selector is None:
            raise AccessInvalidRequestError("memory-entry-selector")
        memory = await application.records.for_scope(resource.scope_id).get_artifact("memory", identity.artifact_id)
        if selector.entry_id not in _memory_manifest_entry_ids(memory):
            raise MemoryEntryNotFoundError(selector.entry_id)
        return
    if profile.family == "experience":
        await application.experience.for_scope(resource.scope_id).get(RuntimeGetExperienceRequest(artifact=artifact))
        return
    if profile.family == "skill":
        await application.skill.for_scope(resource.scope_id).get(RuntimeGetSkillRequest(artifact=artifact))
        return
    raise AccessInvalidRequestError("artifact-family-disabled")


async def _establish_created_owner(
    request: Request,
    resource: ResourceRef,
    *,
    idempotency_key: str,
    operation: str,
) -> None:
    access = access_control_for_mode(
        request.app.state.access_control,
        mode=request.app.state.access_mode,
    )
    if access is None:
        return
    await access.establish_artifact_owner(
        resource,
        _require_principal(),
        idempotency_key=idempotency_key,
        context=_access_audit_context(operation),
    )


async def _establish_base_artifact_owners(
    request: Request,
    application: ServerApplication,
    result: RuntimeArtifactCreated,
) -> None:
    if access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode) is None:
        return
    resource = ResourceRef.artifact(result.scope_id, family=result.family, artifact_id=result.artifact_id)
    if result.family != BaseArtifactFamily.MEMORY.value:
        await _establish_created_owner(
            request,
            resource,
            idempotency_key=_base_owner_idempotency_key(resource),
            operation=CREATE_ARTIFACT.operation_id,
        )
        return
    artifact = await application.records.for_scope(result.scope_id).get_artifact(result.family, result.artifact_id)
    await _establish_new_memory_entry_owners(
        request,
        artifact,
        previous_entry_ids=frozenset(),
        operation=CREATE_ARTIFACT.operation_id,
    )


async def _establish_new_memory_entry_owners(
    request: Request,
    result: RuntimeArtifactRecord,
    *,
    previous_entry_ids: frozenset[str],
    operation: str,
) -> None:
    if result.family != BaseArtifactFamily.MEMORY.value:
        return
    for entry_id in sorted(_memory_manifest_entry_ids(result) - previous_entry_ids):
        resource = ResourceRef.artifact(
            result.scope_id,
            family=result.family,
            artifact_id=result.artifact_id,
            selector=MemoryEntrySelector(entry_id=entry_id),
        )
        await _establish_created_owner(
            request,
            resource,
            idempotency_key=_base_owner_idempotency_key(resource),
            operation=operation,
        )


def _memory_manifest_entry_ids(result: RuntimeArtifactRecord) -> frozenset[str]:
    manifest = result.content.get("manifest")
    entries = manifest.get("entries") if isinstance(manifest, Mapping) else None
    if not isinstance(entries, list):
        raise AccessUnavailableError
    entry_ids: set[str] = set()
    for entry in entries:
        entry_id = entry.get("entry_id") if isinstance(entry, Mapping) else None
        if not isinstance(entry_id, str) or not entry_id or entry_id in entry_ids:
            raise AccessUnavailableError
        entry_ids.add(entry_id)
    return frozenset(entry_ids)


def _base_owner_idempotency_key(resource: ResourceRef) -> str:
    return f"base-artifact-owner:{sha256(resource.key.encode()).hexdigest()}"


async def _attest_candidate_owner(
    request: Request,
    *,
    scope_id: str,
    candidate_id: str,
    family: str,
    target: ArtifactRef | None,
) -> None:
    access = access_control_for_mode(
        request.app.state.access_control,
        mode=request.app.state.access_mode,
    )
    if access is None:
        return
    logical_target = (
        None
        if target is None
        else ResourceRef.artifact(
            scope_id,
            family=target.family,
            artifact_id=target.artifact_id,
        )
    )
    await access.attest_candidate_owner(
        scope_id=scope_id,
        candidate_id=candidate_id,
        family=family,
        proposed_owner=_require_principal(),
        target=logical_target,
        idempotency_key=f"candidate-owner:{scope_id}:{candidate_id}",
    )


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
    http_request: Request,
) -> GeneratedCandidateResponse:
    result = await application.external_skills.for_scope(request.scope_id).import_managed(
        mapping.import_external_skill_request(request)
    )
    if result.candidate is not None:
        await _attest_candidate_owner(
            http_request,
            scope_id=request.scope_id,
            candidate_id=result.candidate.candidate_id,
            family=result.candidate.family,
            target=result.candidate.target,
        )
    response = mapping.generated_candidate_response(result)
    if result.candidate is not None:
        response.candidate = await _candidate_response(http_request, request.scope_id, result.candidate)
    return response


async def _candidate_response(
    request: Request, scope_id: str, candidate: RuntimeArtifactCandidate[Any]
) -> ArtifactCandidate:
    response = mapping.candidate_response(candidate)
    access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
    if access is None:
        return response
    decision = await access.check(
        _require_principal(),
        AccessAction.SCOPE_REVIEW,
        ResourceRef.scope(scope_id),
        context=_access_audit_context("candidate_permissions"),
    )
    pending = candidate.status.value == "pending"
    attestation = (
        await access.candidate_owner(scope_id, candidate.candidate_id)
        if decision.allowed and access.provider_capabilities.relationship_management
        else None
    )
    response.permissions = CandidatePermissions(
        can_revise=pending
        and decision.allowed
        and attestation is not None
        and attestation.proposed_owner == _require_principal(),
        can_approve=pending and decision.allowed and attestation is not None,
        can_reject=pending and decision.allowed,
    )
    return response


async def list_artifact_candidates(
    request: ListArtifactCandidatesRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ArtifactCandidatePage:
    result = await application.review.for_scope(request.scope_id).list(mapping.list_candidates_request(request))
    response = mapping.candidate_page_response(result)
    response.candidates = [
        await _candidate_response(http_request, request.scope_id, value) for value in result.candidates
    ]
    return response


async def get_artifact_candidate(
    request: GetArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ArtifactCandidate:
    result = await application.review.for_scope(request.scope_id).get(mapping.get_candidate_request(request))
    await _require_candidate_artifact_owner(http_request, request.scope_id, result)
    return await _candidate_response(http_request, request.scope_id, result)


async def approve_artifact_candidate(
    request: ApproveArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ArtifactCandidate:
    review = application.review.for_scope(request.scope_id)
    access = access_control_for_mode(
        http_request.app.state.access_control,
        mode=http_request.app.state.access_mode,
    )
    attestation = None if access is None else await access.candidate_owner(request.scope_id, request.candidate_id)
    if access is not None and attestation is None:
        raise AccessUnavailableError("artifact_owner_pending")
    try:
        result = await review.approve(mapping.approve_candidate_request(request))
    except CandidateTerminalError:
        current = await review.get(RuntimeGetArtifactCandidateRequest(candidate_id=request.candidate_id))
        if current.status.value != "approved" or current.version != request.expected_version:
            raise
        result = current
    if access is not None and attestation is not None and attestation.target is None:
        artifact = result.result_artifact
        if artifact is None:
            raise AccessUnavailableError("artifact_owner_pending")
        await access.establish_artifact_owner(
            ResourceRef.artifact(
                request.scope_id,
                family=artifact.family,
                artifact_id=artifact.artifact_id,
            ),
            attestation.proposed_owner,
            idempotency_key=f"candidate-artifact-owner:{request.scope_id}:{request.candidate_id}",
            context=_access_audit_context(APPROVE_ARTIFACT_CANDIDATE.operation_id),
        )
    return await _candidate_response(http_request, request.scope_id, result)


async def reject_artifact_candidate(
    request: RejectArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ArtifactCandidate:
    result = await application.review.for_scope(request.scope_id).reject(mapping.reject_candidate_request(request))
    return await _candidate_response(http_request, request.scope_id, result)


async def revise_artifact_candidate(
    request: ReviseArtifactCandidateRequest,
    application: Annotated[ServerApplication, Depends(_require_application)],
    http_request: Request,
) -> ArtifactCandidate:
    access = access_control_for_mode(
        http_request.app.state.access_control,
        mode=http_request.app.state.access_mode,
    )
    if access is not None:
        attestation = await access.candidate_owner(request.scope_id, request.candidate_id)
        if attestation is None:
            raise AccessUnavailableError("artifact_owner_pending")
        if attestation.proposed_owner != _require_principal():
            raise AccessDeniedError
    result = await application.review.for_scope(request.scope_id).revise(mapping.revise_candidate_request(request))
    return await _candidate_response(http_request, request.scope_id, result)


async def _require_candidate_artifact_owner(
    request: Request,
    scope_id: str,
    candidate: ReviewedCandidate,
) -> None:
    if candidate.status.value != "approved" or candidate.result_artifact is None:
        return
    access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
    if access is None:
        return
    artifact = candidate.result_artifact
    owner = await access.artifact_owner(
        ResourceRef.artifact(scope_id, family=artifact.family, artifact_id=artifact.artifact_id)
    )
    if owner is None:
        raise AccessUnavailableError("artifact_owner_pending")


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


def _require_scope_application(request: Request) -> ScopeApplication:
    application = _require_application(request)
    if application.scopes is None:
        raise _RuntimeNotReadyError
    return application.scopes


def _require_publication_application(request: Request) -> ArtifactPublicationApplication:
    application = _require_application(request)
    if application.publications is None:
        raise _RuntimeNotReadyError
    return application.publications


def _require_handoff_report_application(request: Request) -> HandoffReportApplication:
    application = _require_application(request)
    if application.handoff_report is None:
        raise _RuntimeNotReadyError
    return application.handoff_report


def _require_access_control(request: Request) -> AccessControlService:
    access = access_control_for_mode(
        request.app.state.access_control,
        mode=request.app.state.access_mode,
    )
    if access is None:
        raise _RuntimeNotReadyError
    return access


def _require_principal() -> PrincipalRef:
    principal = current_principal()
    if principal is None:
        raise AccessIdentityRequiredError
    return principal


def _access_audit_context(operation: str) -> AccessAuditContext:
    authentication = current_authentication()
    return AccessAuditContext(
        transport="mcp" if is_internal_bridge() else "http",
        operation=operation,
        request_id=current_request_id(),
        actor=None if authentication is None else authentication.actor,
        subject_groups=() if authentication is None else authentication.subject_groups,
    )


def _access_principal(value: TransportAccessPrincipal) -> PrincipalRef:
    # IDs are canonical. A description in an Access mutation payload is not a
    # trusted directory assertion, so never persist it as identity metadata.
    return PrincipalRef(type=value.type, id=value.id)


def _access_subject(value: TransportAccessSubject) -> AccessSubjectRef:
    subject = value.root
    if isinstance(subject, TransportAccessGroup):
        return GroupRef(type=subject.type, id=subject.id)
    return _access_principal(subject)


def _access_principal_response(value: PrincipalRef) -> TransportAccessPrincipal:
    return TransportAccessPrincipal(
        type=cast(Literal["user", "service"], value.type),
        id=value.id,
        description=value.description,
    )


def _access_subject_response(value: AccessSubjectRef) -> TransportAccessSubject:
    if isinstance(value, GroupRef):
        return TransportAccessSubject(
            root=TransportAccessGroup(type="group", id=value.id, description=value.description)
        )
    return TransportAccessSubject(root=_access_principal_response(value))


def _access_resource(value: TransportAccessResource) -> ResourceRef:
    resource = value.root
    if isinstance(resource, ServerAccessResource):
        return ResourceRef.server(resource.deployment_id)
    if isinstance(resource, ScopeAccessResource):
        return ResourceRef.scope(resource.scope_id)
    selector = (
        None
        if resource.selector is None
        else MemoryEntrySelector(
            entry_id=resource.selector.entry_id,
        )
    )
    return ResourceRef.artifact(
        resource.scope_id,
        family=resource.identity.family,
        artifact_id=resource.identity.artifact_id,
        selector=selector,
    )


def _access_resource_response(value: ResourceRef) -> TransportAccessResource:
    if value.type is AccessResourceType.SERVER:
        return TransportAccessResource(
            root=ServerAccessResource(type="server", deployment_id=value.deployment_id or "")
        )
    if value.type is AccessResourceType.SCOPE:
        return TransportAccessResource(root=ScopeAccessResource(type="scope", scope_id=value.scope_id or ""))
    if value.identity is None:
        raise AccessUnavailableError
    selector = value.selector
    return TransportAccessResource(
        root=ArtifactAccessResource(
            type="artifact",
            scope_id=value.scope_id or "",
            identity=TransportAccessArtifactIdentity(
                family=value.identity.family,
                artifact_id=value.identity.artifact_id,
            ),
            selector=(
                None
                if selector is None
                else MemoryEntryAccessSelector(
                    type=TransportMemoryEntrySelectorType.MEMORY_ENTRY,
                    entry_id=selector.entry_id,
                )
            ),
        )
    )


def _access_decision_response(value: AccessDecision) -> TransportAccessDecision:
    return TransportAccessDecision(
        allowed=value.allowed,
        reason_code=value.reason_code,
    )


def _access_binding_response(value: AccessBinding) -> TransportAccessBinding:
    return TransportAccessBinding(
        binding_id=value.binding_id,
        subject=_access_subject_response(value.subject),
        resource=_access_resource_response(value.resource),
        role=TransportAccessRole(value.role.value),
        granted_by=_access_principal_response(value.granted_by),
        reason=value.reason,
        created_at=value.created_at,
        expires_at=value.expires_at,
        state=TransportAccessBindingState(value.state.value),
        version=value.version,
        policy_revision=value.policy_revision,
        idempotency_key=value.idempotency_key,
        revoked_at=value.revoked_at,
        revoked_by=None if value.revoked_by is None else _access_principal_response(value.revoked_by),
    )


def _access_audit_response(value: AccessAuditEvent) -> TransportAccessAuditEvent:
    if value.cursor is None:
        raise AccessUnavailableError
    return TransportAccessAuditEvent(
        cursor=value.cursor,
        event_id=value.event_id,
        occurred_at=value.occurred_at,
        request_id=value.request_id,
        transport=value.transport,
        operation=value.operation,
        principal=_access_principal_response(value.principal),
        actor=None if value.actor is None else _access_principal_response(value.actor),
        action=TransportAccessAction(value.action.value),
        resource=_access_resource_response(value.resource),
        allowed=value.allowed,
        reason_code=value.reason_code,
        policy_revision=value.policy_revision,
        matched_subject=(None if value.matched_subject is None else _access_subject_response(value.matched_subject)),
        binding_id=value.binding_id,
        target=None if value.target is None else _access_subject_response(value.target),
        role=None if value.role is None else TransportAccessRole(value.role.value),
        expected_version=value.expected_version,
        result_version=value.result_version,
    )


def _binding_administrative_check(
    resource: ResourceRef | None,
    *,
    deployment_id: str,
) -> tuple[AccessAction, ResourceRef]:
    if resource is None or resource.type is AccessResourceType.SERVER:
        if resource is not None and resource.deployment_id != deployment_id:
            raise AccessInvalidRequestError("deployment")
        return AccessAction.SERVER_ADMIN, ResourceRef.server(deployment_id)
    if resource.type is AccessResourceType.SCOPE:
        return AccessAction.SCOPE_ADMIN, resource
    parent = resource.parent_scope
    if parent is None:
        raise AccessInvalidRequestError("artifact-reference")
    action = (
        AccessAction.SCOPE_DELEGATE
        if artifact_family_profile(resource).family == "handoff"
        else AccessAction.SCOPE_ADMIN
    )
    return action, parent


def _scope_descriptor_response(value: DomainScopeDescriptor) -> ScopeDescriptor:
    return ScopeDescriptor.model_validate(value.model_dump(mode="json"))


def _domain_binding_key(value: ScopeBindingKey) -> DomainScopeBindingKey:
    return DomainScopeBindingKey(
        integration=value.integration,
        kind=value.kind,
        external_id=value.external_id,
    )


def _domain_scope_selection(value: ScopeSelection) -> DomainScopeSelection:
    return DomainScopeSelection.model_validate(value.root.model_dump(mode="json"))


def _transport_binding_key(value: DomainScopeBindingKey) -> ScopeBindingKey:
    return ScopeBindingKey(
        integration=value.integration,
        kind=value.kind,
        external_id=value.external_id,
    )


def _add_route(
    app: FastAPI,
    operation: Operation[_RequestT, _ResponseT],
    endpoint: Callable[..., Awaitable[_ResponseT | Response]],
) -> None:
    observed = _observe_application_operation(app, operation, endpoint)
    app.router.add_api_route(
        operation.path,
        observed,
        methods=[operation.method],
        operation_id=operation.operation_id,
        response_model=operation.response_type,
        status_code=operation.success_status,
        responses=operation.responses,
        summary=operation.summary,
        tags=list(operation.tags),
        dependencies=[] if operation.access is None else [Depends(_authorization_dependency(operation))],
        route_class_override=_EncodedPathAPIRoute if operation.path.startswith("/v1/scopes/") else None,
    )


# Collection permission allows identity discovery, but content remains unavailable
# until every committed identity has its immutable owner relation.
_COLLECTION_CONTENT_OPERATIONS = frozenset({
    "search_memory",
    "list_memory_entries",
    "list_memory_changes",
    "prepare_context",
    "list_managed_skills",
    "list_artifact_candidates",
    "get_artifact_candidate",
    "list_artifacts",
    "get_artifact",
    "get_artifact_revision",
    "prepare_handoff",
    "activate_handoff",
    "finalize_handoff",
    "handoff_current_work",
    "generate_experience",
    "generate_skill",
})


async def _require_selection_content_ready(request: Request, selection: ScopeSelection) -> None:
    if access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode) is None:
        return
    scopes = _require_scope_application(request)
    for scope in await scopes.resolve_selection(_domain_scope_selection(selection)):
        await require_scope_content_ready(request, scope.scope_id)


async def require_scope_content_ready(request: Request, scope_id: str) -> None:
    """Check an already-authorized collection using content-free catalog identities."""

    access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
    if access is None:
        return
    application = _require_application(request)
    for identity in await application.records.for_scope(scope_id).logical_artifacts():
        resource = ResourceRef.artifact(
            scope_id,
            family=identity.family,
            artifact_id=identity.artifact_id,
            selector=None if identity.entry_id is None else MemoryEntrySelector(entry_id=identity.entry_id),
        )
        if await access.artifact_owner(resource) is None:
            raise AccessUnavailableError("artifact_owner_pending")


def _authorization_dependency(
    operation: Operation[Any, Any],
) -> Callable[[Request], Awaitable[None]]:
    requirement = operation.access
    if requirement is None:
        raise AccessInvalidRequestError("resource")

    async def authorize(request: Request) -> None:
        access = access_control_for_mode(
            request.app.state.access_control,
            mode=request.app.state.access_mode,
        )
        if access is not None:
            payload = await _authorization_payload(request, operation)
            checks = _resolve_access_requirements(requirement, payload, deployment_id=access.deployment_id)
            context = _access_audit_context(operation.operation_id)
            for scope_id in sorted({resource.scope_id for _, resource in checks if resource.scope_id is not None}):
                await access.bootstrap_static_scope(current_principal(), scope_id, context=context)
            if len(checks) == 1:
                action, resource = checks[0]
                await access.require(current_principal(), action, resource, context=context)
            else:
                await access.require_all(current_principal(), checks, context=context)
            if operation.operation_id in _COLLECTION_CONTENT_OPERATIONS:
                for scope_id in sorted({
                    resource.scope_id
                    for _, resource in checks
                    if resource.type is AccessResourceType.SCOPE and resource.scope_id is not None
                }):
                    await require_scope_content_ready(request, scope_id)

    return authorize


async def _authorization_payload(request: Request, operation: Operation[Any, Any]) -> Mapping[str, Any]:
    path_values = dict(request.path_params)
    if operation.request_type is None:
        return path_values
    if operation.request_location == "query":
        return {**path_values, **request.query_params}
    try:
        value = await request.json()
    except (UnicodeDecodeError, ValueError) as error:
        raise AccessInvalidRequestError("resource") from error
    if not isinstance(value, dict):
        raise AccessInvalidRequestError("resource")
    request_type = operation.request_type
    if request_type is None:
        return value
    try:
        validated = request_type.model_validate(value)
    except ValueError as error:
        raise AccessInvalidRequestError("resource") from error
    return {**path_values, **cast(Mapping[str, Any], validated.model_dump(mode="json"))}


def _resolve_access_requirements(
    requirement: AccessRequirement,
    payload: Mapping[str, Any],
    *,
    deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    if requirement.resolver == "static":
        if requirement.action is None:
            raise AccessInvalidRequestError("resource")
        return ((AccessAction(requirement.action), ResourceRef.server(deployment_id)),)
    if requirement.resolver == "request":
        if requirement.action is None:
            raise AccessInvalidRequestError("resource")
        scope_id = _nested_request_value(payload, requirement.scope_id_field)
        return ((AccessAction(requirement.action), ResourceRef.scope(scope_id)),)
    resolver = _NAMED_ACCESS_RESOLVERS.get(requirement.resolver)
    if resolver is None:
        raise AccessInvalidRequestError("resource")
    return resolver(payload, deployment_id)


def _continue_handoff_access(payload: Mapping[str, Any]) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    scope_id = _nested_request_value(payload, "scope_id")
    selection = str(_nested_request_value(payload, "selection"))
    if selection == "prepared":
        return ((AccessAction.SCOPE_READ, ResourceRef.scope(scope_id)),)
    resource = (
        _artifact_resource(payload, "revision", family="handoff")
        if selection == "exact"
        else ResourceRef.artifact(scope_id, family="handoff", artifact_id="handoff")
    )
    return (
        (AccessAction.ARTIFACT_READ, resource),
        (AccessAction.HANDOFF_EVIDENCE_INSPECT, resource),
    )


def _acknowledge_handoff_access(payload: Mapping[str, Any]) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    scope_id = _nested_request_value(payload, "scope_id")
    selection = str(_nested_request_value(payload, "selection"))
    if selection != "exact":
        return ((AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id)),)
    return ((AccessAction.HANDOFF_ACKNOWLEDGE, _artifact_resource(payload, "revision", family="handoff")),)


def _artifact_resource(payload: Mapping[str, Any], field: str, *, family: str) -> ResourceRef:
    reference = payload.get(field)
    if not isinstance(reference, Mapping) or _mapping_text(reference, "family") != family:
        raise AccessInvalidRequestError("artifact-reference")
    return ResourceRef.artifact(
        _nested_request_value(payload, "scope_id"),
        family=family,
        artifact_id=_mapping_text(reference, "artifact_id"),
    )


def _memory_artifact_resource(payload: Mapping[str, Any]) -> ResourceRef:
    citation = payload.get("citation")
    if not isinstance(citation, Mapping):
        raise AccessInvalidRequestError("memory-entry-selector")
    reference = citation.get("memory_ref")
    if not isinstance(reference, Mapping) or _mapping_text(reference, "family") != "memory":
        raise AccessInvalidRequestError("artifact-reference")
    return ResourceRef.artifact(
        _nested_request_value(payload, "scope_id"),
        family="memory",
        artifact_id=_mapping_text(reference, "artifact_id"),
        selector=MemoryEntrySelector(
            entry_id=_mapping_text(citation, "entry_id"),
        ),
    )


def _exact_memory_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return ((AccessAction.ARTIFACT_READ, _memory_artifact_resource(payload)),)


def _exact_memory_write_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return ((AccessAction.ARTIFACT_WRITE, _memory_artifact_resource(payload)),)


def _commit_handoff_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    scope_id = _nested_request_value(payload, "scope_id")
    checks = [(AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id))]
    handoff = payload.get("handoff")
    base = handoff.get("base") if isinstance(handoff, Mapping) else None
    if isinstance(base, Mapping):
        checks.append((
            AccessAction.ARTIFACT_WRITE,
            ResourceRef.artifact(
                scope_id,
                family="handoff",
                artifact_id=_mapping_text(base, "artifact_id"),
            ),
        ))
    return tuple(checks)


def _candidate_write_access(
    payload: Mapping[str, Any],
    *,
    family: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    scope_id = _nested_request_value(payload, "scope_id")
    checks = [(AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id))]
    target = payload.get("target")
    if isinstance(target, Mapping):
        if _mapping_text(target, "family") != family:
            raise AccessInvalidRequestError("artifact-family")
        checks.append((
            AccessAction.ARTIFACT_WRITE,
            ResourceRef.artifact(
                scope_id,
                family=family,
                artifact_id=_mapping_text(target, "artifact_id"),
            ),
        ))
    return tuple(checks)


def _experience_candidate_write_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return _candidate_write_access(payload, family="experience")


def _skill_candidate_write_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return _candidate_write_access(payload, family="skill")


def _exact_experience_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return ((AccessAction.ARTIFACT_READ, _artifact_resource(payload, "artifact", family="experience")),)


def _exact_skill_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return ((AccessAction.ARTIFACT_READ, _artifact_resource(payload, "artifact", family="skill")),)


def _skill_identity_write_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return (
        (
            AccessAction.ARTIFACT_WRITE,
            ResourceRef.artifact(
                _nested_request_value(payload, "scope_id"),
                family="skill",
                artifact_id=_nested_request_value(payload, "artifact_id"),
            ),
        ),
    )


def _skill_usage_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    scope_id = _nested_request_value(payload, "scope_id")
    return (
        (AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id)),
        (AccessAction.ARTIFACT_READ, _artifact_resource(payload, "skill_ref", family="skill")),
    )


def _publish_remote_skill_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    scope_id = _nested_request_value(payload, "scope_id")
    return (
        (AccessAction.SCOPE_ADMIN, ResourceRef.scope(scope_id)),
        (AccessAction.ARTIFACT_READ, _artifact_resource(payload, "artifact", family="skill")),
    )


def _path_scope_access(
    payload: Mapping[str, Any],
    *,
    action: AccessAction,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return ((action, ResourceRef.scope(_nested_request_value(payload, "scope_id"))),)


def _path_scope_read_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return _path_scope_access(payload, action=AccessAction.SCOPE_READ)


def _path_scope_admin_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return _path_scope_access(payload, action=AccessAction.SCOPE_ADMIN)


def _path_artifact_access(
    payload: Mapping[str, Any],
    *,
    action: AccessAction,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    family = _path_artifact_family(payload)
    return (
        (
            action,
            ResourceRef.artifact(
                _nested_request_value(payload, "scope_id"),
                family=family,
                artifact_id=_nested_request_value(payload, "artifact_id"),
            ),
        ),
    )


def _path_artifact_family(payload: Mapping[str, Any]) -> str:
    try:
        return BaseArtifactFamily(_nested_request_value(payload, "family")).value
    except ValueError as error:
        raise AccessInvalidRequestError("artifact-family") from error


def _path_artifact_read_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    if _path_artifact_family(payload) == BaseArtifactFamily.MEMORY.value:
        return _path_scope_access(payload, action=AccessAction.SCOPE_READ)
    return _path_artifact_access(payload, action=AccessAction.ARTIFACT_READ)


def _path_artifact_write_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    if _path_artifact_family(payload) == BaseArtifactFamily.MEMORY.value:
        return _base_memory_write_access(payload)
    return _path_artifact_access(payload, action=AccessAction.ARTIFACT_WRITE)


def _base_memory_write_access(
    payload: Mapping[str, Any],
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    content = payload.get("content")
    entries = content.get("entries") if isinstance(content, Mapping) else None
    if not isinstance(entries, list) or not entries:
        raise AccessInvalidRequestError("resource")
    scope_id = _nested_request_value(payload, "scope_id")
    artifact_id = _nested_request_value(payload, "artifact_id")
    checks: list[tuple[AccessAction, ResourceRef]] = []
    if any(isinstance(entry, Mapping) and entry.get("entry_id") is None for entry in entries):
        checks.append((AccessAction.SCOPE_CONTRIBUTE, ResourceRef.scope(scope_id)))
    seen_entry_ids: set[str] = set()
    for entry in entries:
        entry_id = entry.get("entry_id") if isinstance(entry, Mapping) else None
        if entry_id is None:
            continue
        if not isinstance(entry_id, str) or not entry_id:
            raise AccessInvalidRequestError("memory-entry-selector")
        if entry_id in seen_entry_ids:
            continue
        seen_entry_ids.add(entry_id)
        checks.append((
            AccessAction.ARTIFACT_WRITE,
            ResourceRef.artifact(
                scope_id,
                family=BaseArtifactFamily.MEMORY.value,
                artifact_id=artifact_id,
                selector=MemoryEntrySelector(entry_id=entry_id),
            ),
        ))
    if not checks:
        raise AccessInvalidRequestError("resource")
    return tuple(checks)


def _scope_selection_read_access(
    payload: Mapping[str, Any],
    deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    selection = payload.get("selection")
    if not isinstance(selection, Mapping):
        raise AccessInvalidRequestError("scope-selection")
    mode = _mapping_text(selection, "mode")
    if mode != "exact":
        return ((AccessAction.SERVER_OBSERVE, ResourceRef.server(deployment_id)),)
    scope_ids = selection.get("scope_ids")
    if (
        not isinstance(scope_ids, list)
        or not scope_ids
        or not all(isinstance(item, str) and item for item in scope_ids)
    ):
        raise AccessInvalidRequestError("scope-selection")
    return tuple((AccessAction.SCOPE_READ, ResourceRef.scope(scope_id)) for scope_id in scope_ids)


def _publish_artifact_access(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    source = payload.get("source")
    if not isinstance(source, Mapping):
        raise AccessInvalidRequestError("artifact-reference")
    artifact = source.get("artifact")
    if not isinstance(artifact, Mapping):
        raise AccessInvalidRequestError("artifact-reference")
    source_resource = ResourceRef.artifact(
        _mapping_text(source, "scope_id"),
        family=_mapping_text(artifact, "family"),
        artifact_id=_mapping_text(artifact, "artifact_id"),
    )
    target_scope = ResourceRef.scope(_nested_request_value(payload, "target_scope_id"))
    return (
        (AccessAction.ARTIFACT_SHARE, source_resource),
        (AccessAction.SCOPE_ADMIN, target_scope),
    )


def _continue_handoff_resolver(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return _continue_handoff_access(payload)


def _acknowledge_handoff_resolver(
    payload: Mapping[str, Any],
    _deployment_id: str,
) -> tuple[tuple[AccessAction, ResourceRef], ...]:
    return _acknowledge_handoff_access(payload)


_NAMED_ACCESS_RESOLVERS: dict[
    str,
    Callable[[Mapping[str, Any], str], tuple[tuple[AccessAction, ResourceRef], ...]],
] = {
    "acknowledge_handoff_access": _acknowledge_handoff_resolver,
    "continue_handoff_access": _continue_handoff_resolver,
    "commit_handoff_access": _commit_handoff_access,
    "exact_memory_write_access": _exact_memory_write_access,
    "experience_candidate_write_access": _experience_candidate_write_access,
    "exact_experience_access": _exact_experience_access,
    "exact_memory_access": _exact_memory_access,
    "exact_skill_access": _exact_skill_access,
    "path_scope_admin_access": _path_scope_admin_access,
    "path_scope_read_access": _path_scope_read_access,
    "path_artifact_read_access": _path_artifact_read_access,
    "path_artifact_write_access": _path_artifact_write_access,
    "publish_artifact_access": _publish_artifact_access,
    "publish_remote_skill_access": _publish_remote_skill_access,
    "scope_selection_read_access": _scope_selection_read_access,
    "skill_candidate_write_access": _skill_candidate_write_access,
    "skill_identity_write_access": _skill_identity_write_access,
    "skill_usage_access": _skill_usage_access,
}


def _nested_request_value(payload: Mapping[str, Any], field: str | None) -> str:
    if not field:
        raise AccessInvalidRequestError("resource")
    value = payload
    for part in field.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
        if value is None:
            raise AccessInvalidRequestError("resource")
    text = str(value)
    if not text:
        raise AccessInvalidRequestError("resource")
    return text


def _mapping_text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise AccessInvalidRequestError("artifact-reference")
    return item


def _mapping_revision(value: Mapping[str, Any]) -> int:
    revision = value.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise AccessInvalidRequestError("artifact-reference")
    return revision


class _EncodedPathAPIRoute(APIRoute):
    """Match scoped resources against raw paths and decode each identity exactly once."""

    @override
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        raw_path = scope.get("raw_path")
        if scope["type"] != "http" or not isinstance(raw_path, bytes):
            return super().matches(scope)
        try:
            encoded_path = raw_path.decode("ascii")
        except UnicodeDecodeError:
            return super().matches(scope)
        encoded_scope = dict(scope)
        encoded_scope["path"] = encoded_path
        root_path = scope.get("root_path", "")
        if root_path:
            encoded_scope["root_path"] = quote(str(root_path), safe="/")
        match, child_scope = super().matches(cast(Scope, encoded_scope))
        if match is not Match.NONE:
            child_scope["path_params"] = {
                key: unquote(value) if isinstance(value, str) else value
                for key, value in child_scope.get("path_params", {}).items()
            }
        return match, child_scope


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
            await _validate_current_scope(app, operation, args, kwargs)
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
            diagnostic_error = None if _sensitive_operation_error(error) else error
            _log_operation(
                logging.ERROR if response_status >= status.HTTP_500_INTERNAL_SERVER_ERROR else logging.WARNING,
                "PowerContext application operation failed",
                operation=operation.operation_id,
                outcome="failure",
                started_at=started_at,
                error=diagnostic_error,
                error_code=error_code,
            )
            _finish_span(span, "failure", error=diagnostic_error)
            raise
        outcome = _application_outcome(result)
        _observe_application(app, operation, outcome, started_at)
        _finish_span(span, outcome)
        return result

    return observed_endpoint


def _sensitive_operation_error(error: Exception) -> bool:
    return isinstance(
        error,
        (
            AccessControlError,
            RemoteSkillDistributionError,
        ),
    )


async def _validate_current_scope(
    app: FastAPI,
    operation: Operation[Any, Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    if operation.scope_mode != "current" or operation.request_type is None:
        return
    scopes = getattr(app.state.application, "scopes", None)
    if scopes is None:
        return
    request = next(
        (value for value in (*args, *kwargs.values()) if isinstance(value, operation.request_type)),
        None,
    )
    scope_id = getattr(request, "scope_id", None)
    if isinstance(scope_id, str):
        await scopes.get(scope_id)


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
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=response_status, content=error.model_dump(mode="json"), headers=headers)


def _validation_error_details(error: RequestValidationError | PydanticValidationError) -> list[Any]:
    details: list[Any] = []
    for item in error.errors():
        if isinstance(item, dict):
            details.append({key: value for key, value in item.items() if key not in {"ctx", "input", "url"}})
        else:
            details.append(item)
    return details


def _map_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None]:
    access_error = _map_access_error(error)
    if access_error is not None:
        return access_error
    service_error = _map_service_error(error)
    return _map_domain_error(error) if service_error is None else service_error


def _map_service_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:  # noqa: C901
    if isinstance(error, _RuntimeNotReadyError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "runtime_not_ready", "The Runtime is not ready.", None
    base_access_error = _map_base_access_error(error)
    if base_access_error is not None:
        return base_access_error
    external_skill_error = _map_external_skill_error(error)
    if external_skill_error is not None:
        return external_skill_error
    remote_skill_error = _map_remote_skill_error(error)
    if remote_skill_error is not None:
        return remote_skill_error
    if isinstance(error, GenerationCapabilityUnavailableError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "generation_unavailable",
            "Artifact generation is not configured.",
            {"family": error.family},
        )
    governance_error = _map_governance_error(error)
    if governance_error is not None:
        return governance_error
    scope_error = _map_scope_error(error)
    if scope_error is not None:
        return scope_error
    candidate_error = _map_candidate_error(error)
    if candidate_error is not None:
        return candidate_error
    availability_error = _map_availability_error(error)
    if availability_error is not None:
        return availability_error
    report_error = _map_report_error(error)
    if report_error is not None:
        return report_error
    return None


def _map_external_skill_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
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
    return None


def _map_remote_skill_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, RemoteTargetAuthenticationError):
        return status.HTTP_401_UNAUTHORIZED, error.code, "The target credential is invalid or revoked.", None
    if isinstance(error, RemoteTargetEnrollmentError):
        return status.HTTP_409_CONFLICT, error.code, "The enrollment cannot be completed.", None
    if isinstance(error, RemotePublicationGenerationError):
        return status.HTTP_409_CONFLICT, error.code, "The remote publication generation is stale.", None
    if isinstance(error, RemoteSkillLifecycleError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, error.code, "The Skill lifecycle rejects publication.", None
    if isinstance(error, RemoteTargetStateError):
        return status.HTTP_409_CONFLICT, error.code, "The remote target state rejects this operation.", None
    if isinstance(error, RemoteSkillDistributionError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, error.code, "The remote Skill request is invalid.", None
    return None


def _map_governance_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, RepositoryNotFoundError):
        return status.HTTP_404_NOT_FOUND, "not_found", "The requested value was not found.", None
    if isinstance(error, StoredPayloadConflictError):
        return status.HTTP_409_CONFLICT, "generation_conflict", "The requested state is stale.", None
    if isinstance(error, InvalidArtifactLifecycleError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_lifecycle", str(error), None
    return None


def _map_access_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, AccessIdentityRequiredError):
        return status.HTTP_401_UNAUTHORIZED, "unauthorized", "An authenticated Principal is required.", None
    if isinstance(error, AccessDeniedError):
        return status.HTTP_403_FORBIDDEN, "forbidden", "The Principal is not authorized for this operation.", None
    if isinstance(error, AccessBindingNotFoundError):
        return status.HTTP_404_NOT_FOUND, "access_binding_not_found", "The Access Binding was not found.", None
    if isinstance(error, AccessConflictError):
        return status.HTTP_409_CONFLICT, error.code, "The Access Binding conflicts with current state.", None
    if isinstance(error, AccessInvalidRequestError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_access_request", "The Access request is invalid.", None
    if isinstance(error, AccessUnavailableError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, error.code, "Access Control is unavailable.", None
    return None


def _map_base_access_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, SourceNotEligibleError):
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "source_not_eligible",
            "The Source is reserved for its bound Artifact creation lineage.",
            {"source": error.source.model_dump(mode="json")},
        )
    if isinstance(error, _PreconditionRequiredError):
        return (
            status.HTTP_428_PRECONDITION_REQUIRED,
            "precondition_required",
            "Artifact mutation requires the current ETag in If-Match.",
            None,
        )
    if isinstance(error, ArtifactRevisionPreconditionError):
        return (
            status.HTTP_412_PRECONDITION_FAILED,
            "revision_conflict",
            "Artifact ETag does not match the current head.",
            {
                "provided_etag": error.provided_etag,
                "current_etag": error.current_etag,
            },
        )
    if isinstance(error, BaseValueNotFoundError):
        return (
            status.HTTP_404_NOT_FOUND,
            f"{error.kind}_not_found",
            f"The requested {error.kind.capitalize()} was not found.",
            None,
        )
    if isinstance(error, BaseValueConflictError):
        return (
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            "The stable identity already names different durable state.",
            {"kind": error.kind},
        )
    if isinstance(error, ArtifactAlreadyExistsError):
        return (
            status.HTTP_409_CONFLICT,
            "artifact_already_exists",
            "The Scope already has this singleton Artifact; use Replace Artifact to update it.",
            {
                "family": error.family,
                "artifact_id": error.artifact_id,
                "use_replace": error.use_replace,
            },
        )
    if isinstance(error, CursorExpiredError):
        return (
            status.HTTP_410_GONE,
            "cursor_expired",
            "The pagination cursor has expired.",
            None,
        )
    if isinstance(error, InvalidCursorError):
        return (
            status.HTTP_400_BAD_REQUEST,
            "invalid_cursor",
            "The pagination cursor is invalid or does not match this request.",
            {"field": error.field, "reason": error.reason},
        )
    if isinstance(error, InvalidBaseAccessRequestError):
        response_status = (
            status.HTTP_400_BAD_REQUEST
            if error.field in {"If-Match", "limit", "mode"}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        return (
            response_status,
            "invalid_request",
            "The request is invalid.",
            {"field": error.field, "reason": error.reason},
        )
    return None


def _map_scope_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, ArtifactPublicationUnsupportedError):
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "artifact_publication_unsupported",
            "The Artifact family cannot be published as complete target state.",
            {"family": error.family},
        )
    if isinstance(error, ArtifactPublicationConflictError):
        return (
            status.HTTP_409_CONFLICT,
            "artifact_publication_conflict",
            "The publication key identifies a different source Artifact.",
            None,
        )
    if isinstance(error, (ScopeNotFoundError, ScopeBindingNotFoundError)):
        return status.HTTP_404_NOT_FOUND, "scope_not_found", "The requested Scope was not found.", None
    if isinstance(error, ScopeVersionConflictError):
        return (
            status.HTTP_409_CONFLICT,
            "scope_version_conflict",
            "The Scope metadata version is stale.",
            {"expected_version": error.expected, "current_version": error.actual},
        )
    if isinstance(error, ScopeIdempotencyConflictError):
        return (
            status.HTTP_409_CONFLICT,
            "scope_idempotency_conflict",
            "The Scope creation key identifies different parameters.",
            None,
        )
    if isinstance(error, ScopeRelationshipError):
        return (
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_scope_relationship",
            "The Scope relationship is invalid.",
            {"relationship": error.relationship, "issue": error.issue},
        )
    return None


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
    if isinstance(error, HandoffReportTooLargeError):
        return (
            status.HTTP_413_CONTENT_TOO_LARGE,
            "handoff_report_too_large",
            "The Handoff Report is too large; narrow the Scope selection.",
            {
                "estimated_bytes": error.estimated_bytes,
                "selected_scopes": error.selected_scopes,
            },
        )
    if isinstance(error, HandoffReportInconsistentError):
        return (
            status.HTTP_409_CONFLICT,
            "handoff_report_inconsistent",
            "The frozen Handoff selection could not be read consistently.",
            {"scope_id": error.scope_id},
        )
    if isinstance(error, HandoffReportError):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "handoff_report_unavailable", "Handoff Report is unavailable.", None
    return None


def _map_domain_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None]:
    source_ingestion = _map_source_ingestion_error(error)
    if source_ingestion is not None:
        return source_ingestion
    if isinstance(error, ArtifactNotFoundError):
        return status.HTTP_404_NOT_FOUND, "artifact_not_found", "The requested Artifact was not found.", None
    if isinstance(error, MemoryEntryNotFoundError):
        return status.HTTP_404_NOT_FOUND, "memory_not_found", "The requested Memory value was not found.", None
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


def _map_source_ingestion_error(error: Exception) -> tuple[int, str, str, dict[str, Any] | None] | None:
    if isinstance(error, SourceConflictError):
        return status.HTTP_409_CONFLICT, "source_conflict", "The Source identity has different content.", None
    if isinstance(error, InvalidConnectorRunError):
        return status.HTTP_409_CONFLICT, "connector_checkpoint_conflict", "The Connector checkpoint is stale.", None
    if isinstance(error, SourceDefinitionNotFoundError):
        return (
            status.HTTP_404_NOT_FOUND,
            "source_definition_not_found",
            "The Source Definition is not registered.",
            None,
        )
    if isinstance(error, (InvalidSourceDefinitionError, InvalidSourceObservationError)):
        return status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_source_ingestion", "Source ingestion is invalid.", None
    return None


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
