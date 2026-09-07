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

"""Relational metadata persistence for Subject routing and Profile revisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.profile.models import (
    PROFILE_SOURCE_WINDOW_BINDING,
    ProfileActivationMode,
    ProfileGenerationMode,
    ProfilePolicy,
    ProfileRevisionMetadata,
    SourceAddress,
    SubjectRoot,
    SubjectSourceProjection,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_PENDING_TABLE,
    PROFILE_POLICIES_TABLE,
    PROFILE_REVISION_METADATA_TABLE,
    SUBJECT_ROOTS_TABLE,
    SUBJECT_SOURCE_PROJECTIONS_TABLE,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class SubjectProfileRepository:
    """Persist family-neutral Subject roots and Profile-specific metadata."""

    async def subject_root(
        self,
        connection: AsyncConnection,
        subject_key: str,
        /,
    ) -> SubjectRoot | None:
        row = (
            (
                await connection.execute(
                    select(SUBJECT_ROOTS_TABLE).where(SUBJECT_ROOTS_TABLE.c.subject_key == subject_key)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _subject_root(row)

    async def root_subject(
        self,
        connection: AsyncConnection,
        root_scope_id: str,
        /,
    ) -> SubjectRoot | None:
        row = (
            (
                await connection.execute(
                    select(SUBJECT_ROOTS_TABLE).where(SUBJECT_ROOTS_TABLE.c.root_scope_id == root_scope_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _subject_root(row)

    async def add_subject_root(
        self,
        connection: AsyncConnection,
        subject_key: str,
        root_scope_id: str,
        /,
        *,
        created_at: datetime | None = None,
    ) -> SubjectRoot:
        timestamp = utc_now() if created_at is None else created_at
        await connection.execute(
            insert(SUBJECT_ROOTS_TABLE).values(
                subject_key=subject_key,
                root_scope_id=root_scope_id,
                created_at=timestamp,
            )
        )
        return SubjectRoot(subject_key=subject_key, root_scope_id=root_scope_id, created_at=timestamp)

    async def add_projection(
        self,
        connection: AsyncConnection,
        projection: SubjectSourceProjection,
        /,
    ) -> None:
        await connection.execute(
            insert(SUBJECT_SOURCE_PROJECTIONS_TABLE).values(
                origin_scope_id=projection.origin.scope_id,
                origin_source_type=projection.origin.source_type,
                origin_source_id=projection.origin.source_id,
                subject_key=projection.subject_key,
                root_scope_id=projection.projected.scope_id,
                projected_source_type=projection.projected.source_type,
                projected_source_id=projection.projected.source_id,
                content_digest=projection.content_digest,
                created_at=projection.created_at,
            )
        )

    async def projection_for_origin(
        self,
        connection: AsyncConnection,
        origin: SourceAddress,
        /,
    ) -> SubjectSourceProjection | None:
        row = (
            (
                await connection.execute(
                    select(SUBJECT_SOURCE_PROJECTIONS_TABLE).where(
                        SUBJECT_SOURCE_PROJECTIONS_TABLE.c.origin_scope_id == origin.scope_id,
                        SUBJECT_SOURCE_PROJECTIONS_TABLE.c.origin_source_type == origin.source_type,
                        SUBJECT_SOURCE_PROJECTIONS_TABLE.c.origin_source_id == origin.source_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _projection(row)

    async def projection_for_root_source(
        self,
        connection: AsyncConnection,
        projected: SourceAddress,
        /,
    ) -> SubjectSourceProjection | None:
        row = (
            (
                await connection.execute(
                    select(SUBJECT_SOURCE_PROJECTIONS_TABLE).where(
                        SUBJECT_SOURCE_PROJECTIONS_TABLE.c.root_scope_id == projected.scope_id,
                        SUBJECT_SOURCE_PROJECTIONS_TABLE.c.projected_source_type == projected.source_type,
                        SUBJECT_SOURCE_PROJECTIONS_TABLE.c.projected_source_id == projected.source_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _projection(row)

    async def add_default_policy(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        updated_at: datetime | None = None,
    ) -> ProfilePolicy:
        timestamp = utc_now() if updated_at is None else updated_at
        await connection.execute(
            insert(PROFILE_POLICIES_TABLE).values(
                scope_id=scope_id,
                generation_enabled=True,
                activation_mode="automatic",
                pending_candidate_id=None,
                version=1,
                updated_at=timestamp,
            )
        )
        return ProfilePolicy(
            scope_id=scope_id,
            generation_enabled=True,
            activation_mode="automatic",
            pending_candidate_id=None,
            version=1,
            updated_at=timestamp,
        )

    async def policy(self, connection: AsyncConnection, scope_id: str, /) -> ProfilePolicy | None:
        row = (
            (
                await connection.execute(
                    select(PROFILE_POLICIES_TABLE).where(PROFILE_POLICIES_TABLE.c.scope_id == scope_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _policy(row)

    async def replace_policy(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        generation_enabled: bool,
        activation_mode: ProfileActivationMode,
        expected_version: int,
        updated_at: datetime | None = None,
    ) -> ProfilePolicy | None:
        timestamp = utc_now() if updated_at is None else updated_at
        result = await connection.execute(
            update(PROFILE_POLICIES_TABLE)
            .where(
                PROFILE_POLICIES_TABLE.c.scope_id == scope_id,
                PROFILE_POLICIES_TABLE.c.version == expected_version,
            )
            .values(
                generation_enabled=generation_enabled,
                activation_mode=activation_mode,
                version=expected_version + 1,
                updated_at=timestamp,
            )
        )
        if result.rowcount != 1:
            return None
        return await self.policy(connection, scope_id)

    async def add_revision_metadata(
        self,
        connection: AsyncConnection,
        *,
        scope_id: str,
        artifact_ref: ArtifactRef,
        generation_mode: ProfileGenerationMode,
        operation_reason: str | None = None,
        generator_id: str | None = None,
        generator_version: str | None = None,
        source_after: int | None = None,
        source_through: int | None = None,
        restored_from_revision: int | None = None,
        created_at: datetime | None = None,
    ) -> ProfileRevisionMetadata:
        metadata = ProfileRevisionMetadata(
            scope_id=scope_id,
            artifact_ref=artifact_ref,
            generation_mode=generation_mode,
            generator_id=generator_id,
            generator_version=generator_version,
            source_after=source_after,
            source_through=source_through,
            restored_from_revision=restored_from_revision,
            operation_reason=operation_reason,
            created_at=utc_now() if created_at is None else created_at,
        )
        await connection.execute(
            insert(PROFILE_REVISION_METADATA_TABLE).values(
                scope_id=scope_id,
                family=artifact_ref.family,
                artifact_id=artifact_ref.artifact_id,
                revision=artifact_ref.revision,
                generation_mode=generation_mode,
                generator_id=generator_id,
                generator_version=generator_version,
                source_after=source_after,
                source_through=source_through,
                restored_from_revision=restored_from_revision,
                operation_reason=operation_reason,
                created_at=metadata.created_at,
            )
        )
        return metadata

    async def revision_metadata(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_ref: ArtifactRef,
        /,
    ) -> ProfileRevisionMetadata | None:
        row = (
            (
                await connection.execute(
                    select(PROFILE_REVISION_METADATA_TABLE).where(
                        PROFILE_REVISION_METADATA_TABLE.c.scope_id == scope_id,
                        PROFILE_REVISION_METADATA_TABLE.c.family == artifact_ref.family,
                        PROFILE_REVISION_METADATA_TABLE.c.artifact_id == artifact_ref.artifact_id,
                        PROFILE_REVISION_METADATA_TABLE.c.revision == artifact_ref.revision,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ProfileRevisionMetadata(
            scope_id=str(row["scope_id"]),
            artifact_ref=ArtifactRef(
                family=str(row["family"]),
                artifact_id=str(row["artifact_id"]),
                revision=int(row["revision"]),
            ),
            generation_mode=cast(ProfileGenerationMode, str(row["generation_mode"])),
            generator_id=None if row["generator_id"] is None else str(row["generator_id"]),
            generator_version=None if row["generator_version"] is None else str(row["generator_version"]),
            source_after=None if row["source_after"] is None else int(row["source_after"]),
            source_through=None if row["source_through"] is None else int(row["source_through"]),
            restored_from_revision=(
                None if row["restored_from_revision"] is None else int(row["restored_from_revision"])
            ),
            operation_reason=None if row["operation_reason"] is None else str(row["operation_reason"]),
            created_at=_utc_datetime(row["created_at"]),
        )

    async def mark_pending(
        self,
        connection: AsyncConnection,
        scope_id: str,
        source_through: int,
        /,
        *,
        request_flush: bool = False,
    ) -> None:
        current = (
            (
                await connection.execute(
                    select(ARTIFACT_PROCESSING_PENDING_TABLE).where(
                        ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == PROFILE_SOURCE_WINDOW_BINDING,
                        ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            await connection.execute(
                insert(ARTIFACT_PROCESSING_PENDING_TABLE).values(
                    binding_name=PROFILE_SOURCE_WINDOW_BINDING,
                    scope_id=scope_id,
                    source_through=source_through,
                    flush_generation=1 if request_flush else 0,
                    handled_flush_generation=0,
                )
            )
            return
        await connection.execute(
            update(ARTIFACT_PROCESSING_PENDING_TABLE)
            .where(
                ARTIFACT_PROCESSING_PENDING_TABLE.c.binding_name == PROFILE_SOURCE_WINDOW_BINDING,
                ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == scope_id,
            )
            .values(
                source_through=max(source_through, int(current["source_through"])),
                flush_generation=int(current["flush_generation"]) + (1 if request_flush else 0),
            )
        )


def _subject_root(row: RowMapping) -> SubjectRoot:
    values = row
    return SubjectRoot(
        subject_key=str(values["subject_key"]),
        root_scope_id=str(values["root_scope_id"]),
        created_at=_utc_datetime(values["created_at"]),
    )


def _projection(row: RowMapping) -> SubjectSourceProjection:
    values = row
    return SubjectSourceProjection(
        subject_key=str(values["subject_key"]),
        origin=SourceAddress(
            scope_id=str(values["origin_scope_id"]),
            source_type=str(values["origin_source_type"]),
            source_id=str(values["origin_source_id"]),
        ),
        projected=SourceAddress(
            scope_id=str(values["root_scope_id"]),
            source_type=str(values["projected_source_type"]),
            source_id=str(values["projected_source_id"]),
        ),
        content_digest=str(values["content_digest"]),
        created_at=_utc_datetime(values["created_at"]),
    )


def _policy(row: RowMapping) -> ProfilePolicy:
    values = row
    return ProfilePolicy(
        scope_id=str(values["scope_id"]),
        generation_enabled=bool(values["generation_enabled"]),
        activation_mode=cast(Literal["automatic", "review_required"], str(values["activation_mode"])),
        pending_candidate_id=None if values["pending_candidate_id"] is None else str(values["pending_candidate_id"]),
        version=int(values["version"]),
        updated_at=_utc_datetime(values["updated_at"]),
    )


def _utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("stored Profile timestamp must be a datetime")  # noqa: TRY003
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = ["SubjectProfileRepository", "utc_now"]
