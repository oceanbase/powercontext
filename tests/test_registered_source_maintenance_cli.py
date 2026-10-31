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

"""Compatibility commands preserve registered Server sources before adoption."""

import json
import os
import sqlite3
from contextlib import closing

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from tests.builtin.persistence.test_release140_server_migrations import _candidate_rows, _source


def _environment(monkeypatch, settings, embedding):
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", settings.database.url)
    monkeypatch.setenv("POWERCONTEXT_SERVER_MCP_ENABLED", "false")
    monkeypatch.setenv("POWERCONTEXT_SERVER_CODE", settings.code.model_dump_json())
    if embedding is not None:
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", embedding.model)
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID", embedding.profile_id)
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION", str(embedding.dimension))
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION", embedding.normalization)


@pytest.mark.parametrize(("vectors", "code"), [(False, False), (True, False), (False, True), (True, True)])
def test_release140_compatibility_commands_preserve_source_and_share_full_upgrade(tmp_path, monkeypatch, vectors, code):
    settings, embedding, runner = _source(tmp_path, vectors=vectors, code=code)
    monkeypatch.chdir(tmp_path)
    _environment(monkeypatch, settings, embedding)
    cli = create_cli([app])
    original = runner.database.read_bytes()
    candidates = _candidate_rows(runner.database)

    for command in ("processing-migrate", "atomic-memory-rebuild-projection", "atomic-memory-migrate"):
        result = CliRunner().invoke(cli, ["server", command, "--action", "apply", "--maintenance-confirmed"])
        assert result.exit_code != 0, result.output
        assert runner.database.read_bytes() == original
        status = CliRunner().invoke(cli, ["server", "db-migrate", "status"])
        assert status.exit_code == 0, status.output
        assert json.loads(status.output)["source_revision"] == "r1400"

    arguments = ["server", "atomic-memory-migrate", "--action"]
    planned = CliRunner().invoke(cli, [*arguments, "plan", "--backup", "skip"])
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    assert plan["source_revision"] == "r1400" and plan["target_revision"] == "v1_5_0_001"
    assert plan["readiness_scope"] == "server"
    full = CliRunner().invoke(cli, ["server", "db-migrate", "plan", "--backup", "skip"])
    assert full.exit_code == 0, full.output
    assert json.loads(full.output)["plan_id"] == plan["plan_id"]
    assert runner.database.read_bytes() == original

    applied = CliRunner().invoke(
        cli,
        [
            *arguments,
            "apply",
            "--maintenance-confirmed",
            "--plan-id",
            plan["plan_id"],
            "--backup",
            "skip",
            "--accept-no-backup",
            "--yes",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["readiness_scope"] == "server"
    verified = CliRunner().invoke(cli, ["server", "db-migrate", "verify"])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["state"] == "ready"
    assert _candidate_rows(runner.database) == candidates


@pytest.mark.parametrize("command", ["atomic-memory-migrate", "processing-migrate", "atomic-memory-rebuild-projection"])
@pytest.mark.parametrize("mismatch", ["extra-object", "missing-embedding-configuration"])
def test_unrecognized_server_never_falls_back_to_legacy_writes(tmp_path, monkeypatch, command, mismatch):
    vectors = mismatch == "missing-embedding-configuration"
    settings, embedding, runner = _source(tmp_path, vectors=vectors)
    monkeypatch.chdir(tmp_path)
    if mismatch == "extra-object":
        with closing(sqlite3.connect(runner.database)) as connection, connection:
            connection.execute("CREATE TABLE unknown_server_object(value TEXT)")
    _environment(monkeypatch, settings, None)
    original = runner.database.read_bytes()
    cli = create_cli([app])

    denied = CliRunner().invoke(cli, ["server", command, "--action", "apply", "--maintenance-confirmed"])

    assert denied.exit_code != 0, denied.output
    assert runner.database.read_bytes() == original
    if vectors:
        _environment(monkeypatch, settings, embedding)
        status = CliRunner().invoke(cli, ["server", "db-migrate", "status"])
        assert status.exit_code == 0, status.output
        assert json.loads(status.output)["source_revision"] == "r1400"
    else:
        status = CliRunner().invoke(cli, ["server", "db-migrate", "status"])
        assert status.exit_code != 0
        assert "unknown_baseline" in status.output
    assert runner.database.read_bytes() == original
