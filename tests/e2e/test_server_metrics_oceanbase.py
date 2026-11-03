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

"""Opt-in real OceanBase and CLI checks; no SQLite/SeekDB substitutions.

Set POWERCONTEXT_TEST_OCEANBASE_URL to a disposable OceanBase MySQL tenant
with database create/drop rights. These checks use the deterministic TestModel,
not a live model provider. Each case creates and drops its own database.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


@pytest.fixture
def oceanbase_url():
    configured = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not configured:
        pytest.skip("requires a dedicated real OceanBase MySQL tenant")
    name = f"pc_metrics_{uuid4().hex}"

    async def execute(statement):
        async with (
            OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(configured)), tables=()) as profile,
            profile.database.transaction() as connection,
        ):
            version = str((await connection.exec_driver_sql("SELECT VERSION()")).scalar_one())
            assert "oceanbase" in version.lower(), "requires real OceanBase, not a compatible substitute"
            await connection.exec_driver_sql(statement)

    asyncio.run(execute(f"CREATE DATABASE `{name}`"))
    try:
        yield make_url(configured).set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(execute(f"DROP DATABASE `{name}`"))


def _samples(response):
    assert response.status_code == 200
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    }


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX SIGTERM")
@pytest.mark.parametrize("role", ["all", "background"])
def test_real_oceanbase_cli_supervisor_metrics_auth_and_shutdown(oceanbase_url, tmp_path, role):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_SERVER_", "OTEL_", "OPENAI_"))
    }
    environment.update({
        "POWERCONTEXT_SERVER_DATABASE_KIND": "oceanbase",
        "POWERCONTEXT_SERVER_DATABASE_URL": oceanbase_url,
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": "isolated-ob-metrics-token",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        "POWERCONTEXT_SERVER_LOGGING_ACCESS": "false",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL": "test",
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES": '["memory"]',
        "POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS": "3600",
        "POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_WORKERS": "1",
        "POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT": str(port),
    })
    migration_command = [sys.executable, "-c", "from powercontext.server.cli import app; app()", "db-migrate"]
    migration_options = [
        "--evidence-dir",
        str(tmp_path / "migration-evidence"),
        "--lock-coordination",
        "single-host",
        "--backup",
        "skip",
    ]
    planned = subprocess.run(
        [*migration_command, "plan", *migration_options],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    applied = subprocess.run(
        [
            *migration_command,
            "apply",
            *migration_options,
            "--plan-id",
            json.loads(planned.stdout)["plan_id"],
            "--yes",
            "--maintenance-confirmed",
            "--accept-no-backup",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert json.loads(applied.stdout)["server_ready"] is True
    log_path = tmp_path / "server.log"
    with log_path.open("w+") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from powercontext.server.cli import app; app()",
                "run",
                "--no-env-file",
                "--role",
                role,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 90
                while True:
                    assert process.poll() is None, log_path.read_text()
                    try:
                        response = client.get("/metrics")
                        break
                    except httpx.ConnectError:
                        assert time.monotonic() < deadline, log_path.read_text()
                        time.sleep(0.1)
                assert response.status_code == 401
                assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
                headers = {"Authorization": "Bearer isolated-ob-metrics-token"}
                observations = []
                for _ in range(3):
                    samples = _samples(client.get("/metrics", headers=headers))
                    assert samples[("powercontext_server_service_role_info", (("role", role),))] == 1
                    assert samples[("powercontext_server_artifact_processing_supervisor_present", ())] == 1
                    assert (
                        samples[("powercontext_server_artifact_processing_supervisor_running", (("family", "memory"),))]
                        == 1
                    )
                    assert samples[("powercontext_server_telemetry_collection_failures_total", ())] == 0
                    assert not any(
                        name.endswith((
                            "oldest_pending_timestamp_seconds",
                            "oldest_pending_age_seconds",
                            "pending_age_unknown_scopes",
                        ))
                        for name, _ in samples
                    )
                    observations.append({
                        key: value
                        for key, value in samples.items()
                        if "dependency" in key[0] and key[0].endswith(("_count", "_timestamp_seconds"))
                    })
                assert observations[0] == observations[1] == observations[2]
                if role == "all":
                    assert observations[0]
                    assert client.get("/health/ready", headers=headers).status_code == 200
                    assert client.get("/v1/capabilities", headers=headers).status_code == 200
                else:
                    assert samples[("powercontext_server_runtime_readiness_state", (("state", "unobserved"),))] == 0
                    assert client.get("/health/live").status_code == 200
                    for path in ("/health/ready", "/v1/capabilities", "/mcp", "/docs", "/openapi.json"):
                        assert client.get(path, headers=headers).status_code == 404
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=30) in {0, -signal.SIGTERM}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    with socket.socket() as rebound:
        rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rebound.bind(("127.0.0.1", port))


def test_real_oceanbase_pending_count_survives_reopen_and_partial_ack(oceanbase_url):
    async def scenario():
        config = OceanBaseConfig(url=SecretStr(oceanbase_url))
        intents = ArtifactProcessingIntentRepository()
        binding = "memory-source-window"
        async with OceanBaseProfile.open(config, tables=BUILTIN_TABLES) as profile:
            for generation in range(1, 4):
                async with profile.database.transaction() as connection:
                    row = await intents.request(connection, "pending-scope", binding)
                    assert row.requested_generation == generation
            async with profile.database.transaction(consistent_snapshot=True) as connection:
                observed = await intents.pending_observation(connection, binding)
                assert observed.pending_count == 1
                assert set(observed.model_dump()) == {"pending_count", "observed_at"}
                first_observed_at = observed.observed_at
        async with OceanBaseProfile.open(config, tables=BUILTIN_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await intents.acknowledge(connection, "pending-scope", binding, 1)
            async with profile.database.transaction(consistent_snapshot=True) as connection:
                observed = await intents.pending_observation(connection, binding)
                assert observed.pending_count == 1
                assert observed.observed_at >= first_observed_at
            async with profile.database.transaction() as connection:
                await intents.acknowledge(connection, "pending-scope", binding, 3)
            async with profile.database.transaction(consistent_snapshot=True) as connection:
                observed = await intents.pending_observation(connection, binding)
                assert observed.pending_count == 0
                assert observed.observed_at >= first_observed_at

    asyncio.run(scenario())
