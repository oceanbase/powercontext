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

"""Runtime operations over the Atomic Memory Family service and current index."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import cast
from uuid import uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError, AtomicMemoryPreviewStaleError
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemory,
    AtomicMemoryContent,
    AtomicMemoryRead,
    AtomicMemoryRecord,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.atomic_memory.restoration import AtomicMemoryPreviewSigner
from powercontext.builtin.artifacts.atomic_memory.service import AtomicMemoryService
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding, canonical_json, normalize_query
from powercontext.builtin.artifacts.memory.models import MemoryQueryEmbedding
from powercontext.builtin.artifacts.memory.reranking import MemoryReranker
from powercontext.builtin.artifacts.search import (
    AdmissionCounts,
    AdmissionFloor,
    analyze_text,
    unit_l2_cosine_similarity,
)
from powercontext.builtin.inference import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InferenceUsage,
    InvalidInferenceOutputError,
    embed_query,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndex,
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryIndexHit,
    AtomicMemoryProjectionPublisher,
    AtomicMemorySearchMode,
    AtomicMemorySearchRequest,
    atomic_memory_embedding_input,
    atomic_memory_embedding_input_hash,
    combine_atomic_memory_channels,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import ATOMIC_MEMORY_PROJECTION_FORMAT
from powercontext.builtin.persistence.atomic_memory_schema import ATOMIC_MEMORY_STATES_TABLE
from powercontext.builtin.persistence.cursor_codec import SignedCursorCodec
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACT_TAGS_TABLE
from powercontext.builtin.persistence.tags import tag_predicate
from powercontext.builtin.records import BaseOperationNotSupportedError, InvalidBaseAccessRequestError
from powercontext.builtin.tags import TagFilter, normalize_tags


class AtomicMemoryAccess:
    """Apply the shared access service; a missing context means access control is disabled."""

    @staticmethod
    def subject(context: ArtifactSearchExecutionContext | None) -> str:
        principal = None if context is None else context.principal
        return "" if principal is None else f"{principal.type}:{principal.id}"

    async def authorize(
        self,
        connection: AsyncConnection,
        scope_id: str,
        context: ArtifactSearchExecutionContext | None,
        action: str,
        ref: ArtifactRef | None = None,
    ) -> None:
        from powercontext.server.authz import AccessAction, AccessDeniedError, ResourceRef

        if context is None:
            return
        if context.access is None:
            if not context.trusted_local:
                raise AccessDeniedError
            return
        # Prompt and Topic Memory are Scope-owned and have no Artifact owner.
        if action == "read" and (ref is None or ref.family in {"prompt", "topic-memory"}):
            await context.access.require_scope_read(
                context.principal, scope_id, connection=connection, context=context.audit
            )
            return
        access = context.access.with_connection(connection)
        if ref is None or action == "create":
            permission = AccessAction.SCOPE_READ if action == "read" else AccessAction.SCOPE_CONTRIBUTE
            resource = ResourceRef.scope(scope_id)
            await access.bootstrap_static_scope(context.principal, scope_id, context=context.audit)
        else:
            permission = AccessAction.ARTIFACT_READ if action == "read" else AccessAction.ARTIFACT_WRITE
            resource = ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
        await access.require(context.principal, permission, resource, context=context.audit)

    async def authorize_sources(self, connection: AsyncConnection, scope_id: str, context, sources) -> None:
        if sources:
            await self.authorize(connection, scope_id, context, "read")

    async def establish_owner(self, connection: AsyncConnection, scope_id: str, artifact_id: str, context) -> None:
        if context is None or context.access is None:
            return
        from powercontext.server.authz import ResourceRef

        await context.access.with_connection(connection).establish_artifact_owner(
            ResourceRef.artifact(scope_id, family="atomic-memory", artifact_id=artifact_id),
            context.principal,
            idempotency_key=f"atomic-memory-owner:{scope_id}:{artifact_id}",
            context=context.audit,
        )

    async def filters(
        self, connection: AsyncConnection, scope_id: str, context, *, tags: TagFilter | None = None
    ) -> AtomicMemoryIndexFilter:
        await self.authorize(connection, scope_id, context, "read")
        return AtomicMemoryIndexFilter(tag_filter=tags)


def deferred_decision_audit(context: ArtifactSearchExecutionContext | None):
    """Flush decision audit only after the business snapshot closes."""

    if context is None or context.access is None:
        return nullcontext()
    return context.access.defer_decision_audit()


@dataclass(frozen=True, slots=True)
class AtomicMemoryPage:
    items: tuple[AtomicMemoryRecord, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AtomicMemorySearchHit:
    hit: AtomicMemoryIndexHit
    matched_by: tuple[str, ...]

    @property
    def text(self) -> str:
        return self.hit.text

    @property
    def relevance(self) -> float | None:
        """Cosine similarity of the vector channel, when this hit was vector-matched."""

        return None if self.hit.distance is None else unit_l2_cosine_similarity(self.hit.distance)


@dataclass(frozen=True, slots=True)
class AtomicMemoryRerankTrace:
    policy_id: str
    candidate_hits: tuple[AtomicMemorySearchHit, ...]
    selected_ranks: tuple[int, ...]
    discarded_rank_count: int
    used_fallback: bool
    latency_ms: float
    usage: InferenceUsage


@dataclass(frozen=True, slots=True)
class AtomicMemorySearchPage:
    mode: str
    hits: tuple[AtomicMemorySearchHit, ...]
    query_embedding: MemoryQueryEmbedding | None = None
    embedding_calls: int = 0
    generation_calls: int = 0
    admission: AdmissionCounts | None = None
    rerank: AtomicMemoryRerankTrace | None = None
    recoverable: bool = False


class AtomicMemoryApplication:
    def __init__(
        self,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        index: AtomicMemoryIndex,
        *,
        embedding_model=None,
        cursor_secret: bytes | None = None,
        id_factory=None,
        restore_retry_budget: int = 3,
        preview_signer: AtomicMemoryPreviewSigner | None = None,
        reranker: MemoryReranker | None = None,
        rerank_candidate_limit: int = 30,
        prompt_context_factory=None,
    ) -> None:
        if reranker is not None and not getattr(reranker, "supports_atomic_memory", False):
            raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "Atomic text candidate contract")
        self.reranker = reranker
        self.rerank_candidate_limit = rerank_candidate_limit
        self.prompt_context_factory = prompt_context_factory
        self.database = database
        self.artifacts = artifacts
        self.index = index
        self.security = AtomicMemoryAccess()
        self.embedding_model = embedding_model
        self.restore_retry_budget = restore_retry_budget
        self.id_factory = id_factory or (lambda _kind: f"art_{uuid4().hex}")
        self.cursor = SignedCursorCodec(secret=cursor_secret)
        self.publisher = AtomicMemoryProjectionPublisher(
            index,
            embedding_model=embedding_model,
        )
        self.service = AtomicMemoryService(
            artifacts=artifacts,
            states=AtomicMemoryStateRepository(),
            security=self.security,
            projections=self.publisher,
            merge_tags=self.merge_tags,
            preview_signer=preview_signer,
        )

    def for_scope(self, scope_id: str) -> ScopedAtomicMemory:
        return ScopedAtomicMemory(self, scope_id)

    async def merge_tags(self, connection, scope_id: str, result_id: str, input_ids: tuple[str, ...]) -> None:
        from datetime import UTC, datetime
        from hashlib import sha256

        rows = (
            await connection.execute(
                select(ARTIFACT_TAGS_TABLE.c.tag_key, ARTIFACT_TAGS_TABLE.c.tag)
                .where(
                    ARTIFACT_TAGS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_TAGS_TABLE.c.family == "atomic-memory",
                    ARTIFACT_TAGS_TABLE.c.artifact_id.in_(input_ids),
                    ARTIFACT_TAGS_TABLE.c.target_type == "artifact",
                )
                .order_by(ARTIFACT_TAGS_TABLE.c.tag_key, ARTIFACT_TAGS_TABLE.c.artifact_id)
                .with_for_update()
            )
        ).all()
        labels: dict[str, str] = {}
        for row in rows:
            labels.setdefault(str(row.tag_key), str(row.tag))
        labels = normalize_tags(tuple(labels.values()))
        for key, label in labels.items():
            await connection.execute(
                insert(ARTIFACT_TAGS_TABLE).values(
                    scope_id=scope_id,
                    family="atomic-memory",
                    artifact_id=result_id,
                    target_type="artifact",
                    target_id=result_id,
                    tag_key=key,
                    tag_key_hash=sha256(key.encode()).digest(),
                    tag=label,
                    assigned_at=datetime.now(UTC),
                )
            )


class ScopedAtomicMemory:
    def __init__(self, application: AtomicMemoryApplication, scope_id: str) -> None:
        self.application = application
        self.scope_id = scope_id

    async def get(self, artifact_id: str, *, revision: int | None = None, context=None) -> AtomicMemoryRecord:
        # Builtin authority shares the business snapshot. Configured external
        # providers keep their own decision boundary. Audit writes are flushed
        # after it closes so SQLite never upgrades an old read snapshot.
        # SAVEPOINT contains the business reads and composes with an existing
        # in-memory write transaction without committing it.
        async with (
            deferred_decision_audit(context),
            self.application.database.transaction(consistent_snapshot=True) as connection,
        ):
            return await self.application.service.get(
                connection, self.scope_id, artifact_id, context, revision=revision
            )

    async def list(
        self,
        *,
        states: tuple[str, ...] = ("active",),
        kind: str | None = None,
        tag_filter: TagFilter | None = None,
        limit: int = 50,
        cursor: str | None = None,
        context=None,
    ) -> AtomicMemoryPage:
        _validate_limit(limit)
        if not states or len(set(states)) != len(states):
            raise InvalidBaseAccessRequestError("states", "must contain distinct lifecycle values")
        states = tuple(AtomicMemoryStateValue(state).value for state in states)
        if kind is not None:
            AtomicMemoryContent(kind=kind, text="validation")
        bound = {
            "endpoint": "atomic_memory_list",
            "version": 1,
            "scope_id": self.scope_id,
            "subject": self.application.security.subject(context),
            "states": sorted(states),
            "kind": kind,
            "tags": None if tag_filter is None else list(tag_filter.keys),
            "tag_match": None if tag_filter is None else tag_filter.match,
        }
        after = self.application.cursor.after_text(cursor, bound)
        items: list[AtomicMemoryRecord] = []
        last = after
        has_more = False
        table = ATOMIC_MEMORY_STATES_TABLE
        head = ARTIFACT_HEADS_TABLE
        async with (
            deferred_decision_audit(context),
            self.application.database.transaction(consistent_snapshot=True) as connection,
        ):
            await connection.execute(
                select(ARTIFACT_HEADS_TABLE.c.artifact_id)
                .where(ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id)
                .limit(1)
            )
            await self.application.security.filters(connection, self.scope_id, context, tags=tag_filter)
            while len(items) <= limit:
                statement = (
                    select(table.c.artifact_id)
                    .join(
                        head,
                        (head.c.scope_id == table.c.scope_id)
                        & (head.c.artifact_id == table.c.artifact_id)
                        & (head.c.family == "atomic-memory"),
                    )
                    .where(table.c.scope_id == self.scope_id, table.c.artifact_id > last, table.c.state.in_(states))
                    .order_by(table.c.artifact_id)
                    .limit(100)
                )
                if tag_filter is not None:
                    statement = statement.where(
                        tag_predicate(
                            self.scope_id,
                            "atomic-memory",
                            table.c.artifact_id,
                            "artifact",
                            table.c.artifact_id,
                            tag_filter,
                        )
                    )
                rows = (await connection.execute(statement)).scalars().all()
                if not rows:
                    break
                for artifact_id in rows:
                    record = await self._current_record(connection, str(artifact_id))
                    if kind is not None and record.artifact.content.kind != kind:
                        last = str(artifact_id)
                        continue
                    if len(items) == limit:
                        has_more = True
                        break
                    items.append(record)
                    last = str(artifact_id)
                if has_more or len(rows) < 100:
                    break
        return AtomicMemoryPage(tuple(items), self.application.cursor.encode(bound, last) if has_more else None)

    async def _current_record(self, connection, artifact_id: str) -> AtomicMemoryRecord:
        """Load current authority after a Scope read boundary has been checked."""
        application = self.application
        artifact = await application.artifacts.latest(connection, self.scope_id, AtomicMemory.family, artifact_id)
        return AtomicMemoryRecord(
            artifact=cast(AtomicMemory, artifact),
            state=await application.service.states.get(connection, self.scope_id, artifact_id),
        )

    async def search(
        self,
        query: str,
        *,
        mode: str = "text",
        limit: int = 20,
        kind: str | None = None,
        tag_filter: TagFilter | None = None,
        context=None,
        admission: AdmissionFloor | None = None,
        query_embedding: MemoryQueryEmbedding | None = None,
        recovery_admission: AdmissionFloor | None = None,
        embedding_timeout_seconds: float | None = None,
        allow_embedding: bool = True,
    ) -> AtomicMemorySearchPage:
        application = self.application
        _validate_limit(limit)
        normalize_query(query)
        if mode not in {"auto", "text", "vector", "hybrid"}:
            raise InvalidBaseAccessRequestError("mode", "must be auto, text, vector, or hybrid")
        if kind is not None:
            AtomicMemoryContent(kind=kind, text="validation")
        mode, vector, query_embedding, embedding_calls = await self._resolve_query_embedding(
            query, mode, query_embedding, embedding_timeout_seconds, allow_embedding
        )
        profile = application.index.capabilities.embedding_profile if vector is not None else None
        # Inference finishes before the business snapshot opens. Builtin policy
        # reads share it; configured providers retain their own read boundary.
        # Decision audit writes flush only after the business snapshot closes.
        async with (
            deferred_decision_audit(context),
            application.database.transaction(consistent_snapshot=True) as connection,
        ):
            # Pin SQLite's business read before a configured provider is consulted.
            # Its decision repository may use a separate transaction.
            await connection.execute(
                select(ARTIFACT_HEADS_TABLE.c.artifact_id)
                .where(ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id)
                .limit(1)
            )
            filters = await application.security.filters(connection, self.scope_id, context, tags=tag_filter)
            request = AtomicMemorySearchRequest(
                query,
                replace(filters, kind=kind),
                mode=cast(AtomicMemorySearchMode, "fts" if mode == "text" else mode),
                limit=limit if application.reranker is None else max(limit, application.rerank_candidate_limit),
                query_vector=vector,
                embedding_profile=profile,
                admission=admission,
            )
            channels = await application.index.search(connection, self.scope_id, request)
            hits = combine_atomic_memory_channels(channels)[: request.limit]
            await self._validate_search_hits(connection, hits, verify_vectors=vector is not None)
            recoverable = recovery_admission is not None and await application.index.probe_recoverable(
                connection, self.scope_id, request, recovery_admission
            )
        fts = {_artifact_key(item.artifact_ref) for item in channels.fts}
        vectors = {_artifact_key(item.artifact_ref) for item in channels.vector}
        candidates = tuple(
            AtomicMemorySearchHit(
                hit,
                tuple(
                    channel
                    for channel, refs in (("text", fts), ("vector", vectors))
                    if _artifact_key(hit.artifact_ref) in refs
                ),
            )
            for hit in hits
        )
        page = await self._rerank(
            query,
            mode,
            candidates,
            limit,
            query_embedding if vector is not None else None,
            embedding_calls,
            context,
        )
        return replace(page, recoverable=recoverable)

    async def _validate_search_hits(self, connection, hits, *, verify_vectors):
        """Verify only returned candidates against one bounded authority snapshot.

        The current projection still supplies the body and filters before LIMIT.
        An inconsistent candidate fails the search; it is never silently dropped
        or replaced with authority content. Every authority read is batched.
        """
        if not hits:
            return
        ids = tuple(hit.artifact_ref.artifact_id for hit in hits)
        application = self.application
        table = application.index.table
        columns = (
            "artifact_id",
            "revision",
            "state_version",
            "kind",
            "text",
            "content_hash",
            "searchable_text",
            "embedding_input_hash",
            "profile_fingerprint",
            "projection_format",
        )
        projected = {
            row["artifact_id"]: row
            for row in (
                await connection.execute(
                    select(*(table.c[name] for name in columns)).where(
                        table.c.scope_id == self.scope_id, table.c.artifact_id.in_(ids)
                    )
                )
            ).mappings()
        }
        heads = {
            row["artifact_id"]: row
            for row in (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == "atomic-memory",
                        ARTIFACT_HEADS_TABLE.c.artifact_id.in_(ids),
                    )
                )
            ).mappings()
        }
        states = {
            row["artifact_id"]: row
            for row in (
                await connection.execute(
                    select(ATOMIC_MEMORY_STATES_TABLE).where(
                        ATOMIC_MEMORY_STATES_TABLE.c.scope_id == self.scope_id,
                        ATOMIC_MEMORY_STATES_TABLE.c.artifact_id.in_(ids),
                    )
                )
            ).mappings()
        }
        for hit in hits:
            identity = hit.artifact_ref.artifact_id
            projection, head, state = projected.get(identity), heads.get(identity), states.get(identity)
            if (
                projection is None
                or head is None
                or state is None
                or hit.artifact_ref.family != "atomic-memory"
                or head["revision"] != hit.artifact_ref.revision
                or projection["revision"] != hit.artifact_ref.revision
                or state["state"] != "active"
                or state["merged_into_id"] is not None
                or head["lifecycle_state"] != "active"
                or head["replacement_artifact_id"] is not None
                or state["state_version"] != hit.state_version
                or projection["state_version"] != hit.state_version
                or head["governance_generation"] != hit.state_version
            ):
                raise AtomicMemoryIndexError("stale-projection", "Atomic Memory projection identity is inconsistent")
        artifacts = await application.artifacts.get_many(
            connection, self.scope_id, tuple(hit.artifact_ref for hit in hits)
        )
        for hit, artifact in zip(hits, artifacts, strict=True):
            projection = projected[hit.artifact_ref.artifact_id]
            content = artifact.content
            payload_hash = sha256(canonical_json(content.model_dump(mode="json", by_alias=True))).hexdigest()
            input_hash = (
                None
                if application.publisher.profile_fingerprint is None
                else atomic_memory_embedding_input_hash(content.kind, content.text)
            )
            if (
                hit.kind != content.kind
                or hit.text != content.text
                or projection["kind"] != content.kind
                or projection["text"] != content.text
                or projection["content_hash"] != payload_hash
                or projection["searchable_text"]
                != analyze_text(atomic_memory_embedding_input(content.kind, content.text))
                or (verify_vectors and projection["embedding_input_hash"] != input_hash)
                or (verify_vectors and projection["profile_fingerprint"] != application.publisher.profile_fingerprint)
                or projection["projection_format"] != ATOMIC_MEMORY_PROJECTION_FORMAT
            ):
                raise AtomicMemoryIndexError("stale-projection", "Atomic Memory projection content is inconsistent")

    async def _resolve_query_embedding(self, query, mode, reuse, embedding_timeout_seconds, allow_embedding):
        application = self.application
        capabilities = application.index.capabilities
        profile = capabilities.embedding_profile
        requested_mode = mode
        if mode == "auto":
            mode = "hybrid" if profile is not None else "text"
            if not allow_embedding and capabilities.fts:
                return "text", None, None, 0
        if mode not in {"vector", "hybrid"}:
            return mode, None, None, 0
        model = application.embedding_model
        if profile is None or model is None or model.profile != profile:
            raise AtomicMemoryIndexError("embedding-profile", "Requested vector retrieval is unavailable")
        if reuse is not None and reuse.embedding_profile == profile:
            return mode, reuse.query_vector, reuse, 0
        try:
            async with asyncio.timeout(embedding_timeout_seconds):
                result = await embed_query(model, (query,))
        except (InferenceUnavailableError, InferenceTimeoutError, TimeoutError):
            if requested_mode != "auto" or not capabilities.fts:
                raise
            return "text", None, None, 1
        if len(result.vectors) != 1:
            raise AtomicMemoryIndexError("embedding-result", "Expected one query vector")
        vector = canonical_embedding(
            result.vectors[0], dimension=profile.dimension, normalization=profile.normalization
        )
        return mode, vector, MemoryQueryEmbedding(vector, profile), 1

    async def _filter_current_candidates(
        self,
        candidates: tuple[AtomicMemorySearchHit, ...],
        *,
        context: ArtifactSearchExecutionContext | None = None,
    ) -> tuple[AtomicMemorySearchHit, ...]:
        """Recheck Scope read and retain exact, active candidates from current authority."""
        if not candidates:
            return ()
        application = self.application
        available: list[AtomicMemorySearchHit] = []
        async with (
            deferred_decision_audit(context),
            application.database.transaction(consistent_snapshot=True) as connection,
        ):
            await application.security.authorize(connection, self.scope_id, context, "read")
            for candidate in candidates:
                current = await self._current_record(connection, candidate.hit.artifact_ref.artifact_id)
                if (
                    current.ref == candidate.hit.artifact_ref
                    and current.state.state_version == candidate.hit.state_version
                    and current.state.state is AtomicMemoryStateValue.ACTIVE
                ):
                    available.append(candidate)
        return tuple(available)

    async def _rerank(self, query, mode, candidates, limit, query_embedding, embedding_calls, context):
        application = self.application
        reranker = application.reranker
        if reranker is None or not candidates:
            return AtomicMemorySearchPage(mode, candidates[:limit], query_embedding, embedding_calls)
        # Reauthorize the exact candidate bodies immediately before an external rank model.
        candidates = await self._filter_current_candidates(candidates, context=context)
        if not candidates:
            return AtomicMemorySearchPage(mode, (), query_embedding, embedding_calls)
        prompt = (
            None if application.prompt_context_factory is None else application.prompt_context_factory(self.scope_id)
        )
        binding = nullcontext() if prompt is None else prompt.service.bind(self.scope_id, "memory.rerank")
        started = perf_counter()
        async with binding:
            decision = await reranker.rerank(query, candidates, min(limit, len(candidates)))
        ranks = decision.selected_ranks
        if (
            len(ranks) > limit
            or len(set(ranks)) != len(ranks)
            or any(rank < 1 or rank > len(candidates) for rank in ranks)
        ):
            raise InvalidInferenceOutputError("atomic-memory-rerank", "Reranker returned invalid candidate ranks")
        trace = AtomicMemoryRerankTrace(
            reranker.policy_id,
            candidates,
            ranks,
            decision.discarded_rank_count,
            decision.used_fallback,
            (perf_counter() - started) * 1000,
            decision.usage,
        )
        return AtomicMemorySearchPage(
            mode, tuple(candidates[rank - 1] for rank in ranks), query_embedding, embedding_calls, 1, rerank=trace
        )

    async def merge(
        self, inputs: tuple[AtomicMemoryRead, ...], content: AtomicMemoryContent, *, lineage=None, context=None
    ):
        application = self.application
        async with application.database.transaction() as connection:
            plan = await application.service.inspect_merge(
                connection,
                self.scope_id,
                application.id_factory("atomic-memory"),
                inputs,
                content,
                context,
                lineage=lineage,
            )
        prepared = await application.service.prepare_merge(plan)
        async with application.database.transaction() as connection:
            return await application.service.commit(connection, prepared, context)

    async def forget(self, artifact_id: str, *, expected_revision: int, expected_state_version: int, context=None):
        application = self.application
        async with application.database.transaction() as connection:
            plan = await application.service.inspect_forget(
                connection,
                self.scope_id,
                artifact_id,
                context,
                expected_revision=expected_revision,
                expected_state_version=expected_state_version,
            )
        prepared = await application.service.prepare_forget(plan)
        async with application.database.transaction() as connection:
            return await application.service.commit(connection, prepared, context)

    async def preview_restoration(self, artifact_id: str, *, operation="restore", revision=None, context=None):
        application = self.application
        async with (
            deferred_decision_audit(context),
            application.database.transaction(consistent_snapshot=True) as connection,
        ):
            plan = await application.service.inspect_restore(
                connection, self.scope_id, artifact_id, context, operation=operation, revision=revision
            )
        return application.service.restoration_preview(plan)

    async def restore(self, artifact_id: str, *, operation="restore", revision=None, preview_token=None, context=None):
        application = self.application
        for attempt in range(application.restore_retry_budget + 1):
            try:
                async with application.database.transaction() as connection:
                    plan = await application.service.inspect_restore(
                        connection,
                        self.scope_id,
                        artifact_id,
                        context,
                        operation=operation,
                        revision=revision,
                        preview_token=preview_token,
                    )
                prepared = await application.service.prepare_restore(plan)
                async with application.database.transaction() as connection:
                    return await application.service.commit(connection, prepared, context)
            except AtomicMemoryConflictError as error:
                if preview_token is not None:
                    if isinstance(error, AtomicMemoryPreviewStaleError):
                        raise
                    raise AtomicMemoryPreviewStaleError("restoration preview is stale") from error  # noqa: TRY003
                if attempt == application.restore_retry_budget:
                    raise
        raise AssertionError("restoration retry loop ended without a result")  # noqa: TRY003


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise InvalidBaseAccessRequestError("limit", "must be between 1 and 100")


def _artifact_key(ref) -> tuple[str, str, int]:
    return ref.family, ref.artifact_id, ref.revision
