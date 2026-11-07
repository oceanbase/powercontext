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

"""Real schema replay; each native case owns a dedicated scratch database.

The shared fixtures keep a seekDB engine lease during one scenario. Connection
interruption is exercised here; native engine crash acceptance remains separate.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from replay_candidate_migration import CandidateReplay, ReplayInterrupted, inventory, objects
from sqlalchemy import create_engine, inspect, text

from powercontext.builtin.persistence.migrations.models import digest
from tests.builtin.persistence import test_full_server_migrations as sqlite_fixtures
from tests.builtin.persistence import test_mysql_migration_runner as native_fixtures

sqlite_source = sqlite_fixtures.source
backend = native_fixtures.backend

_INTERRUPTIONS = {
    "complete": None,
    "copy": "INSERT",
    "rename": "ALTER TABLE pc_artifact_candidate_versions RENAME TO pc_candidate_versions",
}


def _directory(tmp_path, backend, scenario):
    base = Path(os.environ.get("POWERCONTEXT_CANDIDATE_REPLAY_OUTPUT", str(tmp_path / "replay")))
    return base / f"{backend}-{scenario}"


def _seed(connection, scope):
    for number in (1, 2):
        connection.execute(
            text(
                "INSERT INTO pc_artifact_candidate_versions "
                "(scope_id,candidate_id,version,family,proposal,source_refs,artifact_refs) "
                "VALUES (:scope,'candidate',:version,'custom-family',:proposal,:refs,:refs)"
            ),
            {"scope": scope, "version": number, "proposal": f'{{"history":{number}}}'.encode(), "refs": b"[]"},
        )
    connection.execute(
        text(
            "INSERT INTO pc_artifact_candidate_heads (scope_id,candidate_id,family,version,status) "
            "VALUES (:scope,'candidate','custom-family',2,'pending')"
        ),
        {"scope": scope},
    )
    connection.execute(
        text(
            "INSERT INTO pc_profile_policies "
            "(scope_id,generation_enabled,activation_mode,pending_candidate_id,version,updated_at) "
            "VALUES (:scope,0,'review_required','candidate',1,'2026-01-01 00:00:00')"
        ),
        {"scope": scope},
    )
    connection.commit()


def _history(connection, bundle, *, upgraded=False):
    entries = json.loads((bundle.snapshot_directory / "dream/schema.json").read_text())["tables"]
    rows = {}
    for name, entry in entries.items():
        table = entry["target_name"] if upgraded else entry["source_name"]
        columns = ", ".join(entry["source_columns"])
        order = "scope_id, candidate_id" + (", version" if name.endswith("versions") else "")
        values = [
            tuple(row)
            for row in connection.exec_driver_sql(
                f"SELECT {columns} FROM {table} ORDER BY {order}"  # noqa: S608 -- frozen identifiers
            )
        ]
        rows[name] = values
        if upgraded:
            retained = entry["retained_name"]
            assert values == [
                tuple(row)
                for row in connection.exec_driver_sql(
                    f"SELECT {columns} FROM {retained} ORDER BY {order}"  # noqa: S608 -- frozen identifiers
                )
            ]
    return rows


def _history_digest(rows):
    return digest({
        name: [[value.hex() if isinstance(value, bytes) else value for value in row] for row in values]
        for name, values in rows.items()
    })


def _foreign_keys(connection):
    assert any(
        key["referred_table"] == "pc_candidate_versions"
        for key in inspect(connection).get_foreign_keys("pc_candidate_heads")
    )
    assert any(
        key["referred_table"] == "pc_candidate_heads"
        for key in inspect(connection).get_foreign_keys("pc_profile_policies")
    )


def _cross_table_effects(replay):
    version_rename = next(
        record
        for record in replay.records
        if record["operation"].startswith("ALTER TABLE pc_artifact_candidate_versions RENAME")
    )
    assert "table:pc_artifact_candidate_heads" in version_rename["changes"]
    head_rename = next(
        record
        for record in replay.records
        if record["operation"].startswith("ALTER TABLE pc_artifact_candidate_heads RENAME")
    )
    assert "table:pc_profile_policies" in head_rename["changes"]
    # The generated changes must reconstruct each full inventory, including
    # objects not named by the SQL. Never infer a delta from the ALTER target.
    reconstructed = {}
    for record in replay.records:
        for name, value in record["changes"].items():
            if value is None:
                reconstructed.pop(name)
            else:
                reconstructed[name] = value
        state = json.loads((replay.output / record["inventory"]).read_text())
        assert reconstructed == objects(state)
        assert digest(state) == record["schema_sha256"]


@pytest.mark.parametrize("scenario", _INTERRUPTIONS)
def test_candidate_replay_sqlite(sqlite_source, tmp_path, scenario):
    runner = sqlite_source
    replay = CandidateReplay(
        runner.bundle, "sqlite", _directory(tmp_path, "sqlite", scenario), interrupt=_INTERRUPTIONS[scenario]
    )
    interrupted = None
    engine = create_engine(f"sqlite:///{runner.database}")
    try:
        with engine.connect() as connection:
            _seed(connection, "scope")
            original = _history(connection, runner.bundle)
            baseline = replay.begin(connection)
        if scenario != "complete":
            with replay.sqlite(), pytest.raises(ReplayInterrupted):
                sqlite_fixtures.apply(runner)
            interrupted = json.loads(runner.evidence_path.read_text())
            with engine.connect() as connection:
                assert replay.capture(connection, "after rollback") == baseline
                assert _history(connection, runner.bundle) == original
            assert runner.plan().state == "recovery_required"
        with replay.sqlite():
            result = sqlite_fixtures.apply(runner)
        if scenario != "complete":
            assert interrupted is not None
            completed = json.loads(runner.evidence_path.read_text())
            assert completed["maintenance_window_id"] == interrupted["maintenance_window_id"]
            assert result.backup_ref == interrupted["backup"]["location"]
        assert runner.verify().state == "ready"
        with engine.connect() as connection:
            assert _history(connection, runner.bundle, upgraded=True) == original
            _foreign_keys(connection)
            final = inventory(connection)
        assert not sqlite_fixtures.apply(runner).changed
        with engine.connect() as connection:
            assert inventory(connection) == final
            assert _history(connection, runner.bundle, upgraded=True) == original
        _cross_table_effects(replay)
        assert any(record.get("matches_registered_checkpoint") for record in replay.records)
        replay.finish(
            history_sha256=_history_digest(original),
            repeat_apply_changed=False,
            interruption=scenario,
            interruption_semantics="transactional rollback",
            final_schema_sha256=digest(final),
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize("scenario", _INTERRUPTIONS)
def test_candidate_replay_native(backend, tmp_path, scenario):
    native_fixtures._load_full_server_baseline(backend)
    runner = native_fixtures._full_server_runner(backend)
    replay = CandidateReplay(
        runner.bundle,
        backend.config.kind,
        _directory(tmp_path, backend.config.kind, scenario),
        interrupt=_INTERRUPTIONS[scenario],
    )

    def seed(connection, _identity, _guard):
        _seed(connection, "historical")
        return _history(connection, runner.bundle), replay.begin(connection)

    original, baseline = backend.connections().run(seed, writable=True)
    interrupted = None
    journal = None
    if scenario != "complete":
        with replay.native(runner.schema), pytest.raises(ReplayInterrupted):
            native_fixtures._apply(runner, policy="manual")
        (journal,) = backend.evidence_directory.rglob("maintenance.json")
        interrupted = json.loads(journal.read_text())
        assert interrupted["pending_revision"] == "s1400"

        def inspect_interruption(connection, _identity, _guard):
            state = replay.capture(connection, "after connection rollback")
            assert state != baseline  # MySQL DDL survives rollback/disconnection.
            if scenario == "copy":
                # The copy DML rolled back, but its preceding CREATE committed.
                assert (
                    connection.exec_driver_sql("SELECT COUNT(*) FROM pc_retained_dream_candidate_versions").scalar_one()
                    == 0
                )
            return state

        backend.connections().run(inspect_interruption)
        recovery = native_fixtures._full_server_runner(backend).plan(backup_policy="manual")
        assert recovery.state == "recovery_required"
        token = (recovery.coordination or {}).get("pending_run_id")
        # The injected interruption follows a returned statement and a closed
        # owned connection. Only that controlled case permits acknowledgement.
        runner = native_fixtures._full_server_runner(backend, acknowledge_previous_run=token)
    with replay.native(runner.schema):
        native_fixtures._apply(runner, policy="manual")
    assert runner.verify().state == "ready"
    if scenario != "complete":
        assert journal is not None and interrupted is not None
        completed = json.loads(journal.read_text())
        assert completed["maintenance_window_id"] == interrupted["maintenance_window_id"]
        assert completed["manual_ref"] == interrupted["manual_ref"]

    def final(connection, _identity, _guard):
        assert _history(connection, runner.bundle, upgraded=True) == original
        _foreign_keys(connection)
        return inventory(connection)

    state = backend.connections().run(final)
    # The recovery acknowledgement was consumed; a fresh command has no stale token.
    assert not native_fixtures._apply(native_fixtures._full_server_runner(backend), policy="manual").changed
    assert backend.connections().run(final) == state
    _cross_table_effects(replay)
    positions = {
        record["registered_position"] for record in replay.records if record["registered_position"] is not None
    }
    assert positions == set(range(1, len(replay.manifest["registered_operations"]) + 1))
    replay.finish(
        history_sha256=_history_digest(original),
        repeat_apply_changed=False,
        interruption=scenario,
        interruption_semantics="committed DDL, transactional data copy",
        final_schema_sha256=digest(state),
    )
