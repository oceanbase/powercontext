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

"""Deploy and stop collect plugins using the delivered CLI and public management API."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


def prepare_environment(directory: Path, base: Path | None = None) -> tuple[Path, Path]:
    state = directory / "source-services" / uuid4().hex
    envfile = directory / "source.env"
    original = base.read_text() if base is not None else ""
    values = {
        "POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR": str(directory / "source-plugins"),
        "POWERCONTEXT_SERVER_SOURCE_MANAGEMENT": json.dumps({"state_dir": str(state)}),
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
    }
    envfile.write_text(original + "\n" + "\n".join(f"{key}='{value}'" for key, value in values.items()) + "\n")
    return envfile, state


def bind_server(envfile: Path, url: str) -> None:
    with envfile.open("a") as stream:
        stream.write(f"POWERCONTEXT_SERVER_PUBLIC_URL='{url.rstrip('/')}'\n")


async def cli(directory: Path, *args: str) -> dict[str, Any]:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("POWERCONTEXT_SERVER_")}
    environment["POWERCONTEXT_HOME"] = str(directory / "home")
    process = await asyncio.create_subprocess_exec(
        str(Path(sys.executable).with_name("powercontext")),
        *args,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 100)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, (stdout + stderr).decode()
    return json.loads(stdout)


async def instance_state(client: httpx.AsyncClient, identifier: str, actual: str | None = None) -> dict[str, Any]:
    async with asyncio.timeout(40):
        while True:
            response = await client.post(
                "/v1/source-management/instances", json={"instance_id": identifier, "view": "control"}
            )
            if response.status_code == 200:
                state = response.json()["instances"][0]
                if actual is None or state["actual"] == actual:
                    return state
            await asyncio.sleep(0.1)


async def set_enabled(client: httpx.AsyncClient, identifier: str, enabled: bool) -> dict[str, Any]:
    response = await client.post(
        "/v1/source-management/instance-set-enabled", json={"instance_id": identifier, "enabled": enabled}
    )
    assert response.status_code == 200, response.text
    return await instance_state(client, identifier, "running" if enabled else "stopped")


async def stop_services(directory: Path, state: Path) -> None:
    for path in sorted((state / "instances").glob("*/runtime-config.json")):
        await cli(directory, "source", "runtime", "stop", "--config", str(path))
    if (state / "manager.json").exists():
        await cli(directory, "source", "manager", "stop", "--config", str(state / "manager.json"))


async def grant_scope(client: httpx.AsyncClient, scope: str) -> None:
    identity = await client.get("/v1/access/me")
    assert identity.status_code == 200, identity.text
    principal = identity.json()["principal"]
    response = await client.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": principal["type"], "id": principal["id"]},
            "resource": {"type": "scope", "scope_id": scope},
            "role": "scope.contributor",
            "idempotency_key": "collect-test-" + scope,
        },
    )
    assert response.status_code == 201, response.text
