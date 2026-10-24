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

"""Shared Artifact merge, whole-group restoration and transactional publication."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactDraft, ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.merge_models import (
    ArtifactMergeErrors,
    ArtifactMergeMutationResult,
    ArtifactMergePlan,
    ArtifactMergeRead,
    ArtifactMergeRecord,
    ArtifactMergeRestorationPreview,
    ArtifactMergeRestoreOperation,
    ArtifactMergeWrite,
    PreparedArtifactMerge,
)
from powercontext.builtin.artifacts.merge_restoration import ArtifactMergePreviewSigner, calculate_restoration
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactGovernanceRepository,
    ArtifactLifecycleState,
    decode_artifact_governance,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository, _ValidatedLineageSources
from powercontext.builtin.persistence.database import SELECTION_BATCH_SIZE
from powercontext.builtin.persistence.sources import SourceRepository, _SourceReadSet
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.builtin.sources.content import (
    ArtifactRestorationOutcome,
    ArtifactRestorationWrite,
    ContentSource,
    ContentSourceInternal,
    ContentSourceTarget,
)
from powercontext.errors import RevisionConflictError
from powercontext.sources import SourceMaterialization, SourceRef


class ArtifactMergeSecurity(Protocol):
    """Explicit policy shared by HTTP and background callers; none is implicit."""

    def subject(self, context: Any) -> str: ...

    async def authorize(
        self,
        connection: AsyncConnection,
        scope_id: str,
        context: Any,
        action: Literal["read", "write", "create"],
        ref: ArtifactRef | None = None,
    ) -> None: ...

    async def authorize_sources(
        self, connection: AsyncConnection, scope_id: str, context: Any, sources: tuple[SourceRef, ...]
    ) -> None: ...

    async def authorize_creates(
        self, connection: AsyncConnection, scope_id: str, context: Any, refs: Sequence[ArtifactRef]
    ) -> None: ...

    async def establish_owners(
        self, connection: AsyncConnection, scope_id: str, artifact_ids: Sequence[str], context: Any
    ) -> None: ...


class ArtifactMergeAdapter(Protocol):
    family: str

    def draft(
        self,
        content: Any,
        lineage: ArtifactLineage,
        *,
        merge_inputs: tuple[ArtifactRef, ...] = (),
        historical: bool = False,
    ) -> ArtifactDraft[Any]: ...
    async def prepare(self, content: Any) -> Any: ...
    def validate_prepared(self, prepared: Any) -> None: ...
    async def publish(
        self,
        connection: AsyncConnection,
        scope_id: str,
        record: ArtifactMergeRecord,
        prepared: Any,
        execution_context: Any,
    ) -> None: ...
    async def publish_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        records: Sequence[tuple[ArtifactMergeRecord, Any]],
        execution_context: Any,
    ) -> None: ...
    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str) -> None: ...


MergeTags = Callable[[AsyncConnection, str, str, tuple[str, ...]], Awaitable[None]]


class ArtifactMergeService:
    """Own Family transitions while leaving transaction lifetime with the caller.

    Inspect under a read transaction, close it, and prepare vectors without a
    connection. Commit receives the caller's write transaction; it never
    commits or retries a transaction whose outcome it does not own.
    """

    def __init__(
        self,
        *,
        artifacts: ArtifactRepository,
        adapter: ArtifactMergeAdapter,
        security: ArtifactMergeSecurity,
        merge_tags: MergeTags,
        preview_signer: Any = None,
        errors: ArtifactMergeErrors | None = None,
        sources: SourceRepository | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.states = ArtifactGovernanceRepository()
        self.adapter = adapter
        self.family = adapter.family
        self.errors = errors or ArtifactMergeErrors()
        self.security = security
        self.projections = adapter
        self.merge_tags = merge_tags
        self.preview_signer = preview_signer
        self.sources = artifacts.source_repository if sources is None else sources

    async def get(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        context: Any,
        *,
        revision: int | None = None,
        for_update: bool = False,
    ) -> ArtifactMergeRecord:
        identity = ArtifactRef(
            family=self.family, artifact_id=artifact_id, revision=1 if revision is None else revision
        )
        await self.security.authorize(connection, scope_id, context, "read", identity)
        if revision is None:
            stored = await self.artifacts.latest(connection, scope_id, self.family, artifact_id, for_update=for_update)
        else:
            stored = await self.artifacts.get(connection, scope_id, identity, for_update=for_update)
        state = await self.states.get(connection, scope_id, self.family, artifact_id, for_update=for_update)
        return ArtifactMergeRecord(artifact=stored, state=state)

    async def inspect_change(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        context: Any,
        *,
        expected_revision: int | None = None,
        expected_state_version: int | None = None,
        lineage: ArtifactLineage | None = None,
        reads: Sequence[ArtifactMergeRead] = (),
        source_read_set: _SourceReadSet | None = None,
    ) -> ArtifactMergePlan:
        return await self._inspect_change(
            connection,
            scope_id,
            artifact_id,
            content,
            context,
            expected_revision=expected_revision,
            expected_state_version=expected_state_version,
            lineage=lineage,
            reads=reads,
            source_read_set=source_read_set,
            create_authorized=False,
        )

    async def inspect_creates(
        self,
        connection: AsyncConnection,
        scope_id: str,
        items: Sequence[tuple[str, BaseModel]],
        context: Any,
    ) -> tuple[ArtifactMergePlan, ...]:
        if not items:
            return ()
        await self.security.authorize_creates(
            connection,
            scope_id,
            context,
            tuple(ArtifactRef(family=self.family, artifact_id=artifact_id, revision=1) for artifact_id, _ in items),
        )
        return tuple([
            await self._inspect_change(connection, scope_id, artifact_id, content, context, create_authorized=True)
            for artifact_id, content in items
        ])

    async def _inspect_change(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        context: Any,
        *,
        expected_revision: int | None = None,
        expected_state_version: int | None = None,
        lineage: ArtifactLineage | None = None,
        reads: Sequence[ArtifactMergeRead] = (),
        source_read_set: _SourceReadSet | None = None,
        create_authorized: bool,
    ) -> ArtifactMergePlan:
        draft = self.adapter.draft(content, lineage or ArtifactLineage())
        current = None
        if expected_revision is None:
            target = ArtifactRef(family=self.family, artifact_id=artifact_id, revision=1)
            if not create_authorized:
                await self.security.authorize(connection, scope_id, context, "create", target)
            state = ArtifactLifecycleState.ACTIVE
        else:
            current = await self.get(connection, scope_id, artifact_id, context)
            await self.security.authorize(connection, scope_id, context, "write", current.ref)
            self._expected(current, expected_revision, expected_state_version, draft)
            if current.state.merged_into_id is not None or current.state.lifecycle_state not in {
                ArtifactLifecycleState.ACTIVE,
                ArtifactLifecycleState.DEPRECATED,
            }:
                raise self.errors.invalid_state("merged and retired Artifacts cannot be edited")  # noqa: TRY003
            target = ArtifactRef(family=self.family, artifact_id=artifact_id, revision=current.artifact.revision + 1)
            state = current.state.lifecycle_state
            draft = draft.model_copy(update={"artifacts": _unique_refs((*draft.artifacts, current.ref))})
        await self._validate_draft(connection, scope_id, target, draft, context, source_read_set=source_read_set)
        dependencies = (*reads, *((current.as_read(),) if current is not None else ()))
        await self._check_dependencies(connection, scope_id, dependencies, context)
        return ArtifactMergePlan(
            scope_id=scope_id,
            subject=self.security.subject(context),
            reads=self._unique_reads(dependencies),
            writes=(
                ArtifactMergeWrite(
                    artifact_id=artifact_id,
                    state=state,
                    draft=draft,
                    current=current,
                    replacement_artifact_id=None if current is None else current.state.replacement_artifact_id,
                ),
            ),
            primary_artifact_id=artifact_id,
            operation="change",
        )

    async def restoration_outcome(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        revision: int,
        context: Any,
    ) -> ArtifactRestorationOutcome | None:
        """Read a primary revision's saved outcome after authorizing every affected identity."""

        record = await self.get(connection, scope_id, artifact_id, context, revision=revision)
        if self.sources is None:
            return None
        for source_ref in record.artifact.lineage.sources:
            source = (await self.sources.get(connection, scope_id, source_ref)).value
            if not isinstance(source, ContentSource) or source.internal is None:
                continue
            internal = source.internal
            if (
                internal.target
                != ContentSourceTarget(
                    scope_id=scope_id, family=self.family, artifact_id=artifact_id, revision=revision
                )
                or internal.restoration_outcome is None
            ):
                continue
            outcome = internal.restoration_outcome
            refs = _unique_refs((
                outcome.target,
                *outcome.undo_merge_results,
                *(
                    ref
                    for write in outcome.writes
                    for ref in (write.before_ref, write.content_from_ref, write.after_ref)
                ),
            ))
            for ref in refs:
                await self.security.authorize(connection, scope_id, context, "read", ref)
            return outcome
        return None

    async def inspect_merge(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        inputs: Sequence[ArtifactMergeRead],
        content: BaseModel,
        context: Any,
        *,
        lineage: ArtifactLineage | None = None,
        reads: Sequence[ArtifactMergeRead] = (),
        source_read_set: _SourceReadSet | None = None,
    ) -> ArtifactMergePlan:
        selected = tuple(inputs)
        ids = tuple(read.ref.artifact_id for read in selected)
        if (
            len(ids) < 2
            or len(set(ids)) != len(ids)
            or artifact_id in ids
            or any(read.ref.family != self.family for read in selected)
        ):
            raise self.errors.relation("merge needs at least two distinct inputs and a new result identity")  # noqa: TRY003
        target = ArtifactRef(family=self.family, artifact_id=artifact_id, revision=1)
        await self.security.authorize(connection, scope_id, context, "create", target)
        current_inputs: list[ArtifactMergeRecord] = []
        for expected in selected:
            current = await self.get(connection, scope_id, expected.ref.artifact_id, context)
            await self.security.authorize(connection, scope_id, context, "write", current.ref)
            if current.as_read() != expected:
                raise self.errors.conflict("merge inputs changed before preparation")  # noqa: TRY003
            if (
                current.state.lifecycle_state is not ArtifactLifecycleState.ACTIVE
                or current.state.merged_into_id is not None
            ):
                raise self.errors.invalid_state("only active Artifacts can be merge inputs")  # noqa: TRY003
            current_inputs.append(current)
        draft = self.adapter.draft(
            content, lineage or ArtifactLineage(), merge_inputs=tuple(read.ref for read in selected)
        )
        # An explicitly supplied lineage must already contain every selected
        # exact reference. Internal callers may omit lineage and use inputs.
        if lineage is None:
            draft = draft.model_copy(update={"artifacts": tuple(read.ref for read in selected)})
        self._require_merge_draft(artifact_id, draft, selected)
        await self._validate_draft(connection, scope_id, target, draft, context, source_read_set=source_read_set)
        dependencies = self._unique_reads((*reads, *selected))
        await self._check_dependencies(connection, scope_id, dependencies, context)
        writes = (
            ArtifactMergeWrite(
                artifact_id=artifact_id, state=ArtifactLifecycleState.ACTIVE, draft=draft, merge_input_ids=ids
            ),
            *(
                ArtifactMergeWrite(
                    artifact_id=current.artifact.artifact_id,
                    state=ArtifactLifecycleState.DEPRECATED,
                    merged_into_id=artifact_id,
                    current=current,
                )
                for current in current_inputs
            ),
        )
        return ArtifactMergePlan(
            scope_id=scope_id,
            subject=self.security.subject(context),
            reads=dependencies,
            writes=writes,
            primary_artifact_id=artifact_id,
            operation="merge",
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
    ) -> ArtifactMergePlan:
        current = await self.get(connection, scope_id, artifact_id, context)
        await self.security.authorize(connection, scope_id, context, "write", current.ref)
        self._expected(current, expected_revision, expected_state_version)
        if current.state.merged_into_id is not None or current.state.lifecycle_state not in {
            ArtifactLifecycleState.ACTIVE,
            ArtifactLifecycleState.DEPRECATED,
        }:
            raise self.errors.invalid_state("only active or deprecated Artifacts can be deprecated")  # noqa: TRY003
        writes = (
            ()
            if current.state.lifecycle_state is ArtifactLifecycleState.DEPRECATED
            else (
                ArtifactMergeWrite(artifact_id=artifact_id, state=ArtifactLifecycleState.DEPRECATED, current=current),
            )
        )
        return ArtifactMergePlan(
            scope_id=scope_id,
            subject=self.security.subject(context),
            reads=(current.as_read(),),
            writes=writes,
            primary_artifact_id=artifact_id,
            operation="forget",
        )

    async def inspect_restore(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        context: Any,
        *,
        operation: ArtifactMergeRestoreOperation = "restore",
        revision: int | None = None,
        preview_token: str | None = None,
    ) -> ArtifactMergePlan:
        if operation not in {"restore", "undo_merge"}:
            raise self.errors.invalid_state("unknown restoration operation")  # noqa: TRY003
        if operation == "undo_merge" and revision is not None:
            raise self.errors.invalid_state("undo_merge does not accept a content revision")  # noqa: TRY003
        target = await self.get(connection, scope_id, artifact_id, context)
        token_endpoint = (
            None
            if preview_token is None
            else self._signer().validate(
                preview_token,
                scope_id=scope_id,
                subject=self.security.subject(context),
                operation=operation,
                artifact_id=artifact_id,
                revision=revision,
            )
        )

        async def load_current(identity: str) -> ArtifactMergeRecord:
            record = await self.get(connection, scope_id, identity, context)
            await self.security.authorize(connection, scope_id, context, "write", record.ref)
            return record

        async def load_inputs(identity: str) -> tuple[ArtifactRef, ...]:
            ref = ArtifactRef(family=self.family, artifact_id=identity, revision=1)
            await self.security.authorize(connection, scope_id, context, "read", ref)
            return await self.artifacts.merge_inputs(connection, scope_id, ref)

        await self.security.authorize(connection, scope_id, context, "write", target.ref)
        selected = None
        if revision is not None:
            selected = (await self.get(connection, scope_id, artifact_id, context, revision=revision)).artifact
        plan = await calculate_restoration(
            scope_id=scope_id,
            subject=self.security.subject(context),
            target=target,
            operation=operation,
            selected=selected,
            load_current=load_current,
            load_inputs=load_inputs,
            adapter=self.adapter,
            errors=self.errors,
        )
        if token_endpoint is not None and plan.endpoint != token_endpoint:
            raise self.errors.preview_stale("the restoration preview endpoint changed")  # noqa: TRY003
        return replace(plan, preview_token=preview_token)

    def restoration_preview(self, plan: ArtifactMergePlan) -> ArtifactMergeRestorationPreview:
        token, expires_at = self._signer().encode(plan)
        if plan.endpoint is None:
            raise self.errors.invalid_preview("the restoration plan has no endpoint")  # noqa: TRY003
        retire_ids = {write.artifact_id for write in plan.writes if write.state is ArtifactLifecycleState.RETIRED}
        return ArtifactMergeRestorationPreview(
            preview_token=token,
            expires_at=expires_at,
            endpoint=plan.endpoint,
            restore=plan.restore,
            retire=tuple(read.ref for read in plan.reads if read.ref.artifact_id in retire_ids),
            undo_merge_results=plan.undo_merge_results,
        )

    async def prepare_change(self, plan: ArtifactMergePlan) -> PreparedArtifactMerge:
        """Perform external preparation only, with no database connection."""

        projections: list[tuple[str, Any]] = []
        for write in plan.writes:
            if write.state is not ArtifactLifecycleState.ACTIVE:
                continue
            content = (
                write.draft.content
                if write.draft is not None
                else write.current.artifact.content
                if write.current is not None
                else None
            )
            if content is None:
                raise self.errors.relation("active publication has no content")  # noqa: TRY003
            projections.append((write.artifact_id, await self.projections.prepare(content)))
        return PreparedArtifactMerge(plan=plan, projections=tuple(projections))

    async def prepare_merge(self, plan: ArtifactMergePlan) -> PreparedArtifactMerge:
        return await self.prepare_change(plan)

    async def prepare_forget(self, plan: ArtifactMergePlan) -> PreparedArtifactMerge:
        return await self.prepare_change(plan)

    async def prepare_restore(self, plan: ArtifactMergePlan) -> PreparedArtifactMerge:
        return await self.prepare_change(plan)

    async def validate_read_set(
        self, connection: AsyncConnection, scope_id: str, reads: Sequence[ArtifactMergeRead], context: Any
    ) -> None:
        """Allow no-op processing plans to protect their actual decision dependencies."""

        await self.artifacts.lock_heads(connection, scope_id, tuple(read.ref for read in reads))
        await self._check_dependencies(connection, scope_id, reads, context, for_update=True)

    async def commit(
        self,
        connection: AsyncConnection,
        prepared: PreparedArtifactMerge,
        context: Any,
        *,
        direct_source: SourceRef | None = None,
    ) -> ArtifactMergeMutationResult:
        return (
            await self.commit_window(
                connection, prepared.plan.scope_id, (prepared,), context, direct_source=direct_source
            )
        )[0]

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
        """Validate all window dependencies before publishing any of its final decisions."""

        plans = tuple(item.plan for item in prepared)
        if direct_sources is not None and (direct_source is not None or len(direct_sources) != len(prepared)):
            raise ValueError("direct sources must match the prepared window")  # noqa: TRY003
        operation_sources = (direct_source,) * len(prepared) if direct_sources is None else tuple(direct_sources)
        subject = self.security.subject(context)
        self._require_window(plans, scope_id, subject)
        for item in prepared:
            for _, projection in item.projections:
                self.projections.validate_prepared(projection)
            projections = dict(item.projections)
            if any(
                write.state is ArtifactLifecycleState.ACTIVE and projections.get(write.artifact_id) is None
                for write in item.plan.writes
            ):
                raise self.errors.relation("active publication requires a prepared projection")  # noqa: TRY003
        reads = self._unique_reads((*read_set, *(read for plan in plans for read in plan.reads)))
        await self.artifacts.lock_heads(connection, scope_id, tuple(read.ref for read in reads))
        for plan in plans:
            await self._validate_preview(connection, plan, context)
        full_records = {
            identity
            for plan in plans
            for identity in (plan.primary_artifact_id, *(write.artifact_id for write in plan.writes))
        }
        current = await self._check_dependencies(
            connection, scope_id, reads, context, for_update=True, full_records=full_records
        )
        for plan in plans:
            if plan.operation in {"restore", "undo_merge"}:
                verified = await self.inspect_restore(
                    connection,
                    scope_id,
                    plan.primary_artifact_id,
                    context,
                    operation=cast(ArtifactMergeRestoreOperation, plan.operation),
                    revision=plan.target_revision,
                    preview_token=plan.preview_token,
                )
                if verified != plan:
                    raise self.errors.conflict("restoration relations changed; prepare the whole group again")  # noqa: TRY003
        independent_creates = all(
            item.plan.operation == "change"
            and len(item.plan.writes) == 1
            and item.plan.writes[0].current is None
            and item.plan.writes[0].draft is not None
            and not item.plan.writes[0].draft.artifacts
            and item.plan.writes[0].state is ArtifactLifecycleState.ACTIVE
            and not item.plan.writes[0].merge_input_ids
            and item.plan.writes[0].merged_into_id is None
            and item.plan.writes[0].replacement_artifact_id is None
            for item in prepared
        )
        groups = (
            (tuple(zip(prepared, operation_sources, strict=True)),)
            if independent_creates
            else tuple(((item, source),) for item, source in zip(prepared, operation_sources, strict=True))
        )
        results = []
        for group in groups:
            results.extend(await self._commit_locked(connection, group, current, context, source_read_set))
        return tuple(results)

    def _require_window(self, plans: Sequence[ArtifactMergePlan], scope_id: str, subject: str) -> None:
        identities: set[str] = set()
        for plan in plans:
            if plan.scope_id != scope_id or plan.subject != subject:
                raise self.errors.invalid_state("window scope or identity differs from preparation")  # noqa: TRY003
            self._require_plan(plan)
            for write in plan.writes:
                if write.artifact_id in identities:
                    raise self.errors.relation("window decisions must coordinate each Artifact into one final write")  # noqa: TRY003
                identities.add(write.artifact_id)

    async def _validate_preview(self, connection: AsyncConnection, plan: ArtifactMergePlan, context: Any) -> None:
        if plan.preview_token is None:
            return
        endpoint = self._signer().validate(
            plan.preview_token,
            scope_id=plan.scope_id,
            subject=plan.subject,
            operation=cast(ArtifactMergeRestoreOperation, plan.operation),
            artifact_id=plan.primary_artifact_id,
            revision=plan.target_revision,
        )
        current = await self.get(connection, plan.scope_id, endpoint.ref.artifact_id, context, for_update=True)
        if current.as_read() != endpoint:
            raise self.errors.preview_stale("the restoration preview endpoint changed")  # noqa: TRY003

    async def _commit_locked(
        self,
        connection: AsyncConnection,
        prepared: Sequence[tuple[PreparedArtifactMerge, SourceRef | None]],
        current: dict[str, ArtifactMergeRecord],
        context: Any,
        source_read_set: _SourceReadSet | None,
    ) -> tuple[ArtifactMergeMutationResult, ...]:
        """Apply independent plans through the same validated write lifecycle."""

        if not prepared:
            return ()
        scope_id = prepared[0][0].plan.scope_id
        writes = []
        projections = {}
        system_sources = {}
        merge_inputs = {}
        restorations = []
        for item, direct_source in prepared:
            plan = item.plan
            restoration = await self._record_restoration(connection, plan, current)
            restorations.append(restoration)
            projections.update(item.projections)
            for write in plan.writes:
                writes.append(write)
                restoration_source = (
                    restoration[1]
                    if restoration is not None and write.artifact_id == plan.primary_artifact_id
                    else None
                )
                system_sources[write.artifact_id] = tuple(
                    source for source in (direct_source, restoration_source) if source is not None
                )
                merge_inputs[write.artifact_id] = tuple(
                    (read.ref, read.state_version)
                    for read in plan.reads
                    if read.ref.artifact_id in write.merge_input_ids
                )
        committed = await self._commit_writes(
            connection, scope_id, writes, current, projections, context, system_sources, merge_inputs, source_read_set
        )
        results = []
        for (item, _), restoration in zip(prepared, restorations, strict=True):
            plan = item.plan
            records = {write.artifact_id: committed[write.artifact_id] for write in plan.writes}
            if plan.primary_artifact_id not in records:
                records[plan.primary_artifact_id] = current[plan.primary_artifact_id]
            if restoration is not None and any(
                records[write.after_ref.artifact_id].ref != write.after_ref
                or records[write.after_ref.artifact_id].state.lifecycle_state.value != write.lifecycle_state
                for write in restoration[0].writes
            ):
                raise self.errors.relation("restoration outcome differs from committed revisions")  # noqa: TRY003
            results.append(
                ArtifactMergeMutationResult(
                    changed=bool(plan.writes),
                    records=tuple(records[identity] for identity in sorted(records)),
                    primary_artifact_id=plan.primary_artifact_id,
                    retired=tuple(
                        record.ref
                        for record in records.values()
                        if record.state.lifecycle_state is ArtifactLifecycleState.RETIRED
                    ),
                    undo_merge_results=plan.undo_merge_results,
                )
            )
        return tuple(results)

    async def _record_restoration(
        self,
        connection: AsyncConnection,
        plan: ArtifactMergePlan,
        current: dict[str, ArtifactMergeRecord],
    ) -> tuple[ArtifactRestorationOutcome, SourceRef] | None:
        """Persist one outcome inside the already locked group transaction."""

        if plan.operation not in {"restore", "undo_merge"} or not plan.writes:
            return None
        if self.sources is None:
            raise self.errors.invalid_state("restoration requires a configured Source repository")  # noqa: TRY003
        writes: list[ArtifactRestorationWrite] = []
        for write in plan.writes:
            before = current[write.artifact_id].ref
            writes.append(
                ArtifactRestorationWrite(
                    before_ref=before,
                    content_from_ref=before
                    if write.artifact_id != plan.primary_artifact_id or plan.target_revision is None
                    else before.model_copy(update={"revision": plan.target_revision}),
                    after_ref=before.model_copy(update={"revision": before.revision + 1}),
                    lifecycle_state=cast(Literal["active", "retired"], write.state.value),
                )
            )
        outcome = ArtifactRestorationOutcome(
            operation=cast(ArtifactMergeRestoreOperation, plan.operation),
            target=current[plan.primary_artifact_id].ref,
            writes=tuple(writes),
            undo_merge_results=tuple(current[identity].ref for identity in plan.undo_merge_results),
        )
        primary = next(write.after_ref for write in writes if write.after_ref.artifact_id == plan.primary_artifact_id)
        source = await self.sources.add(
            connection,
            plan.scope_id,
            ContentSource(
                name=f"artifact-restoration_{uuid4().hex}",
                materialization=SourceMaterialization.CAPTURED,
                content="Artifact restoration completed.",
                internal=ContentSourceInternal(
                    role="lineage_only",
                    operation="artifact_restore" if plan.operation == "restore" else "artifact_undo_merge",
                    target=ContentSourceTarget(scope_id=plan.scope_id, **primary.model_dump()),
                    restoration_outcome=outcome,
                ),
            ),
        )
        return outcome, source.ref

    async def _commit_writes(  # noqa: C901 - One boundary owns validation, persistence, governance and publication.
        self,
        connection: AsyncConnection,
        scope_id: str,
        writes: Sequence[ArtifactMergeWrite],
        current: dict[str, ArtifactMergeRecord],
        projections: dict[str, Any],
        context: Any,
        system_sources: dict[str, tuple[SourceRef, ...]],
        merge_inputs: dict[str, tuple[tuple[ArtifactRef, int], ...]],
        source_read_set: _SourceReadSet | None,
    ) -> dict[str, ArtifactMergeRecord]:
        drafts = {}
        creates = []
        create_refs = []
        for write in writes:
            draft = write.draft
            if draft is None:
                continue
            before = current.get(write.artifact_id)
            target = ArtifactRef(
                family=self.family,
                artifact_id=write.artifact_id,
                revision=1 if before is None else before.artifact.revision + 1,
            )
            sources = system_sources[write.artifact_id]
            if sources:
                unique = {(source.source_type, source.source_id): source for source in (*draft.sources, *sources)}
                draft = draft.model_copy(update={"sources": tuple(unique.values())})
            validated = await self._validate_draft(
                connection, scope_id, target, draft, context, system_sources=sources, source_read_set=source_read_set
            )
            drafts[write.artifact_id] = (draft, validated)
            if before is None:
                create_refs.append(target)
                if not write.merge_input_ids:
                    creates.append((write.artifact_id, draft, validated))
        await self.security.authorize_creates(connection, scope_id, context, create_refs)
        created = {
            artifact.artifact_id: artifact
            for artifact in await self.artifacts.create_many(connection, scope_id, creates)
        }
        if created:
            await self.security.establish_owners(connection, scope_id, tuple(created), context)
        records = {}
        for write in writes:
            before = current.get(write.artifact_id)
            if write.draft is not None:
                draft, validated = drafts[write.artifact_id]
                if before is None:
                    if write.merge_input_ids:
                        artifact = await self.artifacts.create_merge_result(
                            connection,
                            scope_id,
                            write.artifact_id,
                            draft,
                            merge_inputs[write.artifact_id],
                            validated_sources=validated,
                        )
                        await self.security.establish_owners(connection, scope_id, (write.artifact_id,), context)
                        await self.merge_tags(connection, scope_id, write.artifact_id, write.merge_input_ids)
                    else:
                        artifact = created[write.artifact_id]
                    state = ArtifactGovernance(
                        artifact=artifact.as_ref(),
                        lifecycle_state=ArtifactLifecycleState.ACTIVE,
                        governance_generation=0,
                    )
                else:
                    state = before.state
                    if state.merged_into_id is not None:
                        artifact = await self.artifacts.revise_for_restoration(
                            connection, scope_id, before.artifact, draft, state, validated_sources=validated
                        )
                    else:
                        artifact = await self.artifacts.revise(
                            connection, scope_id, before.artifact, draft, validated_sources=validated
                        )
            else:
                if before is None:
                    raise self.errors.relation("state transitions require an existing Artifact")  # noqa: TRY003
                artifact, state = before.artifact, before.state
            if (
                before is not None
                or write.state is not ArtifactLifecycleState.ACTIVE
                or write.merged_into_id is not None
                or write.replacement_artifact_id is not None
            ):
                state = await self.states.transition_merge(
                    connection,
                    scope_id,
                    self.family,
                    write.artifact_id,
                    state,
                    write.state,
                    write.merged_into_id,
                    write.replacement_artifact_id,
                )
            records[write.artifact_id] = ArtifactMergeRecord(artifact=artifact, state=state)
        if (
            created
            and len(created) == len(writes)
            and all(record.state.lifecycle_state is ArtifactLifecycleState.ACTIVE for record in records.values())
        ):
            await self.projections.publish_many(
                connection,
                scope_id,
                tuple((record, projections[artifact_id]) for artifact_id, record in records.items()),
                context,
            )
        else:
            for artifact_id, record in records.items():
                if record.state.lifecycle_state is ArtifactLifecycleState.ACTIVE:
                    projection = projections.get(artifact_id)
                    if projection is None:
                        raise self.errors.relation("active publication requires a prepared projection")  # noqa: TRY003
                    await self.projections.publish(connection, scope_id, record, projection, context)
                else:
                    await self.projections.remove(connection, scope_id, artifact_id)
        return records

    async def _check_dependencies(
        self,
        connection: AsyncConnection,
        scope_id: str,
        reads: Sequence[ArtifactMergeRead],
        context: Any,
        *,
        for_update: bool = False,
        full_records: set[str] | None = None,
    ) -> dict[str, ArtifactMergeRecord]:
        records: dict[str, ArtifactMergeRecord] = {}
        head_states: dict[str, ArtifactGovernance] = {}
        if full_records is not None:
            read_only = tuple(read.ref.artifact_id for read in reads if read.ref.artifact_id not in full_records)
            for offset in range(0, len(read_only), SELECTION_BATCH_SIZE):
                rows = (
                    await connection.execute(
                        select(ARTIFACT_HEADS_TABLE).where(
                            ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                            ARTIFACT_HEADS_TABLE.c.family == self.family,
                            ARTIFACT_HEADS_TABLE.c.artifact_id.in_(read_only[offset : offset + SELECTION_BATCH_SIZE]),
                        )
                    )
                ).mappings()
                head_states.update({row["artifact_id"]: decode_artifact_governance(row) for row in rows})
        for expected in self._unique_reads(reads):
            if expected.ref.artifact_id in head_states:
                # The exact body and lineage were checked while preparing the plan.
                # After the ordered head locks, only this mutable summary can differ.
                await self.security.authorize(connection, scope_id, context, "read", expected.ref)
                state = head_states[expected.ref.artifact_id]
                await self.security.authorize(connection, scope_id, context, "write", state.artifact)
                if (
                    ArtifactMergeRead(
                        ref=state.artifact,
                        lifecycle_state=state.lifecycle_state,
                        state_version=state.governance_generation,
                        merged_into_id=state.merged_into_id,
                        replacement_artifact_id=state.replacement_artifact_id,
                    )
                    != expected
                ):
                    raise self.errors.conflict("Artifact content, state or merge relation changed; prepare again")  # noqa: TRY003
                continue
            current = await self.get(connection, scope_id, expected.ref.artifact_id, context, for_update=for_update)
            await self.security.authorize(connection, scope_id, context, "write", current.ref)
            if current.as_read() != expected:
                raise self.errors.conflict("Artifact content, state or merge relation changed; prepare again")  # noqa: TRY003
            if for_update:
                await self.states.require_summary(
                    connection, scope_id, self.family, expected.ref.artifact_id, current.state
                )
            records[expected.ref.artifact_id] = current
        return records

    async def _validate_draft(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target: ArtifactRef,
        draft: ArtifactDraft[Any],
        context: Any,
        *,
        system_sources: tuple[SourceRef, ...] = (),
        source_read_set: _SourceReadSet | None = None,
    ) -> _ValidatedLineageSources:
        # The host creates a lineage_only Source bound to this exact new target.
        # It is not caller-supplied evidence and need not grant Scope body read.
        ordinary_sources = tuple(source for source in draft.sources if source not in system_sources)
        await self.security.authorize_sources(connection, scope_id, context, ordinary_sources)
        validated_sources = await self.artifacts.validate_lineage_sources(
            connection, scope_id, target, draft.sources, source_read_set=source_read_set
        )
        for ref in draft.artifacts:
            await self.security.authorize(connection, scope_id, context, "read", ref)
        await self.artifacts.get_many(connection, scope_id, draft.artifacts)
        return validated_sources

    def _expected(
        self, current: ArtifactMergeRecord, revision: int, state_version: int | None, requested: Any = None
    ) -> None:
        if current.artifact.revision != revision:
            raise RevisionConflictError(requested, current.artifact)
        if state_version is not None and current.state.governance_generation != state_version:
            raise self.errors.conflict("the expected Artifact state version is stale")  # noqa: TRY003

    def _require_merge_draft(
        self, artifact_id: str, draft: ArtifactDraft[Any], inputs: Sequence[ArtifactMergeRead]
    ) -> None:
        refs = tuple(read.ref for read in inputs)
        if (
            len(refs) < 2
            or len({ref.artifact_id for ref in refs}) != len(refs)
            or artifact_id in {ref.artifact_id for ref in refs}
        ):
            raise self.errors.relation("merge requires distinct inputs and a new result identity")  # noqa: TRY003
        if draft.family != self.family or any(ref.family != self.family for ref in refs):
            raise self.errors.relation("merge inputs and result must have the same Family")  # noqa: TRY003
        selected = {(ref.family, ref.artifact_id, ref.revision) for ref in refs}
        lineage = {(ref.family, ref.artifact_id, ref.revision) for ref in draft.artifacts}
        if (
            any(sum(item == ref for item in draft.artifacts) != 1 for ref in refs)
            or not selected.issubset(lineage)
            or any(
                ref.family == self.family
                and ref.artifact_id in {item.artifact_id for item in refs}
                and (ref.family, ref.artifact_id, ref.revision) not in selected
                for ref in draft.artifacts
            )
        ):
            raise self.errors.relation("merge inputs require their supplied exact active versions in lineage")  # noqa: TRY003

    def _require_plan(self, plan: ArtifactMergePlan) -> None:
        ids = tuple(write.artifact_id for write in plan.writes)
        if len(ids) != len(set(ids)):
            raise self.errors.relation("one plan cannot write an identity twice")  # noqa: TRY003
        reads = {read.ref.artifact_id: read for read in self._unique_reads(plan.reads)}
        for write in plan.writes:
            if write.current is not None and write.current.state.lifecycle_state is ArtifactLifecycleState.RETIRED:
                raise self.errors.invalid_state("retired Artifact identities cannot be modified")  # noqa: TRY003
            if write.current is not None and reads.get(write.artifact_id) != write.current.as_read():
                raise self.errors.relation("every existing write requires its exact current read dependency")  # noqa: TRY003
            if write.current is None and (write.draft is None or write.state is not ArtifactLifecycleState.ACTIVE):
                raise self.errors.relation("new Artifacts require active content")  # noqa: TRY003
            if write.draft is None:
                continue
            if write.merge_input_ids:
                self._require_merge_plan(plan, write, reads)
            else:
                self.adapter.draft(
                    write.draft.content,
                    ArtifactLineage(sources=write.draft.sources, artifacts=write.draft.artifacts),
                    historical=plan.operation in {"restore", "undo_merge"},
                )
                if (
                    write.current is not None
                    and plan.operation == "change"
                    and (
                        write.current.state.merged_into_id is not None
                        or write.current.state.lifecycle_state
                        not in {ArtifactLifecycleState.ACTIVE, ArtifactLifecycleState.DEPRECATED}
                    )
                ):
                    raise self.errors.invalid_state("merged and retired Artifacts cannot be edited")  # noqa: TRY003

    def _require_merge_plan(
        self, plan: ArtifactMergePlan, write: ArtifactMergeWrite, reads: dict[str, ArtifactMergeRead]
    ) -> None:
        if plan.operation != "merge" or write.current is not None or write.draft is None:
            raise self.errors.relation("merge creation belongs only to a new result")  # noqa: TRY003
        if any(identity not in reads for identity in write.merge_input_ids):
            raise self.errors.relation("merge inputs require exact read dependencies")  # noqa: TRY003
        inputs = tuple(reads[identity] for identity in write.merge_input_ids)
        self._require_merge_draft(write.artifact_id, write.draft, inputs)
        writes = {item.artifact_id: item for item in plan.writes}
        for expected in inputs:
            matching = writes.get(expected.ref.artifact_id)
            if (
                expected.lifecycle_state is not ArtifactLifecycleState.ACTIVE
                or matching is None
                or matching.state is not ArtifactLifecycleState.DEPRECATED
                or matching.merged_into_id != write.artifact_id
            ):
                raise self.errors.relation("all active merge inputs must freeze into the new result")  # noqa: TRY003

    def _signer(self) -> ArtifactMergePreviewSigner:
        if self.preview_signer is None:
            raise self.errors.invalid_preview(  # noqa: TRY003
                "restoration previews need an explicitly configured shared signing key"
            )
        return self.preview_signer

    def _unique_reads(self, reads: Sequence[ArtifactMergeRead]) -> tuple[ArtifactMergeRead, ...]:
        unique: dict[str, ArtifactMergeRead] = {}
        for read in reads:
            if read.ref.family != self.family:
                raise self.errors.relation("read dependencies must belong to the service Family")  # noqa: TRY003
            previous = unique.get(read.ref.artifact_id)
            if previous is not None and previous != read:
                raise self.errors.conflict("a plan cannot mix versions of the same Artifact")  # noqa: TRY003
            unique[read.ref.artifact_id] = read
        return tuple(unique[identity] for identity in sorted(unique))


def _unique_refs(refs: Sequence[ArtifactRef]) -> tuple[ArtifactRef, ...]:
    return tuple({(ref.family, ref.artifact_id, ref.revision): ref for ref in refs}.values())
