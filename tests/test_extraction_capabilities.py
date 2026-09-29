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

import httpx
import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.inference.errors import (
    InferenceConfigurationError,
    InferenceTimeoutError,
    InferenceUnavailableError,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class _RecoveringPipeline:
    def __init__(self, failure: Exception) -> None:
        self.failure: Exception | None = failure

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        if self.failure is not None:
            raise self.failure
        return ()


def test_capabilities_reports_unconfigured_extraction_without_claiming_health(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'diagnostics.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:
        response = client.get("/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["memory_extraction"] is False
        extraction = response.json()["extraction"]
        assert extraction["model_configured"] is False
        assert extraction["worker_status"] == "disabled"
        assert extraction["automatic_processing_enabled"] is False
        assert extraction["last_result"] == "unverified"
        assert extraction["last_error"] is None

        async def read_with_sdk() -> None:
            async with httpx.AsyncClient(
                base_url="http://testserver", transport=httpx.ASGITransport(app=app)
            ) as transport:
                sdk = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
                capabilities = await sdk.get_capabilities()
                assert capabilities.extraction is not None
                assert capabilities.extraction.model_configured is False

        assert client.portal is not None
        client.portal.call(read_with_sdk)


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (InferenceConfigurationError("secret-provider-rejection"), "model_configuration_error"),
        (InferenceTimeoutError("memory.extract", 0.01), "model_timeout"),
        (InferenceUnavailableError("memory.extract", "secret-provider-response"), "model_unavailable"),
    ],
)
def test_capabilities_observes_extraction_failure_and_recovery_after_startup(tmp_path, failure, category) -> None:
    pipeline = _RecoveringPipeline(failure)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'diagnostics.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=pipeline,
    )
    with TestClient(app) as client:
        initial = client.get("/v1/capabilities").json()["extraction"]
        assert initial["model_configured"] is True
        assert initial["last_result"] == "unverified"
        assert initial["last_error"] is None
        assert initial["last_success_at"] is None
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        captured = client.post(
            "/v1/sources/content",
            json={"scope_id": scope_id, "source_id": "one", "content": "Keep this decision.", "metadata": {}},
        )
        assert captured.status_code == 202
        assert client.post("/v1/memory/flush", json={"scope_id": scope_id}).is_error
        response = client.get("/v1/capabilities")
        failed = response.json()["extraction"]
        assert failed["last_result"] == "failed"
        assert failed["last_error"] == category
        assert failed["last_error_at"] is not None
        assert failed["last_success_at"] is None
        assert "secret-provider" not in response.text

        pipeline.failure = None
        assert client.post("/v1/memory/flush", json={"scope_id": scope_id}).status_code == 200
        recovered = client.get("/v1/capabilities").json()["extraction"]
        assert recovered["last_result"] == "succeeded"
        assert recovered["last_error"] == category
        assert recovered["last_error_at"] == failed["last_error_at"]
        assert recovered["last_success_at"] > recovered["last_error_at"]
        assert client.post("/v1/memory/flush", json={"scope_id": scope_id}).status_code == 200
        assert client.get("/v1/capabilities").json()["extraction"]["last_success_at"] == recovered["last_success_at"]

    with TestClient(app) as client:
        restarted = client.get("/v1/capabilities").json()["extraction"]
        assert restarted["last_result"] == "unverified"
        assert restarted["last_error"] is None
        assert restarted["last_success_at"] is None
