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

"""Report-owned Project and Workstream catalog persistence.

The tables in this module deliberately keep ``scope_id`` opaque.  They are
application-layer metadata and do not create foreign keys into Core Handoff,
Artifact, Source, Memory, or Context tables.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import ValidationError
from sqlalchemy import (
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.handoff_report.errors import (
    HandoffReportCatalogArgumentError,
    InvalidStoredCatalogError,
    ProjectConflictError,
    ProjectNotFoundError,
    ScopeAlreadyGroupedError,
    WorkstreamConflictError,
    WorkstreamNotFoundError,
)
from powercontext.builtin.handoff_report.models import (
    MAX_PROJECT_KEY_LENGTH,
    MAX_REPORT_ID_LENGTH,
    MAX_WORKSTREAM_KEY_LENGTH,
    ProjectDescriptor,
    WorkstreamDescriptor,
)
from powercontext.builtin.persistence.tables import identity_string
from powercontext.limits import MAX_SCOPE_ID_LENGTH

HANDOFF_REPORT_CATALOG_METADATA = MetaData()

HANDOFF_REPORT_PROJECTS_TABLE = Table(
    "pc_handoff_report_projects",
    HANDOFF_REPORT_CATALOG_METADATA,
    Column("project_id", identity_string(MAX_REPORT_ID_LENGTH), primary_key=True),
    Column("project_key", identity_string(MAX_PROJECT_KEY_LENGTH), nullable=False, unique=True),
    Column("version", Integer, nullable=False),
    Column("catalog_state", identity_string(16), nullable=False),
    Column("payload", Text, nullable=False),
    CheckConstraint("version > 0", name="ck_pc_handoff_report_projects_version_positive"),
)

HANDOFF_REPORT_PROJECT_REVISIONS_TABLE = Table(
    "pc_handoff_report_project_revisions",
    HANDOFF_REPORT_CATALOG_METADATA,
    Column("project_id", identity_string(MAX_REPORT_ID_LENGTH), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("effective_at", identity_string(32), nullable=False),
    Column("payload", Text, nullable=False),
    CheckConstraint("version > 0", name="ck_pc_handoff_report_project_revisions_version_positive"),
)
Index(
    "ix_pc_handoff_report_project_revisions_effective_at",
    HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.project_id,
    HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.effective_at,
    HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.version,
)

HANDOFF_REPORT_WORKSTREAMS_TABLE = Table(
    "pc_handoff_report_workstreams",
    HANDOFF_REPORT_CATALOG_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("project_id", identity_string(MAX_REPORT_ID_LENGTH), nullable=False),
    Column("workstream_key", identity_string(MAX_WORKSTREAM_KEY_LENGTH)),
    Column("version", Integer, nullable=False),
    Column("catalog_state", identity_string(16), nullable=False),
    Column("payload", Text, nullable=False),
    UniqueConstraint(
        "project_id",
        "workstream_key",
        name="uq_pc_handoff_report_workstreams_project_key",
    ),
    CheckConstraint("version > 0", name="ck_pc_handoff_report_workstreams_version_positive"),
)
Index(
    "ix_pc_handoff_report_workstreams_project_scope",
    HANDOFF_REPORT_WORKSTREAMS_TABLE.c.project_id,
    HANDOFF_REPORT_WORKSTREAMS_TABLE.c.scope_id,
)

HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE = Table(
    "pc_handoff_report_workstream_revisions",
    HANDOFF_REPORT_CATALOG_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("project_id", identity_string(MAX_REPORT_ID_LENGTH), nullable=False),
    Column("effective_at", identity_string(32), nullable=False),
    Column("payload", Text, nullable=False),
    CheckConstraint("version > 0", name="ck_pc_handoff_report_workstream_revisions_version_positive"),
)
Index(
    "ix_pc_handoff_report_workstream_revisions_effective_at",
    HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.scope_id,
    HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.effective_at,
    HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.version,
)

HANDOFF_REPORT_CATALOG_TABLES = (
    HANDOFF_REPORT_PROJECTS_TABLE,
    HANDOFF_REPORT_PROJECT_REVISIONS_TABLE,
    HANDOFF_REPORT_WORKSTREAMS_TABLE,
    HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE,
)

DEFAULT_CATALOG_PAGE_SIZE = 50
MAX_CATALOG_PAGE_SIZE = 100
CatalogItemT = TypeVar("CatalogItemT")


class CatalogPage(Generic[CatalogItemT]):
    """A stable identity-cursor page returned by the Report catalog."""

    __slots__ = ("items", "next_cursor")

    def __init__(self, items: tuple[CatalogItemT, ...], next_cursor: str | None) -> None:
        self.items = items
        self.next_cursor = next_cursor

    def __repr__(self) -> str:
        return f"CatalogPage(items={self.items!r}, next_cursor={self.next_cursor!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CatalogPage) and self.items == other.items and self.next_cursor == other.next_cursor


class ReportCatalogRepository:
    """Persist mutable catalog heads and immutable descriptor revisions."""

    async def create_project(
        self,
        connection: AsyncConnection,
        descriptor: ProjectDescriptor,
        /,
        *,
        effective_at: datetime | None = None,
    ) -> ProjectDescriptor:
        _validate_project_descriptor(descriptor)
        if descriptor.version != 1:
            raise HandoffReportCatalogArgumentError("version", "a new Project must start at version 1")
        if await self._find_project(connection, descriptor.project_id) is not None:
            raise ProjectConflictError(descriptor.project_id, None, descriptor.version)
        key_owner = await self._find_project_by_key(connection, descriptor.project_key)
        if key_owner is not None:
            raise ProjectConflictError(
                descriptor.project_id,
                None,
                int(key_owner["version"]),
                detail=f"Project key {descriptor.project_key!r} is already in use",
            )

        effective_text = _effective_at_text(effective_at)
        try:
            await connection.execute(
                insert(HANDOFF_REPORT_PROJECTS_TABLE).values(
                    project_id=descriptor.project_id,
                    project_key=descriptor.project_key,
                    version=descriptor.version,
                    catalog_state=descriptor.catalog_state,
                    payload=_dump_descriptor(descriptor),
                )
            )
            await connection.execute(
                insert(HANDOFF_REPORT_PROJECT_REVISIONS_TABLE).values(
                    project_id=descriptor.project_id,
                    version=descriptor.version,
                    effective_at=effective_text,
                    payload=_dump_descriptor(descriptor),
                )
            )
        except IntegrityError as error:
            raise ProjectConflictError(
                descriptor.project_id,
                None,
                None,
                detail=f"Project key {descriptor.project_key!r} conflicts with the current catalog",
            ) from error
        return descriptor

    async def get_project(self, connection: AsyncConnection, project_id: str, /) -> ProjectDescriptor:
        project_id = _identifier("project_id", project_id, MAX_REPORT_ID_LENGTH)
        row = await self._find_project(connection, project_id)
        if row is None:
            raise ProjectNotFoundError(project_id)
        return _decode_project(row)

    async def list_projects(
        self,
        connection: AsyncConnection,
        /,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_CATALOG_PAGE_SIZE,
        include_archived: bool = False,
    ) -> CatalogPage[ProjectDescriptor]:
        cursor = None if cursor is None else _identifier("cursor", cursor, MAX_REPORT_ID_LENGTH)
        _page_limit(limit)
        statement = (
            select(HANDOFF_REPORT_PROJECTS_TABLE).order_by(HANDOFF_REPORT_PROJECTS_TABLE.c.project_id).limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(HANDOFF_REPORT_PROJECTS_TABLE.c.project_id > cursor)
        if not include_archived:
            statement = statement.where(HANDOFF_REPORT_PROJECTS_TABLE.c.catalog_state == "included")
        rows = list((await connection.execute(statement)).mappings())
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(_decode_project(row) for row in selected)
        return CatalogPage(items, items[-1].project_id if has_more and items else None)

    async def update_project(
        self,
        connection: AsyncConnection,
        descriptor: ProjectDescriptor,
        expected_version: int,
        /,
        *,
        effective_at: datetime | None = None,
    ) -> ProjectDescriptor:
        _validate_project_descriptor(descriptor)
        _version("expected_version", expected_version)
        if descriptor.version != expected_version + 1:
            raise HandoffReportCatalogArgumentError(
                "version",
                "updated Project version must equal expected_version + 1",
            )
        current = await self._find_project(connection, descriptor.project_id)
        if current is None:
            raise ProjectNotFoundError(descriptor.project_id)
        current_version = int(current["version"])
        if current_version != expected_version:
            raise ProjectConflictError(descriptor.project_id, expected_version, current_version)
        key_owner = await self._find_project_by_key(connection, descriptor.project_key)
        if key_owner is not None and str(key_owner["project_id"]) != descriptor.project_id:
            raise ProjectConflictError(
                descriptor.project_id,
                expected_version,
                current_version,
                detail=f"Project key {descriptor.project_key!r} is already in use",
            )

        effective_text = _effective_at_text(effective_at)
        try:
            result = await connection.execute(
                update(HANDOFF_REPORT_PROJECTS_TABLE)
                .where(
                    HANDOFF_REPORT_PROJECTS_TABLE.c.project_id == descriptor.project_id,
                    HANDOFF_REPORT_PROJECTS_TABLE.c.version == expected_version,
                )
                .values(
                    project_key=descriptor.project_key,
                    version=descriptor.version,
                    catalog_state=descriptor.catalog_state,
                    payload=_dump_descriptor(descriptor),
                )
            )
            if result.rowcount != 1:
                current = await self._find_project(connection, descriptor.project_id)
                if current is None:
                    raise ProjectNotFoundError(descriptor.project_id)
                raise ProjectConflictError(
                    descriptor.project_id,
                    expected_version,
                    int(current["version"]),
                )
            await connection.execute(
                insert(HANDOFF_REPORT_PROJECT_REVISIONS_TABLE).values(
                    project_id=descriptor.project_id,
                    version=descriptor.version,
                    effective_at=effective_text,
                    payload=_dump_descriptor(descriptor),
                )
            )
        except IntegrityError as error:
            raise ProjectConflictError(
                descriptor.project_id,
                expected_version,
                current_version,
                detail=f"Project key {descriptor.project_key!r} conflicts with the current catalog",
            ) from error
        return descriptor

    async def project_revision(
        self,
        connection: AsyncConnection,
        project_id: str,
        version: int,
        /,
    ) -> ProjectDescriptor:
        project_id = _identifier("project_id", project_id, MAX_REPORT_ID_LENGTH)
        _version("version", version)
        row = (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_PROJECT_REVISIONS_TABLE).where(
                        HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.project_id == project_id,
                        HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ProjectNotFoundError(project_id)
        return _decode_project(row)

    async def project_at(
        self,
        connection: AsyncConnection,
        project_id: str,
        effective_at: datetime,
        /,
    ) -> ProjectDescriptor | None:
        project_id = _identifier("project_id", project_id, MAX_REPORT_ID_LENGTH)
        boundary = _effective_at_text(effective_at)
        row = (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_PROJECT_REVISIONS_TABLE)
                    .where(
                        HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.project_id == project_id,
                        HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.effective_at <= boundary,
                    )
                    .order_by(
                        HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.effective_at.desc(),
                        HANDOFF_REPORT_PROJECT_REVISIONS_TABLE.c.version.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_project(row)

    async def create_workstream(
        self,
        connection: AsyncConnection,
        descriptor: WorkstreamDescriptor,
        /,
        *,
        effective_at: datetime | None = None,
    ) -> WorkstreamDescriptor:
        _validate_workstream_descriptor(descriptor)
        if descriptor.version != 1:
            raise HandoffReportCatalogArgumentError("version", "a new Workstream must start at version 1")
        await self.get_project(connection, descriptor.project_id)
        existing = await self._find_workstream(connection, descriptor.scope_id)
        if existing is not None:
            existing_project = str(existing["project_id"])
            if existing_project != descriptor.project_id:
                raise ScopeAlreadyGroupedError(descriptor.scope_id, existing_project)
            raise WorkstreamConflictError(
                descriptor.scope_id,
                None,
                int(existing["version"]),
                detail=f"scope {descriptor.scope_id!r} is already registered",
            )
        key_owner = await self._find_workstream_by_key(connection, descriptor.project_id, descriptor.key)
        if key_owner is not None:
            raise WorkstreamConflictError(
                descriptor.scope_id,
                None,
                int(key_owner["version"]),
                detail=f"Workstream key {descriptor.key!r} is already in use",
            )

        effective_text = _effective_at_text(effective_at)
        try:
            await connection.execute(
                insert(HANDOFF_REPORT_WORKSTREAMS_TABLE).values(
                    scope_id=descriptor.scope_id,
                    project_id=descriptor.project_id,
                    workstream_key=descriptor.key,
                    version=descriptor.version,
                    catalog_state=descriptor.catalog_state,
                    payload=_dump_descriptor(descriptor),
                )
            )
            await connection.execute(
                insert(HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE).values(
                    scope_id=descriptor.scope_id,
                    version=descriptor.version,
                    project_id=descriptor.project_id,
                    effective_at=effective_text,
                    payload=_dump_descriptor(descriptor),
                )
            )
        except IntegrityError as error:
            raise WorkstreamConflictError(
                descriptor.scope_id,
                None,
                None,
                detail=f"Workstream key {descriptor.key!r} conflicts with the current catalog",
            ) from error
        return descriptor

    async def get_workstream(self, connection: AsyncConnection, scope_id: str, /) -> WorkstreamDescriptor:
        scope_id = _identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        row = await self._find_workstream(connection, scope_id)
        if row is None:
            raise WorkstreamNotFoundError(scope_id)
        return _decode_workstream(row)

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
        project_id = _identifier("project_id", project_id, MAX_REPORT_ID_LENGTH)
        cursor = None if cursor is None else _identifier("cursor", cursor, MAX_SCOPE_ID_LENGTH)
        _page_limit(limit)
        statement = (
            select(HANDOFF_REPORT_WORKSTREAMS_TABLE)
            .where(HANDOFF_REPORT_WORKSTREAMS_TABLE.c.project_id == project_id)
            .order_by(HANDOFF_REPORT_WORKSTREAMS_TABLE.c.scope_id)
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(HANDOFF_REPORT_WORKSTREAMS_TABLE.c.scope_id > cursor)
        if not include_archived:
            statement = statement.where(HANDOFF_REPORT_WORKSTREAMS_TABLE.c.catalog_state == "included")
        rows = list((await connection.execute(statement)).mappings())
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(_decode_workstream(row) for row in selected)
        return CatalogPage(items, items[-1].scope_id if has_more and items else None)

    async def update_workstream(
        self,
        connection: AsyncConnection,
        descriptor: WorkstreamDescriptor,
        expected_version: int,
        /,
        *,
        effective_at: datetime | None = None,
    ) -> WorkstreamDescriptor:
        _validate_workstream_descriptor(descriptor)
        _version("expected_version", expected_version)
        if descriptor.version != expected_version + 1:
            raise HandoffReportCatalogArgumentError(
                "version",
                "updated Workstream version must equal expected_version + 1",
            )
        current = await self._find_workstream(connection, descriptor.scope_id)
        if current is None:
            raise WorkstreamNotFoundError(descriptor.scope_id)
        current_project = str(current["project_id"])
        current_version = int(current["version"])
        if current_project != descriptor.project_id:
            raise HandoffReportCatalogArgumentError(
                "project_id",
                "Workstream membership cannot move between Projects",
            )
        if current_version != expected_version:
            raise WorkstreamConflictError(descriptor.scope_id, expected_version, current_version)
        key_owner = await self._find_workstream_by_key(connection, descriptor.project_id, descriptor.key)
        if key_owner is not None and str(key_owner["scope_id"]) != descriptor.scope_id:
            raise WorkstreamConflictError(
                descriptor.scope_id,
                expected_version,
                current_version,
                detail=f"Workstream key {descriptor.key!r} is already in use",
            )

        effective_text = _effective_at_text(effective_at)
        try:
            result = await connection.execute(
                update(HANDOFF_REPORT_WORKSTREAMS_TABLE)
                .where(
                    HANDOFF_REPORT_WORKSTREAMS_TABLE.c.scope_id == descriptor.scope_id,
                    HANDOFF_REPORT_WORKSTREAMS_TABLE.c.version == expected_version,
                )
                .values(
                    workstream_key=descriptor.key,
                    version=descriptor.version,
                    catalog_state=descriptor.catalog_state,
                    payload=_dump_descriptor(descriptor),
                )
            )
            if result.rowcount != 1:
                current = await self._find_workstream(connection, descriptor.scope_id)
                if current is None:
                    raise WorkstreamNotFoundError(descriptor.scope_id)
                raise WorkstreamConflictError(
                    descriptor.scope_id,
                    expected_version,
                    int(current["version"]),
                )
            await connection.execute(
                insert(HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE).values(
                    scope_id=descriptor.scope_id,
                    version=descriptor.version,
                    project_id=descriptor.project_id,
                    effective_at=effective_text,
                    payload=_dump_descriptor(descriptor),
                )
            )
        except IntegrityError as error:
            raise WorkstreamConflictError(
                descriptor.scope_id,
                expected_version,
                current_version,
                detail=f"Workstream key {descriptor.key!r} conflicts with the current catalog",
            ) from error
        return descriptor

    async def workstream_revision(
        self,
        connection: AsyncConnection,
        scope_id: str,
        version: int,
        /,
    ) -> WorkstreamDescriptor:
        scope_id = _identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        _version("version", version)
        row = (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE).where(
                        HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.scope_id == scope_id,
                        HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise WorkstreamNotFoundError(scope_id)
        return _decode_workstream(row)

    async def workstream_at(
        self,
        connection: AsyncConnection,
        scope_id: str,
        effective_at: datetime,
        /,
    ) -> WorkstreamDescriptor | None:
        scope_id = _identifier("scope_id", scope_id, MAX_SCOPE_ID_LENGTH)
        boundary = _effective_at_text(effective_at)
        row = (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE)
                    .where(
                        HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.scope_id == scope_id,
                        HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.effective_at <= boundary,
                    )
                    .order_by(
                        HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.effective_at.desc(),
                        HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE.c.version.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_workstream(row)

    async def _find_project(self, connection: AsyncConnection, project_id: str) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_PROJECTS_TABLE).where(
                        HANDOFF_REPORT_PROJECTS_TABLE.c.project_id == project_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def _find_project_by_key(self, connection: AsyncConnection, project_key: str) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_PROJECTS_TABLE).where(
                        HANDOFF_REPORT_PROJECTS_TABLE.c.project_key == project_key
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def _find_workstream(self, connection: AsyncConnection, scope_id: str) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_WORKSTREAMS_TABLE).where(
                        HANDOFF_REPORT_WORKSTREAMS_TABLE.c.scope_id == scope_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def _find_workstream_by_key(
        self,
        connection: AsyncConnection,
        project_id: str,
        key: str | None,
    ) -> Mapping[Any, Any] | None:
        if key is None:
            return None
        return (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_WORKSTREAMS_TABLE).where(
                        HANDOFF_REPORT_WORKSTREAMS_TABLE.c.project_id == project_id,
                        HANDOFF_REPORT_WORKSTREAMS_TABLE.c.workstream_key == key,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )


def _validate_project_descriptor(value: ProjectDescriptor) -> None:
    if not isinstance(value, ProjectDescriptor):
        raise HandoffReportCatalogArgumentError("descriptor", "must be a ProjectDescriptor")


def _validate_workstream_descriptor(value: WorkstreamDescriptor) -> None:
    if not isinstance(value, WorkstreamDescriptor):
        raise HandoffReportCatalogArgumentError("descriptor", "must be a WorkstreamDescriptor")


def _dump_descriptor(value: ProjectDescriptor | WorkstreamDescriptor) -> str:
    return json.dumps(
        value.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_project(row: Mapping[Any, Any]) -> ProjectDescriptor:
    try:
        value = ProjectDescriptor.model_validate_json(str(row["payload"]))
    except ValidationError as error:
        raise InvalidStoredCatalogError("Project descriptor", "does not match its schema") from error  # noqa: TRY003
    if value.project_id != str(row["project_id"]) or value.version != int(row["version"]):
        raise InvalidStoredCatalogError(  # noqa: TRY003
            "Project descriptor",
            "identity does not match indexed columns",
        )
    return value


def _decode_workstream(row: Mapping[Any, Any]) -> WorkstreamDescriptor:
    try:
        value = WorkstreamDescriptor.model_validate_json(str(row["payload"]))
    except ValidationError as error:
        raise InvalidStoredCatalogError("Workstream descriptor", "does not match its schema") from error  # noqa: TRY003
    if value.scope_id != str(row["scope_id"]) or value.version != int(row["version"]):
        raise InvalidStoredCatalogError(  # noqa: TRY003
            "Workstream descriptor",
            "identity does not match indexed columns",
        )
    return value


def _identifier(field: str, value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HandoffReportCatalogArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise HandoffReportCatalogArgumentError(field, f"must not exceed {maximum} characters")
    return value


def _version(field: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise HandoffReportCatalogArgumentError(field, "must be a positive integer")


def _page_limit(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_CATALOG_PAGE_SIZE:
        raise HandoffReportCatalogArgumentError(
            "limit",
            f"must be between 1 and {MAX_CATALOG_PAGE_SIZE}",
        )


def _effective_at_text(value: datetime | None) -> str:
    current = datetime.now(UTC) if value is None else value
    if current.tzinfo is None or current.utcoffset() is None:
        raise HandoffReportCatalogArgumentError("effective_at", "must include a UTC offset")
    return current.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "DEFAULT_CATALOG_PAGE_SIZE",
    "HANDOFF_REPORT_CATALOG_METADATA",
    "HANDOFF_REPORT_CATALOG_TABLES",
    "HANDOFF_REPORT_PROJECTS_TABLE",
    "HANDOFF_REPORT_PROJECT_REVISIONS_TABLE",
    "HANDOFF_REPORT_WORKSTREAMS_TABLE",
    "HANDOFF_REPORT_WORKSTREAM_REVISIONS_TABLE",
    "MAX_CATALOG_PAGE_SIZE",
    "CatalogPage",
    "ReportCatalogRepository",
]
