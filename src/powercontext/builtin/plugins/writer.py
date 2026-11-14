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

"""The constrained Artifact writer handed to ``generate``, user changes, and plugin routes.

Writes reuse the shared merge service so plugin families follow the same revision,
merge, restoration and governance rules as built-in families. In a Worker the single
write transaction commits together with the consumption cursor and acknowledgment; in
the host process it commits one request's writes as the caller.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.merge import ArtifactMergeService
from powercontext.builtin.artifacts.merge_models import ArtifactMergeRecord
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernanceRepository,
    ArtifactLifecycleState,
)
from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.records import _canonical_source_text
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HANDLES_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACT_TAGS_TABLE,
)
from powercontext.builtin.plugins.families import (
    PluginArtifactContent,
    plugin_artifact_repository,
    record_revision_metadata,
)
from powercontext.builtin.records import ArtifactRevisionPreconditionError
from powercontext.builtin.runtime.artifact_merge import ArtifactMergeAccess, merge_artifact_tags
from powercontext.builtin.sources.content import ContentSource, ContentSourceInternal, ContentSourceTarget
from powercontext.builtin.tags import normalize_tags
from powercontext.plugins import ArtifactChange, ArtifactPage, ArtifactSnapshot, LifecycleState, UndoMergePreview
from powercontext.sources import SourceMaterialization, SourceRef

MAX_WRITES_PER_TRANSACTION = 1000
MAX_CONTENT_BYTES = 256 * 1024
MAX_TRANSACTION_BYTES = 8 * 1024 * 1024


class PluginWriteError(ValueError):
    """A plugin write violated the writer's scope, evidence, or size constraints."""


class PluginContractError(PluginWriteError):
    """A user-change method returned without completing exactly its agreed write."""


@dataclass(frozen=True)
class UserChange:
    """The one write a user-change method must complete, on ``artifact_id`` (or ``into`` for merges)."""

    operation: Literal["create", "update", "merge", "forget", "restore", "undo_merge"]
    artifact_id: str
    inputs: tuple[str, ...] = ()
    # The caller's (revision, state_version) per Artifact, enforced against the rows the transaction writes.
    expected: dict[str, tuple[int, int | None]] = field(default_factory=dict)


_AGREED = {
    "create": {"put"},
    "update": {"put"},
    "merge": {"merge"},
    "forget": {"deprecate"},
    "restore": {"revert", "activate"},
    "undo_merge": {"undo_merge"},
}


class _Adapter:
    """Plugin content is opaque; core records only which plugin version wrote each revision."""

    def __init__(self, family: str, metadata: bytes | None) -> None:
        self.family = family
        self.metadata = metadata

    def draft(self, content, lineage, *, merge_inputs=(), historical=False):
        return RepositoryArtifactDraft(
            family=self.family,
            content=PluginArtifactContent.model_validate(content.model_dump(mode="json")),
            sources=lineage.sources,
            artifacts=lineage.artifacts,
        )

    async def prepare(self, content):
        return ()

    def validate_prepared(self, prepared):
        pass

    async def publish(self, connection, scope_id, record, prepared, execution_context):
        if self.metadata is not None:
            artifact = record.artifact
            await record_revision_metadata(
                connection, scope_id, self.family, artifact.artifact_id, artifact.revision, self.metadata
            )

    async def publish_many(self, connection, scope_id, records, execution_context):
        for record, prepared in records:
            await self.publish(connection, scope_id, record, prepared, execution_context)

    async def remove(self, connection, scope_id, artifact_id):
        pass


class _Security:
    """Worker security establishes owners; Scope authority is checked when the transaction opens."""

    def __init__(self, family: str, security: Any) -> None:
        self.family = family
        self.security = security

    def subject(self, context):
        return "plugin" if self.security is None else f"{self.security.principal.type}:{self.security.principal.id}"

    async def authorize(self, connection, scope_id, context, action, ref=None):
        pass

    async def authorize_sources(self, connection, scope_id, context, sources):
        pass

    async def authorize_creates(self, connection, scope_id, context, refs):
        pass

    async def establish_owners(self, connection, scope_id, artifact_ids, context):
        if self.security is None:
            return
        from powercontext.server.authz import ResourceRef

        bound = self.security.access.with_connection(connection)
        for artifact_id in artifact_ids:
            await bound.establish_artifact_owner(
                ResourceRef.artifact(scope_id, family=self.family, artifact_id=artifact_id),
                self.security.principal,
                idempotency_key=f"plugin-owner:{scope_id}:{self.family}:{artifact_id}",
                context=self.security.context,
            )


class _Reads:
    """Reads over one connection; the writer and the transaction share this code."""

    def __init__(self, owner: PluginArtifactWriter, connect: Callable[[], Any]) -> None:
        self._owner = owner
        self._connect = connect

    def _seen(self, snapshots: Sequence[ArtifactSnapshot]) -> None:
        """Committed reads remember the revision each later write is decided against."""

    async def _record(self, connection, artifact_id: str, revision: int | None = None) -> ArtifactMergeRecord | None:
        owner = self._owner
        try:
            return await owner.service.get(connection, owner.scope_id, artifact_id, None, revision=revision)
        except RepositoryNotFoundError:
            return None

    async def get(self, artifact_id: str, /, *, revision: int | None = None) -> ArtifactSnapshot | None:
        async with self._connect() as connection:
            record = await self._record(connection, artifact_id, revision)
        snapshot = None if record is None else _snapshot(record)
        if snapshot is not None and revision is None:
            self._seen((snapshot,))
        return snapshot

    async def revisions(self, artifact_id: str, /, *, cursor: str | None = None, limit: int = 50) -> ArtifactPage:
        owner = self._owner
        after = 0 if cursor is None else int(cursor)
        async with self._connect() as connection:
            state = await owner.states.get(connection, owner.scope_id, owner.family, artifact_id)
            artifacts = await owner.artifacts.revisions(
                connection, owner.scope_id, owner.family, artifact_id, since_revision=after, through_revision=None
            )
        selected = artifacts[:limit]
        items = tuple(_snapshot(ArtifactMergeRecord(artifact=item, state=state)) for item in selected)
        return ArtifactPage(items=items, next_cursor=str(selected[-1].revision) if len(artifacts) > limit else None)

    async def list(
        self,
        *,
        states: Collection[LifecycleState] = ("active",),
        tags: Collection[str] = (),
        cursor: str | None = None,
        limit: int = 50,
    ) -> ArtifactPage:
        owner = self._owner
        heads = ARTIFACT_HEADS_TABLE
        statement = (
            select(heads.c.artifact_id)
            .where(
                heads.c.scope_id == owner.scope_id,
                heads.c.family == owner.family,
                heads.c.lifecycle_state.in_(tuple(states)),
            )
            .order_by(heads.c.artifact_id)
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(heads.c.artifact_id > cursor)
        if tags:
            keys = tuple(normalize_tags(tuple(tags), allow_empty=False))
            tagged = (
                select(ARTIFACT_TAGS_TABLE.c.artifact_id)
                .where(
                    ARTIFACT_TAGS_TABLE.c.scope_id == owner.scope_id,
                    ARTIFACT_TAGS_TABLE.c.family == owner.family,
                    ARTIFACT_TAGS_TABLE.c.target_type == "artifact",
                    ARTIFACT_TAGS_TABLE.c.tag_key.in_(keys),
                )
                .group_by(ARTIFACT_TAGS_TABLE.c.artifact_id)
                .having(func.count() == len(keys))
            )
            statement = statement.where(heads.c.artifact_id.in_(tagged))
        async with self._connect() as connection:
            identities = (await connection.execute(statement)).scalars().all()
            records = [await self._record(connection, identity) for identity in identities[:limit]]
        items = tuple(_snapshot(record) for record in records if record is not None)
        self._seen(items)
        return ArtifactPage(items=items, next_cursor=identities[limit - 1] if len(identities) > limit else None)

    async def by_source(self, ref: SourceRef, /) -> tuple[ArtifactSnapshot, ...]:
        owner = self._owner
        lineage = ARTIFACT_LINEAGE_SOURCES_TABLE
        statement = (
            select(lineage.c.artifact_id)
            .where(
                lineage.c.scope_id == owner.scope_id,
                lineage.c.family == owner.family,
                lineage.c.source_type == ref.source_type,
                lineage.c.source_id == ref.source_id,
            )
            .distinct()
            .order_by(lineage.c.artifact_id)
        )
        async with self._connect() as connection:
            identities = (await connection.execute(statement)).scalars().all()
            records = [await self._record(connection, identity) for identity in identities]
        found = tuple(_snapshot(record) for record in records if record is not None)
        self._seen(found)
        return found

    async def merge_inputs(self, artifact_id: str, /) -> tuple[ArtifactRef, ...]:
        owner = self._owner
        async with self._connect() as connection:
            return await owner.artifacts.merge_inputs(
                connection, owner.scope_id, ArtifactRef(family=owner.family, artifact_id=artifact_id, revision=1)
            )

    async def tags(self, artifact_id: str, /) -> tuple[str, ...]:
        owner = self._owner
        async with self._connect() as connection:
            labels = await connection.scalars(
                select(ARTIFACT_TAGS_TABLE.c.tag)
                .where(*_tag_identity(owner.scope_id, owner.family, artifact_id))
                .order_by(ARTIFACT_TAGS_TABLE.c.tag_key)
            )
            return tuple(labels)

    async def resolve_handle(self, handle_key: str, /) -> ArtifactRef | None:
        owner = self._owner
        handles = ARTIFACT_HANDLES_TABLE
        async with self._connect() as connection:
            row = (
                await connection.execute(
                    select(handles.c.artifact_id, handles.c.revision).where(
                        handles.c.scope_id == owner.scope_id,
                        handles.c.family == owner.family,
                        handles.c.handle_key == handle_key,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return ArtifactRef(family=owner.family, artifact_id=str(row.artifact_id), revision=int(row.revision))


class PluginArtifactTransaction(_Reads):
    """Writes run directly in the open transaction and return their exact revisions."""

    def __init__(self, owner: PluginArtifactWriter, connection: AsyncConnection) -> None:
        @asynccontextmanager
        async def borrow():
            yield connection

        super().__init__(owner, borrow)
        self._connection = connection
        self._writes = 0
        self._bytes = 0
        self.closed = False
        self.agreed = 0

    def _agree(self, kind: str, artifact_id: str) -> None:
        """In a user change, only the agreed write on its target (and its tags) may run."""

        change = self._owner.change
        if change is None:
            return
        if kind == "tags" and artifact_id == change.artifact_id:
            return
        if kind not in _AGREED[change.operation] or artifact_id != change.artifact_id or self.agreed:
            raise PluginContractError(f"{change.operation} allows exactly one agreed write on its target")  # noqa: TRY003
        self.agreed += 1

    def _admit(self, content: dict[str, JsonValue] | None = None, sources: Sequence[SourceRef] = ()) -> None:
        if self.closed:
            raise PluginWriteError("the write transaction is closed")  # noqa: TRY003
        self._writes += 1
        if self._writes > MAX_WRITES_PER_TRANSACTION:
            raise PluginWriteError("the write transaction exceeds its operation limit")  # noqa: TRY003
        if content is not None:
            size = len(PluginArtifactContent(content=content).model_dump_json().encode())
            self._bytes += size
            if size > MAX_CONTENT_BYTES or self._bytes > MAX_TRANSACTION_BYTES:
                raise PluginWriteError("the write transaction exceeds its size limit")  # noqa: TRY003
        if self._owner.change is None:
            outside = {(ref.source_type, ref.source_id) for ref in sources} - self._owner.window_sources
            if outside:
                raise PluginWriteError("source_refs must reference Sources of this invocation window")  # noqa: TRY003

    async def _committed(self, plan, direct_source: SourceRef | None = None):
        owner = self._owner
        prepared = await owner.service.prepare_change(plan)
        result = await owner.service.commit(self._connection, prepared, owner.context, direct_source=direct_source)
        owner.records = tuple(_snapshot(record) for record in result.records)
        return result

    async def _commit(self, plan) -> ArtifactSnapshot:
        return self._written(_snapshot((await self._committed(plan)).primary))

    async def _commit_change(self, plan, artifact_id: str) -> ArtifactChange:
        result = await self._committed(plan)
        change = ArtifactChange(
            artifact=self._written(_snapshot(result.primary)),
            restored=tuple(ref for ref in result.restored if ref.artifact_id != artifact_id),
            retired=tuple(ref for ref in result.retired if ref.artifact_id != artifact_id),
        )
        self._owner.outcome = (result.restored, result.retired, result.undo_merge_results)
        return change

    async def _require(self, artifact_id: str) -> ArtifactMergeRecord:
        record = await self._record(self._connection, artifact_id)
        if record is None:
            raise RepositoryNotFoundError("artifact", (self._owner.scope_id, self._owner.family, artifact_id))
        return record

    def _expect(self, record: ArtifactMergeRecord) -> None:
        """A user change writes only the revision and governance state its caller read."""

        change = self._owner.change
        expected = None if change is None else change.expected.get(record.artifact.artifact_id)
        if expected is None:
            return
        revision, state_version = expected
        if record.artifact.revision != revision:
            if change is not None and change.operation == "update":
                raise ArtifactRevisionPreconditionError(f'"{revision}"', f'"{record.artifact.revision}"')
            raise self._owner.service.errors.conflict("the Artifact changed after it was read")  # noqa: TRY003
        if state_version is not None and record.state.governance_generation != state_version:
            raise self._owner.service.errors.conflict("the Artifact governance changed after it was read")  # noqa: TRY003

    def _put_revision(self, artifact_id: str, current: ArtifactMergeRecord | None, requested: int | None) -> int | None:
        """The revision a put is decided against: the caller's in a user change, else the plugin's or its last read."""

        change = self._owner.change
        if current is not None:
            self._expect(current)
        if change is not None and artifact_id in change.expected:
            return change.expected[artifact_id][0]
        return self._owner.observed.get(artifact_id) if requested is None else requested

    async def _authorize_write(self, ref: ArtifactRef) -> None:
        """Writes that bypass the governance engine still need the caller's ``artifact.write``."""

        owner = self._owner
        await owner.service.security.authorize(self._connection, owner.scope_id, owner.context, "write", ref)

    def _written(self, snapshot: ArtifactSnapshot) -> ArtifactSnapshot:
        self._owner.written = snapshot
        return snapshot

    async def put(
        self,
        artifact_id: str,
        /,
        *,
        content: dict[str, JsonValue],
        source_refs: Sequence[SourceRef] = (),
        artifact_refs: Sequence[ArtifactRef] = (),
        expected_revision: int | None = None,
    ) -> ArtifactSnapshot:
        self._admit(content, source_refs)
        owner = self._owner
        change = owner.change
        self._agree("put", artifact_id)
        body = owner.body(content)
        current = await self._record(self._connection, artifact_id)
        expected_revision = self._put_revision(artifact_id, current, expected_revision)
        if change is not None:
            if (change.operation == "create") != (current is None):
                raise owner.service.errors.conflict(
                    "create needs a new Artifact" if current is not None else "the Artifact no longer exists"
                )
            allowed = (
                set()
                if current is None
                else {(ref.source_type, ref.source_id) for ref in current.artifact.lineage.sources}
            )
            if {(ref.source_type, ref.source_id) for ref in source_refs} - allowed:
                raise PluginWriteError("user changes may only keep Sources already in the Artifact lineage")  # noqa: TRY003
        elif current is not None and current.artifact.content == body:
            # As in Atomic Memory, unchanged content adds no revision and records no new evidence.
            if expected_revision is not None and expected_revision != current.artifact.revision:
                raise owner.service.errors.conflict("the Artifact changed after it was read")  # noqa: TRY003
            return _snapshot(current)
        if current is None and expected_revision not in {None, 0}:
            raise owner.service.errors.conflict("the Artifact no longer exists")  # noqa: TRY003
        plan = await owner.service.inspect_change(
            self._connection,
            owner.scope_id,
            artifact_id,
            body,
            owner.context,
            expected_revision=None
            if current is None
            else (current.artifact.revision if expected_revision is None else expected_revision),
            lineage=ArtifactLineage(sources=tuple(source_refs), artifacts=tuple(artifact_refs)),
        )
        direct = None
        if change is not None:
            # Like built-in edits, the submitted content is kept as a lineage-only Source of the new revision.
            direct = await _lineage_source(
                self._connection,
                owner,
                artifact_id,
                content,
                "artifact_create" if current is None else "artifact_replace",
                1 if current is None else current.artifact.revision + 1,
            )
        return self._written(_snapshot((await self._committed(plan, direct)).primary))

    async def revert(self, artifact_id: str, /, *, to_revision: int) -> ArtifactChange:
        self._admit()
        self._agree("revert", artifact_id)
        owner = self._owner
        plan = await owner.service.inspect_restore(
            self._connection, owner.scope_id, artifact_id, owner.context, operation="restore", revision=to_revision
        )
        return await self._commit_change(plan, artifact_id)

    async def set_lifecycle(
        self, artifact_id: str, state: LifecycleState, /, *, replacement_artifact_id: str | None = None
    ) -> ArtifactChange:
        self._admit()
        owner = self._owner
        requested = ArtifactLifecycleState(state)
        self._agree(
            "deprecate"
            if requested is ArtifactLifecycleState.DEPRECATED and replacement_artifact_id is None
            else "activate"
            if requested is ArtifactLifecycleState.ACTIVE
            else "transition",
            artifact_id,
        )
        current = await self._require(artifact_id)
        self._expect(current)
        if current.state.lifecycle_state is requested and current.state.replacement_artifact_id == (
            replacement_artifact_id
        ):
            return ArtifactChange(artifact=self._written(_snapshot(current)))
        if requested is ArtifactLifecycleState.DEPRECATED and replacement_artifact_id is None:
            plan = await owner.service.inspect_forget(
                self._connection,
                owner.scope_id,
                artifact_id,
                owner.context,
                expected_revision=current.artifact.revision,
                expected_state_version=current.state.governance_generation,
            )
            return await self._commit_change(plan, artifact_id)
        if requested is ArtifactLifecycleState.ACTIVE:
            # Shared restoration also undoes the merges that froze an input, as one group.
            plan = await owner.service.inspect_restore(
                self._connection, owner.scope_id, artifact_id, owner.context, operation="restore"
            )
            return await self._commit_change(plan, artifact_id)
        await self._authorize_write(current.artifact.as_ref())
        await owner.states.transition(
            self._connection,
            owner.scope_id,
            owner.family,
            artifact_id,
            current.state.governance_generation,
            requested,
            replacement_artifact_id,
        )
        return ArtifactChange(artifact=_snapshot(await self._require(artifact_id)))

    async def merge(
        self,
        inputs: Sequence[str],
        /,
        *,
        into: str,
        content: dict[str, JsonValue],
        source_refs: Sequence[SourceRef] = (),
        artifact_refs: Sequence[ArtifactRef] = (),
    ) -> ArtifactSnapshot:
        self._admit(content, source_refs)
        self._agree("merge", into)
        owner = self._owner
        change = owner.change
        if change is not None and tuple(inputs) != change.inputs:
            raise PluginContractError("merge_artifacts must merge exactly the requested inputs")  # noqa: TRY003
        records = [await self._require(identity) for identity in inputs]
        for record in records:
            self._expect(record)
        result = await self._record(self._connection, into)
        if result is not None and all(record.state.merged_into_id == into for record in records):
            return self._written(_snapshot(result))
        reads = tuple(record.as_read() for record in records)
        lineage = ArtifactLineage(
            sources=tuple(source_refs),
            artifacts=(*(read.ref for read in reads), *artifact_refs),
        )
        plan = await owner.service.inspect_merge(
            self._connection,
            owner.scope_id,
            into,
            reads,
            owner.body(content),
            owner.context,
            lineage=lineage,
        )
        return await self._commit(plan)

    async def _undo_plan(self, artifact_id: str):
        owner = self._owner
        return await owner.service.inspect_restore(
            self._connection, owner.scope_id, artifact_id, owner.context, operation="undo_merge"
        )

    async def preview_undo_merge(self, artifact_id: str, /) -> UndoMergePreview:
        # Plugins act inside their own transaction, so the plan itself is the preview; no signed token.
        plan = await self._undo_plan(artifact_id)
        retire = {write.artifact_id for write in plan.writes if write.state is ArtifactLifecycleState.RETIRED}
        return UndoMergePreview(
            restore=tuple(
                ArtifactRef(family=self._owner.family, artifact_id=item.artifact_id, revision=item.source_revision)
                for item in plan.restore
            ),
            retire=tuple(read.ref for read in plan.reads if read.ref.artifact_id in retire),
            undo_merge_results=plan.undo_merge_results,
        )

    async def undo_merge(self, artifact_id: str, /) -> UndoMergePreview:
        self._admit()
        self._agree("undo_merge", artifact_id)
        owner = self._owner
        prepared = await owner.service.prepare_change(await self._undo_plan(artifact_id))
        result = await owner.service.commit(self._connection, prepared, owner.context)
        owner.records = tuple(_snapshot(record) for record in result.records)
        target = next((record for record in result.records if record.ref.artifact_id == artifact_id), None)
        if target is not None:
            self._written(_snapshot(target))
        owner.outcome = (result.restored, result.retired, result.undo_merge_results)
        return UndoMergePreview(
            restore=result.restored, retire=result.retired, undo_merge_results=result.undo_merge_results
        )

    async def bind_handle(self, handle_key: str, ref: ArtifactRef, /) -> ArtifactRef:
        self._admit()
        self._agree("handle", ref.artifact_id)
        owner = self._owner
        if ref.family != owner.family:
            raise PluginWriteError("handles must bind this plugin's family")  # noqa: TRY003
        await self._authorize_write(ref)
        existing = await self.resolve_handle(handle_key)
        if existing is not None:
            if existing != ref:
                raise owner.service.errors.conflict("the handle is already bound to another revision")  # noqa: TRY003
            return existing
        await owner.artifacts.get(self._connection, owner.scope_id, ref)
        await self._connection.execute(
            insert(ARTIFACT_HANDLES_TABLE).values(
                scope_id=owner.scope_id,
                family=owner.family,
                handle_key=handle_key,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
            )
        )
        return ref

    async def set_tags(self, artifact_id: str, tags: Sequence[str], /) -> tuple[str, ...]:
        self._admit()
        self._agree("tags", artifact_id)
        owner = self._owner
        ref = (await self._require(artifact_id)).artifact.as_ref()
        await self._authorize_write(ref)
        await owner.artifacts.lock_heads(self._connection, owner.scope_id, (ref,))
        desired = normalize_tags(tuple(tags))
        await self._connection.execute(
            delete(ARTIFACT_TAGS_TABLE).where(*_tag_identity(owner.scope_id, owner.family, artifact_id))
        )
        assigned_at = datetime.now(UTC)
        for key, label in desired.items():
            await self._connection.execute(
                insert(ARTIFACT_TAGS_TABLE).values(
                    scope_id=owner.scope_id,
                    family=owner.family,
                    artifact_id=artifact_id,
                    target_type="artifact",
                    target_id=artifact_id,
                    tag_key=key,
                    tag_key_hash=hashlib.sha256(key.encode("utf-8")).digest(),
                    tag=label,
                    assigned_at=assigned_at,
                )
            )
        return tuple(desired.values())


async def _nothing(connection: AsyncConnection) -> None:
    return None


class PluginArtifactWriter(_Reads):
    """Committed reads plus one write transaction.

    Inside an invocation the transaction also commits the cursor and acknowledgment.
    """

    def __init__(
        self,
        *,
        database: Any,
        scope_id: str,
        family: str,
        sources: SourceRepository,
        metadata: bytes | None = None,
        window_sources: frozenset[tuple[str, str]] = frozenset(),
        begin: Callable[[AsyncConnection], Awaitable[None]] = _nothing,
        complete: Callable[[AsyncConnection], Awaitable[None]] = _nothing,
        transaction_timeout_seconds: float = 30,
        security: Any = None,
        context: Any = None,
        change: UserChange | None = None,
        caller_scoped: bool = False,
    ) -> None:
        """``caller_scoped`` writers act as ``context``'s caller under the shared Artifact access policy."""

        self.database = database
        self.scope_id = scope_id
        self._family = family
        self.window_sources = window_sources
        self._begin = begin
        self._complete = complete
        self._timeout = transaction_timeout_seconds
        self.context = context
        self.change = change
        self.service = plugin_merge_service(
            family,
            ArtifactMergeAccess(family) if caller_scoped or change is not None else _Security(family, security),
            sources,
            metadata=metadata,
        )
        self.artifacts = self.service.artifacts
        self.states = ArtifactGovernanceRepository()
        self.sources = sources
        self.observed: dict[str, int] = {}
        # Restored and retired refs of the last lifecycle write, which user changes report back.
        self.outcome: tuple[tuple[ArtifactRef, ...], tuple[ArtifactRef, ...], tuple[str, ...]] = ((), (), ())
        # The snapshot the agreed write committed, and every record it changed, which user changes return
        # instead of a later head.
        self.written: ArtifactSnapshot | None = None
        self.records: tuple[ArtifactSnapshot, ...] = ()
        self.opened = False
        self.committed = False

        @asynccontextmanager
        async def committed_read():
            async with database.transaction() as connection:
                yield connection

        super().__init__(self, committed_read)

    @override
    def _seen(self, snapshots: Sequence[ArtifactSnapshot]) -> None:
        for snapshot in snapshots:
            self.observed.setdefault(snapshot.ref.artifact_id, snapshot.ref.revision)

    @property
    def family(self) -> str:
        return self._family

    def body(self, content: dict[str, JsonValue]) -> PluginArtifactContent:
        return PluginArtifactContent(content=content)

    def stable_id(self, *parts: str) -> str:
        digest = hashlib.sha256()
        for part in (self._family, *parts):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    @asynccontextmanager
    async def transaction(self):
        if self.opened:
            raise PluginWriteError("each invocation opens at most one write transaction")  # noqa: TRY003
        self.opened = True
        async with asyncio.timeout(self._timeout), self.database.transaction() as connection:
            await self._begin(connection)
            transaction = PluginArtifactTransaction(self, connection)
            try:
                yield transaction
            finally:
                transaction.closed = True
            if self.change is not None and transaction.agreed != 1:
                raise PluginContractError(f"{self.change.operation} must complete its agreed write")  # noqa: TRY003
            await self._complete(connection)
        self.committed = True


def plugin_merge_service(
    family: str, security: Any, sources: SourceRepository, *, metadata: bytes | None = None
) -> ArtifactMergeService:
    """The shared governance engine for one plugin family; ``security`` decides who may change it.

    Plugin invocations use Worker security. The Server registers a second instance with
    the shared Artifact access policy for public merge, forget and restoration. ``metadata``
    names the plugin version recorded on every revision it writes.
    """

    return ArtifactMergeService(
        artifacts=plugin_artifact_repository(family, sources),
        adapter=_Adapter(family, metadata),
        security=security,
        merge_tags=partial(merge_artifact_tags, family=family),
        sources=sources,
    )


async def _lineage_source(
    connection: AsyncConnection,
    owner: PluginArtifactWriter,
    artifact_id: str,
    content: dict[str, JsonValue],
    operation: Literal["artifact_create", "artifact_replace"],
    revision: int,
) -> SourceRef:
    """Keep submitted content as a lineage-only Source; it never enters a plugin's input window."""

    source = ContentSource(
        name=f"src_{uuid4().hex}",
        materialization=SourceMaterialization.CAPTURED,
        content=_canonical_source_text(cast(JsonValue, content)),
        wire_content=content,
        wire_content_present=True,
        internal=ContentSourceInternal(
            role="lineage_only",
            operation=operation,
            target=ContentSourceTarget(
                scope_id=owner.scope_id, family=owner.family, artifact_id=artifact_id, revision=revision
            ),
        ),
    )
    return (await owner.sources.add(connection, owner.scope_id, source)).ref


def _snapshot(record: ArtifactMergeRecord) -> ArtifactSnapshot:
    artifact, state = record.artifact, record.state
    return ArtifactSnapshot(
        ref=artifact.as_ref(),
        content=artifact.content.content,
        lineage=artifact.lineage,
        lifecycle_state=state.lifecycle_state.value,
        merged_into_id=state.merged_into_id,
        replacement_artifact_id=state.replacement_artifact_id,
        governance_generation=state.governance_generation,
    )


def _tag_identity(scope_id: str, family: str, artifact_id: str):
    tags = ARTIFACT_TAGS_TABLE
    return (
        tags.c.scope_id == scope_id,
        tags.c.family == family,
        tags.c.artifact_id == artifact_id,
        tags.c.target_type == "artifact",
    )
