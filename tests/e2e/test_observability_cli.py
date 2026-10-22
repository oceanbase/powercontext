"""Opt-in acceptance against real Compose backends and an existing foreground Server."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PC_OBSERVABILITY_CLI_ACCEPTANCE") != "1",
    reason="Set PC_OBSERVABILITY_CLI_ACCEPTANCE=1 with Docker available to run real CLI deployment acceptance",
)


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_existing_server_opt_in_stores_three_signals_and_preserves_business_data(tmp_path):
    executable = str(Path(sys.executable).parent / "powercontext")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "OTEL_", "OPENAI_", "ANTHROPIC_"))
    }
    state = tmp_path / "installed-observability"
    environment["POWERCONTEXT_OBSERVABILITY_HOME"] = str(state)
    port, grafana_port, gateway_port, phoenix = _port(), _port(), _port(), _port()
    token = secrets.token_urlsafe(32)
    config = tmp_path / "existing.env"
    config.write_text(
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        f"POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{tmp_path / 'existing.db'}\n"
        "POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0\n"
        f"POWERCONTEXT_SERVER_HTTP_PORT={port}\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
        f"POWERCONTEXT_SERVER_AUTH_TOKEN={token}\n"
    )
    config.chmod(0o600)
    original = config.read_bytes()
    log = (tmp_path / "server-output.log").open("w")
    command = [executable, "server", "run", "--env-file", str(config)]
    process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)

    def cli(*arguments: str):
        result = subprocess.run(
            [executable, "observability", *arguments],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def ready(client):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            assert process.poll() is None, "Isolated Server exited; inspect server-output.log"
            try:
                if client.get("/health/ready").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        pytest.fail("Isolated Server did not become ready")

    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + token},
            trust_env=False,
            timeout=5,
        ) as client:
            ready(client)
            scope = client.get("/v1/scopes/default").json()["scope_id"]
            created = client.post(
                "/v1/sources/content",
                json={
                    "scope_id": scope,
                    "source_id": "existing-before-monitoring",
                    "content": "Isolated durable record before enabling observability.",
                },
            )
            assert created.status_code == 202
            cli(
                "up",
                "--server-env",
                str(config),
                "--grafana-port",
                str(grafana_port),
                "--gateway-port",
                str(gateway_port),
                "--phoenix",
                str(phoenix),
            )
            credentials = (state / ".local" / "gateway-password").read_bytes()
            process.terminate()
            process.wait(timeout=15)
            process = subprocess.Popen(
                [*command, "--observability"], env=environment, stdout=log, stderr=subprocess.STDOUT
            )
            ready(client)
            assert "existing-before-monitoring" in client.get(f"/v1/scopes/{scope}/sources").text
            assert client.get("/v1/capabilities", headers={"Authorization": "Bearer wrong"}).status_code == 401
            delivery = cli("check", "--exercise")
            assert "PASS: fresh request metric, trace and matching log stored" in delivery
            assert "PASS: matching trace stored in Phoenix" in delivery
            cli("up")
            assert (state / ".local" / "gateway-password").read_bytes() == credentials
            cli("status")
            assert "Metrics: current scrape is healthy" in cli("check")
            cli("down")
            assert client.get("/v1/capabilities").status_code == 200
            assert config.read_bytes() == original
            assert (state / "state.json").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if (state / "state.json").exists():
            # Own monitoring containers only; volumes remain available for failure diagnosis.
            subprocess.run(
                [executable, "observability", "down"], env=environment, capture_output=True, timeout=120, check=False
            )
        log.close()
