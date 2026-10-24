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
import logging
from collections.abc import Sequence
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import cast
from uuid import uuid4

from sqlalchemy import Text, and_, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactLineage, ArtifactRef, ArtifactSearchContractError, ArtifactSearchMatch
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.atomic_memory.errors import (
    AtomicMemoryConflictError,
    AtomicMemoryPreviewStaleError,
    AtomicMemoryRelationError,
)
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemory,
    AtomicMemoryContent,
    AtomicMemoryRead,
    AtomicMemoryRecord,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.atomic_memory.restoration import AtomicMemoryPreviewSigner
from powercontext.builtin.artifacts.atomic_memory.search import AtomicArtifactSearchRequest, plan_atomic_search
from powercontext.builtin.artifacts.atomic_memory.service import AtomicMemoryService
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding, canonical_json, normalize_query
from powercontext.builtin.artifacts.memory.models import MemoryQueryEmbedding
from powercontext.builtin.artifacts.memory.reranking import MemoryReranker
from powercontext.builtin.artifacts.search import (
    DEFAULT_RECALL_CHANNEL_WEIGHTS,
    AdmissionCounts,
    AdmissionFloor,
    RecallChannelWeights,
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
from powercontext.builtin.persistence.artifacts import ArtifactRepository, with_artifact_content
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_index import (
    ATOMIC_MEMORY_RRF_CONSTANT,
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
from powercontext.builtin.persistence.cursor_codec import SignedCursorCodec
from powercontext.builtin.persistence.database import SELECTION_BATCH_SIZE, AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, MYSQL_IDENTITY_COLLATION
from powercontext.builtin.persistence.tags import tag_predicate
from powercontext.builtin.records import BaseOperationNotSupportedError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime.artifact_merge import ArtifactMergeAccess, merge_artifact_tags
from powercontext.builtin.tags import TagFilter

logger = logging.getLogger(__name__)


def _fallback_reason(error: Exception) -> str:
    """Why an abandoned query embedding gave way to lexical retrieval.

    This is RFC 1435's ``fallback_reason`` vocabulary and the ``memory.search.embedding_fallback``
    ``error_code`` at once: one classification, one spelling, so the event and the response cannot
    describe the same lost channel differently.
    """

    return "inference_timeout" if isinstance(error, (InferenceTimeoutError, TimeoutError)) else "inference_unavailable"


def _log_embedding_fallback(error: Exception) -> None:
    """Record that a bounded query embedding gave way to lexical retrieval; the query itself is never logged."""

    log_safely(
        logger,
        logging.WARNING,
        "Memory search fell back to lexical retrieval",
        extra={
            "event": "memory.search.embedding_fallback",
            "outcome": "fallback",
            "mode": "text",
            "error_code": _fallback_reason(error),
            "unit": "memory",
        },
    )


class AtomicMemoryAccess(ArtifactMergeAccess):
    def __init__(self) -> None:
        super().__init__("atomic-memory")

    @override
    async def authorize_creates(self, connection, scope_id, context, refs) -> None:
        if type(self) is AtomicMemoryAccess:
            if refs:
                await super().authorize(connection, scope_id, context, "create")
            return
        await super().authorize_creates(connection, scope_id, context, refs)

    @override
    async def authorize(
        self,
        connection: AsyncConnection,
        scope_id: str,
        context: ArtifactSearchExecutionContext | None,
        action: str,
        ref: ArtifactRef | None = None,
    ) -> None:
        # Artifact reads with deferred audit retain their snapshot-bound policy.
        # Other decisions use the shared transaction-bound policy.
        if (
            action == "read"
            and ref is not None
            and ref.family not in {"prompt", "topic-memory"}
            and context is not None
            and context.access is not None
            and context.access._deferred_decisions.get() is not None
        ):
            from powercontext.server.authz import AccessAction, ResourceRef

            access = context.access.for_read(connection)
            await access.require(
                context.principal,
                AccessAction.ARTIFACT_READ,
                ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id),
                context=context.audit,
            )
            return
        await super().authorize(connection, scope_id, context, action, ref)

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
    """Exact retrieval evidence with an in-process bound for normalized RRF captures."""

    hit: AtomicMemoryIndexHit
    matched_by: tuple[str, ...]
    score_upper_bound: float | None = None
    lineage: ArtifactLineage | None = None

    @property
    def text(self) -> str:
        return self.hit.text

    @property
    def relevance(self) -> float | None:
        """Cosine similarity of the vector channel, when this hit was vector-matched."""

        return (
            None
            if "vector" not in self.matched_by or self.hit.distance is None
            else unit_l2_cosine_similarity(self.hit.distance)
        )


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
    matches: tuple[ArtifactSearchMatch, ...] = ()
    artifacts: tuple[AtomicMemory, ...] = ()
    #: Set only when the query embedding was abandoned for lexical retrieval, so a
    #: caller can tell "no vector channel was ever wanted" apart from "it was lost".
    embedding_fallback: str | None = None


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
        recall_channel_weights: RecallChannelWeights = DEFAULT_RECALL_CHANNEL_WEIGHTS,
        prompt_context_factory=None,
    ) -> None:
        if reranker is not None and not getattr(reranker, "supports_atomic_memory", False):
            raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "Atomic text candidate contract")
        self.reranker = reranker
        self.rerank_candidate_limit = rerank_candidate_limit
        self.recall_channel_weights = recall_channel_weights
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
        await merge_artifact_tags(connection, scope_id, result_id, input_ids, family=self.security.family)


class ScopedAtomicMemory:
    def __init__(self, application: AtomicMemoryApplication, scope_id: str) -> None:
        self.application = application
        self.scope_id = scope_id

    @asynccontextmanager
    async def _read(self, context, *, pin: bool = True):
        # Local policy shares the business snapshot; external providers keep their boundary.
        # Flush audit after closing the read snapshot. SAVEPOINT preserves enclosing writes.
        async with (
            deferred_decision_audit(context),
            self.application.database.transaction(consistent_snapshot=True) as connection,
        ):
            # Pin business data before a configured provider can consult a separate policy database.
            if pin and context is not None and context.access is not None:
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE.c.artifact_id)
                    .where(ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id)
                    .limit(1)
                )
            yield connection

    async def state(self, artifact_id: str, *, context=None) -> AtomicMemoryRead:
        # The authoritative head itself pins revision and lifecycle before the PEP.
        async with self._read(context, pin=False) as connection:
            return await self.application.service.read_state(connection, self.scope_id, artifact_id, context)

    async def get(self, artifact_id: str, *, revision: int | None = None, context=None) -> AtomicMemoryRecord:
        async with self._read(context) as connection:
            return await self.application.service.get(
                connection, self.scope_id, artifact_id, context, revision=revision
            )

    async def get_many(self, artifact_ids: Sequence[str], *, context=None) -> tuple[AtomicMemoryRecord, ...]:
        """Read a caller's exact results in one snapshot after checking each resource."""

        if not artifact_ids:
            return ()
        async with self._read(context) as connection:
            identities = tuple(dict.fromkeys(artifact_ids))
            if context is not None and context.access is not None:
                from powercontext.server.authz import ResourceRef

                access = context.access.for_read(connection)
                policy = access.prefetch_point_decisions(
                    connection,
                    context.principal,
                    tuple(
                        ResourceRef.artifact(self.scope_id, family="atomic-memory", artifact_id=artifact_id)
                        for artifact_id in identities
                    ),
                    context=context.audit,
                )
            else:
                policy = nullcontext()
            async with policy:
                for artifact_id in identities:
                    await self.application.security.authorize(
                        connection,
                        self.scope_id,
                        context,
                        "read",
                        ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1),
                    )
            rows = []
            for offset in range(0, len(artifact_ids), SELECTION_BATCH_SIZE):
                statement = with_artifact_content(
                    select(ARTIFACT_HEADS_TABLE).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == "atomic-memory",
                        ARTIFACT_HEADS_TABLE.c.artifact_id.in_(artifact_ids[offset : offset + SELECTION_BATCH_SIZE]),
                    )
                )
                rows.extend((await connection.execute(statement)).mappings().all())
            records = await self.application.service.read_many(connection, self.scope_id, rows)
        by_id = {record.artifact.artifact_id: record for record in records}
        if (
            missing := next((artifact_id for artifact_id in artifact_ids if artifact_id not in by_id), None)
        ) is not None:
            raise RepositoryNotFoundError("artifact", (self.scope_id, "atomic-memory", missing))
        return tuple(by_id[artifact_id] for artifact_id in artifact_ids)

    def list_query(
        self,
        connection: AsyncConnection,
        *,
        states: tuple[str, ...] = ("active",),
        kind: str | None = None,
        tag_filter: TagFilter | None = None,
        limit: int = 50,
        cursor: str | None = None,
        context=None,
    ):
        """Build the canonical browse window; the caller owns its read transaction and PEP."""
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
        table = ARTIFACT_HEADS_TABLE
        statement = (
            select(table)
            .where(
                table.c.scope_id == self.scope_id,
                table.c.family == "atomic-memory",
                table.c.artifact_id > after,
                # Keep lifecycle sargable; deprecated heads are split below.
                table.c.lifecycle_state.in_(
                    tuple(sorted({"deprecated" if state in {"forgotten", "merged"} else state for state in states}))
                ),
            )
            .order_by(table.c.artifact_id)
            .limit(limit + 1)
        )
        if states == ("active",):
            from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
            from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

            index = self.application.index
            if type(index) in (SQLiteAtomicMemoryIndex, OceanBaseAtomicMemoryIndex):
                # The native current projection is the active candidate set. The
                # write/rebuild lifecycle owns completeness; this read rejects stale rows.
                current = index.table
                statement = (
                    statement
                    .select_from(
                        current.join(
                            table,
                            and_(
                                current.c.scope_id == table.c.scope_id,
                                current.c.artifact_id == table.c.artifact_id,
                                current.c.revision == table.c.revision,
                                current.c.state_version == table.c.governance_generation,
                            ),
                        )
                    )
                    .where(
                        current.c.scope_id == self.scope_id,
                        current.c.artifact_id > after,
                        current.c.projection_format == ATOMIC_MEMORY_PROJECTION_FORMAT,
                        table.c.merged_into_id.is_(None),
                    )
                    .order_by(None)
                    .order_by(current.c.artifact_id)
                )
        if ("forgotten" in states) != ("merged" in states):
            statement = statement.where(
                or_(
                    table.c.lifecycle_state != "deprecated",
                    table.c.merged_into_id.is_(None) if "forgotten" in states else table.c.merged_into_id.is_not(None),
                )
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
        if kind is None:
            # LIMIT before joining content keeps the optimizer from driving
            # an ordinary page from the whole revision collection.
            table = statement.subquery("selected_heads")
            statement = select(table).order_by(table.c.artifact_id)
        statement = with_artifact_content(statement, table)
        if kind is not None:
            stored_kind = func.json_extract(sql_cast(ARTIFACTS_TABLE.c.content, Text), "$.kind")
            if connection.dialect.name == "mysql":
                stored_kind = func.json_unquote(stored_kind).collate(MYSQL_IDENTITY_COLLATION)
            else:
                stored_kind = stored_kind.collate("BINARY")
            statement = statement.where(stored_kind == kind)
        return statement, bound, after

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
        async with self._read(context) as connection:
            statement, bound, after = self.list_query(
                connection,
                states=states,
                kind=kind,
                tag_filter=tag_filter,
                limit=limit,
                cursor=cursor,
                context=context,
            )
            await self.application.security.filters(connection, self.scope_id, context, tags=tag_filter)
            rows = (await connection.execute(statement)).mappings().all()
            items = await self.application.service.read_many(connection, self.scope_id, rows[:limit])
        last = after if not items else items[-1].ref.artifact_id
        return AtomicMemoryPage(items, self.application.cursor.encode(bound, last) if len(rows) > limit else None)

    async def search(  # noqa: C901
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
        artifact_request: AtomicArtifactSearchRequest | None = None,
    ) -> AtomicMemorySearchPage:
        application = self.application
        plan = None
        if artifact_request is not None:
            model = application.embedding_model
            plan = plan_atomic_search(
                artifact_request,
                application.index.capabilities,
                embedding_profile=None if model is None else model.profile,
                channel_weights=application.recall_channel_weights,
            )
            query, mode, limit = artifact_request.query, plan.mode, artifact_request.limit
            kind, tag_filter = artifact_request.filters.kind, artifact_request.filters.as_tag_filter()
            admission = artifact_request.admission.as_floor()
        _validate_limit(limit)
        normalize_query(query)
        if mode not in {"auto", "text", "vector", "hybrid"}:
            raise InvalidBaseAccessRequestError("mode", "must be auto, text, vector, or hybrid")
        if kind is not None:
            AtomicMemoryContent(kind=kind, text="validation")
        mode, vector, query_embedding, embedding_calls, embedding_fallback = await self._resolve_query_embedding(
            query,
            mode,
            query_embedding,
            embedding_timeout_seconds,
            allow_embedding,
            public_search=artifact_request is not None,
        )
        channel_weights = application.recall_channel_weights if mode == "hybrid" else None
        profile = application.index.capabilities.embedding_profile if vector is not None else None
        # Inference finishes before the business snapshot opens. Builtin policy
        # reads share it; configured providers retain their own read boundary.
        # Decision audit writes flush only after the business snapshot closes.
        async with self._read(context) as connection:
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
            hits = combine_atomic_memory_channels(
                channels,
                fusion=None if plan is None else plan.fusion,
                mode=mode,
                channel_weights=channel_weights,
            )
            if artifact_request is not None and artifact_request.min_score is not None:
                hits = tuple(
                    hit
                    for hit in hits
                    if hit.retrieval_score is not None and hit.retrieval_score >= artifact_request.min_score
                )
            hits = hits[: request.limit]
            try:
                validated = await self._validate_search_hits(connection, hits, verify_vectors=vector is not None)
            except RepositoryNotFoundError as exc:
                if artifact_request is None:
                    raise
                raise ArtifactSearchContractError("atomic-memory", "Selected exact revision is unavailable") from exc
            artifacts = validated if artifact_request is not None else ()
            recoverable = recovery_admission is not None and await application.index.probe_recoverable(
                connection, self.scope_id, request, recovery_admission
            )
        fts = {_artifact_key(item.artifact_ref) for item in channels.fts}
        vectors = {_artifact_key(item.artifact_ref) for item in channels.vector}
        weights = {
            "text": 1.0 if channel_weights is None else channel_weights.fts,
            "vector": 1.0 if channel_weights is None else channel_weights.vector,
        }
        if plan is not None:
            weights = {name: plan.fusion.weights.get(name, 1.0) for name in weights}
        candidates: list[AtomicMemorySearchHit] = []
        for hit, artifact in zip(hits, validated, strict=True):
            matched_by = tuple(
                channel
                for channel, refs in (("text", fts), ("vector", vectors))
                if weights[channel] > 0.0 and _artifact_key(hit.artifact_ref) in refs
            )
            candidates.append(
                AtomicMemorySearchHit(
                    hit,
                    matched_by,
                    lineage=artifact.lineage,
                    score_upper_bound=(
                        sum(weights[channel] for channel in matched_by) / (ATOMIC_MEMORY_RRF_CONSTANT + 1)
                        if plan is None
                        else None
                    ),
                )
            )
        page = await self._rerank(
            query,
            mode,
            tuple(candidates),
            limit,
            query_embedding if vector is not None else None,
            embedding_calls,
            context,
            artifact_request=artifact_request,
            artifacts=artifacts,
            embedding_fallback=embedding_fallback,
        )
        if artifact_request is not None:
            matches = []
            for item in page.hits:
                if item.hit.retrieval_score is None:
                    raise ArtifactSearchContractError("atomic-memory", "Selected result has no retrieval score")
                matches.append(
                    ArtifactSearchMatch(
                        item.hit.artifact_ref,
                        item.hit.retrieval_score,
                        item.hit.channel_scores if artifact_request.include_scores else None,
                    )
                )
            page = replace(page, matches=tuple(matches))
        return replace(page, recoverable=recoverable)

    async def _validate_search_hits(self, connection, hits, *, verify_vectors) -> tuple[AtomicMemory, ...]:
        """Verify only returned candidates against one bounded authority snapshot.

        The current projection still supplies the body and filters before LIMIT.
        An inconsistent candidate fails the search; it is never silently dropped
        or replaced with authority content. Every authority read is batched.
        """
        if not hits:
            return ()
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
        heads = ARTIFACT_HEADS_TABLE
        rows = {
            row["projection_artifact_id"]: row
            for row in (
                await connection.execute(
                    with_artifact_content(
                        select(heads, *(table.c[name].label(f"projection_{name}") for name in columns)).select_from(
                            table.outerjoin(
                                heads,
                                and_(
                                    heads.c.scope_id == table.c.scope_id,
                                    heads.c.family == "atomic-memory",
                                    heads.c.artifact_id == table.c.artifact_id,
                                ),
                            )
                        )
                    ).where(table.c.scope_id == self.scope_id, table.c.artifact_id.in_(ids))
                )
            ).mappings()
        }
        projected = {identity: {name: row[f"projection_{name}"] for name in columns} for identity, row in rows.items()}
        for hit in hits:
            identity = hit.artifact_ref.artifact_id
            projection, head = projected.get(identity), rows.get(identity)
            if (
                projection is None
                or head is None
                or head["artifact_id"] is None
                or hit.artifact_ref.family != "atomic-memory"
                or head["revision"] != hit.artifact_ref.revision
                or projection["revision"] != hit.artifact_ref.revision
                or head["merged_into_id"] is not None
                or head["lifecycle_state"] != "active"
                or head["replacement_artifact_id"] is not None
                or projection["state_version"] != hit.state_version
                or head["governance_generation"] != hit.state_version
            ):
                raise AtomicMemoryIndexError("stale-projection", "Atomic Memory projection identity is inconsistent")
        artifacts = await application.artifacts.decode_many(
            connection, self.scope_id, tuple(rows[hit.artifact_ref.artifact_id] for hit in hits)
        )
        for hit, artifact in zip(hits, artifacts, strict=True):
            if not isinstance(artifact, AtomicMemory):
                raise ArtifactSearchContractError("atomic-memory", "Selected result has another Artifact Family")
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

        return cast(tuple[AtomicMemory, ...], artifacts)

    async def _resolve_query_embedding(
        self, query, mode, reuse, embedding_timeout_seconds, allow_embedding, *, public_search=False
    ):
        application = self.application
        capabilities = application.index.capabilities
        profile = capabilities.embedding_profile
        requested_mode = mode
        if mode == "auto":
            mode = "hybrid" if profile is not None else "text"
            if not allow_embedding and capabilities.fts:
                return "text", None, None, 0, None
        if mode not in {"vector", "hybrid"}:
            return mode, None, None, 0, None
        model = application.embedding_model
        if profile is None or model is None or model.profile != profile:
            raise AtomicMemoryIndexError("embedding-profile", "Requested vector retrieval is unavailable")
        if reuse is not None and reuse.embedding_profile == profile:
            return mode, reuse.query_vector, reuse, 0, None
        try:
            async with asyncio.timeout(embedding_timeout_seconds):
                result = await embed_query(model, (query,))
        except (InferenceUnavailableError, InferenceTimeoutError, TimeoutError) as exc:
            if public_search and isinstance(exc, TimeoutError) and not isinstance(exc, InferenceTimeoutError):
                raise InferenceUnavailableError("embedding", "Atomic query embedding timed out") from exc
            if requested_mode != "auto" or not capabilities.fts:
                raise
            _log_embedding_fallback(exc)
            return "text", None, None, 1, _fallback_reason(exc)
        if len(result.vectors) != 1:
            raise AtomicMemoryIndexError("embedding-result", "Expected one query vector")
        vector = canonical_embedding(
            result.vectors[0], dimension=profile.dimension, normalization=profile.normalization
        )
        return mode, vector, MemoryQueryEmbedding(vector, profile), 1, None

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
        async with self._read(context) as connection:
            await application.security.authorize(connection, self.scope_id, context, "read")
            rows = (
                await connection.execute(
                    select(ARTIFACT_HEADS_TABLE).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == "atomic-memory",
                        ARTIFACT_HEADS_TABLE.c.artifact_id.in_(
                            tuple(candidate.hit.artifact_ref.artifact_id for candidate in candidates)
                        ),
                    )
                )
            ).mappings()
            heads = {str(row["artifact_id"]): row for row in rows}
            for candidate in candidates:
                artifact_id = candidate.hit.artifact_ref.artifact_id
                head = heads.get(artifact_id)
                if head is None:
                    raise RepositoryNotFoundError("artifact", (self.scope_id, "atomic-memory", artifact_id))
                if head["replacement_artifact_id"] is not None:
                    raise AtomicMemoryRelationError("Atomic Memory does not use generic governance replacements")  # noqa: TRY003
                if (
                    head["revision"] == candidate.hit.artifact_ref.revision
                    and head["governance_generation"] == candidate.hit.state_version
                    and head["lifecycle_state"] == "active"
                    and head["merged_into_id"] is None
                ):
                    available.append(candidate)
        return tuple(available)

    async def _rerank(
        self,
        query,
        mode,
        candidates,
        limit,
        query_embedding,
        embedding_calls,
        context,
        *,
        artifact_request=None,
        artifacts=(),
        embedding_fallback: str | None = None,
    ):
        application = self.application
        reranker = application.reranker
        if reranker is None or not candidates:
            return AtomicMemorySearchPage(
                mode,
                candidates[:limit],
                query_embedding,
                embedding_calls,
                artifacts=artifacts,
                embedding_fallback=embedding_fallback,
            )
        # Reauthorize the exact candidate bodies immediately before an external rank model.
        try:
            candidates = await self._filter_current_candidates(candidates, context=context)
        except RepositoryNotFoundError as exc:
            if artifact_request is None:
                raise
            raise ArtifactSearchContractError("atomic-memory", "Selected exact revision is unavailable") from exc
        if not candidates:
            return AtomicMemorySearchPage(
                mode,
                (),
                query_embedding,
                embedding_calls,
                embedding_fallback=embedding_fallback,
            )
        complete_artifacts = []
        if artifact_request is not None:
            artifacts_by_ref = {_artifact_key(artifact.as_ref()): artifact for artifact in artifacts}
            complete_artifacts = [
                artifacts_by_ref[_artifact_key(candidate.hit.artifact_ref)] for candidate in candidates
            ]
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
            mode,
            tuple(candidates[rank - 1] for rank in ranks),
            query_embedding,
            embedding_calls,
            1,
            rerank=trace,
            artifacts=tuple(complete_artifacts[rank - 1] for rank in ranks) if artifact_request is not None else (),
            embedding_fallback=embedding_fallback,
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
            await connection.execute(
                select(ARTIFACT_HEADS_TABLE.c.artifact_id)
                .where(ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id)
                .limit(1)
            )
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
