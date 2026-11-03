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

"""Shared HTTP, MCP and real Server CLI helpers for Source acceptance tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = Path(sys.executable).with_name("powercontext")
TOKENS = {name: f"source-write-e2e-{name}" for name in ("deployer", "host", "reader")}


@asynccontextmanager
async def running_server(directory: Path, *, write_timeout=10, environment=None):
    identities = directory / "identities.json"
    identities.write_text(
        json.dumps({
            "identities": [
                {
                    "credential_id": name,
                    "token_digest": hashlib.sha256(token.encode()).hexdigest(),
                    "subject": {"type": "service", "id": name},
                }
                for name, token in TOKENS.items()
            ]
        })
    )
    identities.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    env.update({
        "POWERCONTEXT_HOME": str(directory / "home"),
        "POWERCONTEXT_SERVER_DATABASE": json.dumps({
            "kind": "sqlite",
            "url": f"sqlite+aiosqlite:///{directory / 'server.db'}",
        }),
        "POWERCONTEXT_SERVER_WORKSPACE": str(directory),
        "POWERCONTEXT_SERVER_RUNTIME": json.dumps({"artifact_processing_families": []}),
        "POWERCONTEXT_SERVER_AUTH": json.dumps({
            "enabled": True,
            "identities_file": str(identities),
            "administrators": ["service:deployer"],
        }),
        "POWERCONTEXT_SERVER_ACCESS": json.dumps({"mode": "enforced", "background_principal_id": "deployer"}),
        "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": json.dumps({"write_wait_timeout_seconds": write_timeout}),
        "POWERCONTEXT_SERVER_MCP_ENABLED": "true",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
    })
    env.update(environment or {})
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (directory / "server.log").open("a") as log:
        process = await asyncio.create_subprocess_exec(
            str(ENTRYPOINT),
            "server",
            "run",
            "--no-env-file",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=directory,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(
                base_url=url, headers={"Authorization": "Bearer " + TOKENS["deployer"]}, timeout=15
            ) as admin:
                async with asyncio.timeout(40):
                    while True:
                        assert process.returncode is None, (directory / "server.log").read_text()
                        try:
                            if (await admin.get("/health/ready")).status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.1)
                yield admin, url, process
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 20)
            except TimeoutError:
                process.kill()
                await process.wait()


async def load_write_cli(config: Path, url: str):
    credential = config.parent / "admin-token"
    credential.write_text(TOKENS["deployer"])
    credential.chmod(0o600)
    process = await asyncio.create_subprocess_exec(
        str(ENTRYPOINT),
        "source",
        "load",
        "--kind",
        "write",
        "--config",
        str(config),
        "--server-url",
        url,
        "--credential-file",
        str(credential),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, (stdout + stderr).decode()
    return json.loads(stdout)


async def create_scope(client, key):
    response = await client.post(
        "/v1/scopes", json={"title": key, "summary": "Source acceptance", "idempotency_key": key}
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


async def grant(admin, scope, subject="host", role="scope.contributor"):
    response = await admin.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": "service", "id": subject},
            "resource": {"type": "scope", "scope_id": scope},
            "role": role,
            "idempotency_key": f"{scope}-{subject}-{role}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def call(client, name, arguments):
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    return result.data


async def discover(client, scope):
    return await call(client, "pc_source_capabilities", {"scope_id": scope})


async def read(admin, scope, ref):
    response = await admin.get(f"/v1/scopes/{scope}/sources/{ref['source_type']}/{ref['source_id']}")
    assert response.status_code == 200, response.text
    return response.json()


def protected(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


async def wait_until(call, predicate, *, timeout_seconds: float = 15):
    async with asyncio.timeout(timeout_seconds):
        while True:
            value = await call()
            if predicate(value):
                return value
            await asyncio.sleep(0.05)
