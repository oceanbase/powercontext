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

"""Observable safety and recovery behavior using actual SQLite DDL and files."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text

from powercontext.builtin.persistence.migrations import MigrationBundle, MigrationError, SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.backup import BackupCapabilities, SQLiteBackupProvider

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/database_migrations"


@pytest.fixture
def bundle() -> MigrationBundle:
    return MigrationBundle(FIXTURE)


def load_schema(database: Path, name: str = "pre_dream") -> None:
    statements = json.loads((FIXTURE / "schemas" / f"{name}.json").read_text())["sqlite"]
    with sqlite3.connect(database) as connection:
        for statement in statements:
            connection.execute(statement)


def seed(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO pc_artifacts (scope_id, family, artifact_id, revision, content) VALUES ('s','memory','m',1,:body)"
        ),
        {"body": b'{"content":"original"}'},
    )
    connection.exec_driver_sql(
        "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) VALUES ('s','memory','m',1)"
    )
    connection.exec_driver_sql(
        "INSERT INTO pc_artifact_tags (scope_id,family,artifact_id,target_type,target_id,tag_key_hash,tag_key,tag,assigned_at) "
        "VALUES ('s','memory','m','artifact','m',X'00','keep','Keep','2026-09-28 12:00:00')"
    )
    connection.exec_driver_sql(
        "INSERT INTO pc_artifact_candidate_versions "
        "(scope_id,candidate_id,version,family,proposal,source_refs,artifact_refs) "
        "VALUES ('s','c',1,'memory',X'7B7D',X'5B5D',X'5B5D')"
    )


def populate(database: Path) -> None:
    engine = create_engine("sqlite:///" + str(database))
    try:
        with engine.begin() as connection:
            seed(connection)
    finally:
        engine.dispose()


def apply(runner: SQLiteMigrationRunner, **kwargs: Any):
    return runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True, **kwargs)


def rows(database: Path, query: str):
    with sqlite3.connect(database) as connection:
        return connection.execute(query).fetchall()


def test_read_only_commands_do_not_create_missing_database_or_parent(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "absent" / "nested" / "pc.sqlite3"
    runner = SQLiteMigrationRunner(database, bundle)
    assert runner.plan().state == "uninitialized"
    with pytest.raises(MigrationError, match="uninitialized"):
        runner.verify()
    with pytest.raises(MigrationError, match="confirmation_required"):
        runner.apply()
    assert not list(tmp_path.iterdir())


def test_empty_initialization_and_noop_apply(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "new.sqlite3"
    runner = SQLiteMigrationRunner(database, bundle)
    result = apply(runner)
    assert result.changed and result.backup_state == "not_required" and result.backup_ref is None
    assert runner.verify().revision == "p0003"
    before = database.read_bytes()
    files = sorted(str(path) for path in tmp_path.rglob("*"))
    assert not runner.apply().changed
    assert database.read_bytes() == before
    assert sorted(str(path) for path in tmp_path.rglob("*")) == files
    assert rows(database, "SELECT name FROM sqlite_schema WHERE name LIKE 'pc_migration_%'") == []


def test_distinct_databases_can_migrate_in_one_process_without_crossing_contexts(
    tmp_path: Path, bundle: MigrationBundle
) -> None:
    runners = [SQLiteMigrationRunner(tmp_path / f"database-{index}.sqlite3", bundle) for index in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(apply, runners))
    assert all(result.changed and result.backup_state == "not_required" for result in results)
    for runner in runners:
        assert runner.verify().state == "ready"
        assert rows(runner.database, "SELECT version_num FROM pc_schema_revision") == [("p0003",)]


@pytest.mark.parametrize("source", ["pre_dream", "v1_1_0"])
def test_known_historical_tables_preserve_data_and_indexes(
    tmp_path: Path, bundle: MigrationBundle, source: str
) -> None:
    database = tmp_path / "legacy.sqlite3"
    load_schema(database, source)
    populate(database)
    runner = SQLiteMigrationRunner(database, bundle)
    original = database.read_bytes()
    plan = runner.plan()
    assert plan.adopt_baseline
    with pytest.raises(MigrationError, match="migration_required"):
        runner.verify()
    assert database.read_bytes() == original
    result = apply(runner)
    assert result.backup_ref
    backup = Path(result.backup_ref)
    assert SQLiteMigrationRunner(backup, bundle).plan().schema_fingerprint == plan.schema_fingerprint
    assert rows(backup, "SELECT content FROM pc_artifacts") == [(b'{"content":"original"}',)]
    assert rows(database, "SELECT content FROM pc_artifacts") == [(b'{"content":"original"}',)]
    assert rows(database, "SELECT tag, tag_key_hash FROM pc_artifact_tags") == [("Keep", b"\x00")]
    assert rows(database, "SELECT proposal, memory_citations FROM pc_artifact_candidate_versions") == [(b"{}", None)]
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) VALUES ('s','topic-memory','t',1,X'7B7D')"
        )
        connection.execute(
            "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) VALUES ('s','topic-memory','t',1)"
        )
        connection.execute(
            "INSERT INTO pc_artifact_tags VALUES ('s','topic-memory','t','artifact','t',X'01','new','New','2026-09-28')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE pc_artifact_tags SET target_id='not-the-artifact' WHERE artifact_id='t'")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        indexes = {item[1] for item in connection.execute("PRAGMA index_list(pc_artifact_tags)")}
        assert {"ix_pc_artifact_tags_family_key", "ix_pc_artifact_tags_key"} <= indexes
    assert runner.verify().state == "ready"


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE surprise (id INTEGER)",
        "CREATE TABLE pc_artifact_tags_topic_memory (id INTEGER)",
        "CREATE TABLE pc_schema_revision (version_num TEXT)",
    ],
)
def test_unknown_or_partial_schema_is_rejected_without_writes(
    tmp_path: Path, bundle: MigrationBundle, ddl: str
) -> None:
    database = tmp_path / "unknown.sqlite3"
    load_schema(database)
    with sqlite3.connect(database) as connection:
        connection.execute(ddl)
    before = database.read_bytes()
    runner = SQLiteMigrationRunner(database, bundle)
    with pytest.raises(MigrationError, match=r"unknown_baseline|recovery_required"):
        apply(runner)
    assert database.read_bytes() == before
    assert len(list(tmp_path.iterdir())) == 1


def test_unknown_newer_revision_never_downgrades(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "newer.sqlite3"
    runner = SQLiteMigrationRunner(database, bundle)
    apply(runner)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE pc_schema_revision SET version_num='future-release'")
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="incompatible_schema"):
        runner.apply(accepted=True, maintenance_confirmed=True, plan_id="anything")
    assert database.read_bytes() == before


def test_changed_configuration_requires_a_new_plan(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "new.sqlite3"
    reviewed = SQLiteMigrationRunner(database, bundle, configuration={"projection": "fts"}).plan()
    runner = SQLiteMigrationRunner(database, bundle, configuration={"projection": "hybrid"})
    with pytest.raises(MigrationError, match="plan_changed"):
        runner.apply(plan_id=reviewed.plan_id, accepted=True, maintenance_confirmed=True)
    assert not database.exists()


def test_confirmation_and_stopped_writes_are_both_required(tmp_path: Path, bundle: MigrationBundle) -> None:
    runner = SQLiteMigrationRunner(tmp_path / "new.sqlite3", bundle)
    with pytest.raises(MigrationError, match="confirmation_required"):
        runner.apply(accepted=True, maintenance_confirmed=True)
    with pytest.raises(MigrationError, match="maintenance_required"):
        runner.apply(plan_id=runner.plan().plan_id, accepted=True)
    assert not list(tmp_path.iterdir())


def test_backup_failure_precedes_schema_writes(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "legacy.sqlite3"
    load_schema(database)
    populate(database)
    database.with_name(database.name + ".pc-migration-backups").write_text("not a directory")
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="backup_failed"):
        apply(SQLiteMigrationRunner(database, bundle))
    assert database.read_bytes() == before
    assert not rows(database, "SELECT name FROM sqlite_schema WHERE name='pc_schema_revision'")


def test_consistent_backup_includes_committed_wal_data_and_can_be_restored(
    tmp_path: Path, bundle: MigrationBundle
) -> None:
    database = tmp_path / "wal.sqlite3"
    load_schema(database)
    populate(database)
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE pc_artifact_tags SET tag='Committed in WAL'")
        writer.commit()
        assert Path(str(database) + "-wal").stat().st_size > 0
        result = apply(SQLiteMigrationRunner(database, bundle))
        assert result.backup_ref
        backup = Path(result.backup_ref)
        assert rows(backup, "SELECT tag FROM pc_artifact_tags") == [("Committed in WAL",)]
        assert "memory_citations" not in {row[1] for row in rows(backup, "PRAGMA table_info(pc_artifacts)")}
        restored = tmp_path / "restored.sqlite3"
        shutil.copy2(backup, restored)
        assert SQLiteMigrationRunner(restored, bundle).plan().source_revision == "p0001"
        assert rows(restored, "PRAGMA integrity_check") == [("ok",)]


def test_current_writer_blocks_even_with_confirmation(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "busy.sqlite3"
    load_schema(database)
    with sqlite3.connect(database) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(MigrationError, match="active_writers"):
            apply(SQLiteMigrationRunner(database, bundle))
    assert not rows(database, "SELECT name FROM sqlite_schema WHERE name='pc_schema_revision'")
    assert not database.with_name(database.name + ".pc-migration-backups").exists()


@pytest.mark.parametrize("revision", ["p0001", "p0002", "p0003"])
@pytest.mark.parametrize("after_commit", [False, True])
def test_interruption_at_each_revision_boundary_retries_from_verified_revision(
    tmp_path: Path, bundle: MigrationBundle, monkeypatch: pytest.MonkeyPatch, revision: str, after_commit: bool
) -> None:
    database = tmp_path / "interrupted.sqlite3"
    runner = SQLiteMigrationRunner(database, bundle)
    real_upgrade = command.upgrade
    real_commit = Connection.commit
    interrupted = False

    def upgrade(config, target, **kwargs):
        real_upgrade(config, target, **kwargs)
        if target == revision:
            raise InterruptedError("Injected after real DDL, before commit")  # noqa: TRY003

    def commit(connection):
        nonlocal interrupted
        present = connection.exec_driver_sql("SELECT name FROM sqlite_schema WHERE name='pc_schema_revision'").first()
        current = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar() if present else None
        real_commit(connection)
        if current == revision and not interrupted:
            interrupted = True
            raise InterruptedError("Injected after commit")  # noqa: TRY003

    with monkeypatch.context() as injection:
        injection.setattr(Connection, "commit", commit) if after_commit else injection.setattr(
            command, "upgrade", upgrade
        )
        with pytest.raises(InterruptedError):
            apply(runner)
    assert runner.plan().state == "recovery_required"
    with pytest.raises(MigrationError, match="recovery_required"):
        runner.verify()
    result = apply(runner)
    assert result.backup_state == "not_required"
    assert result.backup_ref is None
    assert rows(database, "SELECT version_num FROM pc_schema_revision") == [("p0003",)]
    assert rows(database, "SELECT name FROM sqlite_schema WHERE name LIKE 'pc_migration_%'") == []
    assert runner.verify().state == "ready"


@pytest.mark.parametrize("resource", ["versions/p0002_citations.py", "schemas/pre_dream.json"])
def test_applied_revision_checksum_conflict_is_rejected(tmp_path: Path, resource: str) -> None:
    copied = tmp_path / "bundle"
    shutil.copytree(FIXTURE, copied)
    database = tmp_path / "pc.sqlite3"
    apply(SQLiteMigrationRunner(database, MigrationBundle(copied)))
    script = copied / resource
    script.write_text(script.read_text() + "\n")
    with pytest.raises(MigrationError, match="checksum_conflict"):
        SQLiteMigrationRunner(database, MigrationBundle(copied)).verify()


def test_retry_requires_the_original_backup(
    tmp_path: Path, bundle: MigrationBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    populate(database)
    runner = SQLiteMigrationRunner(database, bundle)
    real_upgrade = command.upgrade

    def interrupted(config, target, **kwargs):
        real_upgrade(config, target, **kwargs)
        raise InterruptedError

    with monkeypatch.context() as injection:
        injection.setattr(command, "upgrade", interrupted)
        with pytest.raises(InterruptedError):
            apply(runner)
    backups = list(tmp_path.glob("*.pc-migration-backups/*.sqlite3"))
    assert len(backups) == 1
    backups[0].write_bytes(b"not the original recovery point")
    before = runner.database.read_bytes()
    with pytest.raises(MigrationError, match="backup_required"):
        apply(runner)
    assert runner.database.read_bytes() == before
    assert list(tmp_path.glob("*.pc-migration-backups/*.sqlite3")) == backups


def test_missing_original_evidence_does_not_back_up_partial_state(
    tmp_path: Path, bundle: MigrationBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    runner = SQLiteMigrationRunner(database, bundle)
    upgrade = command.upgrade

    def interrupted(config, target, **kwargs):
        upgrade(config, target, **kwargs)
        if target == "p0003":
            raise InterruptedError

    with monkeypatch.context() as injection:
        injection.setattr(command, "upgrade", interrupted)
        with pytest.raises(InterruptedError):
            apply(runner)
    assert rows(database, "SELECT version_num FROM pc_schema_revision") == [("p0002",)]
    backups = list(tmp_path.glob("*.pc-migration-backups/*.sqlite3"))
    assert len(backups) == 1
    runner.evidence_path.unlink()
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="recovery_required"):
        apply(SQLiteMigrationRunner(database, bundle))
    assert database.read_bytes() == before
    assert list(tmp_path.glob("*.pc-migration-backups/*.sqlite3")) == backups


def test_incomplete_external_evidence_reports_recovery_error(tmp_path: Path, bundle: MigrationBundle) -> None:
    runner = SQLiteMigrationRunner(tmp_path / "pc.sqlite3", bundle)
    apply(runner)
    evidence = json.loads(runner.evidence_path.read_text())
    del evidence["bundle_checksum"]
    runner.evidence_path.write_text(json.dumps(evidence))
    with pytest.raises(MigrationError, match="recovery_required"):
        runner.plan()


def test_integrity_failure_blocks_readiness(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "corrupt.sqlite3"
    apply(SQLiteMigrationRunner(database, bundle))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO pc_artifact_tags VALUES ('missing','memory','a','artifact','a',X'00','k','t','2026-09-28')"
        )
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="verification_failed"):
        SQLiteMigrationRunner(database, bundle).verify()
    assert database.read_bytes() == before


def test_another_process_cannot_migrate_through_a_symlink_then_lock_owner_exit_releases(
    tmp_path: Path, bundle: MigrationBundle
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(database)
    code = (
        "from pathlib import Path; import sys; "
        "from powercontext.builtin.persistence.migrations.locking import local_migration_lock; "
        "lock=local_migration_lock(Path(sys.argv[1])); lock.__enter__(); "
        "print('locked', flush=True); sys.stdin.read()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(database)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout and process.stdout.readline().strip() == "locked"
        with pytest.raises(MigrationError, match="migration_locked"):
            apply(SQLiteMigrationRunner(alias, bundle))
    finally:
        process.terminate()
        process.wait(timeout=10)
    assert apply(SQLiteMigrationRunner(database, bundle)).state == "ready"


def test_batch_rebuild_preserves_registered_triggers_and_foreign_keys(bundle: MigrationBundle) -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        command.upgrade(bundle.config(connection), "p0002")
        seed(connection)
        connection.exec_driver_sql("CREATE TABLE tag_audit (tag TEXT)")
        connection.exec_driver_sql(
            "CREATE TRIGGER registered_tag_audit AFTER UPDATE ON pc_artifact_tags "
            "BEGIN INSERT INTO tag_audit VALUES (new.tag); END"
        )
        command.upgrade(bundle.config(connection), "p0003")
        connection.exec_driver_sql("UPDATE pc_artifact_tags SET tag='Changed'")
        assert connection.exec_driver_sql("SELECT tag FROM tag_audit").all() == [("Changed",)]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert {item["name"] for item in inspect(connection).get_check_constraints("pc_artifact_tags")} == {
            "ck_pc_artifact_tags_target"
        }
        connection.commit()
    engine.dispose()


@pytest.mark.parametrize("policy", ["manual", "skip"])
def test_operator_backup_choices_do_not_inspect_manual_backups(
    tmp_path: Path, bundle: MigrationBundle, monkeypatch: pytest.MonkeyPatch, policy: Literal["manual", "skip"]
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    provider = SQLiteBackupProvider(database)

    def forbidden(*args, **kwargs):
        pytest.fail("An operator backup declaration must not call the automatic provider")

    monkeypatch.setattr(provider, "capabilities", forbidden)
    monkeypatch.setattr(provider, "create_backup", forbidden)
    monkeypatch.setattr(provider, "inspect_backup", forbidden)
    runner = SQLiteMigrationRunner(database, bundle, backup_provider=provider)
    plan = runner.plan(backup_policy=policy)
    with pytest.raises(MigrationError, match="backup_confirmation_required"):
        runner.apply(plan_id=plan.plan_id, backup_policy=policy, accepted=True, maintenance_confirmed=True)
    result = runner.apply(
        plan_id=plan.plan_id,
        backup_policy=policy,
        accepted=True,
        maintenance_confirmed=True,
        backup_confirmed=policy == "manual",
        accept_no_backup=policy == "skip",
        backup_ref="unreachable://user-backup",
    )
    assert result.backup_state == ("user_confirmed" if policy == "manual" else "skipped")
    assert not database.with_name(database.name + ".pc-migration-backups").exists()
    assert runner.verify().state == "ready"


def test_unavailable_automatic_backup_blocks_before_writing(
    tmp_path: Path, bundle: MigrationBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    provider = SQLiteBackupProvider(database)
    monkeypatch.setattr(
        provider,
        "capabilities",
        lambda _: BackupCapabilities(
            available=False,
            product="sqlite",
            reasons=("Native backup is unavailable.",),
        ),
    )
    runner = SQLiteMigrationRunner(database, bundle, backup_provider=provider)
    before = database.read_bytes()
    assert not runner.plan().backup_available
    with pytest.raises(MigrationError, match="backup_unsupported"):
        apply(runner)
    assert database.read_bytes() == before
    assert not runner.evidence_path.exists()


@pytest.mark.parametrize("policy", ["manual", "skip"])
def test_failed_backup_before_ddl_allows_explicit_new_choice(
    tmp_path: Path, bundle: MigrationBundle, monkeypatch: pytest.MonkeyPatch, policy: Literal["manual", "skip"]
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    provider = SQLiteBackupProvider(database)

    def unavailable(context):
        raise MigrationError("backup_failed", "Insufficient backup storage.")

    monkeypatch.setattr(provider, "create_backup", unavailable)
    runner = SQLiteMigrationRunner(database, bundle, backup_provider=provider)
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="backup_failed"):
        apply(runner)
    assert database.read_bytes() == before
    revised = runner.plan(backup_policy=policy)
    result = runner.apply(
        plan_id=revised.plan_id,
        backup_policy=policy,
        accepted=True,
        maintenance_confirmed=True,
        backup_confirmed=policy == "manual",
        accept_no_backup=policy == "skip",
    )
    assert result.backup_state == ("user_confirmed" if policy == "manual" else "skipped")


def test_changed_bundle_during_backup_is_not_executed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copied = tmp_path / "bundle"
    shutil.copytree(FIXTURE, copied)
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    provider = SQLiteBackupProvider(database)
    backup = provider.create_backup
    script = copied / "versions/p0002_citations.py"

    def change_source(context):
        ref = backup(context)
        script.write_text(script.read_text() + "\n# Changed during backup\n")
        return ref

    monkeypatch.setattr(provider, "create_backup", change_source)
    runner = SQLiteMigrationRunner(database, MigrationBundle(copied), backup_provider=provider)
    with pytest.raises(MigrationError, match="plan_changed"):
        apply(runner)
    assert "memory_citations" not in {row[1] for row in rows(database, "PRAGMA table_info(pc_artifacts)")}
    assert rows(database, "SELECT name FROM sqlite_schema WHERE name='pc_schema_revision'") == []
    assert len(list(tmp_path.glob("*.pc-migration-backups/*.sqlite3"))) == 1


def test_alembic_executes_reviewed_bytes_when_sources_change_between_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "bundle"
    shutil.copytree(FIXTURE, copied)
    database = tmp_path / "pc.sqlite3"
    runner = SQLiteMigrationRunner(database, MigrationBundle(copied))
    reviewed = runner.plan()
    upgrade = command.upgrade
    script = copied / "versions/p0002_citations.py"

    def alter_installed_script(config, target, **kwargs):
        upgrade(config, target, **kwargs)
        if target == "p0001":
            script.write_text(
                script.read_text().replace(
                    "def upgrade() -> None:",
                    "def upgrade() -> None:\n    op.execute('CREATE TABLE unapproved (id INTEGER)')",
                )
            )

    monkeypatch.setattr(command, "upgrade", alter_installed_script)
    result = runner.apply(plan_id=reviewed.plan_id, accepted=True, maintenance_confirmed=True)
    assert result.state == "ready"
    assert rows(database, "SELECT name FROM sqlite_schema WHERE name='unapproved'") == []
    assert rows(database, "SELECT version_num FROM pc_schema_revision") == [("p0003",)]
    with pytest.raises(MigrationError, match="checksum_conflict"):
        SQLiteMigrationRunner(database, MigrationBundle(copied)).verify()


def test_invalid_sqlite_file_reports_operator_error(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "pc.sqlite3"
    database.write_text("not a SQLite database")
    with pytest.raises(MigrationError, match="invalid_database"):
        SQLiteMigrationRunner(database, bundle).plan()


def test_replaced_table_is_retained_across_verification_and_retry(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    populate(database)
    runner = SQLiteMigrationRunner(database, bundle)
    apply(runner)
    expected = [("Keep",)]
    assert rows(database, "SELECT tag FROM pc_retained_p0002_artifact_tags") == expected
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("DELETE FROM pc_artifact_heads")
    assert rows(database, "SELECT tag FROM pc_artifact_tags") == []
    assert runner.verify().state == "ready"
    assert not runner.apply().changed
    assert rows(database, "SELECT tag FROM pc_retained_p0002_artifact_tags") == expected


@pytest.mark.parametrize("remove_retained", [False, True])
def test_committed_revision_retains_old_table_before_completion_evidence(
    tmp_path: Path, bundle: MigrationBundle, remove_retained: bool
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    populate(database)
    code = """
import os
import sys
from pathlib import Path
from sqlalchemy import Connection
from powercontext.builtin.persistence.migrations import MigrationBundle, SQLiteMigrationRunner

commit = Connection.commit
def crash_after_commit(connection):
    present = connection.exec_driver_sql("SELECT name FROM sqlite_schema WHERE name='pc_schema_revision'").first()
    current = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar() if present else None
    commit(connection)
    if current == 'p0003':
        os._exit(73)
Connection.commit = crash_after_commit
runner = SQLiteMigrationRunner(Path(sys.argv[1]), MigrationBundle(Path(sys.argv[2])))
runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)
"""
    process = subprocess.run([sys.executable, "-c", code, str(database), str(FIXTURE)], check=False, timeout=30)
    assert process.returncode == 73
    assert rows(database, "SELECT version_num FROM pc_schema_revision") == [("p0003",)]
    assert rows(database, "SELECT tag FROM pc_retained_p0002_artifact_tags") == [("Keep",)]
    runner = SQLiteMigrationRunner(database, bundle)
    evidence = runner.evidence_path.read_bytes()

    if remove_retained:
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE pc_retained_p0002_artifact_tags")
        for operation in (runner.plan, lambda: apply(runner), runner.verify):
            with pytest.raises(MigrationError, match="recovery_required"):
                operation()
        assert runner.evidence_path.read_bytes() == evidence
        assert rows(database, "SELECT tag FROM pc_artifact_tags") == [("Keep",)]
    else:
        assert runner.plan().state == "recovery_required"
        assert apply(runner).state == "ready"
        assert runner.verify().state == "ready"
        assert rows(database, "SELECT tag FROM pc_retained_p0002_artifact_tags") == [("Keep",)]


def test_adopting_current_baseline_does_not_require_an_unexecuted_retention_revision(
    tmp_path: Path, bundle: MigrationBundle
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database, "v1_1_0")
    populate(database)
    runner = SQLiteMigrationRunner(database, bundle)
    assert runner.plan().source_revision == "p0003"
    assert apply(runner).state == "ready"
    assert runner.verify().state == "ready"
    assert rows(database, "SELECT name FROM sqlite_schema WHERE name='pc_retained_p0002_artifact_tags'") == []
    assert rows(database, "SELECT tag FROM pc_artifact_tags") == [("Keep",)]


def test_a_new_maintenance_window_preserves_existing_retention_requirements(
    tmp_path: Path, bundle: MigrationBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    populate(database)
    apply(SQLiteMigrationRunner(database, bundle))
    copied = tmp_path / "bundle"
    shutil.copytree(FIXTURE, copied)
    (copied / "versions/p0004.py").write_text(
        'revision = "p0004"\ndown_revision = "p0003"\ndef upgrade():\n    pass\n', encoding="utf-8"
    )
    manifest = json.loads((copied / "manifest.json").read_text())
    manifest["revision_resources"]["p0004"] = []
    manifest["sqlite_fingerprints"]["p0004"] = manifest["sqlite_fingerprints"]["p0003"]
    (copied / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    runner = SQLiteMigrationRunner(database, MigrationBundle(copied))

    def interrupt(config, target, **kwargs):
        raise InterruptedError

    with monkeypatch.context() as injection:
        injection.setattr(command, "upgrade", interrupt)
        with pytest.raises(InterruptedError):
            apply(runner)
    assert rows(database, "SELECT version_num FROM pc_schema_revision") == [("p0003",)]
    evidence = runner.evidence_path.read_bytes()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE pc_retained_p0002_artifact_tags")
    for operation in (runner.plan, lambda: apply(runner), runner.verify):
        with pytest.raises(MigrationError, match="recovery_required"):
            operation()
    assert runner.evidence_path.read_bytes() == evidence


def test_retained_table_cannot_precede_its_revision(tmp_path: Path, bundle: MigrationBundle) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    with sqlite3.connect(database) as connection:
        original = connection.execute("SELECT sql FROM sqlite_schema WHERE name='pc_artifact_tags'").fetchone()[0]
        connection.execute(
            original.replace("CREATE TABLE pc_artifact_tags", "CREATE TABLE pc_retained_p0002_artifact_tags")
        )
    before = database.read_bytes()
    with pytest.raises(MigrationError, match="recovery_required"):
        apply(SQLiteMigrationRunner(database, bundle))
    assert database.read_bytes() == before


@pytest.mark.parametrize("policy", ["manual", "skip"])
def test_partial_migration_can_reconfirm_backup_policy_without_replacing_original(
    tmp_path: Path,
    bundle: MigrationBundle,
    monkeypatch: pytest.MonkeyPatch,
    policy: Literal["manual", "skip"],
) -> None:
    database = tmp_path / "pc.sqlite3"
    load_schema(database)
    populate(database)
    runner = SQLiteMigrationRunner(database, bundle)
    original_upgrade = command.upgrade

    def interrupt(config, target, **kwargs):
        if target == "p0003":
            raise InterruptedError
        original_upgrade(config, target, **kwargs)

    with monkeypatch.context() as injection:
        injection.setattr(command, "upgrade", interrupt)
        with pytest.raises(InterruptedError):
            apply(runner)
    assert rows(database, "SELECT version_num FROM pc_schema_revision") == [("p0002",)]
    evidence = json.loads(runner.evidence_path.read_text())
    backup = Path(evidence["backup"]["location"])
    backup.write_bytes(b"damaged recovery point")
    old_plan = runner.plan()
    plan = runner.plan(backup_policy=policy)
    assert plan.plan_id != old_plan.plan_id
    with pytest.raises(MigrationError, match="plan_changed"):
        runner.apply(plan_id=old_plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy=policy)
    with pytest.raises(MigrationError, match="backup_confirmation_required"):
        runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True, backup_policy=policy)
    result = runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy=policy,
        backup_confirmed=policy == "manual",
        accept_no_backup=policy == "skip",
        backup_ref="user declaration",
    )
    assert runner.verify().state == "ready"
    assert result.backup_state == ("user_confirmed" if policy == "manual" else "skipped")
    final = json.loads(runner.evidence_path.read_text())
    assert final["backup_history"][0]["backup"] == evidence["backup"]
    assert final["maintenance_window_id"] == evidence["maintenance_window_id"]
    assert final["source_revision"] == evidence["source_revision"]
    assert backup.read_bytes() == b"damaged recovery point"
