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

"""Partial maintenance preserves the full migration's historical evidence."""

import json
import shutil
import sqlite3
from contextlib import closing

import pytest
from alembic import command
from sqlalchemy import create_engine

from powercontext.builtin.persistence.migrations.atomic_projection import AtomicProjectionMigrationRunner
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.processing_configuration import ProcessingConfigurationMigrationRunner
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.settings import ServerSettings


def _managed(tmp_path, source):
    path = tmp_path / "managed.db"
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    full = deployment_runner(settings)
    if source:
        bundle = server_bundle(source_release=source)
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as connection:
                config = bundle.config(connection)
                config.attributes["configuration"] = full.configuration
                command.upgrade(config, bundle.source_revision)
                connection.exec_driver_sql("DROP TABLE pc_schema_revision")
        finally:
            engine.dispose()
    plan = full.plan(backup_policy="skip")
    full.apply(
        plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy="skip", accept_no_backup=True
    )
    return path, settings, full


@pytest.mark.parametrize("source", [None, "1.4.0"], ids=["canonical", "release140"])
@pytest.mark.parametrize("operation", ["processing", "atomic-projection"])
@pytest.mark.parametrize("damage", ["checksum", "resource"])
def test_partial_maintenance_rejects_changed_released_history(tmp_path, source, operation, damage):
    path, settings, full = _managed(tmp_path, source)
    requested = settings.model_copy(update={"runtime": RuntimeConfig(artifact_processing_supervisor_mode="dedicated")})
    partial = deployment_runner(requested if operation == "processing" else settings, bundle=operation)
    reviewed = partial.plan(backup_policy="skip")
    if damage == "checksum":
        evidence = json.loads(full.evidence_path.read_text())
        evidence["release_checksums"][full.bundle.head] = "0" * 64
        full.evidence_path.write_text(json.dumps(evidence))
    else:
        directory = tmp_path / "resources"
        shutil.copytree(full.bundle.directory, directory)
        resource = directory / "versions/s1402_portable_storage.py"
        resource.write_text(resource.read_text() + "\n# Changed released migration resource.\n")
        changed = MigrationBundle(directory, source_directory=full.bundle.source_directory)
        full = SQLiteMigrationRunner(path, changed, configuration=full.configuration)
        runner = (
            ProcessingConfigurationMigrationRunner if operation == "processing" else AtomicProjectionMigrationRunner
        )
        partial = runner(
            MaintenanceConnections(settings.database),
            changed,
            configuration=partial.configuration,
            evidence_directory=full.evidence_path.parent,
        )
    original = path.read_bytes()
    original_evidence = full.evidence_path.read_bytes()
    with pytest.raises(MigrationError, match="checksum_conflict"):
        full.verify()
    for inspect in (
        partial.plan,
        partial.verify,
        lambda: partial.apply(
            plan_id=reviewed.plan_id,
            accepted=True,
            maintenance_confirmed=True,
            backup_policy="skip",
            accept_no_backup=True,
        ),
    ):
        with pytest.raises(MigrationError, match="checksum_conflict"):
            inspect()
    assert path.read_bytes() == original
    assert full.evidence_path.read_bytes() == original_evidence
    assert not list(full.evidence_path.parent.glob("*/*/maintenance.json"))
    assert not list(path.parent.glob("*.pc-migration-backups/*"))


def test_source140_processing_switch_keeps_original_migration_evidence(tmp_path):
    path, settings, full = _managed(tmp_path, "1.4.0")
    requested = settings.model_copy(update={"runtime": RuntimeConfig(artifact_processing_supervisor_mode="dedicated")})
    original_evidence = full.evidence_path.read_bytes()
    partial = deployment_runner(requested, bundle="processing", migration_id="source140-switch")
    plan = partial.plan(backup_policy="skip")
    result = partial.apply(
        plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy="skip", accept_no_backup=True
    )
    assert result.changed and partial.verify().state == "ready"
    assert full.evidence_path.read_bytes() == original_evidence
    assert deployment_runner(requested).verify().state == "ready"
    with closing(sqlite3.connect(path)) as connection:
        assert (
            json.loads(connection.execute("SELECT config_manifest FROM pc_artifact_processing_schema").fetchone()[0])[
                "mode"
            ]
            == "dedicated"
        )


@pytest.mark.parametrize("source", [None, "1.4.0"], ids=["canonical", "release140"])
@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
@pytest.mark.parametrize("damage", [None, "checksum", "database-id", "head", "unfinished"])
def test_native_partial_admission_requires_original_completed_evidence(tmp_path, monkeypatch, source, backend, damage):
    """Use real SQLite storage with modeled native identity/evidence, not a native backend."""
    from pydantic import SecretStr

    from powercontext.builtin.persistence.migrations import processing_configuration
    from powercontext.builtin.persistence.migrations.connections import BackendIdentity
    from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner, _Evidence, _Journal
    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
    from powercontext.builtin.persistence.seekdb import SeekDBConfig

    path, _settings, original = _managed(tmp_path, source)
    directory = tmp_path / "native-evidence"
    config = (
        SeekDBConfig(path=tmp_path / "seekdb")
        if backend == "seekdb"
        else OceanBaseConfig(url=SecretStr("mysql+aoceanbase://root@localhost:2881/test?charset=utf8mb4"))
    )
    connections = MaintenanceConnections(
        config, evidence_directory=directory, lock_coordination="single-host" if backend == "oceanbase" else None
    )
    full = MySQLMigrationRunner(
        connections,
        original.bundle,
        configuration=original.configuration,
        evidence_directory=directory,
    )
    identity = BackendIdentity(backend, "modeled-native-database", "test")
    evidence = _Evidence(
        state="complete",
        database_id=identity.database_id,
        source_revision=original.bundle.source_revision,
        source_fingerprint="registered-source",
        bundle_checksum=full.checksum,
        configuration_digest=full.configuration_digest,
        last_revision=full.bundle.head,
        maintenance_window_id="completed-native-migration",
        backup_policy="skip",
        backup_state="skipped",
        release_checksums=full.checksums,
    )
    journal = _Journal(directory, identity)
    journal.write(evidence)
    value = json.loads(journal.path.read_text())
    if damage == "checksum":
        value["release_checksums"][full.bundle.head] = "0" * 64
    elif damage == "database-id":
        value["database_id"] = "another-database"
    elif damage == "head":
        value["last_revision"] = "s1401"
    elif damage == "unfinished":
        value["pending_revision"] = "s1402"
        value["last_revision"] = "s1401"
    journal.path.write_text(json.dumps(value))
    partial = ProcessingConfigurationMigrationRunner(
        connections,
        server_bundle(),
        source_bundles=(server_bundle(source_release="1.4.0"),),
        configuration=original.configuration,
        evidence_directory=directory,
    )
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            monkeypatch.setattr(
                connections, "run", lambda operation, **_options: operation(connection, identity, lambda: None)
            )
            # The storage is SQLite; provide the corresponding registered native
            # layout solely for the partial operation's inspection fingerprint.
            monkeypatch.setattr(
                processing_configuration, "mysql_inventory", lambda _connection: full.schema._schemas[full.bundle.head]
            )
            before, before_evidence = path.read_bytes(), journal.path.read_bytes()
            if damage:
                code = "checksum_conflict" if damage == "checksum" else "recovery_required"
                for inspect in (
                    partial.plan,
                    partial.verify,
                    lambda: partial.apply(accepted=True, maintenance_confirmed=True),
                ):
                    with pytest.raises(MigrationError, match=code):
                        inspect()
            else:
                assert partial.verify().state == "ready"
                assert not partial.apply().changed
            assert path.read_bytes() == before
            assert journal.path.read_bytes() == before_evidence
            assert list(directory.rglob("maintenance.json")) == [journal.path]
    finally:
        engine.dispose()
