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

"""Runtime-facing application service for Handoff Report operations."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import JsonValue

from powercontext.builtin.handoff_report.catalog import HandoffReportCatalog
from powercontext.builtin.handoff_report.catalog_store import (
    DEFAULT_CATALOG_PAGE_SIZE,
    CatalogPage,
)
from powercontext.builtin.handoff_report.errors import HandoffReportCatalogArgumentError, HandoffReportTooLargeError
from powercontext.builtin.handoff_report.models import (
    CatalogState,
    ExternalReference,
    ProjectDescriptor,
    ReportActivityEvent,
    ReportLocale,
    RepositoryRef,
    WorkspaceBinding,
    WorkstreamDescriptor,
    WorkstreamKind,
)
from powercontext.builtin.handoff_report.protocols import HandoffReadAdapter
from powercontext.builtin.handoff_report.report import (
    MAX_REPORT_ACTIVITIES,
    MAX_REPORT_WORKSTREAMS,
    HandoffReport,
    ReportActivityCoverageStatus,
    ReportFormat,
    ReportPeriodComparison,
)
from powercontext.builtin.handoff_report.repository import ActivityEventRepository, StoredActivityEvent
from powercontext.builtin.handoff_report.service import HandoffReportService
from powercontext.builtin.handoff_report.sqlite import SQLiteActivityEventRepository
from powercontext.builtin.handoff_report.workspace import WorkspaceBindingService
from powercontext.builtin.persistence.database import AsyncDatabase


@dataclass(frozen=True, slots=True)
class ReportActivityPage:
    """One cursor page plus the frozen current Project high watermark."""

    items: tuple[ReportActivityEvent, ...]
    next_cursor: int | None
    high_watermark: int


@dataclass(frozen=True, slots=True)
class ReportPeriodInput:
    """Explicit half-open period requested by a Report consumer."""

    start: datetime
    end: datetime
    timezone: str | None = None
    compare_to_previous_period: bool = False


class HandoffReportApplication:
    """Coordinate Report-owned persistence with the existing Handoff read port."""

    def __init__(
        self,
        database: AsyncDatabase,
        handoffs: HandoffReadAdapter,
        /,
        *,
        activities: ActivityEventRepository | None = None,
        workspace_bindings: WorkspaceBindingService | None = None,
    ) -> None:
        self._database = database
        self._catalog = HandoffReportCatalog()
        self._activities = SQLiteActivityEventRepository() if activities is None else activities
        self._workspace_bindings = WorkspaceBindingService() if workspace_bindings is None else workspace_bindings
        self._reports = HandoffReportService(handoffs)

    async def create_project(
        self,
        *,
        project_key: str,
        title: str,
        description: str | None = None,
        default_locale: ReportLocale = "zh-CN",
        timezone: str = "UTC",
        effective_at: datetime | None = None,
    ) -> ProjectDescriptor:
        async with self._database.transaction() as connection:
            return await self._catalog.create_project(
                connection,
                project_key=project_key,
                title=title,
                description=description,
                default_locale=default_locale,
                timezone=timezone,
                effective_at=effective_at,
            )

    async def get_project(self, project_id: str, /) -> ProjectDescriptor:
        async with self._database.transaction() as connection:
            return await self._catalog.get_project(connection, project_id)

    async def update_project(
        self,
        descriptor: ProjectDescriptor,
        expected_version: int,
        /,
        *,
        effective_at: datetime | None = None,
    ) -> ProjectDescriptor:
        async with self._database.transaction() as connection:
            return await self._catalog.update_project(
                connection,
                descriptor,
                expected_version,
                effective_at=effective_at,
            )

    async def list_projects(
        self,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CATALOG_PAGE_SIZE,
        include_archived: bool = False,
    ) -> CatalogPage[ProjectDescriptor]:
        async with self._database.transaction() as connection:
            return await self._catalog.list_projects(
                connection,
                cursor=cursor,
                limit=limit,
                include_archived=include_archived,
            )

    async def register_workstream(
        self,
        *,
        project_id: str,
        scope_id: str,
        title: str,
        kind: WorkstreamKind,
        key: str | None = None,
        catalog_state: CatalogState = "included",
        external_refs: tuple[ExternalReference, ...] = (),
        labels: tuple[str, ...] = (),
        effective_at: datetime | None = None,
    ) -> WorkstreamDescriptor:
        async with self._database.transaction() as connection:
            return await self._catalog.register_workstream(
                connection,
                project_id=project_id,
                scope_id=scope_id,
                title=title,
                kind=kind,
                key=key,
                catalog_state=catalog_state,
                external_refs=external_refs,
                labels=labels,
                effective_at=effective_at,
            )

    async def list_workstreams(
        self,
        project_id: str,
        /,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CATALOG_PAGE_SIZE,
        include_archived: bool = False,
    ) -> CatalogPage[WorkstreamDescriptor]:
        async with self._database.transaction() as connection:
            return await self._catalog.list_workstreams(
                connection,
                project_id,
                cursor=cursor,
                limit=limit,
                include_archived=include_archived,
            )

    async def update_workstream(
        self,
        descriptor: WorkstreamDescriptor,
        expected_version: int,
        /,
        *,
        effective_at: datetime | None = None,
    ) -> WorkstreamDescriptor:
        async with self._database.transaction() as connection:
            return await self._catalog.update_workstream(
                connection,
                descriptor,
                expected_version,
                effective_at=effective_at,
            )

    async def record_activity(self, event: ReportActivityEvent, /) -> StoredActivityEvent:
        """Record an explicit observation without entering the Handoff write path."""

        async with self._database.transaction() as connection:
            await self._catalog.get_project(connection, event.project_id)
            return await self._activities.record(connection, event)

    async def list_activities(
        self,
        project_id: str,
        /,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        sources: tuple[str, ...] | None = None,
        after_cursor: int = 0,
        through_cursor: int | None = None,
        limit: int = DEFAULT_CATALOG_PAGE_SIZE,
    ) -> ReportActivityPage:
        """Read a stable cursor page from the Report-owned Activity Store."""

        async with self._database.transaction() as connection:
            await self._catalog.get_project(connection, project_id)
            high_watermark = await self._activities.high_watermark(connection, project_id)
            frozen_cursor = high_watermark if through_cursor is None else through_cursor
            stored = await self._activities.list(
                connection,
                project_id,
                period_start=period_start,
                period_end=period_end,
                sources=sources,
                after_cursor=after_cursor,
                through_cursor=frozen_cursor,
                limit=limit + 1,
            )
        has_more = len(stored) > limit
        selected = stored[:limit]
        return ReportActivityPage(
            items=tuple(_activity_event(item) for item in selected),
            next_cursor=selected[-1].cursor if has_more and selected else None,
            high_watermark=high_watermark,
        )

    async def purge_activities(self, project_id: str, observed_before: datetime, /) -> int:
        """Purge only Report-owned Activity rows for one Project."""

        async with self._database.transaction() as connection:
            await self._catalog.get_project(connection, project_id)
            return await self._activities.purge(connection, project_id, observed_before)

    async def get_workspace_binding(self, workspace_instance_id: str, /) -> WorkspaceBinding:
        async with self._database.transaction() as connection:
            return await self._workspace_bindings.get(connection, workspace_instance_id)

    async def attach_workspace_binding(
        self,
        *,
        workspace_instance_id: str,
        project_id: str,
        repository_ref: RepositoryRef,
        expected_version: int | None,
    ) -> WorkspaceBinding:
        async with self._database.transaction() as connection:
            return await self._workspace_bindings.attach(
                connection,
                workspace_instance_id=workspace_instance_id,
                project_id=project_id,
                repository_ref=repository_ref,
                expected_version=expected_version,
            )

    async def detach_workspace_binding(
        self,
        workspace_instance_id: str,
        expected_version: int,
        /,
    ) -> WorkspaceBinding:
        async with self._database.transaction() as connection:
            return await self._workspace_bindings.detach(connection, workspace_instance_id, expected_version)

    async def get_report(
        self,
        project_id: str,
        /,
        *,
        locale: ReportLocale | None = None,
        include_evidence_checks: bool = True,
        report_format: ReportFormat = "markdown",
        include_archived: bool = False,
        normalized_filters: dict[str, JsonValue] | None = None,
        period: ReportPeriodInput | None = None,
    ) -> HandoffReport:
        project, workstreams, activities, activity_cursor, normalized_period, comparison = await self._report_inputs(
            project_id,
            include_archived=include_archived,
            period=period,
        )
        return await self._reports.generate(
            project,
            workstreams,
            locale=locale,
            include_evidence_checks=include_evidence_checks,
            activities=activities,
            activity_cursor=activity_cursor,
            activity_coverage=_activity_coverage(activities, activity_cursor),
            report_format=report_format,
            report_kind="handoff" if period is None else "periodic",
            normalized_filters={} if normalized_filters is None else normalized_filters,
            normalized_period=normalized_period,
            period_comparison=comparison,
        )

    async def _report_inputs(
        self,
        project_id: str,
        *,
        include_archived: bool,
        period: ReportPeriodInput | None,
    ) -> tuple[
        ProjectDescriptor,
        tuple[WorkstreamDescriptor, ...],
        tuple[ReportActivityEvent, ...],
        int,
        dict[str, JsonValue] | None,
        ReportPeriodComparison | None,
    ]:
        async with self._database.transaction() as connection:
            project = await self._catalog.get_project(connection, project_id)
            period_values = _normalize_period(project, period)
            page = await self._catalog.list_workstreams(
                connection,
                project_id,
                limit=MAX_REPORT_WORKSTREAMS,
                include_archived=include_archived,
            )
            if page.next_cursor is not None:
                raise HandoffReportTooLargeError(
                    selected_workstreams=MAX_REPORT_WORKSTREAMS + 1,
                    selected_activities=0,
                )
            activity_cursor = await self._activities.high_watermark(connection, project_id)
            stored = await self._activities.list(
                connection,
                project_id,
                period_start=None if period_values is None else period_values[0],
                period_end=None if period_values is None else period_values[1],
                after_cursor=0,
                through_cursor=activity_cursor,
                limit=MAX_REPORT_ACTIVITIES + 1,
            )
            previous_stored: tuple[StoredActivityEvent, ...] = ()
            if period_values is not None and period is not None and period.compare_to_previous_period:
                start, end, _ = period_values
                duration = end - start
                previous_stored = await self._activities.list(
                    connection,
                    project_id,
                    period_start=start - duration,
                    period_end=start,
                    after_cursor=0,
                    through_cursor=activity_cursor,
                    limit=MAX_REPORT_ACTIVITIES + 1,
                )
        if len(stored) > MAX_REPORT_ACTIVITIES:
            raise HandoffReportTooLargeError(
                selected_workstreams=len(page.items),
                selected_activities=len(stored),
            )
        if len(previous_stored) > MAX_REPORT_ACTIVITIES:
            raise HandoffReportTooLargeError(
                selected_workstreams=len(page.items),
                selected_activities=len(previous_stored),
            )
        normalized_period = None
        comparison = None
        if period_values is not None:
            requested_period = cast(ReportPeriodInput, period)
            start, end, timezone = period_values
            normalized_period = {
                "start": _utc_text(start),
                "end": _utc_text(end),
                "timezone": timezone,
                "compare_to_previous_period": requested_period.compare_to_previous_period,
            }
            if requested_period.compare_to_previous_period:
                duration = end - start
                comparison = ReportPeriodComparison(
                    previous_start=start - duration,
                    previous_end=start,
                    current_activity_count=len(stored),
                    previous_activity_count=len(previous_stored),
                    activity_delta=len(stored) - len(previous_stored),
                )
        return (
            project,
            page.items,
            tuple(_activity_event(item) for item in stored),
            activity_cursor,
            normalized_period,
            comparison,
        )


def _activity_event(value: StoredActivityEvent) -> ReportActivityEvent:
    return ReportActivityEvent.model_validate_json(json.dumps(value.payload))


def _activity_coverage(
    activities: Sequence[ReportActivityEvent],
    activity_cursor: int,
) -> ReportActivityCoverageStatus:
    return "captured" if activities or activity_cursor > 0 else "not_configured"


def _normalize_period(
    project: ProjectDescriptor,
    period: ReportPeriodInput | None,
) -> tuple[datetime, datetime, str] | None:
    if period is None:
        return None
    if period.start.tzinfo is None or period.start.utcoffset() is None:
        raise HandoffReportCatalogArgumentError("period.start", "must include a UTC offset")
    if period.end.tzinfo is None or period.end.utcoffset() is None:
        raise HandoffReportCatalogArgumentError("period.end", "must include a UTC offset")
    start = period.start.astimezone(UTC)
    end = period.end.astimezone(UTC)
    if start >= end:
        raise HandoffReportCatalogArgumentError("period", "start must precede end")
    if end - start > timedelta(days=366):
        raise HandoffReportCatalogArgumentError("period", "must not exceed 366 days")
    timezone = project.timezone if period.timezone is None else period.timezone
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise HandoffReportCatalogArgumentError("period.timezone", "must be a recognized IANA timezone") from error
    return start, end, timezone


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = ["HandoffReportApplication", "ReportActivityPage", "ReportPeriodInput"]
