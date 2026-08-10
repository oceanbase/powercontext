"""Explicit WorkspaceBinding application operations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.handoff_report.catalog import HandoffReportCatalog
from powercontext.builtin.handoff_report.errors import (
    WorkspaceBindingConflictError,
    WorkspaceBindingNotFoundError,
)
from powercontext.builtin.handoff_report.models import RepositoryRef, WorkspaceBinding, normalize_repository_ref
from powercontext.builtin.handoff_report.workspace_store import WorkspaceBindingRepository


class WorkspaceBindingService:
    """Attach and detach a workspace only after exact Project selection."""

    def __init__(
        self,
        catalog: HandoffReportCatalog | None = None,
        repository: WorkspaceBindingRepository | None = None,
    ) -> None:
        self._catalog = HandoffReportCatalog() if catalog is None else catalog
        self._repository = WorkspaceBindingRepository() if repository is None else repository

    async def get(self, connection: AsyncConnection, workspace_instance_id: str, /) -> WorkspaceBinding:
        return await self._repository.get_confirmed(connection, workspace_instance_id)

    async def attach(
        self,
        connection: AsyncConnection,
        *,
        workspace_instance_id: str,
        project_id: str,
        repository_ref: RepositoryRef,
        expected_version: int | None,
        confirmed_at: datetime | None = None,
    ) -> WorkspaceBinding:
        await self._catalog.get_project(connection, project_id)
        normalized_ref = normalize_repository_ref(repository_ref)
        current = None
        if expected_version is not None:
            try:
                current = await self._repository.get(connection, workspace_instance_id)
            except WorkspaceBindingNotFoundError as error:
                raise WorkspaceBindingConflictError(
                    workspace_instance_id,
                    expected_version,
                    None,
                    detail="workspace binding record is missing",
                ) from error
        version = 1 if expected_version is None else expected_version + 1
        binding = WorkspaceBinding(
            workspace_instance_id=workspace_instance_id,
            project_id=project_id,
            repository_ref=normalized_ref,
            state="confirmed",
            confirmed_at=datetime.now(UTC) if confirmed_at is None else confirmed_at,
            version=version,
        )
        if current is not None and current.state == "confirmed" and current.project_id != project_id:
            raise WorkspaceBindingConflictError(
                workspace_instance_id,
                expected_version,
                current.version,
                detail="detach the confirmed binding before attaching another Project",
            )
        return await self._repository.attach(connection, binding, expected_version)

    async def detach(
        self,
        connection: AsyncConnection,
        workspace_instance_id: str,
        expected_version: int,
    ) -> WorkspaceBinding:
        return await self._repository.detach(connection, workspace_instance_id, expected_version)

    async def get_record(self, connection: AsyncConnection, workspace_instance_id: str, /) -> WorkspaceBinding:
        """Read a detached record for explicit re-attach workflows."""

        return await self._repository.get(connection, workspace_instance_id)


__all__ = ["WorkspaceBindingService"]
