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

"""New runtimes respect managed maintenance and recorded history retention."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations.backup import SQLiteBackupProvider
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from tests.test_managed_maintenance_evidence import _managed


def open_runtime(database, *, background=False):
    async def run():
        async with open_builtin_contexts(BuiltinConfig(database=database), _topic_memory_worker=background):
            pass

    asyncio.run(run())


@pytest.mark.parametrize("source", [None, "1.4.0"])
@pytest.mark.parametrize("missing", ["pc_retained_dream_candidate_heads", "pc_retained_dream_candidate_versions"])
def test_sdk_and_background_reject_missing_recorded_history(tmp_path, source, missing):
    path, settings, runner = _managed(tmp_path, source)
    open_runtime(settings.database)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"DROP TABLE {missing}")
    original = path.read_bytes()
    evidence = runner.evidence_path.read_bytes()
    for background in (False, True):
        with pytest.raises(MigrationError, match="recovery_required"):
            open_runtime(settings.database, background=background)
    assert path.read_bytes() == original
    assert runner.evidence_path.read_bytes() == evidence


def test_sdk_accepts_current_storage_without_recorded_history(tmp_path):
    path, settings, runner = _managed(tmp_path, None)
    runner.evidence_path.unlink()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE pc_retained_dream_candidate_heads")
        connection.execute("DROP TABLE pc_retained_dream_candidate_versions")
    open_runtime(settings.database)
    assert runner.verify().state == "ready"


@pytest.mark.parametrize("damage", ["unfinished", "checksum"])
def test_sdk_rejects_incomplete_or_changed_observable_history(tmp_path, damage):
    _, settings, runner = _managed(tmp_path, None)
    evidence = json.loads(runner.evidence_path.read_text())
    if damage == "unfinished":
        evidence["state"] = "active"
    else:
        evidence["release_checksums"][runner.bundle.head] = "0" * 64
    runner.evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(MigrationError, match="recovery_required" if damage == "unfinished" else "checksum_conflict"):
        open_runtime(settings.database)


_START_CHILD = """
import asyncio, sys
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.server.settings import ServerSettings
database = SQLiteConfig(url=sys.argv[1])
async def sdk(background):
    async with open_builtin_contexts(BuiltinConfig(database=database), _topic_memory_worker=background):
        raise AssertionError('SDK admitted a writer during locked maintenance')
for operation in (
    lambda: prepare_server_storage(ServerSettings(database=database)),
    lambda: asyncio.run(sdk(False)),
    lambda: asyncio.run(sdk(True)),
):
    try:
        operation()
    except MigrationError as error:
        assert error.code == 'migration_locked', error
    else:
        raise AssertionError('Server admitted a writer during locked maintenance')
print('All startup entries rejected active maintenance')
"""


def test_new_process_cannot_start_after_backup_while_maintenance_owns_lock(tmp_path, monkeypatch):
    _, settings, _ = _managed(tmp_path, None)
    target = settings.model_copy(update={"runtime": RuntimeConfig(artifact_processing_supervisor_mode="dedicated")})
    runner = deployment_runner(target, bundle="processing")
    plan = runner.plan()
    original_backup = SQLiteBackupProvider.create_backup

    def check_after_backup(provider, context):
        backup = original_backup(provider, context)
        child = subprocess.run(
            [sys.executable, "-c", _START_CHILD, settings.database.url],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert child.returncode == 0, child.stdout + child.stderr
        assert "All startup entries rejected active maintenance" in child.stdout
        return backup

    monkeypatch.setattr(SQLiteBackupProvider, "create_backup", check_after_backup)
    assert runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True).state == "ready"
    assert deployment_runner(target).verify().state == "ready"

    async def reopen():
        async with open_builtin_contexts(BuiltinConfig(database=settings.database, runtime=target.runtime)):
            pass

    asyncio.run(reopen())


def test_interrupted_partial_maintenance_blocks_startup_after_lock_release(tmp_path, monkeypatch):
    path, settings, full = _managed(tmp_path, None)
    target = settings.model_copy(update={"runtime": RuntimeConfig(artifact_processing_supervisor_mode="dedicated")})
    runner = deployment_runner(target, bundle="processing")
    plan = runner.plan()
    original_backup = SQLiteBackupProvider.create_backup

    def interrupt_after_backup(provider, context):
        original_backup(provider, context)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteBackupProvider, "create_backup", interrupt_after_backup)
        with pytest.raises(KeyboardInterrupt):
            runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    with pytest.raises(MigrationError, match="recovery_required"):
        open_runtime(settings.database)
    environment = tmp_path / "server.env"
    environment.write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_DATABASE_URL={settings.database.url}\n"
    )
    database_before = path.read_bytes()
    journals_before = {entry: entry.read_bytes() for entry in full.evidence_path.parent.rglob("maintenance.json")}
    for action in ("status", "plan", "verify", "apply"):
        result = CliRunner().invoke(create_cli([app]), ["server", "db-migrate", action, "--env-file", str(environment)])
        assert result.exit_code == 1, result.output
        assert json.loads(result.output)["error"] == "recovery_required", result.output
    assert path.read_bytes() == database_before
    assert {
        entry: entry.read_bytes() for entry in full.evidence_path.parent.rglob("maintenance.json")
    } == journals_before
    resumed = runner.plan()
    assert runner.apply(plan_id=resumed.plan_id, accepted=True, maintenance_confirmed=True).state == "ready"
    assert deployment_runner(target).verify().state == "ready"


def test_seekdb_startup_rejects_local_maintenance_before_opening_engine(tmp_path):
    from powercontext.builtin.persistence.migrations.locking import local_migration_lock
    from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile

    target = tmp_path / "seekdb"

    async def start():
        async with SeekDBProfile.open(SeekDBConfig(path=target), tables=()):
            pytest.fail("Startup admitted an embedded engine during maintenance")

    with local_migration_lock(target), pytest.raises(MigrationError, match="migration_locked"):
        asyncio.run(start())
    assert not target.exists()


@pytest.mark.parametrize("backend", ["sqlite", "seekdb", "oceanbase"])
@pytest.mark.parametrize("operation", ["processing_configuration", "atomic_projection"])
@pytest.mark.parametrize("state", ["preparing", "active"])
def test_full_admission_rejects_unfinished_domain_journals(tmp_path, monkeypatch, backend, operation, state):
    """Native cases model the connection identity; they do not claim native engine acceptance."""
    from pydantic import SecretStr

    from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
    from powercontext.builtin.persistence.migrations.models import digest
    from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner, _Evidence, _Journal
    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
    from powercontext.builtin.persistence.seekdb import SeekDBConfig

    path, _settings, full = _managed(tmp_path, None)
    directory = full.evidence_path.parent
    identity = BackendIdentity("sqlite", digest({"kind": "sqlite", "path": str(path.resolve())}), path.name)
    if backend != "sqlite":
        config = (
            SeekDBConfig(path=tmp_path / "seekdb")
            if backend == "seekdb"
            else OceanBaseConfig(url=SecretStr("mysql+aoceanbase://root@localhost:2881/test?charset=utf8mb4"))
        )
        connections = MaintenanceConnections(
            config, evidence_directory=directory, lock_coordination="single-host" if backend == "oceanbase" else None
        )
        full = MySQLMigrationRunner(connections, full.bundle, evidence_directory=directory)
        identity = BackendIdentity(backend, "modeled-native-database", "test")
        monkeypatch.setattr(connections, "run", lambda callback, **_options: callback(None, identity, lambda: None))
        assert full.plan().state == "uninitialized"
    else:
        assert full.verify().state == "ready"
    journal = _Journal(directory / operation, identity)
    journal.write(
        _Evidence(
            state=state,
            database_id=identity.database_id,
            source_revision=full.bundle.head,
            source_fingerprint="interrupted-domain-maintenance",
            bundle_checksum="domain-bundle",
            configuration_digest="domain-configuration",
            last_revision=full.bundle.head,
            maintenance_window_id="interrupted-domain-window",
            backup_policy="skip",
            backup_state="skipped",
        )
    )
    before = journal.path.read_bytes()
    database_before = path.read_bytes()
    for inspect in (full.plan, full.verify, lambda: full.apply(accepted=True, maintenance_confirmed=True)):
        with pytest.raises(MigrationError, match="recovery_required"):
            inspect()
    assert journal.path.read_bytes() == before
    assert path.read_bytes() == database_before
