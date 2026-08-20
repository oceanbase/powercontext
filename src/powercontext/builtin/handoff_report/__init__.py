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

"""Optional, read-only Handoff Report domain values."""

from powercontext.builtin.handoff_report.adapters import RuntimeHandoffReadAdapter, RuntimeWorkContinuityReadAdapter
from powercontext.builtin.handoff_report.application import (
    HandoffReportApplication,
    ReportActivityPage,
    ReportPeriodInput,
)
from powercontext.builtin.handoff_report.canonical import (
    ReportCanonicalizationError,
    canonical_json_bytes,
    finalize_digests,
    report_digest,
    selection_digest,
    selection_envelope,
)
from powercontext.builtin.handoff_report.catalog import HandoffReportCatalog, ProjectIdFactory
from powercontext.builtin.handoff_report.catalog_store import (
    DEFAULT_CATALOG_PAGE_SIZE,
    HANDOFF_REPORT_CATALOG_TABLES,
    MAX_CATALOG_PAGE_SIZE,
    CatalogPage,
    ReportCatalogRepository,
)
from powercontext.builtin.handoff_report.errors import (
    HandoffReportBusyError,
    HandoffReportCatalogArgumentError,
    HandoffReportError,
    HandoffReportEvidenceCheckUnavailableError,
    HandoffReportInconsistentError,
    HandoffReportTooLargeError,
    InvalidStoredCatalogError,
    ProjectConflictError,
    ProjectNotFoundError,
    ScopeAlreadyGroupedError,
    WorkspaceBindingConflictError,
    WorkspaceBindingNotFoundError,
    WorkstreamConflictError,
    WorkstreamNotFoundError,
)
from powercontext.builtin.handoff_report.models import (
    ActivityAgent,
    ActivityVcsContext,
    CatalogState,
    ExternalReference,
    ExternalReferenceKind,
    GeneratedSummaryTrust,
    HandoffReportTrust,
    ProjectDescriptor,
    ReportActivityEvent,
    ReportActivitySource,
    ReportActivityTrust,
    ReportLocale,
    ReportSelectionConsistency,
    ReportSelectionEntry,
    ReportSelectionStatus,
    ReportTimeBasis,
    RepositoryProvider,
    RepositoryRef,
    WorkspaceBinding,
    WorkspaceBindingState,
    WorkstreamDescriptor,
    WorkstreamKind,
    activity_sort_key,
    normalize_repository_ref,
    normalized_sort_text,
    selection_sort_key,
    workstream_sort_key,
)
from powercontext.builtin.handoff_report.rendering import render_markdown
from powercontext.builtin.handoff_report.report import (
    HandoffReport,
    HandoffRevisionSummary,
    ReportActivityCoverageStatus,
    ReportActivityStatus,
    ReportCoverage,
    ReportEvidenceChecks,
    ReportFormat,
    ReportHandoffActivityRelation,
    ReportKind,
    ReportPeriodComparison,
    ReportReportingStatus,
    ReportSummary,
    ReportWorkStatus,
    WorkstreamReport,
)
from powercontext.builtin.handoff_report.selection import select_optimistic_stable_handoffs
from powercontext.builtin.handoff_report.service import HandoffReportService
from powercontext.builtin.handoff_report.workspace import WorkspaceBindingService
from powercontext.builtin.handoff_report.workspace_store import (
    HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE,
    HANDOFF_REPORT_WORKSPACE_TABLES,
    WorkspaceBindingRepository,
)

__all__ = [
    "DEFAULT_CATALOG_PAGE_SIZE",
    "HANDOFF_REPORT_CATALOG_TABLES",
    "HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE",
    "HANDOFF_REPORT_WORKSPACE_TABLES",
    "MAX_CATALOG_PAGE_SIZE",
    "ActivityAgent",
    "ActivityVcsContext",
    "CatalogPage",
    "CatalogState",
    "ExternalReference",
    "ExternalReferenceKind",
    "GeneratedSummaryTrust",
    "HandoffReport",
    "HandoffReportApplication",
    "HandoffReportBusyError",
    "HandoffReportCatalog",
    "HandoffReportCatalogArgumentError",
    "HandoffReportError",
    "HandoffReportEvidenceCheckUnavailableError",
    "HandoffReportInconsistentError",
    "HandoffReportService",
    "HandoffReportTooLargeError",
    "HandoffReportTrust",
    "HandoffRevisionSummary",
    "InvalidStoredCatalogError",
    "ProjectConflictError",
    "ProjectDescriptor",
    "ProjectIdFactory",
    "ProjectNotFoundError",
    "ReportActivityCoverageStatus",
    "ReportActivityEvent",
    "ReportActivityPage",
    "ReportActivitySource",
    "ReportActivityStatus",
    "ReportActivityTrust",
    "ReportCanonicalizationError",
    "ReportCatalogRepository",
    "ReportCoverage",
    "ReportEvidenceChecks",
    "ReportFormat",
    "ReportHandoffActivityRelation",
    "ReportKind",
    "ReportLocale",
    "ReportPeriodComparison",
    "ReportPeriodInput",
    "ReportReportingStatus",
    "ReportSelectionConsistency",
    "ReportSelectionEntry",
    "ReportSelectionStatus",
    "ReportSummary",
    "ReportTimeBasis",
    "ReportWorkStatus",
    "RepositoryProvider",
    "RepositoryRef",
    "RuntimeHandoffReadAdapter",
    "RuntimeWorkContinuityReadAdapter",
    "ScopeAlreadyGroupedError",
    "WorkspaceBinding",
    "WorkspaceBindingConflictError",
    "WorkspaceBindingNotFoundError",
    "WorkspaceBindingRepository",
    "WorkspaceBindingService",
    "WorkspaceBindingState",
    "WorkstreamConflictError",
    "WorkstreamDescriptor",
    "WorkstreamKind",
    "WorkstreamNotFoundError",
    "WorkstreamReport",
    "activity_sort_key",
    "canonical_json_bytes",
    "finalize_digests",
    "normalize_repository_ref",
    "normalized_sort_text",
    "render_markdown",
    "report_digest",
    "select_optimistic_stable_handoffs",
    "selection_digest",
    "selection_envelope",
    "selection_sort_key",
    "workstream_sort_key",
]
