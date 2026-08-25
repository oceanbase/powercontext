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

"""Content-addressed persistence for canonical Agent Skill packages."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.skill.models import SkillPackageRef
from powercontext.builtin.artifacts.skill.package import SkillPackageError, SkillPackageSnapshot, capture_skill_archive
from powercontext.builtin.persistence.codec import stored_bytes
from powercontext.builtin.persistence.errors import (
    InvalidRepositoryArgumentError,
    InvalidStoredPayloadError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.tables import SKILL_PACKAGES_TABLE
from powercontext.limits import MAX_SCOPE_ID_LENGTH


class SkillPackageRepository:
    """Store immutable canonical packages and reject identity reuse with different bytes."""

    async def add(
        self,
        connection: AsyncConnection,
        scope_id: str,
        snapshot: SkillPackageSnapshot,
        /,
    ) -> SkillPackageRef:
        """Insert a package or return an identical content-addressed record."""

        _require_scope(scope_id)
        existing = await self._find_row(connection, scope_id, snapshot.reference.tree_digest)
        if existing is not None:
            self._require_same(scope_id, existing, snapshot)
            return snapshot.reference
        try:
            await connection.execute(
                insert(SKILL_PACKAGES_TABLE).values(
                    scope_id=scope_id,
                    tree_digest=snapshot.reference.tree_digest,
                    archive_digest=snapshot.reference.archive_digest,
                    archive_bytes=snapshot.archive_bytes,
                    manifest=snapshot.manifest_bytes,
                    file_count=snapshot.reference.file_count,
                    uncompressed_size=snapshot.reference.uncompressed_size,
                    archive_size=snapshot.reference.archive_size,
                    created_at=datetime.now(UTC),
                )
            )
        except IntegrityError:
            existing = await self._find_row(connection, scope_id, snapshot.reference.tree_digest)
            if existing is None:
                raise
            self._require_same(scope_id, existing, snapshot)
        return snapshot.reference

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        reference: SkillPackageRef,
        /,
    ) -> SkillPackageSnapshot:
        """Load and fully verify one exact package reference."""

        _require_scope(scope_id)
        row = await self._find_row(connection, scope_id, reference.tree_digest)
        if row is None:
            raise RepositoryNotFoundError("skill-package", (scope_id, reference.tree_digest))
        snapshot = _decode_row(row)
        if snapshot.reference != reference:
            raise InvalidStoredPayloadError(
                "skill-package", reference.tree_digest, "indexed package reference mismatch"
            )
        return snapshot

    async def _find_row(
        self,
        connection: AsyncConnection,
        scope_id: str,
        tree_digest: str,
    ) -> Mapping[Any, Any] | None:
        return (
            (
                await connection.execute(
                    select(SKILL_PACKAGES_TABLE).where(
                        SKILL_PACKAGES_TABLE.c.scope_id == scope_id,
                        SKILL_PACKAGES_TABLE.c.tree_digest == tree_digest,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    @staticmethod
    def _require_same(
        scope_id: str,
        row: Mapping[Any, Any],
        snapshot: SkillPackageSnapshot,
    ) -> None:
        if (
            str(row["archive_digest"]) != snapshot.reference.archive_digest
            or stored_bytes(row["archive_bytes"], column="archive_bytes") != snapshot.archive_bytes
            or stored_bytes(row["manifest"], column="manifest") != snapshot.manifest_bytes
            or int(row["file_count"]) != snapshot.reference.file_count
            or int(row["uncompressed_size"]) != snapshot.reference.uncompressed_size
            or int(row["archive_size"]) != snapshot.reference.archive_size
        ):
            raise StoredPayloadConflictError("skill-package", (scope_id, snapshot.reference.tree_digest))


def _decode_row(row: Mapping[Any, Any]) -> SkillPackageSnapshot:
    archive_bytes = stored_bytes(row["archive_bytes"], column="archive_bytes")
    try:
        snapshot = capture_skill_archive(archive_bytes)
    except SkillPackageError as error:
        raise InvalidStoredPayloadError(
            "skill-package",
            str(row["tree_digest"]),
            "canonical archive is invalid",
        ) from error
    indexed = SkillPackageRef(
        tree_digest=str(row["tree_digest"]),
        archive_digest=str(row["archive_digest"]),
        file_count=int(row["file_count"]),
        uncompressed_size=int(row["uncompressed_size"]),
        archive_size=int(row["archive_size"]),
    )
    if snapshot.reference != indexed:
        raise InvalidStoredPayloadError(
            "skill-package",
            indexed.tree_digest,
            "archive digest or bounds do not match indexed columns",
        )
    if snapshot.manifest_bytes != stored_bytes(row["manifest"], column="manifest"):
        raise InvalidStoredPayloadError("skill-package", indexed.tree_digest, "manifest does not match archive")
    return snapshot


def _require_scope(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
    if len(value) > MAX_SCOPE_ID_LENGTH:
        raise InvalidRepositoryArgumentError("scope_id", f"must not exceed {MAX_SCOPE_ID_LENGTH} characters")


__all__ = ["SkillPackageRepository"]
