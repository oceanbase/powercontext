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

"""Canonical output values for the optional Handoff Report feature."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import HandoffContent, HandoffDisposition, HandoffEvidenceCheck
from powercontext.builtin.handoff_report.models import (
    HandoffReportTrust,
    ProjectDescriptor,
    ReportActivityEvent,
    ReportLocale,
    ReportSelectionConsistency,
    ReportSelectionEntry,
    WorkstreamDescriptor,
)
from powercontext.builtin.work import WorkContinuity

ReportEvidenceChecks: TypeAlias = tuple[HandoffEvidenceCheck, ...] | Literal["not_checked"]
ReportActivityCoverageStatus: TypeAlias = Literal["not_configured", "captured", "unavailable"]
ReportFormat: TypeAlias = Literal["json", "markdown"]
ReportKind: TypeAlias = Literal["handoff", "periodic"]
ReportWorkStatus: TypeAlias = Literal["continuable", "blocked", "complete", "no_handoff"]
ReportActivityStatus: TypeAlias = Literal[
    "no_observed_activity",
    "activity_after_handoff",
    "activity_without_handoff",
    "current_only",
    "unknown",
]
ReportReportingStatus: TypeAlias = Literal[
    "reported",
    "reported_with_omissions",
    "evidence_unavailable",
    "no_handoff",
]
ReportHandoffActivityRelation: TypeAlias = Literal[
    "activity_after_handoff",
    "no_observed_activity_after_handoff",
    "unknown",
]
ReportHandoffBoundaryCoverage: TypeAlias = Literal["unavailable"]
MAX_REPORT_WORKSTREAMS = 100
MAX_REPORT_ACTIVITIES = 5_000
MAX_REPORT_HANDOFF_HISTORY = 20
MAX_REPORT_HANDOFF_HISTORY_EXCERPT_LENGTH = 240


class _ReportOutputValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReportCoverage(_ReportOutputValue):
    """Counts and explicit adapter coverage for one frozen report."""

    total_included_workstreams: StrictInt = Field(ge=0)
    catalog_matched_workstreams: StrictInt = Field(default=0, ge=0)
    selected_workstreams: StrictInt = Field(ge=0)
    missing_handoff_workstreams: StrictInt = Field(ge=0)
    reported_with_omissions: StrictInt = Field(ge=0)
    unchecked_evidence_workstreams: StrictInt = Field(default=0, ge=0)
    unavailable_evidence_workstreams: StrictInt = Field(ge=0)
    activity_without_handoff_workstreams: StrictInt = Field(ge=0)
    activity_after_handoff_workstreams: StrictInt = Field(default=0, ge=0)
    unknown_time_events: StrictInt = Field(default=0, ge=0)
    unassigned_activity_count: StrictInt = Field(ge=0)
    unassigned_activity_events: StrictInt = Field(default=0, ge=0)
    activity_coverage: ReportActivityCoverageStatus


class ReportSummary(_ReportOutputValue):
    """Deterministic work-status counts derived from exact Handoff content."""

    continuable_count: StrictInt = Field(ge=0)
    blocked_count: StrictInt = Field(ge=0)
    complete_count: StrictInt = Field(ge=0)
    no_handoff_count: StrictInt = Field(ge=0)


class ReportPeriodComparison(_ReportOutputValue):
    """Truthful Activity comparison when Handoff boundary time is unavailable."""

    previous_start: datetime
    previous_end: datetime
    current_activity_count: StrictInt = Field(ge=0)
    previous_activity_count: StrictInt = Field(ge=0)
    activity_delta: StrictInt
    handoff_boundary_coverage: ReportHandoffBoundaryCoverage = "unavailable"

    @field_validator("previous_start", "previous_end")
    @classmethod
    def require_aware_boundary(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("period comparison boundaries must be timezone-aware")  # noqa: TRY003
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_comparison(self) -> ReportPeriodComparison:
        if self.previous_start >= self.previous_end:
            raise ValueError("previous period start must precede its end")  # noqa: TRY003
        if self.activity_delta != self.current_activity_count - self.previous_activity_count:
            raise ValueError("activity_delta must match current minus previous Activity count")  # noqa: TRY003
        return self


class HandoffRevisionSummary(_ReportOutputValue):
    """Bounded display metadata for one committed Handoff Revision."""

    reference: ArtifactRef
    objective_excerpt: Annotated[str, Field(max_length=MAX_REPORT_HANDOFF_HISTORY_EXCERPT_LENGTH)]
    disposition: HandoffDisposition
    next_action_excerpt: Annotated[str | None, Field(max_length=MAX_REPORT_HANDOFF_HISTORY_EXCERPT_LENGTH)] = None
    state_count: StrictInt = Field(ge=1)
    omission_count: StrictInt = Field(ge=0)


class WorkstreamReport(_ReportOutputValue):
    """One Workstream projected from an exact Handoff selection."""

    workstream: WorkstreamDescriptor
    continuity: WorkContinuity
    handoff_ref: ArtifactRef | None
    content: HandoffContent | None
    handoff_revision_count: StrictInt = Field(default=0, ge=0)
    handoff_history_truncated: bool = False
    handoff_history: Annotated[tuple[HandoffRevisionSummary, ...], Field(max_length=MAX_REPORT_HANDOFF_HISTORY)] = ()
    evidence_checks: ReportEvidenceChecks = "not_checked"
    evidence_unavailable: bool = False
    activities: Annotated[tuple[ReportActivityEvent, ...], Field(max_length=MAX_REPORT_ACTIVITIES)] = ()
    work_status: ReportWorkStatus
    reporting_status: ReportReportingStatus
    activity_status: ReportActivityStatus
    handoff_activity_relation: ReportHandoffActivityRelation | None = None
    observed_activity_count: StrictInt = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_handoff_projection(self) -> WorkstreamReport:
        if self.continuity.scope_id != self.workstream.scope_id:
            raise ValueError("continuity scope must match its Workstream")  # noqa: TRY003
        if self.handoff_ref is None:
            _validate_no_handoff(self)
        else:
            _validate_selected_handoff(self)
        if self.observed_activity_count != len(self.activities):
            raise ValueError("observed_activity_count must match the Workstream activity count")  # noqa: TRY003
        return self


def _validate_no_handoff(report: WorkstreamReport) -> None:
    if report.content is not None:
        raise ValueError("a Workstream without Handoff cannot contain Handoff content")  # noqa: TRY003
    if report.evidence_checks != "not_checked":
        raise ValueError("a Workstream without Handoff cannot contain evidence checks")  # noqa: TRY003
    if report.evidence_unavailable:
        raise ValueError("a Workstream without Handoff cannot have unavailable evidence checks")  # noqa: TRY003
    if report.work_status != "no_handoff":
        raise ValueError("a Workstream without Handoff must have no_handoff work status")  # noqa: TRY003
    if report.reporting_status != "no_handoff":
        raise ValueError("a Workstream without Handoff must report missing Handoff state")  # noqa: TRY003
    if report.handoff_revision_count != 0 or report.handoff_history or report.handoff_history_truncated:
        raise ValueError("a Workstream without Handoff cannot contain Handoff Revision history")  # noqa: TRY003


def _validate_selected_handoff(report: WorkstreamReport) -> None:
    if report.content is None:
        raise ValueError("an exact Handoff selection must contain Handoff content")  # noqa: TRY003
    if report.evidence_unavailable and report.evidence_checks != "not_checked":
        raise ValueError("an unavailable evidence check must remain not_checked")  # noqa: TRY003
    if report.work_status != report.content.disposition:
        raise ValueError("work status must match the exact Handoff disposition")  # noqa: TRY003
    if report.reporting_status == "no_handoff":
        raise ValueError("an exact Handoff selection cannot report missing Handoff state")  # noqa: TRY003
    _validate_handoff_history(report)


def _validate_handoff_history(report: WorkstreamReport) -> None:
    handoff_ref = report.handoff_ref
    if handoff_ref is None:
        raise ValueError("Handoff Revision history requires an exact selected Handoff")  # noqa: TRY003
    if not report.handoff_history:
        raise ValueError("an exact Handoff selection must contain Handoff Revision history")  # noqa: TRY003
    if report.handoff_history[-1].reference != handoff_ref:
        raise ValueError("Handoff Revision history must end at the exact selected Handoff")  # noqa: TRY003
    if report.handoff_revision_count < len(report.handoff_history):
        raise ValueError("Handoff Revision count cannot be smaller than its projected history")  # noqa: TRY003
    if report.handoff_history_truncated != (report.handoff_revision_count > len(report.handoff_history)):
        raise ValueError("Handoff Revision truncation must match its projected history")  # noqa: TRY003
    references = tuple(item.reference for item in report.handoff_history)
    if any(
        reference.family != handoff_ref.family or reference.artifact_id != handoff_ref.artifact_id
        for reference in references
    ):
        raise ValueError("Handoff Revision history must belong to the selected Artifact lifecycle")  # noqa: TRY003
    if tuple(reference.revision for reference in references) != tuple(
        sorted({reference.revision for reference in references})
    ):
        raise ValueError("Handoff Revision history must be unique and ascending")  # noqa: TRY003


class HandoffReport(_ReportOutputValue):
    """Language-neutral canonical report used by renderers and Agents."""

    schema_version: Literal["powercontext.handoff-report.v1"] = Field(
        default="powercontext.handoff-report.v1",
        alias="schema",
    )
    trust: HandoffReportTrust = "untrusted_history"
    locale: ReportLocale
    format: ReportFormat = "json"
    report_kind: ReportKind = "handoff"
    renderer_version: str = "canonical-v1"
    generated_at: datetime
    selection_consistency: ReportSelectionConsistency
    project: ProjectDescriptor
    project_revision: StrictInt = Field(default=1, ge=1)
    normalized_filters: dict[str, JsonValue] = Field(default_factory=dict)
    normalized_period: dict[str, JsonValue] | None = None
    period_comparison: ReportPeriodComparison | None = None
    baseline_selection: Annotated[tuple[ReportSelectionEntry, ...], Field(max_length=MAX_REPORT_WORKSTREAMS)] | None = (
        None
    )
    end_selection: Annotated[tuple[ReportSelectionEntry, ...], Field(max_length=MAX_REPORT_WORKSTREAMS)]
    activity_cursor: StrictInt = Field(ge=0)
    activity_selection: Annotated[tuple[str, ...], Field(max_length=MAX_REPORT_ACTIVITIES)] = ()
    selection_digest: str | None = Field(default=None, pattern=r"sha256:[0-9a-f]{64}")
    report_digest: str | None = Field(default=None, pattern=r"sha256:[0-9a-f]{64}")
    coverage: ReportCoverage
    summary: ReportSummary
    unassigned_activity: Annotated[tuple[ReportActivityEvent, ...], Field(max_length=MAX_REPORT_ACTIVITIES)] = ()
    workstreams: Annotated[tuple[WorkstreamReport, ...], Field(max_length=MAX_REPORT_WORKSTREAMS)]

    @field_validator("generated_at")
    @classmethod
    def require_utc_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")  # noqa: TRY003
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_selection_projection(self) -> HandoffReport:
        _validate_scope_projection(self)
        if self.project_revision != self.project.version:
            raise ValueError("project_revision must match the projected Project descriptor")  # noqa: TRY003
        if self.coverage.selected_workstreams != len(self.workstreams):
            raise ValueError("selected_workstreams must match Workstream report count")  # noqa: TRY003
        _validate_activity_projection(self)
        _validate_coverage_projection(self)
        _validate_summary_projection(self)
        if self.report_kind == "periodic" and self.normalized_period is None:
            raise ValueError("a periodic report must contain a normalized period")  # noqa: TRY003
        if self.report_kind == "handoff" and (self.normalized_period is not None or self.period_comparison is not None):
            raise ValueError("a point-in-time Handoff report cannot contain period values")  # noqa: TRY003
        if self.period_comparison is not None and self.report_kind != "periodic":
            raise ValueError("period comparison is only valid for a periodic report")  # noqa: TRY003
        return self


def _validate_scope_projection(report: HandoffReport) -> None:
    selection_scopes = tuple(entry.scope_id for entry in report.end_selection)
    report_scopes = tuple(item.workstream.scope_id for item in report.workstreams)
    if len(set(selection_scopes)) != len(selection_scopes):
        raise ValueError("Handoff Report selection scopes must be unique")  # noqa: TRY003
    if len(set(report_scopes)) != len(report_scopes):
        raise ValueError("Handoff Report Workstream scopes must be unique")  # noqa: TRY003
    if selection_scopes != report_scopes:
        raise ValueError("Workstream reports must exactly match selection scope order")  # noqa: TRY003
    for entry, item in zip(report.end_selection, report.workstreams, strict=True):
        if item.workstream.project_id != report.project.project_id:
            raise ValueError("every Workstream report must belong to the Report Project")  # noqa: TRY003
        if entry.workstream_revision != item.workstream.version:
            raise ValueError("selection Workstream revision must match the projected descriptor")  # noqa: TRY003
        if entry.handoff_ref != item.handoff_ref:
            raise ValueError("selection Handoff reference must match the Workstream report")  # noqa: TRY003


def _validate_activity_projection(report: HandoffReport) -> None:
    known_scopes = {item.workstream.scope_id for item in report.workstreams}
    assigned_activity: list[ReportActivityEvent] = []
    for item in report.workstreams:
        for event in item.activities:
            if event.project_id != report.project.project_id:
                raise ValueError("every assigned Activity Event must belong to the Report Project")  # noqa: TRY003
            if event.scope_id != item.workstream.scope_id:
                raise ValueError("assigned Activity Event scope must match its Workstream report")  # noqa: TRY003
            assigned_activity.append(event)
    for event in report.unassigned_activity:
        if event.project_id != report.project.project_id:
            raise ValueError("every unassigned Activity Event must belong to the Report Project")  # noqa: TRY003
        if event.scope_id in known_scopes:
            raise ValueError("Activity Event for a selected scope cannot be unassigned")  # noqa: TRY003
    activity_ids = tuple(event.event_id for event in (*assigned_activity, *report.unassigned_activity))
    if len(set(activity_ids)) != len(activity_ids):
        raise ValueError("Activity Event ids must be unique within a Handoff Report")  # noqa: TRY003
    if report.activity_selection != activity_ids:
        raise ValueError("activity_selection must match projected Activity Event order")  # noqa: TRY003


def _validate_coverage_projection(report: HandoffReport) -> None:
    if report.coverage.total_included_workstreams < len(report.workstreams):
        raise ValueError("total_included_workstreams cannot be smaller than the selected report")  # noqa: TRY003
    if report.coverage.catalog_matched_workstreams > report.coverage.total_included_workstreams:
        raise ValueError("catalog_matched_workstreams cannot exceed total_included_workstreams")  # noqa: TRY003
    all_events = tuple(event for item in report.workstreams for event in item.activities) + report.unassigned_activity
    expected = {
        "missing_handoff_workstreams": sum(item.handoff_ref is None for item in report.workstreams),
        "reported_with_omissions": sum(
            item.reporting_status == "reported_with_omissions" for item in report.workstreams
        ),
        "unchecked_evidence_workstreams": sum(
            item.handoff_ref is not None and item.evidence_checks == "not_checked" for item in report.workstreams
        ),
        "unavailable_evidence_workstreams": sum(
            item.evidence_unavailable
            or (
                item.evidence_checks != "not_checked"
                and any(check.status == "unavailable" for check in item.evidence_checks)
            )
            for item in report.workstreams
        ),
        "activity_without_handoff_workstreams": sum(
            item.activity_status == "activity_without_handoff" for item in report.workstreams
        ),
        "activity_after_handoff_workstreams": sum(
            item.handoff_activity_relation == "activity_after_handoff" for item in report.workstreams
        ),
        "unknown_time_events": sum(event.effective_period_time() is None for event in all_events),
        "unassigned_activity_count": len(report.unassigned_activity),
        "unassigned_activity_events": len(report.unassigned_activity),
    }
    for field, value in expected.items():
        if getattr(report.coverage, field) != value:
            raise ValueError(f"{field} must match the canonical report projection")  # noqa: TRY003


def _validate_summary_projection(report: HandoffReport) -> None:
    expected = {
        "continuable_count": sum(item.work_status == "continuable" for item in report.workstreams),
        "blocked_count": sum(item.work_status == "blocked" for item in report.workstreams),
        "complete_count": sum(item.work_status == "complete" for item in report.workstreams),
        "no_handoff_count": sum(item.work_status == "no_handoff" for item in report.workstreams),
    }
    for field, value in expected.items():
        if getattr(report.summary, field) != value:
            raise ValueError(f"{field} must match the canonical report projection")  # noqa: TRY003


__all__ = [
    "MAX_REPORT_ACTIVITIES",
    "MAX_REPORT_WORKSTREAMS",
    "HandoffReport",
    "ReportActivityCoverageStatus",
    "ReportActivityStatus",
    "ReportCoverage",
    "ReportEvidenceChecks",
    "ReportFormat",
    "ReportHandoffActivityRelation",
    "ReportHandoffBoundaryCoverage",
    "ReportKind",
    "ReportPeriodComparison",
    "ReportReportingStatus",
    "ReportSummary",
    "ReportWorkStatus",
    "WorkstreamReport",
]
