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

"""SQL and state tests using doubles only; no native OceanBase execution is claimed."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pyobvector import VectorIndex
from sqlalchemy import Insert, Select, Table
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryCapabilityError,
    TopicMemoryStorageInvariantError,
)
from powercontext.builtin.persistence.oceanbase import topic_memory_reindex as reindex_module
from powercontext.builtin.persistence.oceanbase.topic_memory_reindex import (
    OceanBaseTopicMemoryReindexBackend,
)
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE
from powercontext.builtin.persistence.topic_memory_reindex import (
    TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
    TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
    TopicMemoryReindexChunk,
    TopicMemoryReindexSource,
)


class _Result:
    def __init__(self, row: tuple[Any, ...] | None = None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self._row


class _Inspector:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def get_table_names(self) -> list[str]:
        return sorted(self._connection.names)

    def get_check_constraints(self, table_name: str) -> list[dict[str, str]]:
        return [{"name": name} for name in sorted(self._connection.checks.get(table_name, set()))]


class _Sync:
    def __init__(self, inspector: _Inspector) -> None:
        self.inspector = inspector


class _Connection:
    """Small stateful connection double for schema reads and captured writes."""

    def __init__(
        self,
        backend: OceanBaseTopicMemoryReindexBackend,
        *,
        names: set[str] | None = None,
        shape_rows: dict[str, tuple[str, str | None] | None] | None = None,
        storage_row: str | None = None,
        checks: dict[str, set[str]] | None = None,
    ) -> None:
        self.backend = backend
        self.names = set() if names is None else set(names)
        self.shape_rows = {} if shape_rows is None else dict(shape_rows)
        self.storage_row = storage_row
        self.checks = {} if checks is None else {name: set(values) for name, values in checks.items()}
        self.vector_types: dict[str, str | None] = {}
        self.executed: list[tuple[Any, Any]] = []
        self.scalar_parameters: list[dict[str, Any]] = []
        self.driver_sql: list[str] = []
        self.index_checks: list[bool] = []
        self.commits = 0
        self.rollbacks = 0
        self.dialect = mysql_dialect()
        self._inspector = _Inspector(self)
        self._sync = _Sync(self._inspector)

    def install_inspector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(reindex_module, "inspect", lambda _sync: self._inspector)

    async def run_sync(self, callback: Any) -> Any:
        return callback(self._sync)

    async def execute(self, statement: object, parameters: object = None) -> _Result:
        self.executed.append((statement, parameters))
        if isinstance(statement, Select):
            table_name = cast(Table, statement.get_final_froms()[0]).name
            if table_name in {TOPIC_MEMORY_REINDEX_SHAPE_TABLE, TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name}:
                return _Result(self.shape_rows.get(table_name))
            if table_name == TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE:
                row = None if self.storage_row is None else (self.storage_row,)
                return _Result(row)
        if isinstance(statement, Insert):
            if statement.table.name == TOPIC_MEMORY_REINDEX_SHAPE_TABLE:
                self.shape_rows[statement.table.name] = ("hybrid", self.backend.fingerprint)
            elif statement.table.name == TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE:
                self.storage_row = self.backend.profile.model_dump_json()
        return _Result()

    async def scalar(self, statement: object, parameters: dict[str, Any]) -> str | None:
        self.scalar_parameters.append(parameters)
        table_name = str(parameters["table_name"])
        return self.vector_types.get(table_name, f"VECTOR({self.backend.profile.dimension})")

    async def exec_driver_sql(self, statement: str) -> _Result:
        self.driver_sql.append(statement)
        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _async(connection: _Connection) -> AsyncConnection:
    return cast(AsyncConnection, connection)


class _Index:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def create(self, _sync: _Sync, *, checkfirst: bool) -> None:
        self._connection.index_checks.append(checkfirst)


def _profile(dimension: int = 2) -> EmbeddingProfile:
    return EmbeddingProfile(profile_id="target", model="target-model", dimension=dimension)


def _backend(dimension: int = 2) -> OceanBaseTopicMemoryReindexBackend:
    return OceanBaseTopicMemoryReindexBackend(_profile(dimension))


def _active_and_staging(backend: OceanBaseTopicMemoryReindexBackend) -> set[str]:
    return {
        backend._active.topic_table.name,
        backend._active.chunk_table.name,
        TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name,
        backend.topic_table.name,
        backend.chunk_table.name,
        backend.shape_table.name,
    }


def test_staging_schema_uses_target_vectors_and_original_hnsw_name() -> None:
    backend = _backend(3)

    assert str(backend.topic_table.c.embedding.type) == "VECTOR(3)"
    assert str(backend.chunk_table.c.embedding.type) == "VECTOR(3)"
    indexes = list(backend.topic_table.indexes)
    assert len(indexes) == 1
    assert isinstance(indexes[0], VectorIndex)
    assert indexes[0].name == "ix_pc_topic_memory_vector_topics_embedding"
    assert indexes[0].params == "distance=l2,type=hnsw"
    assert {constraint.name for constraint in backend.shape_table.constraints} >= {
        "ck_pc_topic_memory_reindex_shape_singleton",
        "ck_pc_topic_memory_reindex_shape_value",
        "ck_pc_topic_memory_reindex_shape_profile",
    }


def test_prepare_resumes_matching_partial_ddl_without_touching_active_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(
            backend,
            names={
                backend._active.topic_table.name,
                backend._active.chunk_table.name,
                TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name,
                backend.topic_table.name,
                backend.shape_table.name,
            },
            shape_rows={backend.shape_table.name: ("hybrid", backend.fingerprint)},
        )
        connection.install_inspector(monkeypatch)
        created: list[str] = []

        async def create_table(_connection: object, table: Any) -> None:
            created.append(table.name)

        monkeypatch.setattr(reindex_module, "_create_table", create_table)
        backend._topic_index = cast(VectorIndex, _Index(connection))

        await backend.prepare(_async(connection), update_storage_profile=True)

        assert created == [
            backend.shape_table.name,
            backend.topic_table.name,
            backend.chunk_table.name,
            backend.storage_profile_table.name,
        ]
        assert connection.index_checks == [True]
        assert connection.storage_row == backend.profile.model_dump_json()
        touched = " ".join(str(statement) for statement, _ in connection.executed)
        assert backend._active.topic_table.name not in touched
        assert backend._active.chunk_table.name not in touched
        assert TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name not in touched
        assert connection.driver_sql == []
        assert connection.commits == 0
        assert connection.rollbacks == 0

    asyncio.run(scenario())


def test_prepare_rejects_different_shape_intent_before_any_ddl(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(
            backend,
            names={backend.topic_table.name, backend.shape_table.name},
            shape_rows={backend.shape_table.name: ("hybrid", "different-target")},
        )
        connection.install_inspector(monkeypatch)
        create_table = AsyncMock()
        monkeypatch.setattr(reindex_module, "_create_table", create_table)

        with pytest.raises(TopicMemoryStorageInvariantError) as caught:
            await backend.prepare(_async(connection), update_storage_profile=False)

        assert caught.value.code == "reindex-staging-shape"
        create_table.assert_not_awaited()
        assert connection.driver_sql == []

    asyncio.run(scenario())


@pytest.mark.parametrize("shape_present", [False, True])
def test_prepare_recreates_partial_tables_without_shape_declaration(
    monkeypatch: pytest.MonkeyPatch, shape_present: bool
) -> None:
    async def scenario() -> None:
        backend = _backend()
        names = {backend.chunk_table.name} | ({backend.shape_table.name} if shape_present else set())
        connection = _Connection(backend, names=names, shape_rows={backend.shape_table.name: None})
        connection.install_inspector(monkeypatch)
        created: list[str] = []

        async def create_table(_connection: object, table: Any) -> None:
            created.append(table.name)

        monkeypatch.setattr(reindex_module, "_create_table", create_table)
        backend._topic_index = cast(VectorIndex, _Index(connection))

        await backend.prepare(_async(connection), update_storage_profile=False)

        assert connection.driver_sql == [
            "DROP TABLE IF EXISTS `pc_topic_memory_reindex_chunks`, `pc_topic_memory_reindex_shape`, "
            "`pc_topic_memory_reindex_storage_profile`, `pc_topic_memory_reindex_topics`"
        ]
        assert created == [backend.shape_table.name, backend.topic_table.name, backend.chunk_table.name]
        assert connection.shape_rows[backend.shape_table.name] == ("hybrid", backend.fingerprint)
        assert connection.index_checks == [True]

    asyncio.run(scenario())


def test_write_staging_flattens_and_canonicalizes_only_staged_rows() -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(backend)
        sources = (
            TopicMemoryReindexSource(
                scope_id="scope",
                artifact_id="a",
                revision=3,
                title="A",
                summary="first",
                chunks=(
                    TopicMemoryReindexChunk(ordinal=0, text="zero"),
                    TopicMemoryReindexChunk(ordinal=2, text="two"),
                ),
            ),
            TopicMemoryReindexSource(
                scope_id="scope",
                artifact_id="b",
                revision=4,
                title="B",
                summary="second",
                chunks=(),
            ),
        )

        await backend.write_staging(
            _async(connection),
            sources,
            ((3.0, 4.0), (0.0, 2.0), (-2.0, 0.0), (5.0, 0.0)),
        )

        assert len(connection.executed) == 4
        first_delete, second_delete = (statement for statement, _ in connection.executed[:2])
        assert "pc_topic_memory_reindex_chunks" in str(first_delete)
        assert "pc_topic_memory_reindex_topics" in str(second_delete)
        assert "pc_topic_memory_vector_chunks" not in str(first_delete)
        assert "pc_topic_memory_vector_topics" not in str(second_delete)
        topics = connection.executed[2][1]
        chunks = connection.executed[3][1]
        assert [row["artifact_id"] for row in topics] == ["a", "b"]
        assert topics[0]["embedding"] == pytest.approx((0.6, 0.8))
        assert topics[1]["embedding"] == pytest.approx((1.0, 0.0))
        assert [(row["artifact_id"], row["chunk_ordinal"]) for row in chunks] == [("a", 0), ("a", 2)]
        assert chunks[0]["embedding"] == pytest.approx((0.0, 1.0))
        assert chunks[1]["embedding"] == pytest.approx((-1.0, 0.0))
        assert all(row["profile_fingerprint"] == backend.fingerprint for row in (*topics, *chunks))
        assert connection.commits == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("vectors", [(), ((3.0, 4.0), (0.0, 0.0))])
def test_write_staging_rejects_invalid_vectors_before_deleting(vectors: tuple[tuple[float, ...], ...]) -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(backend)
        source = TopicMemoryReindexSource("scope", "a", 1, "A", "summary", ())

        with pytest.raises(TopicMemoryCapabilityError):
            await backend.write_staging(_async(connection), (source,), vectors)

        assert connection.executed == []

    asyncio.run(scenario())


def test_validate_active_uses_the_supplied_current_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(backend)
        validate = AsyncMock()
        monkeypatch.setattr(reindex_module, "validate_current_topic_vectors", validate)

        await backend.validate_active(_async(connection), "current-fingerprint")

        validate.assert_awaited_once_with(
            connection,
            backend._active.topic_table,
            backend._active.chunk_table,
            "current-fingerprint",
        )
        assert connection.scalar_parameters == []

    asyncio.run(scenario())


def test_validate_staging_checks_declaration_shared_vectors_and_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(
            backend,
            names={backend.storage_profile_table.name},
            storage_row=backend.profile.model_dump_json(),
        )
        connection.install_inspector(monkeypatch)
        fingerprint = AsyncMock(return_value=backend.fingerprint)
        validate = AsyncMock()
        monkeypatch.setattr(reindex_module, "staged_fingerprint", fingerprint)
        monkeypatch.setattr(reindex_module, "validate_staging_vectors", validate)

        await backend.validate_staging(_async(connection))

        fingerprint.assert_awaited_once_with(connection, backend)
        validate.assert_awaited_once_with(connection, backend)
        assert [parameters["table_name"] for parameters in connection.scalar_parameters] == [
            backend.topic_table.name,
            backend.chunk_table.name,
        ]

    asyncio.run(scenario())


def test_activate_validates_and_executes_one_atomic_rename_with_staged_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend()
        names = _active_and_staging(backend) | {
            reindex_module._STORAGE_PROFILE_TABLE,
            backend.storage_profile_table.name,
        }
        connection = _Connection(backend, names=names)
        connection.install_inspector(monkeypatch)
        validate = AsyncMock()
        monkeypatch.setattr(backend, "validate_staging", validate)

        # Physical staging presence, not a changed retry flag, selects the profile swap.
        await backend.activate(_async(connection), update_storage_profile=False)

        validate.assert_awaited_once_with(connection)
        assert len(connection.driver_sql) == 1
        statement = connection.driver_sql[0]
        assert statement.startswith("RENAME TABLE ")
        assert statement.count(" TO ") == 8
        assert "`pc_topic_memory_reindex_previous_topics`" in statement
        assert "`pc_topic_memory_reindex_previous_chunks`" in statement
        assert "`pc_topic_memory_reindex_previous_shape`" in statement
        assert "`pc_topic_memory_reindex_previous_storage_profile`" in statement
        assert "pc_topic_memory_active_topics" not in statement
        assert "pc_topic_memory_active_chunks" not in statement
        assert "DROP TABLE" not in statement

    asyncio.run(scenario())


def test_activate_recognizes_complete_post_rename_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(
            backend,
            names={
                backend._active.topic_table.name,
                backend._active.chunk_table.name,
                TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name,
                reindex_module._PREVIOUS_TOPICS,
                reindex_module._PREVIOUS_CHUNKS,
                reindex_module._PREVIOUS_SHAPE,
                reindex_module._STORAGE_PROFILE_TABLE,
                reindex_module._PREVIOUS_STORAGE_PROFILE,
            },
        )
        connection.install_inspector(monkeypatch)
        validate = AsyncMock()
        monkeypatch.setattr(backend, "validate_staging", validate)

        await backend.activate(_async(connection), update_storage_profile=True)

        validate.assert_not_awaited()
        assert connection.driver_sql == []

    asyncio.run(scenario())


def test_activate_rejects_mixed_state_without_renaming(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        backend = _backend()
        connection = _Connection(
            backend,
            names=_active_and_staging(backend) | {reindex_module._PREVIOUS_SHAPE},
        )
        connection.install_inspector(monkeypatch)
        validate = AsyncMock()
        monkeypatch.setattr(backend, "validate_staging", validate)

        with pytest.raises(TopicMemoryStorageInvariantError):
            await backend.activate(_async(connection), update_storage_profile=False)

        validate.assert_not_awaited()
        assert connection.driver_sql == []

    asyncio.run(scenario())


def test_cleanup_guards_target_drops_previous_and_recovers_check_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend()
        active_shape = TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name
        connection = _Connection(
            backend,
            names={
                active_shape,
                reindex_module._STORAGE_PROFILE_TABLE,
                *reindex_module._PREVIOUS_TABLES,
            },
            shape_rows={active_shape: ("hybrid", backend.fingerprint)},
            checks={
                active_shape: {
                    # singleton: interrupted after DROP CHECK; only ADD remains.
                    # value: normal temporary-to-original restoration.
                    "ck_pc_topic_memory_reindex_shape_value",
                    # profile: already restored; no statement is needed.
                    "ck_pc_topic_memory_retrieval_shape_profile",
                },
                reindex_module._STORAGE_PROFILE_TABLE: {"ck_pc_topic_memory_reindex_storage_profile_singleton"},
                reindex_module._PREVIOUS_SHAPE: {"ck_pc_topic_memory_retrieval_shape_value"},
                reindex_module._PREVIOUS_STORAGE_PROFILE: {"ck_pc_server_storage_profile_singleton"},
            },
        )
        connection.install_inspector(monkeypatch)

        await backend.cleanup(_async(connection))

        assert connection.driver_sql == [
            "ALTER TABLE `pc_topic_memory_reindex_previous_shape` DROP CHECK "
            "`ck_pc_topic_memory_retrieval_shape_value`",
            "ALTER TABLE `pc_topic_memory_reindex_previous_storage_profile` DROP CHECK "
            "`ck_pc_server_storage_profile_singleton`",
            "ALTER TABLE `pc_topic_memory_retrieval_shape` ADD CONSTRAINT "
            "`ck_pc_topic_memory_retrieval_shape_singleton` CHECK (singleton = 1)",
            "ALTER TABLE `pc_topic_memory_retrieval_shape` DROP CHECK `ck_pc_topic_memory_reindex_shape_value`",
            "ALTER TABLE `pc_topic_memory_retrieval_shape` ADD CONSTRAINT "
            "`ck_pc_topic_memory_retrieval_shape_value` CHECK (shape IN ('fts', 'hybrid'))",
            "ALTER TABLE `pc_server_storage_profile` DROP CHECK `ck_pc_topic_memory_reindex_storage_profile_singleton`",
            "ALTER TABLE `pc_server_storage_profile` ADD CONSTRAINT "
            "`ck_pc_server_storage_profile_singleton` CHECK (singleton = 1)",
            "DROP TABLE `pc_topic_memory_reindex_previous_chunks`, "
            "`pc_topic_memory_reindex_previous_topics`, "
            "`pc_topic_memory_reindex_previous_shape`, "
            "`pc_topic_memory_reindex_previous_storage_profile`",
        ]
        assert connection.commits == 0

    asyncio.run(scenario())


def test_cleanup_never_drops_previous_for_another_active_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        backend = _backend()
        active_shape = TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.name
        connection = _Connection(
            backend,
            names={active_shape, reindex_module._PREVIOUS_TOPICS},
            shape_rows={active_shape: ("hybrid", "another-target")},
        )
        connection.install_inspector(monkeypatch)

        with pytest.raises(TopicMemoryStorageInvariantError) as caught:
            await backend.cleanup(_async(connection))

        assert caught.value.code == "reindex-active-shape"
        assert connection.driver_sql == []

    asyncio.run(scenario())
