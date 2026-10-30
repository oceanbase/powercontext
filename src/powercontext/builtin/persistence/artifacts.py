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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel, ValidationError
from sqlalchemy import and_, insert, literal, select, true, tuple_, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncTransaction
from sqlalchemy.sql import FromClause, Select

from powercontext.artifacts import (
    Artifact,
    ArtifactAddress,
    ArtifactDraft,
    ArtifactLineage,
    ArtifactRef,
)
from powercontext.builtin.persistence.artifact_governance import ArtifactGovernance, InvalidArtifactLifecycleError
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes, validate_json_model
from powercontext.builtin.persistence.database import SELECTION_BATCH_SIZE
from powercontext.builtin.persistence.errors import (
    IdentityMismatchError,
    InvalidPublicationLineageError,
    InvalidRepositoryArgumentError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.revision_meta import (
    attach_revision_meta,
    meta_from_row,
    rollback_columns,
)
from powercontext.builtin.persistence.sources import _SourceReadSet
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_PUBLICATIONS_TABLE,
    ARTIFACT_SOURCE_LOCATORS_TABLE,
    ARTIFACTS_TABLE,
)
from powercontext.builtin.source_eligibility import ArtifactLineageTarget, require_source_eligible
from powercontext.errors import (
    ArtifactFamilyMismatchError,
    RevisionConflictError,
)
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import SourceLocator, SourceRef


class _StoredSourceLocator(RootModel[SourceLocator]):
    pass


def with_artifact_content(statement: Select[Any], heads: FromClause = ARTIFACT_HEADS_TABLE) -> Select[Any]:
    """Join each selected head to its exact body, retaining dangling heads for validation."""
    return statement.add_columns(
        *(
            ARTIFACTS_TABLE.c[name]
            for name in ("content", "created_at", "created_by", "restored_from_revision", "rollback_reason")
        )
    ).outerjoin(
        ARTIFACTS_TABLE,
        and_(*(ARTIFACTS_TABLE.c[name] == heads.c[name] for name in ("scope_id", "family", "artifact_id", "revision"))),
    )


class RepositoryArtifactDraft(BaseModel):
    """A family-validated draft accepted by the shared Artifact repository."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    family: str
    content: BaseModel
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class _ValidatedLineageSources:
    connection: AsyncConnection
    transaction: AsyncTransaction | None
    nested_transaction: AsyncTransaction | None
    scope_id: str
    target: ArtifactRef
    sources: tuple[SourceRef, ...]
    allow_working_notes: bool

    def matches(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target: ArtifactRef,
        sources: tuple[SourceRef, ...],
        allow_working_notes: bool,
    ) -> bool:
        return (
            self.connection is connection
            and self.transaction is not None
            and self.transaction is connection.get_transaction()
            and self.nested_transaction is connection.get_nested_transaction()
            and self.scope_id == scope_id
            and self.target == target
            and self.sources == sources
            and self.allow_working_notes == allow_working_notes
        )


class ArtifactRepository:
    """Store composed Artifact types in the shared revision schema."""

    def __init__(
        self,
        artifact_types: Iterable[type[Artifact[Any]]],
        /,
        *,
        sources: Any | None = None,
    ) -> None:
        self._by_family = {artifact_type.family: artifact_type for artifact_type in artifact_types}
        self._content_types: dict[str, type[BaseModel]] = {}
        for family, artifact_type in self._by_family.items():
            content_type = artifact_type.model_fields["content"].annotation
            if not isinstance(content_type, type) or not issubclass(content_type, BaseModel):
                raise TypeError(f"{artifact_type.__name__}.content must be a BaseModel")  # noqa: TRY003
            self._content_types[family] = content_type
        self._sources = sources

    @property
    def families(self) -> frozenset[str]:
        """Return the registered domain Families whose writes require their owning service."""

        return frozenset(self._by_family)

    @property
    def source_repository(self) -> Any | None:
        """Expose the composed Source repository to services recording revision provenance."""

        return self._sources

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        draft: ArtifactDraft[Any] | RepositoryArtifactDraft,
        /,
        *,
        allow_working_notes: bool = False,
        validated_sources: _ValidatedLineageSources | None = None,
    ) -> Artifact[Any]:
        """Create revision one, rejecting an already existing lifecycle."""

        return (
            await self.create_many(
                connection,
                scope_id,
                ((artifact_id, draft, validated_sources),),
                allow_working_notes=allow_working_notes,
            )
        )[0]

    async def create_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        items: Sequence[tuple[str, ArtifactDraft[Any] | RepositoryArtifactDraft, _ValidatedLineageSources | None]],
        /,
        *,
        allow_working_notes: bool = False,
    ) -> tuple[Artifact[Any], ...]:
        """Insert independent new revisions by table after their Source checks."""

        _require_scope(scope_id)
        revisions = []
        new_targets = {(draft.family, artifact_id) for artifact_id, draft, _ in items}
        for artifact_id, draft, validated_sources in items:
            if len(items) > 1 and any(
                (upstream.family, upstream.artifact_id) in new_targets for upstream in draft.artifacts
            ):
                raise InvalidRepositoryArgumentError("items", "bulk create cannot reference another new target")
            artifact_type = self._artifact_type(draft.family)
            self._require_content(draft.family, draft.content)
            ref = ArtifactRef(family=draft.family, artifact_id=artifact_id, revision=1)
            if validated_sources is None or not validated_sources.matches(
                connection, scope_id, ref, draft.sources, allow_working_notes
            ):
                await self._validate_lineage_sources(
                    connection, scope_id, ref, draft.sources, allow_working_notes=allow_working_notes
                )
            revisions.append((
                artifact_type,
                ref,
                draft.content,
                ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts),
            ))
        if not revisions:
            return ()
        try:
            artifacts = await self._insert_revisions(connection, scope_id, revisions)
        except IntegrityError:
            # No new head has been inserted yet. Classify an existing lifecycle
            # collision without prechecking every proposed ID on the success path.
            existing = set(
                (
                    await connection.execute(
                        select(ARTIFACT_HEADS_TABLE.c.family, ARTIFACT_HEADS_TABLE.c.artifact_id).where(
                            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                            tuple_(ARTIFACT_HEADS_TABLE.c.family, ARTIFACT_HEADS_TABLE.c.artifact_id).in_(
                                tuple((draft.family, artifact_id) for artifact_id, draft, _ in items)
                            ),
                        )
                    )
                ).all()
            )
            for artifact_id, draft, _ in items:
                if (draft.family, artifact_id) in existing and (
                    conflict := await self._head_conflict(
                        connection,
                        scope_id,
                        ArtifactRef(family=draft.family, artifact_id=artifact_id, revision=1),
                        draft,
                    )
                ) is not None:
                    raise conflict from None
            raise
        await connection.execute(
            insert(ARTIFACT_HEADS_TABLE),
            [
                {"scope_id": scope_id, "family": artifact.family, "artifact_id": artifact.artifact_id, "revision": 1}
                for artifact in artifacts
            ],
        )
        return artifacts

    async def create_merge_result(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        draft: ArtifactDraft[Any] | RepositoryArtifactDraft,
        inputs: Sequence[tuple[ArtifactRef, int]],
        /,
        *,
        validated_sources: _ValidatedLineageSources | None = None,
    ) -> Artifact[Any]:
        """Create a result with explicit exact input membership; the caller freezes its inputs."""

        refs = tuple(ref for ref, _ in inputs)
        identities = {ref.artifact_id for ref in refs}
        if (
            len(refs) < 2
            or len(identities) != len(refs)
            or artifact_id in identities
            or any(ref.family != draft.family for ref in refs)
            or any(ref not in draft.artifacts for ref in refs)
            or any(sum(item == ref for item in draft.artifacts) != 1 for ref in refs)
        ):
            raise InvalidArtifactLifecycleError("merge inputs must be distinct exact references in the result Family")  # noqa: TRY003
        await self.lock_heads(connection, scope_id, refs)
        for ref, generation in inputs:
            head = (
                (
                    await connection.execute(
                        select(ARTIFACT_HEADS_TABLE).where(
                            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                            ARTIFACT_HEADS_TABLE.c.family == ref.family,
                            ARTIFACT_HEADS_TABLE.c.artifact_id == ref.artifact_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                head is None
                or head["revision"] != ref.revision
                or head["governance_generation"] != generation
                or head["lifecycle_state"] != "active"
                or head["merged_into_id"] is not None
            ):
                raise InvalidArtifactLifecycleError(  # noqa: TRY003
                    "merge inputs must retain their expected active head and generation"
                )
        result = await self.create(connection, scope_id, artifact_id, draft, validated_sources=validated_sources)
        await connection.execute(
            update(ARTIFACT_LINEAGE_ARTIFACTS_TABLE)
            .where(
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.scope_id == scope_id,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.family == result.family,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.artifact_id == result.artifact_id,
                ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.revision == 1,
                tuple_(
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_family,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_artifact_id,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.upstream_revision,
                ).in_(tuple((ref.family, ref.artifact_id, ref.revision) for ref in refs)),
            )
            .values(is_merge_input=True)
        )
        return result

    async def merge_inputs(
        self,
        connection: AsyncConnection,
        scope_id: str,
        result: ArtifactRef,
        /,
    ) -> tuple[ArtifactRef, ...]:
        """Read selected creation inputs in their immutable lineage ordinal order."""

        if result.revision != 1:
            return ()
        await self.get(connection, scope_id, result)
        rows = (
            await connection.execute(
                select(ARTIFACT_LINEAGE_ARTIFACTS_TABLE)
                .where(
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.family == result.family,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.artifact_id == result.artifact_id,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.revision == 1,
                    ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.is_merge_input.is_(True),
                )
                .order_by(ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.ordinal)
            )
        ).mappings()
        return tuple(
            ArtifactRef(
                family=str(row["upstream_family"]),
                artifact_id=str(row["upstream_artifact_id"]),
                revision=int(row["upstream_revision"]),
            )
            for row in rows
        )

    async def revise(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact: Artifact[Any],
        draft: ArtifactDraft[Any] | RepositoryArtifactDraft,
        /,
        *,
        allow_working_notes: bool = False,
        validated_sources: _ValidatedLineageSources | None = None,
    ) -> Artifact[Any]:
        """Commit a next revision only when ``artifact`` remains the head."""

        return await self._revise(
            connection,
            scope_id,
            artifact,
            draft,
            allow_working_notes=allow_working_notes,
            validated_sources=validated_sources,
        )

    async def revise_for_restoration(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact: Artifact[Any],
        draft: ArtifactDraft[Any] | RepositoryArtifactDraft,
        expected: ArtifactGovernance,
        /,
        *,
        validated_sources: _ValidatedLineageSources | None = None,
    ) -> Artifact[Any]:
        """Append to a frozen input under the restoration service's exact governance dependency.

        The caller must apply its validated final group state in the same
        transaction. Already retired identities remain immutable.
        """

        if expected.artifact != artifact.as_ref():
            raise StoredPayloadConflictError("artifact-governance", (scope_id, artifact.family, artifact.artifact_id))
        return await self._revise(
            connection, scope_id, artifact, draft, restoration=expected, validated_sources=validated_sources
        )

    async def _revise(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact: Artifact[Any],
        draft: ArtifactDraft[Any] | RepositoryArtifactDraft,
        *,
        restoration: ArtifactGovernance | None = None,
        allow_working_notes: bool = False,
        validated_sources: _ValidatedLineageSources | None = None,
    ) -> Artifact[Any]:
        _require_scope(scope_id)
        artifact_type = self._artifact_type(artifact.family)
        if type(artifact) is not artifact_type or draft.family != artifact.family:
            raise ArtifactFamilyMismatchError(artifact, draft)
        self._require_content(draft.family, draft.content)
        target = ArtifactRef(
            family=artifact.family,
            artifact_id=artifact.artifact_id,
            revision=artifact.revision + 1,
        )
        if validated_sources is None or not validated_sources.matches(
            connection, scope_id, target, draft.sources, allow_working_notes
        ):
            await self._validate_lineage_sources(
                connection,
                scope_id,
                target,
                draft.sources,
                allow_working_notes=allow_working_notes,
            )

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
        head = (
            (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == artifact.family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id == artifact.artifact_id,
                    )
                )
            )
            .mappings()
            .one()
        )
        if restoration is not None and (
            head["lifecycle_state"] != restoration.lifecycle_state.value
            or head["merged_into_id"] != restoration.merged_into_id
            or head["replacement_artifact_id"] != restoration.replacement_artifact_id
            or head["governance_generation"] != restoration.governance_generation
        ):
            raise StoredPayloadConflictError("artifact-governance", (scope_id, artifact.family, artifact.artifact_id))
        if head["lifecycle_state"] == "retired" or (head["merged_into_id"] is not None and restoration is None):
            raise InvalidArtifactLifecycleError("frozen and retired Artifact identities cannot be revised")  # noqa: TRY003

        ref = ArtifactRef(
            family=artifact.family,
            artifact_id=artifact.artifact_id,
            revision=artifact.revision + 1,
        )
        revised = (
            await self._insert_revisions(
                connection,
                scope_id,
                (
                    (
                        artifact_type,
                        ref,
                        draft.content,
                        ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts),
                    ),
                ),
            )
        )[0]
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
        *,
        for_update: bool = False,
    ) -> Artifact[Any]:
        """Load one exact revision with ordered direct lineage."""

        _require_scope(scope_id)
        if not for_update:
            return (await self.get_many(connection, scope_id, (ref,)))[0]
        statement = select(ARTIFACTS_TABLE).where(
            ARTIFACTS_TABLE.c.scope_id == scope_id,
            ARTIFACTS_TABLE.c.family == ref.family,
            ARTIFACTS_TABLE.c.artifact_id == ref.artifact_id,
            ARTIFACTS_TABLE.c.revision == ref.revision,
        )
        statement = statement.with_for_update()
        row = (await connection.execute(statement)).mappings().one_or_none()
        if row is None:
            raise RepositoryNotFoundError("artifact", (scope_id, ref))
        return (await self.decode_many(connection, scope_id, (row,)))[0]

    async def get_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[ArtifactRef],
        /,
    ) -> tuple[Artifact[Any], ...]:
        """Load exact Revisions and ordered lineage with bounded batch queries."""

        _require_scope(scope_id)
        requested = tuple(refs)
        if not requested:
            return ()
        keys = tuple(dict.fromkeys((ref.family, ref.artifact_id, ref.revision) for ref in requested))
        for ref in requested:
            try:
                self._artifact_type(ref.family)
            except RepositoryNotFoundError:
                raise RepositoryNotFoundError("artifact", (scope_id, ref)) from None
        identity = tuple_(
            ARTIFACTS_TABLE.c.family,
            ARTIFACTS_TABLE.c.artifact_id,
            ARTIFACTS_TABLE.c.revision,
        )
        by_key = {}
        for offset in range(0, len(keys), 100):
            rows = (
                await connection.execute(
                    select(ARTIFACTS_TABLE).where(
                        ARTIFACTS_TABLE.c.scope_id == scope_id,
                        identity.in_(keys[offset : offset + 100]),
                    )
                )
            ).mappings()
            by_key.update({(str(row["family"]), str(row["artifact_id"]), int(row["revision"])): row for row in rows})
        for ref in requested:
            key = (ref.family, ref.artifact_id, ref.revision)
            if key not in by_key:
                raise RepositoryNotFoundError("artifact", (scope_id, ref))

        decoded = await self.decode_many(connection, scope_id, tuple(by_key.values()))
        by_ref = {(artifact.family, artifact.artifact_id, artifact.revision): artifact for artifact in decoded}
        return tuple(by_ref[(ref.family, ref.artifact_id, ref.revision)] for ref in requested)

    async def decode_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        rows: Sequence[Mapping[Any, Any]],
    ) -> tuple[Artifact[Any], ...]:
        """Expand already-read authoritative rows, preserving direct and publication lineage."""
        if not rows:
            return ()
        keys = tuple((str(row["family"]), str(row["artifact_id"]), int(row["revision"])) for row in rows)
        for key, row in zip(keys, rows, strict=True):
            if row["content"] is None:
                raise RepositoryNotFoundError(
                    "artifact", (scope_id, ArtifactRef(family=key[0], artifact_id=key[1], revision=key[2]))
                )
        lineage = {}
        for offset in range(0, len(keys), 100):
            lineage.update(await self._load_lineage_many(connection, scope_id, keys[offset : offset + 100]))
        return tuple(self._decode_artifact(row, lineage[key]) for key, row in zip(keys, rows, strict=True))

    async def latest(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        artifact_id: str,
        /,
        *,
        for_update: bool = False,
        known: Artifact[Any] | None = None,
    ) -> Artifact[Any]:
        """Load the current revision selected by the authoritative head.

        ``known`` lets a caller that already holds one exact revision skip the second read when the
        head still selects it. The head is always read, so the decision itself is unchanged.
        """

        _require_scope(scope_id)
        ArtifactRef(family=family, artifact_id=artifact_id, revision=1)
        revision = await self._find_head(connection, scope_id, family, artifact_id, for_update=for_update)
        if revision is None:
            raise RepositoryNotFoundError("artifact", (scope_id, family, artifact_id))
        if known is not None and not for_update and known.revision == int(revision):
            return known
        return await self.get(
            connection,
            scope_id,
            ArtifactRef(family=family, artifact_id=artifact_id, revision=int(revision)),
            for_update=for_update,
        )

    async def lock_heads(
        self,
        connection: AsyncConnection,
        scope_id: str,
        refs: Sequence[ArtifactRef],
        /,
    ) -> None:
        """Lock current logical identities in deterministic order, independent of revisions.

        SQLite uses a no-op UPDATE; MySQL uses an ordered SELECT FOR UPDATE.
        Callers validate their saved revisions after all locks are acquired.
        """

        _require_scope(scope_id)
        identities = sorted({(ref.family, ref.artifact_id) for ref in refs})
        table = ARTIFACT_HEADS_TABLE
        for offset in range(0, len(identities), SELECTION_BATCH_SIZE // 2):
            selected = identities[offset : offset + SELECTION_BATCH_SIZE // 2]
            condition = (table.c.scope_id == scope_id) & tuple_(table.c.family, table.c.artifact_id).in_(selected)
            statement = select(table.c.family, table.c.artifact_id).where(condition)
            if connection.dialect.name == "mysql":
                present = set(
                    (
                        await connection.execute(
                            statement.order_by(table.c.family, table.c.artifact_id).with_for_update()
                        )
                    ).all()
                )
            else:
                locked = await connection.execute(update(table).where(condition).values(revision=table.c.revision))
                if locked.rowcount == len(selected):
                    continue
                present = set((await connection.execute(statement)).all())
            if len(present) != len(selected):
                missing = next(item for item in selected if item not in present)
                raise RepositoryNotFoundError("artifact-head", (scope_id, *missing))

    async def validate_lineage_sources(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target: ArtifactRef,
        sources: tuple[SourceRef, ...],
        /,
        *,
        source_read_set: _SourceReadSet | None = None,
    ) -> _ValidatedLineageSources:
        """Validate prospective evidence for reuse within this transaction."""

        await self._validate_lineage_sources(connection, scope_id, target, sources, source_read_set=source_read_set)
        return _ValidatedLineageSources(
            connection,
            connection.get_transaction(),
            connection.get_nested_transaction(),
            scope_id,
            target,
            sources,
            False,
        )

    async def revisions(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: str,
        artifact_id: str,
        /,
        *,
        since_revision: int = 0,
        through_revision: int | None = None,
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
                    ARTIFACTS_TABLE.c.revision > since_revision,
                    true() if through_revision is None else ARTIFACTS_TABLE.c.revision <= through_revision,
                )
                .order_by(ARTIFACTS_TABLE.c.revision)
            )
        ).mappings()
        return await self.decode_many(connection, scope_id, tuple(rows))

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
        copied = (
            await self._insert_revisions(
                connection,
                target_scope_id,
                (
                    (
                        artifact_type,
                        ref,
                        source.content,
                        ArtifactLineage(
                            publication_source=source.lineage.publication_source or source_address,
                            publication_digest=source.lineage.publication_digest or content_digest,
                        ),
                    ),
                ),
            )
        )[0]
        await connection.execute(
            insert(ARTIFACT_HEADS_TABLE).values(
                scope_id=target_scope_id,
                family=ref.family,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
            )
        )
        return copied

    async def _insert_revisions(
        self,
        connection: AsyncConnection,
        scope_id: str,
        revisions: Sequence[tuple[type[Artifact[Any]], ArtifactRef, object, ArtifactLineage]],
    ) -> tuple[Artifact[Any], ...]:
        bodies, sources, source_locators, artifact_refs, artifacts = [], [], [], [], []
        for artifact_type, ref, content, lineage in revisions:
            if not isinstance(content, BaseModel):
                raise TypeError("artifact content must be a BaseModel")  # noqa: TRY003
            created_at = datetime.now(UTC)
            stamped = rollback_columns(ref.family, ref.artifact_id, ref.revision)
            identity = {
                "scope_id": scope_id,
                "family": ref.family,
                "artifact_id": ref.artifact_id,
                "revision": ref.revision,
            }
            bodies.append({
                **identity,
                "content": dump_model(content, kind="artifact", name=artifact_type.family),
                "created_at": created_at,
                **stamped,
            })
            sources.extend(
                {**identity, "ordinal": ordinal, "source_type": source.source_type, "source_id": source.source_id}
                for ordinal, source in enumerate(lineage.sources)
            )
            source_locators.extend(
                {
                    **identity,
                    "ordinal": ordinal,
                    "locator": dump_model(_StoredSourceLocator(source.locator), kind="source", name="locator"),
                }
                for ordinal, source in enumerate(lineage.sources)
                if source.locator is not None
            )
            artifact_refs.extend(
                {
                    **identity,
                    "ordinal": ordinal,
                    "upstream_family": upstream.family,
                    "upstream_artifact_id": upstream.artifact_id,
                    "upstream_revision": upstream.revision,
                }
                for ordinal, upstream in enumerate(lineage.artifacts)
            )
            artifact = artifact_type(
                artifact_id=ref.artifact_id, revision=ref.revision, content=content, lineage=lineage
            )
            attach_revision_meta(artifact, meta_from_row({"created_at": created_at, **stamped}))
            if artifact.as_ref() != ref:
                raise IdentityMismatchError("artifact", ref, artifact.as_ref())
            artifacts.append(artifact)
        await connection.execute(insert(ARTIFACTS_TABLE), bodies)
        if sources:
            await connection.execute(insert(ARTIFACT_LINEAGE_SOURCES_TABLE), sources)
        if source_locators:
            await connection.execute(insert(ARTIFACT_SOURCE_LOCATORS_TABLE), source_locators)
        if artifact_refs:
            await connection.execute(insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE), artifact_refs)
        return tuple(artifacts)

    def _decode_artifact(
        self,
        row: Mapping[Any, Any],
        lineage: ArtifactLineage,
    ) -> Artifact[Any]:
        family = str(row["family"])
        artifact_type = self._artifact_type(family)
        content = self.decode_content(row)
        ref = ArtifactRef(
            family=family,
            artifact_id=str(row["artifact_id"]),
            revision=int(row["revision"]),
        )
        artifact = artifact_type(
            artifact_id=ref.artifact_id,
            revision=ref.revision,
            content=content,
            lineage=lineage,
        )
        attach_revision_meta(artifact, meta_from_row(row))
        if artifact.as_ref() != ref:
            raise IdentityMismatchError("artifact", ref, artifact.as_ref())
        return artifact

    def decode_content(self, row: Mapping[Any, Any]) -> BaseModel:
        """Validate a stored body's Family schema without inventing unrequested lineage."""
        family = str(row["family"])
        self._artifact_type(family)
        return load_model(
            self._content_types[family],
            stored_bytes(row["content"], column="payload"),
            kind="artifact",
            name=family,
        )

    async def _load_lineage_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        keys: Sequence[tuple[str, str, int]],
    ) -> dict[tuple[str, str, int], ArtifactLineage]:
        source, artifact, publication = (
            ARTIFACT_LINEAGE_SOURCES_TABLE.c,
            ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c,
            ARTIFACT_PUBLICATIONS_TABLE.c,
        )
        located_sources = ARTIFACT_LINEAGE_SOURCES_TABLE.outerjoin(
            ARTIFACT_SOURCE_LOCATORS_TABLE,
            and_(
                *(
                    source[name] == ARTIFACT_SOURCE_LOCATORS_TABLE.c[name]
                    for name in ("scope_id", "family", "artifact_id", "revision", "ordinal")
                )
            ),
        )
        columns = (
            "relation",
            "family",
            "artifact_id",
            "revision",
            "ordinal",
            "ref_scope",
            "ref_family",
            "ref_id",
            "ref_revision",
            "digest",
            "locator",
        )

        def project(relation, table, *, prefix="", from_clause=None, **references):
            fields = {
                "relation": literal(relation),
                **{name: table[prefix + name] for name in ("family", "artifact_id", "revision")},
                **references,
            }
            statement = select(*(fields.get(name, literal(None)).label(name) for name in columns))
            if from_clause is not None:
                statement = statement.select_from(from_clause)
            return statement.where(
                table[prefix + "scope_id"] == scope_id,
                tuple_(*(table[prefix + name] for name in ("family", "artifact_id", "revision"))).in_(keys),
            )

        relations = union_all(
            project(
                "source",
                source,
                ordinal=source.ordinal,
                ref_family=source.source_type,
                ref_id=source.source_id,
                from_clause=located_sources,
                locator=ARTIFACT_SOURCE_LOCATORS_TABLE.c.locator,
            ),
            project(
                "artifact",
                artifact,
                ordinal=artifact.ordinal,
                ref_family=artifact.upstream_family,
                ref_id=artifact.upstream_artifact_id,
                ref_revision=artifact.upstream_revision,
            ),
            project(
                "publication",
                publication,
                prefix="target_",
                ordinal=literal(0),
                ref_scope=publication.source_scope_id,
                ref_family=publication.source_family,
                ref_id=publication.source_artifact_id,
                ref_revision=publication.source_revision,
                digest=publication.content_digest,
            ),
        ).subquery("artifact_relations")
        rows = (
            await connection.execute(
                select(relations).order_by(
                    relations.c.family,
                    relations.c.artifact_id,
                    relations.c.revision,
                    relations.c.relation,
                    relations.c.ordinal,
                )
            )
        ).mappings()
        sources: dict[tuple[str, str, int], list[SourceRef]] = {key: [] for key in keys}
        artifacts: dict[tuple[str, str, int], list[ArtifactRef]] = {key: [] for key in keys}
        publications = {}
        for row in rows:
            key = (str(row["family"]), str(row["artifact_id"]), int(row["revision"]))
            if row["relation"] == "source":
                locator = (
                    None
                    if row["locator"] is None
                    else load_model(
                        _StoredSourceLocator,
                        stored_bytes(row["locator"], column="locator"),
                        kind="source",
                        name="locator",
                    ).root
                )
                sources[key].append(
                    SourceRef(
                        source_type=str(row["ref_family"]),
                        source_id=str(row["ref_id"]),
                        locator=locator,
                    )
                )
            elif row["relation"] == "artifact":
                artifacts[key].append(
                    ArtifactRef(
                        family=str(row["ref_family"]),
                        artifact_id=str(row["ref_id"]),
                        revision=int(row["ref_revision"]),
                    )
                )
            else:
                publications[scope_id, *key] = (
                    (str(row["ref_scope"]), str(row["ref_family"]), str(row["ref_id"]), int(row["ref_revision"])),
                    str(row["digest"]),
                )
        provenance = await self._load_publication_provenance_many(connection, scope_id, keys, publications)
        return {
            key: ArtifactLineage(
                sources=tuple(sources[key]),
                artifacts=tuple(artifacts[key]),
                publication_source=provenance[key][0],
                publication_digest=provenance[key][1],
            )
            for key in keys
        }

    async def _load_publication_provenance_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        keys: Sequence[tuple[str, str, int]],
        publications: dict[tuple[str, str, str, int], tuple[tuple[str, str, str, int], str]],
    ) -> dict[tuple[str, str, int], tuple[ArtifactAddress | None, str | None]]:
        """Read each publication frontier once and validate every exact path."""
        current = {key: (scope_id, *key) for key in keys}
        digests: dict[tuple[str, str, int], str | None] = dict.fromkeys(keys)
        visited = {key: set() for key in keys}
        known = set(current.values())
        result = {}
        table = ARTIFACT_PUBLICATIONS_TABLE.c
        identity = tuple_(table.target_scope_id, table.target_family, table.target_artifact_id, table.target_revision)
        while current:
            missing = sorted(set(current.values()) - known)
            for offset in range(0, len(missing), 100):
                batch = missing[offset : offset + 100]
                rows = (
                    await connection.execute(select(ARTIFACT_PUBLICATIONS_TABLE).where(identity.in_(batch)))
                ).mappings()
                for row in rows:
                    target = (
                        str(row["target_scope_id"]),
                        str(row["target_family"]),
                        str(row["target_artifact_id"]),
                        int(row["target_revision"]),
                    )
                    source = (
                        str(row["source_scope_id"]),
                        str(row["source_family"]),
                        str(row["source_artifact_id"]),
                        int(row["source_revision"]),
                    )
                    publications[target] = source, str(row["content_digest"])
                known.update(batch)
            pending = {}
            for origin, target in current.items():
                if target in visited[origin]:
                    raise InvalidPublicationLineageError("cycle")
                visited[origin].add(target)
                publication = publications.get(target)
                if publication is None:
                    address = (
                        None
                        if digests[origin] is None
                        else ArtifactAddress(
                            scope_id=target[0],
                            artifact=ArtifactRef(family=target[1], artifact_id=target[2], revision=target[3]),
                        )
                    )
                    result[origin] = address, digests[origin]
                    continue
                source, digest = publication
                if digests[origin] is not None and digests[origin] != digest:
                    raise InvalidPublicationLineageError("digest")
                digests[origin] = digest
                pending[origin] = source
            current = pending
        return result

    async def _head_conflict(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: ArtifactRef,
        draft: ArtifactDraft[Any] | RepositoryArtifactDraft,
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
        *,
        for_update: bool = False,
    ) -> int | None:
        statement = select(ARTIFACT_HEADS_TABLE.c.revision).where(
            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
            ARTIFACT_HEADS_TABLE.c.family == family,
            ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
        )
        if for_update:
            statement = statement.with_for_update()
        value = await connection.scalar(statement)
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

    def draft(
        self,
        family: str,
        content: dict[str, JsonValue],
        /,
        *,
        sources: tuple[SourceRef, ...] = (),
        artifacts: tuple[ArtifactRef, ...] = (),
    ) -> RepositoryArtifactDraft:
        """Validate untrusted JSON with the registered family model."""

        content_type = self._content_types.get(family)
        if content_type is None:
            raise RepositoryNotFoundError("artifact-family", family)
        try:
            validated = validate_json_model(content_type, content)
        except ValidationError as error:
            raise InvalidRepositoryArgumentError("content", "does not match the Artifact family") from error
        return RepositoryArtifactDraft(
            family=family,
            content=validated,
            sources=sources,
            artifacts=artifacts,
        )

    async def _validate_lineage_sources(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target: ArtifactRef,
        sources: tuple[SourceRef, ...],
        *,
        allow_working_notes: bool = False,
        source_read_set: _SourceReadSet | None = None,
    ) -> None:
        if self._sources is None:
            return
        missing = tuple(
            source_ref
            for source_ref in sources
            if source_read_set is None or source_read_set.find(connection, scope_id, source_ref) is None
        )
        fetched = {
            (item.ref.source_type, item.ref.source_id): item
            for item in await self._sources.get_many(connection, scope_id, missing)
        }
        for source_ref in sources:
            stored = None if source_read_set is None else source_read_set.find(connection, scope_id, source_ref)
            if stored is None:
                stored = fetched[(source_ref.source_type, source_ref.source_id)]
            stored.validate_locator(source_ref)
            require_source_eligible(
                source_ref,
                stored.value,
                target=ArtifactLineageTarget(
                    scope_id=scope_id,
                    family=target.family,
                    artifact_id=target.artifact_id,
                    revision=target.revision,
                ),
                allow_working_notes=allow_working_notes,
            )


def _require_scope(scope_id: object) -> None:
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty trimmed string")
    if len(scope_id) > MAX_SCOPE_ID_LENGTH:
        raise InvalidRepositoryArgumentError(
            "scope_id",
            f"must not exceed {MAX_SCOPE_ID_LENGTH} characters",
        )
