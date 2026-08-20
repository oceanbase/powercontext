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

"""Application service for the Report-owned Project catalog."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.handoff_report.catalog_store import (
    DEFAULT_CATALOG_PAGE_SIZE,
    CatalogPage,
    ReportCatalogRepository,
)
from powercontext.builtin.handoff_report.models import (
    CatalogState,
    ExternalReference,
    ProjectDescriptor,
    ReportLocale,
    WorkstreamDescriptor,
    WorkstreamKind,
)

ProjectIdFactory = Callable[[], str]


class HandoffReportCatalog:
    """Coordinate server-generated Project identity with catalog persistence.

    The service only mutates Report-owned catalog tables.  It never creates a
    Core scope, writes Handoff data, or infers membership from repository
    signals.
    """

    def __init__(
        self,
        repository: ReportCatalogRepository | None = None,
        *,
        project_id_factory: ProjectIdFactory | None = None,
    ) -> None:
        self._repository = ReportCatalogRepository() if repository is None else repository
        self._project_id_factory = _new_project_id if project_id_factory is None else project_id_factory

    async def create_project(
        self,
        connection: AsyncConnection,
        *,
        project_key: str,
        title: str,
        description: str | None = None,
        default_locale: ReportLocale = "zh-CN",
        timezone: str = "UTC",
        effective_at: datetime | None = None,
    ) -> ProjectDescriptor:
        descriptor = ProjectDescriptor(
            project_id=self._project_id_factory(),
            project_key=project_key,
            title=title,
            description=description,
            default_locale=default_locale,
            timezone=timezone,
            version=1,
        )
        return await self._repository.create_project(connection, descriptor, effective_at=effective_at)

    async def get_project(self, connection: AsyncConnection, project_id: str, /) -> ProjectDescriptor:
        return await self._repository.get_project(connection, project_id)

    async def list_projects(
        self,
        connection: AsyncConnection,
        /,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CATALOG_PAGE_SIZE,
        include_archived: bool = False,
    ) -> CatalogPage[ProjectDescriptor]:
        return await self._repository.list_projects(
            connection,
            cursor=cursor,
            limit=limit,
            include_archived=include_archived,
        )

    async def update_project(
        self,
        connection: AsyncConnection,
        descriptor: ProjectDescriptor,
        expected_version: int,
        *,
        effective_at: datetime | None = None,
    ) -> ProjectDescriptor:
        return await self._repository.update_project(
            connection,
            descriptor,
            expected_version,
            effective_at=effective_at,
        )

    async def project_revision(
        self,
        connection: AsyncConnection,
        project_id: str,
        version: int,
        /,
    ) -> ProjectDescriptor:
        return await self._repository.project_revision(connection, project_id, version)

    async def project_at(
        self,
        connection: AsyncConnection,
        project_id: str,
        effective_at: datetime,
        /,
    ) -> ProjectDescriptor | None:
        return await self._repository.project_at(connection, project_id, effective_at)

    async def register_workstream(
        self,
        connection: AsyncConnection,
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
        descriptor = WorkstreamDescriptor(
            scope_id=scope_id,
            project_id=project_id,
            key=key,
            title=title,
            kind=kind,
            catalog_state=catalog_state,
            external_refs=external_refs,
            labels=labels,
            version=1,
        )
        return await self._repository.create_workstream(connection, descriptor, effective_at=effective_at)

    async def get_workstream(self, connection: AsyncConnection, scope_id: str, /) -> WorkstreamDescriptor:
        return await self._repository.get_workstream(connection, scope_id)

    async def list_workstreams(
        self,
        connection: AsyncConnection,
        project_id: str,
        /,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CATALOG_PAGE_SIZE,
        include_archived: bool = False,
    ) -> CatalogPage[WorkstreamDescriptor]:
        return await self._repository.list_workstreams(
            connection,
            project_id,
            cursor=cursor,
            limit=limit,
            include_archived=include_archived,
        )

    async def update_workstream(
        self,
        connection: AsyncConnection,
        descriptor: WorkstreamDescriptor,
        expected_version: int,
        *,
        effective_at: datetime | None = None,
    ) -> WorkstreamDescriptor:
        return await self._repository.update_workstream(
            connection,
            descriptor,
            expected_version,
            effective_at=effective_at,
        )

    async def workstream_revision(
        self,
        connection: AsyncConnection,
        scope_id: str,
        version: int,
        /,
    ) -> WorkstreamDescriptor:
        return await self._repository.workstream_revision(connection, scope_id, version)

    async def workstream_at(
        self,
        connection: AsyncConnection,
        scope_id: str,
        effective_at: datetime,
        /,
    ) -> WorkstreamDescriptor | None:
        return await self._repository.workstream_at(connection, scope_id, effective_at)

    async def project_for_scope(self, connection: AsyncConnection, scope_id: str, /) -> ProjectDescriptor:
        workstream = await self._repository.get_workstream(connection, scope_id)
        return await self._repository.get_project(connection, workstream.project_id)


def _new_project_id() -> str:
    return f"prj_{uuid4().hex}"


__all__ = ["HandoffReportCatalog", "ProjectIdFactory"]
