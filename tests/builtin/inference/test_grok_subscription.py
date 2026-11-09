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

from __future__ import annotations

import asyncio
import json
import multiprocessing
import threading
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import httpx2
import pytest
from pydantic import AnyHttpUrl, SecretStr
from pydantic_ai.exceptions import UserError

from powercontext.builtin.inference import grok
from powercontext.builtin.inference.privacy import InferenceProtectionConfig
from powercontext.builtin.runtime import BuiltinConfigurationError
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model

_CONTRACT = json.loads((Path(__file__).parents[2] / "fixtures/oauth/contract.json").read_text())
_EXPIRED = next(case for case in _CONTRACT["scenarios"] if case["id"] == "grok-cli-expired-rotate")
_SCOPE = _CONTRACT["profiles"]["grok-cli"]["credential_scope"]


def _worker(path: Path, endpoint: str, connection) -> None:
    """Use real HTTP and the real shared-file auth; only redirect the test's wire destination."""

    async def run() -> None:
        async with httpx.AsyncClient(trust_env=False) as wire:

            async def token_exchange(request: httpx.Request) -> httpx.Response:
                assert str(request.url) == "https://auth.x.ai/oauth2/token"
                return await wire.post(endpoint + "/oauth2/token", content=request.content, headers=request.headers)

            async def resource(request: httpx2.Request) -> httpx2.Response:
                response = await wire.post(endpoint + "/v1/responses", content=request.content, headers=request.headers)
                return httpx2.Response(response.status_code, content=response.content)

            with patch.object(
                grok,
                "AsyncOAuth2Client",
                partial(grok.AsyncOAuth2Client, transport=httpx.MockTransport(token_exchange)),
            ):
                async with httpx2.AsyncClient(
                    auth=grok.GrokCliAuth(path, clock=lambda: _EXPIRED["now"]), transport=httpx2.MockTransport(resource)
                ) as client:
                    connection.send("ready")
                    assert connection.recv() == "request"
                    response = await client.post("https://api.x.ai/v1/responses", json={"input": "synthetic"})
                    connection.send(response.status_code)

    try:
        asyncio.run(run())
    finally:
        connection.close()


class _OAuthServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _OAuthHandler)
        self.forms: list[dict[str, list[str]]] = []
        self.access_tokens: list[str | None] = []


class _OAuthHandler(BaseHTTPRequestHandler):
    server: _OAuthServer

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/oauth2/token":
            form = parse_qs(body.decode())
            self.server.forms.append(form)
            assert not self.headers.get("Authorization")
            expected = "synthetic-refresh" if len(self.server.forms) == 1 else "synthetic-rotated"
            if form["refresh_token"] != [expected]:
                self.send_error(400)
                return
            response = {"access_token": "synthetic-renewed", "refresh_token": "synthetic-rotated", "expires_in": 3600}
        elif self.path == "/v1/responses":
            self.server.access_tokens.append(self.headers.get("Authorization"))
            response = {"ok": True}
        else:
            self.send_error(404)
            return
        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def test_grok_processes_share_refresh_and_restart_uses_persisted_rotation(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(_EXPIRED["credentials"]))
    server = _OAuthServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = multiprocessing.get_context("spawn")
    workers = []
    endpoint = f"http://127.0.0.1:{server.server_port}"

    def start():
        parent, child = context.Pipe()
        process = context.Process(target=_worker, args=(path, endpoint, child))
        process.start()
        child.close()
        workers.append((process, parent))
        assert parent.poll(30), "worker did not start"
        assert parent.recv() == "ready"
        return process, parent

    def finish(process, parent):
        assert parent.poll(30), "worker did not finish"
        assert parent.recv() == 200
        process.join(10)
        assert process.exitcode == 0

    try:
        first, second = start(), start()
        for _, parent in (first, second):
            parent.send("request")
        for process, parent in (first, second):
            finish(process, parent)
        assert len(server.forms) == 1
        saved = json.loads(path.read_text())
        assert saved[_SCOPE]["refresh_token"] == "synthetic-rotated"  # noqa: S105 - synthetic credential
        assert saved["unrelated-provider"] == _EXPIRED["credentials"]["unrelated-provider"]
        # Force expiry through the supported disk format, then start a fresh process.
        saved[_SCOPE]["expires_at"] = datetime.fromtimestamp(0, UTC).isoformat()
        path.write_text(json.dumps(saved))
        process, parent = start()
        parent.send("request")
        finish(process, parent)
        assert len(server.forms) == 2
        assert server.forms[1]["refresh_token"] == ["synthetic-rotated"]
        assert server.access_tokens == ["Bearer synthetic-renewed"] * 3
    finally:
        for process, parent in workers:
            if process.is_alive():
                process.terminate()
                process.join(10)
            parent.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_grok_runtime_model_uses_oauth_responses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai import Agent

    path = tmp_path / "auth.json"
    credentials = json.loads(json.dumps(_EXPIRED["credentials"]))
    credentials[_SCOPE]["expires_at"] = "2099-01-01T00:00:00+00:00"
    path.write_text(json.dumps(credentials))
    monkeypatch.setenv("GROK_AUTH_PATH", str(path))
    monkeypatch.setenv("XAI_API_KEY", "api-key-must-not-replace-cli-oauth")
    requests = []

    def response(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "resp_synthetic",
                "object": "response",
                "created_at": 1,
                "model": "grok-test",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_synthetic",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "synthetic-result", "annotations": []}],
                    }
                ],
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    class Client(httpx2.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx2.MockTransport(response))

    monkeypatch.setattr(httpx2, "AsyncClient", Client)

    async def run() -> None:
        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                "grok:grok-test",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
            )
            assert (await Agent(model).run("synthetic-input")).output == "synthetic-result"

    asyncio.run(run())
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.x.ai/v1/responses"
    assert requests[0].headers["authorization"] == "Bearer synthetic-access"
    assert json.loads(requests[0].content)["model"] == "grok-test"
    assert json.loads(path.read_text()) == credentials


@pytest.mark.parametrize("option", ["base_url", "headers", "protection"])
def test_grok_rejects_unsupported_endpoint_configuration(option: str, tmp_path: Path, monkeypatch) -> None:
    # Reject configuration before trying any real CLI store or paid model endpoint.
    monkeypatch.setenv("GROK_AUTH_PATH", str(tmp_path / "not-a-login"))

    async def run() -> None:
        async with AsyncExitStack() as resources:
            with pytest.raises(BuiltinConfigurationError, match="custom inference endpoints"):
                await _open_pydantic_ai_model(
                    "grok:grok-test",
                    resources=resources,
                    instrumentation=None,
                    base_url=AnyHttpUrl("https://attacker.test/v1") if option == "base_url" else None,
                    headers={"Authorization": SecretStr("synthetic")} if option == "headers" else {},
                    protection=InferenceProtectionConfig(mode="enforcing") if option == "protection" else None,
                )

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["token_response", "resource_response"])
def test_grok_model_does_not_replay_failed_exchanges(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError

    path = tmp_path / "auth.json"
    path.write_text(json.dumps(_EXPIRED["credentials"]))
    original = path.read_bytes()
    monkeypatch.setenv("GROK_AUTH_PATH", str(path))
    token_requests = []
    resource_requests = []

    def token_response(request: httpx.Request) -> httpx.Response:
        token_requests.append(request)
        if failure == "token_response":
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "synthetic-secret-error"})
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-renewed",
                "refresh_token": "synthetic-rotated",
                "expires_in": 3600,
            },
        )

    def resource_response(request: httpx2.Request) -> httpx2.Response:
        resource_requests.append(request)
        return httpx2.Response(500, json={"error": {"message": "synthetic failure"}})

    class Client(httpx2.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx2.MockTransport(resource_response))

    monkeypatch.setattr(httpx2, "AsyncClient", Client)
    monkeypatch.setattr(
        grok, "AsyncOAuth2Client", partial(grok.AsyncOAuth2Client, transport=httpx.MockTransport(token_response))
    )

    async def run() -> None:
        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                "grok:grok-test",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
            )
            with pytest.raises(UserError if failure == "token_response" else ModelHTTPError):
                await Agent(model).run("synthetic-input")

    asyncio.run(run())
    assert len(token_requests) == 1
    assert len(resource_requests) == (0 if failure == "token_response" else 1)
    if failure == "token_response":
        assert path.read_bytes() == original
