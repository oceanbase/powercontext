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

"""Registered Atomic Memory metadata survives managed vector profile selection."""

import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema, _parse_table
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest


@pytest.mark.parametrize("source_release", [None, "1.4.0"])
def test_managed_sqlite_vector_schema_includes_registered_nullable_description(tmp_path, source_release):
    profile = EmbeddingProfile(profile_id="description-space", model="fixture", dimension=3)
    bundle = server_bundle(embedding_profile=profile, source_release=source_release)
    configuration = {
        "embedding_enabled": True,
        "embedding_profile": profile.model_dump(mode="json"),
        "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
    }
    database = tmp_path / "vector.db"
    runner = SQLiteMigrationRunner(database, bundle, configuration=configuration)
    result = runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)
    assert result.revision == "v1_4_1_006"
    with closing(sqlite3.connect(database)) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(pc_atomic_memory_current)")}
        assert columns["description"][2:4] == ("VARCHAR(200)", 0)
    assert runner.verify().state == "ready"


def test_mysql_vector_admission_retains_description_and_exact_previous_revision():
    profile = EmbeddingProfile(profile_id="description-space", model="fixture", dimension=3)
    plain = server_bundle()
    vector = server_bundle(embedding_profile=profile)
    assert vector.vector_profile is not None
    inventory = {"tables": {}, "views": [], "triggers": []}
    for resource in json.loads((plain.directory / "manifest.json").read_text())["mysql_state_layouts"]:
        if resource == "schemas/mysql-v1_4_1_006.json":
            continue
        for entry in json.loads((plain.directory / resource).read_text())["revisions"].values():
            for change in entry["changes"]:
                for name, table in change.items():
                    if table is None:
                        del inventory["tables"][name]
                    else:
                        inventory["tables"][name] = table
    for name, sql in vector.vector_profile.mysql_tables().items():
        if name == "pc_atomic_memory_current":
            sql = sql.replace(
                "  PRIMARY KEY",
                "  `description` varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL,\n  PRIMARY KEY",
            )
        inventory["tables"][name] = _parse_table(sql)[1]
    declaration = vector.vector_profile.layout["mysql_profile_table"]
    name, table = _parse_table(declaration)
    inventory["tables"][name] = table
    schema = MySQLSchema(vector)
    schema.verify(inventory, "v1_4_1_005")
    assert schema.identify(inventory) == "v1_4_1_005"
    description = inventory["tables"]["pc_atomic_memory_current"]["columns"].pop("description")
    schema.verify(inventory, "v1_4_1_004")
    recovery = schema.recovery(inventory, "v1_4_1_004")
    assert recovery.revision == "v1_4_1_005" and len(recovery.pending_statements) == 1
    with pytest.raises(MigrationError, match="incompatible_schema"):
        schema.verify(inventory, "v1_4_1_005")
    inventory["tables"]["pc_atomic_memory_current"]["columns"]["description"] = description
    description["nullable"] = False
    with pytest.raises(MigrationError, match="incompatible_schema"):
        schema.verify(inventory, "v1_4_1_005")


@pytest.mark.parametrize("source_release", [None, "1.4.0"])
@pytest.mark.parametrize("vectors", [False, True])
def test_description_upgrade_preserves_registered_owner_recovery_and_evidence(tmp_path, source_release, vectors):
    from powercontext.builtin.persistence.migrations.bundle import MigrationBundle

    profile = EmbeddingProfile(profile_id="owner-description", model="fixture", dimension=3) if vectors else None
    current = server_bundle(embedding_profile=profile, source_release=source_release)
    directory = tmp_path / "owner-bundle"
    shutil.copytree(current.snapshot_directory, directory, ignore=shutil.ignore_patterns("__pycache__"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    locator = manifest["revision_resources"].pop("v1_4_1_006")
    for resource in (*locator, "versions/v1_4_1_006_source_locators.py", "mysql/versions/v1_4_1_006.py"):
        (directory / resource).unlink()
    del manifest["sqlite_fingerprints"]["v1_4_1_006"]
    del manifest["domain_readiness"]["v1_4_1_006"]
    manifest["mysql_state_layouts"].remove("schemas/mysql-v1_4_1_006.json")
    manifest["affected_objects"].remove("pc_artifact_source_locators")
    resources = manifest["revision_resources"].pop("v1_4_1_005")
    for resource in (*resources, "versions/v1_4_1_005_atomic_memory_description.py", "mysql/versions/v1_4_1_005.py"):
        (directory / resource).unlink()
    del manifest["sqlite_fingerprints"]["v1_4_1_005"]
    del manifest["domain_readiness"]["v1_4_1_005"]
    manifest["mysql_state_layouts"].remove("schemas/mysql-v1_4_1_005.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    previous = MigrationBundle(directory, embedding_profile=profile)
    configuration = {
        "embedding_enabled": profile is not None,
        "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
    }
    if profile is not None:
        configuration["embedding_profile"] = profile.model_dump(mode="json")
    database = tmp_path / "owner.db"
    old = SQLiteMigrationRunner(database, previous, configuration=configuration)
    assert old.apply(plan_id=old.plan().plan_id, accepted=True, maintenance_confirmed=True).revision == "v1_4_1_004"
    owner_record = ("original-operation", "original-deployment", "original-scope", 0, '{"principal":"原始身份"}')
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO pc_access_owner_recovery VALUES (?,?,?,?,?)", owner_record)
    original_evidence = json.loads(old.evidence_path.read_text(encoding="utf-8"))
    upgraded = SQLiteMigrationRunner(database, current, configuration=configuration)
    plan = upgraded.plan()
    assert plan.source_revision == "v1_4_1_004" and plan.revisions == ("v1_4_1_005", "v1_4_1_006")
    assert upgraded.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True).revision == "v1_4_1_006"
    assert upgraded.verify().state == "ready"
    evidence = json.loads(upgraded.evidence_path.read_text(encoding="utf-8"))
    for revision, checksum in original_evidence["release_checksums"].items():
        assert evidence["release_checksums"][revision] == checksum
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT * FROM pc_access_owner_recovery").fetchall() == [owner_record]
