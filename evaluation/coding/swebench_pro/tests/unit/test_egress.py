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

import json
import socket
import socketserver
import threading
from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest

from powercontext_eval_swebench_pro.egress import (
    API_KEY_MODEL_HOSTS,
    CHATGPT_MODEL_HOSTS,
    PREWARM_HOSTS,
    AllowlistProxy,
    EgressError,
    EgressPolicy,
    model_hosts,
    validated_hosts,
)


class _Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(1024):
            self.request.sendall(data)


@pytest.fixture
def echo_port() -> Iterator[int]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy(echo_port: int) -> Iterator[tuple[AllowlistProxy, int]]:
    instance = AllowlistProxy(ports=frozenset({echo_port}))
    url = instance.start("127.0.0.1")
    port = urlsplit(url).port
    assert port is not None
    yield instance, port
    instance.stop()


def _request(proxy_port: int, request: bytes) -> tuple[str, socket.socket]:
    connection = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    connection.sendall(request)
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = connection.recv(1024)
        if not chunk:
            break
        response += chunk
    return response.split(b"\r\n", 1)[0].decode(), connection


def test_allowed_hosts_are_tunneled_and_others_are_refused(proxy: tuple[AllowlistProxy, int], echo_port: int) -> None:
    instance, port = proxy
    instance.allow("agent", frozenset({"localhost"}))

    status, connection = _request(port, f"CONNECT localhost:{echo_port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    assert status == "HTTP/1.1 200 Connection Established"
    connection.sendall(b"opaque tls bytes")
    assert connection.recv(1024) == b"opaque tls bytes"
    connection.close()

    status, connection = _request(port, b"CONNECT github.com:443 HTTP/1.1\r\n\r\n")
    connection.close()
    assert status == "HTTP/1.1 403 Forbidden"
    status, connection = _request(port, f"CONNECT localhost:{echo_port + 1} HTTP/1.1\r\n\r\n".encode())
    connection.close()
    assert status == "HTTP/1.1 403 Forbidden"
    status, connection = _request(port, b"GET http://github.com/ HTTP/1.1\r\n\r\n")
    connection.close()
    assert status == "HTTP/1.1 405 Method Not Allowed"

    # A refused target that is not a DNS name (here localhost on another port) is grouped, not recorded verbatim.
    assert instance.decisions() == [
        {"phase": "agent", "host": "<invalid>", "allowed": False, "connections": 1},
        {"phase": "agent", "host": "github.com", "allowed": False, "connections": 1},
        {"phase": "agent", "host": "localhost", "allowed": True, "connections": 1},
    ]


def test_switching_phase_changes_what_new_connections_reach(proxy: tuple[AllowlistProxy, int], echo_port: int) -> None:
    instance, port = proxy
    request = f"CONNECT localhost:{echo_port} HTTP/1.1\r\n\r\n".encode()
    instance.allow("prewarm", frozenset({"localhost"}))
    status, connection = _request(port, request)
    connection.close()
    assert status.endswith("200 Connection Established")
    instance.allow("agent", frozenset({"api.openai.com"}))
    status, connection = _request(port, request)
    connection.close()
    assert status.endswith("403 Forbidden")
    assert [(item["phase"], item["allowed"]) for item in instance.decisions()] == [
        ("agent", False),
        ("prewarm", True),
    ]


def test_a_new_proxy_refuses_everything_until_a_phase_is_allowed(proxy: tuple[AllowlistProxy, int]) -> None:
    _, port = proxy
    status, connection = _request(port, b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
    connection.close()
    assert status.endswith("403 Forbidden")


@pytest.mark.parametrize("host", ["10.0.0.1", "*.github.com", "localhost", "", "exa mple.com"])
def test_policies_accept_dns_names_only(host: str) -> None:
    with pytest.raises(EgressError):
        validated_hosts([host])
    with pytest.raises(EgressError):
        EgressPolicy(agent=frozenset({host}))


def test_default_policy_keeps_package_indexes_out_of_the_agent_phase() -> None:
    policy = EgressPolicy()
    assert policy.prewarm == PREWARM_HOSTS
    assert policy.agent == {"api.openai.com"}
    assert not policy.prewarm & policy.agent
    assert validated_hosts(["API.OpenAI.com "]) == {"api.openai.com"}


@pytest.mark.parametrize(
    ("auth", "hosts"),
    [
        ({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test"}, API_KEY_MODEL_HOSTS),
        ({"auth_mode": "chatgpt", "tokens": {"access_token": "token"}}, CHATGPT_MODEL_HOSTS),
        # Files written before auth_mode existed.
        ({"OPENAI_API_KEY": "sk-test"}, API_KEY_MODEL_HOSTS),
        ({"OPENAI_API_KEY": None, "tokens": {"access_token": "token"}}, CHATGPT_MODEL_HOSTS),
    ],
)
def test_model_hosts_follow_the_codex_login(tmp_path, auth: dict[str, object], hosts: frozenset[str]) -> None:
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    assert model_hosts(path) == hosts


def test_an_unreadable_auth_file_is_an_error(tmp_path) -> None:
    with pytest.raises(EgressError):
        model_hosts(tmp_path / "missing.json")
