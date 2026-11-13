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

"""Real SQLite wiring for the Topic Memory reindex maintenance command."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from click import unstyle
from typer.testing import CliRunner

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.persistence.topic_memory_index import topic_memory_embedding_profile_fingerprint
from powercontext.cli.app import create_cli
from powercontext.server.authz.repository import ACCESS_TABLES
from powercontext.server.cli import app
from tests.builtin.persistence.test_topic_memory_reindex import _Embedding, _index, _profile, _publish


def _seed(database: Path, profile: EmbeddingProfile, *, count: int, managed: bool = False) -> None:
    async def scenario() -> None:
        index = _index(profile)
        atomic_index = SQLiteAtomicMemoryIndex(profile) if managed else None
        tables = BUILTIN_TABLES + index.tables
        if atomic_index is not None:
            tables += ACCESS_TABLES + atomic_index.tables
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}", journal_mode="DELETE")
        async with SQLiteProfile.open(
            config,
            tables=tables,
            load_vector_extension=True,
        ) as opened:
            await _publish(opened, profile, count=count)
            if atomic_index is not None:
                async with opened.database.transaction() as connection:
                    await connection.exec_driver_sql(
                        "CREATE TABLE pc_server_storage_profile ("
                        "singleton INTEGER NOT NULL PRIMARY KEY CHECK (singleton=1), "
                        "embedding_profile TEXT NOT NULL)"
                    )
                    await connection.exec_driver_sql(
                        "INSERT INTO pc_server_storage_profile (singleton, embedding_profile) VALUES (1, ?)",
                        (profile.model_dump_json(),),
                    )
                    await atomic_index.initialize(connection)

    asyncio.run(scenario())


def _configure_target(monkeypatch: pytest.MonkeyPatch, database: Path, profile: EmbeddingProfile) -> None:
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_JOURNAL_MODE", "DELETE")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", profile.model)
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID", profile.profile_id)
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION", str(profile.dimension))
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION", profile.normalization)


def _authority(database: Path) -> dict[str, list[tuple[object, ...]]]:
    statements = {
        "artifacts": "SELECT * FROM pc_artifacts ORDER BY scope_id, family, artifact_id, revision",
        "topics": "SELECT * FROM pc_topic_memory_active_topics ORDER BY scope_id, family, artifact_id",
        "chunks": ("SELECT * FROM pc_topic_memory_active_chunks ORDER BY scope_id, family, artifact_id, chunk_ordinal"),
    }
    with closing(sqlite3.connect(database)) as connection:
        return {name: [tuple(row) for row in connection.execute(statement)] for name, statement in statements.items()}


def _active_fingerprint(database: Path) -> str:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT profile_fingerprint FROM pc_topic_memory_retrieval_shape").fetchone()
        assert row is not None
        return str(row[0])


def _staging_tables(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pc_topic_memory_reindex%'"
            )
        ]


def test_cli_dry_run_skips_provider_and_preserves_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "dry-run.db"
    old_profile, new_profile = _profile("old", 2), _profile("new", 3)
    _seed(database, old_profile, count=1)
    original = database.read_bytes()
    before = _authority(database)
    _configure_target(monkeypatch, database, new_profile)

    async def refuse_provider(*_args, **_kwargs):
        raise AssertionError("dry-run must not initialize the embedding provider")  # noqa: TRY003

    monkeypatch.setattr("powercontext.server.cli._embedding_models", refuse_provider)
    cli = create_cli([app])
    runner = CliRunner()

    unconfirmed = runner.invoke(cli, ["server", "topic-memory-reindex", "--action", "apply"])
    assert unconfirmed.exit_code == 2
    assert "--maintenance-confirmed" in unstyle(unconfirmed.output)

    result = runner.invoke(cli, ["server", "topic-memory-reindex"])

    assert result.exit_code == 0, result.exception or result.output
    report = json.loads(result.stdout)
    assert report["action"] == "dry-run"
    assert report["ready"] is True and report["activated"] is False
    assert report["counts"]["topics_total"] == 1
    assert report["counts"]["topics_remaining"] == 1
    assert database.read_bytes() == original
    assert _authority(database) == before
    assert _active_fingerprint(database) == topic_memory_embedding_profile_fingerprint(old_profile)
    assert _staging_tables(database) == []


def test_cli_apply_uses_configured_model_and_reports_committed_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "apply.db"
    old_profile, new_profile = _profile("old", 2), _profile("new", 3)
    _seed(database, old_profile, count=2)
    before = _authority(database)
    _configure_target(monkeypatch, database, new_profile)
    model = _Embedding(new_profile)
    provider_calls = []

    async def models(*_args, **_kwargs):
        provider_calls.append(True)
        return model, model

    monkeypatch.setattr("powercontext.server.cli._embedding_models", models)
    command = [
        "server",
        "topic-memory-reindex",
        "--action",
        "apply",
        "--maintenance-confirmed",
        "--batch-size",
        "1",
    ]
    result = CliRunner().invoke(create_cli([app]), command)

    assert result.exit_code == 0, result.exception or result.output
    report = json.loads(result.stdout)
    assert report["action"] == "apply"
    assert report["ready"] is True and report["activated"] is True
    assert report["embedded_vectors"] == report["counts"]["topics_total"] + report["counts"]["chunks_total"]
    assert report["counts"]["topics_remaining"] == report["counts"]["chunks_remaining"] == 0
    assert provider_calls == [True]
    assert len(model.calls) == 2
    progress = [json.loads(line)["progress"] for line in result.stderr.splitlines() if line.startswith('{"progress"')]
    assert len(progress) >= 2
    assert progress[-1]["topics_remaining"] == progress[-1]["chunks_remaining"] == 0
    assert _authority(database) == before
    assert _active_fingerprint(database) == topic_memory_embedding_profile_fingerprint(new_profile)
    assert _staging_tables(database) == []

    async def refuse_provider(*_args, **_kwargs):
        raise AssertionError("an activated apply must not initialize the embedding provider")  # noqa: TRY003

    monkeypatch.setattr("powercontext.server.cli._embedding_models", refuse_provider)
    repeated = CliRunner().invoke(create_cli([app]), command)
    assert repeated.exit_code == 0, repeated.exception or repeated.output
    repeated_report = json.loads(repeated.stdout)
    assert repeated_report["ready"] is True and repeated_report["activated"] is True
    assert repeated_report["embedded_vectors"] == 0


def test_cli_apply_accepts_explicit_atomic_rebuild_for_managed_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "managed-apply.db"
    old_profile, new_profile = _profile("old", 2), _profile("new", 3)
    _seed(database, old_profile, count=1, managed=True)
    before = _authority(database)
    _configure_target(monkeypatch, database, new_profile)
    model = _Embedding(new_profile)

    async def models(*_args, **_kwargs):
        return model, model

    monkeypatch.setattr("powercontext.server.cli._embedding_models", models)
    result = CliRunner().invoke(
        create_cli([app]),
        [
            "server",
            "topic-memory-reindex",
            "--action",
            "apply",
            "--maintenance-confirmed",
            "--rebuild-atomic-memory",
        ],
    )

    assert result.exit_code == 0, result.exception or result.output
    report = json.loads(result.stdout)
    assert report["ready"] is True and report["activated"] is True
    assert report["atomic_memory_rebuild_required"] is False
    assert report["atomic_memory_rebuilt_rows"] == 0
    assert _authority(database) == before
    with closing(sqlite3.connect(database)) as connection:
        declared = connection.execute("SELECT embedding_profile FROM pc_server_storage_profile").fetchone()
        assert declared is not None
        assert EmbeddingProfile.model_validate_json(declared[0]) == new_profile
