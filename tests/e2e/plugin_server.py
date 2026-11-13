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

"""Run the installed Server CLI with a plugin directory and talk to its public HTTP API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sys
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

ADMIN_AUTHORIZATION_VALUE = "plugin-e2e-admin"
REPOSITORY = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def running_server(
    directory: Path,
    plugins: Path | None = None,
    *,
    extra_env: Mapping[str, str | None] | None = None,
    identities: dict[str, str] | None = None,
):
    """Run Server with the admin Bearer, or with an identity map of ``{name: token}`` where ``admin`` administers.

    An ``extra_env`` value of ``None`` removes that default variable.
    """

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    env.update({
        "POWERCONTEXT_SERVER_DATABASE": json.dumps({
            "kind": "sqlite",
            "url": f"sqlite+aiosqlite:///{directory / 'server.db'}",
        }),
        "POWERCONTEXT_SERVER_RUNTIME": json.dumps({"artifact_processing_families": []}),
        "POWERCONTEXT_SERVER_WORKSPACE": str(directory),
        "POWERCONTEXT_HOME": str(directory / "home"),
        "POWERCONTEXT_SERVER_AUTH_ENABLED": "true",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": ADMIN_AUTHORIZATION_VALUE,
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        # Worker children import test-owned plugin helpers from the repository.
        "PYTHONPATH": os.pathsep.join(filter(None, (str(REPOSITORY), env.get("PYTHONPATH")))),
    })
    overrides: dict[str, str | None] = dict(extra_env or {})
    if identities is not None:
        overrides |= {"POWERCONTEXT_SERVER_AUTH_ENABLED": None, "POWERCONTEXT_SERVER_AUTH_TOKEN": None}
        overrides |= _identity_environment(directory, identities)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    if plugins is not None:
        env["POWERCONTEXT_SERVER_PLUGINS_DIR"] = str(plugins)
    entrypoint = Path(sys.executable).with_name("powercontext")
    with (directory / "server.log").open("a") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(entrypoint),
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
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"},
                timeout=20,
            ) as client:
                async with asyncio.timeout(60):
                    while True:
                        assert process.returncode is None, (directory / "server.log").read_text()
                        try:
                            if (await client.get("/health/ready")).status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.1)
                yield client
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 20)
            except TimeoutError:
                process.kill()
                await process.wait()


def _identity_environment(directory: Path, identities: dict[str, str]) -> dict[str, str]:
    """Write an identity map of ``{name: token}`` plus the admin token, and configure Server to use it."""

    tokens = {"admin": ADMIN_AUTHORIZATION_VALUE, **identities}
    mapping = directory / "identities.json"
    mapping.write_text(
        json.dumps({
            "identities": [
                {
                    "credential_id": name,
                    "token_digest": hashlib.sha256(token.encode()).hexdigest(),
                    "subject": {"type": "service", "id": name},
                }
                for name, token in tokens.items()
            ]
        })
    )
    mapping.chmod(0o600)
    return {
        "POWERCONTEXT_SERVER_AUTH": json.dumps({
            "enabled": True,
            "identities_file": str(mapping),
            "administrators": ["service:admin"],
        }),
        "POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID": "admin",
    }


async def create_scope(client: httpx.AsyncClient, key: str) -> str:
    response = await client.post(
        "/v1/scopes", json={"title": key, "summary": "Plugin acceptance", "idempotency_key": key}
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


async def capture(client: httpx.AsyncClient, scope: str, source_id: str, content: str) -> None:
    response = await client.post(
        "/v1/sources/content", json={"scope_id": scope, "source_id": source_id, "content": content}
    )
    assert response.status_code == 202, response.text


async def wait_for(
    read: Callable[[], Any], ready: Callable[[Any], bool], *, seconds: float = 60, log: Path | None = None
) -> Any:
    value = None
    try:
        async with asyncio.timeout(seconds):
            while True:
                value = await read()
                if ready(value):
                    return value
                await asyncio.sleep(0.2)
    except TimeoutError:
        detail = "" if log is None else (await asyncio.to_thread(log.read_text))[-4000:]
        raise AssertionError(f"condition not reached; last value: {value!r}\n{detail}") from None  # noqa: TRY003


async def items(client: httpx.AsyncClient, scope: str, family: str) -> list[dict[str, Any]]:
    response = await client.get(f"/v1/scopes/{scope}/artifacts/{family}")
    assert response.status_code == 200, response.text
    return response.json()["items"]
