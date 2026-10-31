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

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from powercontext.cli.env_file import parse_environment
from powercontext.ops.local_policy import (
    classify_database,
    default_data_dir,
    inspect_existing_configuration,
    new_configuration,
    preview_new_configuration,
)
from powercontext.ops.protocol import OpsError
from powercontext.server.configuration import server_settings_context
from powercontext.service.environment import write_protected_file


def test_new_configuration_ignores_parent_server_settings_and_does_not_create_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_default = default_data_dir()
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "inherited-home"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "inherited-imports"))
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_ENABLED", "true")
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_TOKEN", "inherited-secret")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", "inherited:model")
    assert default_data_dir() == expected_default
    root = tmp_path / "new-data"
    content = new_configuration(root, 8999)
    summary = preview_new_configuration(root, 8999)
    values = parse_environment(content.decode("utf-8"))
    with server_settings_context(environment=values, data_dir=root) as settings:
        assert settings.http.host == "127.0.0.1"
        assert settings.http.port == 8999
        assert settings.database.kind == "sqlite"
        assert settings.dashboard.enabled
        assert settings.mcp.enabled
        assert settings.access.mode == "disabled"
        assert settings.auth.enabled is False
        assert settings.auth.token is None
        assert settings.access.background_principal_id is None
        assert settings.inference.generation_model is None
        assert settings.inference.embedding_model is None
    assert summary.identity is None
    assert summary.endpoint == "http://127.0.0.1:8999"
    assert summary.database_path == root / "powercontext.db"
    assert summary.local_mode == "tokenless"
    assert classify_database(summary, "exact-server-version").classification == "new"
    assert not root.exists()
    assert os.environ["POWERCONTEXT_HOME"] == str(tmp_path / "inherited-home")
    assert os.environ["PYTHONPATH"] == str(tmp_path / "inherited-imports")
    assert b"inherited" not in content


def test_existing_configuration_preserves_explicit_settings_and_redacts_secrets(tmp_path: Path) -> None:
    root = tmp_path / "existing-data"
    content = new_configuration(root, 8991).replace(
        b"POWERCONTEXT_SERVER_AUTH_ENABLED=false", b"POWERCONTEXT_SERVER_AUTH_ENABLED=true"
    )
    content += b"POWERCONTEXT_SERVER_AUTH_TOKEN='private credential'\nPOWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL='chosen:model'\n"
    path = tmp_path / "config" / "server.env"
    write_protected_file(path, content)
    before = path.read_bytes()
    summary = inspect_existing_configuration(path)
    assert summary.identity is not None
    assert summary.data_dir == root
    assert summary.database_path == root / "powercontext.db"
    assert summary.endpoint == "http://127.0.0.1:8991"
    assert summary.local_mode == "authenticated"
    assert "private credential" not in json.dumps(summary.as_dict())
    assert "chosen:model" not in json.dumps(summary.as_dict())
    assert path.read_bytes() == before
    assert not root.exists()


def test_existing_configuration_uses_registered_data_root_without_rewriting_file(tmp_path: Path) -> None:
    path = tmp_path / "config" / "server.env"
    write_protected_file(path, b"POWERCONTEXT_SERVER_HTTP_PORT=8992\n")
    registered_root = tmp_path / "registered-data"
    summary = inspect_existing_configuration(path, data_dir=registered_root)
    assert summary.data_dir == registered_root
    assert summary.database_path == registered_root / "powercontext.db"
    assert path.read_bytes() == b"POWERCONTEXT_SERVER_HTTP_PORT=8992\n"


def test_malformed_database_url_returns_stable_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "config" / "server.env"
    content = b"POWERCONTEXT_SERVER_DATABASE_URL=bad\n"
    write_protected_file(path, content)
    with pytest.raises(OpsError, match="configuration_invalid"):
        inspect_existing_configuration(path)
    assert path.read_bytes() == content


@pytest.mark.parametrize(
    "url",
    ["sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///relative.db", "sqlite+aiosqlite:///file:db?uri=true&mode=ro"],
)
def test_database_locations_are_not_guessed_from_unsupported_urls(tmp_path: Path, url: str) -> None:
    path = tmp_path / "config" / "server.env"
    write_protected_file(path, f"POWERCONTEXT_SERVER_DATABASE_URL='{url}'\n".encode())
    with pytest.raises(OpsError, match=r"database_location_unsupported|configuration_path_unsupported"):
        inspect_existing_configuration(path)


@pytest.mark.parametrize("marker", [None, "pc_schema_revision", "pc_artifact_processing_intents", "pc_candidate_heads"])
def test_existing_sqlite_and_partial_migration_markers_never_prove_compatibility(
    tmp_path: Path, marker: str | None
) -> None:
    summary = preview_new_configuration(tmp_path, 8993)
    with closing(sqlite3.connect(summary.database_path)) as connection:
        connection.execute("CREATE TABLE preserved (value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved VALUES (?)", ("user memory",))
        if marker is not None:
            connection.execute(f'CREATE TABLE "{marker}" (state TEXT)')
            connection.execute(f'INSERT INTO "{marker}" VALUES (?)', ("ready",))  # noqa: S608
        connection.commit()
    before = summary.database_path.read_bytes()
    observed = classify_database(summary, "server-not-ops-version")
    assert observed.classification == "existing_unknown"
    assert not observed.allowed_to_start
    assert observed.reason == "whole_database_compatibility_unavailable"
    assert summary.database_path.read_bytes() == before
    with closing(sqlite3.connect(summary.database_path)) as connection:
        assert connection.execute("SELECT value FROM preserved").fetchall() == [("user memory",)]
    assert classify_database(summary, "another-server-version").fingerprint != observed.fingerprint


def test_existing_empty_file_is_not_a_new_database(tmp_path: Path) -> None:
    summary = preview_new_configuration(tmp_path, 8994)
    summary.database_path.touch()
    observed = classify_database(summary, "target")
    assert observed.classification == "existing_unknown"
    assert not observed.allowed_to_start
    assert summary.database_path.stat().st_size == 0


def test_wal_database_inventory_does_not_open_sqlite_or_checkpoint_user_data(tmp_path: Path) -> None:
    summary = preview_new_configuration(tmp_path, 8995)
    with closing(sqlite3.connect(summary.database_path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE preserved (value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('committed in WAL')")
        connection.commit()
        paths = [Path(f"{summary.database_path}{suffix}") for suffix in ("", "-wal", "-shm")]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        observed = classify_database(summary, "target")
        assert observed.classification == "existing_unknown"
        assert not observed.allowed_to_start
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
        assert connection.execute("SELECT value FROM preserved").fetchall() == [("committed in WAL",)]


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_orphan_sidecars_block_new_database_classification(tmp_path: Path, suffix: str) -> None:
    summary = preview_new_configuration(tmp_path, 8996)
    Path(f"{summary.database_path}{suffix}").write_bytes(b"existing recovery evidence")
    observed = classify_database(summary, "target")
    assert observed.classification == "unknown"
    assert not observed.allowed_to_start
    assert not summary.database_path.exists()


def test_non_sqlite_content_is_preserved_and_blocks_start(tmp_path: Path) -> None:
    summary = preview_new_configuration(tmp_path, 8997)
    summary.database_path.write_bytes(b"not an sqlite database")
    observed = classify_database(summary, "target")
    assert observed.classification == "incompatible"
    assert not observed.allowed_to_start
    assert summary.database_path.read_bytes() == b"not an sqlite database"


def test_database_creation_invalidates_new_database_fingerprint(tmp_path: Path) -> None:
    summary = preview_new_configuration(tmp_path, 8998)
    before = classify_database(summary, "target")
    summary.database_path.touch()
    after = classify_database(summary, "target")
    assert before.allowed_to_start
    assert not after.allowed_to_start
    assert before.fingerprint != after.fingerprint


@pytest.mark.parametrize("journal_mode", ["WAL", "DELETE"])
def test_repair_observation_allows_database_writes_but_detects_replacement(tmp_path: Path, journal_mode: str) -> None:
    summary = preview_new_configuration(tmp_path, 8999)
    with closing(sqlite3.connect(summary.database_path)) as connection:
        connection.execute(f"PRAGMA journal_mode={journal_mode}")
        connection.execute("CREATE TABLE preserved (value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('before repair plan')")
        connection.commit()
        strict = classify_database(summary, "target")
        planned = classify_database(summary, "target", for_repair=True)
        connection.execute("INSERT INTO preserved VALUES (?)", ("committed while preparing repair " * 1024,))
        connection.commit()
        assert classify_database(summary, "target", for_repair=True) == planned
        assert classify_database(summary, "target").fingerprint != strict.fingerprint
        if journal_mode == "WAL":
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            assert classify_database(summary, "target", for_repair=True) == planned
        assert connection.execute("SELECT count(*) FROM preserved").fetchone() == (2,)
    # Closing the last WAL connection removes sidecars, while retaining the
    # same database identity that a program-only repair must preserve.
    assert classify_database(summary, "target", for_repair=True) == planned
    assert not planned.allowed_to_start
    assert classify_database(summary, "another-target", for_repair=True).fingerprint != planned.fingerprint
    replacement = tmp_path / "replacement.db"
    replacement.write_bytes(summary.database_path.read_bytes())
    os.replace(replacement, summary.database_path)
    assert classify_database(summary, "target", for_repair=True).fingerprint != planned.fingerprint
