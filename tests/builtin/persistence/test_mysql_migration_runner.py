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
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from powercontext.builtin.persistence.migrations import mysql as mysql_migrations
from powercontext.builtin.persistence.migrations.backup import BackupCapabilities, BackupProvider
from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import production_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError, MigrationPlan
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.oceanbase import profile as oceanbase_profile
from powercontext.builtin.persistence.seekdb import SeekDBConfig


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


@pytest.fixture(params=("seekdb", "oceanbase"), ids=("seekdb", "oceanbase"))
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_BackendTarget]:
    if request.param == "seekdb":
        if os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1":
            pytest.skip("real seekdb probe not enabled")
        pytest.importorskip("pylibseekdb")
        yield _BackendTarget(SeekDBConfig(path=tmp_path / "seekdb"), tmp_path / "evidence")
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
    with _oceanbase_scratch_target(config, tmp_path / "evidence") as target:
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
    assert result.revision == "p0003"
    replacement = backend.runner()
    assert replacement.plan().state == "ready"
    assert replacement.verify().revision == "p0003"
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
    assert state["revision"] == "p0003"
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
    assert state["revision"] == "p0003"
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
    assert replacement.verify().revision == "p0003"
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
    assert replacement.verify().revision == "p0003"
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
    assert replacement.verify().revision == "p0003"
    assert _read_state(backend)["content"] == [(b'{"content":"preserved"}',)]


def test_mysql_completed_head_with_an_interrupted_run_requires_exact_acknowledgement(backend: _BackendTarget) -> None:
    if not isinstance(backend.config, OceanBaseConfig):
        pytest.skip("embedded seekdb closes its engine and has no remote in-flight run receipt")
    _load_baseline(backend)
    assert _apply(backend.runner(), policy="manual").state == "ready"
    assert backend.runner().verify().revision == "p0003"

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
    assert before["revision"] == "p0003"
    assert before["tags"] == [("Confirmed",)]
    replacement = backend.runner(backup_factory=_forbidden_automatic_backup)
    recovery = replacement.plan()
    assert recovery.state == "recovery_required"
    assert recovery.source_revision == "p0003"
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
    assert backend.runner().verify().revision == "p0003"
    assert not backend.runner().apply().changed
    assert "pending_run_id" not in (backend.runner().plan().coordination or {})
