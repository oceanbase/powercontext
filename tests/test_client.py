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

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from powercontext.client import InvalidResponseError, PowerContextClient, ServerResponseError, TransportError
from powercontext.client.settings import ClientSettings
from powercontext.http import (
    CaptureContentSourceRequest,
    GetHandoffReportRequest,
)


def test_client_rejects_an_undeclared_success_status() -> None:
    async def scenario() -> None:
        response = httpx.Response(
            200,
            json={
                "status": "accepted",
                "source": {"name": "content", "source_id": "turn-1"},
                "position": 1,
            },
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(ServerResponseError) as caught:
                await client.capture_content_source(
                    CaptureContentSourceRequest(scope_id="project", source_id="turn-1", content="content")
                )

        assert caught.value.status_code == 200

    asyncio.run(scenario())


def test_client_preserves_server_error_context() -> None:
    async def scenario() -> None:
        response = httpx.Response(
            503,
            headers={"X-PowerContext-Request-ID": "request-123"},
            json={
                "error": {
                    "code": "runtime_not_ready",
                    "message": "The Runtime is not ready.",
                    "details": {"component": "memory"},
                }
            },
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(ServerResponseError) as caught:
                await client.get_readiness()

        assert caught.value.status_code == 503
        assert caught.value.request_id == "request-123"
        assert caught.value.code == "runtime_not_ready"
        assert caught.value.server_message == "The Runtime is not ready."
        assert caught.value.details == {"component": "memory"}

    asyncio.run(scenario())


def test_client_sends_an_explicit_bearer_token() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"status": "ok"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient(
                "https://memory.example",
                token="secret-token",  # noqa: S106 - non-secret test credential.
                http_client=http_client,
            )
            await client.get_liveness()

        assert len(requests) == 1
        assert requests[0].headers["Authorization"] == "Bearer secret-token"

    asyncio.run(scenario())


def test_client_keeps_a_generic_server_error_when_the_error_body_is_invalid() -> None:
    async def scenario() -> None:
        response = httpx.Response(500, text="Internal Server Error")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(ServerResponseError) as caught:
                await client.get_liveness()

        assert caught.value.status_code == 500
        assert caught.value.code is None

    asyncio.run(scenario())


def test_client_rejects_an_invalid_success_response() -> None:
    async def scenario() -> None:
        response = httpx.Response(
            200,
            headers={"X-PowerContext-Request-ID": "request-123"},
            json={"status": "ok", "unexpected": True},
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(InvalidResponseError) as caught:
                await client.get_liveness()

        assert caught.value.request_id == "request-123"

    asyncio.run(scenario())


def test_client_wraps_http_transport_failures() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message, request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(TransportError) as caught:
                await client.get_liveness()

        assert caught.value.path == "/health/live"
        assert isinstance(caught.value.__cause__, httpx.ConnectError)

    asyncio.run(scenario())


def test_client_downloads_handoff_report_bytes_and_sets_download_flag() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"# Handoff Report\n", headers={"content-type": "text/markdown"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            request = GetHandoffReportRequest(project_id="project-1")
            rendered = await client.get_handoff_report(request)
            content = await client.download_handoff_report(request)

        assert rendered == "# Handoff Report\n"
        assert content == b"# Handoff Report\n"
        assert len(requests) == 2
        assert json.loads(requests[0].content)["download"] is False
        assert json.loads(requests[1].content)["download"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "server_url",
    [
        "https://user:password@memory.example",
        "https://memory.example/api?token=secret",
        "https://memory.example/api#fragment",
    ],
)
def test_client_settings_reject_ambiguous_or_sensitive_server_urls(server_url: str) -> None:
    with pytest.raises(ValidationError):
        ClientSettings(server_url=server_url)


def test_client_settings_error_repr_does_not_leak_url_credentials() -> None:
    with pytest.raises(ValidationError) as caught:
        ClientSettings(server_url="https://user:do-not-log@memory.example")

    assert "do-not-log" not in repr(caught.value)


def test_client_settings_hide_the_api_token(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "secret-token")

    settings = ClientSettings()

    assert settings.api_token is not None
    assert settings.api_token.get_secret_value() == "secret-token"
    assert "secret-token" not in repr(settings)
