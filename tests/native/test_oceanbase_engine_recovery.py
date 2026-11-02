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

"""Actual observer crash/reopen acceptance in an explicitly owned disposable container."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from tests.builtin.persistence.test_mysql_migration_runner import (
    _apply,
    _BackendTarget,
    _full_server_runner,
    _load_full_server_baseline,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("POWERCONTEXT_TEST_MIGRATION_OCEANBASE_ENGINE_CRASH_ID"),
    reason="requires the exact ID and loopback URL of a disposable OceanBase container owned by this test run",
)


def _docker(container: str, operation: str, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", operation, container, *arguments],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
        check=check,
    )


@pytest.fixture
def engine_target(tmp_path: Path) -> Iterator[tuple[str, _BackendTarget]]:
    container = os.environ["POWERCONTEXT_TEST_MIGRATION_OCEANBASE_ENGINE_CRASH_ID"]
    assert re.fullmatch(r"[0-9a-f]{64}", container), "Supply the exact owned container ID"
    (info,) = json.loads(_docker(container, "inspect").stdout)
    assert info["Id"] == container and info["State"]["Running"]
    config = OceanBaseConfig(url=SecretStr(os.environ["POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL"]))
    url = make_url(config.url.get_secret_value())
    assert url.host in {"127.0.0.1", "localhost"} and url.database
    assert url.database.startswith("pc_migration_probe_")
    assert not any(key.lower() in {"db", "database"} for key in url.query), (
        "The crash target must not override its selected database"
    )
    (binding,) = info["NetworkSettings"]["Ports"]["2881/tcp"]
    assert binding["HostIp"] == "127.0.0.1" and int(binding["HostPort"]) == (url.port or 2881)
    (tmp_path / "container.json").write_text(
        json.dumps({"id": container, "image": info["Config"]["Image"], "image_id": info["Image"], "port": url.port}),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    management = _BackendTarget(config, evidence)
    database = f"pc_migration_probe_crash_{uuid4().hex}"

    def create(connection, _identity, _guard):
        assert connection is not None
        connection.exec_driver_sql(f"CREATE DATABASE `{database}`")

    management.connections().run(create)
    child = config.model_copy(
        update={"url": SecretStr(url.set(database=database).render_as_string(hide_password=False))}
    )
    try:
        yield container, _BackendTarget(child, evidence)
    finally:
        # Keep the owned database and journal for diagnosis, including on failure.
        result = _docker(container, "logs", check=False)
        (tmp_path / "oceanbase.log").write_text(result.stdout + result.stderr, encoding="utf-8")


def test_oceanbase_observer_crash_resumes_original_migration_and_preserves_history(
    engine_target: tuple[str, _BackendTarget], tmp_path: Path
) -> None:
    container, backend = engine_target
    assert isinstance(backend.config, OceanBaseConfig)
    _load_full_server_baseline(backend)
    evidence = backend.evidence_directory
    root = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, (str(root), os.environ.get("PYTHONPATH")))),
        "POWERCONTEXT_MIGRATION_CRASH_TARGET": backend.config.url.get_secret_value(),
        "POWERCONTEXT_MIGRATION_WAIT_FOR_ENGINE_CRASH": "1",
    }
    with (tmp_path / "migration-client.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            [
                sys.executable,
                str(root / "tests/fixtures/database_migrations/crash_after_registered_ddl.py"),
                str(evidence),
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 180
            while not (evidence / "engine-crash-ready.json").exists():
                assert child.poll() is None, "Migration exited before reaching the engine crash boundary"
                assert time.monotonic() < deadline, "Migration did not reach the registered DDL"
                time.sleep(0.1)
            (journal,) = evidence.rglob("maintenance.json")
            interrupted = json.loads(journal.read_text())
            assert interrupted["state"] == "active"
            (pid,) = _docker(container, "exec", "pgrep", "-x", "observer").stdout.split()
            assert pid.isdigit()
            cwd = _docker(container, "exec", "readlink", f"/proc/{pid}/cwd").stdout.strip()
            executable = _docker(container, "exec", "readlink", f"/proc/{pid}/exe").stdout.strip()
            expected = _docker(container, "exec", "readlink", "-f", "/root/demo/bin/observer").stdout.strip()
            assert cwd == "/root/demo" and executable == expected
            _docker(container, "exec", "kill", "-KILL", pid)
            assert _docker(container, "exec", "readlink", f"/proc/{pid}/exe", check=False).returncode != 0
            (evidence / "engine-stopped").write_text("Owned observer terminated with SIGKILL.\n", encoding="utf-8")
            assert child.wait(timeout=120) != 0
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=30)
    # The slim image's entrypoint unfolds a snapshot and truncates logs. Restart
    # only observer so this test recovers the same data and commit logs.
    subprocess.run(
        ["docker", "exec", "--workdir", cwd, "--env", "LD_LIBRARY_PATH=/root/demo/lib", container, "bin/observer"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    deadline = time.monotonic() + 240

    def ping(connection, _identity, _guard):
        assert connection is not None
        return connection.exec_driver_sql("SELECT 1").scalar_one()

    while True:
        try:
            assert backend.connections().run(ping) == 1
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    (new_pid,) = _docker(container, "exec", "pgrep", "-x", "observer").stdout.split()
    assert new_pid != pid
    recovery = _full_server_runner(backend).plan(backup_policy="manual")
    assert recovery.state == "recovery_required" and recovery.source_revision == "s1300"
    token = (recovery.coordination or {}).get("pending_run_id")
    assert token
    with pytest.raises(MigrationError, match="recovery_required"):
        _apply(_full_server_runner(backend), policy="manual")
    resumed = _full_server_runner(backend, acknowledge_previous_run=token)
    result = _apply(resumed, policy="manual")
    assert result.state == "ready" and result.revision == "v1_5_0_001" and result.readiness_scope == "server"
    assert result.backup_state == "user_confirmed" and result.backup_ref == interrupted["manual_ref"]
    assert resumed.verify().state == "ready"
    completed = json.loads(journal.read_text())
    assert completed["state"] == "complete"
    for key in ("maintenance_window_id", "source_fingerprint", "manual_ref"):
        assert completed[key] == interrupted[key]

    def history(connection, _identity, _guard):
        assert connection is not None
        return (
            connection.exec_driver_sql(
                "SELECT content FROM pc_artifacts WHERE scope_id='historical' AND artifact_id='preserved'"
            ).scalar_one(),
            connection.exec_driver_sql("SELECT title,summary FROM pc_scopes WHERE scope_id='historical'").one(),
        )

    assert backend.connections().run(history) == (
        b'{"history":"unchanged"}',
        ("retained title", "retained summary"),
    )
    assert not _apply(_full_server_runner(backend), policy="manual").changed
    (tmp_path / "engine-recovery.json").write_text(
        json.dumps({
            "previous_pid": pid,
            "pid": new_pid,
            "revision": result.revision,
            "history_preserved": True,
            "recovery_identity_preserved": True,
            "repeat_unchanged": True,
            "backup": "operator declaration, restoration not tested",
        }),
        encoding="utf-8",
    )
