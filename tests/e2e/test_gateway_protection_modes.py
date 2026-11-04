# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Real Core/Gateway processes against a local HTTP provider; no model calls.

Build the optional Gateway with ``cd gateway && cargo build --locked -p pc-gateway-app``.
``PC_TEST_GATEWAY_BINARY`` can select another build of the current checkout.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import uvicorn

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_context_protection_transports import _ADMIN, _Authentication, _settings

_ROOT = Path(__file__).resolve().parents[2]
_MODE_VALUES = ("disabled", "shadow", "advisory", "enforcing")
_RESPONSE = {
    "id": "resp_local_fixture",
    "object": "response",
    "status": "completed",
    "model": "test",
    "output": [
        {
            "id": "msg_local_fixture",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "fixture-upstream-content", "annotations": []}],
        }
    ],
    "image": {"context_compression": "disabled"},
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
}
_IMAGE = {
    "type": "input_image",
    "image_url": "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=",
}


def _mode_settings(directory, mode, endpoint):
    return _settings(directory).model_copy(
        update={
            "inference": InferenceConfig(
                protection=InferenceProtectionConfig(
                    mode=mode,
                    targets={
                        "model": UpstreamProfile(
                            endpoint=endpoint,
                            model="test",
                            protocol="openai-responses",
                            context_window_tokens=65536,
                            max_output_tokens=64,
                            validated_utf8_token_bound=True,
                        )
                    },
                )
            )
        }
    )


def _serve_core(directory, socket_path, mode, endpoint):
    directory = Path(directory)
    app = create_server_app(
        settings=_mode_settings(directory, mode, endpoint),
        authentication_provider=_Authentication(),
        scheduler_path=directory / "scheduler.db",
    )
    uvicorn.run(app, uds=socket_path, log_level="warning", access_log=False)


@contextmanager
def _process(arguments, directory, environment, log_name):
    log_path = directory / log_name
    with log_path.open("w+", encoding="utf-8") as output:
        process = subprocess.Popen(
            arguments,
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            yield process, log_path
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _ready(http, path, process, log_path):
    deadline = time.monotonic() + 30
    while process.poll() is None and time.monotonic() < deadline:
        try:
            if http.get(path).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    pytest.fail(f"Process did not become ready: {log_path.read_text()}")


def _request(*, image=False):
    content = [{"type": "input_text", "text": "Reply using the local test fixture."}]
    if image:
        content.append(_IMAGE)
    return {
        "model": "test",
        "max_output_tokens": 64,
        "store": False,
        "stream": False,
        "input": [{"role": "user", "content": content}],
    }


@pytest.fixture(scope="module", params=_MODE_VALUES)
def mode_core(request):
    if sys.platform == "win32":
        pytest.skip("Requires Unix domain sockets and POSIX child processes")
    mode = request.param
    received = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(_RESPONSE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's keyword-compatible API
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{provider.server_port}/v1/responses"
    try:
        # Short paths are required by macOS's Unix domain socket path limit.
        with tempfile.TemporaryDirectory(prefix="pc-gateway-modes-") as temporary:
            directory = Path(temporary)
            settings = _mode_settings(directory, mode, endpoint)
            prepare_server_database(settings)

            async def bootstrap():
                async with open_builtin_access_control(
                    settings.database,
                    bootstrap_administrators=(_ADMIN,),
                    deployment_id="context-transports",
                ):
                    pass

            asyncio.run(bootstrap())
            socket_path = directory / "core.sock"
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("POWERCONTEXT_SERVER_", "PC_GATEWAY_", "OTEL_", "OPENAI_"))
            }
            environment["PYTHONPATH"] = str(_ROOT)
            runner = "import runpy,sys; runpy.run_path(sys.argv[1])['_serve_core'](*sys.argv[2:])"
            arguments = [sys.executable, "-c", runner, __file__, temporary, str(socket_path), mode, endpoint]
            with (
                _process(arguments, directory, environment, "core.log") as (process, log_path),
                httpx.Client(
                    transport=httpx.HTTPTransport(uds=str(socket_path)),
                    base_url="http://localhost",
                    timeout=5,
                    trust_env=False,
                    headers={"Authorization": "Bearer transport-admin"},
                ) as http,
            ):
                _ready(http, "/health/ready", process, log_path)
                created = http.post(
                    "/v1/scopes",
                    json={"title": "Gateway mode fixture", "summary": "Local processes", "idempotency_key": mode},
                )
                assert created.status_code == 201, created.text
                scope = created.json()["scope_id"]
                granted = http.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": _ADMIN.type, "id": _ADMIN.id},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": "scope.contributor",
                        "idempotency_key": f"mode-{mode}",
                    },
                )
                assert granted.status_code == 201, granted.text
                yield mode, http, scope, socket_path, endpoint, received, directory, environment
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


def test_mode_coverage_survives_real_core_http(mode_core):
    mode, http, scope, *_ = mode_core
    capabilities = http.get("/v1/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    assert capabilities.json()["context_protection"]["enforcing"] is (mode == "enforcing")
    send = http.post(
        "/v1/context/check",
        json={
            "phase": "before_send",
            "scope_id": scope,
            "target_profile": "model",
            "payload": {
                "schema": "powercontext.model-json-payload.v1",
                "headers": {"content-type": "application/json"},
                "body": json.dumps(_request()),
            },
            "prepared_contexts": [],
        },
    )
    assert send.status_code == 200, send.text
    checked = send.json()
    assert checked["decision"] == "allow", checked
    assert checked["binding"]
    assert checked["coverage"] == {
        "profile": "text-and-tools.v1" if mode == "enforcing" else "context-bindings.v1",
        "complete": True,
    }


def test_gateway_releases_images_only_in_non_enforcing_modes(mode_core):
    binary = Path(os.environ.get("PC_TEST_GATEWAY_BINARY", _ROOT / "gateway/target/debug/pc-gateway"))
    if not binary.is_file():
        pytest.skip("Build the current Rust Gateway with: cd gateway && cargo build --locked -p pc-gateway-app")
    mode, _, scope, socket_path, endpoint, received, directory, environment = mode_core
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    environment = environment | {
        "PC_GATEWAY_CORE_UDS": str(socket_path),
        "PC_GATEWAY_CORE_TOKEN": "transport-admin",
        "PC_GATEWAY_LISTEN": f"127.0.0.1:{port}",
        "PC_GATEWAY_UPSTREAM_PROFILE": "model",
        "PC_GATEWAY_UPSTREAM_URL_RESPONSES": endpoint,
        "PC_GATEWAY_UPSTREAM_API_KEY": "local-fixture-only",
        "PC_GATEWAY_DEFAULT_SCOPE": scope,
        "PC_GATEWAY_INJECTION": "off",
        "PC_GATEWAY_SEDIMENT": "off",
        "PC_GATEWAY_MAX_ATTEMPTS": "1",
    }
    with (
        _process([str(binary.resolve())], directory, environment, "gateway.log") as (process, log_path),
        httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False) as http,
    ):
        _ready(http, "/healthz", process, log_path)
        for image in (False, True):
            before = len(received)
            request = _request(image=image)
            response = http.post("/gateway/v1/responses", json=request)
            if mode == "enforcing":
                assert response.status_code == 409, response.text
                assert "fixture-upstream-content" not in response.text
                # Text requests reach the local provider; image inputs are refused before sending.
                assert len(received) == before + (not image)
            else:
                assert response.status_code == 200, response.text
                assert response.json() == _RESPONSE
                assert len(received) == before + 1
                assert received[-1]["input"] == request["input"]
