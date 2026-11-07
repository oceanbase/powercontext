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

"""Explicit vector maintenance preserves the configured embedding space."""

import asyncio
import json
from contextlib import closing

import pytest
import sqlite_vec
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference.models import EmbeddingResult
from powercontext.builtin.persistence.migrations.bundle import sqlite_inventory
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.server_readiness import check_server_schema
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.topic_memory_index import topic_memory_embedding_profile_fingerprint
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class ControlledEmbedding:
    def __init__(self, embedding):
        self.profile = embedding

    async def embed(self, texts, /):
        vector = (1.0,) + (0.0,) * (self.profile.dimension - 1)
        return EmbeddingResult(vectors=tuple(vector for _ in texts))


def connect(database):
    import sqlite3

    connection = sqlite3.connect(database)
    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)
    return connection


def profile(dimension):
    return EmbeddingProfile(profile_id="migration-vectors", model="fixture", dimension=dimension)


def configuration(embedding):
    return {
        "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
        "embedding_enabled": True,
        "embedding_profile": embedding.model_dump(mode="json"),
    }


def apply(runner):
    plan = runner.plan()
    return runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)


def source(database, embedding):
    bundle = server_bundle()
    frozen = json.loads((bundle.directory / "profiles/vector.json").read_text())
    engine = create_engine("sqlite://", creator=lambda: connect(database))
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        config = bundle.config(connection)
        config.attributes["configuration"] = configuration(embedding)
        command.upgrade(config, "s1300")
        connection.exec_driver_sql("DROP TABLE pc_schema_revision")
        for statement in frozen["sqlite"]["initialize"]:
            connection.exec_driver_sql(statement.replace("__DIMENSION__", str(embedding.dimension)))
        connection.exec_driver_sql(
            "INSERT INTO pc_topic_memory_retrieval_shape VALUES (1,'hybrid',?)",
            (topic_memory_embedding_profile_fingerprint(embedding),),
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version)"
            " VALUES ('scope','title','summary','scope','title','summary',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content)"
            " VALUES ('scope','custom-family','historical',1,?)",
            (b'{"history":"unchanged"}',),
        )
        connection.commit()
    engine.dispose()


@pytest.mark.parametrize("dimension", [3, 4])
def test_vector_upgrade_preserves_history_shape_and_matches_fresh_storage(tmp_path, dimension):
    embedding = profile(dimension)
    database = tmp_path / "old.db"
    source(database, embedding)
    bundle = server_bundle(embedding_profile=embedding)
    runner = SQLiteMigrationRunner(database, bundle, configuration=configuration(embedding))
    assert runner.plan().source_revision == "s1300"
    assert apply(runner).backup_state == "completed"
    assert not apply(runner).changed
    assert runner.verify().state == "ready"
    with closing(connect(database)) as connection:
        assert connection.execute("SELECT content FROM pc_artifacts WHERE artifact_id='historical'").fetchone() == (
            b'{"history":"unchanged"}',
        )
        assert connection.execute(
            "SELECT shape,profile_fingerprint FROM pc_topic_memory_retrieval_shape"
        ).fetchone() == (
            "hybrid",
            topic_memory_embedding_profile_fingerprint(embedding),
        )
        assert json.loads(
            connection.execute("SELECT embedding_profile FROM pc_server_storage_profile").fetchone()[0]
        ) == embedding.model_dump(mode="json")

    fresh = tmp_path / "fresh.db"
    apply(SQLiteMigrationRunner(fresh, bundle, configuration=configuration(embedding)))
    inventories = []
    for path in (database, fresh):
        engine = create_engine("sqlite://", creator=lambda path=path: connect(path))
        with engine.connect() as connection:
            check_server_schema(connection)
            inventories.append(sqlite_inventory(connection, exclude=frozenset(bundle.retained_objects)))
        engine.dispose()
    assert inventories[0] == inventories[1]
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), mcp=McpConfig(enabled=False)
    )
    app = create_server_app(
        settings=settings, embedding_model=ControlledEmbedding(embedding), scheduler_path=tmp_path / "scheduler"
    )
    with TestClient(app) as client:
        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": "scope", "kind": "fact", "text": "New vectors remain searchable after maintenance."},
        )
        assert remembered.status_code == 200
        searched = client.post(
            "/v1/memory/search", json={"scope_id": "scope", "query": "New vectors", "mode": "vector"}
        )
        assert searched.status_code == 200
        assert searched.json()["hits"][0]["memory"]["text"] == "New vectors remain searchable after maintenance."


def test_vector_configuration_change_is_rejected_without_rebuilding_helpers(tmp_path):
    database = tmp_path / "managed.db"
    embedding = profile(3)
    bundle = server_bundle(embedding_profile=embedding)
    apply(SQLiteMigrationRunner(database, bundle, configuration=configuration(embedding)))
    before = database.read_bytes()
    other = embedding.model_copy(update={"model": "different-model"})
    runner = SQLiteMigrationRunner(database, server_bundle(embedding_profile=other), configuration=configuration(other))
    with pytest.raises(MigrationError, match="embedding_profile_mismatch"):
        runner.plan()
    assert database.read_bytes() == before


def test_missing_managed_vector_helper_is_rejected_without_startup_repair(tmp_path):
    database = tmp_path / "managed.db"
    embedding = profile(3)
    bundle = server_bundle(embedding_profile=embedding)
    apply(SQLiteMigrationRunner(database, bundle, configuration=configuration(embedding)))
    with closing(connect(database)) as connection, connection:
        connection.execute("DROP TRIGGER pc_atomic_memory_vec_insert")
    before = database.read_bytes()
    engine = create_engine("sqlite://", creator=lambda: connect(database))
    try:
        with engine.connect() as connection, pytest.raises(MigrationError, match="incompatible_schema"):
            check_server_schema(connection)
    finally:
        engine.dispose()
    assert database.read_bytes() == before


def test_generic_storage_reader_admits_managed_vectors_without_explicit_extension_flag(tmp_path):
    database = tmp_path / "managed.db"
    embedding = profile(3)
    runner = SQLiteMigrationRunner(
        database, server_bundle(embedding_profile=embedding), configuration=configuration(embedding)
    )
    apply(runner)

    async def read():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        async with SQLiteProfile.open(config, tables=()) as storage, storage.database.transaction() as connection:
            assert (
                await connection.exec_driver_sql("SELECT COUNT(*) FROM pc_atomic_memory_current_vec")
            ).scalar_one() == 0

    asyncio.run(read())
    assert not apply(runner).changed


def test_configured_vector_plan_is_read_only_for_a_missing_database(tmp_path):
    from powercontext.builtin.persistence.migrations.deployment import deployment_runner
    from powercontext.builtin.runtime.config import InferenceConfig
    from powercontext.server.settings import ServerSettings

    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
        inference=InferenceConfig(
            embedding_model="openai:fixture", embedding_profile_id="fixture", embedding_dimension=3
        ),
    )
    plan = deployment_runner(settings).plan()
    assert plan.state == "uninitialized" and plan.readiness_scope == "server"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("damage", ["missing_current", "helper_identity"])
def test_vector_readiness_rejects_incomplete_atomic_projections(tmp_path, damage):
    database = tmp_path / "managed.db"
    embedding = profile(3)
    source(database, embedding)
    runner = SQLiteMigrationRunner(
        database, server_bundle(embedding_profile=embedding), configuration=configuration(embedding)
    )
    apply(runner)
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), mcp=McpConfig(enabled=False)
    )
    app = create_server_app(
        settings=settings, embedding_model=ControlledEmbedding(embedding), scheduler_path=tmp_path / "scheduler"
    )
    with TestClient(app) as client:
        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": "scope", "kind": "fact", "text": "Keep this vector-backed memory searchable."},
        )
        assert remembered.status_code == 200
    assert runner.verify().state == "ready"
    with closing(connect(database)) as connection, connection:
        if damage == "missing_current":
            connection.execute("DELETE FROM pc_atomic_memory_current")
        else:
            connection.execute("UPDATE pc_atomic_memory_current_fts SET identity_token='broken'")
    original = database.read_bytes()
    for inspect_state in (runner.plan, runner.verify):
        with pytest.raises(MigrationError, match="projection_verification_failed"):
            inspect_state()
    app = create_server_app(
        settings=settings, embedding_model=ControlledEmbedding(embedding), scheduler_path=tmp_path / "scheduler"
    )
    with pytest.raises(MigrationError, match="projection_verification_failed"), TestClient(app):
        pytest.fail("Incomplete projections must block vector Server startup")
    assert database.read_bytes() == original
