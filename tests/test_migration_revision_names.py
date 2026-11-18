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

"""Release-based names preserve the existing upgrade and recovery identities."""

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations import MigrationBundle, SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.revision_names import describe_revision
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from tests.builtin.persistence.test_release140_server_migrations import _source
from tests.test_registered_source_maintenance_cli import _environment


@pytest.mark.parametrize("release", ["1.3.0", "1.4.0"])
@pytest.mark.parametrize("vectors", [False, True])
def test_release_names_preserve_source_upgrade_plan_and_evidence(tmp_path, monkeypatch, release, vectors):
    settings, embedding, runner = _source(tmp_path, release=release, vectors=vectors)
    monkeypatch.chdir(tmp_path)
    _environment(monkeypatch, settings, embedding)
    cli = create_cli([app])

    def invoke(action, *options):
        result = CliRunner().invoke(cli, ["server", "db-migrate", action, *options])
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    reviewed = runner.plan(backup_policy="skip")
    original = runner.database.read_bytes()
    status = invoke("status")
    plan = invoke("plan", "--backup", "skip")
    expected_source = {
        "revision": "s1300" if release == "1.3.0" else "r1400",
        "name": "v" + release.replace(".", "_") + "_000",
        "software_version": release,
        "sequence": 0,
    }
    assert status["source_migration"] == plan["source_migration"] == expected_source
    assert plan["plan_id"] == reviewed.plan_id
    assert runner.database.read_bytes() == original
    assert [item["name"] for item in plan["pending_migrations"]] == [
        "v1_4_1_001",
        "v1_4_1_002",
        "v1_4_1_003",
        "v1_4_1_004",
        "v1_4_1_005",
        "v1_4_1_006",
        "v1_5_0_001",
        "v1_5_0_002",
        "v1_5_0_003",
        "v1_5_0_004",
        "v1_5_0_005",
        "v1_5_2_pr2639",
        "v1_5_0_007",
    ]
    assert [item["revision"] for item in plan["pending_migrations"]] == list(reviewed.revisions)
    target = {"revision": "v1_5_0_007", "name": "v1_5_0_007", "software_version": "1.5.0", "sequence": 7}
    assert plan["target_migration"] == target

    applied = invoke(
        "apply",
        "--backup",
        "skip",
        "--plan-id",
        reviewed.plan_id,
        "--yes",
        "--maintenance-confirmed",
        "--accept-no-backup",
    )
    assert applied["migration"] == target and applied["server_ready"]
    with closing(sqlite3.connect(runner.database)) as connection:
        assert connection.execute("SELECT version_num FROM pc_schema_revision").fetchall() == [("v1_5_0_007",)]
    evidence = runner.evidence_path.read_bytes()
    recorded = json.loads(evidence)
    assert recorded["source_revision"] == expected_source["revision"]
    assert set(recorded["release_checksums"]) == {
        expected_source["revision"],
        "s1400",
        "s1401",
        "s1402",
        "v1_4_1_004",
        "v1_4_1_005",
        "v1_4_1_006",
        "v1_5_0_001",
        "v1_5_0_002",
        "v1_5_0_003",
        "v1_5_0_004",
        "v1_5_0_005",
        "v1_5_2_pr2639",
        "v1_5_0_007",
    }
    assert runner.verify().state == "ready"
    assert invoke("verify")["migration"] == target
    repeated = invoke("apply")
    assert repeated["changed"] is False and repeated["migration"] == target
    assert runner.evidence_path.read_bytes() == evidence


@pytest.mark.parametrize("revision", ["v1_4_2_001", "v1_5_0_002"])
def test_new_revisions_describe_their_own_release_without_an_alias(revision):
    _prefix, minor, patch, sequence = revision.split("_")
    assert describe_revision(revision) == {
        "revision": revision,
        "name": revision,
        "software_version": f"1.{minor}.{patch}",
        "sequence": int(sequence),
    }


def test_unregistered_legacy_revisions_do_not_claim_a_software_release():
    assert describe_revision(None) is None
    assert describe_revision("p0004") == {
        "revision": "p0004",
        "name": "p0004",
        "software_version": None,
        "sequence": None,
    }


@pytest.mark.parametrize("revision", ["v1_5_3_pr2674", "v1_5_3_pr2688", "v1_5_3_pr1"])
def test_pr_scoped_revisions_describe_target_release_without_sequence(revision):
    assert describe_revision(revision) == {
        "revision": revision,
        "name": revision,
        "software_version": "1.5.3",
        "sequence": None,
    }


@pytest.mark.parametrize(
    "revision",
    [
        "v1_5_3_pr0",
        "v1_5_3_pr02674",
        "v1_5_3_pr2674_01",
        "v1_5_3_pr2674_1",
        "v1_5_3_pr2674_001",
        "v1_5_3_pr2674_01_extra",
    ],
)
def test_malformed_pr_names_remain_opaque(revision):
    assert describe_revision(revision) == {
        "revision": revision,
        "name": revision,
        "software_version": None,
        "sequence": None,
    }


def test_pr_scoped_ids_follow_dependencies_and_survive_database_round_trip(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "database_migrations"
    resources = shutil.copytree(fixture, tmp_path / "bundle")
    # The smaller PR number deliberately follows the larger one in the chain.
    revisions = ["v1_5_3_pr2688", "v1_5_3_pr2674", "v1_5_3_pr2690"]
    for path in [resources / "manifest.json", *sorted((resources / "versions").glob("*.py"))]:
        content = path.read_text()
        for original, revision in zip(("p0001", "p0002", "p0003"), revisions, strict=True):
            content = content.replace(f'"{original}"', f'"{revision}"')
        path.write_text(content)

    database = tmp_path / "new.sqlite3"
    runner = SQLiteMigrationRunner(database, MigrationBundle(resources))
    plan = runner.plan()
    assert list(plan.revisions) == revisions
    result = runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert result.changed
    assert runner.verify().revision == revisions[-1]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT version_num FROM pc_schema_revision").fetchall() == [(revisions[-1],)]
        assert "memory_citations" in {row[1] for row in connection.execute("PRAGMA table_info(pc_artifacts)")}
    assert not runner.apply().changed
