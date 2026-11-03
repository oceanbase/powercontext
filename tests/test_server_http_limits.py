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
import logging
from collections import deque
from collections.abc import Sequence

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext.http import ArtifactReference, ProposeSkillPackageRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import HttpConfig, McpConfig, MetricsConfig, ServerSettings
from powercontext.server.tracing import ServerTracing


def _server(downstream: ASGIApp, *, max_body: int = 8, max_requests: int = 1) -> ASGIApp:
    app = create_server_app(
        settings=ServerSettings(
            http=HttpConfig(max_request_body_bytes=max_body, max_concurrent_requests=max_requests),
            mcp=McpConfig(enabled=False),
        )
    )
    app.mount("/sentinel", downstream)
    return app


async def _invoke(
    app: ASGIApp,
    chunks: Sequence[bytes] = (),
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    response_send: Send | None = None,
    path: str = "/sentinel/",
    method: str = "POST",
) -> tuple[list[Message], int]:
    pending = deque(chunks)
    reads = 0
    output: list[Message] = []

    async def receive() -> Message:
        nonlocal reads
        reads += 1
        return {
            "type": "http.request",
            "body": pending.popleft() if pending else b"",
            "more_body": bool(pending),
        }

    async def send(message: Message) -> None:
        if response_send is not None:
            await response_send(message)
        output.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [] if headers is None else headers,
        "server": ("localhost", 8000),
        "client": ("127.0.0.1", 10000),
    }
    await app(scope, receive, send)
    return output, reads


def test_blocked_outward_response_holds_server_capacity_until_sent() -> None:
    async def scenario() -> None:
        sending = asyncio.Event()
        release = asyncio.Event()
        app = create_server_app(
            settings=ServerSettings(
                http=HttpConfig(max_concurrent_requests=1),
                mcp=McpConfig(enabled=False),
            )
        )

        async def blocked_send(message: Message) -> None:
            if message["type"] == "http.response.body":
                sending.set()
                await release.wait()

        first = asyncio.create_task(_invoke(app, response_send=blocked_send, path="/health/live", method="GET"))
        try:
            await asyncio.wait_for(sending.wait(), timeout=2)
            for _ in range(4):
                overloaded, reads = await _invoke(app, [b"must not be read"], path="/health/live", method="GET")
                assert overloaded[0]["status"] == 503
                assert json.loads(overloaded[1]["body"])["error"]["code"] == "server_overloaded"
                assert reads == 0
        finally:
            release.set()
            await first

        accepted, _ = await _invoke(app, path="/health/live", method="GET")
        assert accepted[0]["status"] == 200

    asyncio.run(scenario())


@pytest.mark.parametrize("rejection", ["declared_body", "streamed_body", "capacity"])
def test_admission_rejections_keep_request_identity_and_transport_observations(
    caplog: pytest.LogCaptureFixture, rejection: str
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    caplog.set_level(logging.INFO, logger="powercontext.server.access")
    app = create_server_app(
        settings=ServerSettings(
            http=HttpConfig(max_request_body_bytes=8, max_concurrent_requests=1),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=True),
        ),
        tracing=ServerTracing(provider),
    )

    async def scenario() -> list[Message]:
        sending = asyncio.Event()
        release = asyncio.Event()

        async def blocked_send(message: Message) -> None:
            if message["type"] == "http.response.body":
                sending.set()
                await release.wait()

        first = None
        if rejection == "capacity":
            first = asyncio.create_task(_invoke(app, response_send=blocked_send, path="/health/live", method="GET"))
        try:
            if first is not None:
                await asyncio.wait_for(sending.wait(), timeout=2)
            messages, reads = await _invoke(
                app,
                [b"1234", b"56789"],
                headers=[(b"content-length", b"9")] if rejection == "declared_body" else [],
                path="/v1/scope-bindings/resolve",
            )
            assert reads == (2 if rejection == "streamed_body" else 0)
            return messages
        finally:
            release.set()
            if first is not None:
                await first

    try:
        messages = asyncio.run(scenario())
        status = 503 if rejection == "capacity" else 413
        assert messages[0]["status"] == status
        request_id = dict(messages[0]["headers"])[b"x-powercontext-request-id"].decode()
        assert request_id
        records = [record for record in caplog.records if getattr(record, "event", "") == "transport.request.completed"]
        assert len(records) == 1
        assert records[0].__dict__["request_id"] == request_id
        assert records[0].__dict__["status_code"] == status
        assert records[0].__dict__["outcome"] == "failure"
        assert (
            app.state.metrics.registry.get_sample_value(
                "powercontext_server_transport_requests_total",
                {"transport": "http", "operation": "resolve_scope_binding", "outcome": "failure"},
            )
            == 1
        )
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attributes = spans[0].attributes
        assert attributes is not None
        assert attributes["powercontext.request.id"] == request_id
        assert attributes["http.response.status_code"] == status
    finally:
        provider.shutdown()


@pytest.mark.parametrize(
    ("headers", "chunks", "expected_reads"),
    [
        ([(b"content-length", b"9")], [b"private"], 0),
        ([(b"transfer-encoding", b"chunked")], [b"1234", b"56789", b"unread"], 2),
        ([(b"content-length", b"1")], [b"123456789", b"unread"], 1),
        ([(b"x-powercontext-internal-bridge", b"true")], [b"123456789"], 1),
    ],
)
def test_oversized_body_is_rejected_before_downstream_and_stops_reading(
    headers: list[tuple[bytes, bytes]], chunks: list[bytes], expected_reads: int
) -> None:
    entered: list[bool] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        entered.append(True)
        await Response(b"parsed")(scope, receive, send)

    app = _server(downstream)
    messages, reads = asyncio.run(_invoke(app, chunks, headers=headers))

    assert messages[0]["status"] == 413
    assert json.loads(messages[1]["body"])["error"]["code"] == "request_too_large"
    assert entered == []
    assert reads == expected_reads
    accepted, _ = asyncio.run(_invoke(app))
    assert accepted[0]["status"] == 200
    assert entered == [True]


def test_body_at_the_limit_is_replayed_completely_to_downstream() -> None:
    bodies: list[bytes] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        bodies.append(await Request(scope, receive).body())
        await Response(b"accepted")(scope, receive, send)

    messages, _ = asyncio.run(_invoke(_server(downstream), [b"abc", b"defgh"]))

    assert messages[0]["status"] == 200
    assert bodies == [b"abcdefgh"]


def test_default_http_admission_accepts_the_maximum_archive_field_with_metadata() -> None:
    request = ProposeSkillPackageRequest(
        scope_id="\x00" * 256,
        archive_base64="A" * 6_990_507 + "=",
        reason="\x00" * 2000,
        target=ArtifactReference(family='"' * 128, artifact_id='"' * 128, revision=2**63 - 1),
    )
    body = json.dumps(request.model_dump(mode="json"), ensure_ascii=True).encode()
    observed: list[int] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        payload = await Request(scope, receive).json()
        observed.append(len(payload["archive_base64"]))
        await Response(b"accepted")(scope, receive, send)

    app = _server(downstream, max_body=HttpConfig().max_request_body_bytes)
    messages, _ = asyncio.run(_invoke(app, [body]))

    assert messages[0]["status"] == 200
    assert observed == [6_990_508]


@pytest.mark.parametrize("completion", ["success", "error", "cancel"])
def test_streaming_response_holds_capacity_and_releases_after_completion(completion: str) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"first", "more_body": True})
                started.set()
                await release.wait()
                if completion == "error":
                    raise RuntimeError
                await send({"type": "http.response.body", "body": b"last", "more_body": False})
            else:
                await Response(b"accepted")(scope, receive, send)

        app = _server(downstream)
        first = asyncio.create_task(_invoke(app))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            overloaded, reads = await _invoke(app, [b"must not be read"])
            assert overloaded[0]["status"] == 503
            assert (b"retry-after", b"1") in overloaded[0]["headers"]
            assert json.loads(overloaded[1]["body"])["error"]["code"] == "server_overloaded"
            assert reads == 0
            assert calls == 1
        finally:
            if completion == "cancel":
                first.cancel()
            release.set()
            if completion == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await first
            elif completion == "error":
                with pytest.raises(RuntimeError):
                    await first
            else:
                await first

        accepted, _ = await _invoke(app)
        assert accepted[0]["status"] == 200
        assert calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("trailers", [False, True])
def test_finished_response_releases_capacity_before_cleanup_without_releasing_a_later_request(trailers: bool) -> None:
    async def scenario() -> None:
        cleanup = asyncio.Event()
        cleaning_up = asyncio.Event()
        response_sent = asyncio.Event()
        second_started = asyncio.Event()
        second_release = asyncio.Event()
        calls = 0

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                await send({"type": "http.response.start", "status": 200, "headers": [], "trailers": trailers})
                await send({"type": "http.response.body", "body": b"complete", "more_body": False})
                if trailers:
                    overloaded, _ = await _invoke(app)
                    assert overloaded[0]["status"] == 503
                    await send({"type": "http.response.trailers", "headers": [], "more_trailers": False})
                cleaning_up.set()
                await cleanup.wait()
            else:
                second_started.set()
                await second_release.wait()
                await Response(b"accepted")(scope, receive, send)

        app = _server(downstream)

        async def observe_send(message: Message) -> None:
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_sent.set()

        first = asyncio.create_task(_invoke(app, response_send=observe_send))
        second: asyncio.Task[tuple[list[Message], int]] | None = None
        try:
            await asyncio.wait_for(cleaning_up.wait(), timeout=1)
            await asyncio.wait_for(response_sent.wait(), timeout=1)
            second = asyncio.create_task(_invoke(app))
            await asyncio.wait_for(second_started.wait(), timeout=1)
            cleanup.set()
            await first
            overloaded, _ = await _invoke(app)
            assert overloaded[0]["status"] == 503
            assert calls == 2
        finally:
            cleanup.set()
            second_release.set()
            await first
            if second is not None:
                await second

    asyncio.run(scenario())


@pytest.mark.parametrize("setting", ["MAX_REQUEST_BODY_BYTES", "MAX_CONCURRENT_REQUESTS"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_nonpositive_http_resource_limits_are_rejected(
    monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    monkeypatch.setenv(f"POWERCONTEXT_SERVER_HTTP_{setting}", value)

    with pytest.raises(ValidationError, match=setting.lower()):
        ServerSettings()
