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

"""OceanBase staged vectors and recoverable multi-table Topic activation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pyobvector import VECTOR, VectorIndex
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    delete,
    insert,
    inspect,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import ColumnElement

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryCapabilityError,
    TopicMemoryStorageInvariantError,
)
from powercontext.builtin.persistence.oceanbase.topic_memory_index import (
    _TOPIC_VECTOR_INDEX,
    OceanBaseTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE, identity_string
from powercontext.builtin.persistence.topic_memory_index import (
    topic_memory_embedding_profile_fingerprint,
    validate_current_topic_vectors,
)
from powercontext.builtin.persistence.topic_memory_reindex import (
    TOPIC_MEMORY_REINDEX_CHUNK_TABLE,
    TOPIC_MEMORY_REINDEX_PREVIOUS_PREFIX,
    TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
    TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
    TOPIC_MEMORY_REINDEX_TOPIC_TABLE,
    TopicMemoryReindexSource,
    staged_fingerprint,
    validate_staging_vectors,
)
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

_STORAGE_PROFILE_TABLE = "pc_server_storage_profile"
_PREVIOUS_TOPICS = TOPIC_MEMORY_REINDEX_PREVIOUS_PREFIX + "topics"
_PREVIOUS_CHUNKS = TOPIC_MEMORY_REINDEX_PREVIOUS_PREFIX + "chunks"
_PREVIOUS_SHAPE = TOPIC_MEMORY_REINDEX_PREVIOUS_PREFIX + "shape"
_PREVIOUS_STORAGE_PROFILE = TOPIC_MEMORY_REINDEX_PREVIOUS_PREFIX + "storage_profile"
_PREVIOUS_TABLES = (
    _PREVIOUS_CHUNKS,
    _PREVIOUS_TOPICS,
    _PREVIOUS_SHAPE,
    _PREVIOUS_STORAGE_PROFILE,
)
_VECTOR_TYPE_SQL = text(
    "SELECT data_type FROM information_schema.columns "
    "WHERE table_schema=DATABASE() AND table_name=:table_name AND column_name='embedding'"
)

# MySQL-family CHECK names are schema-wide. Staging uses fixed temporary names;
# cleanup releases the original names on retired tables before restoring them.
_SHAPE_CHECKS = (
    (
        "ck_pc_topic_memory_reindex_shape_singleton",
        "ck_pc_topic_memory_retrieval_shape_singleton",
        "singleton = 1",
    ),
    (
        "ck_pc_topic_memory_reindex_shape_value",
        "ck_pc_topic_memory_retrieval_shape_value",
        "shape IN ('fts', 'hybrid')",
    ),
    (
        "ck_pc_topic_memory_reindex_shape_profile",
        "ck_pc_topic_memory_retrieval_shape_profile",
        "(shape = 'fts' AND profile_fingerprint IS NULL) OR (shape = 'hybrid' AND profile_fingerprint IS NOT NULL)",
    ),
)
_STORAGE_CHECKS = (
    (
        "ck_pc_topic_memory_reindex_storage_profile_singleton",
        "ck_pc_server_storage_profile_singleton",
        "singleton = 1",
    ),
)


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


async def _table_names(connection: AsyncConnection) -> set[str]:
    return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))


async def _create_table(connection: AsyncConnection, table: Table) -> None:
    await connection.run_sync(lambda sync: table.create(sync, checkfirst=True))


class OceanBaseTopicMemoryReindexBackend:
    """Stage native VECTOR rows, then swap only derived tables and declarations."""

    staging_progress_tables = frozenset({
        TOPIC_MEMORY_REINDEX_TOPIC_TABLE,
        TOPIC_MEMORY_REINDEX_CHUNK_TABLE,
        TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
    })

    def __init__(self, profile: EmbeddingProfile) -> None:
        self._active = OceanBaseTopicMemoryVectorIndex(profile)
        self.profile = profile
        self.fingerprint = topic_memory_embedding_profile_fingerprint(profile)
        metadata = MetaData()
        self.topic_table = self._vector_table(
            metadata,
            TOPIC_MEMORY_REINDEX_TOPIC_TABLE,
            chunk=False,
            constraint="uq_pc_topic_memory_vector_topics_active",
        )
        self.chunk_table = self._vector_table(
            metadata,
            TOPIC_MEMORY_REINDEX_CHUNK_TABLE,
            chunk=True,
            constraint="uq_pc_topic_memory_vector_chunks_active",
        )
        self.shape_table = Table(
            TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
            metadata,
            Column("singleton", Integer, primary_key=True, autoincrement=False),
            Column("shape", identity_string(16), nullable=False),
            Column("profile_fingerprint", identity_string(64)),
            *(CheckConstraint(expression, name=temporary) for temporary, _, expression in _SHAPE_CHECKS),
        )
        self.storage_profile_table = Table(
            TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
            metadata,
            Column("singleton", BigInteger, primary_key=True, autoincrement=False),
            Column("embedding_profile", MEDIUMTEXT, nullable=False),
            *(CheckConstraint(expression, name=temporary) for temporary, _, expression in _STORAGE_CHECKS),
        )
        self._topic_index = VectorIndex(
            _TOPIC_VECTOR_INDEX,
            self.topic_table.c.embedding,
            params="distance=l2,type=hnsw",
        )

    def _vector_table(self, metadata: MetaData, name: str, *, chunk: bool, constraint: str) -> Table:
        columns: list[Any] = [
            Column("vector_id", BigInteger, primary_key=True, autoincrement=True),
            Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
            Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), nullable=False),
            Column("revision", BigInteger, nullable=False),
        ]
        if chunk:
            columns.append(Column("chunk_ordinal", BigInteger, nullable=False))
        columns.extend([
            Column("profile_fingerprint", String(64), nullable=False),
            Column("embedding", VECTOR(self.profile.dimension), nullable=False),
        ])
        identity = ("scope_id", "artifact_id", "chunk_ordinal") if chunk else ("scope_id", "artifact_id")
        return Table(name, metadata, *columns, UniqueConstraint(*identity, name=constraint))

    @staticmethod
    def topic_vector_present(vectors: Any, /) -> ColumnElement[bool]:
        return vectors.c.embedding.is_not(None)

    @staticmethod
    def chunk_vector_present(vectors: Any, /) -> ColumnElement[bool]:
        return vectors.c.embedding.is_not(None)

    async def validate_active(self, connection: AsyncConnection, fingerprint: str | None, /) -> None:
        if fingerprint is None:
            return
        await validate_current_topic_vectors(
            connection,
            self._active.topic_table,
            self._active.chunk_table,
            fingerprint,
        )
        if fingerprint == self.fingerprint:
            await self._require_vector_types(connection, self._active.tables)

    async def validate_staging(self, connection: AsyncConnection, /) -> None:
        if await staged_fingerprint(connection, self) != self.fingerprint:
            raise TopicMemoryStorageInvariantError("reindex-staging-shape", 1)
        await validate_staging_vectors(connection, self)
        await self._require_vector_types(connection, (self.topic_table, self.chunk_table))
        if self.storage_profile_table.name in await _table_names(connection):
            await self._require_storage_profile(connection)

    async def prepare(self, connection: AsyncConnection, *, update_storage_profile: bool) -> None:
        names = await _table_names(connection)
        if names & set(_PREVIOUS_TABLES):
            raise TopicMemoryStorageInvariantError("reindex-activation-state", sorted(names & set(_PREVIOUS_TABLES)))
        staging = {self.topic_table.name, self.chunk_table.name, self.storage_profile_table.name}
        derived = staging | {self.shape_table.name}
        shape_row = None
        if self.shape_table.name in names:
            shape_row = await self._read_shape_declaration(connection, self.shape_table)
            self._require_target_shape(shape_row)
        if shape_row is None and names & derived:
            # Without a declaration row, existing rows cannot be bound to this
            # target. Repeated IF EXISTS drops recover interrupted derived DDL.
            await connection.exec_driver_sql(
                "DROP TABLE IF EXISTS " + ", ".join(_quote(name) for name in sorted(derived))
            )
            names.difference_update(derived)

        # Establish intent before the vector DDL. Subsequent calls can safely
        # fill in missing tables after any partial matching preparation.
        await _create_table(connection, self.shape_table)
        if shape_row is None:
            await connection.execute(
                mysql_insert(self.shape_table)
                .values(singleton=1, shape="hybrid", profile_fingerprint=self.fingerprint)
                .prefix_with("IGNORE")
            )
        await _create_table(connection, self.topic_table)
        await _create_table(connection, self.chunk_table)
        await connection.run_sync(lambda sync: self._topic_index.create(sync, checkfirst=True))
        if update_storage_profile:
            await _create_table(connection, self.storage_profile_table)
            await connection.execute(
                mysql_insert(self.storage_profile_table)
                .values(singleton=1, embedding_profile=self.profile.model_dump_json())
                .prefix_with("IGNORE")
            )

        # INSERT IGNORE handles matching concurrent preparations without
        # overwriting a conflicting declaration; read both declarations back.
        self._require_target_shape(await self._read_shape_declaration(connection, self.shape_table))
        current_names = names | {self.shape_table.name, self.topic_table.name, self.chunk_table.name}
        if update_storage_profile:
            current_names.add(self.storage_profile_table.name)
        if self.storage_profile_table.name in current_names:
            await self._require_storage_profile(connection)

    async def write_staging(
        self,
        connection: AsyncConnection,
        sources: Sequence[TopicMemoryReindexSource],
        vectors: Sequence[tuple[float, ...]],
        /,
    ) -> None:
        expected = sum(len(source.embedding_inputs) for source in sources)
        if len(vectors) != expected:
            raise TopicMemoryCapabilityError(
                "embedding-result", f"provider returned {len(vectors)} vectors for {expected} Topic Memory inputs"
            )
        try:
            canonical = tuple(
                canonical_embedding(
                    vector,
                    dimension=self.profile.dimension,
                    normalization=self.profile.normalization,
                )
                for vector in vectors
            )
        except ValueError as error:
            raise TopicMemoryCapabilityError("embedding-values", str(error)) from error
        if not sources:
            return

        # Canonicalize the complete batch before replacing any staged rows.
        topics: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        offset = 0
        for source in sources:
            values = {
                "scope_id": source.scope_id,
                "artifact_id": source.artifact_id,
                "revision": source.revision,
                "profile_fingerprint": self.fingerprint,
            }
            topics.append({**values, "embedding": canonical[offset]})
            offset += 1
            chunks.extend(
                {
                    **values,
                    "chunk_ordinal": chunk.ordinal,
                    "embedding": canonical[offset + index],
                }
                for index, chunk in enumerate(source.chunks)
            )
            offset += len(source.chunks)

        keys = tuple(dict.fromkeys(source.key for source in sources))
        for table in (self.chunk_table, self.topic_table):
            await connection.execute(delete(table).where(tuple_(table.c.scope_id, table.c.artifact_id).in_(keys)))
        await connection.execute(insert(self.topic_table), topics)
        if chunks:
            await connection.execute(insert(self.chunk_table), chunks)

    async def activate(self, connection: AsyncConnection, *, update_storage_profile: bool) -> None:
        names = await _table_names(connection)
        active_vectors = {self._active.topic_table.name, self._active.chunk_table.name}
        if not active_vectors <= names:
            active_shape = await self._read_shape_declaration(connection, TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE)
            if active_shape != ("fts", None):
                raise TopicMemoryStorageInvariantError("reindex-activation-state", 1)
            # An FTS-only source has no old vector generation to preserve. Its
            # empty metadata tables complete the rename set; authoritative and
            # full-text tables remain untouched.
            for active in (self._active.topic_table, self._active.chunk_table):
                await _create_table(connection, active)
            names = await _table_names(connection)
        if self._is_post_rename(names):
            self._require_post_storage_state(names)
            return
        if not self._is_pre_rename(names):
            raise TopicMemoryStorageInvariantError("reindex-activation-state", 1)
        include_storage = self.storage_profile_table.name in names
        if _PREVIOUS_STORAGE_PROFILE in names or (include_storage and _STORAGE_PROFILE_TABLE not in names):
            raise TopicMemoryStorageInvariantError("reindex-activation-state", 1)

        await self.validate_staging(connection)
        pairs = [
            (self._active.topic_table.name, _PREVIOUS_TOPICS, self.topic_table.name),
            (self._active.chunk_table.name, _PREVIOUS_CHUNKS, self.chunk_table.name),
            (TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name, _PREVIOUS_SHAPE, self.shape_table.name),
        ]
        if include_storage:
            pairs.append((_STORAGE_PROFILE_TABLE, _PREVIOUS_STORAGE_PROFILE, self.storage_profile_table.name))
        assignments = []
        for active, previous, staging in pairs:
            assignments.extend((f"{_quote(active)} TO {_quote(previous)}", f"{_quote(staging)} TO {_quote(active)}"))

        # Preparation controls creation; physical presence controls activation.
        # Otherwise a retry with a different flag could strand a staged profile.
        _ = update_storage_profile
        # One OceanBase/MySQL DDL statement atomically swaps every derived table.
        # Authoritative Artifacts, active Topics and active chunks are not named.
        await connection.exec_driver_sql("RENAME TABLE " + ", ".join(assignments))

    async def cleanup(self, connection: AsyncConnection) -> None:
        names = await _table_names(connection)
        if TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name not in names:
            raise TopicMemoryStorageInvariantError("reindex-active-shape", 0)
        active_shape = await self._read_shape_declaration(connection, TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE)
        if active_shape != ("hybrid", self.fingerprint):
            raise TopicMemoryStorageInvariantError("reindex-active-shape", 1)

        retired = [name for name in _PREVIOUS_TABLES if name in names]
        # Release schema-wide original CHECK names on the retired generation
        # first, but keep those tables as a durable cleanup marker until every
        # active constraint has been restored. Any interruption is resumable.
        for active, previous in (
            (TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name, _PREVIOUS_SHAPE),
            (_STORAGE_PROFILE_TABLE, _PREVIOUS_STORAGE_PROFILE),
        ):
            if previous not in retired:
                continue
            constraints = await connection.run_sync(
                lambda sync, table_name=previous: inspect(sync).get_check_constraints(table_name)
            )
            previous_names = {str(item["name"]) for item in constraints if item.get("name") is not None}
            expected = _SHAPE_CHECKS if active == TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name else _STORAGE_CHECKS
            for _temporary, original, _expression in expected:
                if original in previous_names:
                    await connection.exec_driver_sql(f"ALTER TABLE {_quote(previous)} DROP CHECK {_quote(original)}")

        # Each decision is based on current physical names. If a prior attempt
        # stopped after DROP CHECK but before ADD CONSTRAINT, the missing pair
        # is rebuilt here without requiring the temporary name to still exist.
        await self._restore_checks(connection, TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name, _SHAPE_CHECKS)
        if _STORAGE_PROFILE_TABLE in names:
            await self._restore_checks(connection, _STORAGE_PROFILE_TABLE, _STORAGE_CHECKS)
        if retired:
            await connection.exec_driver_sql("DROP TABLE " + ", ".join(_quote(name) for name in retired))

    def _is_pre_rename(self, names: set[str]) -> bool:
        active = {self._active.topic_table.name, self._active.chunk_table.name, TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name}
        staging = {self.topic_table.name, self.chunk_table.name, self.shape_table.name}
        previous = {_PREVIOUS_TOPICS, _PREVIOUS_CHUNKS, _PREVIOUS_SHAPE}
        return active | staging <= names and not names & previous

    def _is_post_rename(self, names: set[str]) -> bool:
        active = {self._active.topic_table.name, self._active.chunk_table.name, TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name}
        staging = {self.topic_table.name, self.chunk_table.name, self.shape_table.name}
        previous = {_PREVIOUS_TOPICS, _PREVIOUS_CHUNKS, _PREVIOUS_SHAPE}
        return active | previous <= names and not names & staging

    @staticmethod
    def _require_post_storage_state(names: set[str]) -> None:
        if TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE in names:
            raise TopicMemoryStorageInvariantError("reindex-activation-state", 1)
        if _PREVIOUS_STORAGE_PROFILE in names and _STORAGE_PROFILE_TABLE not in names:
            raise TopicMemoryStorageInvariantError("reindex-activation-state", 1)

    async def _require_vector_types(self, connection: AsyncConnection, tables: Sequence[Table]) -> None:
        expected = f"VECTOR({self.profile.dimension})"
        for table in tables:
            actual = await connection.scalar(_VECTOR_TYPE_SQL, {"table_name": table.name})
            if actual is None or str(actual).replace(" ", "").upper() != expected:
                raise TopicMemoryStorageInvariantError("reindex-vector-schema", (table.name, actual))

    @staticmethod
    async def _read_shape_declaration(connection: AsyncConnection, table: Table) -> tuple[str, str | None] | None:
        row = (
            await connection.execute(select(table.c.shape, table.c.profile_fingerprint).where(table.c.singleton == 1))
        ).one_or_none()
        if row is None:
            return None
        return str(row[0]), None if row[1] is None else str(row[1])

    def _require_target_shape(self, row: tuple[str, str | None] | None) -> None:
        if row is not None and row != ("hybrid", self.fingerprint):
            raise TopicMemoryStorageInvariantError("reindex-staging-shape", 1)

    async def _require_storage_profile(self, connection: AsyncConnection) -> None:
        row = (
            await connection.execute(
                select(self.storage_profile_table.c.embedding_profile).where(
                    self.storage_profile_table.c.singleton == 1
                )
            )
        ).one_or_none()
        try:
            declared = None if row is None else EmbeddingProfile.model_validate_json(str(row[0]))
        except (TypeError, ValueError):
            raise TopicMemoryStorageInvariantError("reindex-storage-profile", 1) from None
        if declared != self.profile:
            raise TopicMemoryStorageInvariantError("reindex-storage-profile", 1)

    @staticmethod
    async def _restore_checks(
        connection: AsyncConnection,
        table_name: str,
        checks: Sequence[tuple[str, str, str]],
    ) -> None:
        constraints = await connection.run_sync(lambda sync: inspect(sync).get_check_constraints(table_name))
        names = {str(constraint["name"]) for constraint in constraints if constraint.get("name") is not None}
        for temporary, original, expression in checks:
            if temporary in names:
                await connection.exec_driver_sql(f"ALTER TABLE {_quote(table_name)} DROP CHECK {_quote(temporary)}")
            if original not in names:
                await connection.exec_driver_sql(
                    f"ALTER TABLE {_quote(table_name)} ADD CONSTRAINT {_quote(original)} CHECK ({expression})"
                )


__all__ = ["OceanBaseTopicMemoryReindexBackend"]
