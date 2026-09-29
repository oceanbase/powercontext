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

import time

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.topic_memory_product.common import FakeInference, start_loopback_server


@pytest.mark.parametrize("families", [None, ("topic-memory",)], ids=["inferred", "declared"])
def test_qwen_thinking_disabled_reaches_scheduled_worker_and_publication(tmp_path, families):
    fake = FakeInference(generation_delay_seconds=0)
    provider_app = fake.app()
    requests = []

    @provider_app.middleware("http")
    async def record(request: Request, call_next):
        if request.url.path == "/v1/chat/completions":
            requests.append(await request.json())
        return await call_next(request)

    provider = start_loopback_server(provider_app)
    try:
        settings = ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            runtime=RuntimeConfig(
                artifact_processing_families=families,
                topic_memory_schedule_seconds=0.1,
                artifact_processing_worker_timeout_seconds=60,
            ),
            inference=InferenceConfig(
                generation_model="openai-chat:qwen-test",
                generation_base_url=AnyHttpUrl(f"{provider.base_url}/v1"),
                generation_headers={"Authorization": SecretStr("Bearer synthetic-test")},
                generation_max_requests=1,
                generation_model_settings={
                    "max_tokens": 1024,
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
                },
            ),
            mcp=McpConfig(enabled=False),
        )
        with TestClient(create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")) as client:
            created = client.post(
                "/v1/scopes", json={"title": "Qwen", "summary": "Qwen settings", "idempotency_key": "qwen"}
            )
            assert created.status_code == 201, created.text
            scope_id = created.json()["scope_id"]
            captured = client.post(
                "/v1/sources/content",
                json={"scope_id": scope_id, "source_id": "qwen-source", "content": f"Remember {fake.canary}."},
            )
            assert captured.is_success, captured.text
            deadline = time.monotonic() + 60
            while True:
                listed = client.get(f"/v1/scopes/{scope_id}/artifacts/topic-memory")
                assert listed.status_code == 200, listed.text
                items = listed.json()["items"]
                if items:
                    break
                assert time.monotonic() < deadline, fake.redacted_calls()
                time.sleep(0.1)
            assert fake.canary in items[0]["summary"]
        assert {call["stage"] for call in fake.redacted_calls()} >= {"topic_probe", "topic_global"}
        assert requests
        for body in requests:
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            assert body["model"] == "qwen-test"
            assert 0 < body.get("max_completion_tokens", body.get("max_tokens", 0)) <= 1024
            assert "extra_body" not in body
            assert not body.get("tools")
    finally:
        provider.stop()
