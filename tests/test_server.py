from fastapi.testclient import TestClient

from powercontext.api import Capabilities, CapabilityLimit, ReadinessResponse, ReadinessStatus
from powercontext.server.app import create_app
from powercontext.server.settings import ServerSettings


def test_settings_load_server_environment(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_HOST", "127.0.0.2")
    monkeypatch.setenv("POWERCONTEXT_SERVER_PORT", "9000")

    settings = ServerSettings()

    assert settings.host == "127.0.0.2"
    assert settings.port == 9000


def test_liveness_adds_a_request_id() -> None:
    client = TestClient(create_app())

    generated = client.get("/health/live")
    supplied = client.get("/health/live", headers={"X-Request-ID": "request-123"})

    assert generated.status_code == 200
    assert generated.json() == {"status": "ok"}
    assert generated.headers["X-Request-ID"]
    assert supplied.headers["X-Request-ID"] == "request-123"


def test_liveness_does_not_reflect_an_unsafe_request_id() -> None:
    client = TestClient(create_app())

    response = client.get("/health/live", headers={"X-Request-ID": "unsafe value"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "unsafe value"


def test_readiness_probe_is_injected_by_the_assembly() -> None:
    async def probe() -> ReadinessResponse:
        return ReadinessResponse(
            status=ReadinessStatus.READY,
            checks={"database": "ready", "migrations": "ready"},
        )

    client = TestClient(create_app(readiness_probe=probe))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ready", "migrations": "ready"},
    }


def test_readiness_reports_unavailable_bindings() -> None:
    async def probe() -> ReadinessResponse:
        return ReadinessResponse(
            status=ReadinessStatus.NOT_READY,
            checks={"database": "unavailable"},
        )

    response = TestClient(create_app(readiness_probe=probe)).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable"},
    }
    assert response.headers["X-Request-ID"]


def test_unhandled_errors_preserve_the_request_id() -> None:
    def fail() -> Capabilities:
        raise RuntimeError("boom")

    client = TestClient(create_app(capability_provider=fail))

    response = client.get("/v1/capabilities", headers={"X-Request-ID": "request-123"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "request-123"


def test_capabilities_translate_core_values() -> None:
    capabilities = Capabilities(
        source_types=["git-commit"],
        artifact_families=["memory"],
        search_modes=["text"],
        limits=[CapabilityLimit(name="max_results", value=20)],
    )
    client = TestClient(create_app(capability_provider=lambda: capabilities))

    response = client.get("/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "source_types": ["git-commit"],
        "artifact_families": ["memory"],
        "search_modes": ["text"],
        "limits": [{"name": "max_results", "value": 20}],
    }


def test_openapi_exposes_only_the_implemented_contract() -> None:
    schema = create_app().openapi()

    assert schema["openapi"] == "3.0.3"
    assert set(schema["paths"]) == {
        "/health/live",
        "/health/ready",
        "/v1/capabilities",
    }
    assert schema["paths"]["/v1/capabilities"]["get"]["operationId"] == "get_capabilities"
    assert "X-Request-ID" in schema["paths"]["/v1/capabilities"]["get"]["responses"]["200"]["headers"]
