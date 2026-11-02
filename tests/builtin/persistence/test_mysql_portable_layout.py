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

"""Strict portable layout recognition using native Boolean reflection evidence.

The receipt SHOW CREATE below is modeled, not a claimed native capture. Its
Boolean spelling is anchored to the separately captured seekDB 1.4 table.
"""

import json
from pathlib import Path

import pytest

from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema
from tests.builtin.persistence.test_mysql_migration_schema import _inventory, _ReflectedDatabase

_RECEIPT = "pc_portable_restore_receipts"
_RECEIPT_SHOW_CREATE = """CREATE TABLE `pc_portable_restore_receipts` (
  `bundle_id` varchar(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `format_version` int(11) NOT NULL,
  `total_digest` varchar(71) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `record_count` bigint(20) NOT NULL,
  `status` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `inserted` bigint(20) NOT NULL,
  `already_present` bigint(20) NOT NULL,
  `projections_ready` tinyint(1) NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`bundle_id`),
  CONSTRAINT `ck_pc_portable_restore_receipts_status` CHECK ((`status` in ('authoritative_restored','ready'))),
  CONSTRAINT `ck_pc_portable_restore_receipts_record_count` CHECK ((`record_count` >= 0)),
  CONSTRAINT `ck_pc_portable_restore_receipts_inserted` CHECK ((`inserted` >= 0)),
  CONSTRAINT `ck_pc_portable_restore_receipts_existing` CHECK ((`already_present` >= 0))
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC
COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0"""
_PRECISION_COLUMNS = (
    ("pc_artifacts", "created_at"),
    ("pc_profile_policies", "updated_at"),
    ("pc_topic_memory_revision_publications", "published_at"),
    ("pc_artifact_tags", "assigned_at"),
)


def historical_inventory(bundle):
    inventory = {"tables": {}, "views": [], "triggers": []}
    for revision in ("s1300", "s1400", "s1401"):
        fragment = json.loads((bundle.directory / f"schemas/mysql-{revision}.json").read_text())
        for change in fragment["revisions"][revision]["changes"]:
            for name, table in change.items():
                if table is None:
                    del inventory["tables"][name]
                else:
                    inventory["tables"][name] = table
    return inventory


def receipt_inventory():
    return _inventory(_ReflectedDatabase({_RECEIPT: _RECEIPT_SHOW_CREATE}))["tables"][_RECEIPT]


def test_native_boolean_reflection_allows_only_registered_portable_recovery_prefixes():
    fixture = Path(__file__).parents[2] / "fixtures/database_migrations/schemas/seekdb_v1_3_0_checks.json"
    captured = json.loads(fixture.read_text())["tables"]["pc_model_usage_daily"]
    reflected = _inventory(_ReflectedDatabase({"pc_model_usage_daily": captured["show_create_table"]}))
    native_boolean = reflected["tables"]["pc_model_usage_daily"]["columns"]["input_complete"]
    receipt = receipt_inventory()
    assert receipt["columns"]["projections_ready"] == native_boolean

    bundle = server_bundle()
    schema = MySQLSchema(bundle)
    inventory = historical_inventory(bundle)
    assert schema.identify(inventory) == "s1401"
    inventory["tables"][_RECEIPT] = receipt
    recovery = schema.recovery(inventory, "s1401")
    assert recovery.revision == "s1402"
    assert recovery.pending_statements == tuple(
        f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` DATETIME(6) "
        + ("NULL" if table == "pc_artifacts" else "NOT NULL")
        for table, column in _PRECISION_COLUMNS
    )
    for position, (table, column) in enumerate(_PRECISION_COLUMNS, 1):
        inventory["tables"][table]["columns"][column]["type"] = ["datetime", ["6"]]
        assert schema.recovery(inventory, "s1401").pending_statements == recovery.pending_statements[position:]
    assert schema.identify(inventory) == "s1402"
    schema.verify(inventory, "s1402")


@pytest.mark.parametrize("damage", ["boolean_width", "timestamp_precision", "missing_constraint"])
def test_portable_target_rejects_unregistered_types_and_constraints(damage):
    bundle = server_bundle()
    schema = MySQLSchema(bundle)
    inventory = historical_inventory(bundle)
    inventory["tables"][_RECEIPT] = receipt_inventory()
    for table, column in _PRECISION_COLUMNS:
        inventory["tables"][table]["columns"][column]["type"] = ["datetime", ["6"]]
    if damage == "boolean_width":
        inventory["tables"][_RECEIPT]["columns"]["projections_ready"]["type"] = ["tinyint", ["2"]]
    elif damage == "timestamp_precision":
        inventory["tables"]["pc_artifacts"]["columns"]["created_at"]["type"] = ["datetime", ["3"]]
    else:
        del inventory["tables"][_RECEIPT]["checks"]["ck_pc_portable_restore_receipts_record_count"]
    with pytest.raises(MigrationError, match="incompatible_schema"):
        schema.verify(inventory, "s1402")
    with pytest.raises(MigrationError, match="incompatible_schema"):
        schema.recovery(inventory, "s1401")
