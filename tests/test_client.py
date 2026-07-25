import asyncio

import httpx
import pytest
from pydantic import ValidationError

from powercontext.api import (
    CaptureContentSourceRequest,
)
from powercontext.client import InvalidResponseError, PowerContextClient, ServerResponseError, TransportError
from powercontext.client.settings import ClientSettings


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
            headers={"X-Request-ID": "request-123"},
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
            headers={"X-Request-ID": "request-123"},
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
