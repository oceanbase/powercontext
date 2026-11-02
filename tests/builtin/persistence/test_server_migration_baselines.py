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

"""Recognize complete released schemas without stamping or promising an upgrade."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from powercontext.builtin.persistence.migrations import MigrationError, SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.deployment import production_bundle
from powercontext.builtin.persistence.migrations.server_baselines import (
    recognize_mysql_server_baseline,
    recognize_sqlite_server_baseline,
)

RESOURCE = (
    Path(__file__).resolve().parents[3]
    / "src/powercontext/builtin/persistence/migration_resources/server_baselines/1.3.0/sqlite.json"
)


@pytest.fixture
def released_database(tmp_path: Path) -> Path:
    database = tmp_path / "released.db"
    inventory = json.loads(RESOURCE.read_text(encoding="utf-8"))["inventory"]
    with sqlite3.connect(database) as connection:
        # Creating an FTS virtual table also creates its frozen shadow tables.
        for kind in ("table", "index", "trigger", "view"):
            for object_kind, name, _, sql in inventory:
                if (
                    object_kind == kind
                    and connection.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (name,)).fetchone() is None
                ):
                    connection.execute(sql)
        connection.execute(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
            "VALUES ('scope','atomic-memory','historical',1,?)",
            (b'{"historical":"immutable"}',),
        )
    return database


def recognize(database: Path):
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(database.as_uri() + "?mode=ro", uri=True))
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            return recognize_sqlite_server_baseline(connection)
    finally:
        engine.dispose()


def test_complete_release_schema_is_recognized_without_writing_or_claiming_readiness(released_database: Path) -> None:
    before = released_database.read_bytes()
    files = set(released_database.parent.iterdir())
    baseline = recognize(released_database)
    assert baseline is not None
    assert baseline.release == "1.3.0"
    assert baseline.schema_fingerprint == "11e8770068a5fe32dfb25681564dbbe586ba0fd0238ed01adf1b09dde8a49f22"
    runner = SQLiteMigrationRunner(released_database, production_bundle())
    for operation in (runner.plan, runner.verify, runner.apply):
        with pytest.raises(MigrationError, match="server_migration_unavailable"):
            operation()
    assert released_database.read_bytes() == before
    assert set(released_database.parent.iterdir()) == files


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) PRIMARY KEY)",
        "CREATE TABLE pc_unregistered_persistent_state (id INTEGER PRIMARY KEY)",
        "CREATE VIEW unregistered_view AS SELECT artifact_id FROM pc_artifacts",
        "DROP INDEX ix_pc_artifact_tags_key",
        "CREATE INDEX unregistered_index ON pc_artifacts (family)",
        "ALTER TABLE pc_artifacts ADD COLUMN unexpected TEXT",
        "ALTER TABLE pc_artifact_heads RENAME TO altered_heads",
        "CREATE TRIGGER unregistered_trigger AFTER DELETE ON pc_artifacts BEGIN SELECT 1; END",
    ],
)
def test_unregistered_schema_changes_are_not_recognized(released_database: Path, ddl: str) -> None:
    with sqlite3.connect(released_database) as connection:
        connection.execute(ddl)
    before = released_database.read_bytes()
    assert recognize(released_database) is None
    assert released_database.read_bytes() == before


def test_four_table_subset_is_not_a_complete_server_baseline(tmp_path: Path) -> None:
    database = tmp_path / "subset.db"
    selected = {"pc_artifacts", "pc_artifact_heads", "pc_artifact_tags", "pc_artifact_candidate_versions"}
    inventory = json.loads(RESOURCE.read_text(encoding="utf-8"))["inventory"]
    with sqlite3.connect(database) as connection:
        for kind, name, _, sql in inventory:
            if kind == "table" and name in selected:
                connection.execute(sql)
    before = database.read_bytes()
    assert recognize(database) is None
    with pytest.raises(MigrationError, match="unknown_baseline"):
        SQLiteMigrationRunner(database, production_bundle()).plan()
    assert database.read_bytes() == before


def test_weakened_constraint_with_identical_columns_is_not_recognized(released_database: Path) -> None:
    with sqlite3.connect(released_database) as connection:
        original = connection.execute("SELECT sql FROM sqlite_schema WHERE name='pc_artifact_heads'").fetchone()[0]
        assert "CHECK (revision > 0)" in original
        connection.execute("DROP TABLE pc_artifact_heads")
        connection.execute(original.replace("CHECK (revision > 0)", "CHECK (revision >= 0)"))
    before = released_database.read_bytes()
    assert recognize(released_database) is None
    assert released_database.read_bytes() == before


@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
def test_complete_mysql_release_inventory_is_recognized_only_for_its_captured_backend(backend) -> None:
    resource = json.loads(RESOURCE.with_name(f"{backend}.json").read_text(encoding="utf-8"))
    inventory = resource["inventory"]
    before = copy.deepcopy(inventory)
    baseline = recognize_mysql_server_baseline(inventory, backend=backend)
    assert baseline is not None
    assert baseline.release == "1.3.0"
    assert baseline.schema_fingerprint == resource["schema_fingerprint"]
    other_backend = "oceanbase" if backend == "seekdb" else "seekdb"
    assert recognize_mysql_server_baseline(inventory, backend=other_backend) is None
    assert inventory == before


@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
@pytest.mark.parametrize(
    "change",
    [
        "table",
        "control",
        "column",
        "constraint",
        "index",
        "parser",
        "properties",
        "view",
        "trigger",
        "subset",
    ],
)
def test_incomplete_or_altered_mysql_inventories_are_not_released_baselines(change: str, backend) -> None:
    inventory = json.loads(RESOURCE.with_name(f"{backend}.json").read_text(encoding="utf-8"))["inventory"]
    table = inventory["tables"]["pc_artifact_heads"]
    if change == "table":
        del inventory["tables"]["pc_sources"]
    elif change == "control":
        inventory["tables"]["pc_schema_revision"] = {}
    elif change == "column":
        table["columns"]["revision"]["nullable"] = True
    elif change == "constraint":
        table["checks"]["ck_pc_artifact_heads_revision_positive"]["enforced"] = False
    elif change == "index":
        del table["indexes"]["ix_pc_artifact_heads_fts"]
    elif change == "parser":
        table["indexes"]["ix_pc_artifact_heads_fts"]["parser"] = "ngram"
    elif change == "properties":
        table["indexes"]["ix_pc_artifact_heads_fts"]["parser_properties"]["min_token_size"] = 4
    elif change == "view":
        inventory["views"] = ["unknown_view"]
    elif change == "trigger":
        inventory["triggers"] = [{"Trigger": "unknown_trigger", "Table": "pc_artifacts"}]
    else:
        inventory["tables"] = {
            name: shape for name, shape in inventory["tables"].items() if name.startswith("pc_artifact")
        }
    before = copy.deepcopy(inventory)
    assert recognize_mysql_server_baseline(inventory, backend=backend) is None
    assert inventory == before


def test_changed_audit_auto_increment_mode_is_not_a_released_oceanbase_baseline() -> None:
    inventory = json.loads(RESOURCE.with_name("oceanbase.json").read_text(encoding="utf-8"))["inventory"]
    inventory["tables"]["pc_access_audit"]["auto_increment_mode"] = "noorder"
    assert recognize_mysql_server_baseline(inventory, backend="oceanbase") is None
