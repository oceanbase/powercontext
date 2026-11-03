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

"""Atomic content, projection and public-model adapters for shared Artifact merge."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.errors import (
    AtomicMemoryConflictError,
    AtomicMemoryPreviewExpiredError,
    AtomicMemoryPreviewStaleError,
    AtomicMemoryRelationError,
    InvalidAtomicMemoryPreviewError,
    InvalidAtomicMemoryStateError,
)
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemory,
    AtomicMemoryContent,
    AtomicMemoryCreation,
    AtomicMemoryDraft,
    AtomicMemoryMutationResult,
    AtomicMemoryPlan,
    AtomicMemoryRead,
    AtomicMemoryRecord,
    AtomicMemoryRestorationPreview,
    AtomicMemoryRestoreItem,
    AtomicMemoryRestoreOperation,
    AtomicMemoryState,
    AtomicMemoryStateValue,
    AtomicMemoryWrite,
    PreparedAtomicMemory,
)
from powercontext.builtin.artifacts.atomic_memory.summary import default_description
from powercontext.builtin.artifacts.memory.canonical import canonical_error_code, normalize_text
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCandidateError
from powercontext.builtin.artifacts.merge import ArtifactMergeSecurity, ArtifactMergeService, MergeTags
from powercontext.builtin.artifacts.merge_models import (
    ArtifactMergeErrors,
    ArtifactMergeMutationResult,
    ArtifactMergePlan,
    ArtifactMergeRead,
    ArtifactMergeRecord,
    ArtifactMergeRestoreItem,
    ArtifactMergeWrite,
    PreparedArtifactMerge,
)
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactLifecycleState,
    decode_artifact_governance,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexHit, PreparedAtomicMemoryProjection
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sources import _SourceReadSet
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.atomic_memory.restoration import AtomicMemoryPreviewSigner


class AtomicMemoryProjections(Protocol):
    async def prepare(self, content: AtomicMemoryContent) -> PreparedAtomicMemoryProjection: ...
    def validate_prepared(self, prepared: PreparedAtomicMemoryProjection) -> None: ...
    async def publish(
        self,
        connection: AsyncConnection,
        scope_id: str,
        record: AtomicMemoryRecord,
        prepared: PreparedAtomicMemoryProjection,
    ) -> None: ...
    async def publish_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        records: Sequence[tuple[AtomicMemoryRecord, PreparedAtomicMemoryProjection]],
    ) -> None: ...
    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str) -> None: ...


ATOMIC_MERGE_ERRORS = ArtifactMergeErrors(
    conflict=AtomicMemoryConflictError,
    relation=AtomicMemoryRelationError,
    invalid_state=InvalidAtomicMemoryStateError,
    invalid_preview=InvalidAtomicMemoryPreviewError,
    preview_expired=AtomicMemoryPreviewExpiredError,
    preview_stale=AtomicMemoryPreviewStaleError,
)
AtomicMemorySecurity = ArtifactMergeSecurity


def _lifecycle(state: AtomicMemoryStateValue) -> ArtifactLifecycleState:
    return {
        AtomicMemoryStateValue.ACTIVE: ArtifactLifecycleState.ACTIVE,
        AtomicMemoryStateValue.FORGOTTEN: ArtifactLifecycleState.DEPRECATED,
        AtomicMemoryStateValue.MERGED: ArtifactLifecycleState.DEPRECATED,
        AtomicMemoryStateValue.RETIRED: ArtifactLifecycleState.RETIRED,
    }[state]


def _atomic_state(state: ArtifactGovernance) -> AtomicMemoryState:
    if state.replacement_artifact_id is not None:
        raise AtomicMemoryRelationError("Atomic Memory does not use generic governance replacements")  # noqa: TRY003
    value = {
        ArtifactLifecycleState.ACTIVE: AtomicMemoryStateValue.ACTIVE,
        ArtifactLifecycleState.DEPRECATED: AtomicMemoryStateValue.FORGOTTEN
        if state.merged_into_id is None
        else AtomicMemoryStateValue.MERGED,
        ArtifactLifecycleState.RETIRED: AtomicMemoryStateValue.RETIRED,
    }[state.lifecycle_state]
    return AtomicMemoryState(
        state=value, state_version=state.governance_generation, merged_into_id=state.merged_into_id
    )


def _common_read(read: AtomicMemoryRead) -> ArtifactMergeRead:
    return ArtifactMergeRead(
        ref=read.ref,
        lifecycle_state=_lifecycle(read.state),
        state_version=read.state_version,
        merged_into_id=read.merged_into_id,
    )


def _atomic_read(read: ArtifactMergeRead) -> AtomicMemoryRead:
    return AtomicMemoryRead(
        ref=read.ref,
        **_atomic_state(
            ArtifactGovernance(
                artifact=read.ref,
                lifecycle_state=read.lifecycle_state,
                governance_generation=read.state_version,
                merged_into_id=read.merged_into_id,
                replacement_artifact_id=read.replacement_artifact_id,
            )
        ).model_dump(),
    )


def _common_record(record: AtomicMemoryRecord) -> ArtifactMergeRecord:
    return ArtifactMergeRecord(
        artifact=record.artifact,
        state=ArtifactGovernance(
            artifact=record.ref,
            lifecycle_state=_lifecycle(record.state.state),
            governance_generation=record.state.state_version,
            merged_into_id=record.state.merged_into_id,
        ),
    )


def _atomic_record(record: ArtifactMergeRecord) -> AtomicMemoryRecord:
    if not isinstance(record.artifact, AtomicMemory):
        raise AtomicMemoryRelationError("the stored artifact is not an Atomic Memory")  # noqa: TRY003
    return AtomicMemoryRecord(artifact=record.artifact, state=_atomic_state(record.state))


def _common_plan(plan: AtomicMemoryPlan) -> ArtifactMergePlan:
    return ArtifactMergePlan(
        scope_id=plan.scope_id,
        subject=plan.subject,
        reads=tuple(_common_read(read) for read in plan.reads),
        writes=tuple(
            ArtifactMergeWrite(
                artifact_id=write.artifact_id,
                state=_lifecycle(write.state),
                draft=write.draft,
                current=None if write.current is None else _common_record(write.current),
                merged_into_id=write.merged_into_id,
                merge_input_ids=write.merge_input_ids,
            )
            for write in plan.writes
        ),
        primary_artifact_id=plan.primary_artifact_id,
        operation=plan.operation,
        endpoint=None if plan.endpoint is None else _common_read(plan.endpoint),
        target_revision=plan.target_revision,
        undo_merge_results=plan.undo_merge_results,
        restore=tuple(ArtifactMergeRestoreItem(**item.model_dump()) for item in plan.restore),
        preview_token=plan.preview_token,
    )


def _atomic_plan(plan: ArtifactMergePlan) -> AtomicMemoryPlan:
    return AtomicMemoryPlan(
        scope_id=plan.scope_id,
        subject=plan.subject,
        reads=tuple(_atomic_read(read) for read in plan.reads),
        writes=tuple(
            AtomicMemoryWrite(
                artifact_id=write.artifact_id,
                state=AtomicMemoryStateValue.MERGED
                if write.merged_into_id is not None
                else {
                    ArtifactLifecycleState.ACTIVE: AtomicMemoryStateValue.ACTIVE,
                    ArtifactLifecycleState.DEPRECATED: AtomicMemoryStateValue.FORGOTTEN,
                    ArtifactLifecycleState.RETIRED: AtomicMemoryStateValue.RETIRED,
                }[write.state],
                draft=cast(AtomicMemoryDraft | None, write.draft),
                current=None if write.current is None else _atomic_record(write.current),
                merged_into_id=write.merged_into_id,
                merge_input_ids=write.merge_input_ids,
            )
            for write in plan.writes
        ),
        primary_artifact_id=plan.primary_artifact_id,
        operation=plan.operation,
        endpoint=None if plan.endpoint is None else _atomic_read(plan.endpoint),
        target_revision=plan.target_revision,
        undo_merge_results=plan.undo_merge_results,
        restore=tuple(AtomicMemoryRestoreItem(**item.model_dump()) for item in plan.restore),
        preview_token=plan.preview_token,
    )


class AtomicMemoryMergeAdapter:
    family = AtomicMemory.family

    def __init__(self, projections: AtomicMemoryProjections) -> None:
        self.projections = projections

    def draft(
        self,
        content: Any,
        lineage: ArtifactLineage,
        *,
        merge_inputs: tuple[ArtifactRef, ...] = (),
        historical: bool = False,
    ) -> AtomicMemoryDraft:
        content = AtomicMemoryContent.model_validate(content)
        if historical:
            content = content.without_creation()
        elif content.creation is not None:
            raise AtomicMemoryRelationError("creation metadata is constructed only by the merge service")  # noqa: TRY003
        if lineage.publication_source is not None:
            raise AtomicMemoryRelationError("Atomic Memory accepts direct Sources and exact in-Scope Artifacts")  # noqa: TRY003
        if historical:
            return AtomicMemoryDraft(content=content, sources=lineage.sources, artifacts=lineage.artifacts)
        try:
            text = normalize_text(content.text)
        except (TypeError, ValueError) as error:
            raise InvalidMemoryCandidateError(
                "canonical", str(error), canonical_code=canonical_error_code(error)
            ) from error
        content = content.model_copy(update={"text": text})
        if merge_inputs:
            content = content.model_copy(
                update={
                    "creation": AtomicMemoryCreation(input_artifact_ids=tuple(ref.artifact_id for ref in merge_inputs))
                }
            )
        return AtomicMemoryDraft(content=content, sources=lineage.sources, artifacts=lineage.artifacts)

    async def prepare(self, content: AtomicMemoryContent) -> PreparedAtomicMemoryProjection:
        return await self.projections.prepare(content)

    def validate_prepared(self, prepared: PreparedAtomicMemoryProjection) -> None:
        self.projections.validate_prepared(prepared)

    async def publish(
        self,
        connection: AsyncConnection,
        scope_id: str,
        record: ArtifactMergeRecord,
        prepared: PreparedAtomicMemoryProjection,
        execution_context: Any,
    ) -> None:
        await self.projections.publish(connection, scope_id, _atomic_record(record), prepared)

    async def publish_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        records: Sequence[tuple[ArtifactMergeRecord, PreparedAtomicMemoryProjection]],
        execution_context: Any,
    ) -> None:
        await self.projections.publish_many(
            connection, scope_id, tuple((_atomic_record(record), prepared) for record, prepared in records)
        )

    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str) -> None:
        await self.projections.remove(connection, scope_id, artifact_id)


class _AtomicPreviewAdapter:
    def __init__(self, signer):
        self.signer = signer

    def encode(self, plan):
        return self.signer.encode(_atomic_plan(plan))

    def validate(self, *args, **kwargs):
        return _common_read(self.signer.validate(*args, **kwargs))


class _AtomicMemoryMergeService(ArtifactMergeService):
    """Apply the same summary invariant to generic and Atomic-specific merge entry points."""

    @override
    async def prepare_change(self, plan: ArtifactMergePlan) -> PreparedArtifactMerge:
        # Persist the description with the final normalized body before preparing its projection.
        if plan.operation in {"change", "merge"}:
            writes = []
            for write in plan.writes:
                draft = write.draft
                if draft is not None and draft.content.description is None:
                    content = AtomicMemoryContent.model_validate({
                        **draft.content.model_dump(mode="json", by_alias=True),
                        "description": default_description(draft.content.text),
                    })
                    draft = draft.model_copy(update={"content": content})
                writes.append(replace(write, draft=draft))
            plan = replace(plan, writes=tuple(writes))
        return await super().prepare_change(plan)

    @override
    async def commit_window(
        self,
        connection: AsyncConnection,
        scope_id: str,
        prepared: Sequence[PreparedArtifactMerge],
        context: Any,
        *,
        read_set: Sequence[ArtifactMergeRead] = (),
        direct_source: SourceRef | None = None,
        direct_sources: Sequence[SourceRef | None] | None = None,
        source_read_set: _SourceReadSet | None = None,
    ) -> tuple[ArtifactMergeMutationResult, ...]:
        # Prepared content and projection hashes are already fixed. Reject malformed
        # prepared writes here rather than applying defaults after preparation.
        for item in prepared:
            if item.plan.operation in {"change", "merge"}:
                for write in item.plan.writes:
                    if write.draft is not None and write.draft.content.description is None:
                        raise InvalidMemoryCandidateError("canonical", "description is required for new writes")
        return await super().commit_window(
            connection,
            scope_id,
            prepared,
            context,
            read_set=read_set,
            direct_source=direct_source,
            direct_sources=direct_sources,
            source_read_set=source_read_set,
        )


class AtomicMemoryService:
    """Keep Atomic public contracts while delegating all transitions to the common service."""

    def __init__(
        self,
        *,
        artifacts: ArtifactRepository,
        states: AtomicMemoryStateRepository,
        security: AtomicMemorySecurity,
        projections: AtomicMemoryProjections,
        merge_tags: MergeTags,
        preview_signer: AtomicMemoryPreviewSigner | None = None,
    ) -> None:
        self.artifacts, self.states, self.security = artifacts, states, security
        self.projections, self.merge_tags, self.preview_signer = projections, merge_tags, preview_signer
        self.shared = _AtomicMemoryMergeService(
            artifacts=artifacts,
            adapter=AtomicMemoryMergeAdapter(projections),
            security=security,
            merge_tags=merge_tags,
            errors=ATOMIC_MERGE_ERRORS,
            preview_signer=None if preview_signer is None else _AtomicPreviewAdapter(preview_signer),
        )

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        context: Any,
        *,
        revision: int | None = None,
        for_update: bool = False,
    ) -> AtomicMemoryRecord:
        return _atomic_record(
            await self.shared.get(connection, scope_id, artifact_id, context, revision=revision, for_update=for_update)
        )

    async def read_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        heads: Sequence[Mapping[Any, Any]],
    ) -> tuple[AtomicMemoryRecord, ...]:
        """Hydrate a snapshot's heads without rereading their Revision or governance."""
        states = tuple(decode_artifact_governance(head) for head in heads)
        artifacts = await self.artifacts.decode_many(connection, scope_id, heads)
        return tuple(
            _atomic_record(ArtifactMergeRecord(artifact=artifact, state=state))
            for artifact, state in zip(artifacts, states, strict=True)
        )

    async def authorize_comparison_hit(
        self,
        connection: AsyncConnection,
        scope_id: str,
        head: Mapping[Any, Any],
        hit: AtomicMemoryIndexHit,
        context: Any,
    ) -> AtomicMemoryRead:
        """Validate a recalled body against its authoritative head, without expanding lineage."""
        state = decode_artifact_governance(head)
        if head["content"] is None:
            raise RepositoryNotFoundError("artifact", (scope_id, state.artifact))
        content = self.artifacts.decode_content(head)
        if not isinstance(content, AtomicMemoryContent):
            raise AtomicMemoryRelationError("the stored artifact is not an Atomic Memory")  # noqa: TRY003
        read = AtomicMemoryRead(ref=state.artifact, **_atomic_state(state).model_dump())
        await self.security.authorize(connection, scope_id, context, "write", read.ref)
        if (
            read.ref != hit.artifact_ref
            or read.state_version != hit.state_version
            or read.state is not AtomicMemoryStateValue.ACTIVE
            or content.kind != hit.kind
            or content.text != hit.text
        ):
            raise AtomicMemoryConflictError("Related memory changed before comparison")  # noqa: TRY003
        return read

    async def read_state(self, connection, scope_id: str, artifact_id: str, context) -> AtomicMemoryRead:
        """Read the current reference and lifecycle from one authoritative head."""
        ref = ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1)
        current = None
        with suppress(RepositoryNotFoundError):
            current = await self.shared.states.get(connection, scope_id, "atomic-memory", artifact_id)
        await self.security.authorize(connection, scope_id, context, "read", ref)
        if current is None:
            raise ArtifactNotFoundError(ref)
        return AtomicMemoryRead(ref=current.artifact, **_atomic_state(current).model_dump())

    async def inspect_change(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: AtomicMemoryContent,
        context: Any,
        *,
        expected_revision: int | None = None,
        expected_state_version: int | None = None,
        lineage: ArtifactLineage | None = None,
        reads: Sequence[AtomicMemoryRead] = (),
        source_read_set: _SourceReadSet | None = None,
    ) -> AtomicMemoryPlan:
        return _atomic_plan(
            await self.shared.inspect_change(
                connection,
                scope_id,
                artifact_id,
                content,
                context,
                expected_revision=expected_revision,
                expected_state_version=expected_state_version,
                lineage=lineage,
                reads=tuple(_common_read(read) for read in reads),
                source_read_set=source_read_set,
            )
        )

    async def inspect_creates(
        self,
        connection: AsyncConnection,
        scope_id: str,
        items: Sequence[tuple[str, AtomicMemoryContent]],
        context: Any,
    ) -> tuple[AtomicMemoryPlan, ...]:
        return tuple(
            _atomic_plan(plan) for plan in await self.shared.inspect_creates(connection, scope_id, items, context)
        )

    async def inspect_merge(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        inputs: Sequence[AtomicMemoryRead],
        content: AtomicMemoryContent,
        context: Any,
        *,
        lineage: ArtifactLineage | None = None,
        reads: Sequence[AtomicMemoryRead] = (),
        source_read_set: _SourceReadSet | None = None,
    ) -> AtomicMemoryPlan:
        return _atomic_plan(
            await self.shared.inspect_merge(
                connection,
                scope_id,
                artifact_id,
                tuple(_common_read(read) for read in inputs),
                content,
                context,
                lineage=lineage,
                reads=tuple(_common_read(read) for read in reads),
                source_read_set=source_read_set,
            )
        )

    async def inspect_forget(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        context: Any,
        *,
        expected_revision: int,
        expected_state_version: int,
    ) -> AtomicMemoryPlan:
        return _atomic_plan(
            await self.shared.inspect_forget(
                connection,
                scope_id,
                artifact_id,
                context,
                expected_revision=expected_revision,
                expected_state_version=expected_state_version,
            )
        )

    async def inspect_restore(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        context: Any,
        *,
        operation: AtomicMemoryRestoreOperation = "restore",
        revision: int | None = None,
        preview_token: str | None = None,
    ) -> AtomicMemoryPlan:
        return _atomic_plan(
            await self.shared.inspect_restore(
                connection,
                scope_id,
                artifact_id,
                context,
                operation=operation,
                revision=revision,
                preview_token=preview_token,
            )
        )

    def restoration_preview(self, plan: AtomicMemoryPlan) -> AtomicMemoryRestorationPreview:
        preview = self.shared.restoration_preview(_common_plan(plan))
        return AtomicMemoryRestorationPreview(
            preview_token=preview.preview_token,
            expires_at=preview.expires_at,
            endpoint=_atomic_read(preview.endpoint),
            restore=tuple(AtomicMemoryRestoreItem(**item.model_dump()) for item in preview.restore),
            retire=preview.retire,
            undo_merge_results=preview.undo_merge_results,
        )

    async def prepare_change(self, plan: AtomicMemoryPlan) -> PreparedAtomicMemory:
        prepared = await self.shared.prepare_change(_common_plan(plan))
        return PreparedAtomicMemory(plan=_atomic_plan(prepared.plan), projections=prepared.projections)

    prepare_merge = prepare_change
    prepare_forget = prepare_change
    prepare_restore = prepare_change

    async def validate_read_set(
        self, connection: AsyncConnection, scope_id: str, reads: Sequence[AtomicMemoryRead], context: Any
    ) -> None:
        await self.shared.validate_read_set(connection, scope_id, tuple(_common_read(read) for read in reads), context)

    async def commit(
        self,
        connection: AsyncConnection,
        prepared: PreparedAtomicMemory,
        context: Any,
        *,
        direct_source: SourceRef | None = None,
    ) -> AtomicMemoryMutationResult:
        return (
            await self.commit_window(
                connection, prepared.plan.scope_id, (prepared,), context, direct_source=direct_source
            )
        )[0]

    async def commit_window(
        self,
        connection: AsyncConnection,
        scope_id: str,
        prepared: Sequence[PreparedAtomicMemory],
        context: Any,
        *,
        read_set: Sequence[AtomicMemoryRead] = (),
        direct_source: SourceRef | None = None,
        direct_sources: Sequence[SourceRef | None] | None = None,
        source_read_set: _SourceReadSet | None = None,
    ) -> tuple[AtomicMemoryMutationResult, ...]:
        results = await self.shared.commit_window(
            connection,
            scope_id,
            tuple(
                PreparedArtifactMerge(plan=_common_plan(item.plan), projections=item.projections) for item in prepared
            ),
            context,
            read_set=tuple(_common_read(read) for read in read_set),
            direct_source=direct_source,
            direct_sources=direct_sources,
            source_read_set=source_read_set,
        )
        return tuple(
            AtomicMemoryMutationResult(
                changed=result.changed,
                records=tuple(_atomic_record(record) for record in result.records),
                primary_artifact_id=result.primary_artifact_id,
                retired=result.retired,
                undo_merge_results=result.undo_merge_results,
            )
            for result in results
        )
