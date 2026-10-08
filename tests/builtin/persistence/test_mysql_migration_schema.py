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

"""Frozen schema recognition; mocks alone do not establish backend acceptance."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import Connection, create_engine

from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema, mysql_inventory, read_revision
from powercontext.builtin.persistence.seekdb import SeekDBConfig

_RESOURCES = Path(__file__).parents[3] / "src/powercontext/builtin/persistence/migrations/resources"


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def scalar_one(self):
        assert len(self.rows) == 1
        return self.rows[0][0]

    def mappings(self):
        return iter(self.rows)


class _ReflectedDatabase:
    """Scripted SHOW results, not a MySQL engine or backend acceptance test."""

    dialect = SimpleNamespace(name="mysql")

    def __init__(self, tables: dict[str, str]) -> None:
        self.tables = tables
        self.views: list[str] = []
        self.triggers: list[dict[str, str]] = []
        self.heads: list[str] = []

    def exec_driver_sql(self, statement: str) -> _Rows:
        if statement == "SELECT DATABASE()":
            return _Rows([("TestDB",)])
        if statement == "SHOW FULL TABLES":
            return _Rows([(name, "BASE TABLE") for name in self.tables] + [(name, "VIEW") for name in self.views])
        if statement == "SHOW TRIGGERS":
            return _Rows(self.triggers)
        if statement.startswith("SHOW CREATE TABLE "):
            name = statement.removeprefix("SHOW CREATE TABLE ").strip("`")
            return _Rows([(name, self.tables[name])])
        if statement == "SELECT version_num FROM pc_schema_revision LIMIT 2":
            return _Rows([(head,) for head in self.heads[:2]])
        pytest.fail(f"Unexpected query: {statement}")


@pytest.fixture
def schema() -> MySQLSchema:
    return MySQLSchema(MigrationBundle(_RESOURCES))


def _append_clause(statement: str, clause: str) -> str:
    return statement.replace("\n)", ",\n\t" + clause + "\n)")


def _source_statements() -> list[str]:
    return json.loads((_RESOURCES / "schemas/pre_dream.json").read_text())["mysql"]


def _show_tables(*, baseline_prefix: int = 6, citations: int = 0, drop_family_check: bool = False) -> dict[str, str]:
    """Build scripted native output from frozen DDL, including inline indexes."""
    tables: dict[str, str] = {}
    for statement in _source_statements()[:baseline_prefix]:
        if statement.startswith("CREATE TABLE "):
            name = statement.split()[2]
            tables[name] = statement
        else:
            match = re.fullmatch(r"CREATE INDEX (\w+) ON (\w+) (\(.+\))", statement)
            assert match is not None
            index, table, columns = match.groups()
            tables[table] = _append_clause(tables[table], f"KEY {index} {columns}")
    for table in ("pc_artifacts", "pc_artifact_candidate_versions")[:citations]:
        tables[table] = _append_clause(tables[table], "memory_citations MEDIUMBLOB DEFAULT NULL")
    if drop_family_check:
        tables["pc_artifact_tags"] = re.sub(
            r",\s*CONSTRAINT ck_pc_artifact_tags_family CHECK \(family IN \([^)]*\)\)",
            "",
            tables["pc_artifact_tags"],
        )
    return tables


def _inventory(database: _ReflectedDatabase) -> dict[str, Any]:
    return mysql_inventory(cast(Connection, database))


@pytest.mark.parametrize(
    ("citations", "drop_family", "revision"), [(0, False, "p0001"), (2, False, "p0002"), (2, True, "p0003")]
)
def test_complete_frozen_layouts_are_identified_and_verified(
    schema: MySQLSchema, citations: int, drop_family: bool, revision: str
) -> None:
    inventory = _inventory(_ReflectedDatabase(_show_tables(citations=citations, drop_family_check=drop_family)))

    assert schema.identify(inventory) == revision
    schema.verify(inventory, revision)
    with pytest.raises(MigrationError, match="recorded revision"):
        schema.verify(inventory, None)


def test_native_formatting_fk_names_and_optional_generated_indexes_preserve_the_layout(schema: MySQLSchema) -> None:
    tables = _show_tables(citations=2, drop_family_check=True)
    for name, statement in tables.items():
        statement = statement.replace(f"CREATE TABLE {name}", f"CREATE TABLE `{name}`")
        statement = statement.replace("INTEGER", "int(11)").replace("BIGINT", "bigint(20)")
        statement = re.sub(r"FOREIGN KEY\(", "CONSTRAINT `generated_OBFK_123` FOREIGN KEY (", statement)
        statement = re.sub(r"REFERENCES (\w+) ", r"REFERENCES `TestDB`.\1 ", statement)
        statement = statement.replace("ON DELETE RESTRICT", "ON UPDATE NO ACTION ON DELETE NO ACTION")
        statement = statement.replace("CHECK (revision > 0)", "CHECK (((`revision` > 0)))")
        statement += " ORGANIZATION INDEX DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC BLOCK_SIZE=16384"
        tables[name] = statement
    tables["pc_artifact_heads"] = _append_clause(
        tables["pc_artifact_heads"],
        "KEY generated_foreign_key_index (scope_id, family, artifact_id, revision) BLOCK_SIZE 16384 GLOBAL",
    )
    inventory = _inventory(_ReflectedDatabase(tables))

    assert schema.identify(inventory) == "p0003"
    schema.verify(inventory, "p0003")
    assert schema.fingerprint(inventory) == schema.fingerprint(_inventory(_ReflectedDatabase(tables)))


@pytest.mark.parametrize("applied", range(7))
def test_baseline_recovery_accepts_only_the_completed_statement_prefix(schema: MySQLSchema, applied: int) -> None:
    inventory = _inventory(_ReflectedDatabase(_show_tables(baseline_prefix=applied)))

    recovery = schema.recovery(inventory, None)

    assert recovery.revision == "p0001"
    assert recovery.pending_statements == tuple(_source_statements()[applied:])
    assert recovery.completed_revision == ("p0001" if applied == 6 else None)
    if 0 < applied < 6:
        with pytest.raises(MigrationError, match="complete layout"):
            schema.identify(inventory)


@pytest.mark.parametrize("applied", range(3))
def test_citation_recovery_does_not_repeat_committed_add_columns(schema: MySQLSchema, applied: int) -> None:
    inventory = _inventory(_ReflectedDatabase(_show_tables(citations=applied)))

    recovery = schema.recovery(inventory, "p0001")

    assert recovery.revision == "p0002"
    expected = tuple(
        f"ALTER TABLE {table} ADD COLUMN memory_citations MEDIUMBLOB"
        for table in ("pc_artifacts", "pc_artifact_candidate_versions")[applied:]
    )
    assert recovery.pending_statements == expected
    assert recovery.completed_revision == ("p0002" if applied == 2 else None)
    with pytest.raises(MigrationError, match="recorded revision"):
        schema.verify(inventory, "p0002" if applied < 2 else "p0001")


@pytest.mark.parametrize("dropped", [False, True])
def test_check_drop_recovery_requires_the_entire_predecessor_layout(schema: MySQLSchema, dropped: bool) -> None:
    inventory = _inventory(_ReflectedDatabase(_show_tables(citations=2, drop_family_check=dropped)))

    recovery = schema.recovery(inventory, "p0002")

    assert recovery.revision == "p0003"
    assert recovery.pending_statements == (
        () if dropped else ("ALTER TABLE pc_artifact_tags DROP CHECK ck_pc_artifact_tags_family",)
    )
    assert recovery.completed_revision == ("p0003" if dropped else None)


def test_unordered_or_incompatible_partial_ddl_cannot_be_recovered(schema: MySQLSchema) -> None:
    tables = _show_tables()
    tables["pc_artifact_candidate_versions"] = _append_clause(
        tables["pc_artifact_candidate_versions"], "memory_citations MEDIUMBLOB"
    )
    with pytest.raises(MigrationError, match="controlled"):
        schema.recovery(_inventory(_ReflectedDatabase(tables)), "p0001")
    tables = _show_tables(citations=1)
    tables["pc_artifacts"] = tables["pc_artifacts"].replace("memory_citations MEDIUMBLOB", "memory_citations BLOB")
    with pytest.raises(MigrationError, match="controlled"):
        schema.recovery(_inventory(_ReflectedDatabase(tables)), "p0001")


@pytest.mark.parametrize("change", ["table", "column", "index", "foreign_key", "disabled_check", "trigger", "view"])
def test_unknown_objects_or_changed_integrity_constraints_cannot_be_adopted(schema: MySQLSchema, change: str) -> None:
    database = _ReflectedDatabase(_show_tables())
    if change == "table":
        database.tables["unknown_table"] = "CREATE TABLE unknown_table (id INTEGER NOT NULL, PRIMARY KEY (id))"
    elif change == "column":
        database.tables["pc_artifacts"] = _append_clause(database.tables["pc_artifacts"], "unexpected INTEGER")
    elif change == "index":
        database.tables["pc_artifact_tags"] = _append_clause(
            database.tables["pc_artifact_tags"], "KEY unexpected (tag)"
        )
    elif change == "foreign_key":
        database.tables["pc_artifact_heads"] = database.tables["pc_artifact_heads"].replace(
            "ON DELETE RESTRICT", "ON DELETE CASCADE"
        )
    elif change == "disabled_check":
        database.tables["pc_artifact_heads"] = database.tables["pc_artifact_heads"].replace(
            "CHECK (revision > 0)", "CHECK (revision > 0) NOT ENFORCED"
        )
    elif change == "trigger":
        database.triggers = [
            {
                "Trigger": "writer",
                "Table": "pc_artifacts",
                "Timing": "AFTER",
                "Event": "INSERT",
                "Statement": "DELETE FROM pc_artifact_heads",
            }
        ]
    else:
        database.views = ["unknown_projection"]

    with pytest.raises(MigrationError, match="complete layout"):
        schema.identify(_inventory(database))


def test_version_table_is_validated_before_reading_and_is_not_a_business_table(schema: MySQLSchema) -> None:
    database = _ReflectedDatabase(_show_tables())
    assert read_revision(cast(Connection, database), _inventory(database)) == (False, None)
    database.tables["pc_schema_revision"] = (
        "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num)\n)"
    )
    assert read_revision(cast(Connection, database), _inventory(database)) == (True, None)
    database.heads = ["p0001"]
    inventory = _inventory(database)
    assert read_revision(cast(Connection, database), inventory) == (True, "p0001")
    assert schema.identify(inventory) == "p0001"
    schema.verify(inventory, "p0001")
    database.heads = ["p0001", "p0002"]
    with pytest.raises(MigrationError, match="multiple or unknown"):
        read_revision(cast(Connection, database), inventory)
    database.heads = ["unknown"]
    with pytest.raises(MigrationError, match="multiple or unknown"):
        read_revision(cast(Connection, database), inventory)
    database.tables["pc_schema_revision"] = _append_clause(
        database.tables["pc_schema_revision"], "KEY unexpected (version_num)"
    )
    with pytest.raises(MigrationError, match="version table"):
        read_revision(cast(Connection, database), _inventory(database))


@pytest.mark.parametrize("violation", ["foreign_key", "check"])
def test_portable_integrity_queries_reject_preexisting_invalid_rows(schema: MySQLSchema, violation: str) -> None:
    # Execute the portable existence queries on real SQLite, without claiming
    # MySQL/seekdb acceptance from this regression's storage engine.
    statements = json.loads((_RESOURCES / "schemas/v1_1_0.json").read_text())["sqlite"]
    with create_engine("sqlite://").connect() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        revision = 1 if violation == "foreign_key" else 0
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_heads (scope_id, family, artifact_id, revision) VALUES ('scope', 'memory', 'missing', ?)",
            (revision,),
        )
        with pytest.raises(MigrationError, match="foreign key" if violation == "foreign_key" else "CHECK"):
            schema.validate_data(connection, "p0003")


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_real_seekdb_frozen_schema_reflection_and_revision_recovery(tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")
    schema = MySQLSchema(MigrationBundle(_RESOURCES))

    def migrate(connection, _identity, guard) -> None:
        assert connection is not None
        assert read_revision(connection, mysql_inventory(connection)) == (False, None)
        connection.exec_driver_sql(
            "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num))"
        )
        connection.commit()
        assert read_revision(connection, mysql_inventory(connection)) == (True, None)
        source = None
        for target in ("p0001", "p0002", "p0003"):
            while True:
                guard()
                inventory = mysql_inventory(connection)
                recovery = schema.recovery(inventory, source)
                assert recovery.revision == target
                if recovery.completed_revision is not None:
                    break
                connection.exec_driver_sql(recovery.pending_statements[0])
                connection.commit()
                guard()
            schema.verify(mysql_inventory(connection), target)
            schema.validate_data(connection, target)
            assert schema.identify(mysql_inventory(connection)) == target
            source = target
            if target == "p0001":
                connection.exec_driver_sql(
                    "INSERT INTO pc_artifacts (scope_id, family, artifact_id, revision, content) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ("scope", "memory", "artifact", 1, b"payload"),
                )
                connection.commit()
        assert connection.exec_driver_sql("SELECT content FROM pc_artifacts").scalar_one() == b"payload"
        connection.exec_driver_sql("SET foreign_key_checks=0")
        connection.exec_driver_sql(
            "INSERT INTO pc_artifact_heads (scope_id, family, artifact_id, revision) "
            "VALUES ('scope', 'memory', 'orphan', 1)"
        )
        connection.commit()
        # Re-enabling enforcement does not repair or validate historical rows.
        connection.exec_driver_sql("SET foreign_key_checks=1")
        with pytest.raises(MigrationError, match="foreign key"):
            schema.validate_data(connection, "p0003")

    MaintenanceConnections(SeekDBConfig(path=tmp_path / "seekdb")).run(migrate, writable=True)
