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

"""Managed upgrades retain existing evidence while admitting current portable storage."""

import json
import re
import shutil
import sqlite3
from contextlib import closing

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest


def _created_tables(path):
    """Tables a frozen SQLite schema resource registers, in its exact bytes."""

    resource = json.loads(path.read_text(encoding="utf-8"))
    if "layout" in resource:
        return {row[1] for row in resource["layout"] if row[0] == "table"}
    payload = resource["create"] if "create" in resource else resource.get("statements", resource["statement"])
    statements = [payload] if isinstance(payload, str) else payload
    return {
        match[1]
        for statement in statements
        if (match := re.match(r"CREATE TABLE (?:IF NOT EXISTS )?[`\"]?(\w+)", statement))
    }


def previous_bundle(directory, embedding, *, through="s1401"):
    current = server_bundle(embedding_profile=embedding)
    shutil.copytree(current.directory, directory, ignore=shutil.ignore_patterns("__pycache__"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    revisions = list(manifest["revision_resources"])
    # Prune every revision after the requested previous head so the fixture
    # keeps describing the same earlier registration as later revisions are appended.
    created = set()
    pruned_modules = set()
    for revision in revisions[revisions.index(through) + 1 :]:
        pruned_modules |= {step["module"] for step in manifest["domain_steps"] if step["revision"] == revision}
        for resource in manifest["revision_resources"].pop(revision):
            path = directory / resource
            if resource.startswith("schemas/sqlite") and resource.endswith(".json"):
                created |= _created_tables(path)
            path.unlink(missing_ok=True)
            if resource in manifest["mysql_state_layouts"]:
                manifest["mysql_state_layouts"].remove(resource)
        for version in (directory / "versions").glob(f"{revision}_*.py"):
            version.unlink()
        (directory / "mysql" / "versions" / f"{revision}.py").unlink(missing_ok=True)
        del manifest["sqlite_fingerprints"][revision]
        del manifest["domain_readiness"][revision]
        manifest.get("mysql_wrappers", {}).pop(revision, None)
        manifest["domain_steps"] = [step for step in manifest["domain_steps"] if step["revision"] != revision]
    manifest["affected_objects"] = [name for name in manifest["affected_objects"] if name not in created]
    for module in pruned_modules - {step["module"] for step in manifest["domain_steps"]}:
        (directory / module).unlink(missing_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return MigrationBundle(directory, embedding_profile=embedding)


def configuration(embedding):
    result = {
        "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
        "embedding_enabled": embedding is not None,
    }
    if embedding is not None:
        result["embedding_profile"] = embedding.model_dump(mode="json")
    return result


def apply(runner):
    return runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)


@pytest.mark.parametrize("vectors", [False, True])
def test_registered_s1401_upgrade_preserves_completed_evidence_and_matches_fresh_storage(tmp_path, vectors):
    embedding = EmbeddingProfile(profile_id="portable-vectors", model="fixture", dimension=3) if vectors else None
    config = configuration(embedding)
    previous = previous_bundle(tmp_path / "previous-bundle", embedding)
    database = tmp_path / "existing.db"
    old = SQLiteMigrationRunner(database, previous, configuration=config)
    assert apply(old).revision == "s1401"
    historical_time = "2026-10-23 12:34:56.123456"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('scope','title','summary','scope','title','summary',1)"
        )
        connection.execute(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content,created_at) "
            "VALUES ('scope','custom','preserved',1,?,?)",
            (b'{"history":"unchanged"}', historical_time),
        )
    assert old.verify().state == "ready"

    bundle = server_bundle(embedding_profile=embedding)
    upgraded = SQLiteMigrationRunner(database, bundle, configuration=config)
    plan = upgraded.plan()
    assert plan.source_revision == "s1401"
    assert plan.revisions == (
        "s1402",
        "v1_4_1_004",
        "v1_4_1_005",
        "v1_4_1_006",
        "v1_5_0_001",
        "v1_5_0_002",
        "v1_5_0_003",
    )
    assert apply(upgraded).revision == "v1_5_0_003"
    assert upgraded.verify().state == "ready"
    assert not apply(upgraded).changed
    fresh = SQLiteMigrationRunner(tmp_path / "fresh.db", bundle, configuration=config)
    apply(fresh)
    assert upgraded.plan().schema_fingerprint == fresh.plan().schema_fingerprint
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute(
            "SELECT content,created_at FROM pc_artifacts WHERE artifact_id='preserved'"
        ).fetchone() == (b'{"history":"unchanged"}', historical_time)
        connection.execute(
            "INSERT INTO pc_portable_restore_receipts "
            "(bundle_id,format_version,total_digest,record_count,status,inserted,already_present,projections_ready,updated_at) "
            "VALUES ('receipt',1,'sha256:fixture',0,'ready',0,0,1,?)",
            (historical_time,),
        )
    assert upgraded.verify().state == "ready"


@pytest.mark.parametrize("vectors", [False, True])
def test_pending_portable_restore_blocks_migration_readiness_without_repair(tmp_path, vectors):
    embedding = EmbeddingProfile(profile_id="portable-vectors", model="fixture", dimension=3) if vectors else None
    runner = SQLiteMigrationRunner(
        tmp_path / "managed.db", server_bundle(embedding_profile=embedding), configuration=configuration(embedding)
    )
    apply(runner)
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_portable_restore_receipts "
            "(bundle_id,format_version,total_digest,record_count,status,inserted,already_present,projections_ready,updated_at) "
            "VALUES ('receipt',1,'sha256:fixture',0,'authoritative_restored',0,0,0,'2026-10-23 12:34:56.123456')"
        )
    before = runner.database.read_bytes()
    for check in (runner.plan, runner.verify):
        with pytest.raises(MigrationError, match="archive_recovery_required"):
            check()
    assert runner.database.read_bytes() == before
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("UPDATE pc_portable_restore_receipts SET status='ready',projections_ready=1")
    assert runner.verify().state == "ready"


@pytest.mark.parametrize("vectors", [False, True])
@pytest.mark.parametrize("include_owner", [False, True])
def test_source_write_plugin_upgrade_preserves_data_and_registration(tmp_path, vectors, include_owner):
    embedding = EmbeddingProfile(profile_id="source-vectors", model="fixture", dimension=3) if vectors else None
    config = configuration(embedding)
    previous = previous_bundle(tmp_path / "previous", embedding, through=("v1_4_1_004" if include_owner else "s1402"))
    database = tmp_path / "existing.db"
    old = SQLiteMigrationRunner(database, previous, configuration=config)
    assert apply(old).revision == ("v1_4_1_004" if include_owner else "s1402")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('scope','preserved','summary','scope','preserved','summary',1)"
        )
    bundle = server_bundle(embedding_profile=embedding)
    upgraded = SQLiteMigrationRunner(database, bundle, configuration=config)
    assert upgraded.plan().revisions == (
        ("v1_4_1_005", "v1_4_1_006", "v1_5_0_001", "v1_5_0_002", "v1_5_0_003")
        if include_owner
        else ("v1_4_1_004", "v1_4_1_005", "v1_4_1_006", "v1_5_0_001", "v1_5_0_002", "v1_5_0_003")
    )
    assert apply(upgraded).revision == "v1_5_0_003"
    definition = b'{"configuration":{"id":"tickets","entry":"tickets"}}'
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT title FROM pc_scopes").fetchall() == [("preserved",)]
        connection.execute("INSERT INTO pc_source_write_plugins VALUES (?, ?)", ("tickets", definition))
    reopened = SQLiteMigrationRunner(database, bundle, configuration=config)
    assert reopened.verify().state == "ready"
    assert not apply(reopened).changed
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT plugin_id, definition FROM pc_source_write_plugins").fetchall() == [
            ("tickets", definition)
        ]
    fresh = SQLiteMigrationRunner(tmp_path / "fresh.db", bundle, configuration=config)
    apply(fresh)
    assert reopened.plan().schema_fingerprint == fresh.plan().schema_fingerprint
