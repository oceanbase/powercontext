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

"""Registered domain work using real SQLite revisions, data and recovery evidence.

This isolated extensibility fixture is not a historical Server release baseline.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from powercontext.builtin.persistence.migrations import MigrationBundle, MigrationError, SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.bundle import sqlite_inventory
from powercontext.builtin.persistence.migrations.models import digest

_NORMALIZE = """
from powercontext.builtin.persistence.migrations.models import MigrationError

def preflight(connection, *, configuration):
    if configuration.get("format") != "lower":
        raise MigrationError("unsupported_configuration", "Select the registered text format.")
    present = connection.exec_driver_sql("SELECT name FROM sqlite_schema WHERE name = 'items'").first()
    if present and connection.exec_driver_sql("SELECT 1 FROM items WHERE value = 'UNSUPPORTED' LIMIT 1").first():
        raise MigrationError("unsupported_source", "Source data requires an unsupported conversion.")

def apply(connection, *, configuration):
    connection.exec_driver_sql("UPDATE items SET normalized = lower(value)")

def verify(connection, *, configuration):
    validate(connection, configuration=configuration)

def validate(connection, *, configuration):
    if connection.exec_driver_sql(
        "SELECT 1 FROM items WHERE normalized IS NULL OR normalized != lower(value) LIMIT 1"
    ).first():
        raise MigrationError("domain_not_ready", "Normalized source data is incomplete.")
"""

_PROJECT = """
from pathlib import Path
from powercontext.builtin.persistence.migrations.models import MigrationError

def preflight(connection, *, configuration):
    pass

def apply(connection, *, configuration):
    connection.exec_driver_sql("DELETE FROM projection")
    connection.exec_driver_sql("INSERT INTO projection SELECT id, normalized FROM items")

def verify(connection, *, configuration):
    if configuration.get("fault_file") and Path(configuration["fault_file"]).exists():
        raise MigrationError("injected_failure", "Simulated interruption during domain verification.")
    validate(connection, configuration=configuration)

def validate(connection, *, configuration):
    if connection.exec_driver_sql(
        "SELECT 1 FROM items i LEFT JOIN projection p ON p.id = i.id "
        "WHERE p.id IS NULL OR p.value != i.normalized LIMIT 1"
    ).first() or connection.exec_driver_sql(
        "SELECT 1 FROM projection p LEFT JOIN items i ON i.id = p.id WHERE i.id IS NULL LIMIT 1"
    ).first():
        raise MigrationError("projection_not_ready", "Projection differs from authoritative data.")
"""


def _bundle(directory: Path, *, head: int = 3) -> MigrationBundle:
    directory.mkdir()
    (directory / "versions").mkdir()
    (directory / "domains").mkdir()
    fixture = Path(__file__).resolve().parents[2] / "fixtures/database_migrations"
    shutil.copyfile(fixture / "env.py", directory / "env.py")
    statements = (
        "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",
        "ALTER TABLE items ADD COLUMN normalized TEXT",
        "CREATE TABLE projection (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE INDEX ix_projection_value ON projection(value)",
    )
    fingerprints = {}
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            for number, statement in enumerate(statements[:head], start=1):
                revision = f"d{number}"
                parent = f"d{number - 1}" if number > 1 else None
                (directory / "versions" / f"{revision}.py").write_text(
                    f"from alembic import op\nrevision = {revision!r}\ndown_revision = {parent!r}\n"
                    f"def upgrade():\n    op.get_bind().exec_driver_sql({statement!r})\n",
                    encoding="utf-8",
                )
                connection.exec_driver_sql(statement)
                fingerprints[revision] = [digest(sqlite_inventory(connection))]
    finally:
        engine.dispose()
    (directory / "domains/normalize.py").write_text(_NORMALIZE, encoding="utf-8")
    (directory / "domains/project.py").write_text(_PROJECT, encoding="utf-8")
    steps = [
        {"id": "normalize", "revision": "d2", "kind": "data", "module": "domains/normalize.py", "backends": ["sqlite"]},
        {
            "id": "project",
            "revision": "d3",
            "kind": "projection",
            "module": "domains/project.py",
            "depends_on": ["normalize"],
            "backends": ["sqlite"],
        },
    ]
    if head == 4:
        # A later release registers more work without changing the planner or executor.
        steps.append({**steps[1], "id": "reproject", "revision": "d4", "depends_on": ["project"]})
    manifest = {
        "revision_resources": {revision: [] for revision in fingerprints},
        "sqlite_fingerprints": fingerprints,
        "sqlite_baselines": {fingerprints["d1"][0]: "d1"},
        "affected_objects": ["items", "projection"],
        # Deliberately unordered: execution follows schema order and dependencies.
        "domain_steps": list(reversed([step for step in steps if int(step["revision"][1:]) <= head])),
        "domain_readiness": {f"d{i}": ["normalize"] + (["project"] if i >= 3 else []) for i in range(2, head + 1)},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return MigrationBundle(directory)


def _legacy(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany("INSERT INTO items VALUES (?, ?)", [(1, "FIRST"), (2, "Second")])


def _apply(runner: SQLiteMigrationRunner):
    return runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)


def _rows(database: Path, sql: str):
    with sqlite3.connect(database) as connection:
        return connection.execute(sql).fetchall()


def test_registered_schema_data_and_projection_upgrade_preserves_authority(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    database = tmp_path / "pc.db"
    _legacy(database)
    runner = SQLiteMigrationRunner(database, bundle, configuration={"format": "lower"})
    plan = runner.plan()
    assert plan.revisions == ("d2", "d3")
    assert plan.domain_steps == ("normalize", "project")
    assert plan.domain_validators == ("normalize", "project")
    result = _apply(runner)
    assert result.changed and result.backup_ref
    assert _rows(database, "SELECT * FROM items ORDER BY id") == [(1, "FIRST", "first"), (2, "Second", "second")]
    assert _rows(database, "SELECT * FROM projection ORDER BY id") == [(1, "first"), (2, "second")]
    assert runner.verify().revision == "d3"
    assert not runner.apply().changed
    assert _rows(Path(result.backup_ref), "SELECT * FROM items ORDER BY id") == [(1, "FIRST"), (2, "Second")]


def test_preflight_rejects_incompatible_configuration_before_writes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    database = tmp_path / "pc.db"
    _legacy(database)
    original = database.read_bytes()
    runner = SQLiteMigrationRunner(database, bundle, configuration={"format": "unknown"})
    with pytest.raises(MigrationError, match="unsupported_configuration"):
        runner.apply()
    assert database.read_bytes() == original
    assert not runner.evidence_path.exists()


def test_changed_source_data_is_rechecked_before_applying_an_accepted_plan(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    database = tmp_path / "pc.db"
    _legacy(database)
    configuration = {"format": "lower"}
    runner = SQLiteMigrationRunner(database, bundle, configuration=configuration)
    accepted = runner.plan()
    configuration["format"] = "unreviewed"
    assert runner.plan() == accepted
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE items SET value = 'UNSUPPORTED' WHERE id = 1")
    original = database.read_bytes()
    with pytest.raises(MigrationError, match="unsupported_source"):
        runner.apply(plan_id=accepted.plan_id, accepted=True, maintenance_confirmed=True)
    assert database.read_bytes() == original
    assert not runner.evidence_path.exists()


def test_failed_domain_verification_rolls_back_revision_and_reuses_original_backup(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    database = tmp_path / "pc.db"
    _legacy(database)
    fault = tmp_path / "interrupt"
    fault.touch()
    runner = SQLiteMigrationRunner(database, bundle, configuration={"format": "lower", "fault_file": str(fault)})
    with pytest.raises(MigrationError, match="injected_failure"):
        _apply(runner)
    assert _rows(database, "SELECT version_num FROM pc_schema_revision") == [("d2",)]
    assert not _rows(database, "SELECT name FROM sqlite_schema WHERE name = 'projection'")
    original = json.loads(runner.evidence_path.read_text())["backup"]
    fault.unlink()
    assert runner.plan().state == "recovery_required"
    assert runner.plan().domain_steps == ("project",)
    result = _apply(runner)
    assert result.backup_ref == original["location"]
    assert runner.verify().revision == "d3"
    fault.touch()
    # Ordinary verification uses live invariants, not migration-copy verification.
    assert runner.verify().revision == "d3"


@pytest.mark.parametrize(
    "corruption, error",
    [
        ("DELETE FROM projection WHERE id = 1", "projection_not_ready"),
        ("UPDATE items SET normalized = NULL WHERE id = 1", "domain_not_ready"),
    ],
)
def test_schema_head_alone_does_not_establish_readiness(tmp_path: Path, corruption: str, error: str) -> None:
    bundle = _bundle(tmp_path / "bundle")
    database = tmp_path / "pc.db"
    _legacy(database)
    runner = SQLiteMigrationRunner(database, bundle, configuration={"format": "lower"})
    _apply(runner)
    with sqlite3.connect(database) as connection:
        connection.execute(corruption)
    assert _rows(database, "SELECT version_num FROM pc_schema_revision") == [("d3",)]
    with pytest.raises(MigrationError, match=error):
        runner.verify()
    with pytest.raises(MigrationError, match=error):
        runner.apply()


def test_subsequent_release_and_direct_upgrade_produce_equivalent_state(tmp_path: Path) -> None:
    prior = _bundle(tmp_path / "prior")
    subsequent = _bundle(tmp_path / "subsequent", head=4)
    assert all(subsequent.checksums[revision] == checksum for revision, checksum in prior.checksums.items())
    sequential_db, direct_db = tmp_path / "sequential.db", tmp_path / "direct.db"
    for database in (sequential_db, direct_db):
        _legacy(database)
    _apply(SQLiteMigrationRunner(sequential_db, prior, configuration={"format": "lower"}))
    sequential = SQLiteMigrationRunner(sequential_db, subsequent, configuration={"format": "lower"})
    assert sequential.plan().domain_steps == ("reproject",)
    _apply(sequential)
    direct = SQLiteMigrationRunner(direct_db, subsequent, configuration={"format": "lower"})
    _apply(direct)
    assert sequential.verify().revision == direct.verify().revision == "d4"
    for query in ("SELECT * FROM items ORDER BY id", "SELECT * FROM projection ORDER BY id"):
        assert _rows(sequential_db, query) == _rows(direct_db, query)


def test_released_domain_resources_cannot_be_rewritten(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    database = tmp_path / "pc.db"
    runner = SQLiteMigrationRunner(database, bundle, configuration={"format": "lower"})
    _apply(runner)
    module = bundle.directory / "domains/normalize.py"
    module.write_text(_NORMALIZE + "\n# changed historical resource\n", encoding="utf-8")
    replacement = SQLiteMigrationRunner(database, MigrationBundle(bundle.directory), configuration={"format": "lower"})
    with pytest.raises(MigrationError, match="checksum_conflict"):
        replacement.plan()


@pytest.mark.parametrize(
    "defect", ["cycle", "future_dependency", "missing_resource", "missing_readiness", "unknown_field"]
)
def test_invalid_registration_rejected_before_database_access(tmp_path: Path, defect: str) -> None:
    bundle = _bundle(tmp_path / "bundle")
    path = bundle.directory / "manifest.json"
    manifest = json.loads(path.read_text())
    project, normalize = manifest["domain_steps"]
    if defect == "cycle":
        project["revision"] = "d2"
        normalize["depends_on"] = ["project"]
    elif defect == "future_dependency":
        normalize["depends_on"] = ["project"]
    elif defect == "missing_resource":
        normalize["resources"] = ["missing.json"]
    elif defect == "missing_readiness":
        del manifest["domain_readiness"]["d3"]
    else:
        normalize["depend_on"] = ["project"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MigrationError, match="invalid_bundle"):
        MigrationBundle(bundle.directory)


def test_unregistered_backend_fails_before_execution(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "bundle")
    with pytest.raises(MigrationError, match="unsupported_upgrade_path"):
        bundle.domains.pending("d1", "seekdb")
