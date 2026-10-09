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

"""Atomic Memory projection publication and complete retrieval contracts."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel
from sqlalchemy import Table, bindparam, delete, insert, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, ChannelScore
from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding, canonical_json, normalize_query
from powercontext.builtin.artifacts.search import (
    DEFAULT_ADMISSION_FLOOR,
    AdmissionFloor,
    InvalidSearchScore,
    analyze_text,
    fts_query_requirements,
    lexical_search_score,
)
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.atomic_memory_index_schema import ATOMIC_MEMORY_PROJECTION_FORMAT
from powercontext.builtin.tags import TagFilter

AtomicMemorySearchMode = Literal["fts", "vector", "hybrid"]


def atomic_memory_coverage_sql(terms: Sequence[str]) -> str:
    """Sum integer term matches with logarithmic expression depth.

    Parentheses avoid SQLite's left-associated expression depth limit without
    removing terms or changing lexical coverage and admission.
    """
    expressions = list(terms)
    while len(expressions) > 1:
        expressions = [
            f"({expressions[index]} + {expressions[index + 1]})" if index + 1 < len(expressions) else expressions[index]
            for index in range(0, len(expressions), 2)
        ]
    return expressions[0] if expressions else "0"


class AtomicMemoryIndexError(RuntimeError):
    """Retrieval cannot meet the requested channel or completeness contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AtomicMemoryIndexCapabilities:
    fts: bool = True
    vector: bool = False
    hybrid: bool = False
    tag_filter: bool = True
    embedding_profile: EmbeddingProfile | None = None


@dataclass(frozen=True)
class AtomicMemoryReadGrant:
    """A current direct read binding, including its source and expiry."""

    binding_id: str
    subject_type: str
    subject_id: str
    expires_at: datetime | None = None

    def as_json(self) -> dict[str, object]:
        if self.expires_at is not None and self.expires_at.utcoffset() is None:
            raise AtomicMemoryIndexError("grant-expiry", "Read grant expiry must have a timezone")
        return {
            "binding_id": self.binding_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "expires_at": None if self.expires_at is None else self.expires_at.timestamp(),
        }


@dataclass(frozen=True)
class AtomicMemoryProjectionSecurity:
    owner_type: str
    owner_id: str
    read_grants: tuple[AtomicMemoryReadGrant, ...] = ()


@dataclass(frozen=True)
class AtomicMemoryIndexFilter:
    """Trusted authorization conditions, applied before scoring or truncation.

    scope_read means the authorization boundary verified scope-wide body read.
    writable always requires the formal artifact owner, including for a scope
    administrator. Group IDs must come from the trusted identity resolver.
    """

    tag_filter: TagFilter | None = None
    kind: str | None = None
    principal_type: str | None = None
    principal_id: str | None = None
    scope_read: bool = False
    writable: bool = False
    group_ids: tuple[str, ...] = ()
    now: datetime | None = None


@dataclass(frozen=True)
class PreparedAtomicMemoryProjection:
    kind: str
    text: str
    searchable_text: str
    content_hash: str
    embedding: tuple[float, ...] | None = None
    embedding_profile: EmbeddingProfile | None = None
    profile_fingerprint: str | None = None
    embedding_input_hash: str | None = None


@dataclass(frozen=True)
class AtomicMemoryProjection:
    artifact_ref: ArtifactRef
    state_version: int
    prepared: PreparedAtomicMemoryProjection
    tag_keys: tuple[str, ...]
    security: AtomicMemoryProjectionSecurity


@dataclass(frozen=True)
class AtomicMemoryIndexHit:
    artifact_ref: ArtifactRef
    state_version: int
    kind: str
    text: str
    score: float
    distance: float | None = None
    channel_scores: dict[str, ChannelScore] | None = None
    retrieval_score: float | None = None


@dataclass(frozen=True)
class AtomicMemorySearchChannels:
    fts: tuple[AtomicMemoryIndexHit, ...] = ()
    vector: tuple[AtomicMemoryIndexHit, ...] = ()


@dataclass(frozen=True)
class AtomicMemorySearchRequest:
    query: str
    filters: AtomicMemoryIndexFilter
    mode: AtomicMemorySearchMode = "fts"
    limit: int = 20
    query_vector: tuple[float, ...] | None = None
    embedding_profile: EmbeddingProfile | None = None
    admission: AdmissionFloor | None = None


@dataclass(frozen=True)
class AtomicMemoryRelatedRequest:
    """Complete channel enumeration; there is deliberately no candidate limit."""

    query: str
    filters: AtomicMemoryIndexFilter
    mode: AtomicMemorySearchMode = "fts"
    query_vector: tuple[float, ...] | None = None
    embedding_profile: EmbeddingProfile | None = None
    max_distance: float | None = None
    admission: AdmissionFloor | None = None


class AtomicMemoryIndex(Protocol):
    table: Table
    tables: tuple[Table, ...]
    capabilities: AtomicMemoryIndexCapabilities

    async def initialize(self, connection: AsyncConnection, /) -> None: ...

    async def replace(
        self, connection: AsyncConnection, scope_id: str, projection: AtomicMemoryProjection, /
    ) -> None: ...

    async def delete(self, connection: AsyncConnection, scope_id: str, artifact_id: str, /) -> None: ...

    async def search(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemorySearchRequest, /
    ) -> AtomicMemorySearchChannels: ...

    async def probe_recoverable(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemorySearchRequest, floor: AdmissionFloor, /
    ) -> bool: ...

    async def enumerate_related(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemoryRelatedRequest, /
    ) -> tuple[AtomicMemoryIndexHit, ...]: ...

    async def refresh_tags(
        self, connection: AsyncConnection, scope_id: str, artifact_id: str, tag_keys: tuple[str, ...], /
    ) -> None: ...

    async def refresh_access(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        owner_type: str,
        owner_id: str,
        read_grants: tuple[AtomicMemoryReadGrant, ...],
        /,
    ) -> None: ...


class AtomicMemoryProjectionPublisher:
    """Prepare vectors outside writes and publish with the authoritative transaction."""

    def __init__(
        self,
        index: AtomicMemoryIndex,
        *,
        load_tags: Callable[[AsyncConnection, str, str], Awaitable[tuple[str, ...]]],
        load_security: Callable[[AsyncConnection, str, str, Any], Awaitable[AtomicMemoryProjectionSecurity]],
        embedding_model: EmbeddingModel | None = None,
    ) -> None:
        self.index = index
        self._load_tags = load_tags
        self._load_security = load_security
        self._embedding_model = embedding_model

    @property
    def profile_fingerprint(self) -> str | None:
        profile = self.index.capabilities.embedding_profile
        return None if profile is None else atomic_memory_profile_fingerprint(profile)

    def validate_prepared(self, prepared: PreparedAtomicMemoryProjection) -> None:
        """Reject a prepared vector after a deployment profile change."""

        profile = self.index.capabilities.embedding_profile
        if prepared.profile_fingerprint != self.profile_fingerprint or prepared.embedding_profile != profile:
            raise AtomicMemoryIndexError("embedding-profile", "Embedding profile changed after projection preparation")
        if profile is None:
            if prepared.embedding is not None or prepared.embedding_input_hash is not None:
                raise AtomicMemoryIndexError("embedding-profile", "No-vector deployment received a prepared vector")
        elif prepared.embedding is None or prepared.embedding_input_hash != atomic_memory_embedding_input_hash(
            prepared.kind, prepared.text
        ):
            raise AtomicMemoryIndexError("embedding-input", "Prepared vector does not match the embedding input")

    async def prepare(self, content: BaseModel) -> PreparedAtomicMemoryProjection:
        payload = content.model_dump(mode="json", by_alias=True)
        kind, body = payload.get("kind"), payload.get("text")
        if not isinstance(kind, str) or not isinstance(body, str) or not kind.strip() or not body.strip():
            raise AtomicMemoryIndexError("content", "Atomic Memory requires nonempty kind and text")
        input_text = atomic_memory_embedding_input(kind, body)
        profile = self.index.capabilities.embedding_profile
        vector = None
        if profile is not None:
            model = self._embedding_model
            if model is None or model.profile != profile:
                raise AtomicMemoryIndexError("embedding-profile", "Atomic Memory embedding model does not match index")
            result = await model.embed((input_text,))
            if len(result.vectors) != 1:
                raise AtomicMemoryIndexError("embedding-result", "Embedding model did not return one vector per input")
            vector = canonical_embedding(
                result.vectors[0], dimension=profile.dimension, normalization=profile.normalization
            )
        return PreparedAtomicMemoryProjection(
            kind=kind,
            text=body,
            searchable_text=analyze_text(input_text),
            content_hash=sha256(canonical_json(payload)).hexdigest(),
            embedding=vector,
            embedding_profile=None if profile is None else profile.model_copy(deep=True),
            profile_fingerprint=None if profile is None else atomic_memory_profile_fingerprint(profile),
            embedding_input_hash=None if vector is None else atomic_memory_embedding_input_hash(kind, body),
        )

    async def publish(
        self,
        connection: AsyncConnection,
        scope_id: str,
        record: Any,
        prepared: PreparedAtomicMemoryProjection,
        execution_context: Any,
    ) -> None:
        artifact = record.artifact
        if record.state.state != "active":
            await self.remove(connection, scope_id, artifact.artifact_id)
            return
        content_hash = sha256(canonical_json(artifact.content.model_dump(mode="json", by_alias=True))).hexdigest()
        if content_hash != prepared.content_hash:
            raise AtomicMemoryIndexError("prepared-content", "Prepared projection does not match the committed content")
        self.validate_prepared(prepared)
        tags = await self._load_tags(connection, scope_id, artifact.artifact_id)
        security = await self._load_security(connection, scope_id, artifact.artifact_id, execution_context)
        await self.index.replace(
            connection,
            scope_id,
            AtomicMemoryProjection(
                artifact_ref=artifact.as_ref(),
                state_version=record.state.state_version,
                prepared=prepared,
                tag_keys=tags,
                security=security,
            ),
        )

    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str) -> None:
        await self.index.delete(connection, scope_id, artifact_id)


def atomic_memory_embedding_input(kind: str, body: str, /) -> str:
    return f"{kind}\n{body}"


def atomic_memory_embedding_input_hash(kind: str, body: str, /) -> str:
    return sha256(atomic_memory_embedding_input(kind, body).encode("utf-8")).hexdigest()


def atomic_memory_profile_fingerprint(profile: EmbeddingProfile, /) -> str:
    return sha256(canonical_json(profile.model_dump(mode="json"))).hexdigest()


def atomic_memory_filter_sql(
    filters: AtomicMemoryIndexFilter, dialect: Literal["sqlite", "mysql"], /
) -> tuple[str, dict[str, object]]:
    """Render only fixed SQL structure; all identities and tags are bound parameters."""

    parameters: dict[str, object] = {}
    conditions: list[str] = []
    if (filters.principal_type is None) != (filters.principal_id is None):
        raise AtomicMemoryIndexError("principal", "Both principal type and ID are required")
    if filters.writable or not filters.scope_read:
        if filters.principal_type is None:
            raise AtomicMemoryIndexError("authorization", "Retrieval requires an authenticated or trusted read policy")
        parameters.update(principal_type=filters.principal_type, principal_id=filters.principal_id)
        owner = "(owner_type = :principal_type AND owner_id = :principal_id)"
        if filters.writable:
            conditions.append(owner)
        else:
            grant, grant_parameters = _atomic_memory_read_grant_sql(filters, dialect)
            parameters.update(grant_parameters)
            conditions.append(f"({owner} OR {grant})")
    if filters.tag_filter is not None:
        tags: list[str] = []
        for index, tag_key in enumerate(filters.tag_filter.keys):
            key = f"tag_key_{index}"
            parameters[key] = tag_key
            tags.append(
                f"EXISTS (SELECT 1 FROM json_each(tag_keys) AS t WHERE t.value = :{key})"  # noqa: S608
                if dialect == "sqlite"
                else f"JSON_CONTAINS(tag_keys, JSON_QUOTE(:{key})) = 1"
            )
        conjunction = " AND " if filters.tag_filter.match == "all" else " OR "
        conditions.append(f"({conjunction.join(tags)})")
    if filters.kind is not None:
        conditions.append("kind = :kind_filter" if dialect == "sqlite" else "BINARY kind = BINARY :kind_filter")
        parameters["kind_filter"] = filters.kind
    return " AND ".join(conditions) or "1 = 1", parameters


def _atomic_memory_read_grant_sql(
    filters: AtomicMemoryIndexFilter, dialect: Literal["sqlite", "mysql"]
) -> tuple[str, dict[str, object]]:
    now = datetime.now(UTC) if filters.now is None else filters.now
    if now.utcoffset() is None:
        raise AtomicMemoryIndexError("clock", "Authorization query time must have a timezone")
    parameters: dict[str, object] = {"access_now": now.timestamp()}
    if dialect == "sqlite":
        subject = (
            "(json_extract(g.value, '$.subject_type') = :principal_type "
            "AND json_extract(g.value, '$.subject_id') = :principal_id)"
        )
        expiry = "json_extract(g.value, '$.expires_at')"
        source = "json_each(read_grants) AS g"
    else:
        subject = "(BINARY g.subject_type = BINARY :principal_type AND BINARY g.subject_id = BINARY :principal_id)"
        expiry = "g.expires_at"
        source = (
            "JSON_TABLE(read_grants, '$[*]' COLUMNS("
            "subject_type VARCHAR(16) PATH '$.subject_type', "
            "subject_id VARCHAR(255) PATH '$.subject_id', "
            "expires_at DOUBLE PATH '$.expires_at' NULL ON EMPTY)) AS g"
        )
    subjects = [subject]
    for index, group_id in enumerate(filters.group_ids):
        key = f"access_group_{index}"
        parameters[key] = group_id
        subjects.append(
            f"(json_extract(g.value, '$.subject_type') = 'group' AND json_extract(g.value, '$.subject_id') = :{key})"
            if dialect == "sqlite"
            else f"(BINARY g.subject_type = BINARY 'group' AND BINARY g.subject_id = BINARY :{key})"
        )
    # SQL fragments are fixed above; principal/group IDs remain bound parameters.
    return (
        f"EXISTS (SELECT 1 FROM {source} WHERE ({' OR '.join(subjects)}) "  # noqa: S608
        f"AND ({expiry} IS NULL OR {expiry} > :access_now))",
        parameters,
    )


def atomic_memory_vector_sql(
    eligibility: str, dialect: Literal["sqlite", "mysql"], /, *, bounded: bool, threshold: bool
) -> str:
    """One statement checks readiness and retrieves matches at one channel snapshot.

    The sentinel reports any incomplete eligible row even when no vector meets
    the threshold. Bounded results limit only after same-row eligibility and exact
    scoring. Explicit L2 thresholds belong to complete related enumeration;
    ordinary search uses cosine admission on unit-normalized L2 vectors.
    There is no business-table join, ANN or enumeration result cap.
    """

    distance = "vec_distance_l2" if dialect == "sqlite" else "l2_distance"
    invalid_length = " OR LENGTH(embedding) <> :embedding_bytes" if dialect == "sqlite" else ""
    valid_length = " AND LENGTH(embedding) = :embedding_bytes" if dialect == "sqlite" else ""
    similarity = "1.0 - distance * distance / 2.0"
    clamped_similarity = (
        f"max(-1.0, min(1.0, {similarity}))" if dialect == "sqlite" else f"greatest(-1.0, least(1.0, {similarity}))"
    )
    threshold_sql = (
        " AND distance <= :max_distance" if threshold else f" AND {clamped_similarity} >= :min_semantic_similarity"
    )
    limit_sql = " LIMIT :result_limit" if bounded else ""
    # eligibility is built by atomic_memory_filter_sql from fixed grammar.
    return (
        "WITH eligible AS (SELECT artifact_id, revision, state_version, kind, text, embedding, "  # noqa: S608
        "profile_fingerprint, embedding_input_hash, projection_format FROM pc_atomic_memory_current "
        f"WHERE scope_id = :scope_id AND ({eligibility})), "
        "readiness AS (SELECT COUNT(*) AS invalid_vectors FROM eligible WHERE embedding IS NULL "
        "OR profile_fingerprint IS NULL OR profile_fingerprint <> :profile OR embedding_input_hash IS NULL "
        f"OR projection_format <> :projection_format{invalid_length}), "
        "scored AS (SELECT artifact_id, revision, state_version, kind, text, "
        f"{distance}(embedding, :query_vector) AS distance FROM eligible "
        "WHERE embedding IS NOT NULL AND profile_fingerprint = :profile AND embedding_input_hash IS NOT NULL "
        f"AND projection_format = :projection_format{valid_length}), "
        "matched AS (SELECT artifact_id, revision, state_version, kind, text, distance, 0 AS invalid_vectors "
        f"FROM scored WHERE (SELECT invalid_vectors FROM readiness) = 0{threshold_sql} "
        f"ORDER BY distance, artifact_id{limit_sql}) "
        "SELECT artifact_id, revision, state_version, kind, text, distance, invalid_vectors FROM matched "
        "UNION ALL SELECT NULL, NULL, NULL, NULL, NULL, NULL, invalid_vectors FROM readiness WHERE invalid_vectors > 0 "
        "ORDER BY invalid_vectors DESC, distance, artifact_id"
    )


_AtomicMemoryQuery = TypeVar("_AtomicMemoryQuery", bound=AtomicMemorySearchRequest | AtomicMemoryRelatedRequest)


def freeze_atomic_memory_query_time(request: _AtomicMemoryQuery, /) -> _AtomicMemoryQuery:
    if request.filters.now is not None:
        return request
    return replace(request, filters=replace(request.filters, now=datetime.now(UTC)))


def atomic_memory_channel_hits(rows: Any, /, *, vector: bool = False) -> tuple[AtomicMemoryIndexHit, ...]:
    rows = tuple(rows)
    if vector and any(int(row["invalid_vectors"]) for row in rows):
        raise AtomicMemoryIndexError(
            "incomplete-vector", "Eligible Atomic Memory vectors are incomplete or use another profile"
        )
    hits = []
    for row in rows:
        distance = float(row["distance"]) if vector else None
        channel_scores = None
        if distance is not None:
            if not math.isfinite(distance) or distance < 0:
                raise InvalidSearchScore("Atomic vector distance must be finite and nonnegative")  # noqa: TRY003
            channel_scores = {"vector": ChannelScore(distance, "l2_distance", False)}
        elif "raw_score" in row:
            _, observation = lexical_search_score(row["raw_score"], row.get("score_metric"))
            channel_scores = {"text": observation}
        hits.append(
            AtomicMemoryIndexHit(
                artifact_ref=ArtifactRef(
                    family="atomic-memory", artifact_id=str(row["artifact_id"]), revision=int(row["revision"])
                ),
                state_version=int(row["state_version"]),
                kind=str(row["kind"]),
                text=str(row["text"]),
                score=-distance if distance is not None else float(row["score"]),
                distance=distance,
                channel_scores=channel_scores,
            )
        )
    return tuple(hits)


def combine_atomic_memory_channels(
    channels: AtomicMemorySearchChannels, /, *, fusion: RrfParameters | None = None, mode: str = "hybrid"
) -> tuple[AtomicMemoryIndexHit, ...]:
    """Return exact-ref union with the RRF score used for its stable ordering.

    Vector distance remains independent of the fused ranking score. Revision or
    state changes across channels require a new recall rather than mixed data.
    """

    versions: dict[str, tuple[int, int]] = {}
    hits: dict[str, AtomicMemoryIndexHit] = {}
    ranks: dict[str, float] = {}
    for name, channel in (("text", channels.fts), ("vector", channels.vector)):
        for rank, hit in enumerate(channel):
            artifact_id = hit.artifact_ref.artifact_id
            version = (hit.artifact_ref.revision, hit.state_version)
            if artifact_id in versions and versions[artifact_id] != version:
                raise AtomicMemoryIndexError("stale-recall", "Atomic Memory changed between retrieval channels")
            versions[artifact_id] = version
            previous = hits.setdefault(artifact_id, hit)
            if fusion is not None and (hit.channel_scores is None or name not in hit.channel_scores):
                raise InvalidSearchScore("Atomic search channel did not return its raw score")  # noqa: TRY003
            observations = dict(previous.channel_scores or {})
            observations.update(hit.channel_scores or {})
            if observations:
                hits[artifact_id] = replace(previous, channel_scores=observations)
                previous = hits[artifact_id]
            if previous.distance is None and hit.distance is not None:
                hits[artifact_id] = replace(previous, distance=hit.distance)
            ranks[artifact_id] = ranks.get(artifact_id, 0.0) + 1.0 / (60 + rank + 1)
    if fusion is not None:
        enabled = ("text",) if mode == "text" else ("vector",) if mode == "vector" else ("text", "vector")
        fused = fuse_rrf(
            (
                FusionChannel(
                    name,
                    fusion.weights.get(name, 1.0),
                    tuple(
                        FusionCandidate(hit.artifact_ref.artifact_id, rank)
                        for rank, hit in enumerate(channels.fts if name == "text" else channels.vector, 1)
                    ),
                )
                for name in enabled
            ),
            fusion,
            tie_break=lambda identity: identity,
        )
        return tuple(replace(hits[item.key], score=float(item.raw_score), retrieval_score=item.score) for item in fused)
    return tuple(
        replace(hits[identity], score=ranks[identity])
        for identity in sorted(hits, key=lambda identity: (-ranks[identity], identity))
    )


class RelationalAtomicMemoryIndex:
    """Shared same-row publication and eligibility completeness checks."""

    def __init__(self, table: Table, profile: EmbeddingProfile | None) -> None:
        if profile is not None and (profile.dimension < 1 or profile.distance != "l2"):
            raise AtomicMemoryIndexError("embedding-profile", "Atomic Memory requires a positive L2 embedding profile")
        self.table = table
        self.tables: tuple[Table, ...] = (table,)
        self.profile = profile
        self.capabilities = AtomicMemoryIndexCapabilities(
            vector=profile is not None, hybrid=profile is not None, embedding_profile=profile
        )

    def _encode_embedding(self, vector: tuple[float, ...]) -> object:
        return vector

    async def replace(self, connection: AsyncConnection, scope_id: str, projection: AtomicMemoryProjection, /) -> None:
        if projection.artifact_ref.family != "atomic-memory":
            raise AtomicMemoryIndexError("family", "Atomic Memory index only accepts atomic-memory artifacts")
        prepared = projection.prepared
        security = projection.security
        if not security.owner_type or not security.owner_id:
            raise AtomicMemoryIndexError("owner", "Current projection requires a formal artifact owner")
        embedding = None
        fingerprint = None
        input_hash = None
        if self.profile is not None:
            if (
                prepared.embedding_profile != self.profile
                or prepared.profile_fingerprint != atomic_memory_profile_fingerprint(self.profile)
                or prepared.embedding is None
            ):
                raise AtomicMemoryIndexError(
                    "embedding-profile", "Prepared projection does not cover the active profile"
                )
            input_hash = atomic_memory_embedding_input_hash(prepared.kind, prepared.text)
            if prepared.embedding_input_hash != input_hash:
                raise AtomicMemoryIndexError("embedding-input", "Prepared vector does not match the embedding input")
            embedding = self._encode_embedding(
                canonical_embedding(
                    prepared.embedding, dimension=self.profile.dimension, normalization=self.profile.normalization
                )
            )
            fingerprint = atomic_memory_profile_fingerprint(self.profile)
        await self.delete(connection, scope_id, projection.artifact_ref.artifact_id)
        await connection.execute(
            insert(self.table).values(
                scope_id=scope_id,
                artifact_id=projection.artifact_ref.artifact_id,
                revision=projection.artifact_ref.revision,
                state_version=projection.state_version,
                content_hash=prepared.content_hash,
                projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
                kind=prepared.kind,
                text=prepared.text,
                searchable_text=prepared.searchable_text,
                tag_keys=json.dumps(sorted(set(projection.tag_keys)), ensure_ascii=False),
                owner_type=security.owner_type,
                owner_id=security.owner_id,
                read_grants=json.dumps([grant.as_json() for grant in security.read_grants], ensure_ascii=False),
                embedding=embedding,
                profile_fingerprint=fingerprint,
                embedding_input_hash=input_hash,
            )
        )

    async def delete(self, connection: AsyncConnection, scope_id: str, artifact_id: str, /) -> None:
        await connection.execute(
            delete(self.table).where(self.table.c.scope_id == scope_id, self.table.c.artifact_id == artifact_id)
        )

    async def refresh_tags(
        self, connection: AsyncConnection, scope_id: str, artifact_id: str, tag_keys: tuple[str, ...], /
    ) -> None:
        await connection.execute(
            update(self.table)
            .where(self.table.c.scope_id == scope_id, self.table.c.artifact_id == artifact_id)
            .values(tag_keys=json.dumps(sorted(set(tag_keys)), ensure_ascii=False))
        )

    async def refresh_access(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        owner_type: str,
        owner_id: str,
        read_grants: tuple[AtomicMemoryReadGrant, ...],
        /,
    ) -> None:
        if not owner_type or not owner_id:
            raise AtomicMemoryIndexError("owner", "Current projection requires a formal artifact owner")
        await connection.execute(
            update(self.table)
            .where(self.table.c.scope_id == scope_id, self.table.c.artifact_id == artifact_id)
            .values(
                owner_type=owner_type,
                owner_id=owner_id,
                read_grants=json.dumps([grant.as_json() for grant in read_grants], ensure_ascii=False),
            )
        )

    async def probe_recoverable(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: AtomicMemorySearchRequest,
        floor: AdmissionFloor,
        /,
    ) -> bool:
        """Probe at most one eligible row admitted only by the permitted lower floor.

        Qualification precedes LIMIT. This is an existence signal, not an invented
        pre-admission collection count. Cross-channel admission excludes a row
        already admitted by either channel, even if the delivered pool was capped.
        The same current row carries its body, vectors and authorization fields.
        """

        request = freeze_atomic_memory_query_time(request)
        dialect = "sqlite" if connection.dialect.name == "sqlite" else "mysql"
        eligibility, parameters = atomic_memory_filter_sql(request.filters, dialect)
        parameters["scope_id"] = scope_id
        current: list[str] = []
        recoverable: list[str] = []
        if request.mode in {"fts", "hybrid"}:
            terms, required = fts_query_requirements(request.query, floor=request.admission)
            _, lower_required = fts_query_requirements(request.query, floor=floor)
            coverage: list[str] = []
            for index, term in enumerate(terms):
                key = f"probe_term_{index}"
                parameters[key] = f" {term} "
                matched = (
                    f"instr(' ' || searchable_text || ' ', :{key}) > 0"
                    if dialect == "sqlite"
                    else f"LOCATE(BINARY :{key}, BINARY CONCAT(' ', searchable_text, ' ')) > 0"
                )
                coverage.append(f"CASE WHEN {matched} THEN 1 ELSE 0 END")
            if coverage:
                lexical = atomic_memory_coverage_sql(coverage)
                parameters.update(probe_required=required, probe_lower_required=lower_required)
                current.append(f"(({lexical}) >= :probe_required)")
                recoverable.append(f"(({lexical}) >= :probe_lower_required)")
        if request.mode in {"vector", "hybrid"}:
            vector = self._require_vectors(request)
            profile = self.profile
            if profile is None:
                raise AtomicMemoryIndexError("embedding-profile", "Vector profile is unavailable")
            parameters.update(
                probe_vector=self._encode_embedding(vector),
                probe_similarity=(
                    DEFAULT_ADMISSION_FLOOR if request.admission is None else request.admission
                ).min_semantic_similarity,
                probe_lower_similarity=floor.min_semantic_similarity,
            )
            distance = "vec_distance_l2" if dialect == "sqlite" else "l2_distance"
            similarity = f"(1.0 - POWER({distance}(embedding, :probe_vector), 2) / 2.0)"
            current.append(f"({similarity} >= :probe_similarity)")
            recoverable.append(f"({similarity} >= :probe_lower_similarity)")
        if not recoverable:
            return False
        statement = text(
            "SELECT 1 FROM pc_atomic_memory_current WHERE scope_id = :scope_id "  # noqa: S608
            f"AND ({eligibility}) AND ({' OR '.join(recoverable)}) "
            f"AND NOT ({' OR '.join(current)}) LIMIT 1"
        )
        if request.mode in {"vector", "hybrid"} and dialect == "mysql":
            statement = statement.bindparams(bindparam("probe_vector", type_=self.table.c.embedding.type))
        return await connection.scalar(statement, parameters) is not None

    def _require_vectors(
        self,
        request: AtomicMemorySearchRequest | AtomicMemoryRelatedRequest,
    ) -> tuple[float, ...]:
        profile = self.profile
        if profile is None or request.embedding_profile != profile or request.query_vector is None:
            raise AtomicMemoryIndexError("embedding-profile", "Vector query requires the configured complete profile")
        if isinstance(request, AtomicMemorySearchRequest):
            if profile.normalization != "unit":
                raise AtomicMemoryIndexError(
                    "embedding-profile", "Cosine admission requires a unit-normalized L2 embedding profile"
                )
            floor = DEFAULT_ADMISSION_FLOOR if request.admission is None else request.admission
            if not math.isfinite(floor.min_semantic_similarity):
                raise AtomicMemoryIndexError("admission", "Semantic similarity admission must be finite")
        return canonical_embedding(
            request.query_vector, dimension=profile.dimension, normalization=profile.normalization
        )

    @staticmethod
    def validate_request(request: AtomicMemorySearchRequest | AtomicMemoryRelatedRequest) -> None:
        normalize_query(request.query)
        if request.mode not in {"fts", "vector", "hybrid"}:
            raise AtomicMemoryIndexError("mode", "Atomic Memory search mode must be fts, vector or hybrid")
        if isinstance(request, AtomicMemorySearchRequest):
            if isinstance(request.limit, bool) or request.limit < 1:
                raise AtomicMemoryIndexError("limit", "Search limit must be positive")
        elif request.mode in {"vector", "hybrid"} and (
            request.max_distance is None or not math.isfinite(request.max_distance) or request.max_distance < 0
        ):
            raise AtomicMemoryIndexError(
                "threshold", "Complete vector enumeration requires a finite nonnegative L2 threshold"
            )


__all__ = [
    "AtomicMemoryIndex",
    "AtomicMemoryIndexCapabilities",
    "AtomicMemoryIndexError",
    "AtomicMemoryIndexFilter",
    "AtomicMemoryIndexHit",
    "AtomicMemoryProjection",
    "AtomicMemoryProjectionPublisher",
    "AtomicMemoryProjectionSecurity",
    "AtomicMemoryReadGrant",
    "AtomicMemoryRelatedRequest",
    "AtomicMemorySearchChannels",
    "AtomicMemorySearchRequest",
    "PreparedAtomicMemoryProjection",
    "RelationalAtomicMemoryIndex",
    "atomic_memory_channel_hits",
    "atomic_memory_embedding_input",
    "atomic_memory_embedding_input_hash",
    "atomic_memory_filter_sql",
    "atomic_memory_profile_fingerprint",
    "combine_atomic_memory_channels",
]
