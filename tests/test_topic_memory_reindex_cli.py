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

"""Real SQLite wiring for the Topic Memory reindex maintenance command.

Every fixture here is initialized by the registered Server migration runner, so
the maintenance journal and revision checksums that Server startup uses are part
of the exercised state. Hand-built tables hid the admission boundary these tests
protect.
"""

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
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.server_readiness import check_server_schema
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.topic_memory_index import topic_memory_embedding_profile_fingerprint
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from tests.builtin.persistence.test_topic_memory_reindex import _Embedding, _profile, _publish


def _managed_database(
    database: Path,
    profile: EmbeddingProfile | None,
    *,
    count: int = 2,
) -> SQLiteMigrationRunner:
    """Initialize the registered Server schema and publish authoritative Topics."""

    bundle = server_bundle(embedding_profile=profile)
    configuration: dict[str, object] = {
        "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
        "embedding_enabled": profile is not None,
    }
    if profile is not None:
        configuration["embedding_profile"] = profile.model_dump(mode="json")
    runner = SQLiteMigrationRunner(database, bundle, configuration=configuration)
    plan = runner.plan()
    runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)

    async def publish() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}", journal_mode="DELETE")
        async with SQLiteProfile.open(config, tables=(), load_vector_extension=True) as opened:
            await _publish(opened, profile or _profile("old", 2), count=count, vectorized=profile is not None)

    asyncio.run(publish())
    return runner


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


def _active_shape(database: Path) -> tuple[str, str | None]:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT shape, profile_fingerprint FROM pc_topic_memory_retrieval_shape").fetchone()
        assert row is not None
        return str(row[0]), None if row[1] is None else str(row[1])


def _active_fingerprint(database: Path) -> str:
    shape, fingerprint = _active_shape(database)
    assert shape == "hybrid" and fingerprint is not None
    return fingerprint


def _staging_tables(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pc_topic_memory_reindex%'"
            )
        ]


def _journal(database: Path) -> dict[str, object]:
    path = database.with_name(database.name + ".pc-migration-state") / "maintenance.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _require_restart_admission(database: Path) -> None:
    """Run the same admission Server startup uses against a reopened database."""

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}", journal_mode="DELETE")
        async with (
            SQLiteProfile.open(config, tables=(), load_vector_extension=True) as opened,
            opened.database.transaction() as connection,
        ):
            await connection.run_sync(lambda sync: check_server_schema(sync, _startup=True))

    asyncio.run(scenario())


def test_cli_dry_run_skips_provider_and_preserves_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "dry-run.db"
    old_profile, new_profile = _profile("old", 2), _profile("new", 3)
    _managed_database(database, old_profile, count=1)
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
    assert _authority(database) == before
    assert _active_fingerprint(database) == topic_memory_embedding_profile_fingerprint(old_profile)
    assert _staging_tables(database) == []


def test_cli_apply_preserves_history_for_restart_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "apply.db"
    old_profile, new_profile = _profile("old", 2), _profile("new", 3)
    _managed_database(database, old_profile, count=2)
    before = _authority(database)
    recorded = dict(_journal(database)["release_checksums"])  # type: ignore[arg-type]
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

    # The managed profile switch keeps the superseded release evidence while the
    # recorded checksums follow the target declaration, so a restarted Server and
    # a repeated identical apply both admit the storage this command activated.
    journal = _journal(database)
    assert journal["state"] == "complete"
    assert journal["release_checksums"] == server_bundle(embedding_profile=new_profile).checksums
    assert journal["superseded_release_checksums"] == [recorded]
    _require_restart_admission(database)

    async def refuse_provider(*_args, **_kwargs):
        raise AssertionError("an activated apply must not initialize the embedding provider")  # noqa: TRY003

    monkeypatch.setattr("powercontext.server.cli._embedding_models", refuse_provider)
    repeated = CliRunner().invoke(create_cli([app]), command)
    assert repeated.exit_code == 0, repeated.exception or repeated.output
    repeated_report = json.loads(repeated.stdout)
    assert repeated_report["ready"] is True and repeated_report["activated"] is True
    assert repeated_report["embedded_vectors"] == 0
    assert _journal(database)["superseded_release_checksums"] == [recorded]
    _require_restart_admission(database)


def test_cli_apply_rejects_fts_only_managed_storage_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "fts-only.db"
    new_profile = _profile("new", 3)
    _managed_database(database, None, count=1)
    assert _active_shape(database) == ("fts", None)
    before = _authority(database)
    with closing(sqlite3.connect(database)) as connection:
        inventory = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pc_topic_memory_vector_topics" not in inventory
    assert "pc_server_storage_profile" not in inventory
    _configure_target(monkeypatch, database, new_profile)

    async def refuse_provider(*_args, **_kwargs):
        raise AssertionError("a rejected configuration must not initialize the provider")  # noqa: TRY003

    monkeypatch.setattr("powercontext.server.cli._embedding_models", refuse_provider)
    command = [
        "server",
        "topic-memory-reindex",
        "--action",
        "apply",
        "--maintenance-confirmed",
    ]
    applied = CliRunner().invoke(create_cli([app]), command)

    assert applied.exit_code == 1, applied.exception or applied.output
    report = json.loads(applied.stdout)
    assert report["ready"] is False and report["activated"] is False
    assert "no shared embedding declaration" in report["errors"][0]

    with closing(sqlite3.connect(database)) as connection:
        current = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert current == inventory
    assert _active_shape(database) == ("fts", None)
    assert _authority(database) == before
    assert _staging_tables(database) == []
    _require_restart_admission(database)


def test_cli_apply_resumes_after_a_committed_atomic_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "resume.db"
    old_profile, new_profile = _profile("old", 2), _profile("new", 3)
    _managed_database(database, old_profile, count=1)
    before = _authority(database)

    # Reproduce the observed interruption: the Atomic rebuild commits its target
    # generation, then Topic activation fails and rolls back, so the declaration
    # still names the previous profile.
    async def interrupt() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}", journal_mode="DELETE")
        async with (
            SQLiteProfile.open(config, tables=(), load_vector_extension=True) as opened,
            opened.database.transaction() as connection,
        ):
            await SQLiteAtomicMemoryIndex(new_profile).initialize(connection)

    asyncio.run(interrupt())
    with pytest.raises(MigrationError, match="incompatible_schema"):
        _require_restart_admission(database)

    _configure_target(monkeypatch, database, new_profile)
    model = _Embedding(new_profile)

    async def models(*_args, **_kwargs):
        return model, model

    monkeypatch.setattr("powercontext.server.cli._embedding_models", models)
    resumed = CliRunner().invoke(
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

    assert resumed.exit_code == 0, resumed.exception or resumed.output
    report = json.loads(resumed.stdout)
    assert report["ready"] is True and report["activated"] is True
    assert _authority(database) == before
    assert _active_fingerprint(database) == topic_memory_embedding_profile_fingerprint(new_profile)
    with closing(sqlite3.connect(database)) as connection:
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='pc_atomic_memory_current_vec'"
        ).fetchone()
        declared = connection.execute("SELECT embedding_profile FROM pc_server_storage_profile").fetchone()
    assert definition is not None and f"float[{new_profile.dimension}]" in definition[0]
    assert declared is not None
    assert EmbeddingProfile.model_validate_json(declared[0]) == new_profile
    _require_restart_admission(database)
