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

"""Existing managed lexical projections upgrade without rewriting evidence or vectors."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
import sqlite_vec
from pydantic import JsonValue
from typer.testing import CliRunner

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference.models import EmbeddingResult
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from powercontext.server.settings import ServerSettings


class ControlledEmbedding:
    profile = EmbeddingProfile(profile_id="lexical-upgrade", model="fixture", dimension=3)

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


def connect(path):
    connection = sqlite3.connect(path)
    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)
    return connection


def environment_file(path):
    environment = path.parent / "server.env"
    environment.write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{path}\n"
    )
    environment.chmod(0o600)
    return environment


def seed(path, *, vectors=False):
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
    model = ControlledEmbedding() if vectors else None
    profile = None if model is None else model.profile
    runner = deployment_runner(settings, embedding_profile=profile)
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)

    async def write():
        async with open_builtin_runtime(
            BuiltinConfig(database=settings.database),
            embedding_model=model,
            scheduler_path=path.parent / "seed-scheduler",
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Lexical upgrade", summary="Preserve evidence", idempotency_key="lexical-upgrade")
            )
            contents: dict[str, dict[str, JsonValue]] = {
                "atomic-memory": {"kind": "fact", "text": "the www deploymentmarker"},
                "topic-memory": {
                    "title": "deploymentmarker",
                    "summary": "the www",
                    "detail": "the www durable evidence",
                },
                "experience": {
                    "situation": "the www",
                    "action": "Keep evidence",
                    "outcome": "Available",
                    "lesson": "Retain words",
                },
                "skill": {
                    "name": "lexical-evidence",
                    "description": "the www",
                    "instructions": "Read evidence",
                    "validation": ["Retrieve evidence"],
                },
            }
            refs = {
                family: await runtime.records.for_scope(scope.scope_id).create_artifact(
                    family, ArtifactWrite(content=content)
                )
                for family, content in contents.items()
            }
            return scope.scope_id, refs

    scope_id, references = asyncio.run(write())
    with closing(connect(path)) as connection, connection:
        # Reconstruct the preceding persisted analyzer representation, including
        # its version markers, while retaining the exact public artifacts.
        for table in (
            "pc_atomic_memory_current",
            "pc_topic_memory_active_topics",
            "pc_topic_memory_active_chunks",
            "pc_artifact_heads",
        ):
            rows = connection.execute(f"SELECT rowid,searchable_text FROM {table}").fetchall()  # noqa: S608 -- Fixed fixture table names.
            for rowid, text in rows:
                if text is not None:
                    legacy = " ".join(
                        bytes.fromhex(term[3:]).decode() if term.startswith("pcu") else term for term in text.split()
                    )
                    connection.execute(f"UPDATE {table} SET searchable_text=? WHERE rowid=?", (legacy, rowid))  # noqa: S608 -- Fixed fixture table names.
        connection.execute(
            "UPDATE pc_lexical_projection_versions SET analyzer_version=replace(analyzer_version,'words-v2','words-v1')"
        )
    return settings, model, scope_id, references


def preserved(path, *, vectors=False):
    with closing(connect(path)) as connection:
        rows = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1,2,3").fetchall()  # noqa: S608 -- Fixed fixture table names.
            for table in ("pc_artifacts", "pc_artifact_tags", "pc_artifact_lineage_artifacts", "pc_access_owners")
        }
        rows["revision"] = connection.execute("SELECT version_num FROM pc_schema_revision").fetchall()
        if vectors:
            rows["atomic_vectors"] = connection.execute(
                "SELECT scope_id,artifact_id,embedding,embedding_input_hash,profile_fingerprint FROM pc_atomic_memory_current"
            ).fetchall()
            for table in ("pc_atomic_memory_current_vec", "pc_topic_memory_topic_vec", "pc_topic_memory_chunk_vec"):
                rows[table] = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()  # noqa: S608 -- Fixed fixture table names.
        return rows


@pytest.mark.parametrize("vectors", [False, True])
def test_explicit_lexical_upgrade_preserves_authority_vectors_and_search(tmp_path, vectors):
    path = tmp_path / "managed.db"
    settings, model, scope, refs = seed(path, vectors=vectors)
    profile = None if model is None else model.profile
    before = preserved(path, vectors=vectors)
    original = path.read_bytes()
    with pytest.raises(MigrationError, match="projection_migration_required"):
        deployment_runner(settings, embedding_profile=profile).verify()
    runner = deployment_runner(settings, bundle="lexical-projection", embedding_profile=profile)
    plan = runner.plan()
    assert plan.state == "migration_required" and plan.revisions == ()
    assert path.read_bytes() == original
    result = runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert result.backup_state == "completed"
    assert result.backup_ref is not None
    assert preserved(Path(result.backup_ref), vectors=vectors) == before == preserved(path, vectors=vectors)
    assert deployment_runner(settings, embedding_profile=profile).verify().state == "ready"
    after = path.read_bytes()
    assert runner.verify().state == "ready"
    assert not runner.apply().changed and path.read_bytes() == after

    async def search():
        async with open_builtin_runtime(
            BuiltinConfig(database=settings.database), embedding_model=model, scheduler_path=tmp_path / "scheduler"
        ) as runtime:
            for family, reference in refs.items():
                found = await runtime.artifacts.for_scope(scope).search(family, {"query": '"the"', "mode": "text"})
                assert [match.artifact_ref.artifact_id for match in found.matches] == [reference.artifact_id]

    asyncio.run(search())


def test_lexical_upgrade_cli_rejects_unknown_analyzer_without_writes(tmp_path):
    path = tmp_path / "managed.db"
    seed(path)
    with closing(connect(path)) as connection, connection:
        connection.execute("UPDATE pc_lexical_projection_versions SET analyzer_version='unknown'")
    original = path.read_bytes()
    result = CliRunner().invoke(
        create_cli([app]),
        ["server", "db-migrate", "plan", "--bundle", "lexical-projection", "--env-file", str(environment_file(path))],
    )
    assert result.exit_code != 0 and json.loads(result.output)["error"] == "unsupported_analyzer_version"
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.pc-migration-state/lexical_projection/*/maintenance.json"))


def test_lexical_upgrade_rejects_embedding_configuration_drift(tmp_path):
    path = tmp_path / "managed.db"
    settings, model, _scope, _refs = seed(path, vectors=True)
    assert model is not None
    original = path.read_bytes()
    for profile in (None, model.profile.model_copy(update={"model": "different-model"})):
        runner = deployment_runner(settings, bundle="lexical-projection", embedding_profile=profile)
        with pytest.raises(MigrationError, match="embedding_profile_mismatch"):
            runner.plan()
        assert path.read_bytes() == original
    assert not list(path.parent.glob("*.pc-migration-state/lexical_projection/*/maintenance.json"))


def test_interrupted_lexical_upgrade_rolls_back_and_reuses_backup(tmp_path):
    path = tmp_path / "managed.db"
    settings, _model, _scope, _refs = seed(path)
    runner = deployment_runner(settings, bundle="lexical-projection")
    plan = runner.plan()
    before = preserved(path)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    environment.update(
        POWERCONTEXT_SERVER_DATABASE_KIND="sqlite",
        POWERCONTEXT_SERVER_DATABASE_URL=f"sqlite+aiosqlite:///{path}",
    )
    interrupted = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from sqlalchemy import Engine, event
from powercontext.cli.app import main
@event.listens_for(Engine, "after_cursor_execute")
def interrupt(connection, cursor, statement, parameters, context, executemany):
    if statement.startswith("UPDATE pc_atomic_memory_current SET searchable_text"):
        os._exit(73)
main()
""",
            "server",
            "db-migrate",
            "apply",
            "--bundle",
            "lexical-projection",
            "--env-file",
            str(environment_file(path)),
            "--plan-id",
            plan.plan_id,
            "--backup",
            "auto",
            "--yes",
            "--maintenance-confirmed",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert interrupted.returncode == 73, interrupted.stdout + interrupted.stderr
    journal = next(path.parent.glob("*.pc-migration-state/lexical_projection/*/maintenance.json"))
    active = json.loads(journal.read_text())
    assert active["state"] == "active" and preserved(path) == before
    recovery = runner.plan()
    assert recovery.state == "recovery_required"
    result = runner.apply(plan_id=recovery.plan_id, accepted=True, maintenance_confirmed=True)
    complete = json.loads(journal.read_text())
    assert complete["state"] == "complete"
    assert complete["maintenance_window_id"] == active["maintenance_window_id"]
    assert result.backup_ref == active["backup"]["location"]
    assert preserved(path) == before
    assert deployment_runner(settings).verify().state == "ready"
