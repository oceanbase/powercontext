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

"""Atomic Memory inspection, connection-free preparation and transactional publication."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any, Literal, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.errors import (
    AtomicMemoryConflictError,
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
    AtomicMemoryRestoreOperation,
    AtomicMemoryStateValue,
    AtomicMemoryWrite,
    PreparedAtomicMemory,
)
from powercontext.builtin.artifacts.atomic_memory.restoration import (
    AtomicMemoryPreviewSigner,
    calculate_restoration,
    merge_inputs,
)
from powercontext.builtin.artifacts.memory.canonical import canonical_error_code, normalize_text
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCandidateError
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_index import PreparedAtomicMemoryProjection
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.errors import RevisionConflictError
from powercontext.sources import SourceRef


class AtomicMemorySecurity(Protocol):
    """Explicit policy shared by HTTP and background callers; none is implicit."""

    def subject(self, context: Any) -> str: ...

    async def lock_transaction(self, connection: AsyncConnection, scope_id: str, context: Any) -> None: ...

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

    async def establish_owner(
        self, connection: AsyncConnection, scope_id: str, artifact_id: str, context: Any
    ) -> None: ...


class AtomicMemoryProjections(Protocol):
    async def prepare(self, content: AtomicMemoryContent) -> PreparedAtomicMemoryProjection: ...

    def validate_prepared(self, prepared: PreparedAtomicMemoryProjection) -> None: ...

    async def publish(
        self,
        connection: AsyncConnection,
        scope_id: str,
        record: AtomicMemoryRecord,
        prepared: PreparedAtomicMemoryProjection,
        execution_context: Any,
    ) -> None: ...

    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str) -> None: ...


MergeTags = Callable[[AsyncConnection, str, str, tuple[str, ...]], Awaitable[None]]


class AtomicMemoryService:
    """Own Family transitions while leaving transaction lifetime with the caller.

    Inspect under a read transaction, close it, and prepare vectors without a
    connection. Commit receives the caller's write transaction; it never
    commits or retries a transaction whose outcome it does not own.
    """

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
        self.artifacts = artifacts
        self.states = states
        self.security = security
        self.projections = projections
        self.merge_tags = merge_tags
        self.preview_signer = preview_signer

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
        identity = ArtifactRef(
            family=AtomicMemory.family, artifact_id=artifact_id, revision=1 if revision is None else revision
        )
        await self.security.authorize(connection, scope_id, context, "read", identity)
        if revision is None:
            stored = await self.artifacts.latest(
                connection, scope_id, AtomicMemory.family, artifact_id, for_update=for_update
            )
        else:
            stored = await self.artifacts.get(connection, scope_id, identity, for_update=for_update)
        state = await self.states.get(connection, scope_id, artifact_id, for_update=for_update)
        return AtomicMemoryRecord(artifact=self._atomic(stored), state=state)

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
    ) -> AtomicMemoryPlan:
        self._ordinary_content(content)
        draft = self._draft(content, lineage)
        current = None
        if expected_revision is None:
            target = ArtifactRef(family=AtomicMemory.family, artifact_id=artifact_id, revision=1)
            await self.security.authorize(connection, scope_id, context, "create", target)
            try:
                stored = await self.artifacts.latest(connection, scope_id, AtomicMemory.family, artifact_id)
            except RepositoryNotFoundError:
                pass
            else:
                raise RevisionConflictError(draft, stored)
            state = AtomicMemoryStateValue.ACTIVE
        else:
            current = await self.get(connection, scope_id, artifact_id, context)
            await self.security.authorize(connection, scope_id, context, "write", current.ref)
            self._expected(current, expected_revision, expected_state_version, draft)
            if current.state.state not in {AtomicMemoryStateValue.ACTIVE, AtomicMemoryStateValue.FORGOTTEN}:
                raise InvalidAtomicMemoryStateError("merged and retired memories cannot be edited")  # noqa: TRY003
            target = ArtifactRef(
                family=AtomicMemory.family, artifact_id=artifact_id, revision=current.artifact.revision + 1
            )
            state = current.state.state
            draft = draft.model_copy(update={"artifacts": _unique_refs((*draft.artifacts, current.ref))})
        await self._validate_draft(connection, scope_id, target, draft, context)
        dependencies = (*reads, *((current.as_read(),) if current is not None else ()))
        await self._check_dependencies(connection, scope_id, dependencies, context)
        return AtomicMemoryPlan(
            scope_id=scope_id,
            subject=self.security.subject(context),
            reads=_unique_reads(dependencies),
            writes=(AtomicMemoryWrite(artifact_id=artifact_id, state=state, draft=draft, current=current),),
            primary_artifact_id=artifact_id,
            operation="change",
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
    ) -> AtomicMemoryPlan:
        self._ordinary_content(content)
        selected = tuple(inputs)
        ids = tuple(read.ref.artifact_id for read in selected)
        if len(ids) < 2 or len(set(ids)) != len(ids) or artifact_id in ids:
            raise AtomicMemoryRelationError("merge needs at least two distinct inputs and a new result identity")  # noqa: TRY003
        target = ArtifactRef(family=AtomicMemory.family, artifact_id=artifact_id, revision=1)
        await self.security.authorize(connection, scope_id, context, "create", target)
        try:
            stored = await self.artifacts.latest(connection, scope_id, AtomicMemory.family, artifact_id)
        except RepositoryNotFoundError:
            pass
        else:
            raise RevisionConflictError(content, stored)
        current_inputs: list[AtomicMemoryRecord] = []
        for expected in selected:
            current = await self.get(connection, scope_id, expected.ref.artifact_id, context)
            await self.security.authorize(connection, scope_id, context, "write", current.ref)
            if current.as_read() != expected:
                raise AtomicMemoryConflictError("merge inputs changed before preparation")  # noqa: TRY003
            if current.state.state is not AtomicMemoryStateValue.ACTIVE:
                raise InvalidAtomicMemoryStateError("only active memories can be merge inputs")  # noqa: TRY003
            current_inputs.append(current)
        draft = self._draft(
            content.model_copy(update={"creation": AtomicMemoryCreation(input_artifact_ids=ids)}), lineage
        )
        # An explicitly supplied lineage must already contain every selected
        # exact reference. Internal callers may omit lineage and use inputs.
        if lineage is None:
            draft = draft.model_copy(update={"artifacts": tuple(read.ref for read in selected)})
        self._require_merge_draft(artifact_id, draft, selected)
        await self._validate_draft(connection, scope_id, target, draft, context)
        dependencies = _unique_reads((*reads, *selected))
        await self._check_dependencies(connection, scope_id, dependencies, context)
        writes = (
            AtomicMemoryWrite(
                artifact_id=artifact_id, state=AtomicMemoryStateValue.ACTIVE, draft=draft, merge_input_ids=ids
            ),
            *(
                AtomicMemoryWrite(
                    artifact_id=current.artifact.artifact_id,
                    state=AtomicMemoryStateValue.MERGED,
                    merged_into_id=artifact_id,
                    current=current,
                )
                for current in current_inputs
            ),
        )
        return AtomicMemoryPlan(
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
    ) -> AtomicMemoryPlan:
        current = await self.get(connection, scope_id, artifact_id, context)
        await self.security.authorize(connection, scope_id, context, "write", current.ref)
        self._expected(current, expected_revision, expected_state_version)
        if current.state.state not in {AtomicMemoryStateValue.ACTIVE, AtomicMemoryStateValue.FORGOTTEN}:
            raise InvalidAtomicMemoryStateError("only active or forgotten memories can be forgotten")  # noqa: TRY003
        writes = (
            ()
            if current.state.state is AtomicMemoryStateValue.FORGOTTEN
            else (AtomicMemoryWrite(artifact_id=artifact_id, state=AtomicMemoryStateValue.FORGOTTEN, current=current),)
        )
        return AtomicMemoryPlan(
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
        operation: AtomicMemoryRestoreOperation = "restore",
        revision: int | None = None,
        preview_token: str | None = None,
    ) -> AtomicMemoryPlan:
        if operation not in {"restore", "undo_merge"}:
            raise InvalidAtomicMemoryStateError("unknown restoration operation")  # noqa: TRY003
        if operation == "undo_merge" and revision is not None:
            raise InvalidAtomicMemoryStateError("undo_merge does not accept a content revision")  # noqa: TRY003
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

        async def load_current(identity: str) -> AtomicMemoryRecord:
            record = await self.get(connection, scope_id, identity, context)
            await self.security.authorize(connection, scope_id, context, "write", record.ref)
            return record

        async def load_first(identity: str) -> AtomicMemory:
            ref = ArtifactRef(family=AtomicMemory.family, artifact_id=identity, revision=1)
            await self.security.authorize(connection, scope_id, context, "read", ref)
            return self._atomic(await self.artifacts.get(connection, scope_id, ref))

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
            load_first=load_first,
        )
        if token_endpoint is not None and plan.endpoint != token_endpoint:
            raise AtomicMemoryPreviewStaleError("the restoration preview endpoint changed")  # noqa: TRY003
        return replace(plan, preview_token=preview_token)

    def restoration_preview(self, plan: AtomicMemoryPlan) -> AtomicMemoryRestorationPreview:
        token, expires_at = self._signer().encode(plan)
        if plan.endpoint is None:
            raise InvalidAtomicMemoryPreviewError("the restoration plan has no endpoint")  # noqa: TRY003
        retire_ids = {write.artifact_id for write in plan.writes if write.state is AtomicMemoryStateValue.RETIRED}
        return AtomicMemoryRestorationPreview(
            preview_token=token,
            expires_at=expires_at,
            endpoint=plan.endpoint,
            restore=plan.restore,
            retire=tuple(read.ref for read in plan.reads if read.ref.artifact_id in retire_ids),
            undo_merge_results=plan.undo_merge_results,
        )

    async def prepare_change(self, plan: AtomicMemoryPlan) -> PreparedAtomicMemory:
        """Perform external preparation only, with no database connection."""

        projections: list[tuple[str, PreparedAtomicMemoryProjection]] = []
        for write in plan.writes:
            if write.state is not AtomicMemoryStateValue.ACTIVE:
                continue
            content = (
                write.draft.content
                if write.draft is not None
                else write.current.artifact.content
                if write.current is not None
                else None
            )
            if content is None:
                raise AtomicMemoryRelationError("active publication has no content")  # noqa: TRY003
            projections.append((write.artifact_id, await self.projections.prepare(content)))
        return PreparedAtomicMemory(plan=plan, projections=tuple(projections))

    async def prepare_merge(self, plan: AtomicMemoryPlan) -> PreparedAtomicMemory:
        return await self.prepare_change(plan)

    async def prepare_forget(self, plan: AtomicMemoryPlan) -> PreparedAtomicMemory:
        return await self.prepare_change(plan)

    async def prepare_restore(self, plan: AtomicMemoryPlan) -> PreparedAtomicMemory:
        return await self.prepare_change(plan)

    async def validate_read_set(
        self, connection: AsyncConnection, scope_id: str, reads: Sequence[AtomicMemoryRead], context: Any
    ) -> None:
        """Allow no-op processing plans to protect their actual decision dependencies."""

        await self.security.lock_transaction(connection, scope_id, context)
        await self.artifacts.lock_heads(connection, scope_id, tuple(read.ref for read in reads))
        await self._check_dependencies(connection, scope_id, reads, context, for_update=True)

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
    ) -> tuple[AtomicMemoryMutationResult, ...]:
        """Validate all window dependencies before publishing any of its final decisions."""

        plans = tuple(item.plan for item in prepared)
        subject = self.security.subject(context)
        self._require_window(plans, scope_id, subject)
        for item in prepared:
            for _, projection in item.projections:
                self.projections.validate_prepared(projection)
        reads = _unique_reads((*read_set, *(read for plan in plans for read in plan.reads)))
        await self.security.lock_transaction(connection, scope_id, context)
        await self.artifacts.lock_heads(connection, scope_id, tuple(read.ref for read in reads))
        for plan in plans:
            await self._validate_preview(connection, plan, context)
        current = await self._check_dependencies(connection, scope_id, reads, context, for_update=True)
        for plan in plans:
            if plan.operation in {"restore", "undo_merge"}:
                verified = await self.inspect_restore(
                    connection,
                    scope_id,
                    plan.primary_artifact_id,
                    context,
                    operation=cast(AtomicMemoryRestoreOperation, plan.operation),
                    revision=plan.target_revision,
                    preview_token=plan.preview_token,
                )
                if verified != plan:
                    raise AtomicMemoryConflictError("restoration relations changed; prepare the whole group again")  # noqa: TRY003
        results: list[AtomicMemoryMutationResult] = []
        for item in prepared:
            results.append(await self._commit_locked(connection, item, current, context, direct_source))
        return tuple(results)

    @staticmethod
    def _require_window(plans: Sequence[AtomicMemoryPlan], scope_id: str, subject: str) -> None:
        identities: set[str] = set()
        for plan in plans:
            if plan.scope_id != scope_id or plan.subject != subject:
                raise InvalidAtomicMemoryStateError("window scope or identity differs from preparation")  # noqa: TRY003
            AtomicMemoryService._require_plan(plan)
            for write in plan.writes:
                if write.artifact_id in identities:
                    raise AtomicMemoryRelationError("window decisions must coordinate each memory into one final write")  # noqa: TRY003
                identities.add(write.artifact_id)

    async def _validate_preview(self, connection: AsyncConnection, plan: AtomicMemoryPlan, context: Any) -> None:
        if plan.preview_token is None:
            return
        endpoint = self._signer().validate(
            plan.preview_token,
            scope_id=plan.scope_id,
            subject=plan.subject,
            operation=cast(AtomicMemoryRestoreOperation, plan.operation),
            artifact_id=plan.primary_artifact_id,
            revision=plan.target_revision,
        )
        current = await self.get(connection, plan.scope_id, endpoint.ref.artifact_id, context, for_update=True)
        if current.as_read() != endpoint:
            raise AtomicMemoryPreviewStaleError("the restoration preview endpoint changed")  # noqa: TRY003

    async def _commit_locked(
        self,
        connection: AsyncConnection,
        prepared: PreparedAtomicMemory,
        current: dict[str, AtomicMemoryRecord],
        context: Any,
        direct_source: SourceRef | None,
    ) -> AtomicMemoryMutationResult:
        """Apply a plan against the already verified initial window state."""

        plan = prepared.plan
        projections = dict(prepared.projections)
        records: dict[str, AtomicMemoryRecord] = {}
        for write in plan.writes:
            record = await self._commit_write(
                connection,
                plan.scope_id,
                write,
                current.get(write.artifact_id),
                projections.get(write.artifact_id),
                context,
                direct_source,
            )
            records[write.artifact_id] = record
        if plan.primary_artifact_id not in records:
            records[plan.primary_artifact_id] = current[plan.primary_artifact_id]
        return AtomicMemoryMutationResult(
            changed=bool(plan.writes),
            records=tuple(records[identity] for identity in sorted(records)),
            primary_artifact_id=plan.primary_artifact_id,
            retired=tuple(
                record.ref for record in records.values() if record.state.state is AtomicMemoryStateValue.RETIRED
            ),
            undo_merge_results=plan.undo_merge_results,
        )

    async def _commit_write(
        self,
        connection: AsyncConnection,
        scope_id: str,
        write: AtomicMemoryWrite,
        before: AtomicMemoryRecord | None,
        projection: PreparedAtomicMemoryProjection | None,
        context: Any,
        direct_source: SourceRef | None,
    ) -> AtomicMemoryRecord:
        """Publish one identity after the whole plan's locks and checks are complete."""

        draft = write.draft
        if draft is not None:
            target = ArtifactRef(
                family=AtomicMemory.family,
                artifact_id=write.artifact_id,
                revision=1 if before is None else before.artifact.revision + 1,
            )
            if direct_source is not None:
                sources = {(source.source_type, source.source_id): source for source in (*draft.sources, direct_source)}
                draft = draft.model_copy(update={"sources": tuple(sources.values())})
            await self._validate_draft(connection, scope_id, target, draft, context, direct_source=direct_source)
            if before is None:
                await self.security.authorize(connection, scope_id, context, "create", target)
                artifact = self._atomic(await self.artifacts.create(connection, scope_id, write.artifact_id, draft))
                state = await self.states.create(connection, scope_id, write.artifact_id)
                await self.security.establish_owner(connection, scope_id, write.artifact_id, context)
                if write.merge_input_ids:
                    await self.merge_tags(connection, scope_id, write.artifact_id, write.merge_input_ids)
            else:
                artifact = self._atomic(await self.artifacts.revise(connection, scope_id, before.artifact, draft))
                state = before.state
        else:
            if before is None:
                raise AtomicMemoryRelationError("state transitions require an existing memory")  # noqa: TRY003
            artifact, state = before.artifact, before.state
        state = await self.states.transition(
            connection,
            scope_id,
            write.artifact_id,
            state,
            write.state,
            write.merged_into_id,
        )
        record = AtomicMemoryRecord(artifact=artifact, state=state)
        if state.state is AtomicMemoryStateValue.ACTIVE:
            if projection is None:
                raise AtomicMemoryRelationError("active publication requires a prepared projection")  # noqa: TRY003
            await self.projections.publish(connection, scope_id, record, projection, context)
        else:
            await self.projections.remove(connection, scope_id, write.artifact_id)
        return record

    async def _check_dependencies(
        self,
        connection: AsyncConnection,
        scope_id: str,
        reads: Sequence[AtomicMemoryRead],
        context: Any,
        *,
        for_update: bool = False,
    ) -> dict[str, AtomicMemoryRecord]:
        records: dict[str, AtomicMemoryRecord] = {}
        for expected in _unique_reads(reads):
            current = await self.get(connection, scope_id, expected.ref.artifact_id, context, for_update=for_update)
            await self.security.authorize(connection, scope_id, context, "write", current.ref)
            if current.as_read() != expected:
                raise AtomicMemoryConflictError("memory content, state or merge relation changed; prepare again")  # noqa: TRY003
            if for_update:
                await self.states.require_summary(connection, scope_id, expected.ref.artifact_id, current.state)
            records[expected.ref.artifact_id] = current
        return records

    async def _validate_draft(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target: ArtifactRef,
        draft: AtomicMemoryDraft,
        context: Any,
        *,
        direct_source: SourceRef | None = None,
    ) -> None:
        # The host creates a lineage_only Source bound to this exact new target.
        # It is not caller-supplied evidence and need not grant Scope body read.
        ordinary_sources = tuple(source for source in draft.sources if source != direct_source)
        await self.security.authorize_sources(connection, scope_id, context, ordinary_sources)
        await self.artifacts.validate_lineage_sources(connection, scope_id, target, draft.sources)
        for ref in draft.artifacts:
            await self.security.authorize(connection, scope_id, context, "read", ref)
            await self.artifacts.get(connection, scope_id, ref)

    @staticmethod
    def _draft(content: AtomicMemoryContent, lineage: ArtifactLineage | None) -> AtomicMemoryDraft:
        evidence = lineage or ArtifactLineage()
        if evidence.memory_citations or evidence.publication_source is not None:
            raise AtomicMemoryRelationError("Atomic Memory accepts direct Sources and exact in-Scope Artifacts")  # noqa: TRY003
        try:
            text = normalize_text(content.text)
        except (TypeError, ValueError) as error:
            raise InvalidMemoryCandidateError(
                "canonical", str(error), canonical_code=canonical_error_code(error)
            ) from error
        normalized_content = content.model_copy(update={"text": text})
        return AtomicMemoryDraft(content=normalized_content, sources=evidence.sources, artifacts=evidence.artifacts)

    @staticmethod
    def _ordinary_content(content: AtomicMemoryContent) -> None:
        if content.creation is not None:
            raise AtomicMemoryRelationError("creation metadata is constructed only by the merge service")  # noqa: TRY003

    @staticmethod
    def _expected(current: AtomicMemoryRecord, revision: int, state_version: int | None, requested: Any = None) -> None:
        if current.artifact.revision != revision:
            raise RevisionConflictError(requested, current.artifact)
        if state_version is not None and current.state.state_version != state_version:
            raise AtomicMemoryConflictError("the expected memory state version is stale")  # noqa: TRY003

    @staticmethod
    def _require_merge_draft(artifact_id: str, draft: AtomicMemoryDraft, inputs: Sequence[AtomicMemoryRead]) -> None:
        result = AtomicMemory(
            artifact_id=artifact_id,
            revision=1,
            content=draft.content,
            lineage=ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts),
        )
        selected = merge_inputs(result)
        if selected != tuple(read.ref for read in inputs):
            raise AtomicMemoryRelationError("merge selectors must identify the supplied exact active input versions")  # noqa: TRY003

    @staticmethod
    def _require_plan(plan: AtomicMemoryPlan) -> None:
        ids = tuple(write.artifact_id for write in plan.writes)
        if len(ids) != len(set(ids)):
            raise AtomicMemoryRelationError("one plan cannot write an identity twice")  # noqa: TRY003
        reads = {read.ref.artifact_id: read for read in _unique_reads(plan.reads)}
        for write in plan.writes:
            if write.current is not None and write.current.state.state is AtomicMemoryStateValue.RETIRED:
                raise InvalidAtomicMemoryStateError("retired memory identities cannot be modified")  # noqa: TRY003
            if write.current is not None and reads.get(write.artifact_id) != write.current.as_read():
                raise AtomicMemoryRelationError("every existing write requires its exact current read dependency")  # noqa: TRY003
            if write.current is None and (write.draft is None or write.state is not AtomicMemoryStateValue.ACTIVE):
                raise AtomicMemoryRelationError("new memories require active content")  # noqa: TRY003
            if write.draft is None:
                continue
            if write.merge_input_ids:
                AtomicMemoryService._require_merge_plan(plan, write, reads)
            else:
                AtomicMemoryService._ordinary_content(write.draft.content)
                if (
                    write.current is not None
                    and plan.operation == "change"
                    and write.current.state.state
                    not in {AtomicMemoryStateValue.ACTIVE, AtomicMemoryStateValue.FORGOTTEN}
                ):
                    raise InvalidAtomicMemoryStateError("merged and retired memories cannot be edited")  # noqa: TRY003

    @staticmethod
    def _require_merge_plan(
        plan: AtomicMemoryPlan, write: AtomicMemoryWrite, reads: dict[str, AtomicMemoryRead]
    ) -> None:
        if plan.operation != "merge" or write.current is not None or write.draft is None:
            raise AtomicMemoryRelationError("merge creation belongs only to a new result")  # noqa: TRY003
        if any(identity not in reads for identity in write.merge_input_ids):
            raise AtomicMemoryRelationError("merge inputs require exact read dependencies")  # noqa: TRY003
        inputs = tuple(reads[identity] for identity in write.merge_input_ids)
        AtomicMemoryService._require_merge_draft(write.artifact_id, write.draft, inputs)
        writes = {item.artifact_id: item for item in plan.writes}
        for expected in inputs:
            matching = writes.get(expected.ref.artifact_id)
            if (
                expected.state is not AtomicMemoryStateValue.ACTIVE
                or matching is None
                or matching.state is not AtomicMemoryStateValue.MERGED
                or matching.merged_into_id != write.artifact_id
            ):
                raise AtomicMemoryRelationError("all active merge inputs must freeze into the new result")  # noqa: TRY003

    @staticmethod
    def _atomic(value: Any) -> AtomicMemory:
        if not isinstance(value, AtomicMemory):
            raise AtomicMemoryRelationError("the stored artifact is not an Atomic Memory")  # noqa: TRY003
        return value

    def _signer(self) -> AtomicMemoryPreviewSigner:
        if self.preview_signer is None:
            raise InvalidAtomicMemoryPreviewError(  # noqa: TRY003
                "restoration previews need an explicitly configured shared signing key"
            )
        return self.preview_signer


def _unique_refs(refs: Sequence[ArtifactRef]) -> tuple[ArtifactRef, ...]:
    unique = {(ref.family, ref.artifact_id, ref.revision): ref for ref in refs}
    return tuple(unique.values())


def _unique_reads(reads: Sequence[AtomicMemoryRead]) -> tuple[AtomicMemoryRead, ...]:
    unique: dict[str, AtomicMemoryRead] = {}
    for read in reads:
        previous = unique.get(read.ref.artifact_id)
        if previous is not None and previous != read:
            raise AtomicMemoryConflictError("a plan cannot mix versions of the same memory")  # noqa: TRY003
        unique[read.ref.artifact_id] = read
    return tuple(unique[identity] for identity in sorted(unique))
