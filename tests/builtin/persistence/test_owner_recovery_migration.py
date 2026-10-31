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

"""Append owner recovery storage without changing historical migration evidence."""

import json
import sqlite3
from contextlib import closing

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from tests.builtin.persistence.test_mysql_migration_schema import _inventory, _ReflectedDatabase
from tests.builtin.persistence.test_portable_storage_migration import apply, configuration, previous_bundle


@pytest.mark.parametrize("vectors", [False, True])
@pytest.mark.parametrize("previous_revision", ["s1402", "v1_4_1_004", "v1_4_1_006"])
def test_registered_owner_recovery_upgrade_preserves_history_and_revision_checksums(
    tmp_path, vectors, previous_revision
):
    embedding = EmbeddingProfile(profile_id="owner-vectors", model="fixture", dimension=3) if vectors else None
    current = server_bundle(embedding_profile=embedding)
    previous = previous_bundle(tmp_path / "previous", embedding, through=previous_revision)
    database = tmp_path / "managed.db"
    old = SQLiteMigrationRunner(database, previous, configuration=configuration(embedding))
    assert apply(old).revision == previous_revision
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('original','title','summary','original','title','summary',1)"
        )
        if previous_revision == "v1_4_1_006":
            connection.execute("INSERT INTO pc_source_write_plugins VALUES (?, ?)", ("tickets", b"retained definition"))
        if previous_revision == "v1_4_1_004":
            connection.execute(
                "INSERT INTO pc_access_owner_recovery (operation_id,deployment_id,scope_id,completed,payload) "
                "VALUES ('pending','deployment','original',0,'retained intent')"
            )
    original_evidence = json.loads(old.evidence_path.read_text(encoding="utf-8"))
    upgraded = SQLiteMigrationRunner(database, current, configuration=configuration(embedding))
    plan = upgraded.plan()
    appended = current.revisions[current.revisions.index(previous_revision) + 1 :]
    assert plan.source_revision == previous_revision and plan.revisions == appended
    assert apply(upgraded).revision == current.head
    assert upgraded.verify().state == "ready"
    assert not apply(upgraded).changed
    evidence = json.loads(upgraded.evidence_path.read_text(encoding="utf-8"))
    for revision, checksum in original_evidence["release_checksums"].items():
        assert evidence["release_checksums"][revision] == checksum
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT scope_id,title,version FROM pc_scopes").fetchall() == [
            ("original", "title", 1)
        ]
        expected_intents = [("pending", "retained intent", 0)] if previous_revision == "v1_4_1_004" else []
        assert (
            connection.execute("SELECT operation_id,payload,completed FROM pc_access_owner_recovery").fetchall()
            == expected_intents
        )
        expected_plugins = [("tickets", b"retained definition")] if previous_revision == "v1_4_1_006" else []
        assert connection.execute("SELECT * FROM pc_source_write_plugins").fetchall() == expected_plugins
        assert connection.execute("SELECT COUNT(*) FROM pc_artifact_family_schemas").fetchone() == (0,)
    fresh = SQLiteMigrationRunner(tmp_path / "fresh.db", current, configuration=configuration(embedding))
    apply(fresh)
    assert upgraded.plan().schema_fingerprint == fresh.plan().schema_fingerprint


@pytest.mark.parametrize("target", ["owner", "current"])
@pytest.mark.parametrize("damage", [None, "payload-collation", "pending-index"])
def test_owner_recovery_mysql_registration_matches_native_character_and_key_definitions(damage, target):
    # Simulated SHOW output checks normalization, not execution on a native backend.
    definition = """CREATE TABLE `pc_access_owner_recovery` (
      `operation_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
      `deployment_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
      `scope_id` varchar(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
      `completed` tinyint(1) NOT NULL,
      `payload` text CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
      PRIMARY KEY (`operation_id`),
      UNIQUE KEY `uq_pc_access_owner_recovery_pending` (`deployment_id`,`completed`,`operation_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"""
    bundle = server_bundle()
    target_revision = "v1_4_1_004" if target == "owner" else bundle.head
    inventory = {"tables": {}, "views": [], "triggers": []}
    for revision in bundle.revisions[: bundle.revisions.index(target_revision) + 1]:
        path = bundle.directory / f"schemas/mysql-{revision}.json"
        fragment = json.loads(path.read_text(encoding="utf-8"))
        for change in fragment["revisions"][revision]["changes"]:
            for name, table in change.items():
                if table is None:
                    del inventory["tables"][name]
                else:
                    inventory["tables"][name] = table
    table = _inventory(_ReflectedDatabase({"pc_access_owner_recovery": definition}))["tables"][
        "pc_access_owner_recovery"
    ]
    inventory["tables"]["pc_access_owner_recovery"] = table
    if damage == "payload-collation":
        table["columns"]["payload"]["collation"] = "utf8mb4_general_ci"
    elif damage == "pending-index":
        table["indexes"] = {}
    schema = MySQLSchema(bundle)
    if damage is None:
        schema.verify(inventory, target_revision)
        assert schema.identify(inventory) == target_revision
    else:
        with pytest.raises(MigrationError, match="incompatible_schema"):
            schema.verify(inventory, target_revision)
