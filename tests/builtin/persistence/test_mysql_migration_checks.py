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

"""CHECK reflection preserves SQL semantics without expanding migration eligibility."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql import MySQLMigrationRunner
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema, _expression_sql, mysql_inventory
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from tests.builtin.persistence.test_mysql_migration_schema import _inventory, _ReflectedDatabase

_FIXTURE = Path(__file__).parents[2] / "fixtures/database_migrations/schemas/seekdb_v1_3_0_checks.json"
_RESOURCES = Path(__file__).parents[3] / "src/powercontext/builtin/persistence/migrations/resources"


def _check_inventory(expression: str):
    return _inventory(
        _ReflectedDatabase({
            "check_fixture": "CREATE TABLE check_fixture (a INTEGER, b INTEGER, c INTEGER, "
            f"CONSTRAINT ck_arithmetic CHECK ({expression}))"
        })
    )


@pytest.mark.parametrize(
    ("expression", "values", "expected"),
    [
        ("a = (b + c)", (5, 2, 3), 1),
        ("a = (b + c)", (6, 2, 3), 0),
        ("a + b * c = 14", (2, 3, 4), 1),
        ("(a + b) * c = 20", (2, 3, 4), 1),
        ("a - b - c = 5", (10, 3, 2), 1),
        ("a - (b - c) = 9", (10, 3, 2), 1),
        ("a / b / c = 2", (16.0, 4, 2), 1),
        ("a % b + c = 3", (10, 4, 1), 1),
        ("-a + +b = -(c + 1)", (5, 2, 2), 1),
        ("a IS NULL OR a = b + c AND b > 0", (None, -2, 3), 1),
        ("a IS NULL OR a = b + c AND b > 0", (1, -2, 3), 0),
        ("(a IS NULL OR a = b + c) AND b > 0", (None, -2, 3), 0),
        ("a + b IN (3, 4) AND c IS NOT NULL", (1, 2, 0), 1),
        ("a = b + c", (None, 2, 3), None),
    ],
)
def test_reflected_arithmetic_renders_with_equivalent_semantics(expression, values, expected):
    inventory = _check_inventory(expression)
    rendered = _expression_sql(inventory["tables"]["check_fixture"]["checks"]["ck_arithmetic"]["expression"])
    # SQLite executes the portable expressions; this does not claim MySQL division semantics.
    with create_engine("sqlite://").connect() as connection:
        result = connection.exec_driver_sql(
            f"SELECT {rendered} FROM (SELECT ? AS a, ? AS b, ? AS c)",  # noqa: S608 -- test expressions
            values,
        ).scalar_one()
    assert result == expected
    assert _check_inventory(rendered) == inventory


def test_historical_seekdb_checks_reflect_without_accepting_an_unknown_layout():
    fixture = json.loads(_FIXTURE.read_text())
    inventory = _inventory(
        _ReflectedDatabase({name: value["show_create_table"] for name, value in fixture["tables"].items()})
    )
    compiled = _inventory(
        _ReflectedDatabase({name: value["create_table"] for name, value in fixture["tables"].items()})
    )
    for name in fixture["tables"]:
        # seekdb promotes inline column CHECKs to table constraints in SHOW CREATE.
        # Compare the historical table-level checks and round-trip every reflected check.
        for constraint, check in compiled["tables"][name]["checks"].items():
            assert inventory["tables"][name]["checks"][constraint] == check
        for check in inventory["tables"][name]["checks"].values():
            roundtrip = _check_inventory(_expression_sql(check["expression"]))
            assert roundtrip["tables"]["check_fixture"]["checks"]["ck_arithmetic"] == check
    checks = inventory["tables"]["pc_recall_effort_daily"]["checks"]
    rendered = _expression_sql(checks["ck_pc_recall_effort_daily_drop_total"]["expression"])
    with create_engine("sqlite://").connect() as connection:
        for total, expected in [(5, 1), (6, 0)]:
            assert (
                connection.exec_driver_sql(
                    f"SELECT {rendered} FROM (SELECT ? AS dropped_items, "  # noqa: S608 -- frozen expression
                    "2 AS dropped_below_min_bytes, 3 AS dropped_no_fitting_truncation)",
                    (total,),
                ).scalar_one()
                == expected
            )
    schema = MySQLSchema(MigrationBundle(_RESOURCES))
    with pytest.raises(MigrationError, match="registered complete layout"):
        schema.identify(inventory)
    with pytest.raises(MigrationError, match="recorded revision"):
        schema.verify(inventory, "p0004")
    with pytest.raises(MigrationError, match="controlled next-revision"):
        schema.recovery(inventory, "p0003")


@pytest.mark.parametrize(
    ("expression", "construct"),
    [
        ("a = (b ^ c)", "'^'"),
        ("a = (b DIV c)", "'div'"),
        ("a = abs(b)", "'abs'"),
        ("a LIKE 'private literal'", "'like'"),
        ("a = 'private literal' (b)", "'('"),
        ("a = @b", "'@'"),
    ],
)
def test_unsupported_checks_report_table_constraint_and_construct(expression, construct):
    with pytest.raises(MigrationError) as caught:
        _check_inventory(expression)
    message = str(caught.value)
    assert caught.value.code == "incompatible_schema"
    assert "check_fixture" in message
    assert "ck_arithmetic" in message
    assert construct in message
    assert "Unsupported CHECK" in message
    assert "unbalanced" not in message
    assert "private literal" not in message


@pytest.mark.parametrize("expression", ["a = (b + c", "a = b + c)", "a = (b +)", "a = ()", "a = -"])
def test_malformed_checks_remain_rejected(expression):
    with pytest.raises(MigrationError, match="check_fixture"):
        _check_inventory(expression)


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
def test_real_seekdb_historical_check_reflection_and_rendering(short_tmp_path):
    pytest.importorskip("pylibseekdb")
    fixture = json.loads(_FIXTURE.read_text())

    def reflect(connection, _identity, guard):
        assert connection is not None
        for value in fixture["tables"].values():
            guard()
            connection.exec_driver_sql(value["create_table"])
        connection.commit()
        inventory = mysql_inventory(connection)
        checks = inventory["tables"]["pc_recall_effort_daily"]["checks"]
        rendered = _expression_sql(checks["ck_pc_recall_effort_daily_drop_total"]["expression"])
        for total, expected in [(5, 1), (6, 0)]:
            assert (
                connection.exec_driver_sql(
                    f"SELECT {rendered} FROM (SELECT {total} AS dropped_items, "  # noqa: S608 -- test constants
                    "2 AS dropped_below_min_bytes, 3 AS dropped_no_fitting_truncation) AS sample"
                ).scalar_one()
                == expected
            )
        with pytest.raises(MigrationError, match="registered complete layout"):
            MySQLSchema(MigrationBundle(_RESOURCES)).identify(inventory)

    MaintenanceConnections(SeekDBConfig(path=short_tmp_path / "seekdb")).run(reflect, writable=True)


@pytest.mark.skipif(os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled")
@pytest.mark.parametrize(
    ("expression", "column_type", "valid", "invalid"),
    [("a % 2 = 0", "INTEGER", 2, 3), ("a = '100%s%%'", "VARCHAR(32)", "100%s%%", "different")],
    ids=["remainder", "percent_literal"],
)
def test_real_seekdb_percent_checks_validate_data_and_plan(short_tmp_path, expression, column_type, valid, invalid):
    pytest.importorskip("pylibseekdb")
    # Extend an isolated bundle, preserving the shipped layouts and their eligibility.
    directory = short_tmp_path / "bundle"
    shutil.copytree(_RESOURCES, directory)
    ddl = f"CREATE TABLE pc_check_fixture (a {column_type}, CONSTRAINT ck_percent CHECK ({expression}) NOT ENFORCED)"
    for name in ("pre_dream", "v1_1_0"):
        path = directory / "schemas" / f"{name}.json"
        layout = json.loads(path.read_text())
        layout["mysql"].append(ddl)
        path.write_text(json.dumps(layout, ensure_ascii=False))
    path = directory / "mysql_layouts.json"
    layouts = json.loads(path.read_text())
    layouts["revisions"]["p0001"].append(ddl)
    path.write_text(json.dumps(layouts), encoding="utf-8")
    bundle = MigrationBundle(directory)
    schema = MySQLSchema(bundle)
    connections = MaintenanceConnections(SeekDBConfig(path=short_tmp_path / "seekdb"))
    runner = MySQLMigrationRunner(connections, bundle, evidence_directory=short_tmp_path / "evidence")

    def prepare(connection, _identity, guard):
        assert connection is not None
        baseline = json.loads((directory / "schemas/pre_dream.json").read_text())["mysql"]
        for statement in baseline:
            guard()
            connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
        connection.exec_driver_sql("INSERT INTO pc_check_fixture (a) VALUES (%s), (NULL)", (valid,))
        connection.commit()
        inventory = mysql_inventory(connection)
        assert schema.identify(inventory) == "p0001"
        schema.verify(inventory, "p0001")
        schema.validate_data(connection, "p0001")

    def invalidate(connection, _identity, guard):
        assert connection is not None
        guard()
        connection.exec_driver_sql("UPDATE pc_check_fixture SET a = %s WHERE a IS NOT NULL", (invalid,))
        connection.commit()
        with pytest.raises(MigrationError, match=r"data_integrity_error.*CHECK"):
            schema.validate_data(connection, "p0001")

    fresh = MySQLMigrationRunner(
        MaintenanceConnections(SeekDBConfig(path=short_tmp_path / "fresh_seekdb")),
        bundle,
        evidence_directory=short_tmp_path / "fresh_evidence",
    )
    fresh_plan = fresh.plan(backup_policy="skip")
    fresh.apply(
        plan_id=fresh_plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="skip",
        accept_no_backup=True,
    )
    assert fresh.verify().state == "ready"
    connections.run(prepare, writable=True)
    assert runner.plan().state == "migration_required"
    connections.run(invalidate, writable=True)
    with pytest.raises(MigrationError, match=r"data_integrity_error.*CHECK"):
        runner.plan()
