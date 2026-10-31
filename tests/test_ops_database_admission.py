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

"""Actual bounded admission subprocesses and immutable source files, without native services."""

from __future__ import annotations

import os
import shlex
import signal
import sys
import time
from contextlib import suppress
from importlib.metadata import version
from pathlib import Path

import pytest

from powercontext.builtin.persistence.migrations.locking import local_migration_lock
from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage
from powercontext.ops import database_admission
from powercontext.ops.database_admission import admit_database
from powercontext.ops.local_policy import inspect_existing_configuration, new_configuration, preview_new_configuration
from powercontext.server.configuration import server_settings_context
from powercontext.service.environment import write_protected_file


@pytest.fixture
def database(tmp_path):
    data = tmp_path / "data"
    environment = tmp_path / "configuration" / "server.env"
    write_protected_file(environment, new_configuration(data))
    with server_settings_context(env_file=environment, data_dir=data) as settings:
        prepare_server_storage(settings)
    return inspect_existing_configuration(environment, data_dir=data)


def test_absent_database_is_not_created_or_admitted_by_a_fallback_runtime(tmp_path):
    summary = preview_new_configuration(tmp_path / "absent")
    result = admit_database(summary, "1.0.0", python_executable=tmp_path / "missing-python")
    assert result.allowed_to_start and result.classification == "new"
    assert not summary.data_dir.exists()


def test_target_metadata_version_must_match_the_selected_resource(database):
    before = database.database_path.read_bytes()
    result = admit_database(database, "0.0.0", python_executable=Path(sys.executable))
    assert not result.allowed_to_start
    assert result.reason == "database_admission_unavailable"
    assert database.database_path.read_bytes() == before


def test_active_maintenance_blocks_target_admission(database):
    with local_migration_lock(database.database_path):
        result = admit_database(database, version("powercontext"), python_executable=Path(sys.executable))
    assert not result.allowed_to_start
    assert result.reason == "database_recovery_required"


def test_snapshot_budget_rejects_before_copying(database, monkeypatch):
    monkeypatch.setattr(database_admission, "MAX_SNAPSHOT_BYTES", database.database_path.stat().st_size - 1)
    before = database.database_path.read_bytes()
    result = admit_database(database, version("powercontext"), python_executable=Path(sys.executable))
    assert not result.allowed_to_start and result.reason == "database_snapshot_too_large"
    assert database.database_path.read_bytes() == before


def test_source_change_during_copy_cannot_be_certified(database, monkeypatch):
    original = database_admission._copy

    def changing(source, destination, expected, deadline):
        result = original(source, destination, expected, deadline)
        with source.open("ab") as output:
            output.write(b"concurrent write")
        return result

    monkeypatch.setattr(database_admission, "_copy", changing)
    result = admit_database(database, version("powercontext"), python_executable=Path(sys.executable))
    assert not result.allowed_to_start and result.reason == "database_changed_during_admission"


def test_disappearing_observed_source_requires_a_fresh_admission(database, monkeypatch):
    original = database_admission._copy

    def disappearing(source, destination, expected, deadline):
        source.unlink()
        return original(source, destination, expected, deadline)

    monkeypatch.setattr(database_admission, "_copy", disappearing)
    result = admit_database(database, version("powercontext"), python_executable=Path(sys.executable))
    assert not result.allowed_to_start and result.reason == "database_changed_during_admission"
    assert not database.database_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX failure fixture delegates to the current test interpreter")
@pytest.mark.parametrize(
    "failure", ["missing", "malformed", "overflow", "timeout", "descendant_exit", "descendant_timeout"]
)
def test_target_failures_and_inherited_output_remain_bounded(database, tmp_path, monkeypatch, failure):
    marker = tmp_path / "descendant.pid"
    statements = []
    if failure.startswith("descendant"):
        statements.append(
            "import pathlib, subprocess, sys; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))"
        )
    if failure == "overflow":
        statements.append("import os; os.write(1, b'x' * 65536)")
    if failure == "malformed":
        statements.append("import json; print(json.dumps({'protocol_version': 1, 'state': [], 'reason': 'bad'}))")
    if failure in {"timeout", "descendant_timeout", "overflow"}:
        statements.append("import time; time.sleep(30)")
    else:
        statements.append(f"raise SystemExit({0 if failure == 'malformed' else 1})")
    wrapper = tmp_path / "target-python"
    wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -B -c {shlex.quote('; '.join(statements))}\n")
    wrapper.chmod(0o700)
    monkeypatch.setattr(database_admission, "ADMISSION_TIMEOUT_SECONDS", 5)
    before = database.database_path.read_bytes()
    started = time.monotonic()
    try:
        result = admit_database(database, version("powercontext"), python_executable=wrapper)
        assert time.monotonic() - started < 10
        assert not result.allowed_to_start
        assert result.reason == (
            "database_admission_timeout"
            if failure in {"timeout", "descendant_timeout"}
            else "database_admission_unavailable"
        )
        assert database.database_path.read_bytes() == before
    finally:
        if marker.exists():
            with suppress(ProcessLookupError):
                os.kill(int(marker.read_text()), signal.SIGTERM)


def test_supported_old_server_layout_requires_explicit_migration(database):
    import shutil

    from alembic import command
    from sqlalchemy import create_engine

    from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle

    database.database_path.unlink()
    shutil.rmtree(Path(f"{database.database_path}.pc-migration-state"))
    bundle = server_bundle(source_release="1.4.0")
    engine = create_engine(f"sqlite:///{database.database_path}")
    try:
        with server_settings_context(env_file=Path(database.identity.path), data_dir=database.data_dir) as settings:
            configuration = deployment_runner(settings).configuration
        with engine.begin() as connection:
            config = bundle.config(connection)
            config.attributes["configuration"] = configuration
            command.upgrade(config, bundle.source_revision)
            connection.exec_driver_sql("DROP TABLE pc_schema_revision")
    finally:
        engine.dispose()
    before = database.database_path.read_bytes()
    result = admit_database(database, version("powercontext"), python_executable=Path(sys.executable))
    assert not result.allowed_to_start and result.reason == "database_migration_required"
    assert database.database_path.read_bytes() == before


def test_configured_vector_profile_is_compared_with_stored_declaration(tmp_path):
    environment = tmp_path / "configuration" / "server.env"
    data = tmp_path / "data"
    content = new_configuration(data) + (
        b"POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openai:fixture\n"
        b"POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=retained-profile\n"
        b"POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=3\n"
    )
    write_protected_file(environment, content)
    with server_settings_context(env_file=environment, data_dir=data) as settings:
        prepare_server_storage(settings)
    summary = inspect_existing_configuration(environment, data_dir=data)
    before = summary.database_path.read_bytes()
    admitted = admit_database(summary, version("powercontext"), python_executable=Path(sys.executable))
    assert admitted.allowed_to_start, admitted
    write_protected_file(environment, content.replace(b"retained-profile", b"different-profile"))
    summary = inspect_existing_configuration(environment, data_dir=data)
    rejected = admit_database(summary, version("powercontext"), python_executable=Path(sys.executable))
    assert not rejected.allowed_to_start and rejected.reason == "database_configuration_mismatch"
    assert summary.database_path.read_bytes() == before
