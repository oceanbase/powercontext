import httpx
import pytest

from powercontext.api import Capabilities
from powercontext.client.client import PowerContextClient
from powercontext.client.errors import InvalidResponseError, ServerResponseError, TransportError


def test_client_decodes_a_capabilities_response() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "https://memory.example/v1/capabilities"
        return httpx.Response(
            200,
            json={
                "source_types": [],
                "artifact_families": [],
                "search_modes": [],
                "limits": [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        client = PowerContextClient("https://memory.example/", http_client=http_client)

        capabilities = client.get_capabilities()

    assert capabilities.source_types == []
    assert isinstance(capabilities, Capabilities)


def test_client_preserves_server_error_context() -> None:
    response = httpx.Response(503, headers={"X-Request-ID": "request-123"})
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http_client:
        client = PowerContextClient("https://memory.example", http_client=http_client)

        with pytest.raises(ServerResponseError) as caught:
            client.get_readiness()

    assert caught.value.status_code == 503
    assert caught.value.request_id == "request-123"


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
