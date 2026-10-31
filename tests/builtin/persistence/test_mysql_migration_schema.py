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

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import Connection, create_engine

from powercontext.builtin.persistence.migrations import SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle, sqlite_inventory
from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError, digest
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner
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


_NEXT_TABLE = "CREATE TABLE pc_extension_marker (id INTEGER NOT NULL, PRIMARY KEY (id))"


@pytest.fixture
def extended_bundle(tmp_path: Path) -> MigrationBundle:
    directory = tmp_path / "bundle"
    shutil.copytree(_RESOURCES, directory)
    database = tmp_path / "fingerprint.db"
    runner = SQLiteMigrationRunner(database, MigrationBundle(_RESOURCES))
    runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)
    engine = create_engine("sqlite:///" + str(database))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(_NEXT_TABLE)
            fingerprint = digest(
                sqlite_inventory(connection, exclude=frozenset({"pc_schema_revision", *runner.bundle.retained_objects}))
            )
    finally:
        engine.dispose()
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["revision_resources"]["fixture_next"] = ["mysql_fixture_next.json", "mysql_wrappers/fixture_next.py"]
    manifest["sqlite_fingerprints"]["fixture_next"] = [fingerprint]
    manifest["mysql_layouts"].append("mysql_fixture_next.json")
    manifest["mysql_wrappers"] = {"fixture_next": "mysql_wrappers/fixture_next.py"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "mysql_fixture_next.json").write_text(
        json.dumps({
            "format": "powercontext.mysql-layouts.v1",
            "revisions": {"fixture_next": [_NEXT_TABLE]},
            "checkpoints": {},
        }),
        encoding="utf-8",
    )
    (directory / "versions/fixture_next.py").write_text(
        "from alembic import op\n"
        "revision = 'fixture_next'\n"
        "down_revision = 'p0005'\n"
        f"def upgrade():\n    op.execute({_NEXT_TABLE!r})\n",
        encoding="utf-8",
    )
    (directory / "mysql_wrappers").mkdir()
    (directory / "mysql_wrappers/fixture_next.py").write_text(
        "from alembic import context\n"
        "revision = 'fixture_next'\n"
        "down_revision = 'p0005'\n"
        "def upgrade():\n    context.config.attributes['execute_revision'](revision)\n",
        encoding="utf-8",
    )
    return MigrationBundle(directory)


def test_registered_next_revision_uses_the_same_recognition_and_sqlite_executor(
    tmp_path: Path, extended_bundle: MigrationBundle
) -> None:
    schema = MySQLSchema(extended_bundle)
    layouts = json.loads((extended_bundle.snapshot_directory / "mysql_layouts.json").read_text())
    tables = _show_tables(citations=2, drop_family_check=True)
    for revision in ("p0004", "p0005"):
        for statement in layouts["revisions"][revision]:
            tables[statement.split()[2]] = statement
    source = _ReflectedDatabase(tables)
    inventory = _inventory(source)
    assert schema.identify(inventory) == "p0005"
    recovery = schema.recovery(inventory, "p0005")
    assert recovery.pending_statements == (_NEXT_TABLE,)
    source.tables["pc_extension_marker"] = _NEXT_TABLE
    inventory = _inventory(source)
    assert schema.identify(inventory) == "fixture_next"
    schema.verify(inventory, "fixture_next")
    assert schema.recovery(inventory, "p0005").completed_revision == "fixture_next"
    assert extended_bundle.pending("p0004") == ("p0005", "fixture_next")
    source.tables["pc_schema_revision"] = (
        "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, PRIMARY KEY (version_num))"
    )
    source.heads = ["fixture_next"]
    assert read_revision(cast(Connection, source), _inventory(source), revisions=extended_bundle.revisions) == (
        True,
        "fixture_next",
    )

    runner = SQLiteMigrationRunner(tmp_path / "adjacent.db", MigrationBundle(_RESOURCES))
    runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)
    adjacent = SQLiteMigrationRunner(runner.database, extended_bundle)
    plan = adjacent.plan()
    assert plan.revisions == ("fixture_next",)
    adjacent.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    direct = SQLiteMigrationRunner(tmp_path / "direct.db", extended_bundle)
    direct.apply(plan_id=direct.plan().plan_id, accepted=True, maintenance_confirmed=True)
    assert adjacent.verify().revision == direct.verify().revision == "fixture_next"
    assert adjacent.plan().schema_fingerprint == direct.plan().schema_fingerprint


@pytest.mark.parametrize("change", ["missing", "unregistered", "duplicate"])
def test_mysql_layout_registration_must_cover_exactly_the_revision_chain(
    extended_bundle: MigrationBundle, change: str
) -> None:
    path = extended_bundle.directory / "mysql_fixture_next.json"
    layouts = json.loads(path.read_text())
    if change == "missing":
        del layouts["revisions"]["fixture_next"]
    elif change == "unregistered":
        layouts["revisions"]["unregistered"] = []
    else:
        layouts["revisions"]["p0001"] = []
    path.write_text(json.dumps(layouts), encoding="utf-8")
    with pytest.raises(MigrationError, match="invalid_bundle"):
        MySQLSchema(MigrationBundle(extended_bundle.directory))


@pytest.mark.parametrize("change", ["missing", "predecessor", "unbound", "override"])
def test_mysql_execution_registration_rejects_incomplete_history_before_opening_a_target(
    extended_bundle: MigrationBundle, tmp_path: Path, change: str
) -> None:
    path = extended_bundle.directory / "manifest.json"
    manifest = json.loads(path.read_text())
    if change == "missing":
        del manifest["mysql_wrappers"]
    elif change == "predecessor":
        wrapper = extended_bundle.directory / "mysql_wrappers/fixture_next.py"
        wrapper.write_text(wrapper.read_text().replace("'p0005'", "'p0004'"), encoding="utf-8")
    elif change == "unbound":
        manifest["revision_resources"]["fixture_next"].remove("mysql_wrappers/fixture_next.py")
    else:
        manifest["mysql_wrappers"]["p0005"] = "mysql_wrappers/fixture_next.py"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "absent-seekdb"
    evidence = tmp_path / "absent-evidence"
    with pytest.raises(MigrationError, match="invalid_bundle"):
        MySQLMigrationRunner(
            MaintenanceConnections(SeekDBConfig(path=target)),
            MigrationBundle(extended_bundle.directory),
            evidence_directory=evidence,
        )
    assert not target.exists()
    assert not evidence.exists()


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_registered_next_revision_executes_directly_and_sequentially_on_real_seekdb(
    extended_bundle: MigrationBundle, short_tmp_path: Path
) -> None:
    binding = pytest.importorskip("pylibseekdb")
    leases = []
    try:
        fingerprints = []
        for adjacent in (False, True):
            target = short_tmp_path / ("adjacent" if adjacent else "direct")
            # Retain a native engine lease while public maintenance calls open
            # independent connections; restart behavior has separate acceptance.
            leases.append(asyncio.run(binding.aopen(str(target))))
            connections = MaintenanceConnections(SeekDBConfig(path=target))
            evidence = short_tmp_path / (target.name + "-evidence")
            previous = None
            if adjacent:
                previous = MySQLMigrationRunner(connections, MigrationBundle(_RESOURCES), evidence_directory=evidence)
                previous.apply(plan_id=previous.plan().plan_id, accepted=True, maintenance_confirmed=True)
            runner = MySQLMigrationRunner(connections, extended_bundle, evidence_directory=evidence)
            if previous is not None:
                assert all(runner.checksums[revision] == checksum for revision, checksum in previous.checksums.items())
            plan = runner.plan(backup_policy="skip")
            assert plan.revisions == (("fixture_next",) if adjacent else extended_bundle.revisions)
            result = runner.apply(
                plan_id=plan.plan_id,
                backup_policy="skip",
                accepted=True,
                maintenance_confirmed=True,
                accept_no_backup=True,
            )
            assert result.changed and runner.verify().revision == "fixture_next"
            assert not runner.apply().changed
            fingerprints.append(runner.plan().schema_fingerprint)
        assert fingerprints[0] == fingerprints[1]
    finally:
        for lease in leases:
            lease.close()


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


def _released_fulltext_tables() -> dict[str, str]:
    path = Path(__file__).parents[2] / "fixtures/database_migrations/schemas/seekdb_v1_3_0_fulltext.json"
    tables = json.loads(path.read_text(encoding="utf-8"))["tables"]
    return {name: ddl.replace("`test`.", "`TestDB`.") for name, ddl in tables.items()}


def test_released_fulltext_keys_preserve_search_semantics_in_the_inventory() -> None:
    inventory = _inventory(_ReflectedDatabase(_released_fulltext_tables()))
    for name, table in inventory["tables"].items():
        assert "fulltext" not in table["columns"]
        assert table["indexes"][f"ix_{name}_fts"] == {
            "columns": [["searchable_text", None, "asc"]],
            "unique": False,
            "kind": "fulltext",
            "parser": "space",
            "parser_properties": {"min_token_size": 3, "max_token_size": 84},
        }


def test_native_oceanbase_phrase_match_preserves_released_fulltext_inventory() -> None:
    path = Path(__file__).parents[2] / "fixtures/database_migrations/schemas/oceanbase_4_6_3_1_fulltext.json"
    tables = json.loads(path.read_text(encoding="utf-8"))["tables"]
    assert _inventory(_ReflectedDatabase(tables)) == _inventory(_ReflectedDatabase(_released_fulltext_tables()))


@pytest.mark.parametrize(
    "option",
    [
        "",
        "FTS_INDEX_TYPE = MATCH",
        "fts_index_type=match",
        "FTS_INDEX_TYPE = PHRASE_MATCH",
        "fts_index_type=phrase_match",
    ],
)
def test_oceanbase_fulltext_reflection_preserves_server_baseline_and_recovery(option: str) -> None:
    bundle = server_bundle()
    statements = json.loads((bundle.snapshot_directory / "schemas/mysql-s1300.json").read_text())["revisions"]["s1300"][
        "statements"
    ]
    tables = {statement.split("`", 2)[1]: statement for statement in statements if statement.startswith("CREATE TABLE")}
    expected = _inventory(_ReflectedDatabase(tables))
    # Replay MATCH reported by OceanBase 4.6.0.1 and PHRASE_MATCH captured on 4.6.3.1.
    # This models reflection; it is not live OceanBase acceptance.
    reflected = {
        name: ddl.replace(
            "PARSER_PROPERTIES=(min_token_size=3,max_token_size=84)",
            f"PARSER_PROPERTIES=(min_token_size=3,max_token_size=84) {option}",
        )
        for name, ddl in tables.items()
    }
    inventory = _inventory(_ReflectedDatabase(reflected))
    assert inventory == expected
    schema = MySQLSchema(bundle)
    assert schema.identify(inventory) == "s1300"
    schema.verify(inventory, "s1300")
    # The reported interruption leaves 17 tables, including the first FULLTEXT
    # index. Only the remaining registered statements may be scheduled again.
    partial = _inventory(_ReflectedDatabase(dict(list(reflected.items())[:17])))
    recovery = schema.recovery(partial, None)
    assert recovery.revision == "s1300"
    assert recovery.completed_revision is None
    assert recovery.pending_statements == tuple(statements[17:])


@pytest.mark.parametrize(
    "option",
    [
        "FTS_INDEX_TYPE = UNKNOWN",
        "FTS_INDEX_TYPE =",
        "FTS_INDEX_TYPE = MATCH FUTURE_OPTION=1",
        "FTS_INDEX_TYPE = PHRASE_MATCH FUTURE_OPTION=1",
    ],
)
def test_unknown_or_incomplete_fulltext_storage_options_are_rejected(option: str) -> None:
    tables = _released_fulltext_tables()
    tables = {
        name: ddl.replace(
            "PARSER_PROPERTIES=(min_token_size=3,max_token_size=84)",
            f"PARSER_PROPERTIES=(min_token_size=3,max_token_size=84) {option}",
        )
        for name, ddl in tables.items()
    }
    with pytest.raises(MigrationError, match="incompatible_schema"):
        _inventory(_ReflectedDatabase(tables))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("WITH PARSER space", "WITH PARSER ngram"),
        ("min_token_size=3", "min_token_size=4"),
        ("max_token_size=84", "max_token_size=80"),
        ("ix_pc_artifact_heads_fts", "ix_other_fts"),
    ],
)
@pytest.mark.parametrize("physical_option", ["", "FTS_INDEX_TYPE = MATCH", "FTS_INDEX_TYPE = PHRASE_MATCH"])
def test_fulltext_semantic_changes_have_distinct_fingerprints(old: str, new: str, physical_option: str) -> None:
    tables = {
        name: ddl.replace(
            "PARSER_PROPERTIES=(min_token_size=3,max_token_size=84)",
            f"PARSER_PROPERTIES=(min_token_size=3,max_token_size=84) {physical_option}",
        )
        for name, ddl in _released_fulltext_tables().items()
    }
    original = digest(_inventory(_ReflectedDatabase(tables)))
    tables["pc_artifact_heads"] = tables["pc_artifact_heads"].replace(old, new)
    assert digest(_inventory(_ReflectedDatabase(tables))) != original


@pytest.mark.parametrize(
    "properties",
    [
        "unknown_option=3,max_token_size=84",
        "min_token_size='3',max_token_size=84",
        "min_token_size=3,min_token_size=4",
        "min_token_size=3+1,max_token_size=84",
    ],
)
def test_fulltext_unknown_parser_properties_are_rejected(properties: str) -> None:
    tables = _released_fulltext_tables()
    tables["pc_artifact_heads"] = tables["pc_artifact_heads"].replace("min_token_size=3,max_token_size=84", properties)
    with pytest.raises(MigrationError, match="incompatible_schema"):
        _inventory(_ReflectedDatabase(tables))


def test_fulltext_key_cannot_be_hidden_as_an_automatic_foreign_key_index(schema: MySQLSchema) -> None:
    tables = _show_tables()
    tables["pc_artifact_heads"] = _append_clause(
        tables["pc_artifact_heads"],
        "FULLTEXT KEY extra_search (scope_id, family, artifact_id, revision)",
    )
    inventory = _inventory(_ReflectedDatabase(tables))
    assert inventory["tables"]["pc_artifact_heads"]["indexes"]["extra_search"]["kind"] == "fulltext"
    with pytest.raises(MigrationError, match="complete layout"):
        schema.identify(inventory)


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


def test_named_unique_constraints_and_native_keys_have_the_same_fingerprint(schema: MySQLSchema) -> None:
    tables = _show_tables(citations=2, drop_family_check=True)
    tables["pc_artifacts"] = _append_clause(
        tables["pc_artifacts"], "CONSTRAINT uq_fixture_identity UNIQUE (scope_id, family, artifact_id)"
    )
    frozen = _inventory(_ReflectedDatabase(tables))
    tables["pc_artifacts"] = tables["pc_artifacts"].replace(
        "CONSTRAINT uq_fixture_identity UNIQUE", "UNIQUE KEY uq_fixture_identity"
    )
    native = _inventory(_ReflectedDatabase(tables))
    assert schema.fingerprint(native) == schema.fingerprint(frozen)
    with pytest.raises(MigrationError, match="incompatible_schema"):
        schema.identify(native)


def test_native_oceanbase_auto_increment_mode_is_preserved_while_the_counter_can_advance() -> None:
    path = Path(__file__).parents[2] / "fixtures/database_migrations/schemas/oceanbase_v1_3_0_audit.json"
    statement = json.loads(path.read_text())["tables"]["pc_access_audit"]
    original = _inventory(_ReflectedDatabase({"pc_access_audit": statement}))
    assert original["tables"]["pc_access_audit"]["auto_increment_mode"] == "order"
    advanced = _inventory(
        _ReflectedDatabase({"pc_access_audit": statement.replace("AUTO_INCREMENT = 37", "AUTO_INCREMENT = 900")})
    )
    assert advanced == original
    unordered = _inventory(_ReflectedDatabase({"pc_access_audit": statement.replace("'ORDER'", "'NOORDER'")}))
    assert unordered["tables"]["pc_access_audit"]["auto_increment_mode"] == "noorder"
    assert digest(unordered) != digest(original)


@pytest.mark.parametrize("mode", ["'FUTURE'", "'ORDER' AUTO_INCREMENT_MODE='NOORDER'"])
def test_unknown_or_duplicate_auto_increment_mode_is_not_silently_discarded(mode: str) -> None:
    path = Path(__file__).parents[2] / "fixtures/database_migrations/schemas/oceanbase_v1_3_0_audit.json"
    statement = json.loads(path.read_text())["tables"]["pc_access_audit"].replace("'ORDER'", mode)
    with pytest.raises(MigrationError, match="incompatible_schema"):
        _inventory(_ReflectedDatabase({"pc_access_audit": statement}))


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
    assert read_revision(cast(Connection, database), _inventory(database), revisions=schema.bundle.revisions) == (
        False,
        None,
    )
    database.tables["pc_schema_revision"] = (
        "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num)\n)"
    )
    assert read_revision(cast(Connection, database), _inventory(database), revisions=schema.bundle.revisions) == (
        True,
        None,
    )
    database.heads = ["p0001"]
    inventory = _inventory(database)
    assert read_revision(cast(Connection, database), inventory, revisions=schema.bundle.revisions) == (True, "p0001")
    assert schema.identify(inventory) == "p0001"
    schema.verify(inventory, "p0001")
    database.heads = ["p0001", "p0002"]
    with pytest.raises(MigrationError, match="multiple or unknown"):
        read_revision(cast(Connection, database), inventory, revisions=schema.bundle.revisions)
    database.heads = ["unknown"]
    with pytest.raises(MigrationError, match="multiple or unknown"):
        read_revision(cast(Connection, database), inventory, revisions=schema.bundle.revisions)
    database.tables["pc_schema_revision"] = _append_clause(
        database.tables["pc_schema_revision"], "KEY unexpected (version_num)"
    )
    with pytest.raises(MigrationError, match="version table"):
        read_revision(cast(Connection, database), _inventory(database), revisions=schema.bundle.revisions)


_VECTOR_TABLE = (
    "CREATE TABLE pc_vector_projection ("
    "id BIGINT NOT NULL, embedding VECTOR(3) NOT NULL, PRIMARY KEY (id), "
    "VECTOR KEY ix_embedding (embedding) WITH "
    "(DISTANCE=L2, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64) BLOCK_SIZE 16384"
    ") DEFAULT CHARSET=utf8mb4"
)


def test_vector_index_reflection_preserves_parameters_without_inventing_a_column() -> None:
    # VECTOR KEY comes from the actual installed 1.3.0 optional-profile captures.
    table = _inventory(_ReflectedDatabase({"pc_vector_projection": _VECTOR_TABLE}))["tables"]["pc_vector_projection"]
    assert set(table["columns"]) == {"id", "embedding"}
    assert table["columns"]["embedding"]["type"] == ["vector", ["3"]]
    assert table["indexes"]["ix_embedding"] == {
        "columns": [["embedding", None, "asc"]],
        "unique": False,
        "kind": "vector",
        "parameters": {
            "distance": "l2",
            "type": "hnsw",
            "lib": "vsag",
            "m": 16,
            "ef_construction": 200,
            "ef_search": 64,
        },
    }
    changed = _VECTOR_TABLE.replace("EF_SEARCH=64", "EF_SEARCH=128")
    actual = _inventory(_ReflectedDatabase({"pc_vector_projection": changed}))["tables"]["pc_vector_projection"]
    assert actual["columns"] == table["columns"]
    assert actual["indexes"] != table["indexes"]


@pytest.mark.parametrize(
    "replacement",
    ["M=16, M=32", "M=-1", "M=0", "M=anything", "UNKNOWN_OPTION=16"],
)
def test_unprovable_vector_index_options_fail_schema_reflection(replacement: str) -> None:
    definition = _VECTOR_TABLE.replace("M=16", replacement)
    with pytest.raises(MigrationError, match="VECTOR"):
        _inventory(_ReflectedDatabase({"pc_vector_projection": definition}))


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_real_seekdb_vector_index_is_reflected_as_an_index(short_tmp_path: Path) -> None:
    def inspect_vector(connection, _identity, _guard):
        assert connection is not None
        connection.exec_driver_sql(_VECTOR_TABLE)
        connection.commit()
        table = mysql_inventory(connection)["tables"]["pc_vector_projection"]
        assert set(table["columns"]) == {"id", "embedding"}
        index = table["indexes"]["ix_embedding"]
        assert index["kind"] == "vector"
        assert index["columns"] == [["embedding", None, "asc"]]
        assert index["parameters"]["distance"] == "l2"
        assert index["parameters"]["m"] == 16
        assert index["parameters"]["ef_construction"] == 200
        assert index["parameters"]["ef_search"] == 64

    MaintenanceConnections(SeekDBConfig(path=short_tmp_path / "seekdb")).run(inspect_vector, writable=True)


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
def test_real_seekdb_frozen_schema_reflection_and_revision_recovery(short_tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")
    schema = MySQLSchema(MigrationBundle(_RESOURCES))

    def migrate(connection, _identity, guard) -> None:
        assert connection is not None
        assert read_revision(connection, mysql_inventory(connection), revisions=schema.bundle.revisions) == (
            False,
            None,
        )
        connection.exec_driver_sql(
            "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
            "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num))"
        )
        connection.commit()
        assert read_revision(connection, mysql_inventory(connection), revisions=schema.bundle.revisions) == (True, None)
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

    MaintenanceConnections(SeekDBConfig(path=short_tmp_path / "seekdb")).run(migrate, writable=True)
