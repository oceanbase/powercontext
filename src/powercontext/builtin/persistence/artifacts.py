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

"""Optimistic Artifact revision persistence for actual Artifact types."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import (
    Artifact,
    ArtifactAddress,
    ArtifactDraft,
    ArtifactLineage,
    ArtifactRef,
)
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.errors import (
    IdentityMismatchError,
    InvalidPublicationLineageError,
    InvalidRepositoryArgumentError,
    RepositoryNotFoundError,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_PUBLICATIONS_TABLE,
    ARTIFACTS_TABLE,
)
from powercontext.errors import (
    ArtifactFamilyMismatchError,
    RevisionConflictError,
)
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import SourceRef


class ArtifactRepository:
    """Store composed Artifact types in the shared revision schema."""

    def __init__(self, artifact_types: Iterable[type[Artifact[Any]]], /) -> None:
        self._by_family = {artifact_type.family: artifact_type for artifact_type in artifact_types}
        self._content_types: dict[str, type[BaseModel]] = {}
        for family, artifact_type in self._by_family.items():
            content_type = artifact_type.model_fields["content"].annotation
            if not isinstance(content_type, type) or not issubclass(content_type, BaseModel):
                raise TypeError(f"{artifact_type.__name__}.content must be a BaseModel")  # noqa: TRY003
            self._content_types[family] = content_type

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        draft: ArtifactDraft[Any],
        /,
    ) -> Artifact[Any]:
        """Create revision one, rejecting an already existing lifecycle."""

        _require_scope(scope_id)
        artifact_type = self._artifact_type(draft.family)
        self._require_content(draft.family, draft.content)
        ref = ArtifactRef(family=draft.family, artifact_id=artifact_id, revision=1)
        conflict = await self._head_conflict(connection, scope_id, ref, draft)
        if conflict is not None:
            raise conflict
        try:
            artifact = await self._insert_revision(
                connection,
                scope_id,
                artifact_type,
                ref,
                draft.content,
                ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts),
            )
            await connection.execute(
                insert(ARTIFACT_HEADS_TABLE).values(
                    scope_id=scope_id,
                    family=ref.family,
                    artifact_id=ref.artifact_id,
                    revision=ref.revision,
                )
            )
        except IntegrityError:
            # Another writer may have committed this lifecycle after the head
            # read above. Only a committed head proves the draft is stale; any
            # other constraint violation stays an integrity failure.
            conflict = await self._head_conflict(connection, scope_id, ref, draft)
            if conflict is None:
                raise
            raise conflict from None
        return artifact

    async def revise(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact: Artifact[Any],
        draft: ArtifactDraft[Any],
        /,
    ) -> Artifact[Any]:
        """Commit a next revision only when ``artifact`` remains the head."""

        _require_scope(scope_id)
        artifact_type = self._artifact_type(artifact.family)
        if type(artifact) is not artifact_type or draft.family != artifact.family:
            raise ArtifactFamilyMismatchError(artifact, draft)
        self._require_content(draft.family, draft.content)

        locked = await connection.execute(
            update(ARTIFACT_HEADS_TABLE)
            .where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == artifact.family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact.artifact_id,
                ARTIFACT_HEADS_TABLE.c.revision == artifact.revision,
            )
            .values(revision=ARTIFACT_HEADS_TABLE.c.revision)
        )
        if locked.rowcount != 1:
            current = await self.latest(connection, scope_id, artifact.family, artifact.artifact_id)
            raise RevisionConflictError(artifact, current)

        ref = ArtifactRef(
            family=artifact.family,
            artifact_id=artifact.artifact_id,
            revision=artifact.revision + 1,
        )
        revised = await self._insert_revision(
            connection,
            scope_id,
            artifact_type,
            ref,
            draft.content,
            ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts),
        )
        advanced = await connection.execute(
            update(ARTIFACT_HEADS_TABLE)
            .where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == artifact.family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact.artifact_id,
                ARTIFACT_HEADS_TABLE.c.revision == artifact.revision,
            )
            .values(revision=ref.revision)
        )
        if advanced.rowcount != 1:
            raise RevisionConflictError(artifact, revised)
        return revised

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: ArtifactRef,
        /,
    ) -> Artifact[Any]:
        """Load one exact revision with ordered direct lineage."""

        _require_scope(scope_id)
        row = (
            (
                await connection.execute(
                    select(ARTIFACTS_TABLE).where(
                        ARTIFACTS_TABLE.c.scope_id == scope_id,
                        ARTIFACTS_TABLE.c.family == ref.family,
                        ARTIFACTS_TABLE.c.artifact_id == ref.artifact_id,
                        ARTIFACTS_TABLE.c.revision == ref.revision,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise RepositoryNotFoundError("artifact", (scope_id, ref))
        return await self._decode_row(connection, row)

    async def latest(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        artifact_id: str,
        /,
    ) -> Artifact[Any]:
        """Load the current revision selected by the authoritative head."""

        _require_scope(scope_id)
        ArtifactRef(family=family, artifact_id=artifact_id, revision=1)
        revision = await self._find_head(connection, scope_id, family, artifact_id)
        if revision is None:
            raise RepositoryNotFoundError("artifact", (scope_id, family, artifact_id))
        return await self.get(
            connection,
            scope_id,
            ArtifactRef(family=family, artifact_id=artifact_id, revision=int(revision)),
        )

    async def revisions(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        artifact_id: str,
        /,
    ) -> tuple[Artifact[Any], ...]:
        """Return an artifact lifecycle in ascending revision order."""

        _require_scope(scope_id)
        ArtifactRef(family=family, artifact_id=artifact_id, revision=1)
        rows = (
            await connection.execute(
                select(ARTIFACTS_TABLE)
                .where(
                    ARTIFACTS_TABLE.c.scope_id == scope_id,
                    ARTIFACTS_TABLE.c.family == family,
                    ARTIFACTS_TABLE.c.artifact_id == artifact_id,
                )
                .order_by(ARTIFACTS_TABLE.c.revision)
            )
        ).mappings()
        revisions: list[Artifact[Any]] = []
        for row in rows:
            revisions.append(await self._decode_row(connection, row))
        return tuple(revisions)

    async def copy_exact(
        self,
        connection: AsyncConnection,
        target_scope_id: str,
        target_artifact_id: str,
        source_address: ArtifactAddress,
        source: Artifact[Any],
        content_digest: str,
        /,
    ) -> Artifact[Any]:
        """Create an independent target lifecycle from one exact source revision."""

        _require_scope(target_scope_id)
        artifact_type = self._artifact_type(source.family)
        ref = ArtifactRef(family=source.family, artifact_id=target_artifact_id, revision=1)
        copied = await self._insert_revision(
            connection,
            target_scope_id,
            artifact_type,
            ref,
            source.content,
            ArtifactLineage(
                publication_source=source.lineage.publication_source or source_address,
                publication_digest=source.lineage.publication_digest or content_digest,
            ),
        )
        await connection.execute(
            insert(ARTIFACT_HEADS_TABLE).values(
                scope_id=target_scope_id,
                family=ref.family,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
            )
        )
        return copied

    async def _insert_revision(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_type: type[Artifact[Any]],
        ref: ArtifactRef,
        content: object,
        lineage: ArtifactLineage,
    ) -> Artifact[Any]:
        if not isinstance(content, BaseModel):
            raise TypeError("artifact content must be a BaseModel")  # noqa: TRY003
        payload = dump_model(content, kind="artifact", name=artifact_type.family)
        await connection.execute(
            insert(ARTIFACTS_TABLE).values(
                scope_id=scope_id,
                family=ref.family,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
                content=payload,
            )
        )
        if lineage.sources:
            await connection.execute(
                insert(ARTIFACT_LINEAGE_SOURCES_TABLE),
                [
                    {
                        "scope_id": scope_id,
                        "family": ref.family,
                        "artifact_id": ref.artifact_id,
                        "revision": ref.revision,
                        "ordinal": ordinal,
                        "source_type": source.source_type,
                        "source_id": source.source_id,
                    }
                    for ordinal, source in enumerate(lineage.sources)
                ],
            )
        if lineage.artifacts:
            await connection.execute(
                insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE),
                [
                    {
                        "scope_id": scope_id,
                        "family": ref.family,
                        "artifact_id": ref.artifact_id,
                        "revision": ref.revision,
                        "ordinal": ordinal,
                        "upstream_family": upstream.family,
                        "upstream_artifact_id": upstream.artifact_id,
                        "upstream_revision": upstream.revision,
                    }
                    for ordinal, upstream in enumerate(lineage.artifacts)
                ],
            )
        artifact = artifact_type(
            artifact_id=ref.artifact_id,
            revision=ref.revision,
            content=content,
            lineage=lineage,
        )
        if artifact.as_ref() != ref:
            raise IdentityMismatchError("artifact", ref, artifact.as_ref())
        return artifact

    async def _decode_row(
        self,
        connection: AsyncConnection,
        row: Mapping[Any, Any],
    ) -> Artifact[Any]:
        family = str(row["family"])
        artifact_type = self._artifact_type(family)
        content = load_model(
            self._content_types[family],
            stored_bytes(row["content"], column="payload"),
            kind="artifact",
            name=family,
        )
        ref = ArtifactRef(
            family=family,
            artifact_id=str(row["artifact_id"]),
            revision=int(row["revision"]),
        )
        lineage = await self._load_lineage(connection, str(row["scope_id"]), ref)
        artifact = artifact_type(
            artifact_id=ref.artifact_id,
            revision=ref.revision,
            content=content,
            lineage=lineage,
        )
        if artifact.as_ref() != ref:
            raise IdentityMismatchError("artifact", ref, artifact.as_ref())
        return artifact

    async def _load_lineage(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: ArtifactRef,
    ) -> ArtifactLineage:
        sources = (
            await connection.execute(
                select(
                    ARTIFACT_LINEAGE_SOURCES_TABLE.c.source_type,
                    ARTIFACT_LINEAGE_SOURCES_TABLE.c.source_id,
                )
                .where(
                    ARTIFACT_LINEAGE_SOURCES_TABLE.c.scope_id == scope_id,
                    ARTIFACT_LINEAGE_SOURCES_TABLE.c.family == ref.family,
                    ARTIFACT_LINEAGE_SOURCES_TABLE.c.artifact_id == ref.artifact_id,
                    ARTIFACT_LINEAGE_SOURCES_TABLE.c.revision == ref.revision,
                )
                .order_by(ARTIFACT_LINEAGE_SOURCES_TABLE.c.ordinal)
            )
        ).all()
        artifacts = (
            await connection.execute(
                select(
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_family,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_artifact_id,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_revision,
                )
                .where(
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.family == ref.family,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.artifact_id == ref.artifact_id,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.revision == ref.revision,
                )
                .order_by(ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.ordinal)
            )
        ).all()
        publication_source, publication_digest = await self._load_publication_provenance(
            connection,
            ArtifactAddress(scope_id=scope_id, artifact=ref),
        )
        return ArtifactLineage(
            sources=tuple(SourceRef(source_type=str(row.source_type), source_id=str(row.source_id)) for row in sources),
            artifacts=tuple(
                ArtifactRef(
                    family=str(row.upstream_family),
                    artifact_id=str(row.upstream_artifact_id),
                    revision=int(row.upstream_revision),
                )
                for row in artifacts
            ),
            publication_source=publication_source,
            publication_digest=publication_digest,
        )

    async def _load_publication_provenance(
        self,
        connection: AsyncConnection,
        target: ArtifactAddress,
    ) -> tuple[ArtifactAddress | None, str | None]:
        """Resolve a publication chain to the Scope that owns the original revision."""

        current = target
        digest: str | None = None
        visited: set[tuple[str, str, str, int]] = set()
        while True:
            key = (
                current.scope_id,
                current.artifact.family,
                current.artifact.artifact_id,
                current.artifact.revision,
            )
            if key in visited:
                raise InvalidPublicationLineageError("cycle")
            visited.add(key)

            publication = (
                (
                    await connection.execute(
                        select(ARTIFACT_PUBLICATIONS_TABLE).where(
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_scope_id == current.scope_id,
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_family == current.artifact.family,
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_artifact_id == current.artifact.artifact_id,
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_revision == current.artifact.revision,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if publication is None:
                return (None, None) if digest is None else (current, digest)

            current_digest = str(publication["content_digest"])
            if digest is None:
                digest = current_digest
            elif digest != current_digest:
                raise InvalidPublicationLineageError("digest")
            current = ArtifactAddress(
                scope_id=str(publication["source_scope_id"]),
                artifact=ArtifactRef(
                    family=str(publication["source_family"]),
                    artifact_id=str(publication["source_artifact_id"]),
                    revision=int(publication["source_revision"]),
                ),
            )

    async def _head_conflict(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: ArtifactRef,
        draft: ArtifactDraft[Any],
    ) -> RevisionConflictError | None:
        """Return the conflict raised by an already committed lifecycle."""

        head = await self._find_head(connection, scope_id, ref.family, ref.artifact_id)
        if head is None:
            return None
        current = await self.get(
            connection,
            scope_id,
            ArtifactRef(family=ref.family, artifact_id=ref.artifact_id, revision=head),
        )
        return RevisionConflictError(draft, current)

    async def _find_head(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        artifact_id: str,
    ) -> int | None:
        value = await connection.scalar(
            select(ARTIFACT_HEADS_TABLE.c.revision).where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
            )
        )
        return None if value is None else int(value)

    def _artifact_type(self, family: str) -> type[Artifact[Any]]:
        try:
            return self._by_family[family]
        except KeyError:
            raise RepositoryNotFoundError("artifact-family", family) from None

    def _require_content(self, family: str, content: object) -> None:
        expected = self._content_types.get(family)
        if expected is None or type(content) is not expected:
            raise ArtifactFamilyMismatchError(family, content)


def _require_scope(scope_id: object) -> None:
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
    if len(scope_id) > MAX_SCOPE_ID_LENGTH:
        raise InvalidRepositoryArgumentError(
            "scope_id",
            f"must not exceed {MAX_SCOPE_ID_LENGTH} characters",
        )
