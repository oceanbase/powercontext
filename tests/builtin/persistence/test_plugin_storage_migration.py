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

"""v1_5_0_005 and v1_5_0_006 remove the previous plugin frameworks' storage only when it holds no data."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest

PREVIOUS = "v1_5_0_004"
RETIRED_TABLES = {
    "pc_artifact_family_schemas",
    "pc_artifact_vector_spaces",
    "pc_artifact_vector_indexes",
    "pc_artifact_search_states",
    "pc_artifact_index_coverage",
    "pc_artifact_index_changes",
    "pc_artifact_vectors",
    "pc_artifact_search_text",
    "pc_artifact_search_fts",
}
RETIRED_COLUMNS = {"plugin_id", "plugin_family", "plugin_spec", "binding_generation", "desired_state", "phase"}


def previous_bundle(directory: Path) -> MigrationBundle:
    """The current resources without the revisions registered after ``PREVIOUS``."""

    current = server_bundle()
    shutil.copytree(current.directory, directory, ignore=shutil.ignore_patterns("__pycache__"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    appended = current.revisions[current.revisions.index(PREVIOUS) + 1 :]
    for revision in appended:
        for resource in manifest["revision_resources"].pop(revision):
            (directory / resource).unlink()
        (directory / Path(current.script.get_revision(revision).path).relative_to(current.snapshot_directory)).unlink()
        (directory / "mysql" / "versions" / f"{revision}.py").unlink()
        del manifest["sqlite_fingerprints"][revision]
        del manifest["domain_readiness"][revision]
    manifest["domain_steps"] = [step for step in manifest["domain_steps"] if step["revision"] not in appended]
    manifest["mysql_state_layouts"] = [name for name in manifest["mysql_state_layouts"] if (directory / name).exists()]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return MigrationBundle(directory)


def configuration():
    return {"processing_manifest": canonical_processing_manifest(BuiltinConfig()), "embedding_enabled": False}


def apply(runner):
    return runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)


def test_upgrade_refuses_retained_plugin_data_and_drops_only_empty_storage(tmp_path):
    database = tmp_path / "managed.db"
    old = SQLiteMigrationRunner(database, previous_bundle(tmp_path / "previous"), configuration=configuration())
    assert apply(old).revision == PREVIOUS
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('kept','title','summary','kept','title','summary',1)"
        )
        connection.execute(
            "INSERT INTO pc_artifact_family_schemas (family,schema_version,declaration) VALUES ('tickets','1','{}')"
        )

    upgraded = SQLiteMigrationRunner(database, server_bundle(), configuration=configuration())
    with pytest.raises(MigrationError) as refused:
        upgraded.plan()
    assert refused.value.code == "unsupported_plugin_data"
    assert "pc_artifact_family_schemas" in str(refused.value)

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DELETE FROM pc_artifact_family_schemas")
    plan = upgraded.plan()
    assert plan.revisions == ("v1_5_0_005", "v1_5_0_006")
    assert plan.domain_steps == ("plugin_artifacts", "source_collection")
    assert apply(upgraded).revision == "v1_5_0_006"
    assert upgraded.verify().state == "ready"
    with closing(sqlite3.connect(database)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        assert not RETIRED_TABLES & tables
        states = {row[1] for row in connection.execute("PRAGMA table_info(pc_artifact_processing_binding_states)")}
        assert not RETIRED_COLUMNS & states
        artifacts = {row[1]: row for row in connection.execute("PRAGMA table_info(pc_artifacts)")}
        assert artifacts["metadata"][3] == 0
        assert connection.execute("SELECT scope_id FROM pc_scopes").fetchall() == [("kept",)]


def test_upgrade_refuses_loaded_source_write_plugins_and_adds_collection_tables(tmp_path):
    database = tmp_path / "managed.db"
    old = SQLiteMigrationRunner(database, previous_bundle(tmp_path / "previous"), configuration=configuration())
    assert apply(old).revision == PREVIOUS
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO pc_source_write_plugins VALUES (?, ?)", ("tickets", b"loaded definition"))

    upgraded = SQLiteMigrationRunner(database, server_bundle(), configuration=configuration())
    with pytest.raises(MigrationError) as refused:
        upgraded.plan()
    assert refused.value.code == "unsupported_plugin_data"

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DELETE FROM pc_source_write_plugins")
    assert apply(upgraded).revision == "v1_5_0_006"
    with closing(sqlite3.connect(database)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    assert "pc_source_write_plugins" not in tables
    assert {"pc_source_runners", "pc_source_collect_targets"} <= tables
