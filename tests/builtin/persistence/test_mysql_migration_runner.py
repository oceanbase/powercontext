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

"""Real seekdb maintenance acceptance on independent temporary instances."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest
from sqlalchemy import Connection, Engine, event, text

from powercontext.builtin.persistence.migrations import mysql as mysql_migrations
from powercontext.builtin.persistence.migrations.backup import BackupCapabilities, BackupProvider
from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import production_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner
from powercontext.builtin.persistence.seekdb import SeekDBConfig

pytestmark = pytest.mark.skipif(
    os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled"
)


def _connections(tmp_path: Path) -> MaintenanceConnections:
    pytest.importorskip("pylibseekdb")
    return MaintenanceConnections(SeekDBConfig(path=tmp_path / "seekdb"))


def _runner(tmp_path: Path, *, backup_factory=None) -> MySQLMigrationRunner:
    return MySQLMigrationRunner(
        _connections(tmp_path),
        production_bundle(),
        evidence_directory=tmp_path / "evidence",
        backup_factory=backup_factory,
    )


def _forbidden_automatic_backup(_connection, _identity, _directory):
    provider = Mock(spec=BackupProvider)
    for name in ("capabilities", "create_backup", "inspect_backup", "restore_plan"):
        getattr(provider, name).side_effect = AssertionError("This source must not invoke automatic backup")
    return provider


def _load_baseline(tmp_path: Path) -> None:
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

    _connections(tmp_path).run(load, writable=True)


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


def _read_state(tmp_path: Path):
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

    return _connections(tmp_path).run(inspect)


def test_seekdb_initialization_and_shared_evidence_noop(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    plan = runner.plan()
    assert plan.state == "uninitialized"
    assert not (tmp_path / "seekdb").exists()
    result = _apply(runner)
    assert result.state == "ready"
    assert result.changed
    assert result.backup_state == "not_required"
    assert result.revision == "p0003"
    replacement = _runner(tmp_path)
    assert replacement.plan().state == "ready"
    assert replacement.verify().revision == "p0003"
    assert not replacement.apply().changed


@pytest.mark.parametrize("policy", ["manual", "skip"])
def test_seekdb_operator_backup_choices_preserve_baseline_data_without_fork(
    tmp_path: Path, policy: Literal["manual", "skip"]
) -> None:
    _load_baseline(tmp_path)
    runner = _runner(tmp_path, backup_factory=_forbidden_automatic_backup)
    plan = runner.plan(backup_policy=policy)
    assert plan.source_revision == "p0001"
    assert plan.adopt_baseline
    with pytest.raises(MigrationError) as error:
        runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy=policy)
    assert error.value.code == "backup_confirmation_required"
    assert _read_state(tmp_path)["revision"] is None
    result = _apply(runner, policy=policy)
    assert result.backup_state == ("user_confirmed" if policy == "manual" else "skipped")
    state = _read_state(tmp_path)
    assert state["revision"] == "p0003"
    assert state["content"] == [(b'{"content":"preserved"}',)]
    assert state["tags"] == [("Keep",)]
    assert "memory_citations" in state["columns"]["pc_artifacts"]
    assert "memory_citations" in state["columns"]["pc_artifact_candidate_versions"]
    assert _runner(tmp_path).verify().state == "ready"


@pytest.mark.parametrize("table_fork", [False, True])
def test_seekdb_unsupported_backup_does_not_write_source_schema(tmp_path: Path, table_fork: bool) -> None:
    _load_baseline(tmp_path)
    before = _read_state(tmp_path)

    def advertised_table_fork(connection, identity, directory):
        provider = _forbidden_automatic_backup(connection, identity, directory)
        provider.capabilities.side_effect = None
        provider.capabilities.return_value = BackupCapabilities(
            available=True, method="fork_table", product="seekdb", recovery_workflow_accepted=True
        )
        return provider

    runner = _runner(tmp_path, backup_factory=advertised_table_fork if table_fork else None)
    plan = runner.plan()
    assert not plan.backup_available
    with pytest.raises(MigrationError) as error:
        _apply(runner)
    assert error.value.code == "backup_unsupported"
    assert _read_state(tmp_path) == before
    assert not (tmp_path / "evidence").exists()


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


def _interrupt_after_committed_citation_ddl(tmp_path: Path) -> None:
    _load_baseline(tmp_path)
    _interrupt_after_ddl(_runner(tmp_path), "ALTER TABLE pc_artifacts ADD COLUMN memory_citations")
    state = _read_state(tmp_path)
    assert state["revision"] == "p0001"
    assert "memory_citations" in state["columns"]["pc_artifacts"]
    assert "memory_citations" not in state["columns"]["pc_artifact_candidate_versions"]
    assert state["content"] == [(b'{"content":"preserved"}',)]


def test_seekdb_committed_partial_ddl_resumes_from_shared_original_evidence(tmp_path: Path) -> None:
    _interrupt_after_committed_citation_ddl(tmp_path)
    replacement = _runner(tmp_path)
    assert replacement.plan(backup_policy="manual").state == "recovery_required"
    assert _apply(replacement, policy="manual").state == "ready"
    state = _read_state(tmp_path)
    assert state["revision"] == "p0003"
    assert state["content"] == [(b'{"content":"preserved"}',)]
    assert state["tags"] == [("Keep",)]
    assert replacement.verify().state == "ready"


def test_seekdb_missing_original_evidence_blocks_partial_ddl_recovery(tmp_path: Path) -> None:
    _interrupt_after_committed_citation_ddl(tmp_path)
    before = _read_state(tmp_path)
    evidence_files = list((tmp_path / "evidence").rglob("maintenance.json"))
    assert len(evidence_files) == 1
    evidence_files[0].unlink()
    replacement = _runner(tmp_path)
    for operation in (lambda: replacement.plan(backup_policy="manual"), replacement.verify, replacement.apply):
        with pytest.raises(MigrationError) as error:
            operation()
        assert error.value.code == "recovery_required"
        assert _read_state(tmp_path) == before


def test_seekdb_empty_source_recovers_baseline_and_citation_ddl_without_probing_backup(tmp_path: Path) -> None:
    _interrupt_after_ddl(
        _runner(tmp_path, backup_factory=_forbidden_automatic_backup), "CREATE TABLE pc_artifacts", policy="auto"
    )
    first = _read_state(tmp_path)
    assert first["revision"] is None
    assert "pc_schema_revision" in first["tables"]
    assert "pc_artifacts" in first["tables"]
    assert "pc_artifact_heads" not in first["tables"]
    assert first["content"] == []
    _interrupt_after_ddl(
        _runner(tmp_path, backup_factory=_forbidden_automatic_backup),
        "ALTER TABLE pc_artifacts ADD COLUMN memory_citations",
        policy="auto",
    )
    second = _read_state(tmp_path)
    assert second["revision"] == "p0001"
    assert "memory_citations" in second["columns"]["pc_artifacts"]
    assert "memory_citations" not in second["columns"]["pc_artifact_candidate_versions"]
    replacement = _runner(tmp_path, backup_factory=_forbidden_automatic_backup)
    result = _apply(replacement)
    assert result.backup_state == "not_required"
    assert replacement.verify().revision == "p0003"
    assert _read_state(tmp_path)["content"] == []


def test_seekdb_baseline_adoption_recovers_an_empty_committed_version_table(tmp_path: Path) -> None:
    _load_baseline(tmp_path)
    _interrupt_after_ddl(_runner(tmp_path), "CREATE TABLE pc_schema_revision")
    state = _read_state(tmp_path)
    assert "pc_schema_revision" in state["tables"]
    assert state["revision"] is None
    assert state["content"] == [(b'{"content":"preserved"}',)]
    replacement = _runner(tmp_path)
    recovered = replacement.plan(backup_policy="manual")
    assert recovered.state == "recovery_required"
    assert recovered.adopt_baseline
    assert _apply(replacement, policy="manual").state == "ready"
    assert replacement.verify().revision == "p0003"
    assert _read_state(tmp_path)["tags"] == [("Keep",)]


def test_seekdb_committed_revision_recovers_before_evidence_advance(tmp_path: Path, monkeypatch) -> None:
    _load_baseline(tmp_path)
    write = mysql_migrations._Journal.write

    def interrupt(journal, evidence) -> None:
        if evidence.state == "active" and evidence.last_revision == "p0002" and evidence.pending_revision is None:
            raise _InterruptedDDL
        write(journal, evidence)

    with monkeypatch.context() as patch:
        patch.setattr(mysql_migrations._Journal, "write", interrupt)
        with pytest.raises(_InterruptedDDL):
            _apply(_runner(tmp_path), policy="manual")
    state = _read_state(tmp_path)
    assert state["revision"] == "p0002"
    assert "memory_citations" in state["columns"]["pc_artifacts"]
    assert "memory_citations" in state["columns"]["pc_artifact_candidate_versions"]
    replacement = _runner(tmp_path)
    assert replacement.plan(backup_policy="manual").source_revision == "p0002"
    assert _apply(replacement, policy="manual").state == "ready"
    assert replacement.verify().revision == "p0003"
    assert _read_state(tmp_path)["content"] == [(b'{"content":"preserved"}',)]
