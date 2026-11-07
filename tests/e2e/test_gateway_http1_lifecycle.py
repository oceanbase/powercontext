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

"""Spawned Rust Gateway + actual Python Core UDS + controlled local provider.

Core startup/build failures fail acceptance; this suite never substitutes a mock Core.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread

import httpx
import pytest

from tests.e2e.test_server_uds import _HEADERS, _TOKEN, _environment, _running_server

_ROOT = Path(__file__).resolve().parents[2]
_KEY = "disposable-gateway-lifecycle-key"


@pytest.fixture(scope="module")
def gateway_binary():
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    subprocess.run(
        [
            cargo,
            "build",
            "--locked",
            "--manifest-path",
            str(_ROOT / "gateway/Cargo.toml"),
            "--target-dir",
            str(_ROOT / "gateway/target"),
            "-p",
            "pc-gateway-app",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return _ROOT / "gateway/target/debug/pc-gateway"


class _Provider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, protocol):
        super().__init__(("127.0.0.1", 0), _ProviderHandler)
        self.protocol = protocol
        self.lock = Lock()
        self.sends = 0
        self.hold = False
        self.hold_timeout = 15
        self.received = Event()
        self.release = Event()
        self.finished = Event()


class _ProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib override signature
        pass

    def do_POST(self):
        provider = self.server
        assert isinstance(provider, _Provider)
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert request["model"] == "test"
        streaming = request["stream"]
        assert provider.protocol == "openai-chat" or streaming is False
        with provider.lock:
            provider.sends += 1
            hold = provider.hold
        if hold:
            provider.received.set()
            assert provider.release.wait(provider.hold_timeout), "test did not release the remote provider"
        body = json.dumps(
            {
                "model": "test",
                "choices": [{"message": {"role": "assistant", "content": "已检查"}, "finish_reason": "stop"}],
            }
            if provider.protocol == "openai-chat"
            else {
                "id": "resp-local",
                "object": "response",
                "model": "test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg-local",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "已检查", "annotations": []}],
                    }
                ],
            },
            ensure_ascii=False,
        ).encode()
        if streaming:
            body = (
                "data: "
                + json.dumps(
                    {
                        "model": "test",
                        "choices": [
                            {"index": 0, "delta": {"role": "assistant", "content": "已检查"}, "finish_reason": None}
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n\ndata: "
                + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                + "\n\ndata: [DONE]\n\n"
            ).encode()
        try:
            self.send_response(200 if streaming or provider.protocol == "openai-responses" else 201)
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Local cancellation does not claim provider rollback.
        finally:
            if hold:
                provider.finished.set()


@contextmanager
def _gateway(binary, directory, uds, upstream, scope):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        address = reserved.getsockname()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PC_GATEWAY_")}
    environment.update({
        "PC_GATEWAY_LISTEN": f"{address[0]}:{address[1]}",
        "PC_GATEWAY_CORE_UDS": str(uds),
        "PC_GATEWAY_CORE_TOKEN": _TOKEN,
        "PC_GATEWAY_UPSTREAM_URL": upstream,
        "PC_GATEWAY_UPSTREAM_URL_RESPONSES": upstream,
        "PC_GATEWAY_UPSTREAM_PROFILE": "model",
        "PC_GATEWAY_DEFAULT_SCOPE": scope,
        "PC_GATEWAY_API_KEY": _KEY,
        "PC_GATEWAY_INJECTION": "off",
        "PC_GATEWAY_MAX_CONCURRENT": "1",
    })
    log = directory / "gateway.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [str(binary)], env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT
        )
        try:
            with httpx.Client(base_url=f"http://{address[0]}:{address[1]}", trust_env=False, timeout=5) as client:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None and time.monotonic() < deadline, log.read_text()
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.02)
                yield address, client, process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def _raw_request(address, path, body, *, incomplete=False):
    payload = json.dumps(body, ensure_ascii=False).encode()
    connection = socket.create_connection(address, timeout=5)
    connection.sendall(
        f"POST {path} HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer {_KEY}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
        + (payload[:1] if incomplete else payload)
    )
    return connection


def _recovered(client, path, body):
    deadline = time.monotonic() + 5
    while True:
        response = client.post(path, headers={"Authorization": f"Bearer {_KEY}"}, json=body)
        if response.status_code != 503 or response.json().get("error") != "concurrency_limit":
            return response
        assert time.monotonic() < deadline, response.text
        time.sleep(0.01)


@pytest.mark.parametrize("protocol", ["openai-chat", "openai-responses"])
def test_real_core_uds_model_disconnect_delivery_and_fallback(tmp_path, short_tmp_path, gateway_binary, protocol):
    provider = _Provider(protocol)
    success_status = 201 if protocol == "openai-chat" else 200
    thread = Thread(target=provider.serve_forever)
    thread.start()
    path = "/gateway/v1/chat/completions" if protocol == "openai-chat" else "/gateway/v1/responses"
    body = (
        {"model": "test", "max_tokens": 64, "messages": [{"role": "user", "content": "hello"}]}
        if protocol == "openai-chat"
        else {
            "model": "test",
            "max_output_tokens": 64,
            "input": [{"role": "user", "content": "hello"}],
            "store": False,
        }
    )
    endpoint = f"http://127.0.0.1:{provider.server_port}/provider"
    environment = _environment(tmp_path)
    environment["POWERCONTEXT_SERVER_INFERENCE"] = json.dumps({
        "protection": {
            "mode": "enforcing",
            "targets": {
                "model": {
                    "endpoint": endpoint,
                    "model": "test",
                    "protocol": protocol,
                    "context_window_tokens": 65536,
                    "max_output_tokens": 64,
                    "validated_utf8_token_bound": True,
                    "allow_streaming": protocol == "openai-chat",
                }
            },
        }
    })
    uds = short_tmp_path / "core.sock"
    try:
        with _running_server(tmp_path, uds, environment) as (_, core):
            created = core.post(
                "/v1/scopes",
                json={
                    "title": "Gateway lifecycle",
                    "summary": "HTTP/1 lifecycle acceptance",
                    "idempotency_key": "gateway-lifecycle",
                },
            )
            assert created.status_code == 201, created.text
            granted = core.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": "service", "id": "server-token"},
                    "resource": {"type": "scope", "scope_id": created.json()["scope_id"]},
                    "role": "scope.contributor",
                    "idempotency_key": "gateway-contributor",
                },
            )
            assert granted.status_code == 201, granted.text
            with _gateway(gateway_binary, tmp_path, uds, endpoint, created.json()["scope_id"]) as (
                address,
                client,
                _process,
            ):
                # Responses replay checked JSON as SSE. Public Core's JSON
                # carrier still withholds native Chat SSE; preserve that boundary.
                for stream in [False, True]:
                    response = client.post(
                        path, headers={"Authorization": f"Bearer {_KEY}"}, json=body | {"stream": stream}
                    )
                    if stream and protocol == "openai-chat":
                        assert response.status_code == 409, response.text
                        assert response.json() == {"decision": "withhold", "reason_codes": ["carrier_unsupported"]}
                        assert "已检查" not in response.text
                        assert response.headers["connection"] == "close"
                        continue
                    assert response.status_code == success_status, response.text
                    assert response.headers["connection"] == "close"
                    assert response.headers["content-type"].startswith(
                        "text/event-stream" if stream else "application/json"
                    )
                    assert "已检查" in response.text
                    if stream:
                        assert (
                            "data: [DONE]" in response.text
                            if protocol == "openai-chat"
                            else "event: response.completed" in response.text
                        )
                initial = provider.sends
                assert initial == 2
                assert client.post(path, json=body).status_code == 401
                # Incomplete body disconnect cannot authorize or send a model request.
                _raw_request(address, path, body, incomplete=True).close()
                response = _recovered(client, path, body)
                assert response.status_code == success_status, response.text
                assert provider.sends == initial + 1
                # Receiver confirms complete upstream request before the caller disconnects.
                with provider.lock:
                    provider.hold = True
                connection = _raw_request(address, path, body)
                assert provider.received.wait(5)
                refused = client.post(path, headers={"Authorization": f"Bearer {_KEY}"}, json=body)
                assert refused.status_code == 503 and refused.json()["error"] == "concurrency_limit"
                connection.close()
                with provider.lock:
                    provider.hold = False
                response = _recovered(client, path, body)
                assert response.status_code == success_status, response.text
                assert provider.sends == initial + 3  # cancelled send + recovery, no replay
                provider.release.set()
                assert provider.finished.wait(5)
                # Model connection closure leaves Core pools and authenticated fallback usable.
                assert client.get("/v1/capabilities").status_code == 401
                management = http.client.HTTPConnection(*address, timeout=5)
                try:
                    management.request("GET", "/v1/capabilities", headers=_HEADERS)
                    response = management.getresponse()
                    assert response.status == 200
                    assert response.getheader("Connection") != "close"
                    response.read()
                    assert management.sock is not None
                    local = management.sock.getsockname()
                    # Idle keep-alive does not keep the independent fallback quota occupied.
                    assert client.get("/v1/capabilities", headers=_HEADERS).status_code == 200
                    management.request("GET", "/v1/capabilities", headers=_HEADERS)
                    response = management.getresponse()
                    assert response.status == 200
                    response.read()
                    assert management.sock is not None and management.sock.getsockname() == local
                finally:
                    management.close()
    finally:
        provider.release.set()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _pending_shutdown_request(address, mode, process, provider, body, path):
    if mode == "fallback":
        # Actual Core is stateless MCP: GET SSE is intentionally 405. Hold a
        # supported authenticated POST in body intake instead. A separate Rust
        # process test uses the controlled UDS receiver's indefinitely open SSE.
        connection = socket.create_connection(address, timeout=32)
        connection.sendall(
            f"POST /mcp/ HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer {_TOKEN}\r\n"
            "Accept: application/json, text/event-stream\r\nContent-Type: application/json\r\n"
            "Content-Length: 4096\r\n\r\n{".encode()
        )
        connection.settimeout(0.1)
        with pytest.raises(TimeoutError):
            connection.recv(1)
        connection.settimeout(32)
    else:
        connection = _raw_request(address, path, body, incomplete=mode in {"unsent", "earlier_request"})
        connection.settimeout(32)
        if mode == "earlier_request":
            # Real 180s ingress budget: consume 170s before the first
            # provider attempt, leaving less than the 25s drain window.
            intake = time.monotonic() + 170
            while time.monotonic() < intake:
                assert process.poll() is None and provider.sends == 0
                time.sleep(0.05)
            connection.sendall(json.dumps(body, ensure_ascii=False).encode()[1:])
        if mode != "unsent":
            assert provider.received.wait(5), "provider did not receive the model request"
        else:
            assert provider.sends == 0
    return connection


def _signal_and_verify_closed_admission(process, address, bypass, body, path):
    # A TCP connection established before shutdown cannot start new
    # model work after the listener and admission have closed.
    started = time.monotonic()
    process.send_signal(signal.SIGTERM)
    while True:
        try:
            probe = socket.create_connection(address, timeout=0.1)
        except OSError:
            break
        else:
            probe.close()
        assert time.monotonic() - started < 2, "listener remained open"
        time.sleep(0.01)
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    try:
        payload = json.dumps(body).encode()
        bypass.sendall(
            f"POST {path} HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer {_KEY}\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n".encode()
            + payload
        )
        assert bypass.recv(1024) == b""
    except (BrokenPipeError, ConnectionResetError):
        pass
    return started


@pytest.mark.parametrize("mode", ["normal", "unsent", "expiry", "fallback", "earlier_request"])
def test_real_core_uds_staged_shutdown(tmp_path, short_tmp_path, gateway_binary, mode):
    provider = _Provider("openai-chat")
    provider.hold = True
    provider.hold_timeout = 40
    thread = Thread(target=provider.serve_forever)
    thread.start()
    endpoint = f"http://127.0.0.1:{provider.server_port}/provider"
    environment = _environment(tmp_path)
    environment["POWERCONTEXT_SERVER_MCP_ENABLED"] = "true"
    environment["POWERCONTEXT_SERVER_INFERENCE"] = json.dumps({
        "protection": {
            "mode": "enforcing",
            "targets": {
                "model": {
                    "endpoint": endpoint,
                    "model": "test",
                    "protocol": "openai-chat",
                    "context_window_tokens": 65536,
                    "max_output_tokens": 64,
                    "validated_utf8_token_bound": True,
                }
            },
        }
    })
    body = {"model": "test", "max_tokens": 64, "messages": [{"role": "user", "content": "hello"}]}
    path = "/gateway/v1/chat/completions"
    connection = None
    bypass = None
    try:
        with _running_server(tmp_path, short_tmp_path / "core.sock", environment) as (_, core):
            created = core.post(
                "/v1/scopes",
                json={
                    "title": "Staged shutdown",
                    "summary": "Real process shutdown",
                    "idempotency_key": "shutdown",
                },
            )
            assert created.status_code == 201, created.text
            scope = created.json()["scope_id"]
            granted = core.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": "service", "id": "server-token"},
                    "resource": {"type": "scope", "scope_id": scope},
                    "role": "scope.contributor",
                    "idempotency_key": "shutdown-contributor",
                },
            )
            assert granted.status_code == 201, granted.text
            with _gateway(gateway_binary, tmp_path, short_tmp_path / "core.sock", endpoint, scope) as (
                address,
                client,
                process,
            ):
                # Core authentication remains authoritative through the proxy.
                assert client.get("/v1/capabilities").status_code == 401
                assert client.get("/v1/capabilities", headers=_HEADERS).status_code == 200
                request_started = time.monotonic()
                connection = _pending_shutdown_request(address, mode, process, provider, body, path)
                bypass = socket.create_connection(address, timeout=2)
                started = _signal_and_verify_closed_admission(process, address, bypass, body, path)
                if mode == "normal":
                    assert process.poll() is None, "handed-off model call must keep draining"
                    provider.release.set()
                    assert isinstance(connection, socket.socket)
                    response = http.client.HTTPResponse(connection)
                    response.begin()
                    assert response.status == 201
                    assert "已检查" in response.read().decode()
                # Actual process exit is the evidence; _gateway's final kill is
                # solely cleanup if an assertion or this bounded wait fails.
                assert process.wait(timeout=max(0.1, 32 - (time.monotonic() - started))) == 0
                finished = time.monotonic()
                elapsed = finished - started
                assert elapsed < 32, "30s total budget plus 2s scheduler/measurement tolerance"
                if mode in {"expiry", "fallback"}:
                    assert 24.9 <= elapsed <= 27, elapsed
                elif mode == "earlier_request":
                    # Body completion, Core admission and scheduling before the
                    # signal also consume the original 180s request budget.
                    # Allow 0.1s measurement and 3s scheduling tolerance.
                    request_elapsed = finished - request_started
                    assert 179.9 <= request_elapsed <= 183, request_elapsed
                    assert elapsed < 25, "original request deadline must win over drain"
                else:
                    assert elapsed < 3, elapsed
                assert provider.sends == int(mode in {"normal", "expiry", "earlier_request"})
                # Rust shutdown does not own the Python Core/database lifecycle.
                assert core.get("/health/ready").status_code == 200
                print(f"linux HTTP/1 Core-UDS mode={mode} budget=25/30s exit=0 elapsed={elapsed:.3f}s")
    finally:
        if connection is not None:
            connection.close()
        if bypass is not None:
            bypass.close()
        provider.release.set()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
