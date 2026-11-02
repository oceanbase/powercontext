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

"""Actual CLI process coverage for passive metrics over the supported SQLite combined role."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families


@pytest.mark.skipif(sys.platform == "win32", reason="Requires POSIX SIGTERM shutdown")
def test_combined_cli_metrics_authentication_passive_scrapes_and_shutdown(tmp_path):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_SERVER_", "OTEL_", "OPENAI_"))
    }
    environment.update({
        "POWERCONTEXT_SERVER_DATABASE_KIND": "sqlite",
        "POWERCONTEXT_SERVER_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": "local-metrics-test-token",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        "POWERCONTEXT_SERVER_LOGGING_ACCESS": "false",
    })
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
                "all",
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
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        pytest.fail(f"CLI exited before serving metrics: {log_path.read_text()}")
                    try:
                        response = client.get("/metrics")
                        break
                    except httpx.ConnectError:
                        if time.monotonic() >= deadline:
                            pytest.fail(f"CLI did not start: {log_path.read_text()}")
                        time.sleep(0.05)
                assert response.status_code == 401
                headers = {"Authorization": "Bearer local-metrics-test-token"}
                assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
                observations = []
                for _ in range(3):
                    response = client.get("/metrics", headers=headers)
                    assert response.status_code == 200
                    samples = {
                        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
                        for family in text_string_to_metric_families(response.text)
                        for sample in family.samples
                    }
                    assert samples[("powercontext_server_service_role_info", (("role", "all"),))] == 1
                    assert samples[("powercontext_server_artifact_processing_supervisor_present", ())] == 0
                    assert samples[("powercontext_server_telemetry_collection_failures_total", ())] == 0
                    assert (
                        samples[
                            ("powercontext_server_dependency_state", (("dependency", "database"), ("state", "ready")))
                        ]
                        == 1
                    )
                    observations.append({
                        key: value
                        for key, value in samples.items()
                        if "dependency" in key[0] and key[0].endswith(("_count", "_timestamp_seconds"))
                    })
                assert observations[0]
                assert observations[0] == observations[1] == observations[2]
                # This is the supported combined-role process, so its API remains available.
                assert client.get("/v1/capabilities", headers=headers).status_code == 200
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=15) in {0, -signal.SIGTERM}
            assert "Application shutdown complete" in log_path.read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    with socket.socket() as rebound:
        rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rebound.bind(("127.0.0.1", port))
