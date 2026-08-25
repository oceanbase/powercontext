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
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.providers.openai import OpenAIProvider

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceConfigurationError
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, MemoryExtractionProfile, RuntimeConfig
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


class _FailingEmbeddingModel:
    profile = EmbeddingProfile(
        profile_id="readiness-test",
        model="test:embedding",
        dimension=3,
        distance="l2",
        normalization="unit",
    )

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def embed(self, _texts: tuple[str, ...], /) -> EmbeddingResult:
        raise self.error


class _SequencedEmbeddingModel:
    profile = _FailingEmbeddingModel.profile

    def __init__(self, outcomes: list[Exception | None], now: list[float]) -> None:
        self._outcomes = outcomes
        self._now = now
        self.requests: list[float] = []

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.requests.append(self._now[0])
        await asyncio.sleep(0)
        outcome = self._outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


def test_settings_load_server_environment(monkeypatch) -> None:
    monkeypatch.delenv("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", raising=False)
    monkeypatch.delenv("POWERCONTEXT_SERVER_DASHBOARD_SCOPES", raising=False)
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "127.0.0.2")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "9000")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_DATABASE_URL",
        "sqlite+aiosqlite:////var/lib/powercontext/test.db",
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT", "25")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE", "conversation")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED", "true")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT", "40")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS", "45")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", " test ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS", "4")
    monkeypatch.setenv("POWERCONTEXT_SERVER_MCP_ENABLED", "false")
    monkeypatch.setenv("POWERCONTEXT_SERVER_MCP_PATH", "/context/")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_EXTERNAL_SKILLS",
        (
            '{"host_id":"workstation-1","targets":['
            '{"target_id":"codex-project","agent_kind":"codex","installation_scope":"project",'
            '"path":"/srv/project/.agents/skills","allow_managed_publish":true},'
            '{"target_id":"claude-user","agent_kind":"claude_code","installation_scope":"user",'
            '"path":"/home/example/.claude/skills"}]}'
        ),
    )

    settings = ServerSettings()

    assert settings.http.host == "127.0.0.2"
    assert settings.http.port == 9000
    assert isinstance(settings.database, SQLiteConfig)
    assert settings.database.url == "sqlite+aiosqlite:////var/lib/powercontext/test.db"
    assert settings.runtime.source_window_limit == 25
    assert settings.runtime.memory_extraction_profile is MemoryExtractionProfile.CONVERSATION
    assert settings.runtime.memory_rerank_enabled is True
    assert settings.runtime.memory_rerank_candidate_limit == 40
    assert settings.runtime.experience_schedule_seconds == 45
    assert settings.inference.generation_model == "test"
    assert settings.inference.generation_timeout_seconds == 12.5
    assert settings.inference.generation_max_requests == 4
    assert settings.mcp.enabled is False
    assert settings.mcp.path == "/context"
    assert settings.dashboard.enabled is True
    assert settings.dashboard.scopes == []
    assert settings.external_skills.host_id == "workstation-1"
    assert settings.external_skills.targets[0].target_id == "codex-project"
    assert settings.external_skills.targets[0].path.as_posix() == "/srv/project/.agents/skills"
    assert settings.external_skills.targets[0].allow_managed_publish is True
    assert settings.external_skills.targets[1].agent_kind == "claude_code"
    assert settings.external_skills.targets[1].path.as_posix() == "/home/example/.claude/skills"


def test_env_example_loads_server_settings(monkeypatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name)

    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        assignment = line.strip()
        if not assignment or assignment.startswith("#"):
            continue
        name, value = assignment.split("=", maxsplit=1)
        monkeypatch.setenv(name, value)

    settings = ServerSettings()

    assert isinstance(settings.database, SQLiteConfig)
    assert settings.inference.embedding_dimension == 2560


def test_server_settings_select_oceanbase(monkeypatch) -> None:
    url = "mysql+aoceanbase://root:test@127.0.0.1:2881/powercontext?charset=utf8mb4"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)

    settings = ServerSettings()

    assert isinstance(settings.database, OceanBaseConfig)
    assert settings.database.url.get_secret_value() == url


def test_server_settings_select_embedded_seekdb(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "powercontext-data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")

    settings = ServerSettings()

    assert isinstance(settings.database, SeekDBConfig)
    assert settings.database.path == data_dir / "seekdb"
    assert settings.database.database == "test"
    assert not data_dir.exists()


@pytest.mark.parametrize("configured_path", ["", "   "])
def test_server_settings_default_blank_embedded_seekdb_path(configured_path, tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "powercontext-data"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(data_dir))
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", configured_path)

    settings = ServerSettings()

    assert isinstance(settings.database, SeekDBConfig)
    assert settings.database.path == data_dir / "seekdb"


def test_server_settings_override_embedded_seekdb_path(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "custom-seekdb"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(database_path))

    settings = ServerSettings()

    assert isinstance(settings.database, SeekDBConfig)
    assert settings.database.path == database_path
    assert settings.database.database == "test"


def test_server_settings_reject_custom_embedded_seekdb_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "seekdb")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_PATH", str(tmp_path / "seekdb"))
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_DATABASE", "custom")

    with pytest.raises(ValidationError, match="Input should be 'test'"):
        ServerSettings()


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


def test_readiness_keeps_degraded_bindings_in_traffic() -> None:
    async def probe() -> ReadinessResponse:
        return ReadinessResponse(
            status=ReadinessStatus.DEGRADED,
            checks={"inference.embedding": "unavailable"},
        )

    response = TestClient(create_app(readiness_probe=probe)).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "checks": {"inference.embedding": "unavailable"},
    }


def test_server_factory_reports_database_failure_as_not_ready(monkeypatch, tmp_path) -> None:
    async def fail_ping(_database: AsyncDatabase) -> None:
        raise OSError("secret database URL")  # noqa: TRY003 - verifies redaction

    monkeypatch.setattr(AsyncDatabase, "ping", fail_ping)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")
        metrics = client.get("/metrics")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "runtime": "ready",
            "database": "unavailable",
        },
    }
    assert "powercontext_server_runtime_ready 0.0" in metrics.text
    assert "secret database URL" not in response.text


def test_server_factory_reports_database_and_configured_generation_readiness(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "runtime": "ready",
            "database": "ready",
            "inference.generation": "ready",
        },
    }


def test_server_factory_reports_generation_failure_as_degraded(monkeypatch, tmp_path) -> None:
    async def rate_limited(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(429, "test-model", {"secret": "provider response"})

    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name: FunctionModel(rate_limited))
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="provider:test-model"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "checks": {
            "runtime": "ready",
            "database": "ready",
            "inference.generation": "unavailable",
        },
    }
    assert "provider response" not in response.text


def test_server_factory_caches_and_redacts_degraded_embedding_readiness(caplog, tmp_path) -> None:
    embedding = _FailingEmbeddingModel(InferenceConfigurationError("secret provider response"))
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        ),
        embedding_model=embedding,
    )

    with caplog.at_level(logging.INFO, logger="powercontext.server.factory"), TestClient(app) as client:
        first = client.get("/health/ready")
        second = client.get("/health/ready")
        metrics = client.get("/metrics")

    assert first.status_code == second.status_code == 200
    assert (
        first.json()
        == second.json()
        == {
            "status": "degraded",
            "checks": {
                "runtime": "ready",
                "database": "ready",
                "inference.embedding": "misconfigured",
            },
        }
    )
    assert "powercontext_server_runtime_ready 1.0" in metrics.text
    assert "secret provider response" not in first.text
    assert "secret provider response" not in caplog.text


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (TimeoutError("secret provider timeout"), "timeout"),
        (OSError("https://secret-provider.example/v1"), "unavailable"),
    ],
)
def test_server_factory_reports_transient_embedding_failures_as_degraded(
    error: Exception,
    expected_status: str,
    tmp_path,
) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        ),
        embedding_model=_FailingEmbeddingModel(error),
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "checks": {
            "runtime": "ready",
            "database": "ready",
            "inference.embedding": expected_status,
        },
    }
    assert "secret" not in response.text


def test_server_provider_cache_shares_refreshes_and_retries_transient_failures_sooner(
    monkeypatch,
    tmp_path,
) -> None:
    now = [0.0]
    monkeypatch.setattr("powercontext.builtin.runtime.readiness.monotonic", lambda: now[0])
    embedding = _SequencedEmbeddingModel(
        [OSError("provider unavailable"), None, None],
        now,
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        ),
        embedding_model=embedding,
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            now[0] = 29
            cached_transient = await client.get("/health/ready")
            now[0] = 30
            refreshed = await asyncio.gather(*(client.get("/health/ready") for _ in range(5)))
            now[0] = 329
            cached_ready = await client.get("/health/ready")
            now[0] = 330
            refreshed_ready = await client.get("/health/ready")

        assert cached_transient.json()["status"] == "degraded"
        assert all(response.json()["status"] == "ready" for response in refreshed)
        assert cached_ready.json()["status"] == "ready"
        assert refreshed_ready.json()["status"] == "ready"

    asyncio.run(scenario())

    assert embedding.requests == [0, 30, 330]


def test_server_factory_reports_missing_embedding_api_prefix_as_degraded(caplog, monkeypatch, tmp_path) -> None:
    now = [0.0]
    monkeypatch.setattr("powercontext.builtin.runtime.readiness.monotonic", lambda: now[0])
    requests: list[httpx.Request] = []

    def reject(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": "secret provider response",
                    "type": "invalid_request_error",
                }
            },
            request=request,
        )

    provider = OpenAIProvider(
        base_url="https://provider.example",
        api_key="secret-api-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(reject)),
    )
    monkeypatch.setattr("pydantic_ai.providers.infer_provider", lambda _name: provider)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(
                embedding_model="openai:text-embedding-3-small",
                embedding_profile_id="readiness-test",
                embedding_dimension=3,
            ),
            mcp=McpConfig(enabled=False),
        )
    )

    with caplog.at_level(logging.INFO, logger="powercontext.server.factory"), TestClient(app) as client:
        first = client.get("/health/ready")
        now[0] = 299
        second = client.get("/health/ready")
        now[0] = 300
        third = client.get("/health/ready")

    assert first.status_code == second.status_code == third.status_code == 200
    assert (
        first.json()
        == second.json()
        == third.json()
        == {
            "status": "degraded",
            "checks": {
                "runtime": "ready",
                "database": "ready",
                "inference.embedding": "misconfigured",
            },
        }
    )
    assert [request.url.path for request in requests] == ["/embeddings", "/embeddings"]
    assert "secret-api-key" not in first.text
    assert "secret provider response" not in first.text
    assert "secret-api-key" not in caplog.text
    assert "secret provider response" not in caplog.text


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


def test_prepare_context_rejects_unicode_surrogates_without_crashing(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    body = '{"scope_id":"project:test","query":"\\udcaa"}'.encode(
        "utf-8",
        "surrogatepass",
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/context/prepare",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert "input" not in error["details"]["errors"][0]


def test_prepare_context_rejects_invalid_request_without_input_field(tmp_path) -> None:
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
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert "input" not in error["details"]["errors"][0]


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
