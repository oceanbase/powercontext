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

import re
from pathlib import Path

from sqlalchemy import BigInteger, Date, Integer, String, Table
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

from powercontext.builtin.persistence.tables import (
    AGENT_SKILL_TARGETS_TABLE,
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    SHARED_METADATA,
    SKILL_PACKAGES_TABLE,
    SOURCE_CURSORS_TABLE,
    SOURCES_TABLE,
)

INNODB_MAX_INDEX_BYTES = 3072
UTF8MB4_MAX_BYTES_PER_CHARACTER = 4


class _UnbudgetedColumnTypeError(AssertionError):
    def __init__(self, column_type: object) -> None:
        super().__init__(f"unbudgeted indexed column type: {column_type!r}")


def _column_budget(column) -> int:
    if isinstance(column.type, String):
        assert column.type.length is not None
        return column.type.length * UTF8MB4_MAX_BYTES_PER_CHARACTER
    if isinstance(column.type, BigInteger):
        return 8
    if isinstance(column.type, Integer):
        return 4
    if isinstance(column.type, Date):
        return 3
    raise _UnbudgetedColumnTypeError(column.type)


def test_mysql_ddl_uses_utf8mb4_bin_for_identity_keys() -> None:
    dialect = mysql.dialect()
    ddl = str(CreateTable(SOURCES_TABLE).compile(dialect=dialect))
    assert "scope_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in ddl
    assert "source_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in ddl
    assert "source_type VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in ddl


def test_mysql_ddl_uses_mediumblob_for_every_canonical_payload() -> None:
    dialect = mysql.dialect()
    expected = {
        SOURCES_TABLE: ("payload",),
        ARTIFACTS_TABLE: ("content",),
        SOURCE_CURSORS_TABLE: ("`cursor`",),
        SKILL_PACKAGES_TABLE: ("archive_bytes", "manifest"),
    }

    for table, column_names in expected.items():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        for column_name in column_names:
            assert f"{column_name} MEDIUMBLOB NOT NULL" in ddl


def test_mysql_remote_target_credentials_use_binary_identity_columns() -> None:
    ddl = str(CreateTable(AGENT_SKILL_TARGETS_TABLE).compile(dialect=mysql.dialect()))

    assert "credential_verifier VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin" in ddl
    assert "UNIQUE (credential_verifier)" in ddl
    assert "ck_pc_agent_skill_targets_state_payload" in ddl
    assert "state = 'active'" in ddl
    assert "credential_verifier IS NOT NULL" in ddl


def _assert_restore_layers_are_parent_first(
    restore_layers: tuple[tuple[str, ...], ...],
    tables: tuple[Table, ...],
) -> None:
    restored_tables = tuple(table_name for layer in restore_layers for table_name in layer)
    table_names = {table.name for table in tables}

    assert len(restored_tables) == len(set(restored_tables)) == len(tables)
    assert set(restored_tables) == table_names

    foreign_keys = tuple(
        (table, constraint)
        for table in tables
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )

    layer_by_table = {
        table_name: layer_index for layer_index, layer in enumerate(restore_layers) for table_name in layer
    }
    for table, foreign_key in foreign_keys:
        parent_table_name = foreign_key.referred_table.name
        if parent_table_name == table.name:
            continue
        assert layer_by_table[parent_table_name] < layer_by_table[table.name]


def test_documented_obloader_restore_layers_are_parent_first() -> None:
    restore_guides = (
        Path("docs/en/docs/how-to/troubleshoot.md"),
        Path("docs/zh/docs/how-to/troubleshoot.md"),
    )
    restore_plans = tuple(
        tuple(
            tuple(table_names.split(","))
            for table_names in re.findall(r"--table '([^']+)'", guide.read_text(encoding="utf-8"))
        )
        for guide in restore_guides
    )
    assert all(len(restore_layers) == 3 for restore_layers in restore_plans)
    assert len(set(restore_plans)) == 1

    restore_layers = restore_plans[0]
    _assert_restore_layers_are_parent_first(restore_layers, BUILTIN_TABLES)


def test_every_mysql_utf8mb4_key_stays_below_the_innodb_limit() -> None:
    budgets: dict[str, int] = {}
    for table in SHARED_METADATA.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint | ForeignKeyConstraint):
                columns = tuple(column.name for column in constraint.columns)
                name = (
                    str(constraint.name)
                    if constraint.name is not None
                    else f"{table.name}:{type(constraint).__name__}:{','.join(columns)}"
                )
                budgets[name] = sum(_column_budget(column) for column in constraint.columns)
        for index in table.indexes:
            name = str(index.name) if index.name is not None else f"{table.name}:index"
            budgets[name] = sum(_column_budget(column) for column in index.columns)

    assert budgets
    assert max(budgets.values()) == 2560
    assert all(budget < INNODB_MAX_INDEX_BYTES for budget in budgets.values())
