from fastapi.testclient import TestClient

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.http import (
    Capabilities,
    ReadinessResponse,
    ReadinessStatus,
)
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_settings_load_server_environment(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "127.0.0.2")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "9000")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_DATABASE_URL",
        "sqlite+aiosqlite:////var/lib/powercontext/test.db",
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT", "25")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", " test ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS", "4")
    monkeypatch.setenv("POWERCONTEXT_SERVER_MCP_ENABLED", "false")
    monkeypatch.setenv("POWERCONTEXT_SERVER_MCP_PATH", "/context/")

    settings = ServerSettings()

    assert settings.http.host == "127.0.0.2"
    assert settings.http.port == 9000
    assert settings.database.url == "sqlite+aiosqlite:////var/lib/powercontext/test.db"
    assert settings.runtime.source_window_limit == 25
    assert settings.inference.generation_model == "test"
    assert settings.inference.generation_timeout_seconds == 12.5
    assert settings.inference.generation_max_requests == 4
    assert settings.mcp.enabled is False
    assert settings.mcp.path == "/context"


def test_server_settings_select_oceanbase(monkeypatch) -> None:
    url = "mysql+aoceanbase://root:test@127.0.0.1:2881/powercontext?charset=utf8mb4"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)

    settings = ServerSettings()

    assert isinstance(settings.database, OceanBaseConfig)
    assert settings.database.url.get_secret_value() == url


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

    client = TestClient(create_app(capability_provider=fail), raise_server_exceptions=False)

    response = client.get("/v1/capabilities", headers={"X-Request-ID": "request-123"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "request-123"


def test_prepare_context_rejects_memory_specific_tuning_fields(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/context/prepare",
            json={
                "scope_id": "project:test",
                "query": "query",
                "candidate_limit": 2,
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
