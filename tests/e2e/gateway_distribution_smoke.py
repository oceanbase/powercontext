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

"""Exercise an unpacked Gateway against a real, disposable SQLite Core; no provider calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


@contextmanager
def process(command: list[str], env: dict[str, str], directory: Path, name: str):
    with (directory / f"{name}.log").open("w") as log:
        child = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


async def ready(child, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    for _ in range(240):
        require(child.poll() is None, "Distribution process exited before readiness")
        try:
            response = await client.get(path)
            if response.status_code == 200:
                return response.json()
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.25)
    message = "Distribution startup timed out"
    raise RuntimeError(message)


async def smoke(binary: Path, directory: Path) -> None:
    core_token, gateway_token = secrets.token_hex(24), secrets.token_hex(24)
    # Do not inherit provider credentials, model settings or a personal database.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith((
            "POWERCONTEXT_",
            "PC_GATEWAY_",
            "OPENAI_",
            "ANTHROPIC_",
            "OTEL_",
        ))
    }
    core_env = env | {
        "POWERCONTEXT_HOME": str(directory / "home"),
        "POWERCONTEXT_SERVER_DATABASE": json.dumps({
            "kind": "sqlite",
            "url": f"sqlite+aiosqlite:///{directory / 'core.db'}",
        }),
        "POWERCONTEXT_SERVER_HTTP_UDS": str(directory / "core.sock"),
        "POWERCONTEXT_SERVER_AUTH_TOKEN": core_token,
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        "POWERCONTEXT_SERVER_RUNTIME": json.dumps({"artifact_processing_families": []}),
        "POWERCONTEXT_SERVER_INFERENCE": json.dumps({"protection": {"mode": "disabled"}}),
        "POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET": secrets.token_hex(32),
    }
    command = [sys.executable, "-c", "from powercontext.cli.app import main; main()", "server", "run", "--no-env-file"]
    with process(command, core_env, directory, "core") as core:
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(directory / "core.sock")),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {core_token}"},
            timeout=2,
            trust_env=False,
        ) as direct:
            capabilities = await ready(core, direct, "/v1/capabilities")
            info = (await direct.get("/v1/server-info")).json()
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        gateway_env = env | {
            "PC_GATEWAY_CORE_UDS": str(directory / "core.sock"),
            "PC_GATEWAY_CORE_TOKEN": core_token,
            "PC_GATEWAY_API_KEY": gateway_token,
            "PC_GATEWAY_LISTEN": f"127.0.0.1:{port}",
            "PC_GATEWAY_INJECTION": "off",
            "PC_GATEWAY_SEDIMENT": "off",
            "RUST_LOG": "warn",
        }
        with process([str(binary)], gateway_env, directory, "gateway") as gateway:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as proxy:
                health = await ready(gateway, proxy, "/healthz")
                require(health == {"status": "ok", "service": "pc-gateway"}, "Unexpected Gateway health")
                require((await proxy.get("/v1/capabilities")).status_code == 401, "Core authentication bypassed")
                require(
                    (
                        await proxy.get(
                            "/v1/capabilities",
                            headers={
                                "Authorization": f"Bearer {gateway_token}",
                            },
                        )
                    ).status_code
                    == 401,
                    "Gateway credential replaced Core authentication",
                )
                headers = {"Authorization": f"Bearer {core_token}"}
                proxied = await proxy.get("/v1/capabilities", headers=headers)
                require(proxied.status_code == 200 and proxied.json() == capabilities, "Core capabilities mismatch")
                proxied_info = await proxy.get("/v1/server-info", headers=headers)
                require(proxied_info.status_code == 200 and proxied_info.json() == info, "Core identity mismatch")
                denied = await proxy.post("/gateway/v1/responses", json={"model": "unused", "input": "probe"})
                require(denied.status_code == 401, "Model route authentication bypassed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pcg-") as temporary:
        asyncio.run(smoke(args.binary.resolve(), Path(temporary)))
    print("Gateway distribution: real Core identity, proxy and authentication checks passed; no provider calls")


if __name__ == "__main__":
    main()
