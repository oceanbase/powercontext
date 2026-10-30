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
import socket
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from powercontext.builtin.authz import AccessAction
from powercontext.builtin.authz.errors import AccessDeniedError, AccessUnavailableError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.background_metrics import create_background_metrics_app, serve_background_metrics
from powercontext.server.metrics import ServerMetrics
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    HttpConfig,
    MetricsConfig,
    ServerSettings,
    UnauthenticatedNonLoopbackBindError,
)
from powercontext.server.tracing import ServerTracing


def _app(*, enforced: bool = False, access: Any = None, enabled: bool = True):
    settings = ServerSettings(
        access=AccessControlConfig(mode="enforced" if enforced else "disabled"),
        auth=BearerAuthConfig(token=SecretStr("scrape-token") if enforced else None),
        metrics=MetricsConfig(enabled=enabled),
    )
    app = create_background_metrics_app(settings, runtime=None, metrics=ServerMetrics(), access_control=access)
    return app, settings


@pytest.mark.parametrize("enforced", [False, True])
def test_background_exposes_metrics_and_public_liveness(enforced):
    class Access:
        deployment_id = "default"

        async def require(self, principal, action, resource, *, context):
            assert principal.id == "server-token"
            assert action == AccessAction.SERVER_OBSERVE
            assert context.operation == "get_metrics"

    app, _ = _app(enforced=enforced, access=Access() if enforced else None)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer scrape-token"} if enforced else {}
        if enforced:
            assert client.get("/metrics").status_code == 401
            assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get("/metrics", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert client.get("/health/live").status_code == 200
        for path in ("/", "/docs", "/openapi.json", "/health/ready", "/v1/capabilities", "/mcp", "/dashboard"):
            assert client.get(path, headers=headers).status_code == 404


@pytest.mark.parametrize("error,status", [(AccessDeniedError(), 403), (AccessUnavailableError(), 503)])
def test_background_metrics_requires_observe_permission(error, status):
    class Access:
        deployment_id = "default"

        async def require(self, *args, **kwargs):
            raise error

    app, _ = _app(enforced=True, access=Access())
    with TestClient(app) as client:
        response = client.get("/metrics", headers={"Authorization": "Bearer scrape-token"})
    assert response.status_code == status


def test_background_metrics_disabled_has_no_route():
    app, _ = _app(enabled=False)
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 404


def test_failed_scrape_does_not_poison_listener():
    app, _ = _app()
    endpoint = next(route.endpoint for route in app.routes if route.path == "/metrics")
    render = endpoint._metrics.render
    endpoint._metrics.render = lambda: (_ for _ in ()).throw(RuntimeError("private-provider-value"))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/metrics")
        assert response.status_code == 500
        assert "private-provider-value" not in response.text
        endpoint._metrics.render = render
        assert client.get("/metrics").status_code == 200


@pytest.mark.parametrize("cancel", [False, True])
def test_listener_real_socket_stops_and_releases_bind(cancel):
    async def scenario():
        app, settings = _app()
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        settings = settings.model_copy(update={"metrics": MetricsConfig(background_port=port)})
        stopped = asyncio.Event()
        task = asyncio.create_task(serve_background_metrics(app, settings, stopped))
        try:
            async with httpx.AsyncClient() as client:
                async with asyncio.timeout(10):
                    while True:
                        try:
                            response = await client.get(f"http://127.0.0.1:{port}/metrics")
                            assert response.status_code == 200
                            break
                        except httpx.ConnectError:
                            await asyncio.sleep(0.01)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                stopped.set()
                await asyncio.wait_for(task, 10)
            with socket.socket() as rebound:
                rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                rebound.bind(("127.0.0.1", port))
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_listener_bind_failure_propagates_without_aborting_event_loop():
    async def scenario():
        app, settings = _app()
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            settings = settings.model_copy(update={"metrics": MetricsConfig(background_port=occupied.getsockname()[1])})
            with pytest.raises(RuntimeError, match="failed to start"):
                await serve_background_metrics(app, settings, asyncio.Event())
        assert await asyncio.sleep(0, result=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["stopped", "failed", "cancelled", "disabled"])
def test_background_runner_closes_runtime_and_removes_signal_handlers(monkeypatch, outcome):
    from powercontext.server.cli import _run_background_async

    async def scenario():
        _, settings = _app(enabled=outcome != "disabled")
        settings = settings.model_copy(update={"database": SQLiteConfig()})
        events = []
        signals = []
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda signum, callback: signals.append((signum, callback)))
        monkeypatch.setattr(loop, "remove_signal_handler", lambda signum: events.append(("removed", signum)))

        @asynccontextmanager
        async def runtime(*args, **kwargs):
            events.append("runtime-open")
            if outcome == "disabled":
                signals[0][1]()
            try:
                yield None
            finally:
                events.append("runtime-closed")

        async def listener(app, configured, stopped):
            events.append("listener-open")
            try:
                if outcome == "failed":
                    raise OSError
                if outcome == "cancelled":
                    raise asyncio.CancelledError
                signals[0][1]()
                await stopped.wait()
            finally:
                events.append("listener-closed")

        monkeypatch.setattr("powercontext.server.cli.open_builtin_runtime", runtime)
        monkeypatch.setattr("powercontext.server.cli.serve_background_metrics", listener)
        tracing = ServerTracing.context_only(role="background")
        if outcome in {"failed", "cancelled"}:
            with pytest.raises(OSError if outcome == "failed" else asyncio.CancelledError):
                await _run_background_async(settings, tracing)
        else:
            await _run_background_async(settings, tracing)
        assert "runtime-closed" in events
        if outcome == "disabled":
            assert "listener-open" not in events
        else:
            assert events.index("listener-closed") < events.index("runtime-closed")
        assert [item for item in events if isinstance(item, tuple)] == [("removed", sig) for sig, _ in signals]

    asyncio.run(scenario())


def test_background_metrics_fails_closed_without_authorization_service():
    app, _ = _app(enforced=True)
    with TestClient(app) as client:
        response = client.get("/metrics", headers={"Authorization": "Bearer scrape-token"})
    assert response.status_code == 503


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "2001:db8::1"])  # noqa: S104 - policy test never opens an external socket
def test_background_listener_rejects_unauthenticated_non_loopback(monkeypatch, host):
    async def unexpected_listener(_server):
        pytest.fail("unsafe listener reached Uvicorn")

    monkeypatch.setattr("powercontext.server.background_metrics._BackgroundMetricsServer.serve", unexpected_listener)
    settings = ServerSettings(
        runtime=RuntimeConfig(artifact_processing_role="background"),
        database=OceanBaseConfig(url=SecretStr("mysql+aoceanbase://test@localhost:2881/test?charset=utf8mb4")),
    ).model_copy(update={"http": HttpConfig(host=host)})
    app = create_background_metrics_app(settings, runtime=None, metrics=ServerMetrics(), access_control=None)
    with pytest.raises(UnauthenticatedNonLoopbackBindError, match="non-loopback bind requires authentication"):
        asyncio.run(serve_background_metrics(app, settings, asyncio.Event()))


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "2001:db8::1"])  # noqa: S104 - policy test never opens an external socket
def test_background_metrics_settings_reject_unsafe_bind(host):
    with pytest.raises(ValidationError, match="non-loopback bind requires authentication"):
        ServerSettings(
            runtime=RuntimeConfig(artifact_processing_role="background"),
            database=OceanBaseConfig(url=SecretStr("mysql+aoceanbase://test@localhost:2881/test?charset=utf8mb4")),
            http=HttpConfig(host=host),
        )


@pytest.mark.parametrize(
    "host,enforced,override,enabled",
    [
        ("127.0.0.1", False, False, True),
        ("::1", False, False, True),
        ("0.0.0.0", True, False, True),  # noqa: S104 - policy test never opens an external socket
        ("::", True, False, True),
        ("0.0.0.0", False, True, True),  # noqa: S104 - policy test never opens an external socket
        ("::", False, True, True),
        ("0.0.0.0", False, False, False),  # noqa: S104 - policy test never opens an external socket
        ("::", False, False, False),
    ],
)
def test_background_listener_preserves_allowed_bind_modes(monkeypatch, host, enforced, override, enabled):
    started = []

    async def listener(server):
        started.append(server.config.host)

    monkeypatch.setattr("powercontext.server.background_metrics._BackgroundMetricsServer.serve", listener)
    settings = ServerSettings(
        runtime=RuntimeConfig(artifact_processing_role="background"),
        database=OceanBaseConfig(url=SecretStr("mysql+aoceanbase://test@localhost:2881/test?charset=utf8mb4")),
        http=HttpConfig(host=host),
        access=AccessControlConfig(mode="enforced" if enforced else "disabled"),
        auth=BearerAuthConfig(token=SecretStr("scrape-token") if enforced else None),
        metrics=MetricsConfig(enabled=enabled),
        allow_unauthenticated_non_loopback=override,
    )
    app = create_background_metrics_app(settings, runtime=None, metrics=ServerMetrics(), access_control=None)
    asyncio.run(serve_background_metrics(app, settings, asyncio.Event()))
    assert started == ([host] if enabled else [])
