# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Usage history starts empty and preserves the previous database and migration evidence."""

import json
import shutil
import sqlite3

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from tests.builtin.persistence.migration_history import remove_context_usage_revision
from tests.builtin.persistence.test_portable_storage_migration import apply, configuration


@pytest.mark.parametrize("vectors", [False, True])
def test_context_usage_upgrade_is_additive(tmp_path, vectors):
    embedding = EmbeddingProfile(profile_id="usage", model="fixture", dimension=3) if vectors else None
    current = server_bundle(embedding_profile=embedding)
    directory = tmp_path / "previous"
    shutil.copytree(current.directory, directory, ignore=shutil.ignore_patterns("__pycache__"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    remove_context_usage_revision(directory, manifest)
    manifest_path.write_text(json.dumps(manifest))
    previous = MigrationBundle(directory, embedding_profile=embedding)
    database = tmp_path / "managed.db"
    old = SQLiteMigrationRunner(database, previous, configuration=configuration(embedding))
    assert apply(old).revision == "v1_5_0_005"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) VALUES ('original','unchanged','summary','original','unchanged','summary',1)"
        )
    prior = json.loads(old.evidence_path.read_text())
    upgraded = SQLiteMigrationRunner(database, current, configuration=configuration(embedding))
    assert upgraded.plan().revisions == ("v1_5_0_006",)
    result = apply(upgraded)
    assert result.revision == "v1_5_0_006" and result.backup_state == "completed"
    assert upgraded.verify().state == "ready"
    evidence = json.loads(upgraded.evidence_path.read_text())
    assert all(evidence["release_checksums"][key] == value for key, value in prior["release_checksums"].items())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT title FROM pc_scopes WHERE scope_id='original'").fetchone() == ("unchanged",)
        assert connection.execute("SELECT count(*) FROM pc_context_usage_records").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM pc_context_usage_feedback").fetchone() == (0,)
    assert not apply(upgraded).changed
