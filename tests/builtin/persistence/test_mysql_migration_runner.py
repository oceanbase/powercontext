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

"""Native MySQL-mode maintenance acceptance on isolated backend targets."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from typer.testing import CliRunner

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.persistence.migrations import mysql as mysql_migrations
from powercontext.builtin.persistence.migrations.backup import BackupCapabilities, BackupProvider
from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import deployment_runner, production_bundle, server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError, MigrationPlan
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner
from powercontext.builtin.persistence.migrations.mysql_schema import mysql_inventory
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.oceanbase import profile as oceanbase_profile
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.topic_memory_recovery_schema import recovery_statements
from powercontext.builtin.runtime.config import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from powercontext.server.settings import ServerSettings


@dataclass(frozen=True)
class _BackendTarget:
    config: SeekDBConfig | OceanBaseConfig
    evidence_directory: Path

    def connections(self, *, acknowledge_previous_run: str | None = None) -> MaintenanceConnections:
        return MaintenanceConnections(
            self.config,
            evidence_directory=self.evidence_directory,
            lock_coordination="single-host" if isinstance(self.config, OceanBaseConfig) else None,
            acknowledge_previous_run=acknowledge_previous_run,
        )

    def runner(self, *, backup_factory=None, acknowledge_previous_run: str | None = None) -> MySQLMigrationRunner:
        return MySQLMigrationRunner(
            self.connections(acknowledge_previous_run=acknowledge_previous_run),
            production_bundle(),
            evidence_directory=self.evidence_directory,
            backup_factory=backup_factory,
        )


@contextmanager
def _oceanbase_scratch_target(config: OceanBaseConfig, evidence_directory: Path) -> Iterator[_BackendTarget]:
    # Never adopt or drop an existing database: only a successful CREATE grants ownership.
    url = make_url(config.url.get_secret_value())
    database = f"pc_migration_probe_{uuid4().hex}"
    owned = False

    async def manage(*, create: bool) -> None:
        nonlocal owned
        oceanbase_profile._register_official_dialect()
        engine = create_async_engine(
            config.url.get_secret_value(),
            connect_args={"init_command": "SET autocommit = 0"},
            echo=False,
            hide_parameters=True,
            poolclass=NullPool,
        )
        try:
            async with engine.connect() as connection:
                current = (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one()
                if current != url.database:
                    pytest.fail("OceanBase scratch management database identity does not match its URL")
                statement = f"CREATE DATABASE `{database}`" if create else f"DROP DATABASE `{database}`"
                await connection.exec_driver_sql(statement)
                owned = create
                await connection.commit()
        finally:
            await engine.dispose()

    try:
        try:
            asyncio.run(manage(create=True))
        except Exception:
            pytest.fail("OceanBase isolated test database creation failed", pytrace=False)
        child_url = url.set(database=database)
        child = config.model_copy(update={"url": SecretStr(child_url.render_as_string(hide_password=False))})
        yield _BackendTarget(child, evidence_directory)
    finally:
        if owned:
            try:
                asyncio.run(manage(create=False))
            except Exception:
                pytest.fail("OceanBase owned test database cleanup failed", pytrace=False)


@contextmanager
def _seekdb_scenario_lifetime(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    binding = pytest.importorskip("pylibseekdb")
    open_instance = binding.aopen
    instances = {}

    async def open_with_scenario_lifetime(path: str):
        # Keep a real native lease across this scenario's independent
        # maintenance connections. Rapid last-client exits in seekdb 1.4
        # can strand a background DDL lock on restart. Engine restart and
        # cross-process ownership have separate connection acceptance tests.
        # Acquire lazily so inspecting a missing target still creates nothing.
        if path not in instances:
            instances[path] = await open_instance(path)
        return await open_instance(path)

    monkeypatch.setattr(binding, "aopen", open_with_scenario_lifetime)
    try:
        yield
    finally:
        for instance in instances.values():
            instance.close()


@pytest.fixture(params=("seekdb", "oceanbase"), ids=("seekdb", "oceanbase"))
def backend(
    request: pytest.FixtureRequest,
    short_tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_BackendTarget]:
    if os.environ.get("POWERCONTEXT_TEST_MIGRATION_SQL_LOG") == "1":
        caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
    if request.param == "seekdb":
        if os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1":
            pytest.skip("real seekdb probe not enabled")
        with _seekdb_scenario_lifetime(monkeypatch):
            yield _BackendTarget(SeekDBConfig(path=short_tmp_path / "seekdb"), short_tmp_path / "evidence")
        return
    value = os.environ.get("POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL")
    if not value:
        pytest.skip("real OceanBase probe not enabled")
    try:
        config = OceanBaseConfig(url=SecretStr(value))
    except ValidationError:
        pytest.fail("OceanBase probe requires a valid official async-dialect URL", pytrace=False)
    url = make_url(config.url.get_secret_value())
    if not url.database or not url.database.startswith("pc_migration_probe_"):
        pytest.fail("OceanBase probe requires a dedicated pc_migration_probe_* management database", pytrace=False)
    # Driver query options must not override the generated child database name.
    if any(key.lower() in {"db", "database"} for key in url.query):
        pytest.fail("OceanBase probe URL must not override the selected database", pytrace=False)
    with _oceanbase_scratch_target(config, short_tmp_path / "evidence") as target:
        yield target


def _forbidden_automatic_backup(_connection, _identity, _directory):
    provider = Mock(spec=BackupProvider)
    for name in ("capabilities", "create_backup", "inspect_backup", "restore_plan"):
        getattr(provider, name).side_effect = AssertionError("This source must not invoke automatic backup")
    return provider


def _load_baseline(backend: _BackendTarget) -> None:
    bundle = production_bundle()
    schema = json.loads((bundle.snapshot_directory / "schemas/pre_dream.json").read_text())

    def load(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        for statement in schema["mysql"]:
            connection.exec_driver_sql(statement)
        connection.execute(
            text(
                "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
                "VALUES ('s','memory','m',1,:data)"
            ),
            {"data": b'{"content":"preserved"}'},
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) VALUES ('s','memory','m',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_tags (scope_id,family,artifact_id,target_type,target_id,tag_key_hash,tag_key,tag,assigned_at) "
            "VALUES ('s','memory','m','artifact','m',UNHEX(REPEAT('00',32)),'keep','Keep','2026-09-28 12:00:00')"
        )
        connection.commit()

    backend.connections().run(load, writable=True)


def _apply(runner: MySQLMigrationRunner, *, policy: Literal["auto", "manual", "skip"] = "auto"):
    plan = runner.plan(backup_policy=policy)
    return runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy=policy,
        backup_confirmed=policy == "manual",
        accept_no_backup=policy == "skip",
        backup_ref="operator://manual-backup" if policy == "manual" else None,
    )


def _acknowledge_known_completed_interruption(
    backend: _BackendTarget, plan: MigrationPlan, *, backup_factory=None
) -> MySQLMigrationRunner:
    # Only these tests' post-response/post-commit fault injections establish
    # completion. An arbitrary interrupted remote operation cannot auto-resume.
    pending = (plan.coordination or {}).get("pending_run_id")
    if isinstance(backend.config, OceanBaseConfig):
        assert pending
    else:
        assert pending is None
    return backend.runner(backup_factory=backup_factory, acknowledge_previous_run=pending)


def _read_state(backend: _BackendTarget):
    def inspect(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]):
        assert connection is not None
        tables = {name for (name,) in connection.exec_driver_sql("SHOW TABLES")}
        revision = (
            connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar_one_or_none()
            if "pc_schema_revision" in tables
            else None
        )
        # The frozen p0001-p0003 bundle predates the unified Candidate table names.
        columns = {
            name: {row[0] for row in connection.exec_driver_sql(f"SHOW COLUMNS FROM {name}")}
            for name in ("pc_artifacts", "pc_artifact_candidate_versions")
            if name in tables
        }
        content = (
            connection.exec_driver_sql("SELECT content FROM pc_artifacts").all() if "pc_artifacts" in tables else []
        )
        tags = (
            connection.exec_driver_sql("SELECT tag FROM pc_artifact_tags").all() if "pc_artifact_tags" in tables else []
        )
        return {"tables": tables, "revision": revision, "columns": columns, "content": content, "tags": tags}

    return backend.connections().run(inspect)


def test_mysql_topic_bundle_refuses_managed_server_storage(backend: _BackendTarget) -> None:
    from powercontext.builtin.persistence.migrations.topic_memory_recovery import TopicMemoryRecoveryMigrationRunner

    def seed(connection, _identity, _guard):
        connection.exec_driver_sql("CREATE TABLE pc_schema_revision (version_num VARCHAR(32) PRIMARY KEY NOT NULL)")
        connection.exec_driver_sql("INSERT INTO pc_schema_revision VALUES ('unknown-future')")
        for statement in recovery_statements("budget_v0", "mysql"):
            connection.exec_driver_sql(statement)
        connection.commit()

    backend.connections().run(seed, writable=True)

    def snapshot(connection, _identity, _guard):
        assert connection is not None
        return mysql_inventory(connection)

    before = backend.connections().run(snapshot)
    runner = TopicMemoryRecoveryMigrationRunner(
        backend.config,
        evidence_directory=backend.evidence_directory,
        lock_coordination="single-host" if isinstance(backend.config, OceanBaseConfig) else None,
    )
    with pytest.raises(MigrationError, match="unsupported_target"):
        runner.plan()
    after = backend.connections().run(snapshot)
    assert after == before
    assert not list(backend.evidence_directory.rglob("topic-memory-recovery.json"))


def test_mysql_managed_identity_initialization_requires_no_ddl(backend: _BackendTarget) -> None:
    from powercontext.builtin.persistence.oceanbase import OceanBaseProfile
    from powercontext.builtin.persistence.seekdb import SeekDBProfile
    from powercontext.server.identity import ServerIdentityRepository

    _load_full_server_baseline(backend)
    _apply(_full_server_runner(backend), policy="manual")

    async def scenario():
        opened = (
            OceanBaseProfile.open(backend.config, tables=())
            if isinstance(backend.config, OceanBaseConfig)
            else SeekDBProfile.open(backend.config, tables=())
        )
        async with opened as profile:

            @event.listens_for(profile.database.engine.sync_engine, "before_cursor_execute")
            def reject_ddl(_connection, _cursor, statement, _parameters, _context, _executemany):
                assert not statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ")), (
                    "Managed identity initialization must not issue DDL"
                )

            repository = ServerIdentityRepository(profile.database)
            await repository.initialize()
            original = await repository.load_or_create()
            assert await repository.load_or_create() == original
            rotated = await repository.rotate()
            assert rotated != original and await repository.load_or_create() == rotated

    asyncio.run(scenario())
    assert _full_server_runner(backend).verify().state == "ready"


def test_mysql_maintenance_commits_rolls_back_and_reopens_for_ddl(backend: _BackendTarget) -> None:
    def create(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        connection.exec_driver_sql("CREATE TABLE maintenance_probe (id INT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO maintenance_probe VALUES (1)")
        connection.commit()
        connection.exec_driver_sql("INSERT INTO maintenance_probe VALUES (2)")

    backend.connections().run(create, writable=True)

    def change(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        assert connection.exec_driver_sql("SELECT id FROM maintenance_probe").all() == [(1,)]
        connection.exec_driver_sql("ALTER TABLE maintenance_probe ADD COLUMN value INT DEFAULT 7")
        connection.exec_driver_sql("INSERT INTO maintenance_probe (id) VALUES (3)")
        connection.commit()
        connection.exec_driver_sql("INSERT INTO maintenance_probe (id) VALUES (4)")

    backend.connections().run(change, writable=True)

    def inspect(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        assert connection.exec_driver_sql("SELECT id, value FROM maintenance_probe ORDER BY id").all() == [
            (1, 7),
            (3, 7),
        ]

    backend.connections().run(inspect)


def test_mysql_initialization_and_shared_evidence_noop(backend: _BackendTarget) -> None:
    runner = backend.runner()
    plan = runner.plan()
    assert plan.state == "uninitialized"
    assert plan.coordination is not None
    assert plan.coordination["mode"] == (
        "engine-directory" if isinstance(backend.config, SeekDBConfig) else "single-host"
    )
    if isinstance(backend.config, SeekDBConfig):
        assert not backend.config.path.exists()
    result = _apply(runner)
    assert result.state == "ready"
    assert result.changed
    assert result.backup_state == "not_required"
    assert result.revision == "p0004"
    replacement = backend.runner()
    assert replacement.plan().state == "ready"
    assert replacement.verify().revision == "p0004"
    assert not replacement.apply().changed


@pytest.mark.parametrize("policy", ["manual", "skip"])
def test_mysql_operator_backup_choices_preserve_baseline_data_without_fork(
    backend: _BackendTarget, policy: Literal["manual", "skip"]
) -> None:
    _load_baseline(backend)
    runner = backend.runner(backup_factory=_forbidden_automatic_backup)
    plan = runner.plan(backup_policy=policy)
    assert plan.source_revision == "p0001"
    assert plan.adopt_baseline
    with pytest.raises(MigrationError) as error:
        runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy=policy)
    assert error.value.code == "backup_confirmation_required"
    assert _read_state(backend)["revision"] is None
    result = _apply(runner, policy=policy)
    assert result.backup_state == ("user_confirmed" if policy == "manual" else "skipped")
    state = _read_state(backend)
    assert state["revision"] == "p0004"
    assert state["content"] == [(b'{"content":"preserved"}',)]
    assert state["tags"] == [("Keep",)]
    assert "memory_citations" in state["columns"]["pc_artifacts"]
    assert "memory_citations" in state["columns"]["pc_artifact_candidate_versions"]
    assert backend.runner().verify().state == "ready"


@pytest.mark.parametrize("table_fork", [False, True])
def test_mysql_unsupported_backup_does_not_write_source_schema(backend: _BackendTarget, table_fork: bool) -> None:
    _load_baseline(backend)
    before = _read_state(backend)

    def advertised_table_fork(connection, identity, directory):
        provider = _forbidden_automatic_backup(connection, identity, directory)
        provider.capabilities.side_effect = None
        provider.capabilities.return_value = BackupCapabilities(
            available=True, method="fork_table", product=identity.product, recovery_workflow_accepted=True
        )
        return provider

    runner = backend.runner(backup_factory=advertised_table_fork if table_fork else None)
    plan = runner.plan()
    assert not plan.backup_available
    with pytest.raises(MigrationError) as error:
        _apply(runner)
    assert error.value.code == "backup_unsupported"
    assert _read_state(backend) == before
    assert not list(backend.evidence_directory.rglob("maintenance.json"))


class _InterruptedDDL(BaseException):
    pass


def _interrupt_after_ddl(
    runner: MySQLMigrationRunner, prefix: str, *, policy: Literal["auto", "manual", "skip"] = "manual"
) -> None:
    def interrupt(_connection, _cursor, statement: str, _parameters, _context, _executemany) -> None:
        if statement.lstrip().upper().startswith(prefix.upper()):
            raise _InterruptedDDL

    event.listen(Engine, "after_cursor_execute", interrupt)
    try:
        with pytest.raises(_InterruptedDDL):
            _apply(runner, policy=policy)
    finally:
        event.remove(Engine, "after_cursor_execute", interrupt)


def _full_server_runner(
    backend: _BackendTarget, *, acknowledge_previous_run: str | None = None
) -> MySQLMigrationRunner:
    return MySQLMigrationRunner(
        backend.connections(acknowledge_previous_run=acknowledge_previous_run),
        server_bundle(),
        evidence_directory=backend.evidence_directory,
        configuration={
            "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
            "embedding_enabled": False,
        },
    )


@pytest.mark.parametrize(
    ("origin", "dimension", "code"),
    [("released", 3, False), ("empty", 4, True)],
)
def test_mysql_vector_upgrade_preserves_space_and_registered_indexes(
    backend: _BackendTarget, tmp_path: Path, origin: str, dimension: int, code: bool
) -> None:
    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.code import CodeConfig
    from powercontext.builtin.persistence.migrations.server_readiness import check_server_schema, prepare_server_storage
    from powercontext.builtin.persistence.topic_memory_index import topic_memory_embedding_profile_fingerprint
    from powercontext.builtin.runtime.config import InferenceConfig

    profile = EmbeddingProfile(profile_id="native-vectors", model="fixture", dimension=dimension)
    bundle = server_bundle(embedding_profile=profile)
    settings = ServerSettings(
        database=backend.config,
        code=CodeConfig(enabled=code, cache_dir=tmp_path / "code-cache"),
        inference=InferenceConfig(
            embedding_model=profile.model,
            embedding_profile_id=profile.profile_id,
            embedding_dimension=dimension,
        ),
    )
    runner = cast(
        MySQLMigrationRunner,
        deployment_runner(
            settings,
            evidence_dir=backend.evidence_directory,
            lock_coordination="single-host" if isinstance(backend.config, OceanBaseConfig) else None,
        ),
    )
    if origin == "released":

        def initialize_source(connection, _identity, _guard):
            baseline = runner.schema.recovery(mysql_inventory(connection), None)
            for statement in baseline.pending_statements:
                runner.schema.execute(connection, statement, runner.configuration)
            connection.execute(
                text("INSERT INTO pc_topic_memory_retrieval_shape VALUES (1,'hybrid',:profile)"),
                {"profile": topic_memory_embedding_profile_fingerprint(profile)},
            )
            connection.commit()

        backend.connections().run(initialize_source, writable=True)
    assert runner.plan(backup_policy="manual").source_revision == ("s1300" if origin == "released" else None)
    assert _apply(runner, policy="manual").revision == bundle.head
    assert not _apply(runner, policy="manual").changed
    assert runner.verify().state == "ready"
    prepare_server_storage(settings)

    def verify(connection, _identity, _guard):
        check_server_schema(connection)
        inventory = mysql_inventory(connection)
        for name in ("pc_atomic_memory_current", "pc_topic_memory_vector_topics", "pc_topic_memory_vector_chunks"):
            table = inventory["tables"][name]
            assert table["columns"]["embedding"]["type"] == ["vector", [str(dimension)]]
            assert any(index.get("kind") == "vector" for index in table["indexes"].values())
        declaration = connection.exec_driver_sql("SELECT embedding_profile FROM pc_server_storage_profile").scalar_one()
        assert json.loads(declaration) == profile.model_dump(mode="json")
        return inventory, declaration

    before = backend.connections().run(verify)
    evidence = {path: path.read_bytes() for path in backend.evidence_directory.rglob("maintenance.json")}
    assert evidence
    changed_settings = settings.model_copy(
        update={"inference": settings.inference.model_copy(update={"embedding_profile_id": "different-space"})}
    )
    changed = deployment_runner(
        changed_settings,
        evidence_dir=backend.evidence_directory,
        lock_coordination="single-host" if isinstance(backend.config, OceanBaseConfig) else None,
    )
    for operation in (changed.plan, changed.verify, changed.apply):
        with pytest.raises(MigrationError) as error:
            operation()
        assert error.value.code == "embedding_profile_mismatch"
    assert backend.connections().run(verify) == before
    assert {path: path.read_bytes() for path in backend.evidence_directory.rglob("maintenance.json")} == evidence
    assert runner.verify().state == "ready"


@pytest.mark.parametrize("backend", ("oceanbase",), indirect=True)
def test_oceanbase_server_upgrade_with_separate_code_cache_preserves_history(
    backend: _BackendTarget, tmp_path: Path
) -> None:
    from powercontext.builtin.code import CodeConfig
    from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage

    _load_full_server_baseline(backend)
    settings = ServerSettings(
        database=backend.config,
        code=CodeConfig(enabled=True, cache_dir=tmp_path / "code-cache"),
    )
    runner = cast(
        MySQLMigrationRunner,
        deployment_runner(settings, evidence_dir=backend.evidence_directory, lock_coordination="single-host"),
    )
    assert runner.plan(backup_policy="manual").source_revision == "s1300"
    result = _apply(runner, policy="manual")
    assert result.readiness_scope == "server" and result.revision == "v1_4_1_006"
    assert runner.verify().state == "ready"
    prepare_server_storage(settings)
    assert not _apply(runner, policy="manual").changed

    def history(connection, identity, guard):
        guard()
        return connection.exec_driver_sql(
            "SELECT content FROM pc_artifacts WHERE scope_id='historical' AND artifact_id='preserved'"
        ).scalar_one()

    assert backend.connections().run(history) == b'{"history":"unchanged"}'


@pytest.mark.parametrize("backend", ("seekdb",), indirect=True)
@pytest.mark.parametrize("origin", ("empty", "released"))
def test_embedded_code_migration_preserves_registered_tables_and_reopens_server(
    backend: _BackendTarget, tmp_path: Path, origin: str
) -> None:
    from powercontext.builtin.code import CodeConfig
    from powercontext.builtin.persistence.dream_schema import check_dream_schema
    from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage

    bundle = server_bundle()
    profile = json.loads((bundle.snapshot_directory / "profiles/seekdb-code.json").read_text())
    names = tuple(profile["tables"])
    if origin == "released":
        _load_full_server_baseline(backend)

        def populate(connection, _identity, _guard):
            for statement in profile["statements"]:
                connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
            connection.exec_driver_sql(
                "INSERT INTO pc_code_generations VALUES (%s,%s,%s,0,0,0)",
                ("a" * 32, "b" * 64, "c" * 64),
            )
            connection.commit()

        backend.connections().run(populate)
    settings = ServerSettings(
        database=backend.config,
        code=CodeConfig(enabled=True, cache_dir=tmp_path / "code-cache"),
    )
    runner = cast(MySQLMigrationRunner, deployment_runner(settings, evidence_dir=backend.evidence_directory))
    plan = runner.plan(backup_policy="manual")
    assert plan.source_revision == ("s1300" if origin == "released" else None)
    result = _apply(runner, policy="manual")
    assert result.state == "ready" and result.revision == bundle.head and result.readiness_scope == "server"
    assert runner.verify().state == "ready"
    prepare_server_storage(settings)

    def read(connection, _identity, _guard):
        check_dream_schema(connection)
        inventory = mysql_inventory(connection)
        assert {name: inventory["tables"][name] for name in names} == profile["tables"]
        return connection.exec_driver_sql("SELECT * FROM pc_code_generations").all()

    assert backend.connections().run(read) == (
        [("a" * 32, "b" * 64, "c" * 64, 0, 0, 0)] if origin == "released" else []
    )
    assert not _apply(runner, policy="manual").changed

    def damage(connection, _identity, _guard):
        connection.exec_driver_sql("DROP INDEX ix_code_name ON pc_code_nodes")

    backend.connections().run(damage)
    with pytest.raises(MigrationError):
        prepare_server_storage(settings)
    with pytest.raises(MigrationError):
        backend.connections().run(read)


@pytest.mark.parametrize("backend", ("seekdb",), indirect=True)
def test_code_service_cannot_add_unregistered_tables_to_managed_storage(
    backend: _BackendTarget, tmp_path: Path
) -> None:
    from powercontext.builtin.code import CodeConfig, CodeError
    from powercontext.builtin.code.configuration import open_code_service

    _apply(_full_server_runner(backend), policy="manual")

    async def open_service():
        async with open_code_service(CodeConfig(enabled=True, cache_dir=tmp_path / "code-cache"), backend.config):
            pytest.fail("Code cannot implicitly expand the managed Server schema")

    with pytest.raises(CodeError, match="code_storage_unavailable"):
        asyncio.run(open_service())
    assert _full_server_runner(backend).verify().state == "ready"


def test_mysql_registered_processing_mode_switch_preserves_revision_and_history(backend: _BackendTarget) -> None:
    _load_full_server_baseline(backend)
    _apply(_full_server_runner(backend), policy="manual")
    settings = ServerSettings(
        database=backend.config,
        runtime=RuntimeConfig(artifact_processing_supervisor_mode="dedicated"),
    )
    runner = deployment_runner(
        settings,
        bundle="processing",
        evidence_dir=backend.evidence_directory,
        lock_coordination="single-host" if isinstance(backend.config, OceanBaseConfig) else None,
        migration_id="native-mode-switch",
    )
    plan = runner.plan(backup_policy="manual")
    assert plan.state == "migration_required"
    result = runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="manual",
        backup_confirmed=True,
        backup_ref="operator://mode-backup",
    )
    assert result.backup_state == "user_confirmed" and result.backup_ref == "operator://mode-backup"
    assert runner.verify().state == "ready"
    assert not runner.apply().changed

    def inspect_history(connection, identity, guard):
        return (
            connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar_one(),
            json.loads(
                connection.exec_driver_sql(
                    "SELECT config_manifest FROM pc_artifact_processing_schema WHERE singleton=1"
                ).scalar_one()
            )["mode"],
            connection.exec_driver_sql(
                "SELECT content FROM pc_artifacts WHERE scope_id='historical' AND artifact_id='preserved'"
            ).scalar_one(),
        )

    assert backend.connections().run(inspect_history) == ("v1_4_1_006", "dedicated", b'{"history":"unchanged"}')

    (main_path,) = backend.evidence_directory.glob("*/maintenance.json")
    evidence = json.loads(main_path.read_text())
    evidence["release_checksums"]["s1402"] = "0" * 64
    main_path.write_text(json.dumps(evidence))
    original_evidence = {path: path.read_bytes() for path in backend.evidence_directory.rglob("maintenance.json")}
    for inspect in (runner.plan, runner.verify, runner.apply):
        with pytest.raises(MigrationError, match="checksum_conflict"):
            inspect()
    assert backend.connections().run(inspect_history) == ("v1_4_1_006", "dedicated", b'{"history":"unchanged"}')
    assert {
        path: path.read_bytes() for path in backend.evidence_directory.rglob("maintenance.json")
    } == original_evidence


def test_mysql_atomic_compatibility_command_uses_shared_server_upgrade(
    backend: _BackendTarget, monkeypatch, capsys
) -> None:
    _load_full_server_baseline(backend)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", backend.config.kind)
    if isinstance(backend.config, SeekDBConfig):
        monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(backend.config.path))
    else:
        monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", backend.config.url.get_secret_value())
    cli = create_cli([app])
    arguments = [
        "server",
        "atomic-memory-migrate",
        "--evidence-dir",
        str(backend.evidence_directory),
        "--lock-coordination",
        "single-host",
    ]

    def invoke(options):
        # Pytest live SQL logging suspends capture during emit. Keep its
        # stream switching outside Typer's independent stdout isolation.
        with capsys.disabled():
            return CliRunner().invoke(cli, [*arguments, *options])

    inspected = invoke(["--action", "plan", "--backup", "manual"])
    assert inspected.exit_code == 0, inspected.output
    plan = json.loads(inspected.output)
    assert plan["source_revision"] == "s1300" and plan["readiness_scope"] == "server"
    applied = invoke(
        [
            "--action",
            "apply",
            "--plan-id",
            plan["plan_id"],
            "--backup",
            "manual",
            "--backup-confirmed",
            "--backup-ref",
            "operator://native-atomic-backup",
            "--maintenance-confirmed",
            "--yes",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["backup_ref"] == "operator://native-atomic-backup"
    verified = invoke(["--action", "verify"])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["readiness_scope"] == "server"
    repeated = invoke(["--action", "apply", "--maintenance-confirmed"])
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.output)["changed"] is False

    def inspect_history(connection, identity, guard):
        return (
            connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar_one(),
            connection.exec_driver_sql(
                "SELECT content FROM pc_artifacts WHERE scope_id='historical' AND artifact_id='preserved'"
            ).scalar_one(),
        )

    assert backend.connections().run(inspect_history) == ("v1_4_1_006", b'{"history":"unchanged"}')


def test_mysql_registered_projection_repair_preserves_authority_and_revision(backend: _BackendTarget) -> None:
    from powercontext.builtin.persistence.migrations.atomic_projection import AtomicProjectionMigrationRunner

    full = _full_server_runner(backend)
    plan = full.plan()
    full.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    content = (
        AtomicMemoryContent(kind="fact", text="Preserve immutable nebula evidence.")
        .model_dump_json(by_alias=True)
        .encode()
    )

    def seed(connection, identity, guard):
        connection.exec_driver_sql(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('scope','title','summary','scope','title','summary',1)"
        )
        connection.execute(
            text(
                "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
                "VALUES ('scope','atomic-memory','fact',1,:content)"
            ),
            {"content": content},
        )
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) "
            "VALUES ('scope','atomic-memory','fact',1)"
        )
        connection.commit()

    backend.connections().run(seed, writable=True)
    for inspect_state in (full.plan, full.verify):
        with pytest.raises(MigrationError, match="projection_verification_failed"):
            inspect_state()
    runner = AtomicProjectionMigrationRunner(
        backend.connections(),
        full.bundle,
        configuration=full.configuration,
        evidence_directory=backend.evidence_directory,
        batch_size=1,
    )
    plan = runner.plan(backup_policy="manual")
    assert plan.state == "migration_required"
    result = runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="manual",
        backup_confirmed=True,
        backup_ref="operator://projection-recovery",
    )
    assert result.backup_state == "user_confirmed" and result.backup_ref == "operator://projection-recovery"
    assert runner.verify().state == "ready"
    assert full.verify().state == "ready"
    assert not runner.apply().changed

    def inspect_content(connection, identity, guard):
        return (
            connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar_one(),
            connection.exec_driver_sql("SELECT content FROM pc_artifacts WHERE artifact_id='fact'").scalar_one(),
            connection.exec_driver_sql(
                "SELECT text FROM pc_atomic_memory_current WHERE artifact_id='fact'"
            ).scalar_one(),
        )

    assert backend.connections().run(inspect_content) == ("v1_4_1_006", content, "Preserve immutable nebula evidence.")


@pytest.mark.parametrize("origin", ("empty", "released"))
def test_mysql_full_server_registers_portable_storage_and_retains_timestamp_precision(
    backend: _BackendTarget, origin: str
) -> None:
    if origin == "released":
        _load_full_server_baseline(backend)
    runner = _full_server_runner(backend)
    try:
        assert _apply(runner, policy="manual").state == "ready"
    finally:
        _capture_portable_schema(backend)
    assert runner.verify().state == "ready"
    # Native DATETIME stores UTC values without timezone metadata.
    timestamp = datetime(2026, 10, 24, 12, 34, 56, 123456, tzinfo=UTC).replace(tzinfo=None)

    def verify(connection, _identity, _guard):
        inventory = mysql_inventory(connection)
        receipt = inventory["tables"]["pc_portable_restore_receipts"]
        assert receipt["columns"]["projections_ready"]["type"] == ["tinyint", ["1"]]
        for table, column in (
            ("pc_artifacts", "created_at"),
            ("pc_profile_policies", "updated_at"),
            ("pc_topic_memory_revision_publications", "published_at"),
            ("pc_artifact_tags", "assigned_at"),
        ):
            assert inventory["tables"][table]["columns"][column]["type"] == ["datetime", ["6"]]
        connection.execute(
            text(
                "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content,created_at) "
                "VALUES ('precision','custom-family','retained',1,:content,:created_at)"
            ),
            {"content": b'{"history":"retained"}', "created_at": timestamp},
        )
        connection.execute(
            text(
                "INSERT INTO pc_portable_restore_receipts "
                "(bundle_id,format_version,total_digest,record_count,status,inserted,already_present,projections_ready,updated_at) "
                "VALUES ('receipt',1,'sha256:fixture',0,'ready',0,0,1,:updated_at)"
            ),
            {"updated_at": timestamp},
        )
        connection.commit()
        assert (
            connection.exec_driver_sql(
                "SELECT projections_ready FROM pc_portable_restore_receipts WHERE bundle_id='receipt'"
            ).scalar_one()
            == 1
        )
        assert (
            connection.exec_driver_sql(
                "SELECT created_at FROM pc_artifacts WHERE scope_id='precision' AND artifact_id='retained'"
            ).scalar_one()
            == timestamp
        )

    backend.connections().run(verify, writable=True)
    assert not _apply(runner, policy="manual").changed


def _capture_portable_schema(backend: _BackendTarget) -> None:
    """Keep content-free native reflection even when a new DDL prefix is rejected."""

    def capture(connection, _identity, _guard):
        names = {row[0] for row in connection.exec_driver_sql("SHOW FULL TABLES")}
        tables = {}
        for name in (
            "pc_portable_restore_receipts",
            "pc_artifacts",
            "pc_profile_policies",
            "pc_topic_memory_revision_publications",
            "pc_artifact_tags",
        ):
            if name in names:
                tables[name] = connection.exec_driver_sql(f"SHOW CREATE TABLE `{name}`").one()[1]
        return {"backend_version": connection.exec_driver_sql("SELECT VERSION()").scalar_one(), "tables": tables}

    try:
        diagnostic = backend.connections().run(capture)
    except Exception as error:
        diagnostic = {"capture_error_type": type(error).__name__}
    backend.evidence_directory.mkdir(parents=True, exist_ok=True)
    (backend.evidence_directory / "portable-schema.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_mysql_full_server_resumes_interrupted_initialization_before_manifest_seed(backend: _BackendTarget) -> None:
    runner = _full_server_runner(backend)
    _interrupt_after_ddl(runner, "CREATE TABLE `pc_artifact_candidate_versions`", policy="auto")
    (journal,) = backend.evidence_directory.rglob("maintenance.json")
    interrupted = json.loads(journal.read_text())
    assert interrupted["last_revision"] is None
    assert interrupted["pending_revision"] == "s1300"
    recovery = _full_server_runner(backend).plan()
    assert recovery.state == "recovery_required" and recovery.source_revision is None
    token = (recovery.coordination or {}).get("pending_run_id")
    # The injected failure occurs after the native DDL returned and the owned
    # connection closed, so this test can acknowledge that exact operation.
    resumed = _full_server_runner(backend, acknowledge_previous_run=token)
    result = _apply(resumed)
    assert result.state == "ready" and result.revision == resumed.bundle.head
    assert result.backup_state == "not_required"
    assert resumed.verify().state == "ready"
    completed = json.loads(journal.read_text())
    assert completed["maintenance_window_id"] == interrupted["maintenance_window_id"]
    assert completed["source_fingerprint"] == interrupted["source_fingerprint"]
    assert completed["state"] == "complete"


def _load_full_server_baseline(backend: _BackendTarget) -> None:
    runner = _full_server_runner(backend)

    def load(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]) -> None:
        assert connection is not None
        source = runner.schema.recovery(mysql_inventory(connection), None)
        assert source.revision == "s1300"
        for statement in source.pending_statements:
            runner.schema.execute(connection, statement, runner.configuration)
        connection.exec_driver_sql(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('historical','retained title','retained summary','historical','retained title','retained summary',1)"
        )
        connection.execute(
            text(
                "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
                "VALUES ('historical','custom-family','preserved',1,:content)"
            ),
            {"content": b'{"history":"unchanged"}'},
        )
        connection.commit()

    backend.connections().run(load, writable=True)


@pytest.mark.parametrize(
    ("interruption", "recorded_revision"),
    [
        ("CREATE TABLE pc_retained_dream_candidate_versions", "s1300"),
        ("ALTER TABLE pc_artifact_candidate_versions RENAME TO pc_candidate_versions", "s1300"),
        ("UPDATE pc_artifact_processing_schema SET config_manifest", "s1300"),
        ("CREATE TABLE pc_memory_artifact_archive", "s1400"),
    ],
    ids=("retained-copy", "candidate-rename", "domain-processing", "next-revision"),
)
def test_mysql_full_server_interruption_preserves_history_and_original_maintenance(
    backend: _BackendTarget, interruption: str, recorded_revision: str
) -> None:
    """Native backend recovery covers the full Server chain, not just the Artifact prototype."""

    _load_full_server_baseline(backend)
    runner = _full_server_runner(backend)
    original = runner.plan(backup_policy="manual")
    assert original.source_revision == "s1300" and original.target_revision == "v1_4_1_006"
    _interrupt_after_ddl(runner, interruption)
    journals = list(backend.evidence_directory.rglob("maintenance.json"))
    assert len(journals) == 1
    interrupted = json.loads(journals[0].read_text())
    assert interrupted["state"] == "active"
    assert interrupted["manual_ref"] == "operator://manual-backup"
    recovery = _full_server_runner(backend).plan(backup_policy="manual")
    assert recovery.state == "recovery_required"
    assert recovery.source_revision == recorded_revision
    token = (recovery.coordination or {}).get("pending_run_id")
    if isinstance(backend.config, OceanBaseConfig):
        assert token
        with pytest.raises(MigrationError, match="recovery_required"):
            _apply(_full_server_runner(backend), policy="manual")
    # The injected exception happens after the backend returned from the
    # statement and the owned connection closed. This known test interruption
    # permits the required declaration; arbitrary remote interruptions do not.
    resumed = _full_server_runner(backend, acknowledge_previous_run=token)

    result = _apply(resumed, policy="manual")

    assert result.readiness_scope == "server" and result.revision == "v1_4_1_006"
    assert result.backup_state == "user_confirmed"
    assert result.backup_ref == interrupted["manual_ref"]
    assert resumed.verify().state == "ready"
    completed = json.loads(journals[0].read_text())
    assert completed["maintenance_window_id"] == interrupted["maintenance_window_id"]
    assert completed["source_fingerprint"] == interrupted["source_fingerprint"]
    assert completed["manual_ref"] == interrupted["manual_ref"]
    assert completed["state"] == "complete"

    def inspect_history(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]):
        assert connection is not None
        return (
            connection.exec_driver_sql(
                "SELECT content FROM pc_artifacts WHERE scope_id='historical' AND artifact_id='preserved'"
            ).all(),
            connection.exec_driver_sql("SELECT title,summary FROM pc_scopes WHERE scope_id='historical'").all(),
        )

    assert backend.connections().run(inspect_history) == (
        [(b'{"history":"unchanged"}',)],
        [("retained title", "retained summary")],
    )
    assert not _apply(_full_server_runner(backend), policy="manual").changed


@pytest.mark.parametrize("backend", ("oceanbase",), indirect=True)
def test_oceanbase_full_server_client_exit_requires_acknowledgement_and_preserves_recovery(
    backend: _BackendTarget,
) -> None:
    """A dead remote client requires acknowledgement; the database engine stays running."""
    assert isinstance(backend.config, OceanBaseConfig)
    _load_full_server_baseline(backend)
    root = Path(__file__).resolve().parents[3]
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, (str(root), os.environ.get("PYTHONPATH")))),
        "POWERCONTEXT_MIGRATION_CRASH_TARGET": backend.config.url.get_secret_value(),
    }
    with (backend.evidence_directory / "crashed-client.log").open("w", encoding="utf-8") as log:
        crashed = subprocess.run(
            [
                sys.executable,
                str(root / "tests/fixtures/database_migrations/crash_after_registered_ddl.py"),
                str(backend.evidence_directory),
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
    assert crashed.returncode == 73, "The migration client did not exit at the registered DDL boundary"
    marker = json.loads((backend.evidence_directory / "client-exit.json").read_text(encoding="utf-8"))
    journals = list(backend.evidence_directory.rglob("maintenance.json"))
    assert len(journals) == 1
    interrupted = json.loads(journals[0].read_text(encoding="utf-8"))
    assert interrupted["state"] == "active" and interrupted["manual_ref"] == "operator://manual-backup"

    def client_active(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]):
        assert connection is not None
        return connection.execute(
            text("SELECT ID FROM information_schema.PROCESSLIST WHERE ID=:connection_id"),
            {"connection_id": marker["connection_id"]},
        ).first()

    deadline = time.monotonic() + 15
    while backend.connections().run(client_active) is not None:
        assert time.monotonic() < deadline, "The terminated client's remote session did not end"
        time.sleep(0.2)
    recovery = _full_server_runner(backend).plan(backup_policy="manual")
    assert recovery.state == "recovery_required" and recovery.source_revision == "s1300"
    token = (recovery.coordination or {}).get("pending_run_id")
    assert token
    with pytest.raises(MigrationError, match="recovery_required"):
        _apply(_full_server_runner(backend), policy="manual")
    # The completed DDL and vanished remote session establish this test's known
    # interruption. An arbitrary remote failure cannot be acknowledged blindly.
    resumed = _full_server_runner(backend, acknowledge_previous_run=token)
    result = _apply(resumed, policy="manual")
    assert result.state == "ready" and result.readiness_scope == "server" and result.revision == "v1_4_1_006"
    assert result.backup_state == "user_confirmed" and result.backup_ref == interrupted["manual_ref"]
    assert resumed.verify().state == "ready"
    completed = json.loads(journals[0].read_text(encoding="utf-8"))
    assert completed["state"] == "complete"
    for key in ("maintenance_window_id", "source_fingerprint", "manual_ref"):
        assert completed[key] == interrupted[key]

    def history(connection: Connection | None, _identity: BackendIdentity, _guard: Callable[[], None]):
        assert connection is not None
        return connection.exec_driver_sql(
            "SELECT content FROM pc_artifacts WHERE scope_id='historical' AND artifact_id='preserved'"
        ).scalar_one()

    assert backend.connections().run(history) == b'{"history":"unchanged"}'
    assert not _apply(_full_server_runner(backend), policy="manual").changed


def _interrupt_after_committed_citation_ddl(backend: _BackendTarget) -> None:
    _load_baseline(backend)
    _interrupt_after_ddl(backend.runner(), "ALTER TABLE pc_artifacts ADD COLUMN memory_citations")
    state = _read_state(backend)
    assert state["revision"] == "p0001"
    assert "memory_citations" in state["columns"]["pc_artifacts"]
    assert "memory_citations" not in state["columns"]["pc_artifact_candidate_versions"]
    assert state["content"] == [(b'{"content":"preserved"}',)]


def test_mysql_committed_partial_ddl_resumes_from_shared_original_evidence(backend: _BackendTarget) -> None:
    _interrupt_after_committed_citation_ddl(backend)
    replacement = backend.runner()
    recovery = replacement.plan(backup_policy="manual")
    assert recovery.state == "recovery_required"
    replacement = _acknowledge_known_completed_interruption(backend, recovery)
    assert _apply(replacement, policy="manual").state == "ready"
    state = _read_state(backend)
    assert state["revision"] == "p0004"
    assert state["content"] == [(b'{"content":"preserved"}',)]
    assert state["tags"] == [("Keep",)]
    assert replacement.verify().state == "ready"


def test_mysql_missing_original_evidence_blocks_partial_ddl_recovery(backend: _BackendTarget) -> None:
    _interrupt_after_committed_citation_ddl(backend)
    before = _read_state(backend)
    evidence_files = list(backend.evidence_directory.rglob("maintenance.json"))
    assert len(evidence_files) == 1
    evidence_files[0].unlink()
    replacement = backend.runner()
    for operation in (lambda: replacement.plan(backup_policy="manual"), replacement.verify, replacement.apply):
        with pytest.raises(MigrationError) as error:
            operation()
        assert error.value.code == "recovery_required"
        assert _read_state(backend) == before


def test_mysql_empty_source_recovers_baseline_and_citation_ddl_without_probing_backup(backend: _BackendTarget) -> None:
    _interrupt_after_ddl(
        backend.runner(backup_factory=_forbidden_automatic_backup), "CREATE TABLE pc_artifacts", policy="auto"
    )
    first = _read_state(backend)
    assert first["revision"] is None
    assert "pc_schema_revision" in first["tables"]
    assert "pc_artifacts" in first["tables"]
    assert "pc_artifact_heads" not in first["tables"]
    assert first["content"] == []
    recovery = backend.runner(backup_factory=_forbidden_automatic_backup).plan()
    _interrupt_after_ddl(
        _acknowledge_known_completed_interruption(backend, recovery, backup_factory=_forbidden_automatic_backup),
        "ALTER TABLE pc_artifacts ADD COLUMN memory_citations",
        policy="auto",
    )
    second = _read_state(backend)
    assert second["revision"] == "p0001"
    assert "memory_citations" in second["columns"]["pc_artifacts"]
    assert "memory_citations" not in second["columns"]["pc_artifact_candidate_versions"]
    recovery = backend.runner(backup_factory=_forbidden_automatic_backup).plan()
    replacement = _acknowledge_known_completed_interruption(
        backend, recovery, backup_factory=_forbidden_automatic_backup
    )
    result = _apply(replacement)
    assert result.backup_state == "not_required"
    assert replacement.verify().revision == "p0004"
    assert _read_state(backend)["content"] == []


def test_mysql_baseline_adoption_recovers_an_empty_committed_version_table(backend: _BackendTarget) -> None:
    _load_baseline(backend)
    _interrupt_after_ddl(backend.runner(), "CREATE TABLE pc_schema_revision")
    state = _read_state(backend)
    assert "pc_schema_revision" in state["tables"]
    assert state["revision"] is None
    assert state["content"] == [(b'{"content":"preserved"}',)]
    replacement = backend.runner()
    recovered = replacement.plan(backup_policy="manual")
    assert recovered.state == "recovery_required"
    assert recovered.adopt_baseline
    replacement = _acknowledge_known_completed_interruption(backend, recovered)
    assert _apply(replacement, policy="manual").state == "ready"
    assert replacement.verify().revision == "p0004"
    assert _read_state(backend)["tags"] == [("Keep",)]


def test_mysql_committed_revision_recovers_before_evidence_advance(backend: _BackendTarget, monkeypatch) -> None:
    _load_baseline(backend)
    write = mysql_migrations._Journal.write

    def interrupt(journal, evidence) -> None:
        if evidence.state == "active" and evidence.last_revision == "p0002" and evidence.pending_revision is None:
            raise _InterruptedDDL
        write(journal, evidence)

    with monkeypatch.context() as patch:
        patch.setattr(mysql_migrations._Journal, "write", interrupt)
        with pytest.raises(_InterruptedDDL):
            _apply(backend.runner(), policy="manual")
    state = _read_state(backend)
    assert state["revision"] == "p0002"
    assert "memory_citations" in state["columns"]["pc_artifacts"]
    assert "memory_citations" in state["columns"]["pc_artifact_candidate_versions"]
    replacement = backend.runner()
    recovery = replacement.plan(backup_policy="manual")
    assert recovery.source_revision == "p0002"
    replacement = _acknowledge_known_completed_interruption(backend, recovery)
    assert _apply(replacement, policy="manual").state == "ready"
    assert replacement.verify().revision == "p0004"
    assert _read_state(backend)["content"] == [(b'{"content":"preserved"}',)]


def test_mysql_completed_head_with_an_interrupted_run_requires_exact_acknowledgement(backend: _BackendTarget) -> None:
    if not isinstance(backend.config, OceanBaseConfig):
        pytest.skip("embedded seekdb closes its engine and has no remote in-flight run receipt")
    _load_baseline(backend)
    assert _apply(backend.runner(), policy="manual").state == "ready"
    assert backend.runner().verify().revision == "p0004"

    def committed_write_then_interrupt(
        connection: Connection | None, _identity: BackendIdentity, verify: Callable[[], None]
    ) -> None:
        assert connection is not None
        verify()
        connection.exec_driver_sql("UPDATE pc_artifact_tags SET tag = 'Confirmed' WHERE scope_id = 's'")
        connection.commit()
        raise _InterruptedDDL

    with pytest.raises(_InterruptedDDL):
        backend.connections().run(committed_write_then_interrupt, writable=True)
    before = _read_state(backend)
    assert before["revision"] == "p0004"
    assert before["tags"] == [("Confirmed",)]
    replacement = backend.runner(backup_factory=_forbidden_automatic_backup)
    recovery = replacement.plan()
    assert recovery.state == "recovery_required"
    assert recovery.source_revision == "p0004"
    assert recovery.revisions == ()
    pending = (recovery.coordination or {}).get("pending_run_id")
    assert pending
    for operation in (replacement.verify, replacement.apply):
        with pytest.raises(MigrationError) as blocked:
            operation()
        assert blocked.value.code == "recovery_required"
    wrong = backend.runner(backup_factory=_forbidden_automatic_backup, acknowledge_previous_run="wrong-token")
    with pytest.raises(MigrationError) as blocked:
        _apply(wrong)
    assert blocked.value.code == "recovery_required"
    assert _read_state(backend) == before
    assert (backend.runner().plan().coordination or {})["pending_run_id"] == pending
    acknowledged = backend.runner(backup_factory=_forbidden_automatic_backup, acknowledge_previous_run=pending)
    result = _apply(acknowledged)
    assert result.state == "ready"
    assert not result.changed
    assert _read_state(backend) == before
    assert backend.runner().verify().revision == "p0004"
    assert not backend.runner().apply().changed
    assert "pending_run_id" not in (backend.runner().plan().coordination or {})
