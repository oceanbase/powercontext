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

"""The evidence revision must accept maintenance history written by upstream."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest

EVIDENCE_REVISION = "v1_5_0_002"
PROJECTION_REVISION = "v1_5_0_003"
EVIDENCE_TABLES = {
    "pc_atomic_memory_evidence_schema",
    "pc_atomic_memory_evidence_history",
    "pc_atomic_memory_evidence",
    "pc_source_evidence_bindings",
}

# Recorded from the unmodified aedf7beb upstream bundle, not recomputed from
# resources under test. Vector checksums bind the exact profile used below.
UPSTREAM_RELEASE_CHECKSUMS = {
    False: {
        "s1300": "ed030becdb3d2aeffd289440fd492fa31c7e53b6c3f8e30aa7119835353f1d44",
        "s1400": "ab457bcd03e7bf7e7003342104947b031dcfd30e121e836df3af6eddc7dce690",
        "s1401": "bcd58485ec5d1945b18a03335346aaef2eebe403fbfc62ca73e8d56059035e85",
        "s1402": "c0642c4535e2056fd9c2997a70621c26f6c46aa3d9d6cc4dedd1656bc0397c24",
        "v1_4_1_004": "3d3e3a63024d0c4f7efae425c13137db32772f5afb5686148cbd0a5a6d434c54",
    },
    True: {
        "s1300": "2a20255dca66ad183260dcf40dffdf493eae0e160000e6aa3d47bbb20ee92310",
        "s1400": "08013a6084b7cfedda54ecc10347621b4a5460fbbf1935cdd25791799d185158",
        "s1401": "3bc6adcd391ccc04fac21acd2ebcdf372295513024452e6ad613e1620c72d06b",
        "s1402": "e262e4ef25c43b65ec356e3bd27ccfa46c0100e1689aabf31756c205c9098b23",
        "v1_4_1_004": "8340a6331e2fc81a44f79cc7848feb209ac61338ff565e2d42c5a607e9e61c5b",
    },
}


def previous_upstream_bundle(directory, embedding):
    """Drop registrations appended after the frozen upstream owner-recovery revision.

    Historical checksums below are independent upstream constants: copying the
    installed resources is not allowed to silently redefine the old release.
    No git checkout, network access or runtime table initializer is required.
    """

    current = server_bundle(embedding_profile=embedding)
    shutil.copytree(current.directory, directory, ignore=shutil.ignore_patterns("__pycache__"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    appended = current.revisions[current.revisions.index("v1_4_1_004") + 1 :]
    removed_resources = {resource for revision in appended for resource in manifest["revision_resources"].pop(revision)}
    removed_steps = [step for step in manifest["domain_steps"] if step["revision"] in appended]
    manifest["domain_steps"] = [step for step in manifest["domain_steps"] if step["revision"] not in appended]
    for step in removed_steps:
        removed_resources.update((step["module"], *step.get("resources", ())))
    retained_resources = {resource for resources in manifest["revision_resources"].values() for resource in resources}
    for step in manifest["domain_steps"]:
        retained_resources.update((step["module"], *step.get("resources", ())))
    # The bundle substitutes its vector domain module and profile at load time.
    retained_resources.update({"server_vector_domain.py", "profiles/vector.json"})
    for name in appended:
        revision = current.script.get_revision(name)
        removed_resources.add(Path(revision.path).relative_to(current.snapshot_directory).as_posix())
        removed_resources.add(f"mysql/versions/{name}.py")
    for resource in removed_resources - retained_resources:
        (directory / resource).unlink()
    for revision in appended:
        del manifest["sqlite_fingerprints"][revision]
        del manifest["domain_readiness"][revision]
    manifest["mysql_state_layouts"] = [
        resource for resource in manifest["mysql_state_layouts"] if resource not in removed_resources
    ]
    for revision in appended:
        manifest.get("mysql_wrappers", {}).pop(revision, None)
    manifest["affected_objects"] = [
        name
        for name in manifest["affected_objects"]
        if name not in EVIDENCE_TABLES and name != "pc_source_write_plugins"
    ]
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


@pytest.mark.parametrize("vectors", [False, True], ids=["lexical", "vectors"])
def test_evidence_upgrade_accepts_upstream_history_and_preserves_data(tmp_path, vectors):
    embedding = EmbeddingProfile(profile_id="evidence-upgrade", model="fixture", dimension=3) if vectors else None
    config = configuration(embedding)
    expected = UPSTREAM_RELEASE_CHECKSUMS[vectors]
    previous = previous_upstream_bundle(tmp_path / "upstream-bundle", embedding)
    assert previous.head == "v1_4_1_004"
    assert previous.checksums == expected

    database = tmp_path / "managed.db"
    old = SQLiteMigrationRunner(database, previous, configuration=config)
    assert apply(old).revision == "v1_4_1_004"
    historical_time = "2026-10-23 12:34:56.123456"
    historical_content = b'{"history":"unchanged"}'
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('original','title','summary','original','title','summary',1)"
        )
        connection.execute(
            "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content,created_at) "
            "VALUES ('original','custom','preserved',1,?,?)",
            (historical_content, historical_time),
        )
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        assert not EVIDENCE_TABLES & tables
    assert old.verify().state == "ready"
    old_evidence_bytes = old.evidence_path.read_bytes()
    old_evidence = json.loads(old_evidence_bytes)
    assert old_evidence["state"] == "complete"
    assert old_evidence["release_checksums"] == expected

    current = server_bundle(embedding_profile=embedding)
    assert {revision: current.checksums[revision] for revision in expected} == expected
    upgraded = SQLiteMigrationRunner(database, current, configuration=config)
    plan = upgraded.plan()
    assert plan.state == "migration_required"
    assert plan.source_revision == "v1_4_1_004"
    assert plan.target_revision == PROJECTION_REVISION
    assert plan.revisions == ("v1_4_1_005", "v1_4_1_006", "v1_5_0_001", EVIDENCE_REVISION, PROJECTION_REVISION)
    assert plan.domain_steps == (
        "artifact_storage",
        "atomic_memory_evidence",
        "atomic_memory_evidence_projection",
    )
    assert upgraded.evidence_path.read_bytes() == old_evidence_bytes
    result = upgraded.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert result.changed
    assert result.revision == PROJECTION_REVISION
    assert result.backup_state == "completed"

    # Reconstruct both bundle and runner: verification must use retained on-disk
    # maintenance history, not an in-memory object from the upgrade operation.
    reopened = SQLiteMigrationRunner(database, server_bundle(embedding_profile=embedding), configuration=config)
    completed_evidence_bytes = reopened.evidence_path.read_bytes()
    completed_evidence = json.loads(completed_evidence_bytes)
    assert completed_evidence["state"] == "complete"
    assert {revision: completed_evidence["release_checksums"][revision] for revision in expected} == expected
    assert set(completed_evidence["release_checksums"]) == {
        *expected,
        "v1_4_1_005",
        "v1_4_1_006",
        "v1_5_0_001",
        EVIDENCE_REVISION,
        PROJECTION_REVISION,
    }
    ready = reopened.plan()
    assert ready.state == "ready"
    assert ready.revisions == ()
    assert reopened.verify().revision == PROJECTION_REVISION
    assert not apply(reopened).changed
    assert reopened.evidence_path.read_bytes() == completed_evidence_bytes
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT scope_id,title,summary,version FROM pc_scopes").fetchall() == [
            ("original", "title", "summary", 1)
        ]
        assert connection.execute(
            "SELECT scope_id,family,artifact_id,revision,content,created_at FROM pc_artifacts"
        ).fetchall() == [("original", "custom", "preserved", 1, historical_content, historical_time)]
        assert connection.execute("SELECT schema_version FROM pc_atomic_memory_evidence_schema").fetchall() == [
            ("p0005",)
        ]
        assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_evidence_history").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_evidence").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM pc_source_evidence_bindings").fetchone() == (0,)
        if embedding is not None:
            stored_profile = connection.execute("SELECT embedding_profile FROM pc_server_storage_profile").fetchone()
            assert json.loads(stored_profile[0]) == embedding.model_dump(mode="json")

    # Removing only the marker preserves the schema fingerprint. Live validation
    # must still reject incomplete evidence readiness and must not repair it.
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DELETE FROM pc_atomic_memory_evidence_schema")
    with pytest.raises(MigrationError) as caught:
        reopened.verify()
    assert caught.value.code == "domain_validation_failed"
    assert reopened.evidence_path.read_bytes() == completed_evidence_bytes
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT schema_version FROM pc_atomic_memory_evidence_schema").fetchall() == []


@pytest.mark.parametrize("vectors", [False, True], ids=["lexical", "vectors"])
def test_evidence_projection_upgrade_preserves_registered_evidence_revision(tmp_path, vectors):
    embedding = EmbeddingProfile(profile_id="evidence-upgrade", model="fixture", dimension=3) if vectors else None
    current = server_bundle(embedding_profile=embedding)
    directory = tmp_path / "evidence-bundle"
    shutil.copytree(current.directory, directory, ignore=shutil.ignore_patterns("__pycache__"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    removed = manifest["revision_resources"].pop(PROJECTION_REVISION)
    removed.append("atomic_memory_evidence_projection.py")
    revision = current.script.get_revision(PROJECTION_REVISION)
    removed.append(Path(revision.path).relative_to(current.snapshot_directory).as_posix())
    removed.append(f"mysql/versions/{PROJECTION_REVISION}.py")
    for resource in removed:
        (directory / resource).unlink()
    del manifest["sqlite_fingerprints"][PROJECTION_REVISION]
    del manifest["domain_readiness"][PROJECTION_REVISION]
    manifest["domain_steps"] = [step for step in manifest["domain_steps"] if step["revision"] != PROJECTION_REVISION]
    manifest["mysql_state_layouts"] = [name for name in manifest["mysql_state_layouts"] if name not in removed]
    manifest["affected_objects"].remove("pc_atomic_memory_evidence")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    previous = MigrationBundle(directory, embedding_profile=embedding)
    assert previous.head == EVIDENCE_REVISION
    assert {revision: current.checksums[revision] for revision in previous.revisions} == previous.checksums

    database = tmp_path / "adjacent.db"
    config = configuration(embedding)
    old = SQLiteMigrationRunner(database, previous, configuration=config)
    assert apply(old).revision == EVIDENCE_REVISION
    before = old.evidence_path.read_bytes()
    upgraded = SQLiteMigrationRunner(database, current, configuration=config)
    plan = upgraded.plan()
    assert plan.revisions == (PROJECTION_REVISION,)
    assert plan.domain_steps == ("atomic_memory_evidence_projection",)
    assert old.evidence_path.read_bytes() == before
    assert (
        upgraded.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True).revision == PROJECTION_REVISION
    )
    assert upgraded.verify().state == "ready"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_evidence").fetchone() == (0,)
        assert connection.execute("SELECT schema_version FROM pc_atomic_memory_evidence_schema").fetchall() == [
            ("p0005",)
        ]
