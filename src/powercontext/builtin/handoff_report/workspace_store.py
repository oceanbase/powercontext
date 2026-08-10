"""Report-owned persistence for explicit WorkspaceBinding CAS transitions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.handoff_report.errors import (
    HandoffReportCatalogArgumentError,
    InvalidStoredCatalogError,
    WorkspaceBindingConflictError,
    WorkspaceBindingNotFoundError,
)
from powercontext.builtin.handoff_report.models import (
    MAX_REPORT_ID_LENGTH,
    MAX_REPORT_NORMALIZED_REMOTE_LENGTH,
    MAX_REPORT_REPOSITORY_ID_LENGTH,
    MAX_REPORT_SUBPATH_LENGTH,
    MAX_WORKSPACE_INSTANCE_ID_LENGTH,
    WorkspaceBinding,
)

HANDOFF_REPORT_WORKSPACE_METADATA = MetaData()

HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE = Table(
    "pc_handoff_report_workspace_bindings",
    HANDOFF_REPORT_WORKSPACE_METADATA,
    Column("workspace_instance_id", String(MAX_WORKSPACE_INSTANCE_ID_LENGTH), primary_key=True),
    Column("project_id", String(MAX_REPORT_ID_LENGTH), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("repository_id", String(MAX_REPORT_REPOSITORY_ID_LENGTH)),
    Column("normalized_remote", String(MAX_REPORT_NORMALIZED_REMOTE_LENGTH)),
    Column("subpath", String(MAX_REPORT_SUBPATH_LENGTH)),
    Column("state", String(16), nullable=False),
    Column("confirmed_at", String(32), nullable=False),
    Column("version", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    CheckConstraint("version > 0", name="ck_pc_handoff_report_workspace_bindings_version_positive"),
)

HANDOFF_REPORT_WORKSPACE_TABLES = (HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE,)


class WorkspaceBindingRepository:
    """Store one mutable binding record per local workspace instance."""

    async def get(
        self,
        connection: AsyncConnection,
        workspace_instance_id: str,
        /,
    ) -> WorkspaceBinding:
        workspace_instance_id = _identifier(
            "workspace_instance_id",
            workspace_instance_id,
            MAX_WORKSPACE_INSTANCE_ID_LENGTH,
        )
        row = await self._find(connection, workspace_instance_id)
        if row is None:
            raise WorkspaceBindingNotFoundError(workspace_instance_id)
        return _decode_binding(row)

    async def get_confirmed(
        self,
        connection: AsyncConnection,
        workspace_instance_id: str,
        /,
    ) -> WorkspaceBinding:
        binding = await self.get(connection, workspace_instance_id)
        if binding.state != "confirmed":
            raise WorkspaceBindingNotFoundError(workspace_instance_id)
        return binding

    async def attach(
        self,
        connection: AsyncConnection,
        binding: WorkspaceBinding,
        expected_version: int | None,
        /,
    ) -> WorkspaceBinding:
        _validate_binding(binding)
        if binding.state != "confirmed":
            raise HandoffReportCatalogArgumentError("state", "attach requires a confirmed binding")
        _optional_version(expected_version)
        current = await self._find(connection, binding.workspace_instance_id)
        if expected_version is None:
            return await self._attach_absent(connection, binding, current)
        return await self._attach_existing(connection, binding, expected_version, current)

    async def _attach_absent(
        self,
        connection: AsyncConnection,
        binding: WorkspaceBinding,
        current: Mapping[Any, Any] | None,
    ) -> WorkspaceBinding:
        if binding.version != 1:
            raise HandoffReportCatalogArgumentError(
                "version",
                "an expect-absent attach must create version 1",
            )
        if current is not None:
            raise WorkspaceBindingConflictError(
                binding.workspace_instance_id,
                None,
                int(current["version"]),
                detail="workspace already has a binding record",
            )
        try:
            await connection.execute(insert(HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE).values(_row(binding)))
        except IntegrityError as error:
            raise WorkspaceBindingConflictError(
                binding.workspace_instance_id,
                None,
                None,
                detail="workspace already has a binding record",
            ) from error
        return binding

    async def _attach_existing(
        self,
        connection: AsyncConnection,
        binding: WorkspaceBinding,
        expected_version: int,
        current: Mapping[Any, Any] | None,
    ) -> WorkspaceBinding:
        if current is None:
            raise WorkspaceBindingConflictError(
                binding.workspace_instance_id,
                expected_version,
                None,
                detail="workspace binding record is missing",
            )
        current_binding = _decode_binding(current)
        if current_binding.version != expected_version:
            raise WorkspaceBindingConflictError(
                binding.workspace_instance_id,
                expected_version,
                current_binding.version,
            )
        if binding.version != expected_version + 1:
            raise HandoffReportCatalogArgumentError(
                "version",
                "updated binding version must equal expected_version + 1",
            )
        if current_binding.state == "confirmed" and current_binding.project_id != binding.project_id:
            raise WorkspaceBindingConflictError(
                binding.workspace_instance_id,
                expected_version,
                current_binding.version,
                detail="detach the confirmed binding before attaching another Project",
            )
        result = await connection.execute(
            update(HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE)
            .where(
                HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE.c.workspace_instance_id == binding.workspace_instance_id,
                HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE.c.version == expected_version,
            )
            .values(_row(binding))
        )
        if result.rowcount != 1:
            latest = await self.get(connection, binding.workspace_instance_id)
            raise WorkspaceBindingConflictError(
                binding.workspace_instance_id,
                expected_version,
                latest.version,
            )
        return binding

    async def detach(
        self,
        connection: AsyncConnection,
        workspace_instance_id: str,
        expected_version: int,
        /,
    ) -> WorkspaceBinding:
        workspace_instance_id = _identifier(
            "workspace_instance_id",
            workspace_instance_id,
            MAX_WORKSPACE_INSTANCE_ID_LENGTH,
        )
        _required_version(expected_version)
        current = await self._find(connection, workspace_instance_id)
        if current is None:
            raise WorkspaceBindingConflictError(
                workspace_instance_id,
                expected_version,
                None,
                detail="workspace binding record is missing",
            )
        binding = _decode_binding(current)
        if binding.version != expected_version:
            raise WorkspaceBindingConflictError(workspace_instance_id, expected_version, binding.version)
        if binding.state != "confirmed":
            raise WorkspaceBindingConflictError(
                workspace_instance_id,
                expected_version,
                binding.version,
                detail="workspace binding is already detached",
            )
        detached = binding.model_copy(update={"state": "detached", "version": expected_version + 1})
        result = await connection.execute(
            update(HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE)
            .where(
                HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE.c.workspace_instance_id == workspace_instance_id,
                HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE.c.version == expected_version,
            )
            .values(_row(detached))
        )
        if result.rowcount != 1:
            latest = await self.get(connection, workspace_instance_id)
            raise WorkspaceBindingConflictError(
                workspace_instance_id,
                expected_version,
                latest.version,
            )
        return detached

    async def _find(self, connection: AsyncConnection, workspace_instance_id: str) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE).where(
                        HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE.c.workspace_instance_id == workspace_instance_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )


def _validate_binding(value: WorkspaceBinding) -> None:
    if not isinstance(value, WorkspaceBinding):
        raise HandoffReportCatalogArgumentError("binding", "must be a WorkspaceBinding")


def _row(binding: WorkspaceBinding) -> dict[str, object]:
    reference = binding.repository_ref
    return {
        "workspace_instance_id": binding.workspace_instance_id,
        "project_id": binding.project_id,
        "provider": reference.provider,
        "repository_id": reference.repository_id,
        "normalized_remote": reference.normalized_remote,
        "subpath": reference.subpath,
        "state": binding.state,
        "confirmed_at": _utc_text(binding.confirmed_at),
        "version": binding.version,
        "payload": json.dumps(
            binding.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    }


def _decode_binding(row: Mapping[Any, Any]) -> WorkspaceBinding:
    try:
        value = WorkspaceBinding.model_validate_json(str(row["payload"]))
    except ValidationError as error:
        raise InvalidStoredCatalogError("WorkspaceBinding", "does not match its schema") from error
    if value.workspace_instance_id != str(row["workspace_instance_id"]) or value.version != int(row["version"]):
        raise InvalidStoredCatalogError(
            "WorkspaceBinding",
            "identity does not match indexed columns",
        )
    return value


def _identifier(field: str, value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HandoffReportCatalogArgumentError(field, "must be a non-empty trimmed string")
    if len(value) > maximum:
        raise HandoffReportCatalogArgumentError(field, f"must not exceed {maximum} characters")
    return value


def _required_version(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise HandoffReportCatalogArgumentError("expected_version", "must be a positive integer")


def _optional_version(value: object) -> None:
    if value is not None:
        _required_version(value)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HandoffReportCatalogArgumentError("confirmed_at", "must include a UTC offset")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "HANDOFF_REPORT_WORKSPACE_BINDINGS_TABLE",
    "HANDOFF_REPORT_WORKSPACE_TABLES",
    "WorkspaceBindingRepository",
]
