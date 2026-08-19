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

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from powercontext.builtin.handoff_report import (
    HandoffReportCatalog,
    ProjectConflictError,
    ProjectDescriptor,
    ProjectNotFoundError,
    ScopeAlreadyGroupedError,
    WorkstreamConflictError,
    WorkstreamNotFoundError,
)
from powercontext.builtin.handoff_report.sqlite import HANDOFF_REPORT_TABLES
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile


def _time(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


def _updated_project(project: ProjectDescriptor, **changes) -> ProjectDescriptor:
    values = project.model_dump(by_alias=True)
    values.update(changes)
    return ProjectDescriptor.model_validate(values)


def test_catalog_creates_projects_workstreams_and_resolves_scope_membership() -> None:
    async def scenario() -> None:
        catalog = HandoffReportCatalog(project_id_factory=lambda: "prj-1")
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            project = await catalog.create_project(
                connection,
                project_key="powercontext",
                title="PowerContext",
                timezone="Asia/Shanghai",
                effective_at=_time(1),
            )
            workstream = await catalog.register_workstream(
                connection,
                project_id=project.project_id,
                scope_id="scope-report",
                key="handoff-report",
                title="Handoff Report",
                kind="feature",
                effective_at=_time(1, 1),
            )

            assert await catalog.get_project(connection, "prj-1") == project
            assert await catalog.get_workstream(connection, "scope-report") == workstream
            assert await catalog.project_for_scope(connection, "scope-report") == project
            assert (await catalog.list_workstreams(connection, "prj-1")).items == (workstream,)

    asyncio.run(scenario())


def test_project_and_workstream_updates_use_cas_and_preserve_revision_history() -> None:
    async def scenario() -> None:
        catalog = HandoffReportCatalog(project_id_factory=lambda: "prj-1")
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            project = await catalog.create_project(
                connection,
                project_key="powercontext",
                title="PowerContext",
                timezone="UTC",
                effective_at=_time(1),
            )
            workstream = await catalog.register_workstream(
                connection,
                project_id=project.project_id,
                scope_id="scope-report",
                title="Initial title",
                kind="feature",
                effective_at=_time(1, 1),
            )

            updated_project = _updated_project(
                project,
                title="Renamed Project",
                version=2,
            )
            assert (
                await catalog.update_project(
                    connection,
                    updated_project,
                    expected_version=1,
                    effective_at=_time(2),
                )
                == updated_project
            )

            updated_workstream = workstream.model_copy(update={"title": "Renamed Workstream", "version": 2})
            assert (
                await catalog.update_workstream(
                    connection,
                    updated_workstream,
                    expected_version=1,
                    effective_at=_time(2, 1),
                )
                == updated_workstream
            )

            assert await catalog.get_project(connection, "prj-1") == updated_project
            assert await catalog.get_workstream(connection, "scope-report") == updated_workstream
            assert await catalog.project_revision(connection, "prj-1", 1) == project
            assert await catalog.workstream_revision(connection, "scope-report", 1) == workstream
            assert (await catalog.project_at(connection, "prj-1", _time(1, 12))) == project
            assert (await catalog.project_at(connection, "prj-1", _time(2, 12))) == updated_project
            assert (await catalog.workstream_at(connection, "scope-report", _time(1, 12))) == workstream
            assert (await catalog.workstream_at(connection, "scope-report", _time(2, 12))) == updated_workstream

            with pytest.raises(ProjectConflictError) as project_conflict:
                await catalog.update_project(
                    connection,
                    _updated_project(project, title="Stale", version=2),
                    expected_version=1,
                )
            assert project_conflict.value.current_version == 2

            with pytest.raises(WorkstreamConflictError) as workstream_conflict:
                await catalog.update_workstream(
                    connection,
                    workstream.model_copy(update={"title": "Stale", "version": 2}),
                    expected_version=1,
                )
            assert workstream_conflict.value.current_version == 2

    asyncio.run(scenario())


def test_catalog_enforces_project_key_workstream_key_and_scope_identity_uniqueness() -> None:
    async def scenario() -> None:
        project_ids = iter(("prj-1", "prj-2", "prj-3"))
        catalog = HandoffReportCatalog(project_id_factory=project_ids.__next__)
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            first = await catalog.create_project(
                connection,
                project_key="same-key",
                title="First",
                timezone="UTC",
            )
            with pytest.raises(ProjectConflictError, match="already in use"):
                await catalog.create_project(
                    connection,
                    project_key="same-key",
                    title="Duplicate",
                    timezone="UTC",
                )

            await catalog.register_workstream(
                connection,
                project_id=first.project_id,
                scope_id="scope-1",
                key="same-workstream-key",
                title="First Workstream",
                kind="feature",
            )
            with pytest.raises(WorkstreamConflictError, match="already in use"):
                await catalog.register_workstream(
                    connection,
                    project_id=first.project_id,
                    scope_id="scope-2",
                    key="same-workstream-key",
                    title="Duplicate Key",
                    kind="feature",
                )

            second = await catalog.create_project(
                connection,
                project_key="second-key",
                title="Second",
                timezone="UTC",
            )
            with pytest.raises(ScopeAlreadyGroupedError, match="scope-1"):
                await catalog.register_workstream(
                    connection,
                    project_id=second.project_id,
                    scope_id="scope-1",
                    title="Moved Scope",
                    kind="feature",
                )

            with pytest.raises(ProjectNotFoundError):
                await catalog.get_project(connection, "missing")
            with pytest.raises(WorkstreamNotFoundError):
                await catalog.get_workstream(connection, "missing")

    asyncio.run(scenario())


def test_catalog_lists_with_cursor_and_excludes_archived_by_default() -> None:
    async def scenario() -> None:
        ids = iter(("prj-a", "prj-b", "prj-c"))
        catalog = HandoffReportCatalog(project_id_factory=ids.__next__)
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=HANDOFF_REPORT_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            projects = tuple([
                await catalog.create_project(
                    connection,
                    project_key=f"project-{suffix}",
                    title=f"Project {suffix}",
                    timezone="UTC",
                )
                for suffix in ("a", "b", "c")
            ])
            archived = _updated_project(projects[1], catalog_state="archived", version=2)
            await catalog.update_project(connection, archived, expected_version=1)

            first_page = await catalog.list_projects(connection, limit=1)
            assert tuple(item.project_id for item in first_page.items) == ("prj-a",)
            assert first_page.next_cursor == "prj-a"

            second_page = await catalog.list_projects(connection, cursor=first_page.next_cursor, limit=1)
            assert tuple(item.project_id for item in second_page.items) == ("prj-c",)
            assert second_page.next_cursor is None

            all_projects = await catalog.list_projects(connection, include_archived=True)
            assert tuple(item.project_id for item in all_projects.items) == ("prj-a", "prj-b", "prj-c")

    asyncio.run(scenario())
