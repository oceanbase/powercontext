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

"""HTTP response observations preserve the status actually sent by the Server."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter

import pytest
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr
from starlette.types import Message, Receive, Scope, Send

from powercontext.server.context import bind_internal_bridge, reset_internal_bridge
from powercontext.server.factory import create_server_app
from powercontext.server.metrics import HttpResponseMetricsMiddleware, ServerMetrics
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, ServerLoggingConfig
from tests.test_server_metrics import _settings


def _responses(text: str) -> dict[tuple[str, str], float]:
    return {
        (sample.labels["operation"], sample.labels["status_code"]): sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == "powercontext_server_http_responses_total"
    }


def test_statuses_include_routing_and_validation_without_identity_labels(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "responses.db"))

    @app.get("/test/status", operation_id="test_status")
    async def status_response(code: int = 200) -> Response:
        return Response(status_code=code)

    expected: Counter[tuple[str, str]] = Counter()
    with TestClient(app) as client:
        for code in (200, 201, 302, 400, 403, 409, 422, 429, 500, 503):
            assert client.get("/test/status", params={"code": code}, follow_redirects=False).status_code == code
            expected["test_status", str(code)] += 1
        assert client.get("/test/status", params={"code": "private-invalid-input"}).status_code == 422
        expected["test_status", "422"] += 1
        assert client.get("/private-unmatched-path").status_code == 404
        expected["unmatched", "404"] += 1
        assert client.post("/test/status").status_code == 405
        expected["unmatched", "405"] += 1
        assert client.get("/v1/capabilities").status_code == 200
        expected["get_capabilities", "200"] += 1
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        metrics = client.get("/metrics").text

    assert _responses(metrics) == expected
    assert "powercontext_server_http_response_observation_enabled 1.0" in metrics
    for family in text_string_to_metric_families(metrics):
        for sample in family.samples:
            if sample.name == "powercontext_server_http_responses_total":
                assert set(sample.labels) == {"operation", "status_code"}
    assert "private-invalid-input" not in metrics
    assert "private-unmatched-path" not in metrics
    assert 'operation="get_capabilities",outcome="success",transport="http"' in metrics


def test_authentication_rejection_has_a_real_status_and_no_scope(tmp_path):
    settings = _settings(tmp_path / "auth.db").model_copy(
        update={
            "auth": BearerAuthConfig(token=SecretStr("isolated-status-test")),
            "access": AccessControlConfig(mode="enforced"),
        }
    )
    app = create_server_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/v1/capabilities")
        assert response.status_code == 401
        counts = _responses(app.state.metrics.render().decode())
    assert counts == {("get_capabilities", "401"): 1}


def test_authentication_rejection_uses_the_same_operation_in_metrics_and_logs(tmp_path, caplog):
    settings = _settings(tmp_path / "auth-correlation.db").model_copy(
        update={
            "auth": BearerAuthConfig(token=SecretStr("isolated-correlation-test")),
            "access": AccessControlConfig(mode="enforced"),
            "logging": ServerLoggingConfig(access=True),
        }
    )
    with (
        caplog.at_level(logging.INFO, logger="powercontext.server.access"),
        TestClient(app := create_server_app(settings=settings)) as client,
    ):
        response = client.get("/v1/capabilities")
        unknown = client.get("/private-unmatched-path")
    records = [r for r in caplog.records if getattr(r, "event", None) == "transport.request.completed"]
    assert response.status_code == unknown.status_code == 401
    assert [(r.operation, r.status_code) for r in records] == [("get_capabilities", 401), ("unmatched", 401)]
    assert _responses(app.state.metrics.render().decode()) == {
        ("get_capabilities", "401"): 1,
        ("unmatched", "401"): 1,
    }
    assert "private-unmatched-path" not in " ".join(r.operation for r in records)


@pytest.mark.parametrize("handler_status", [None, 503])
def test_outer_error_handler_status_is_counted_without_replacing_the_fastapi_app(tmp_path, handler_status):
    app = create_server_app(settings=_settings(tmp_path / "unhandled.db"))

    @app.get("/test/unhandled", operation_id="test_unhandled")
    async def unhandled() -> Response:
        raise RuntimeError

    if handler_status is not None:

        async def custom_error(request: Request, error: Exception) -> Response:
            return JSONResponse({"error": "unavailable"}, status_code=handler_status)

        app.add_exception_handler(Exception, custom_error)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/unhandled")
        assert response.status_code == (handler_status or 500)
        counts = _responses(client.get("/metrics").text)
    assert counts == {("test_unhandled", str(handler_status or 500)): 1}


def test_dynamic_declared_operation_and_metrics_like_unmatched_path(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "routes.db"))

    @app.get("/test/items/{item}", operation_id="test_item")
    async def item_response(item: str) -> Response:
        return Response(status_code=200)

    with TestClient(app) as client:
        assert client.get("/test/items/private-item").status_code == 200
        assert client.get("/metrics-private-unmatched").status_code == 404
        counts = _responses(client.get("/metrics").text)
    assert counts == {("test_item", "200"): 1, ("unmatched", "404"): 1}


def test_status_observation_is_disabled_with_metrics(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "disabled.db", metrics=False))
    with TestClient(app) as client:
        assert client.get("/v1/capabilities").status_code == 200
        assert client.get("/metrics").status_code == 404
        assert app.state.metrics is None


@pytest.mark.parametrize("failure", ["cancel_before_headers", "send_headers", "body_after_headers", "none"])
def test_only_successfully_sent_headers_supply_a_status(failure):
    metrics = ServerMetrics()
    sent: list[Message] = []

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        if failure == "cancel_before_headers":
            raise asyncio.CancelledError
        await send({"type": "http.response.start", "status": 200, "headers": []})
        if failure == "body_after_headers":
            raise RuntimeError
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if failure == "send_headers":
            raise RuntimeError
        sent.append(message)

    observer = HttpResponseMetricsMiddleware(application, metrics=metrics, operations={})

    async def scenario() -> None:
        if failure == "none":
            await observer({"type": "http", "method": "GET", "path": "/"}, receive, send)
        else:
            error = asyncio.CancelledError if failure == "cancel_before_headers" else RuntimeError
            with pytest.raises(error):
                await observer({"type": "http", "method": "GET", "path": "/"}, receive, send)

    asyncio.run(scenario())
    assert _responses(metrics.render().decode()) == (
        {("unmatched", "200"): 1} if failure in {"body_after_headers", "none"} else {}
    )


def test_response_observation_excludes_internal_bridge_and_mcp(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "bridge.db", mcp=True))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = bind_internal_bridge()
        try:
            assert client.get("/v1/capabilities").status_code == 200
        finally:
            reset_internal_bridge(token)
        client.get("/mcp")
        counts = _responses(client.get("/metrics").text)
    assert counts == {}


def test_failed_telemetry_does_not_change_the_response(tmp_path, monkeypatch):
    app = create_server_app(settings=_settings(tmp_path / "failed-telemetry.db"))

    def fail_labels(*args, **kwargs):
        raise RuntimeError

    monkeypatch.setattr(app.state.metrics.http_responses, "labels", fail_labels)
    with TestClient(app) as client:
        response = client.get("/v1/capabilities")
        assert response.status_code == 200
        assert response.headers["X-PowerContext-Request-ID"]


def test_stream_failure_keeps_sent_status_and_existing_failure_outcome(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "stream.db"))

    @app.get("/test/stream", operation_id="test_stream")
    async def stream() -> StreamingResponse:
        async def body():
            yield b"started"
            raise RuntimeError

        return StreamingResponse(body())

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/test/stream").status_code == 200
        metrics = client.get("/metrics").text
    assert _responses(metrics) == {("test_stream", "200"): 1}
    failures = [
        sample.value
        for family in text_string_to_metric_families(metrics)
        for sample in family.samples
        if sample.name == "powercontext_server_transport_requests_total" and sample.labels["outcome"] == "failure"
    ]
    assert sum(failures) == 1
