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

"""Public MySQL admission with modeled reflection and real vector declarations.

SQLite supplies the stored data; native backend acceptance has separate tests.
"""

import copy
import shutil
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations import mysql as migration
from powercontext.builtin.persistence.migrations import mysql_schema
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql_schema import _parse_table
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.vector_profile import load_sqlite_vector_extension
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest


@pytest.fixture
def managed(tmp_path, monkeypatch):
    profile = EmbeddingProfile(profile_id="stored-space", model="fixture", dimension=3)
    bundle = server_bundle(embedding_profile=profile)
    configuration = {
        "embedding_enabled": True,
        "embedding_profile": profile.model_dump(mode="json"),
        "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
    }
    database = tmp_path / "database.sqlite3"
    sqlite = SQLiteMigrationRunner(database, bundle, configuration=configuration)
    sqlite.apply(plan_id=sqlite.plan().plan_id, accepted=True, maintenance_confirmed=True)
    directory = tmp_path / "mysql-evidence"
    connections = MaintenanceConnections(SeekDBConfig(path=tmp_path / "native"))
    identity = BackendIdentity("seekdb", "fixture-vectors", "test")

    def runner(selected=bundle, configured=configuration):
        return migration.MySQLMigrationRunner(
            connections, selected, evidence_directory=directory, configuration=configured
        )

    original = runner()
    inventory = copy.deepcopy(original.schema._schemas[bundle.head])
    control = _parse_table(
        "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, PRIMARY KEY (version_num))"
    )
    inventory["tables"][control[0]] = control[1]
    monkeypatch.setattr(migration, "mysql_inventory", lambda _connection: copy.deepcopy(inventory))
    monkeypatch.setattr(mysql_schema, "mysql_inventory", lambda _connection: copy.deepcopy(inventory))

    def run(operation, *, writable=False, allow_missing_database=False):
        del allow_missing_database  # This fixture always opens its existing database.
        assert not writable, "Admission must fail before requesting a write connection"
        engine = create_engine(f"sqlite:///{database}")
        try:
            with engine.connect() as connection:
                load_sqlite_vector_extension(connection)
                connection.exec_driver_sql("PRAGMA query_only=ON")
                return operation(connection, identity, lambda: None)
        finally:
            engine.dispose()

    monkeypatch.setattr(connections, "run", run)
    monkeypatch.setattr(connections, "coordination", lambda _identity: {})
    journal = migration._Journal(directory, identity)
    journal.write(
        migration._Evidence(
            state="complete",
            database_id=identity.database_id,
            source_revision=None,
            source_fingerprint=original.schema.fingerprint({"tables": {}, "views": [], "triggers": []}),
            bundle_checksum=original.checksum,
            configuration_digest=original.configuration_digest,
            last_revision=bundle.head,
            maintenance_window_id="vector-upgrade",
            backup_policy="auto",
            backup_state="not_required",
            release_checksums=original.checksums,
        )
    )
    assert original.verify().state == "ready"
    return SimpleNamespace(
        database=database,
        profile=profile,
        bundle=bundle,
        configuration=configuration,
        runner=runner,
        journal=journal,
        inventory=inventory,
        control=control,
    )


@pytest.mark.parametrize("change", ["profile", "resource"])
def test_vector_configuration_drift_is_distinct_from_changed_migration_resources(managed, tmp_path, change):
    if change == "profile":
        profile = managed.profile.model_copy(update={"profile_id": "different-space"})
        bundle = server_bundle(embedding_profile=profile)
        configuration = {**managed.configuration, "embedding_profile": profile.model_dump(mode="json")}
        expected = "embedding_profile_mismatch"
    else:
        copied = tmp_path / "changed-bundle"
        shutil.copytree(managed.bundle.directory, copied)
        resource = copied / "versions/s1402_portable_storage.py"
        resource.write_text(resource.read_text() + "\n# Changed historical migration resource\n")
        bundle = MigrationBundle(copied, embedding_profile=managed.profile)
        configuration = managed.configuration
        expected = "checksum_conflict"
    replacement = managed.runner(bundle, configuration)
    before = managed.database.read_bytes(), managed.journal.path.read_bytes()
    for operation in (replacement.plan, replacement.verify, replacement.apply):
        with pytest.raises(MigrationError) as error:
            operation()
        assert error.value.code == expected
        assert (managed.database.read_bytes(), managed.journal.path.read_bytes()) == before


def test_vector_recovery_allows_declaration_table_before_domain_seed(managed):
    original = managed.runner()
    engine = create_engine(f"sqlite:///{managed.database}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM pc_server_storage_profile")
            connection.exec_driver_sql("UPDATE pc_schema_revision SET version_num='s1300'")
    finally:
        engine.dispose()
    # Model the s1400 DDL prefix after the profile table was created, before
    # its domain transaction stored the declaration or advanced the revision.
    managed.inventory.clear()
    managed.inventory.update(copy.deepcopy(original.schema._schemas["s1400"]))
    managed.inventory["tables"][managed.control[0]] = managed.control[1]
    evidence = managed.journal.read()
    assert evidence is not None
    evidence.state = "active"
    evidence.last_revision = "s1300"
    evidence.pending_revision = "s1400"
    evidence.release_checksums = {"s1300": original.checksums["s1300"]}
    managed.journal.write(evidence)
    before = managed.database.read_bytes(), managed.journal.path.read_bytes()
    plan = original.plan()
    assert plan.state == "recovery_required"
    assert plan.source_revision == "s1300" and plan.revisions[0] == "s1400"
    assert (managed.database.read_bytes(), managed.journal.path.read_bytes()) == before
