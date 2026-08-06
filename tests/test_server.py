import logging
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.http import (
    Capabilities,
    ReadinessResponse,
    ReadinessStatus,
)
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings
from powercontext.sources import Source


class _NoopExperiencePipeline:
    async def incubate(self, _sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        return ()


def test_settings_load_server_environment(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "127.0.0.2")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "9000")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_DATABASE_URL",
        "sqlite+aiosqlite:////var/lib/powercontext/test.db",
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT", "25")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS", "45")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", " test ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS", "4")
    monkeypatch.setenv("POWERCONTEXT_SERVER_MCP_ENABLED", "false")
    monkeypatch.setenv("POWERCONTEXT_SERVER_MCP_PATH", "/context/")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_EXTERNAL_SKILLS",
        (
            '{"host_id":"workstation-1","codex_roots":['
            '{"root_id":"repository","installation_scope":"project","path":"/srv/project/.agents/skills"}]}'
        ),
    )

    settings = ServerSettings()

    assert settings.http.host == "127.0.0.2"
    assert settings.http.port == 9000
    assert settings.database.url == "sqlite+aiosqlite:////var/lib/powercontext/test.db"
    assert settings.runtime.source_window_limit == 25
    assert settings.runtime.experience_schedule_seconds == 45
    assert settings.inference.generation_model == "test"
    assert settings.inference.generation_timeout_seconds == 12.5
    assert settings.inference.generation_max_requests == 4
    assert settings.mcp.enabled is False
    assert settings.mcp.path == "/context"
    assert settings.external_skills.host_id == "workstation-1"
    assert settings.external_skills.codex_roots[0].root_id == "repository"
    assert settings.external_skills.codex_roots[0].path.as_posix() == "/srv/project/.agents/skills"


def test_server_settings_select_oceanbase(monkeypatch) -> None:
    url = "mysql+aoceanbase://root:test@127.0.0.1:2881/powercontext?charset=utf8mb4"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)

    settings = ServerSettings()

    assert isinstance(settings.database, OceanBaseConfig)
    assert settings.database.url.get_secret_value() == url


def test_server_scheduler_uses_the_powercontext_data_directory(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "powercontext-data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    app = create_server_app(
        settings=ServerSettings(
            runtime=RuntimeConfig(experience_schedule_seconds=3_600),
            mcp=McpConfig(enabled=False),
        ),
        experience_pipeline=_NoopExperiencePipeline(),
    )

    with TestClient(app):
        assert (data_dir / "scheduler.db").is_file()


def test_settings_load_bearer_authentication_without_exposing_token(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_ENABLED", "true")
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_TOKEN", "server-secret")

    settings = ServerSettings()

    assert settings.auth.enabled is True
    assert settings.auth.token is not None
    assert settings.auth.token.get_secret_value() == "server-secret"
    assert "server-secret" not in repr(settings)


def test_enabled_bearer_authentication_requires_a_token() -> None:
    with pytest.raises(ValueError, match="Bearer token is required"):
        BearerAuthConfig(enabled=True)


def test_liveness_adds_a_server_owned_request_id() -> None:
    client = TestClient(create_app())

    response = client.get(
        "/health/live",
        headers={
            "X-PowerContext-Request-ID": "caller-request-id",
            "X-Request-ID": "legacy-request-id",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert re.fullmatch(r"[0-9a-f]{16}", response.headers["X-PowerContext-Request-ID"])
    assert response.headers["X-PowerContext-Request-ID"] != "caller-request-id"
    assert "X-Request-ID" not in response.headers


def test_server_factory_optionally_requires_bearer_authentication() -> None:
    app = create_server_app(
        settings=ServerSettings(
            auth=BearerAuthConfig(enabled=True, token=SecretStr("server-secret")),
            mcp=McpConfig(enabled=False),
        )
    )
    client = TestClient(app)

    missing = client.get("/v1/capabilities")
    invalid = client.get("/v1/capabilities", headers={"Authorization": "Bearer wrong"})
    accepted = client.get("/v1/capabilities", headers={"Authorization": "Bearer server-secret"})
    protected_metrics = client.get("/metrics")
    accepted_metrics = client.get("/metrics", headers={"Authorization": "Bearer server-secret"})
    liveness = client.get("/health/live")

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert re.fullmatch(r"[0-9a-f]{16}", missing.headers["X-PowerContext-Request-ID"])
    assert missing.json() == {
        "error": {
            "code": "unauthorized",
            "message": "A valid bearer token is required.",
            "details": None,
        }
    }
    assert invalid.status_code == 401
    assert accepted.status_code == 200
    assert protected_metrics.status_code == 401
    assert accepted_metrics.status_code == 200
    assert liveness.status_code == 200


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
    assert response.headers["X-PowerContext-Request-ID"]


def test_unhandled_errors_return_the_server_request_id() -> None:
    def fail() -> Capabilities:
        raise RuntimeError("boom")

    client = TestClient(create_app(capability_provider=fail), raise_server_exceptions=False)

    response = client.get("/v1/capabilities")

    assert response.status_code == 500
    assert re.fullmatch(r"[0-9a-f]{16}", response.headers["X-PowerContext-Request-ID"])


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


def test_stats_returns_inclusive_utc_periods_for_empty_scope(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        responses = []
        for requested_period, expected_preset, expected_days in (
            (None, "30d", 30),
            ("today", "today", 1),
            ("7d", "7d", 7),
            ("30d", "30d", 30),
        ):
            params = {"scope_id": "project:test"}
            if requested_period is not None:
                params["period"] = requested_period
            responses.append((client.get("/v1/stats", params=params), expected_preset, expected_days))
        invalid = client.get(
            "/v1/stats",
            params={"scope_id": "project:test", "period": "all"},
        )

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"

    for response, expected_preset, expected_days in responses:
        assert response.status_code == 200
        body = response.json()
        as_of = datetime.fromisoformat(body["as_of"])
        assert as_of.utcoffset() == timedelta(0)
        end_date = as_of.date()
        start_date = end_date - timedelta(days=expected_days - 1)
        expected_period = {
            "preset": expected_preset,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "timezone": "UTC",
        }
        expected_dates = [(start_date + timedelta(days=offset)).isoformat() for offset in range(expected_days)]

        assert body["scope_id"] == "project:test"
        assert body["usage"]["period"] == expected_period
        assert body["recall"]["period"] == expected_period
        assert [day["date"] for day in body["usage"]["daily"]] == expected_dates
        assert [day["date"] for day in body["recall"]["daily"]] == expected_dates
        assert all(day["generation"]["requests"] == 0 for day in body["usage"]["daily"])
        assert all(day["embedding"]["requests"] == 0 for day in body["usage"]["daily"])
        assert all(day["preparations"] == 0 for day in body["recall"]["daily"])
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-PowerContext-Request-ID"]


def test_application_failure_log_uses_operation_context(caplog) -> None:
    def fail() -> Capabilities:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="powercontext.server.app"):
        response = TestClient(create_app(capability_provider=fail), raise_server_exceptions=False).get(
            "/v1/capabilities"
        )

    record = next(record for record in caplog.records if record.event == "application.operation.completed")
    assert response.status_code == 500
    assert record.operation == "get_capabilities"
    assert record.outcome == "failure"
    assert record.request_id == response.headers["X-PowerContext-Request-ID"]
    assert record.unit == "application"
    assert record.error_code == "internal_error"


def test_logging_failure_does_not_change_the_response(monkeypatch) -> None:
    def fail() -> Capabilities:
        raise RuntimeError("boom")

    def fail_to_log(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError

    monkeypatch.setattr("powercontext.server.app.logger.log", fail_to_log)

    response = TestClient(create_app(capability_provider=fail), raise_server_exceptions=False).get("/v1/capabilities")

    assert response.status_code == 500
    assert re.fullmatch(r"[0-9a-f]{16}", response.headers["X-PowerContext-Request-ID"])
