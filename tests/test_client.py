import httpx
import pytest

from powercontext.api import (
    Capabilities,
    CaptureContentSourceRequest,
    SearchMemoryRequest,
)
from powercontext.client import InvalidResponseError, PowerContextClient, ServerResponseError, TransportError


def test_client_decodes_a_capabilities_response() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "https://memory.example/v1/capabilities"
        return httpx.Response(
            200,
            json={
                "source_types": [],
                "artifact_families": [],
                "memory_extraction": False,
                "search_modes": [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = PowerContextClient("https://memory.example/", http_client=http_client)

        capabilities = client.get_capabilities()

    assert capabilities.source_types == []
    assert isinstance(capabilities, Capabilities)


def test_client_decodes_an_empty_memory_search() -> None:
    response = httpx.Response(200, json={"hits": []})
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        result = PowerContextClient("https://memory.example", http_client=http_client).search_memory(
            SearchMemoryRequest(scope_id="new-scope", query="anything")
        )

    assert result.memory is None
    assert result.mode is None
    assert result.hits == []


def test_client_rejects_an_undeclared_success_status() -> None:
    response = httpx.Response(
        200,
        json={
            "status": "accepted",
            "source": {"name": "content", "source_id": "turn-1"},
            "position": 1,
        },
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        with pytest.raises(ServerResponseError) as caught:
            client.capture_content_source(
                CaptureContentSourceRequest(scope_id="project", source_id="turn-1", content="content")
            )

    assert caught.value.status_code == 200


def test_client_preserves_server_error_context() -> None:
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
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        with pytest.raises(ServerResponseError) as caught:
            client.get_readiness()

    assert caught.value.status_code == 503
    assert caught.value.request_id == "request-123"
    assert caught.value.code == "runtime_not_ready"
    assert caught.value.server_message == "The Runtime is not ready."
    assert caught.value.details == {"component": "memory"}


def test_client_keeps_a_generic_server_error_when_the_error_body_is_invalid() -> None:
    response = httpx.Response(500, text="Internal Server Error")
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        with pytest.raises(ServerResponseError) as caught:
            client.get_liveness()

    assert caught.value.status_code == 500
    assert caught.value.code is None


def test_client_rejects_an_invalid_success_response() -> None:
    response = httpx.Response(
        200,
        headers={"X-Request-ID": "request-123"},
        json={"status": "ok", "unexpected": True},
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        with pytest.raises(InvalidResponseError) as caught:
            client.get_liveness()

    assert caught.value.request_id == "request-123"


def test_client_wraps_http_transport_failures() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message, request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        with pytest.raises(TransportError):
            client.get_liveness()
