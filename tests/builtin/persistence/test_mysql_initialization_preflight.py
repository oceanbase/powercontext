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

"""Recovery planning with modeled MySQL DDL and real domain seed checks.

These tests do not establish native backend acceptance.
"""

import copy
import json

import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine

from powercontext.builtin.persistence.migrations import mysql as migration
from powercontext.builtin.persistence.migrations import mysql_schema
from powercontext.builtin.persistence.migrations.connections import BackendIdentity, MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql_schema import _parse_table
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest


@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
@pytest.mark.parametrize("damage", [None, "missing-evidence", "unregistered-table"])
def test_initialization_recovery_defers_seed_checks_only_for_registered_prefix(tmp_path, monkeypatch, backend, damage):
    directory = tmp_path / "evidence"
    config = (
        SeekDBConfig(path=tmp_path / "native")
        if backend == "seekdb"
        else OceanBaseConfig(url=SecretStr("mysql+aoceanbase://root@localhost:2881/test?charset=utf8mb4"))
    )
    connections = MaintenanceConnections(
        config, evidence_directory=directory, lock_coordination="single-host" if backend == "oceanbase" else None
    )
    runner = migration.MySQLMigrationRunner(
        connections,
        server_bundle(),
        evidence_directory=directory,
        configuration={
            "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
            "embedding_enabled": False,
        },
    )
    identity = BackendIdentity(backend, "fixture-initialization", "test")
    inventory = copy.deepcopy(
        next(state for state in runner.schema._prefixes["s1300"] if "pc_artifact_candidate_versions" in state["tables"])
    )
    control = "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, PRIMARY KEY (version_num))"
    name, definition = _parse_table(control)
    inventory["tables"][name] = definition
    journal = migration._Journal(directory, identity)
    journal.write(
        migration._Evidence(
            state="active",
            database_id=identity.database_id,
            source_revision=None,
            source_fingerprint=runner.schema.fingerprint({"tables": {}, "views": [], "triggers": []}),
            bundle_checksum=runner.checksum,
            configuration_digest=runner.configuration_digest,
            last_revision=None,
            pending_revision="s1300",
            maintenance_window_id="interrupted-initialization",
            backup_policy="auto",
            backup_state="not_required",
        )
    )
    if damage == "missing-evidence":
        journal.path.unlink()
    elif damage == "unregistered-table":
        name, definition = _parse_table("CREATE TABLE unregistered (id INTEGER PRIMARY KEY)")
        inventory["tables"][name] = definition

    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            resource = json.loads((runner.bundle.snapshot_directory / "schemas/sqlite.json").read_text())
            for statement in resource["initialize"]:
                connection.exec_driver_sql(statement)
            connection.exec_driver_sql(control)
            connection.commit()
            # The baseline table DDL exists, but its final manifest seed has not
            # run. Domain preflight cannot validate this intermediate state yet.
            with pytest.raises(MigrationError, match="domain_validation_failed"):
                runner.bundle.domains.preflight(connection, None, backend=backend, configuration=runner.configuration)
            monkeypatch.setattr(migration, "mysql_inventory", lambda _connection: copy.deepcopy(inventory))
            monkeypatch.setattr(mysql_schema, "mysql_inventory", lambda _connection: copy.deepcopy(inventory))
            monkeypatch.setattr(
                connections, "run", lambda operation, **_options: operation(connection, identity, lambda: None)
            )
            monkeypatch.setattr(connections, "coordination", lambda _identity: {})
            if damage:
                code = "recovery_required" if damage == "missing-evidence" else "incompatible_schema"
                with pytest.raises(MigrationError, match=code):
                    runner.plan()
            else:
                plan = runner.plan()
                assert plan.state == "recovery_required"
                assert plan.source_revision is None
                assert plan.revisions[0] == "s1300"
                assert plan.domain_steps
                native_execute = runner.schema.execute

                def execute_revision(connection, _config, _source, target, guard):
                    # Model native DDL with the matching frozen SQLite schema
                    # revisions. The public runner still uses its real domain
                    # checks and durable evidence throughout plan/apply/verify.
                    config = runner.bundle.config(connection)
                    config.attributes["configuration"] = runner.configuration
                    if target == "s1300":
                        native_execute(connection, "@mysql_operations.py:initialize_processing", runner.configuration)
                        command.stamp(config, target)
                    else:
                        command.upgrade(config, target)
                    runner.bundle.domains.apply(
                        connection, target, backend=backend, configuration=runner.configuration, guard=guard
                    )
                    inventory.clear()
                    inventory.update(copy.deepcopy(runner.schema._schemas[target]))
                    inventory["tables"]["pc_schema_revision"] = _parse_table(control)[1]
                    connection.commit()

                monkeypatch.setattr(runner, "_upgrade", execute_revision)
                result = runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
                assert result.state == "ready" and result.revision == runner.bundle.head
                assert result.backup_state == "not_required"
                assert runner.verify().state == "ready"
                assert not runner.apply(
                    plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True
                ).changed
                completed = journal.read()
                assert completed is not None and completed.state == "complete"
                assert completed.maintenance_window_id == "interrupted-initialization"
    finally:
        engine.dispose()
