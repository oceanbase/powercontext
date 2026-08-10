"""Read-only assembly of canonical Handoff Reports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import JsonValue

from powercontext.builtin.handoff_report.canonical import finalize_digests
from powercontext.builtin.handoff_report.errors import (
    HandoffReportEvidenceCheckUnavailableError,
    HandoffReportInconsistentError,
)
from powercontext.builtin.handoff_report.models import (
    ProjectDescriptor,
    ReportActivityEvent,
    ReportLocale,
    WorkstreamDescriptor,
    activity_sort_key,
)
from powercontext.builtin.handoff_report.protocols import HandoffReadAdapter
from powercontext.builtin.handoff_report.report import (
    MAX_REPORT_ACTIVITIES,
    MAX_REPORT_WORKSTREAMS,
    HandoffReport,
    ReportActivityCoverageStatus,
    ReportActivityStatus,
    ReportCoverage,
    ReportEvidenceChecks,
    ReportFormat,
    ReportKind,
    ReportPeriodComparison,
    ReportReportingStatus,
    ReportSummary,
    WorkstreamReport,
)
from powercontext.builtin.handoff_report.selection import select_optimistic_stable_handoffs


class HandoffReportService:
    """Assemble reports without entering Handoff prepare, commit, or Continue control flow."""

    def __init__(self, handoffs: HandoffReadAdapter, /) -> None:
        self._handoffs = handoffs

    async def generate(
        self,
        project: ProjectDescriptor,
        workstreams: Sequence[WorkstreamDescriptor],
        /,
        *,
        locale: ReportLocale | None = None,
        include_evidence_checks: bool = True,
        activities: Sequence[ReportActivityEvent] = (),
        activity_cursor: int = 0,
        activity_coverage: ReportActivityCoverageStatus = "not_configured",
        generated_at: datetime | None = None,
        selection_attempts: int = 3,
        report_format: ReportFormat = "json",
        report_kind: ReportKind = "handoff",
        normalized_filters: dict[str, JsonValue] | None = None,
        normalized_period: dict[str, JsonValue] | None = None,
        period_comparison: ReportPeriodComparison | None = None,
    ) -> HandoffReport:
        """Freeze exact heads and project only exact Handoffs plus explicit Activity Events."""

        ordered_workstreams = _validate_inputs(project, workstreams, activities, activity_cursor)
        selection = await select_optimistic_stable_handoffs(
            self._handoffs,
            ordered_workstreams,
            attempts=selection_attempts,
        )
        activities_by_scope, unassigned = _group_activities(activities, ordered_workstreams)
        projected: list[WorkstreamReport] = []
        for descriptor, entry in zip(ordered_workstreams, selection, strict=True):
            scoped_activity = activities_by_scope.get(descriptor.scope_id, ())
            if entry.handoff_ref is None:
                projected.append(
                    WorkstreamReport(
                        workstream=descriptor,
                        handoff_ref=None,
                        content=None,
                        activities=scoped_activity,
                        work_status="no_handoff",
                        reporting_status="no_handoff",
                        activity_status=("activity_without_handoff" if scoped_activity else "no_observed_activity"),
                        handoff_activity_relation=None,
                        observed_activity_count=len(scoped_activity),
                    )
                )
                continue

            handoff = await self._handoffs.get(descriptor.scope_id, entry.handoff_ref)
            if handoff.as_ref() != entry.handoff_ref:
                raise HandoffReportInconsistentError(descriptor.scope_id)
            checks: ReportEvidenceChecks = "not_checked"
            evidence_unavailable = False
            if include_evidence_checks:
                try:
                    checks = await self._handoffs.check_evidence(descriptor.scope_id, entry.handoff_ref)
                except HandoffReportEvidenceCheckUnavailableError:
                    evidence_unavailable = True
            projected.append(
                WorkstreamReport(
                    workstream=descriptor,
                    handoff_ref=entry.handoff_ref,
                    content=handoff.content,
                    evidence_checks=checks,
                    evidence_unavailable=evidence_unavailable,
                    activities=scoped_activity,
                    work_status=handoff.content.disposition,
                    reporting_status=_reporting_status(handoff.content.omissions, checks, evidence_unavailable),
                    activity_status=_activity_status(scoped_activity),
                    handoff_activity_relation=(None if not scoped_activity else "unknown"),
                    observed_activity_count=len(scoped_activity),
                )
            )

        reports = tuple(projected)
        activity_selection = tuple(event.event_id for report in reports for event in report.activities) + tuple(
            event.event_id for event in unassigned
        )
        report = HandoffReport(
            locale=project.default_locale if locale is None else locale,
            format=report_format,
            report_kind=report_kind,
            renderer_version="canonical-v1" if report_format == "json" else "markdown-v1",
            generated_at=datetime.now(UTC) if generated_at is None else generated_at,
            selection_consistency="optimistic_stable",
            project=project,
            project_revision=project.version,
            normalized_filters={} if normalized_filters is None else normalized_filters,
            normalized_period=normalized_period,
            period_comparison=period_comparison,
            end_selection=selection,
            activity_cursor=activity_cursor,
            activity_selection=activity_selection,
            coverage=ReportCoverage(
                total_included_workstreams=len(ordered_workstreams),
                catalog_matched_workstreams=len(ordered_workstreams),
                selected_workstreams=len(reports),
                missing_handoff_workstreams=sum(item.handoff_ref is None for item in reports),
                reported_with_omissions=sum(item.reporting_status == "reported_with_omissions" for item in reports),
                unchecked_evidence_workstreams=sum(
                    item.handoff_ref is not None and item.evidence_checks == "not_checked" for item in reports
                ),
                unavailable_evidence_workstreams=sum(
                    item.reporting_status == "evidence_unavailable" for item in reports
                ),
                activity_without_handoff_workstreams=sum(
                    item.activity_status == "activity_without_handoff" for item in reports
                ),
                activity_after_handoff_workstreams=sum(
                    item.handoff_activity_relation == "activity_after_handoff" for item in reports
                ),
                unknown_time_events=sum(
                    event.effective_period_time() is None for item in reports for event in item.activities
                )
                + sum(event.effective_period_time() is None for event in unassigned),
                unassigned_activity_count=len(unassigned),
                unassigned_activity_events=len(unassigned),
                activity_coverage=activity_coverage,
            ),
            summary=ReportSummary(
                continuable_count=sum(item.work_status == "continuable" for item in reports),
                blocked_count=sum(item.work_status == "blocked" for item in reports),
                complete_count=sum(item.work_status == "complete" for item in reports),
                no_handoff_count=sum(item.work_status == "no_handoff" for item in reports),
            ),
            unassigned_activity=unassigned,
            workstreams=reports,
        )
        return finalize_digests(report)


def _validate_inputs(
    project: ProjectDescriptor,
    workstreams: Sequence[WorkstreamDescriptor],
    activities: Sequence[ReportActivityEvent],
    activity_cursor: int,
) -> tuple[WorkstreamDescriptor, ...]:
    if not isinstance(activity_cursor, int) or isinstance(activity_cursor, bool) or activity_cursor < 0:
        raise ValueError("activity_cursor must be a non-negative integer")  # noqa: TRY003
    if len(workstreams) > MAX_REPORT_WORKSTREAMS:
        raise ValueError(f"a Handoff Report selects at most {MAX_REPORT_WORKSTREAMS} Workstreams")  # noqa: TRY003
    if len(activities) > MAX_REPORT_ACTIVITIES:
        raise ValueError(f"a Handoff Report selects at most {MAX_REPORT_ACTIVITIES} Activity Events")  # noqa: TRY003
    ordered = tuple(sorted(workstreams, key=lambda value: value.scope_id))
    for workstream in ordered:
        if workstream.project_id != project.project_id:
            raise ValueError("every Workstream must belong to the requested Project")  # noqa: TRY003
    if len({workstream.scope_id for workstream in ordered}) != len(ordered):
        raise ValueError("Workstream scope_id values must be unique")  # noqa: TRY003
    for event in activities:
        if event.project_id != project.project_id:
            raise ValueError("every Activity Event must belong to the requested Project")  # noqa: TRY003
    return ordered


def _group_activities(
    activities: Sequence[ReportActivityEvent],
    workstreams: Sequence[WorkstreamDescriptor],
) -> tuple[dict[str, tuple[ReportActivityEvent, ...]], tuple[ReportActivityEvent, ...]]:
    known_scopes = {workstream.scope_id for workstream in workstreams}
    grouped: dict[str, list[ReportActivityEvent]] = {}
    unassigned: list[ReportActivityEvent] = []
    for event in sorted(activities, key=activity_sort_key):
        if event.scope_id is None or event.scope_id not in known_scopes:
            unassigned.append(event)
        else:
            grouped.setdefault(event.scope_id, []).append(event)
    return {scope_id: tuple(values) for scope_id, values in grouped.items()}, tuple(unassigned)


def _reporting_status(
    omissions: tuple[object, ...],
    checks: ReportEvidenceChecks,
    evidence_unavailable: bool,
) -> ReportReportingStatus:
    if evidence_unavailable or (checks != "not_checked" and any(check.status == "unavailable" for check in checks)):
        return "evidence_unavailable"
    if omissions:
        return "reported_with_omissions"
    return "reported"


def _activity_status(events: tuple[ReportActivityEvent, ...]) -> ReportActivityStatus:
    if not events:
        return "no_observed_activity"
    if all(event.time_basis == "current_only" for event in events):
        return "current_only"
    # Existing Handoff has no authoritative commit timestamp, so the first slice
    # must not claim that an observation happened after it.
    return "unknown"


__all__ = ["HandoffReportService"]
