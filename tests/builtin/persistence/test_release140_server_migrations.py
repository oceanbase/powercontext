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

"""Exercise the registered 1.4 SQLite source; installed-wheel/native gates remain separate."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest
import sqlite_vec
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.code import CodeConfig
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamRecord, DreamRun
from powercontext.builtin.inference.models import EmbeddingResult
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.pinned import finish, pinned
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class ControlledEmbedding:
    def __init__(self, profile):
        self.profile = profile

    async def embed(self, texts, /):
        vector = (1.0,) + (0.0,) * (self.profile.dimension - 1)
        return EmbeddingResult(vectors=tuple(vector for _ in texts))


def _source(tmp_path, *, release="1.4.0", vectors=False, code=False):
    database = tmp_path / "source.db"
    embedding = EmbeddingProfile(profile_id="source140", model="fixture", dimension=3) if vectors else None
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        code=CodeConfig(enabled=code, cache_dir=tmp_path / "code"),
        mcp=McpConfig(enabled=False),
    )
    runner = deployment_runner(settings, embedding_profile=embedding)
    source = server_bundle(embedding_profile=embedding, source_release="1.4.0" if release == "1.4.0" else None)
    revision = "r1400" if release == "1.4.0" else "s1300"

    def connect():
        connection = sqlite3.connect(database)
        if vectors:
            connection.enable_load_extension(True)
            try:
                sqlite_vec.load(connection)
            finally:
                connection.enable_load_extension(False)
        return connection

    engine = create_engine("sqlite://", creator=connect)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            config = source.config(connection)
            config.attributes["configuration"] = runner.configuration
            command.upgrade(config, revision)
            if source.vector_profile is not None:
                source.vector_profile.sqlite_upgrade(connection, revision)
            connection.exec_driver_sql("DROP TABLE pc_schema_revision")
            connection.exec_driver_sql(
                "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) VALUES ('scope','Source','History','scope','source','history',1)"
            )
            if release == "1.4.0":
                proposal = ExperienceContent(
                    situation="Existing", action="Preserve", outcome="Readable", lesson="History"
                )
                connection.exec_driver_sql(
                    "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) VALUES ('scope','experience','evidence',1,?)",
                    (proposal.model_dump_json().encode(),),
                )
                repository = CandidateRepository({"experience": ExperienceContent})
                options = {
                    "sources": (),
                    "artifacts": (ArtifactRef(family="experience", artifact_id="evidence", revision=1),),
                    "target": None,
                    "reason": "Original",
                }
                finish(repository.create(pinned(connection), "scope", "candidate", "experience", proposal, **options))
                finish(repository.revise(pinned(connection), "scope", "candidate", 1, proposal, **options))
                finish(repository.reject(pinned(connection), "scope", "candidate", 2, "Historical decision"))
            connection.commit()
    finally:
        engine.dispose()
    return settings, embedding, runner


def _apply(runner):
    plan = runner.plan(backup_policy="skip")
    return runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="skip",
        accept_no_backup=True,
    )


def _candidate_rows(database):
    with closing(sqlite3.connect(database)) as connection:
        return {
            name: connection.execute(f"SELECT * FROM {name} ORDER BY 1,2,3").fetchall()  # noqa: S608 -- Two fixed fixture tables.
            for name in ("pc_candidate_heads", "pc_candidate_versions")
        }


@pytest.mark.parametrize(("release", "expected"), [("1.3.0", "s1300"), ("1.4.0", "r1400")])
def test_registered_sources_choose_distinct_lineages(tmp_path, release, expected):
    _settings, _embedding, runner = _source(tmp_path, release=release)
    plan = runner.plan()
    assert plan.source_revision == expected
    assert plan.revisions == ("s1400", "s1401", "s1402", "v1_4_1_004", "v1_4_1_005", "v1_4_1_006", "v1_5_0_001")


@pytest.mark.parametrize(("vectors", "code"), [(False, False), (True, False), (False, True), (True, True)])
def test_release140_four_sqlite_configurations_preserve_candidates_and_serve(tmp_path, vectors, code):
    settings, embedding, runner = _source(tmp_path, vectors=vectors, code=code)
    before = _candidate_rows(runner.database)
    assert len(before["pc_candidate_versions"]) == 2
    _apply(runner)
    assert _candidate_rows(runner.database) == before
    reopened = deployment_runner(settings, embedding_profile=embedding)
    assert reopened.verify().state == "ready"
    assert not _apply(reopened).changed
    with closing(sqlite3.connect(runner.database)) as connection:
        for name in ("pc_retained_dream_candidate_heads", "pc_retained_dream_candidate_versions"):
            assert connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone() == (0,)  # noqa: S608 -- Fixed retained tables.
    app = create_server_app(
        settings=settings,
        embedding_model=ControlledEmbedding(embedding) if embedding is not None else None,
        scheduler_path=tmp_path / "scheduler",
    )
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        response = client.post(
            "/v1/memory/remember", json={"scope_id": "scope", "kind": "fact", "text": "New data after the 1.4 bridge."}
        )
        assert response.status_code == 200
    assert _candidate_rows(runner.database) == before


def test_release140_extra_object_blocks_before_any_write(tmp_path):
    _settings, _embedding, runner = _source(tmp_path)
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("CREATE TABLE unexpected (value TEXT)")
    before = runner.database.read_bytes()
    with pytest.raises(MigrationError, match="unknown_baseline"):
        runner.plan()
    assert runner.database.read_bytes() == before
    assert not runner.evidence_path.exists()


@pytest.mark.parametrize("operation", ["server", "processing", "atomic-projection"])
def test_release140_missing_retained_history_blocks_full_and_partial_verification(tmp_path, operation):
    settings, _embedding, runner = _source(tmp_path)
    _apply(runner)
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("DROP TABLE pc_retained_dream_candidate_versions")
    before = runner.database.read_bytes()
    with pytest.raises(MigrationError, match="recovery_required"):
        deployment_runner(settings, bundle=operation).verify()
    assert runner.database.read_bytes() == before


def test_release140_invalid_processing_manifest_blocks_before_any_write(tmp_path):
    _settings, _embedding, runner = _source(tmp_path)
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        manifest = json.loads(
            connection.execute("SELECT config_manifest FROM pc_artifact_processing_schema").fetchone()[0]
        )
        manifest["bindings"].pop("handoff.dream.v1")
        connection.execute("UPDATE pc_artifact_processing_schema SET config_manifest=?", (json.dumps(manifest),))
    before = runner.database.read_bytes()
    with pytest.raises(MigrationError, match="unknown_processing_manifest"):
        runner.plan()
    assert runner.database.read_bytes() == before
    assert not runner.evidence_path.exists()


@pytest.mark.parametrize("corrupt_identity", [False, True])
def test_release140_current_dream_tasks_keep_their_format_or_fail_before_writes(tmp_path, corrupt_identity):
    _settings, _embedding, runner = _source(tmp_path)
    record = DreamRecord(
        run=DreamRun(
            scope_id="scope",
            run_id="current-task",
            operation="refine_experience",
            status="queued",
            accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        request=CreateDreamRunRequest(
            operation="refine_experience",
            artifacts=(ArtifactRef(family="experience", artifact_id="evidence", revision=1),),
            idempotency_key="current-task",
        ),
        principal_id="source-owner",
    )
    engine = create_engine(f"sqlite:///{runner.database}")
    try:
        with engine.begin() as connection:
            finish(DreamRepository().create(pinned(connection), record))
            if corrupt_identity:
                connection.exec_driver_sql("UPDATE pc_dream_runs SET principal_key='mismatched'")
            before_payload = connection.exec_driver_sql("SELECT payload FROM pc_dream_runs").scalar_one()
    finally:
        engine.dispose()
    if corrupt_identity:
        before = runner.database.read_bytes()
        with pytest.raises(MigrationError, match="unsupported_task_format"):
            runner.plan()
        assert runner.database.read_bytes() == before
        assert not runner.evidence_path.exists()
    else:
        _apply(runner)
        assert runner.verify().state == "ready"
        with closing(sqlite3.connect(runner.database)) as connection:
            assert connection.execute("SELECT status,payload FROM pc_dream_runs").fetchone() == (
                "queued",
                before_payload,
            )
