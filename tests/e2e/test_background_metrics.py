# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.settings import ServerSettings
from tests.e2e.atomic_memory_migration_backend import migration_profile
from tests.e2e.oceanbase_process import CLI_BOOTSTRAP, StartupDeadline, run_cli


async def _database_objects(config: OceanBaseConfig) -> tuple[tuple[str, str], ...]:
    async with migration_profile(config) as profile, profile.database.transaction() as connection:
        rows = await connection.exec_driver_sql("SHOW FULL TABLES")
        return tuple(sorted((row[0], row[1]) for row in rows))


@pytest.fixture
def initialized_background_cli(tmp_path, migration_backend):
    import os
    import socket
    import sys

    assert isinstance(migration_backend.config, OceanBaseConfig)
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    environment.update({
        "POWERCONTEXT_SERVER_DATABASE_KIND": "oceanbase",
        "POWERCONTEXT_SERVER_DATABASE_URL": migration_backend.config.url.get_secret_value(),
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": "background-fixture-token",
        "POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT": str(port),
        "POWERCONTEXT_SERVER_METRICS_ENABLED": "true",
    })
    command = [
        sys.executable,
        "-c",
        CLI_BOOTSTRAP,
        "run",
        "--role",
        "background",
        "--no-env-file",
    ]
    before = asyncio.run(_database_objects(migration_backend.config))
    assert before == ()
    reports = os.environ.get("MIGRATION_REPORTS")
    rejection_dir = Path(reports) / "background-metrics" / "uninitialized" if reports else tmp_path / "uninitialized"
    rejected = run_cli(command, env=environment, output=rejection_dir, timeout=60)
    assert rejected.returncode != 0, rejected.stdout + rejected.stderr
    assert "migration_required" in rejected.stdout + rejected.stderr
    assert asyncio.run(_database_objects(migration_backend.config)) == before

    # Explicit initialization must finish before either split Server role can serve.
    runner = deployment_runner(
        ServerSettings(
            database=migration_backend.config,
            runtime=RuntimeConfig(artifact_processing_role="background"),
        ),
        evidence_dir=tmp_path / "migration-evidence",
        lock_coordination="single-host",
    )
    plan = runner.plan(backup_policy="skip")
    initialized = runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="skip",
        accept_no_backup=True,
    )
    assert initialized.state == "ready"
    assert initialized.readiness_scope == "server"
    return command, environment, port


@pytest.mark.parametrize("migration_backend", ["oceanbase"], indirect=True)
def test_background_cli_serves_only_authorized_metrics_and_stops_cleanly(tmp_path, initialized_background_cli):
    import os
    import signal
    import subprocess
    import time

    import httpx

    command, environment, port = initialized_background_cli
    reports = os.environ.get("MIGRATION_REPORTS")
    log_path = Path(reports) / "background-metrics" / "background.log" if reports else tmp_path / "background.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as output:
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = StartupDeadline(process, log_path.parent / "startup", timeout=90)
            ready = False
            with httpx.Client(timeout=0.5) as client:
                while deadline.pending() and process.poll() is None:
                    try:
                        ready = client.get(base + "/health/live").status_code == 200
                    except httpx.TransportError:
                        time.sleep(0.05)
                    if ready:
                        break
                assert ready, (
                    f"Background CLI did not become live (returncode={process.poll()}); log tail:\n"
                    + log_path.read_text(errors="replace")[-16000:]
                )
                assert client.get(base + "/metrics").status_code == 401
                headers = {"Authorization": "Bearer background-fixture-token"}
                response = client.get(base + "/metrics", headers=headers)
                assert response.status_code == 200
                assert 'powercontext_server_readiness_state{state="ready"} 1.0' in response.text
                assert client.get(base + "/v1/scopes/default", headers=headers).status_code == 404
                process.send_signal(signal.SIGTERM)
                assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
