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

"""Exercise the Core-owned destination through real Core, Gateway and upstream TCP listeners."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
import uvicorn

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_context_protection_transports import _ADMIN, _Authentication, _create_scope, _settings

GATEWAY_BINARY = Path(__file__).resolve().parents[2] / "gateway/target/debug/pc-gateway"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.anyio
@pytest.mark.skipif(not GATEWAY_BINARY.is_file(), reason="Build the real Gateway binary with cargo build first")
async def test_real_gateway_sends_only_to_core_approved_a_and_never_to_b(tmp_path):  # noqa: C901 - cross-process acceptance
    received_a: list[bytes] = []
    received_b: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, received: list[bytes]) -> None:
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            # Count the request as soon as bytes arrive, including redirected GETs without a body.
            received.append(header)
            length = next(
                (
                    int(line.split(b":", 1)[1].strip())
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ),
                0,
            )
            payload = await reader.readexactly(length)
            received[-1] += payload
            if b"redirect-me" in payload and received is received_a:
                writer.write(
                    b"HTTP/1.1 307 Temporary Redirect\r\nLocation: "
                    + f"http://127.0.0.1:{port_b}/v1/chat/completions".encode()
                    + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                return
            body = json.dumps({
                "model": "test",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            }).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server_a = await asyncio.start_server(lambda rd, wr: upstream(rd, wr, received_a), "127.0.0.1", 0)
    server_b = await asyncio.start_server(lambda rd, wr: upstream(rd, wr, received_b), "127.0.0.1", 0)
    port_a = server_a.sockets[0].getsockname()[1]
    port_b = server_b.sockets[0].getsockname()[1]
    settings = _settings(tmp_path).model_copy(
        update={
            "inference": InferenceConfig(
                protection=InferenceProtectionConfig(
                    mode="enforcing",
                    targets={
                        "model": UpstreamProfile(
                            endpoint=f"http://127.0.0.1:{port_a}/v1/chat/completions",
                            model="test",
                            protocol="openai-chat",
                            max_output_tokens=64,
                            context_window_tokens=65536,
                            validated_utf8_token_bound=True,
                        )
                    },
                )
            ),
        }
    )
    await asyncio.to_thread(prepare_server_database, settings)
    async with open_builtin_access_control(
        settings.database, bootstrap_administrators=(_ADMIN,), deployment_id="context-transports"
    ):
        pass
    app = create_server_app(
        settings=settings, authentication_provider=_Authentication(), scheduler_path=tmp_path / "scheduler.db"
    )
    core_port, gateway_port = _free_port(), _free_port()
    core = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=core_port, log_level="error"))
    core_task = asyncio.create_task(core.serve())
    process: asyncio.subprocess.Process | None = None
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{core_port}",
            headers={"Authorization": "Bearer transport-admin"},
            trust_env=False,
        ) as client:
            for _ in range(100):
                if core.started:
                    break
                await asyncio.sleep(0.05)
            assert core.started
            scope = await _create_scope(client, "gateway-real-target")
            for role in ("scope.viewer", "gateway.user"):
                grant = await client.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": "service", "id": _ADMIN.id},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": role,
                        "idempotency_key": f"gateway-real-{role}",
                    },
                )
                assert grant.status_code == 201, grant.text
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("PC_GATEWAY_", "POWERCONTEXT_SERVER_", "OPENAI_"))
            }
            environment.update({
                "PC_GATEWAY_CORE_BASE": f"http://127.0.0.1:{core_port}",
                "PC_GATEWAY_CORE_TOKEN": "transport-admin",
                "PC_GATEWAY_LISTEN": f"127.0.0.1:{gateway_port}",
                "PC_GATEWAY_UPSTREAM_PROFILE": "model",
                "PC_GATEWAY_DEFAULT_SCOPE": scope,
                "PC_GATEWAY_API_KEY": "gateway-key",
            })
            process = await asyncio.create_subprocess_exec(
                str(GATEWAY_BINARY),
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{gateway_port}", trust_env=False) as gateway:
                for _ in range(100):
                    if process.returncode is not None:
                        assert process.stderr is not None
                        pytest.fail(f"Gateway exited before ready: {(await process.stderr.read()).decode()}")
                    try:
                        if (await gateway.get("/healthz")).status_code == 200:
                            break
                    except httpx.ConnectError:
                        await asyncio.sleep(0.05)
                else:
                    pytest.fail("Gateway listener did not start")
                unauthorized = await gateway.post(
                    "/gateway/v1/chat/completions",
                    json={"model": "test", "messages": [{"role": "user", "content": "hello"}]},
                )
                assert unauthorized.status_code == 401
                assert received_a == received_b == []
                wrong_model = await gateway.post(
                    "/gateway/v1/chat/completions",
                    headers={"Authorization": "Bearer gateway-key"},
                    json={
                        "model": "unapproved-model",
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )
                assert wrong_model.status_code == 400, wrong_model.text
                assert wrong_model.json()["reason_codes"] == ["model_mismatch"]
                assert received_a == received_b == []
                response = await gateway.post(
                    "/gateway/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer gateway-key",
                        "X-Upstream-URL": f"http://127.0.0.1:{port_b}/v1/chat/completions",
                    },
                    json={"model": "test", "max_tokens": 64, "messages": [{"role": "user", "content": "hello"}]},
                )
                assert response.status_code == 200, response.text
                assert response.json()["choices"][0]["message"]["content"] == "ok"
                assert len(received_a) == 1
                assert b'"hello"' in received_a[0]
                assert received_b == [], "Core profile A must not send any bytes to listener B"
                redirected = await gateway.post(
                    "/gateway/v1/chat/completions",
                    headers={"Authorization": "Bearer gateway-key"},
                    json={"model": "test", "max_tokens": 64, "messages": [{"role": "user", "content": "redirect-me"}]},
                )
                assert redirected.status_code == 503, redirected.text
                assert redirected.json()["decision"] == "gate_paused"
                assert len(received_a) == 2
                assert received_b == [], "An upstream redirect must not reach listener B"
    finally:
        if process is not None:
            process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
            if process.returncode is None:
                process.kill()
                await process.wait()
        core.should_exit = True
        await core_task
        server_a.close()
        server_b.close()
        await server_a.wait_closed()
        await server_b.wait_closed()


@pytest.mark.anyio
@pytest.mark.skipif(not GATEWAY_BINARY.is_file(), reason="Build the real Gateway binary with cargo build first")
async def test_legacy_gateway_destination_configuration_fails_before_listening(tmp_path):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PC_GATEWAY_")}
    environment["PC_GATEWAY_UPSTREAM_URL_CHAT"] = "http://127.0.0.1:1/v1/chat/completions"
    environment["PC_GATEWAY_API_KEY"] = "gateway-key"
    environment["PC_GATEWAY_CORE_TOKEN"] = "core-token"  # noqa: S105 - synthetic test token
    process = await asyncio.create_subprocess_exec(
        str(GATEWAY_BINARY),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout=5)
        assert process.returncode == 2, (output + error).decode()
        assert b"Gateway upstream URLs are no longer accepted" in output + error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.anyio
@pytest.mark.skipif(not GATEWAY_BINARY.is_file(), reason="Build the real Gateway binary with cargo build first")
@pytest.mark.parametrize(
    ("gateway_key", "core_token", "reason"),
    [
        (None, "core-token", "PC_GATEWAY_API_KEY is required"),
        ("gateway-key", None, "PC_GATEWAY_CORE_TOKEN is required"),
        ("same-token", "same-token", "must differ"),
    ],
)
async def test_gateway_rejects_missing_or_shared_credentials_before_listening(gateway_key, core_token, reason):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PC_GATEWAY_")}
    if gateway_key is not None:
        environment["PC_GATEWAY_API_KEY"] = gateway_key
    if core_token is not None:
        environment["PC_GATEWAY_CORE_TOKEN"] = core_token
    process = await asyncio.create_subprocess_exec(
        str(GATEWAY_BINARY), env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout=5)
        assert process.returncode == 2, (output + error).decode()
        assert reason.encode() in output + error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
