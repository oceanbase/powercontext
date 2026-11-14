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

"""Gateway authentication through the real executable and listening sockets."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from tests.e2e.test_rfc2086_transport_acceptance import completion, upstream
from tests.e2e.test_server_uds import _TOKEN, _environment, _running_server

_ROOT = Path(__file__).resolve().parents[2]
_KEY = "synthetic-gateway-key+/=="
_PROVIDER_KEY = "synthetic-provider-key"
pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="Gateway Core link requires UDS")


@pytest.fixture(scope="session")
def gateway_binary() -> Path:
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Gateway process acceptance requires the Rust toolchain")
    subprocess.run(
        [cargo, "build", "--locked", "--manifest-path", str(_ROOT / "gateway/Cargo.toml"), "-p", "pc-gateway-app"],
        check=True,
        timeout=300,
    )
    return _ROOT / "gateway/target/debug/pc-gateway"


def gateway_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PC_GATEWAY_") and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    environment.update({
        "PC_GATEWAY_CORE_BASE": "http://127.0.0.1:1",
        "PC_GATEWAY_CORE_TOKEN": _TOKEN,
        "RUST_LOG": "info",
    })
    return environment


def rejected_gateway(gateway_binary: Path, environment: dict[str, str]) -> bytes:
    with subprocess.Popen(
        [gateway_binary], env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        try:
            output, _ = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
            pytest.fail(f"Gateway accepted invalid authentication configuration: {output.decode()}")
        assert process.returncode == 2, output.decode()
        assert b"PC_GATEWAY_API_KEY" in output
        return output


@pytest.mark.parametrize("listen", ["0.0.0.0:0", "[::]:0", "192.0.2.1:0", "[::ffff:127.0.0.1]:0"])
@pytest.mark.parametrize("key", [None, ""])
def test_external_listener_requires_key_before_startup(gateway_binary: Path, listen: str, key: str | None) -> None:
    environment = gateway_environment() | {"PC_GATEWAY_LISTEN": listen}
    if key is not None:
        environment["PC_GATEWAY_API_KEY"] = key
    rejected_gateway(gateway_binary, environment)


@pytest.mark.parametrize("listen", ["127.0.0.1:0", "[::]:0"])
@pytest.mark.parametrize("key", ["   ", " padded", "padded ", "in valid", "中文", "=bad", "bad=tail"])
def test_invalid_key_configuration_is_rejected_without_disclosure(gateway_binary: Path, listen: str, key: str) -> None:
    environment = gateway_environment() | {"PC_GATEWAY_LISTEN": listen, "PC_GATEWAY_API_KEY": key}
    output = rejected_gateway(gateway_binary, environment)
    assert key.encode() not in output


def test_non_unicode_key_configuration_is_rejected(gateway_binary: Path) -> None:
    environment = gateway_environment() | {"PC_GATEWAY_API_KEY": os.fsdecode(b"synthetic-invalid-\xff")}
    assert b"synthetic-invalid-" not in rejected_gateway(gateway_binary, environment)


@contextmanager
def running_gateway(
    binary: Path, environment: dict[str, str], directory: Path, *, host: str = "127.0.0.1"
) -> Iterator[httpx.Client]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as reserved:
        try:
            reserved.bind((host, 0))
        except OSError:
            if family == socket.AF_INET6:
                pytest.skip("IPv6 loopback is unavailable on this host")
            raise
        port = reserved.getsockname()[1]
    listen = f"[{host}]:{port}" if family == socket.AF_INET6 else f"{host}:{port}"
    address = f"127.0.0.1:{port}" if host == "0.0.0.0" else listen  # noqa: S104 - test the external-listener policy
    environment = environment | {"PC_GATEWAY_LISTEN": listen}
    with (directory / "gateway.log").open("w+") as output:
        process = subprocess.Popen([binary], env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=output)
        try:
            with httpx.Client(base_url=f"http://{address}", trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("Gateway did not become ready")
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                yield client
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.23.45.67", "::1"])
def test_loopback_listener_requires_key_before_startup(gateway_binary: Path, host: str) -> None:
    listen = f"[{host}]:0" if ":" in host else f"{host}:0"
    rejected_gateway(gateway_binary, gateway_environment() | {"PC_GATEWAY_LISTEN": listen})


@pytest.mark.parametrize("route", ["chat/completions", "responses"])
@pytest.mark.parametrize("mode", ["disabled", "shadow", "advisory", "enforcing"])
def test_model_ingress_authentication_with_real_core_and_provider(
    gateway_binary: Path, tmp_path: Path, short_tmp_path: Path, route: str, mode: str
) -> None:
    response_body = (
        completion("gateway accepted")
        if route == "chat/completions"
        else json.dumps({
            "id": "resp-auth",
            "object": "response",
            "model": "rfc2086-controlled-model",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "gateway accepted", "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        }).encode()
    )
    with upstream(response_body) as provider:
        endpoint = f"http://127.0.0.1:{provider.server_address[1]}/v1/{route}"
        environment = _environment(tmp_path)
        environment["POWERCONTEXT_SERVER_INFERENCE"] = json.dumps({
            "protection": {
                "mode": mode,
                "policy_version": "gateway-auth.v1",
                "targets": {
                    "auth-test": {
                        "endpoint": endpoint,
                        "model": "rfc2086-controlled-model",
                        "protocol": "openai-chat" if route == "chat/completions" else "openai-responses",
                        "context_window_tokens": 65536,
                        "max_output_tokens": 64,
                        "validated_utf8_token_bound": True,
                    }
                },
            }
        })
        socket_path = short_tmp_path / "core.sock"
        with _running_server(tmp_path, socket_path, environment) as (_, core):
            created = core.post(
                "/v1/scopes",
                json={
                    "title": "Gateway auth",
                    "summary": "Authentication acceptance",
                    "idempotency_key": "gateway-auth",
                },
            )
            assert created.status_code == 201, created.text
            scope = created.json()["scope_id"]
            for role in ("scope.contributor", "gateway.user"):
                granted = core.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": "service", "id": "server-token"},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": role,
                        "idempotency_key": f"gateway-auth-{role}",
                    },
                )
                assert granted.status_code == 201, granted.text
            gateway_env = gateway_environment() | {
                "PC_GATEWAY_CORE_UDS": str(socket_path),
                "PC_GATEWAY_CORE_TOKEN": _TOKEN,
                "PC_GATEWAY_API_KEY": _KEY,
                "PC_GATEWAY_UPSTREAM_API_KEY": _PROVIDER_KEY,
                "PC_GATEWAY_DEFAULT_SCOPE": scope,
                "PC_GATEWAY_UPSTREAM_PROFILE": "auth-test",
                "PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES": "auth-test",
                "PC_GATEWAY_INJECTION": "off",
            }
            payload: dict[str, object] = {"model": "rfc2086-controlled-model", "stream": False}
            payload |= (
                {"messages": [{"role": "user", "content": "Return a safe answer."}], "max_tokens": 64}
                if route == "chat/completions"
                else {
                    "input": [{"role": "user", "content": "Return a safe answer."}],
                    "store": False,
                    "max_output_tokens": 64,
                }
            )
            with running_gateway(gateway_binary, gateway_env, tmp_path, host="0.0.0.0") as client:  # noqa: S104
                path = f"/gateway/v1/{route}"
                for headers in [
                    [],
                    [("Authorization", "")],
                    [("Authorization", "Bearer wrong")],
                    [("Authorization", f"Bearer {_KEY.rstrip('=')}extra")],
                    [("Authorization", f"Bearer {_KEY.swapcase()}")],
                    [("Authorization", "Bearer")],
                    [("Authorization", "Basic abc")],
                    [("Authorization", "Bearer invalid token")],
                    [("Authorization", f"Bearer\t{_KEY}")],
                    [("Authorization", f"Bearer {_KEY}"), ("Authorization", f"Bearer {_KEY}")],
                    [("Authorization", f"Bearer {_KEY}"), ("Authorization", "Bearer wrong")],
                ]:
                    response = client.post(path, headers=headers, json=payload)
                    assert response.status_code == 401, response.text
                    assert response.json() == {"error": "invalid_gateway_key"}
                    assert response.headers["www-authenticate"] == "Bearer"
                    assert provider.received == []
                with socket.create_connection(("127.0.0.1", client.base_url.port), timeout=5) as connection:
                    connection.sendall(
                        (
                            f"POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 512\r\n"
                            "Authorization: Bearer wrong\r\n\r\n"
                        ).encode()
                    )
                    with http.client.HTTPResponse(connection) as rejected:
                        rejected.begin()
                        assert rejected.status == 401
                        assert json.loads(rejected.read()) == {"error": "invalid_gateway_key"}
                    assert provider.received == []
                for scheme in ["Bearer ", "bearer ", "BEARER   "]:
                    response = client.post(path, headers={"Authorization": scheme + _KEY}, json=payload)
                    assert response.status_code == 200, response.text
                    assert "gateway accepted" in response.text
                assert len(provider.received) == 3
                assert provider.authorizations == [f"Bearer {_PROVIDER_KEY}"] * 3
                assert client.get("/v1/capabilities").status_code == 401
                assert client.get("/v1/capabilities", headers={"Authorization": f"Bearer {_KEY}"}).status_code == 401
                assert client.get("/v1/capabilities", headers={"Authorization": f"Bearer {_TOKEN}"}).status_code == 200
