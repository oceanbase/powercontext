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

"""Declaration-driven text/vector/hybrid search returning complete exact Artifacts."""

from __future__ import annotations

import math
import sqlite3
import struct
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, field_validator
from sqlalchemy import bindparam, select, text
from sqlalchemy.exc import SQLAlchemyError

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.artifacts.fusion import (
    FusionCandidate,
    FusionChannel,
    FusionSelection,
    RrfParameters,
    fuse_rrf,
    parse_fusion_parameters,
)
from powercontext.artifacts.indexes import VectorIndex
from powercontext.artifacts.search import (
    ArtifactSearchExecutionContext,
    ArtifactSearchFamilyNotFound,
    ArtifactSearchMatch,
    ArtifactSearchQuery,
    ArtifactSearchUnavailable,
    ArtifactSearchUnsupported,
    ChannelScore,
)
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.artifacts.search import analyze_fts_query, fts_match_query, lexical_search_score
from powercontext.builtin.inference.protocols import embed_query
from powercontext.builtin.persistence.artifact_indexes import (
    DEFINITIONS,
    SCHEMAS,
    STATES,
    entries,
    key,
    parse_index,
    projection_table,
    require_current_text_analyzer,
    search_declaration,
    vector_bytes,
)
from powercontext.builtin.persistence.custom_artifacts import custom_artifact_repository
from powercontext.builtin.persistence.sqlite.profile import ensure_sqlite_vector_extension
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACT_PROCESSING_BINDING_STATES_TABLE
from powercontext.server.authz import AccessAction, BuiltinAuthorizationProvider, ResourceRef


class ArtifactQueryVector(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    profile: EmbeddingProfile
    vector: tuple[float, ...] = Field(min_length=1)

    @field_validator("profile", mode="before")
    @classmethod
    def existing_profile_contract(cls, value):
        if isinstance(value, dict) and set(value) - set(EmbeddingProfile.model_fields):
            raise ValueError("query vector profile contains unsupported fields")  # noqa: TRY003
        return value


class CustomArtifactSearchRequest(ArtifactSearchQuery):
    query: str | None = Field(default=None, min_length=1, max_length=8192)
    mode: Literal["text", "vector", "hybrid"] | None = None
    query_vector: ArtifactQueryVector | None = None
    indexes: dict[Literal["text", "vector"], tuple[str, ...]] | None = None
    min_score: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    fusion: FusionSelection | None = None


@dataclass(frozen=True)
class CustomArtifactSearchOutcome:
    matches: tuple[ArtifactSearchMatch, ...]
    artifacts: tuple[Any, ...]


class CustomArtifactSearcher:
    request_type = CustomArtifactSearchRequest

    def __init__(self, database, family, embedding_model=None):
        self.database, self.family, self.embedding_model = database, family, embedding_model
        self.artifact_type = type(
            "CustomSearchArtifact", (Artifact[RootModel[dict[str, JsonValue]]],), {"family": family}
        )

    async def _configuration(self, connection, scope, request, *, config=None):  # noqa: C901 - Validate one target or empty-scope snapshot.
        durable = (
            (
                await connection.execute(
                    select(SCHEMAS.c.declaration)
                    .where(SCHEMAS.c.family == self.family)
                    .order_by(SCHEMAS.c.schema_version)
                )
            )
            .scalars()
            .all()
        )
        if not durable:
            raise ArtifactSearchFamilyNotFound(self.family)
        declarations = [declaration for row in durable if (declaration := search_declaration(row)) is not None]
        if not declarations:
            raise ArtifactSearchUnsupported(self.family, field="family")
        state = (
            (await connection.execute(select(STATES).where(key(STATES, scope, self.family)))).mappings().one_or_none()
        )
        manifest = {} if state is None else state["manifest"]
        if config is None and not manifest.get("config"):
            binding = ARTIFACT_PROCESSING_BINDING_STATES_TABLE
            definition = await connection.scalar(
                select(binding.c.plugin_spec).where(binding.c.plugin_family == self.family)
            )
            default = None if definition is None else search_declaration(definition["declaration"])
            config = default or declarations[0]
        elif config is None:
            # The manifest owns build identity/progress; query policy comes from the
            # durable declaration in the same read snapshot, including during repair.
            config = next(
                (search_declaration(row) for row in durable if row["schema_version"] == manifest["default_schema"]),
                None,
            )
            if config is None:
                raise ArtifactSearchUnavailable(self.family, "index declaration unavailable")
        mode = request.mode or config.default_mode
        self._validate_input(request, mode)
        if mode not in config.modes:
            raise ArtifactSearchUnsupported(self.family, field="mode")
        try:
            chosen = config.select_indexes(mode, request.indexes)
        except ValueError as error:
            raise ArtifactSearchUnsupported(self.family, field="indexes") from error
        profiles = [index.profile for index in chosen if isinstance(index, VectorIndex)]
        if profiles and any(profile != profiles[0] for profile in profiles):
            raise ArtifactSearchUnsupported(self.family, field="indexes")
        profile = profiles[0] if profiles else None
        if request.query_vector is not None:
            if request.query_vector.profile != profile:
                raise ArtifactSearchUnsupported(self.family, field="query_vector.profile")
            try:
                vector_bytes(request.query_vector.vector, profile)
            except ValueError as error:
                raise ArtifactSearchUnsupported(self.family, field="query_vector.vector") from error
        if request.fusion is not None:
            try:
                params = parse_fusion_parameters(request.fusion)
            except ValueError as error:
                raise ArtifactSearchUnsupported(self.family, field="fusion") from error
            weights = {"text": config.text_weight, "vector": config.vector_weight}
            if set(params.weights) - set(weights):
                raise ArtifactSearchUnsupported(self.family, field="fusion.weights")
            weights.update(params.weights)
            if sum(weights.values()) <= 0:
                raise ArtifactSearchUnsupported(self.family, field="fusion.weights")
        if state is None:
            has_content = await connection.scalar(
                select(ARTIFACT_HEADS_TABLE.c.artifact_id)
                .where(ARTIFACT_HEADS_TABLE.c.scope_id == scope, ARTIFACT_HEADS_TABLE.c.family == self.family)
                .limit(1)
            )
            if mode != "text" or has_content is not None:
                raise ArtifactSearchUnavailable(self.family, "index has not been built")
            return None, config, mode, (), None
        if state["state"] != "ready":
            raise ArtifactSearchUnavailable(self.family, "index rebuild is incomplete")
        selected = await self._selected_indexes(connection, state, chosen)
        if any(isinstance(index, VectorIndex) and index.profile != profile for _, _, index in selected):
            raise ArtifactSearchUnsupported(self.family, field="indexes")
        return state, config, mode, selected, profile

    async def _selected_indexes(self, connection, state, chosen):
        kinds = {index.name: index.kind for index in chosen}
        selected = []
        for _, entry in entries(state["manifest"]):
            definition = dict(
                (
                    await connection.execute(
                        select(DEFINITIONS).where(DEFINITIONS.c.index_def_id == entry["index_def_id"])
                    )
                )
                .mappings()
                .one()
            )
            if definition["index_name"] not in kinds:
                continue
            require_current_text_analyzer(definition)
            await projection_table(connection, definition)
            index = parse_index(definition)
            if index.kind != kinds[index.name]:
                raise ArtifactSearchUnsupported(self.family, field="indexes")
            selected.append((state, definition, index))
        if not set(kinds).issubset({definition["index_name"] for _, definition, _ in selected}):
            raise ArtifactSearchUnavailable(self.family, "configuration lacks a selected index")
        return selected

    async def search(self, scope_id, request, /, *, execution_context=None):
        try:
            return await self._search(scope_id, request, execution_context=execution_context)
        except SQLAlchemyError as error:
            raise ArtifactSearchUnavailable(self.family, "search storage is unavailable") from error

    async def _search(self, scope_id, request, /, *, execution_context=None):
        execution = execution_context or ArtifactSearchExecutionContext()
        access = execution.access
        # Pin query policy across encoding outside the transaction; recheck build and access below.
        async with AsyncExitStack() as resources:
            if access is not None:
                await resources.enter_async_context(access.defer_decision_audit())
            async with self.database.transaction(consistent_snapshot=True) as connection:
                if access is not None:
                    await access.for_read(connection).require(
                        execution.principal,
                        AccessAction.SCOPE_READ,
                        ResourceRef.scope(scope_id),
                        context=execution.audit,
                    )
                state, config, mode, selected, profile = await self._configuration(connection, scope_id, request)
                if state is None:
                    return CustomArtifactSearchOutcome((), ())
            query_vector = await self._encode_query(request, profile)
            async with self.database.transaction(consistent_snapshot=True) as connection:
                current, config, mode, selected, checked_profile = await self._configuration(
                    connection, scope_id, request, config=config
                )
                if current["build_id"] != state["build_id"] or checked_profile != profile:
                    raise ArtifactSearchUnavailable(self.family, "search build changed during query preparation; retry")
                bound = None if access is None else access.for_read(connection)
                if bound is not None:
                    await bound.require(
                        execution.principal,
                        AccessAction.SCOPE_READ,
                        ResourceRef.scope(scope_id),
                        context=execution.audit,
                    )
                channels = await self._candidates(
                    connection, scope_id, request, mode, selected, query_vector, profile, bound, execution
                )
                native_text = connection.dialect.name != "sqlite"
                text_metric = "oceanbase_match" if native_text else "sqlite_bm25"
                scored = self._rank(channels, config, mode, request, native_text, text_metric)
                return await self._outcome(connection, scope_id, request, channels, scored, native_text, text_metric)

    async def _encode_query(self, request, profile):
        query_vector = None
        if profile is not None:
            if request.query_vector is not None:
                try:
                    query_vector = vector_bytes(request.query_vector.vector, profile)
                except ValueError as error:
                    raise ArtifactSearchUnsupported(self.family, field="query_vector.vector") from error
            else:
                model = self.embedding_model
                if model is None or model.profile != profile:
                    raise ArtifactSearchUnavailable(self.family, "query encoder unavailable for declared profile")
                try:
                    result = await embed_query(model, (request.query,))
                    query_vector = vector_bytes(result.vectors[0], profile)
                except Exception as error:
                    raise ArtifactSearchUnavailable(self.family, "query encoder failed for declared profile") from error
                if len(result.vectors) != 1:
                    raise ArtifactSearchUnavailable(self.family, "query encoder returned an invalid vector count")
        return query_vector

    async def _candidates(self, connection, scope_id, request, mode, selected, query_vector, profile, bound, execution):
        visible = {}
        channels = {name: {} for name in ({"text", "vector"} if mode == "hybrid" else {mode})}
        for state, definition, index in selected:
            if index.kind == "text":
                rows = await self._text(
                    connection, scope_id, state["build_id"], definition["index_def_id"], request.query
                )
            else:
                rows = await self._vector(connection, scope_id, definition, state["build_id"], query_vector, profile)
            for identity, revision, raw in rows:
                if identity not in visible:
                    visible[identity] = (
                        bound is None
                        # The same transaction already authorized this Scope's reads.
                        or type(bound.provider) is BuiltinAuthorizationProvider
                        or (
                            await bound.check(
                                execution.principal,
                                AccessAction.ARTIFACT_READ,
                                ResourceRef.artifact(scope_id, family=self.family, artifact_id=identity),
                                context=execution.audit,
                            )
                        ).allowed
                    )
                if not visible[identity]:
                    continue
                if not math.isfinite(raw):
                    raise ArtifactSearchUnavailable(self.family, "index returned a nonfinite score")
                address = (identity, revision)
                previous = channels[index.kind].get(address)
                if previous is None or raw < previous:
                    channels[index.kind][address] = raw
        return channels

    def _rank(self, channels, config, mode, request, native_text, text_metric):
        budget = max(request.limit, config.candidate_limit)
        ordered = {
            name: sorted(values, key=lambda address: (values[address], address))[:budget]
            for name, values in channels.items()
        }
        if mode == "hybrid":
            params = RrfParameters(rank_constant=config.rrf_k)
            weights = {"text": config.text_weight, "vector": config.vector_weight}
            if request.fusion is not None:
                try:
                    params = parse_fusion_parameters(request.fusion)
                except ValueError as error:
                    raise ArtifactSearchUnsupported(self.family, field="fusion") from error
                if set(params.weights) - set(weights):
                    raise ArtifactSearchUnsupported(self.family, field="fusion.weights")
                weights.update(params.weights)
            if sum(weights.values()) <= 0:
                raise ArtifactSearchUnsupported(self.family, field="fusion.weights")
            fused = fuse_rrf(
                tuple(
                    FusionChannel(
                        name,
                        weights[name],
                        tuple(FusionCandidate(identity, rank) for rank, identity in enumerate(identities, 1)),
                    )
                    for name, identities in ordered.items()
                ),
                params,
                tie_break=lambda identity: identity,
            )
            scored = [(hit.key, hit.score) for hit in fused]
        elif mode == "text":
            scored = [
                (identity, lexical_search_score((-1 if native_text else 1) * channels[mode][identity], text_metric)[0])
                for identity in ordered[mode]
            ]
        else:
            scored = [(identity, 1 / (1 + channels[mode][identity])) for identity in ordered[mode]]
        return scored

    async def _outcome(self, connection, scope_id, request, channels, scored, native_text, text_metric):
        repository = custom_artifact_repository(self.family)
        matches = []
        final = [(identity, score) for identity, score in scored if score >= request.min_score][: request.limit]
        artifacts = await repository.get_many(
            connection,
            scope_id,
            tuple(
                ArtifactRef(family=self.family, artifact_id=identity, revision=revision)
                for (identity, revision), score in final
            ),
        )
        for ((identity, revision), score), artifact in zip(final, artifacts, strict=True):
            raw_scores = {
                name: ChannelScore(
                    (-1 if native_text and name == "text" else 1) * values[(identity, revision)],
                    text_metric if name == "text" else "l2_distance",
                    native_text and name == "text",
                )
                for name, values in channels.items()
                if (identity, revision) in values
            }
            matches.append(
                ArtifactSearchMatch(artifact.as_ref(), score, raw_scores if request.include_scores else None)
            )
        public_artifacts = tuple(
            self.artifact_type(
                artifact_id=artifact.artifact_id,
                revision=artifact.revision,
                content=artifact.content.content,
                lineage=artifact.lineage,
            )
            for artifact in artifacts
        )
        return CustomArtifactSearchOutcome(tuple(matches), public_artifacts)

    def _validate_input(self, request, mode):
        if mode in {"text", "hybrid"} and request.query is None:
            raise ArtifactSearchUnsupported(self.family, field="query")
        if mode == "text" and request.query_vector is not None:
            raise ArtifactSearchUnsupported(self.family, field="query_vector")
        if mode == "vector" and ((request.query is None) == (request.query_vector is None)):
            raise ArtifactSearchUnsupported(self.family, field="query_vector")
        if mode != "hybrid" and request.fusion is not None:
            raise ArtifactSearchUnsupported(self.family, field="fusion")

    async def _text(self, connection, scope, build_id, index_def_id, query):
        expression = fts_match_query(query)
        if expression is None:
            return []
        if connection.dialect.name != "sqlite":
            result = await connection.execute(
                text("""SELECT artifact_id, revision,
                MATCH(searchable_text) AGAINST(:query) AS score FROM pc_artifact_search_text
                WHERE scope_id=:scope AND family=:family AND build_id=:build_id AND index_def_id=:index_def_id
                AND MATCH(searchable_text) AGAINST(:query) > 0 ORDER BY score DESC, artifact_id"""),
                {
                    "query": analyze_fts_query(query),
                    "scope": scope,
                    "family": self.family,
                    "build_id": build_id,
                    "index_def_id": index_def_id,
                },
            )
            return [(row.artifact_id, int(row.revision), -float(row.score)) for row in result]
        result = await connection.execute(
            text("""SELECT artifact_id, revision, bm25(pc_artifact_search_fts) AS score
            FROM pc_artifact_search_fts WHERE pc_artifact_search_fts MATCH :query
            AND scope_id=:scope AND family=:family AND build_id=:build_id AND index_def_id=:index_def_id
            ORDER BY score, artifact_id"""),
            {
                "query": expression,
                "scope": scope,
                "family": self.family,
                "build_id": build_id,
                "index_def_id": index_def_id,
            },
        )
        return [(row.artifact_id, int(row.revision), float(row.score)) for row in result]

    async def _vector(self, connection, scope, definition, build_id, vector, profile):
        if connection.dialect.name == "sqlite":
            try:
                await ensure_sqlite_vector_extension(connection)
            except (AttributeError, sqlite3.Error) as error:
                raise ArtifactSearchUnavailable(self.family, "SQLite vector extension is unavailable") from error
        table = await projection_table(connection, definition)
        function = "vec_distance_l2" if connection.dialect.name == "sqlite" else "l2_distance"
        # Table identity comes only from a validated server-owned space, never the request.
        statement = text(f"""SELECT artifact_id, revision, MIN({function}(embedding, :vector)) AS distance
            FROM {table.name} WHERE scope_id=:scope AND family=:family AND build_id=:build_id AND space_id=:space AND index_def_id=:index_def_id
            GROUP BY artifact_id, revision ORDER BY distance, artifact_id""")  # noqa: S608 - Function and table names come only from validated server-owned metadata.
        if connection.dialect.name != "sqlite":
            vector = list(struct.unpack(f"<{profile.dimension}f", vector))
            statement = statement.bindparams(bindparam("vector", type_=table.c.embedding.type))
        result = await connection.execute(
            statement,
            {
                "vector": vector,
                "scope": scope,
                "family": self.family,
                "build_id": build_id,
                "space": definition["space_id"],
                "index_def_id": definition["index_def_id"],
            },
        )
        return [(row.artifact_id, int(row.revision), float(row.distance)) for row in result]
