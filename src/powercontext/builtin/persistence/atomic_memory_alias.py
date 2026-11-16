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

"""Scope-local historical matching and batched authoritative successor resolution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import bindparam, func, or_, select, text

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.search import fts_query_requirements
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexHit,
    atomic_memory_bodies_from_rows,
    atomic_memory_body_select,
    atomic_memory_profile_fingerprint,
    oceanbase_fts_coverage_sql,
    sqlite_fts_coverage_sql,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import ATOMIC_MEMORY_ALIAS_FORMAT
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from powercontext.builtin.persistence.atomic_memory_index import (
        AtomicMemoryRelatedRequest,
        RelationalAtomicMemoryIndex,
    )

MAX_ALIAS_DEPTH = 64
_IDENTITY_BATCH = 900


async def _matching_aliases(
    index: RelationalAtomicMemoryIndex, connection: AsyncConnection, scope_id: str, request: AtomicMemoryRelatedRequest
) -> tuple[Any, ...]:
    alias = index.alias_table
    conditions = []
    parameters: dict[str, object] = {}
    if request.mode in {"fts", "hybrid"}:
        terms, required = fts_query_requirements(request.query, floor=request.admission)
        if terms:
            coverage, parameters = (
                sqlite_fts_coverage_sql(terms)
                if connection.dialect.name == "sqlite"
                else oceanbase_fts_coverage_sql(terms)
            )
            conditions.append(text(f"({coverage}) >= :alias_required"))
            parameters["alias_required"] = required
    if request.mode in {"vector", "hybrid"}:
        vector = index._require_vectors(request)
        profile = index.profile
        if profile is None:
            raise AtomicMemoryIndexError("embedding-profile", "Alias vector query requires a configured profile")
        distance = func.vec_distance_l2 if connection.dialect.name == "sqlite" else func.l2_distance
        valid = (
            (alias.c.embedding.is_not(None))
            & (alias.c.profile_fingerprint == atomic_memory_profile_fingerprint(profile))
            & alias.c.embedding_input_hash.is_not(None)
        )
        if connection.dialect.name == "sqlite":
            valid &= func.length(alias.c.embedding) == len(vector) * 4
        # SQL distance still performs an exact scan of compatible alias vectors.
        conditions.append(
            valid
            & (
                distance(alias.c.embedding, bindparam("alias_vector", type_=alias.c.embedding.type))
                <= request.max_distance
            )
        )
        parameters["alias_vector"] = index._encode_embedding(vector)
    if not conditions:
        return ()
    statement = (
        select(
            alias.c.alias_artifact_id, alias.c.alias_revision, alias.c.target_artifact_id, alias.c.merged_state_version
        )
        .where(alias.c.scope_id == scope_id, alias.c.projection_format == ATOMIC_MEMORY_ALIAS_FORMAT, or_(*conditions))
        .order_by(alias.c.alias_artifact_id)
    )
    return tuple((await connection.execute(statement, parameters)).mappings())


async def _successors(connection: AsyncConnection, scope_id: str, candidates: tuple[Any, ...]) -> tuple[str, ...]:
    """Read each shared suffix at most once; one batched read per frontier."""
    heads = ARTIFACT_HEADS_TABLE
    cache: dict[str, Any] = {}
    frontier = {str(row["alias_artifact_id"]) for row in candidates}
    for _ in range(MAX_ALIAS_DEPTH + 1):
        pending = sorted(frontier - cache.keys())
        if not pending:
            break
        for offset in range(0, len(pending), _IDENTITY_BATCH):
            rows = (
                await connection.execute(
                    select(heads).where(
                        heads.c.scope_id == scope_id,
                        heads.c.family == "atomic-memory",
                        heads.c.artifact_id.in_(pending[offset : offset + _IDENTITY_BATCH]),
                    )
                )
            ).mappings()
            cache.update((str(row["artifact_id"]), row) for row in rows)
        if any(identity not in cache for identity in pending):
            raise AtomicMemoryIndexError("alias-chain", "Alias chain has missing in-Scope authority")
        frontier = {
            str(cache[identity]["merged_into_id"])
            for identity in pending
            if cache[identity]["merged_into_id"] is not None
        }
    return tuple(
        dict.fromkeys(endpoint for candidate in candidates if (endpoint := _endpoint(cache, candidate)) is not None)
    )


def _endpoint(cache: dict[str, Any], candidate: Any) -> str | None:
    identity = str(candidate["alias_artifact_id"])
    root = cache[identity]
    if (
        root["lifecycle_state"] != "deprecated"
        or root["merged_into_id"] != candidate["target_artifact_id"]
        or root["revision"] != candidate["alias_revision"]
        or root["governance_generation"] != candidate["merged_state_version"]
    ):
        raise AtomicMemoryIndexError("alias-chain", "Alias differs from latest merged authority")
    visited = set()
    for depth in range(MAX_ALIAS_DEPTH + 1):
        if identity in visited:
            raise AtomicMemoryIndexError("alias-chain", "Alias chain contains a cycle")
        visited.add(identity)
        row = cache.get(identity)
        if row is None or row["replacement_artifact_id"] is not None:
            raise AtomicMemoryIndexError("alias-chain", "Alias chain has invalid authority")
        target = row["merged_into_id"]
        state = row["lifecycle_state"]
        if target is None:
            if state not in {"active", "deprecated", "retired"}:
                raise AtomicMemoryIndexError("alias-chain", "Alias chain has invalid lifecycle state")
            return identity if state == "active" else None
        if state != "deprecated" or depth == MAX_ALIAS_DEPTH:
            raise AtomicMemoryIndexError("alias-chain", "Alias chain is invalid or exceeds 64 merge edges")
        identity = str(target)
    return None


async def enumerate_aliased_related(
    index: RelationalAtomicMemoryIndex, connection: AsyncConnection, scope_id: str, request: AtomicMemoryRelatedRequest
) -> tuple[tuple[AtomicMemoryIndexHit, ...], dict[str, dict[str, object]]]:
    candidates = await _matching_aliases(index, connection, scope_id, request)
    endpoints = await _successors(connection, scope_id, candidates)
    current = index.table
    hits = []
    heads: dict[str, dict[str, object]] = {}
    for offset in range(0, len(endpoints), _IDENTITY_BATCH):
        ids = endpoints[offset : offset + _IDENTITY_BATCH]
        rows = (
            (
                await connection.execute(
                    select(current).where(current.c.scope_id == scope_id, current.c.artifact_id.in_(ids))
                )
            )
            .mappings()
            .all()
        )
        body_rows = (
            await connection.execute(
                text(atomic_memory_body_select()).bindparams(bindparam("body_ids", expanding=True)),
                {"scope_id": scope_id, "body_ids": ids},
            )
        ).mappings()
        _, joined = atomic_memory_bodies_from_rows(body_rows)
        by_id = {str(row["artifact_id"]): row for row in rows}
        for identity in ids:
            row = by_id.get(identity)
            if row is None:
                continue
            head = joined.get(identity)
            if (
                head is None
                or head["lifecycle_state"] != "active"
                or head["merged_into_id"] is not None
                or head["revision"] != row["revision"]
                or head["governance_generation"] != row["state_version"]
            ):
                raise AtomicMemoryIndexError("stale-recall", "Alias successor projection differs from authority")
            filters = request.filters
            if filters.kind is not None and row["kind"] != filters.kind:
                continue
            if filters.tag_filter is not None:
                keys = set(json.loads(row["tag_keys"]))
                required = set(filters.tag_filter.keys)
                if not (required <= keys if filters.tag_filter.match == "all" else required & keys):
                    continue
            hits.append(
                AtomicMemoryIndexHit(
                    artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=identity, revision=row["revision"]),
                    state_version=row["state_version"],
                    kind=row["kind"],
                    text=row["text"],
                    score=0.0,
                )
            )
            heads[identity] = head
    return tuple(hits), heads
