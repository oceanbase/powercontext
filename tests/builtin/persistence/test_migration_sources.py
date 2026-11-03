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

"""Source-specific upgrades retain their immutable lineage across repeated runs."""

import json
import shutil
import sqlite3
from contextlib import closing

import pytest
from alembic import command
from sqlalchemy import create_engine

from powercontext.builtin.persistence.migrations.bundle import MigrationBundle, sqlite_inventory
from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError, digest
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner
from powercontext.builtin.persistence.migrations.sources import MigrationSources
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.seekdb import SeekDBConfig


def _fingerprint(*statements):
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
            return digest(sqlite_inventory(connection))
    finally:
        engine.dispose()


def _revision(directory, revision, parent, *statements):
    path = directory / "versions" / (revision + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from alembic import op\n"
        f"revision = {revision!r}\ndown_revision = {parent!r}\n"
        "def upgrade():\n" + "".join(f"    op.get_bind().exec_driver_sql({statement!r})\n" for statement in statements)
    )


@pytest.fixture
def histories(tmp_path):
    base = tmp_path / "base"
    overlay = tmp_path / "source"
    base.mkdir()
    overlay.mkdir()
    builtin = server_bundle()
    (base / "env.py").write_bytes((builtin.snapshot_directory / "env.py").read_bytes())
    initial = "CREATE TABLE records (value TEXT)"
    intermediate = "ALTER TABLE records ADD COLUMN marker TEXT"
    complete = "ALTER TABLE records ADD COLUMN target TEXT"
    _revision(base, "initial", None, initial)
    _revision(base, "shared", "initial", intermediate, complete)
    _revision(base / "mysql", "initial", None, initial)
    _revision(base / "mysql", "shared", "initial", intermediate, complete)
    (base / "mysql/env.py").write_bytes((base / "env.py").read_bytes())
    mysql_layouts = {
        "format": "powercontext.mysql-layouts.v1",
        "revisions": {"initial": [initial], "shared": [intermediate, complete]},
        "checkpoints": {},
    }
    (base / "mysql.json").write_text(json.dumps(mysql_layouts))
    source_ddl = "CREATE TABLE records (value TEXT, marker TEXT)"
    source_fingerprint = _fingerprint(source_ddl)
    final_fingerprint = _fingerprint(initial, intermediate, complete)
    manifest = {
        "revision_resources": {"initial": ["mysql.json"], "shared": ["mysql.json"]},
        "mysql_layouts": ["mysql.json"],
        "readiness_scope": "server",
        "sqlite_baselines": {_fingerprint(initial): "initial"},
        "sqlite_fingerprints": {"initial": [_fingerprint(initial)], "shared": [final_fingerprint]},
        "affected_objects": ["records"],
    }
    (base / "manifest.json").write_text(json.dumps(manifest))
    canonical = MigrationBundle(base)
    (overlay / "registration.json").write_text(
        json.dumps({"remove_resources": ["versions/initial.py", "mysql/versions/initial.py"]})
    )
    _revision(overlay, "r1400", None, source_ddl)
    _revision(overlay, "shared", "r1400", complete)
    _revision(overlay / "mysql", "r1400", None, source_ddl)
    _revision(overlay / "mysql", "shared", "r1400", complete)
    mysql_layouts["revisions"] = {"r1400": [source_ddl], "shared": [complete]}
    (overlay / "mysql.json").write_text(json.dumps(mysql_layouts))
    manifest.update(
        source_release="1.4.0",
        source_revision="r1400",
        revision_resources={"r1400": ["mysql.json"], "shared": ["mysql.json"]},
        sqlite_baselines={source_fingerprint: "r1400"},
        sqlite_fingerprints={"r1400": [source_fingerprint], "shared": [final_fingerprint]},
    )
    (overlay / "manifest.json").write_text(json.dumps(manifest))
    source = MigrationBundle(base, source_directory=overlay)
    database = tmp_path / "source.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(source_ddl)
        connection.execute("INSERT INTO records VALUES ('kept', 'original')")
    return canonical, source, database


def _runner(histories):
    base, source, database = histories
    return SQLiteMigrationRunner(database, base, source_bundles=(source,))


def test_source_overlay_preserves_canonical_history(histories):
    base, source, _database = histories
    reloaded = MigrationBundle(base.directory)
    assert (reloaded.checksum, reloaded.checksums) == (base.checksum, base.checksums)
    assert source.revisions == ("r1400", "shared")
    assert source.checksums["shared"] != base.checksums["shared"]
    assert not (source.snapshot_directory / "versions/initial.py").exists()
    assert (source.snapshot_directory / "env.py").read_bytes() == (base.directory / "env.py").read_bytes()


@pytest.mark.parametrize("location", ["base", "overlay", "registration"])
def test_source_overlay_rejects_changed_original_inputs(histories, location):
    base, source, _database = histories
    path = {
        "base": base.directory / "versions/initial.py",
        "overlay": source.source_directory / "versions/r1400.py",
        "registration": source.source_directory / "registration.json",
    }[location]
    path.write_text(path.read_text() + "\n")
    with pytest.raises(MigrationError, match="plan_changed"):
        source.assert_unchanged()


def test_source_upgrade_and_reopened_runner_preserve_history(histories):
    runner = _runner(histories)
    plan = runner.plan(backup_policy="skip")
    assert plan.source_revision == "r1400" and plan.revisions == ("shared",)
    result = runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="skip",
        accept_no_backup=True,
    )
    assert result.changed
    with closing(sqlite3.connect(runner.database)) as connection:
        assert connection.execute("SELECT * FROM records").fetchall() == [("kept", "original", None)]
    reopened = _runner(histories)
    assert reopened.verify().revision == "shared"
    assert reopened.plan().bundle_checksum == histories[1].checksum
    assert not reopened.apply().changed


def test_versioned_source_without_journal_is_rejected_before_write(histories):
    _base, source, database = histories
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            command.stamp(source.config(connection), "r1400")
    finally:
        engine.dispose()
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="recovery_required"):
        _runner(histories).plan()
    assert database.read_bytes() == before


def test_unknown_source_object_is_not_adopted(histories):
    database = histories[2]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE unknown (value TEXT)")
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="unknown_baseline"):
        _runner(histories).plan()
    assert database.read_bytes() == before


def test_native_executor_loads_only_the_assembled_source_history(histories, tmp_path):
    _base, source, _database = histories
    runner = MySQLMigrationRunner(
        MaintenanceConnections(SeekDBConfig(path=tmp_path / "seekdb")),
        source,
        evidence_directory=tmp_path / "evidence",
    )
    assert tuple(runner.checksums) == ("r1400", "shared")
    # Construction validates both Alembic chains without opening a native target.
    assert not (tmp_path / "seekdb").exists()


def _append_revision(histories):
    base, source, database = histories
    addition = "ALTER TABLE records ADD COLUMN next_target TEXT"
    final = _fingerprint(
        "CREATE TABLE records (value TEXT)",
        "ALTER TABLE records ADD COLUMN marker TEXT",
        "ALTER TABLE records ADD COLUMN target TEXT",
        addition,
    )
    for directory in (base.directory, source.source_directory):
        _revision(directory, "later", "shared", addition)
        _revision(directory / "mysql", "later", "shared", addition)
        (directory / "later.json").write_text(
            json.dumps({
                "format": "powercontext.mysql-layouts.v1",
                "revisions": {"later": [addition]},
                "checkpoints": {},
            })
        )
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["sqlite_fingerprints"]["later"] = [final]
        manifest["revision_resources"]["later"] = ["later.json"]
        manifest["mysql_layouts"].append("later.json")
        path.write_text(json.dumps(manifest))
    return (
        MigrationBundle(base.directory),
        MigrationBundle(base.directory, source_directory=source.source_directory),
        database,
    )


@pytest.mark.parametrize("interrupt", [False, True])
def test_source_lineage_survives_later_upgrade_and_recovery(histories, monkeypatch, interrupt):
    first = _runner(histories)
    plan = first.plan(backup_policy="skip")
    first.apply(
        plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy="skip", accept_no_backup=True
    )
    extended = _append_revision(histories)
    runner = _runner(extended)
    plan = runner.plan(backup_policy="skip")
    assert plan.source_revision == "shared" and plan.revisions == ("later",)
    options = {
        "plan_id": plan.plan_id,
        "accepted": True,
        "maintenance_confirmed": True,
        "backup_policy": "skip",
        "accept_no_backup": True,
    }
    if interrupt:
        original = runner._write_evidence

        def stop_before_completion(evidence):
            if evidence["state"] == "complete":
                raise RuntimeError("Interrupted after revision commit")  # noqa: TRY003 -- injected process interruption
            original(evidence)

        with monkeypatch.context() as patch:
            patch.setattr(runner, "_write_evidence", stop_before_completion)
            with pytest.raises(RuntimeError, match="Interrupted after revision commit"):
                runner.apply(**options)
    else:
        runner.apply(**options)
    evidence = json.loads(runner.evidence_path.read_text())
    assert evidence["source_revision"] == "shared", "The current backup origin must not become the historical source"
    assert "r1400" in evidence["release_checksums"]
    reopened = _runner(extended)
    if interrupt:
        plan = reopened.plan(backup_policy="skip")
        assert plan.state == "recovery_required"
        reopened.apply(**{**options, "plan_id": plan.plan_id})
    assert reopened.verify().revision == "later"
    assert reopened.plan().bundle_checksum == extended[1].checksum
    assert not reopened.apply().changed
    with closing(sqlite3.connect(reopened.database)) as connection:
        assert connection.execute("SELECT * FROM records").fetchall() == [("kept", "original", None, None)]
    evidence = json.loads(reopened.evidence_path.read_text())
    evidence["release_checksums"]["r1400"] = "changed"
    reopened.evidence_path.write_text(json.dumps(evidence))
    before = reopened.database.read_bytes()
    with pytest.raises(MigrationError, match="checksum_conflict"):
        _runner(extended).plan()
    assert reopened.database.read_bytes() == before


@pytest.mark.parametrize("origin", ["r1400", "shared"])
def test_conflicting_source_lineages_are_rejected(histories, tmp_path, origin):
    base, source, _database = histories
    other_directory = tmp_path / "other-source"
    shutil.copytree(source.source_directory, other_directory)
    for path in other_directory.rglob("*"):
        if path.is_file():
            path.write_text(path.read_text().replace("r1400", "r1500").replace("1.4.0", "1.5.0"))
    other = MigrationBundle(base.directory, source_directory=other_directory)
    sources = MigrationSources(base, (source, other))
    checksums = {"r1500": other.checksums["r1500"]}
    if origin == "shared":
        checksums["r1400"] = source.checksums["r1400"]
    with pytest.raises(MigrationError, match="recovery_required"):
        sources.from_evidence({"source_revision": origin, "release_checksums": checksums})
