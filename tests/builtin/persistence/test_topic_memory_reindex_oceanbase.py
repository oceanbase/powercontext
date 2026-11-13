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
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.oceanbase import topic_memory_reindex as reindex_module
from powercontext.builtin.persistence.oceanbase.topic_memory_reindex import OceanBaseTopicMemoryReindexBackend


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
