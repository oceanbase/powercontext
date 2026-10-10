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

"""Shared network transport-policy contract across Client, CLI, Server, and the agent plugins."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import ClassVar

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from powercontext.client import PowerContextClient
from powercontext.client.settings import ClientSettings
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    HttpConfig,
    ServerSettings,
)
from powercontext.transport import canonical_loopback_endpoint, is_loopback_host, is_plaintext_non_loopback

_ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - a non-loopback bind used to exercise the policy.

# Loopback host vectors shared with the TypeScript plugins' own drift guards (e.g. the Pi plugin's
# transport-policy.spec.ts). Keeping one JSON source of truth means a plugin that drifts from the
# 127.0.0.0/8 loopback contract fails here or in that plugin's suite -- never silently in both.
_LOOPBACK_VECTORS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "transport_loopback_vectors.json").read_text(encoding="utf-8"),
)
_SHARED_LOOPBACK_HOSTS: list[str] = _LOOPBACK_VECTORS["loopback"]
_SHARED_NON_LOOPBACK_HOSTS: list[str] = _LOOPBACK_VECTORS["non_loopback"]


class _HealthHandler(BaseHTTPRequestHandler):
    requested_paths: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.requested_paths.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format: str, *_args: object) -> None:  # noqa: A002
        return None


def _serve_health() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


# The Python plugins (Codex, Claude Code) ship isolated (they do not depend on powercontext) and
# each vendors its own copy of the loopback policy. Load every copy by path and pin it to the shared
# contract so drift in any one implementation is caught. Loading is defensive: a moved path or a
# plugin import error skips that plugin's drift guard rather than failing collection for the whole
# module. The TypeScript plugins are pinned by their own Vitest suites against the same vectors.
_INTEGRATIONS = Path(__file__).resolve().parent.parent / "integrations"
_VENDORED_PLUGIN_PATHS = {
    "codex": _INTEGRATIONS / "codex" / "plugins" / "powercontext" / "settings.py",
    "claude-code": _INTEGRATIONS / "claude-code" / "plugins" / "powercontext" / "claude_code_settings.py",
}


def _load_plugin_module(name: str, path: Path) -> tuple[ModuleType | None, str]:
    module_name = f"vendored_plugin_{name}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: a slotted dataclass (the Claude Code plugin) resolves its own
    # module via sys.modules during class creation and fails to import otherwise.
    sys.modules[module_name] = module
    previous_path = sys.path.copy()
    try:
        sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(module)
    except Exception as error:  # pragma: no cover - exercised only when a plugin is unavailable.
        sys.modules.pop(module_name, None)
        return None, repr(error)
    finally:
        sys.path[:] = previous_path
    return module, ""


_VENDORED_PLUGINS = {name: _load_plugin_module(name, path) for name, path in _VENDORED_PLUGIN_PATHS.items()}
_VENDORED_PLUGIN_PARAMS = [
    pytest.param(
        module,
        id=name,
        marks=pytest.mark.skipif(module is None, reason=f"{name} plugin unavailable: {error}"),
    )
    for name, (module, error) in _VENDORED_PLUGINS.items()
]


# The shared vectors carry only valid URL authorities so both languages can build them into a URL;
# `"::1"` (unbracketed) plus the falsy inputs are Python-only edge cases the shared set cannot express.
@pytest.mark.parametrize("host", [*_SHARED_LOOPBACK_HOSTS, "::1"])
def test_loopback_hosts_are_recognized(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", [*_SHARED_NON_LOOPBACK_HOSTS, "::", "", None])
def test_non_loopback_hosts_are_rejected(host: str | None) -> None:
    assert not is_loopback_host(host)


def test_plaintext_non_loopback_detects_only_remote_http() -> None:
    assert is_plaintext_non_loopback("http://memory.example")
    assert not is_plaintext_non_loopback("http://127.0.0.1:8000")
    assert not is_plaintext_non_loopback("https://memory.example")


@pytest.mark.parametrize(
    "endpoint",
    [
        "HTTP://localhost:8000/",
        "http://127.0.0.1:8000",
        "http://127.0.0.2:8000///",
        "http://[::1]:8000/",
    ],
)
def test_canonical_service_endpoint_normalizes_loopback_hosts(endpoint: str) -> None:
    assert canonical_loopback_endpoint(endpoint) == "http://loopback:8000"


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://localhost", "http://loopback:80"),
        ("https://[::1]/", "https://loopback:443"),
        ("http://memory.example:8000", None),
        ("ftp://127.0.0.1:8000", None),
        ("http://127.0.0.1:not-a-port", None),
    ],
)
def test_canonical_service_endpoint_applies_default_ports_and_rejects_invalid_targets(
    endpoint: str,
    expected: str | None,
) -> None:
    assert canonical_loopback_endpoint(endpoint) == expected


@pytest.mark.parametrize(
    "server_url",
    ["http://memory.example", "http://192.168.1.10:8000"],
)
def test_client_settings_reject_non_loopback_plaintext_urls(server_url: str) -> None:
    with pytest.raises(ValidationError):
        ClientSettings(server_url=server_url)


@pytest.mark.parametrize(
    "server_url",
    ["http://127.0.0.1:8000", "http://localhost:8000", "https://memory.example"],
)
def test_client_settings_accept_loopback_or_tls_urls(server_url: str) -> None:
    assert ClientSettings(server_url=server_url).server_url.startswith(("http://", "https://"))


def test_client_refuses_requests_over_non_loopback_plaintext() -> None:
    # The public constructor is a transport surface in its own right: even without a bearer token the
    # request body carries Memory content, so a plaintext non-loopback base URL must be refused. This
    # is the ``capture_content_source`` probe from the #1319 acceptance -- the facade must not open
    # such a transport itself.
    with pytest.raises(ValueError, match="non-loopback"):
        PowerContextClient("http://memory.example")


def test_client_refuses_a_bearer_token_over_non_loopback_plaintext() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"}))
    with httpx.Client(transport=transport):  # noqa: SIM117 - guard runs before any request.
        with pytest.raises(ValueError, match="non-loopback"):
            PowerContextClient("http://memory.example", token="probe-token")  # noqa: S106 - test credential.


def test_client_allows_an_explicit_remote_receiver_plaintext_exception() -> None:
    client = PowerContextClient(
        "http://memory.example",
        token="probe-token",  # noqa: S106 - test credential.
        allow_insecure_http=True,
    )
    assert client is not None


def test_client_allows_a_bearer_token_over_loopback_plaintext() -> None:
    client = PowerContextClient("http://127.0.0.1:8000", token="probe-token")  # noqa: S106 - test credential.
    assert client is not None


def test_owned_client_bypasses_process_proxy_for_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    server, thread, endpoint = _serve_health()
    try:

        async def request() -> str:
            async with PowerContextClient(endpoint, timeout=1) as client:
                return (await client.get_liveness()).status

        assert asyncio.run(request()) == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_owned_client_preserves_custom_ca_for_loopback_https(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    cert = Path(__file__).resolve().parent / "fixtures" / "loopback_test_ca.pem"
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        async def request() -> str:
            async with PowerContextClient(f"https://{host}:{server.server_port}", timeout=1) as client:
                return (await client.get_liveness()).status

        assert asyncio.run(request()) == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_owned_client_keeps_process_proxy_for_remote_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    _HealthHandler.requested_paths = []
    proxy, thread, proxy_url = _serve_health()
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("http_proxy", proxy_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    try:

        async def request() -> str:
            async with PowerContextClient("http://memory.example", allow_insecure_http=True, timeout=1) as client:
                return (await client.get_liveness()).status

        assert asyncio.run(request()) == "ok"
        assert _HealthHandler.requested_paths == ["http://memory.example/health/live"]
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)


def test_client_allows_a_trusted_caller_supplied_transport() -> None:
    # A caller-supplied http_client may own a transport whose ``http://`` label is only a routing
    # token (here an in-process ASGI/mock transport). The caller must vouch for it explicitly with
    # ``trust_transport_security`` before the plaintext guard stands down; this covers the
    # authenticated e2e flow.
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})),
    )
    client = PowerContextClient(
        "http://testserver",
        token="probe-token",  # noqa: S106 - test credential.
        http_client=http_client,
        trust_transport_security=True,
    )
    assert client is not None


def test_client_refuses_a_bearer_token_over_an_untrusted_caller_supplied_transport() -> None:
    # Supplying an http_client is not evidence of safety: a shared pooling client (as the LangGraph
    # adapter installs) points at a real network. Without an explicit trust opt-in the guard must
    # still refuse to leak the token over non-loopback plaintext.
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})),
    )
    with pytest.raises(ValueError, match="non-loopback"):
        PowerContextClient(
            "http://memory.example",
            token="probe-token",  # noqa: S106 - test credential.
            http_client=http_client,
        )


def test_client_refuses_an_unauthenticated_untrusted_non_loopback_transport() -> None:
    # The guard is not gated on a token: an untrusted caller-supplied transport to a non-loopback
    # plaintext host must be refused even with no credentials, because the Memory content in the body
    # still crosses the wire in the clear.
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"}))
    )
    with pytest.raises(ValueError, match="non-loopback"):
        PowerContextClient("http://memory.example", http_client=http_client)


@pytest.mark.parametrize("dashboard", [False, True])
def test_server_rejects_an_unauthenticated_non_loopback_bind(dashboard: bool) -> None:
    with pytest.raises(ValidationError):
        ServerSettings(
            http=HttpConfig(host=_ALL_INTERFACES),
            dashboard=DashboardConfig(enabled=dashboard),
            auth=BearerAuthConfig(),
        )


def test_server_allows_a_non_loopback_bind_with_authentication() -> None:
    settings = ServerSettings(
        http=HttpConfig(host=_ALL_INTERFACES),
        auth=BearerAuthConfig(token=SecretStr("server-secret")),
        access=AccessControlConfig(mode="enforced"),
    )
    assert settings.http.host == _ALL_INTERFACES


def test_server_allows_a_non_loopback_bind_with_an_explicit_opt_in() -> None:
    settings = ServerSettings(
        http=HttpConfig(host=_ALL_INTERFACES),
        auth=BearerAuthConfig(),
        allow_unauthenticated_non_loopback=True,
    )
    assert settings.allow_unauthenticated_non_loopback is True


@pytest.mark.parametrize("plugin", _VENDORED_PLUGIN_PARAMS)
@pytest.mark.parametrize("host", [*_SHARED_LOOPBACK_HOSTS, *_SHARED_NON_LOOPBACK_HOSTS])
def test_vendored_plugin_matches_the_shared_plaintext_policy(plugin: ModuleType, host: str) -> None:
    """Each plugin's vendored loopback check must agree with the shared transport contract."""

    base_url = f"http://{host}:8000"
    transport_rejects = is_plaintext_non_loopback(base_url)
    try:
        plugin._http_base_url(f"{base_url}/mcp")
        plugin_rejects = False
    except ValueError:
        plugin_rejects = True
    assert plugin_rejects == transport_rejects
