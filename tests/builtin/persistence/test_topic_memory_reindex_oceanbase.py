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

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateTable

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import TopicMemoryStorageInvariantError
from powercontext.builtin.persistence import topic_memory_reindex as shared_reindex_module
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexError
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import topic_memory_reindex as reindex_module
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.oceanbase.topic_memory_reindex import OceanBaseTopicMemoryReindexBackend
from powercontext.builtin.persistence.topic_memory_index import topic_memory_embedding_profile_fingerprint
from powercontext.builtin.persistence.topic_memory_reindex import plan_topic_memory_reindex, reindex_topic_memory


def _profile() -> EmbeddingProfile:
    return EmbeddingProfile(
        profile_id="topic-reindex-test",
        model="test",
        dimension=5,
        distance="l2",
        normalization="unit",
    )


def test_oceanbase_reindex_schema_uses_target_vectors_and_temporary_checks() -> None:
    backend = OceanBaseTopicMemoryReindexBackend(_profile())
    topic_ddl = str(CreateTable(backend.topic_table).compile(dialect=mysql.dialect()))
    shape_ddl = str(CreateTable(backend.shape_table).compile(dialect=mysql.dialect()))

    assert "embedding VECTOR(5) NOT NULL" in topic_ddl
    assert "ck_pc_topic_memory_reindex_shape_singleton" in shape_ddl
    assert "ck_pc_topic_memory_retrieval_shape_singleton" not in shape_ddl


def test_oceanbase_activation_is_one_derived_only_multi_table_rename(monkeypatch) -> None:
    async def scenario() -> None:
        backend = OceanBaseTopicMemoryReindexBackend(_profile())
        names = {
            "pc_topic_memory_vector_topics",
            "pc_topic_memory_vector_chunks",
            "pc_topic_memory_retrieval_shape",
            "pc_topic_memory_reindex_topics",
            "pc_topic_memory_reindex_chunks",
            "pc_topic_memory_reindex_shape",
            "pc_server_storage_profile",
            "pc_topic_memory_reindex_storage_profile",
        }
        monkeypatch.setattr(reindex_module, "_table_names", AsyncMock(return_value=names))
        monkeypatch.setattr(backend, "validate_staging", AsyncMock())
        connection = MagicMock()
        connection.exec_driver_sql = AsyncMock()

        await backend.activate(connection, update_storage_profile=True)

        await_args = connection.exec_driver_sql.await_args
        assert await_args is not None
        statement = await_args.args[0]
        assert statement.startswith("RENAME TABLE ")
        assert statement.count(" TO ") == 8
        assert "`pc_topic_memory_vector_topics` TO `pc_topic_memory_reindex_previous_topics`" in statement
        assert "`pc_topic_memory_reindex_topics` TO `pc_topic_memory_vector_topics`" in statement
        assert "`pc_server_storage_profile` TO `pc_topic_memory_reindex_previous_storage_profile`" in statement
        assert "pc_topic_memory_active_topics" not in statement
        assert "pc_topic_memory_active_chunks" not in statement
        assert "pc_artifacts" not in statement

    asyncio.run(scenario())


def test_oceanbase_cleanup_restores_checks_before_dropping_recovery_generation(monkeypatch) -> None:
    async def scenario() -> None:
        backend = OceanBaseTopicMemoryReindexBackend(_profile())
        names = {
            "pc_topic_memory_retrieval_shape",
            "pc_server_storage_profile",
            "pc_topic_memory_reindex_previous_shape",
            "pc_topic_memory_reindex_previous_storage_profile",
            "pc_topic_memory_reindex_previous_topics",
            "pc_topic_memory_reindex_previous_chunks",
        }
        monkeypatch.setattr(reindex_module, "_table_names", AsyncMock(return_value=names))
        monkeypatch.setattr(backend, "_read_shape_declaration", AsyncMock(return_value=("hybrid", backend.fingerprint)))
        constraints = {
            "pc_topic_memory_reindex_previous_shape": [
                {"name": "ck_pc_topic_memory_retrieval_shape_singleton"},
                {"name": "ck_pc_topic_memory_retrieval_shape_value"},
                {"name": "ck_pc_topic_memory_retrieval_shape_profile"},
            ],
            "pc_topic_memory_reindex_previous_storage_profile": [{"name": "ck_pc_server_storage_profile_singleton"}],
            "pc_topic_memory_retrieval_shape": [
                {"name": "ck_pc_topic_memory_reindex_shape_singleton"},
                {"name": "ck_pc_topic_memory_reindex_shape_value"},
                {"name": "ck_pc_topic_memory_reindex_shape_profile"},
            ],
            "pc_server_storage_profile": [{"name": "ck_pc_topic_memory_reindex_storage_profile_singleton"}],
        }
        inspector = MagicMock()
        inspector.get_check_constraints.side_effect = lambda name: constraints.get(name, [])
        monkeypatch.setattr(reindex_module, "inspect", lambda _sync: inspector)
        connection = MagicMock()

        async def run_sync(callback):
            return callback(None)

        connection.run_sync = AsyncMock(side_effect=run_sync)
        connection.exec_driver_sql = AsyncMock()
        await backend.cleanup(connection)

        statements = [call.args[0] for call in connection.exec_driver_sql.await_args_list]
        drop_recovery = next(index for index, sql in enumerate(statements) if sql.startswith("DROP TABLE "))
        restore_shape = next(index for index, sql in enumerate(statements) if "ADD CONSTRAINT" in sql)
        release_old = next(index for index, sql in enumerate(statements) if "previous_shape` DROP CHECK" in sql)
        assert release_old < restore_shape < drop_recovery
        assert any("ck_pc_topic_memory_retrieval_shape_profile" in sql for sql in statements)
        assert any("ck_pc_server_storage_profile_singleton" in sql for sql in statements)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "missing_index",
    ["ix_pc_topic_memory_vector_topics_embedding", "ix_pc_topic_memory_vector_chunks_embedding"],
)
def test_oceanbase_activation_requires_every_registered_vector_index(monkeypatch, missing_index: str) -> None:
    """Reflect the observed native layout failure without a real OceanBase server."""

    async def scenario() -> None:
        backend = OceanBaseTopicMemoryReindexBackend(_profile())
        monkeypatch.setattr(reindex_module, "_table_names", AsyncMock(return_value=set()))
        monkeypatch.setattr(reindex_module, "validate_current_topic_vectors", AsyncMock())
        monkeypatch.setattr(reindex_module, "validate_staging_vectors", AsyncMock())
        monkeypatch.setattr(reindex_module, "staged_fingerprint", AsyncMock(return_value=backend.fingerprint))
        connection = MagicMock()

        async def run_sync(callback):
            return callback(None)

        async def scalar(statement, parameters=None):
            parameters = parameters or {}
            if "information_schema.columns" in str(statement):
                return f"VECTOR({backend.profile.dimension})"
            return 0 if parameters.get("index_name") == missing_index else 1

        connection.run_sync = AsyncMock(side_effect=run_sync)
        connection.scalar = AsyncMock(side_effect=scalar)

        with pytest.raises(TopicMemoryStorageInvariantError, match="reindex-vector-schema"):
            await backend.validate_active(connection, backend.fingerprint)

        with pytest.raises(TopicMemoryStorageInvariantError, match="reindex-vector-schema"):
            await backend.validate_staging(connection)

    asyncio.run(scenario())


def _patch_native_planner(monkeypatch: pytest.MonkeyPatch, source: EmbeddingProfile) -> None:
    old_fingerprint = topic_memory_embedding_profile_fingerprint(source)
    monkeypatch.setattr(
        shared_reindex_module,
        "_table_names",
        AsyncMock(
            return_value={
                "pc_schema_revision",
                "pc_server_storage_profile",
                "pc_atomic_memory_current",
            }
        ),
    )
    monkeypatch.setattr(
        shared_reindex_module,
        "staging_counts",
        AsyncMock(
            return_value={
                "topics_total": 0,
                "topics_processed": 0,
                "topics_remaining": 0,
                "chunks_total": 0,
                "chunks_processed": 0,
                "chunks_remaining": 0,
            }
        ),
    )
    monkeypatch.setattr(shared_reindex_module, "_active_fingerprint", AsyncMock(return_value=old_fingerprint))
    monkeypatch.setattr(shared_reindex_module, "declared_storage_profile", AsyncMock(return_value=source))
    monkeypatch.setattr(shared_reindex_module, "staged_fingerprint", AsyncMock(return_value=None))


def _native_connection(drivername: str, vector_type: str, incompatible: int | None) -> MagicMock:
    connection = MagicMock()
    connection.dialect = mysql.dialect()
    connection.engine.url.drivername = drivername
    connection.execute = AsyncMock()
    connection.exec_driver_sql = AsyncMock()

    async def scalar(statement, _parameters=None):
        if "information_schema.columns" in str(statement):
            return vector_type
        if "information_schema.statistics" in str(statement):
            return 1
        return incompatible

    connection.scalar = AsyncMock(side_effect=scalar)
    return connection


@pytest.mark.parametrize("incompatible", [None, 1], ids=["empty-projection", "populated-projection"])
def test_seekdb_dimension_change_is_rejected_before_ddl_on_repeated_attempts(
    monkeypatch: pytest.MonkeyPatch,
    incompatible: int | None,
) -> None:
    """Exercise fresh native connections without claiming embedded SeekDB execution."""

    source = _profile().model_copy(update={"profile_id": "source", "dimension": 2})
    target = _profile().model_copy(update={"profile_id": "target", "dimension": 3})
    _patch_native_planner(monkeypatch, source)

    class _Database:
        def __init__(self, connection) -> None:
            self.connection = connection

        @asynccontextmanager
        async def transaction(self):
            yield self.connection

    async def scenario() -> None:
        for _attempt in range(2):
            backend = OceanBaseTopicMemoryReindexBackend(target)
            backend.validate_active = AsyncMock()
            backend.prepare = AsyncMock(side_effect=AssertionError("persistent DDL must not be reached"))
            connection = _native_connection("mysql+aseekdb", "VECTOR(2)", incompatible)
            report = await reindex_topic_memory(
                cast(AsyncDatabase, _Database(connection)),
                backend,
                embedding_model=None,
                maintenance_confirmed=True,
                rebuild_atomic_memory=True,
            )

            assert not report.ready and not report.activated
            assert not report.atomic_memory_rebuild_required
            assert any("embedded SeekDB does not support changing" in error for error in report.errors)
            backend.prepare.assert_not_awaited()
            connection.execute.assert_not_called()
            connection.exec_driver_sql.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("drivername", "vector_type", "incompatible"),
    [
        pytest.param("mysql+aiomysql", "VECTOR(2)", None, id="remote-oceanbase-cross-dimension"),
        pytest.param("mysql+aseekdb", "VECTOR(3)", 1, id="seekdb-same-dimension-row-rebuild"),
    ],
)
def test_supported_native_atomic_rebuilds_remain_ready(
    monkeypatch: pytest.MonkeyPatch,
    drivername: str,
    vector_type: str,
    incompatible: int | None,
) -> None:
    source = _profile().model_copy(update={"profile_id": "source", "dimension": 2})
    target = _profile().model_copy(update={"profile_id": "target", "dimension": 3})
    _patch_native_planner(monkeypatch, source)

    async def scenario() -> None:
        backend = OceanBaseTopicMemoryReindexBackend(target)
        backend.validate_active = AsyncMock()
        connection = _native_connection(drivername, vector_type, incompatible)
        report = await plan_topic_memory_reindex(connection, backend, rebuild_atomic_memory=True)

        assert report.ready and not report.activated
        assert report.atomic_memory_rebuild_required
        assert report.errors == ()
        connection.execute.assert_not_called()
        connection.exec_driver_sql.assert_not_called()

    asyncio.run(scenario())


def test_seekdb_vector_reconfiguration_rejects_before_index_or_column_ddl() -> None:
    target = _profile().model_copy(update={"profile_id": "target", "dimension": 3})
    connection = _native_connection("mysql+aseekdb", "VECTOR(2)", None)
    statements: list[str] = []

    async def ddl(statement, *_args, **_kwargs) -> None:
        statements.append(str(statement))
        if str(statement).startswith("ALTER TABLE"):
            raise SQLAlchemyError("1235")

    connection.exec_driver_sql = AsyncMock(side_effect=ddl)

    async def scenario() -> None:
        with pytest.raises(AtomicMemoryIndexError) as caught:
            await OceanBaseAtomicMemoryIndex(target).reconfigure_vector_column(connection)
        assert "embedded SeekDB does not support changing" in str(caught.value)

    asyncio.run(scenario())
    assert statements == []
    observed = [str(call.args[0]) for call in connection.scalar.await_args_list]
    assert any("information_schema.columns" in statement for statement in observed)
    assert not any("information_schema.statistics" in statement for statement in observed)


@pytest.mark.skipif(
    os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1",
    reason="real seekdb probe not enabled; not executed on this host",
)
def test_real_seekdb_activation_keeps_the_registered_chunk_index(short_tmp_path: Path) -> None:
    """Reproduce the reported native layout loss through the real activation path."""

    pytest.importorskip("pylibseekdb")
    from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
    from powercontext.builtin.persistence.migrations.deployment import deployment_runner
    from powercontext.builtin.persistence.migrations.server_readiness import check_server_layout
    from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
    from powercontext.builtin.persistence.topic_memory_reindex import reindex_topic_memory
    from powercontext.builtin.runtime.config import InferenceConfig
    from powercontext.server.settings import ServerSettings

    source = _profile()
    target = source.model_copy(update={"profile_id": "topic-reindex-target", "model": "fixture-target"})
    config = SeekDBConfig(path=short_tmp_path / "seekdb")
    evidence = short_tmp_path / "evidence"
    settings = ServerSettings(
        database=config,
        inference=InferenceConfig(
            embedding_model=source.model,
            embedding_profile_id=source.profile_id,
            embedding_dimension=source.dimension,
            embedding_normalization=source.normalization,
        ),
    )
    runner = deployment_runner(settings, evidence_dir=evidence)
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)

    async def reindex():
        # No authoritative Topics: activation still performs every staged DDL
        # and declaration swap, which is the DDL that lost the index.
        async with SeekDBProfile.open(config, tables=()) as profile:
            return await reindex_topic_memory(
                profile.database,
                OceanBaseTopicMemoryReindexBackend(target),
                embedding_model=None,
                maintenance_confirmed=True,
                batch_size=1,
            )

    report = asyncio.run(reindex())
    assert report.ready is True and report.activated is True

    def verify(connection, _identity, _guard) -> None:
        indexes = {
            (row[0], row[1])
            for row in connection.exec_driver_sql(
                "SELECT DISTINCT table_name, index_name FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name IN "
                "('pc_topic_memory_vector_topics', 'pc_topic_memory_vector_chunks')"
            )
        }
        assert ("pc_topic_memory_vector_topics", "ix_pc_topic_memory_vector_topics_embedding") in indexes
        assert ("pc_topic_memory_vector_chunks", "ix_pc_topic_memory_vector_chunks_embedding") in indexes
        # Startup admission must accept the exact activated native layout.
        check_server_layout(connection)

    MaintenanceConnections(config, evidence_directory=evidence).run(verify)
