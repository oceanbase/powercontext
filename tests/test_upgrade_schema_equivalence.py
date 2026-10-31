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

"""Prevent schema equivalence from erasing differences in SQL literals."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import upgrade_database_probe

from tests.release_upgrades.test_full_server import canonical_schema


def schema(sql):
    return canonical_schema({"tables": {"example": sql}})


@pytest.mark.parametrize(
    "left,right", [("a  b", "a b"), ('"word"', "word"), ("AUTO_INCREMENT=12", "AUTO_INCREMENT=99")]
)
def test_distinct_sqlite_defaults_do_not_compare_equal(left, right):
    inventories = []
    values = []
    for default in (left, right):
        with sqlite3.connect(":memory:") as connection:
            connection.execute(f"CREATE TABLE example (value TEXT DEFAULT '{default}')")
            ddl = connection.execute("SELECT sql FROM sqlite_schema WHERE name='example'").fetchone()[0]
            connection.execute("INSERT INTO example DEFAULT VALUES")
            values.append(connection.execute("SELECT value FROM example").fetchone()[0])
            inventories.append(schema(ddl))
    assert values[0] != values[1]
    assert inventories[0] != inventories[1]


def test_distinct_sqlite_checks_do_not_compare_equal():
    inventories = []
    for literal in ("a  b", "a b"):
        with sqlite3.connect(":memory:") as connection:
            connection.execute(f"CREATE TABLE example (value TEXT CHECK (value = '{literal}'))")
            connection.execute("INSERT INTO example VALUES (?)", (literal,))
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO example VALUES (?)", ("a b" if literal == "a  b" else "a  b",))
            inventories.append(
                schema(connection.execute("SELECT sql FROM sqlite_schema WHERE name='example'").fetchone()[0])
            )
    assert inventories[0] != inventories[1]


def test_table_counters_and_unquoted_whitespace_are_not_schema_changes():
    left = "CREATE TABLE `example` (`id` int AUTO_INCREMENT, `value` text DEFAULT 'a  b') AUTO_INCREMENT=12"
    right = "CREATE   TABLE `example` ( `id` int AUTO_INCREMENT, `value` text DEFAULT 'a  b' ) AUTO_INCREMENT = 99"
    assert schema(left) == schema(right)
    assert schema(left) != schema(left.replace("int AUTO_INCREMENT", "int"))


@pytest.mark.parametrize("literal", [r"'a\'  b'", "'it''s  quoted'", '"a  b"', "`a  b`", "[a  b]"])
def test_quoted_tokens_preserve_spaces_and_escapes(literal):
    ddl = f"CREATE TABLE example (value TEXT DEFAULT {literal})"
    assert schema(ddl) != schema(ddl.replace("  ", " "))


@pytest.mark.parametrize("name", ["字段", "😀😀"])
def test_unquoted_identifier_boundaries_are_preserved(name):
    inventories = []
    columns = []
    for declaration in (name, " ".join(name)):
        with sqlite3.connect(":memory:") as connection:
            connection.execute(f"CREATE TABLE example ({declaration} TEXT)")
            columns.append(connection.execute("PRAGMA table_info(example)").fetchone()[1])
            inventories.append(
                schema(connection.execute("SELECT sql FROM sqlite_schema WHERE name='example'").fetchone()[0])
            )
    assert columns[0] != columns[1]
    assert inventories[0] != inventories[1]


@pytest.mark.parametrize("kind", ["logical_tables", "views", "triggers"])
def test_native_logical_schema_preserves_object_differences(kind):
    left = {
        "logical_tables": {"example": {"default": "a  b"}},
        "views": {"example_view": "SELECT 'a  b'"},
        "triggers": {"example_trigger": ["BEFORE", "INSERT", "example", "SET NEW.value = 'a  b'"]},
    }
    right = json.loads(json.dumps(left).replace("a  b", "a b"))
    changed = {**left, kind: right[kind]}
    assert canonical_schema(left) != canonical_schema(changed)


def test_native_probe_preserves_trigger_timing(monkeypatch, tmp_path):
    """Synthetic driver rows exercise the probe and comparator, not a live backend."""
    monkeypatch.setenv("PC_UPGRADE_OCEANBASE_URL", "mysql://fixture@localhost/fixture")
    monkeypatch.delenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(upgrade_database_probe, "version", lambda _name: "test-driver")
    snapshots = []
    for timing in ("BEFORE", "AFTER"):
        cursor = MagicMock()
        cursor.fetchone.return_value = ("test-engine", "mysql")
        cursor.fetchall.return_value = []

        def execute(query, *, timing=timing, cursor=cursor):
            if query == "SHOW TRIGGERS":
                fields = ("Trigger", "Event", "Table", "Statement", "Timing")
                cursor.description = [(field,) for field in fields]
                cursor.fetchall.return_value = [("example_trigger", "INSERT", "example", "SET @seen = 1", timing)]

        cursor.execute.side_effect = execute
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connect = MagicMock()
        connect.return_value.__enter__.return_value = connection
        monkeypatch.setattr(
            upgrade_database_probe, "import_module", lambda _name, connect=connect: SimpleNamespace(connect=connect)
        )
        snapshot = asyncio.run(
            upgrade_database_probe.inspect("oceanbase", tmp_path, add_revision=False, canonical=True)
        )
        snapshots.append(json.loads(json.dumps(snapshot)))

    assert snapshots[0]["trigger_rows"][0]["Timing"] == "BEFORE"
    assert snapshots[1]["trigger_rows"][0]["Timing"] == "AFTER"
    assert canonical_schema(snapshots[0]) != canonical_schema(snapshots[1])
