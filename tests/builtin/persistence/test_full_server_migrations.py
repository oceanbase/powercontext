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

"""Complete frozen Server upgrades, rollback, recovery and admission behavior."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine

from powercontext.builtin.persistence.dream_schema import check_dream_schema
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest


@pytest.fixture
def source(tmp_path):
    database = tmp_path / "source.db"
    configuration = {"processing_manifest": canonical_processing_manifest(BuiltinConfig()), "embedding_enabled": False}
    bundle = server_bundle()
    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        config = bundle.config(connection)
        config.attributes["configuration"] = configuration
        command.upgrade(config, "s1300")
        connection.exec_driver_sql("DROP TABLE pc_schema_revision")
        connection.exec_driver_sql(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) VALUES ('scope','title','summary','scope','title','summary',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) VALUES ('scope','custom-family','historical',1,?)",
            (b'{"history":"unchanged"}',),
        )
        connection.commit()
    engine.dispose()
    return SQLiteMigrationRunner(database, bundle, configuration=configuration)


def apply(runner):
    plan = runner.plan()
    return runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)


def dump(path):
    with closing(sqlite3.connect(path)) as connection:
        return tuple(connection.iterdump())


def test_complete_upgrade_preserves_history_backup_and_repeated_apply(source):
    before = dump(source.database)
    plan = source.plan()
    assert plan.source_revision == "s1300" and plan.target_revision == "v1_5_0_001"
    assert plan.revisions == ("s1400", "s1401", "s1402", "v1_4_1_004", "v1_4_1_005", "v1_4_1_006", "v1_5_0_001")
    assert dump(source.database) == before
    result = apply(source)
    assert result.readiness_scope == "server" and result.backup_state == "completed"
    assert result.backup_ref and Path(result.backup_ref).exists()
    with closing(sqlite3.connect(source.database)) as connection:
        assert connection.execute(
            "SELECT content,created_at,created_by,restored_from_revision,rollback_reason FROM pc_artifacts"
        ).fetchall() == [(b'{"history":"unchanged"}', None, None, None, None)]
    after = dump(source.database)
    assert not apply(source).changed
    assert source.verify().readiness_scope == "server"
    assert dump(source.database) == after


def test_archived_memory_history_survives_repeat_apply_and_runtime_admission(source):
    apply(source)
    metadata = json.dumps({
        "format": "powercontext.memory-artifact-archive.v1",
        "lineage": {"sources": [], "artifacts": []},
        "incoming_references": [],
        "collection": {"head": None, "owners": [], "tags": [], "bindings": [], "idempotency": []},
    }).encode()
    archived = ("scope", "memory", "legacy", 2, b'{"history":"unchanged"}', b"[]", metadata)
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute("INSERT INTO pc_memory_artifact_archive VALUES (?,?,?,?,?,?,?)", archived)
    assert not apply(source).changed
    assert source.verify().readiness_scope == "server"

    async def read_archive():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{source.database}")
        async with (
            SQLiteProfile.open(config, tables=()) as profile,
            profile.database.transaction() as connection,
        ):
            rows = await connection.exec_driver_sql("SELECT * FROM pc_memory_artifact_archive")
            assert [tuple(row) for row in rows] == [archived]

    asyncio.run(read_archive())


def test_migration_revokes_old_fence_and_allows_expiring_worker_lease(source):
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_artifact_processing_leases (supervisor_group,holder_id,supervisor_generation,lease_expires_at) VALUES ('global','old-worker',7,NULL)"
        )
    apply(source)

    async def scenario():
        repository = ArtifactProcessingLeaseRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{source.database}")
        async with SQLiteProfile.open(config, tables=()) as profile:
            async with profile.database.transaction() as connection:
                acquired = await repository.try_acquire(connection, "new-worker", 15)
            assert acquired is not None, "The migration fence must allow an OceanBase-style lease takeover"
            assert acquired.supervisor_generation > 7
            assert acquired.lease_expires_at is not None

    asyncio.run(scenario())


def test_domain_failure_rolls_back_and_reuses_original_recovery_point(source, monkeypatch):
    original = source.bundle.domains._invoke

    def interrupted(step, operation, connection, configuration):
        original(step, operation, connection, configuration)
        if step.id == "server_upgrade" and operation == "verify":
            raise MigrationError("injected_failure", "Interrupted before revision advancement")

    with monkeypatch.context() as patch:
        patch.setattr(source.bundle.domains, "_invoke", interrupted)
        with pytest.raises(MigrationError, match="injected_failure"):
            apply(source)
    evidence = json.loads(source.evidence_path.read_text())
    with closing(sqlite3.connect(source.database)) as connection:
        assert connection.execute("SELECT version_num FROM pc_schema_revision").fetchall() == [("s1300",)]
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='pc_candidate_heads'").fetchall() == []
    assert source.plan().state == "recovery_required"
    result = apply(source)
    assert result.backup_ref == evidence["backup"]["location"]
    assert source.verify().state == "ready"


def test_unknown_legacy_task_blocks_before_control_table_and_backup(source):
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_dream_runs (scope_id,run_id,principal_key,idempotency_key,request_digest,operation,status,accepted_at,generation,request_generation,payload) VALUES ('scope','run','principal','key','digest','derive_skill','queued',0,0,0,?)",
            (b"{}",),
        )
    before = hashlib.sha256(source.database.read_bytes()).hexdigest()
    with pytest.raises(MigrationError, match="unsupported_task_format"):
        source.plan()
    assert hashlib.sha256(source.database.read_bytes()).hexdigest() == before
    assert not source.evidence_path.exists()


def test_incompatible_processing_declaration_blocks_before_writes(source):
    source.configuration["processing_manifest"]["capabilities"] = ["unknown-family"]
    before = dump(source.database)
    with pytest.raises(MigrationError, match="incompatible_target_processing_manifest"):
        source.plan()
    assert dump(source.database) == before
    assert not source.evidence_path.exists()


def test_unknown_database_object_is_not_ignored(source):
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute("CREATE TABLE user_extension (id INTEGER PRIMARY KEY)")
    with pytest.raises(MigrationError, match="unknown_baseline"):
        source.plan()
    assert not source.evidence_path.exists()


def test_recorded_head_does_not_authorize_schema_repair_at_startup(source):
    apply(source)
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute("DROP INDEX ix_pc_dream_runs_proposal_fingerprint")
    before = dump(source.database)
    with pytest.raises(MigrationError, match="incompatible_schema"):
        source.verify()
    engine = create_engine(f"sqlite:///{source.database}")
    try:
        with engine.connect() as connection, pytest.raises(MigrationError, match="incompatible_schema"):
            check_dream_schema(connection)
    finally:
        engine.dispose()
    assert dump(source.database) == before


def test_processing_change_after_upgrade_is_not_compared_to_retained_snapshot(source):
    apply(source)
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_artifact_processing_intents (binding_name,scope_id,pending_sequence,dirty_generation,clean_generation,requested_generation,handled_generation,last_auto_scan_generation,consecutive_dream_attempts) VALUES ('topic-memory-source-window','scope',1,3,1,2,1,1,4)"
        )
    assert source.verify().state == "ready"


def test_missing_full_text_rows_fail_live_verification_without_repair(source):
    apply(source)
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_artifact_fts (scope_id,family,artifact_id,revision,searchable_text) VALUES ('scope','skill','stale',1,'text')"
        )
    before = dump(source.database)
    with pytest.raises(MigrationError, match="projection_verification_failed"):
        source.verify()
    assert dump(source.database) == before


def test_optional_configuration_cannot_silently_extend_managed_database(source):
    from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.server.settings import ServerSettings

    apply(source)
    before = dump(source.database)
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{source.database}"))
    with pytest.raises(MigrationError, match="unsupported_upgrade_configuration"):
        prepare_server_storage(settings, injected_embedding=True)
    assert dump(source.database) == before


def test_code_with_separate_cache_upgrades_and_reopens_server(source, tmp_path):
    from fastapi.testclient import TestClient

    from powercontext.builtin.code import CodeConfig
    from powercontext.builtin.persistence.migrations.deployment import deployment_runner
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import McpConfig, ServerSettings

    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{source.database}"),
        code=CodeConfig(enabled=True, cache_dir=tmp_path / "code-cache"),
        mcp=McpConfig(enabled=False),
    )
    runner = deployment_runner(settings)
    assert runner.plan().source_revision == "s1300"
    apply(runner)
    assert runner.verify().readiness_scope == "server"
    app = create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        response = client.get("/v1/scopes")
        assert response.status_code == 200
        assert any(scope["scope_id"] == "scope" for scope in response.json()["items"])
        code = client.post("/v1/scopes/scope/code/query", json={"operation": {"kind": "status"}})
        assert code.status_code == 200
        assert code.json()["status"] == "disabled"
        assert code.json()["reason"] == "code_repository_unconfigured"
    assert not apply(deployment_runner(settings)).changed
    with closing(sqlite3.connect(source.database)) as connection:
        assert connection.execute("SELECT content FROM pc_artifacts WHERE artifact_id='historical'").fetchall() == [
            (b'{"history":"unchanged"}',)
        ]


@pytest.mark.parametrize("bundle", ["processing", "atomic-projection"])
def test_partial_vector_maintenance_is_rejected_before_opening_database(tmp_path, bundle):
    from powercontext.builtin.persistence.migrations.deployment import deployment_runner
    from powercontext.builtin.runtime.config import InferenceConfig
    from powercontext.server.settings import ServerSettings

    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
        inference=InferenceConfig(
            embedding_model="openai:fixture", embedding_profile_id="fixture", embedding_dimension=3
        ),
    )
    with pytest.raises(MigrationError, match="unsupported_upgrade_configuration"):
        deployment_runner(settings, bundle=bundle)
    assert list(tmp_path.iterdir()) == []


def test_captured_oceanbase_baseline_preserves_ordered_audit_cursor():
    import copy

    from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema

    root = Path(__file__).parents[3]
    inventory = json.loads(
        (
            root / "src/powercontext/builtin/persistence/migration_resources/server_baselines/1.3.0/oceanbase.json"
        ).read_text()
    )["inventory"]
    schema = MySQLSchema(server_bundle())
    assert schema.identify(inventory) == "s1300"
    changed = copy.deepcopy(inventory)
    changed["tables"]["pc_access_audit"]["auto_increment_mode"] = "noorder"
    with pytest.raises(MigrationError, match="incompatible_schema"):
        schema.identify(changed)


def test_unregistered_processing_intent_blocks_before_any_upgrade(source):
    with closing(sqlite3.connect(source.database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_artifact_processing_intents (binding_name,scope_id,pending_sequence,dirty_generation,clean_generation,requested_generation,handled_generation,last_auto_scan_generation) VALUES ('unknown-handler','scope',1,0,0,1,0,0)"
        )
    before = dump(source.database)
    with pytest.raises(MigrationError, match="unsupported_task_format"):
        source.plan()
    assert dump(source.database) == before
    assert not source.evidence_path.exists()
