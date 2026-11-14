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

"""Public HTTP and spawned SQLite Worker; inference uses a local deterministic provider."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from functools import partial

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime import composition as runtime_composition
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.topic_memory_product.common import start_loopback_server


def test_http_worker_rejects_placeholder_then_publishes_grounded_revision(tmp_path, monkeypatch, caplog, capfd):
    monkeypatch.setattr(
        runtime_composition,
        "ArtifactProcessingSupervisor",
        partial(
            runtime_composition.ArtifactProcessingSupervisor,
            retry_base_seconds=0.05,
            retry_cap_seconds=0.05,
            retry_jitter=lambda: 1.0,
        ),
    )
    provider_app = FastAPI()
    permit_valid_output = threading.Event()
    global_calls = []
    valid = {
        "title": "Zircon deployment",
        "summary": "Zircon deployment checks passed.",
        "detail": "The dashboard confirms that Zircon deployment checks passed.",
    }
    old = {
        "title": "Zircon deployment",
        "summary": "Zircon deployment checks are pending.",
        "detail": "Inspect the dashboard before confirming deployment checks.",
    }

    @provider_app.post("/v1/chat/completions")
    async def generate(request: Request):
        payload = await request.json()
        rendered = json.dumps(payload)
        value = json.loads(payload["messages"][-1]["content"])
        evidence_id = value["evidence"][0]["evidence_id"]
        if "TopicMemoryProbeOutput" in rendered:
            output = {"probes": [{"query": "Zircon deployment", "evidence_ids": [evidence_id]}]}
        else:
            assert "TopicMemoryGlobalOutput" in rendered
            global_calls.append(payload)
            if len(global_calls) == 1:
                content = {
                    "title": "T-19",
                    "summary": "S-19 coordinated without merging historical identities.",
                    "detail": "D-19 coordinated detail preserving distinct historical identity.",
                }
            else:
                assert await asyncio.to_thread(permit_valid_output.wait, 30)
                content = valid
            output = {
                "proposals": [
                    {
                        "candidate_id": value["historical"][0]["candidate_id"],
                        "content": content,
                        "evidence_ids": [evidence_id],
                    }
                ]
            }
        return {
            "id": "admission-response",
            "object": "chat.completion",
            "created": 0,
            "model": "admission-test",
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(output)}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    provider = start_loopback_server(provider_app)
    database_path = tmp_path / "admission.db"
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
        runtime=RuntimeConfig(
            artifact_processing_families=("topic-memory",),
            topic_memory_source_window_limit=4,
            artifact_processing_worker_timeout_seconds=60,
        ),
        inference=InferenceConfig(
            generation_model="openai-chat:admission-test",
            generation_base_url=AnyHttpUrl(f"{provider.base_url}/v1"),
            generation_headers={"Authorization": SecretStr("Bearer local-fixture")},
            generation_max_requests=1,
            generation_model_settings={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
        mcp=McpConfig(enabled=False),
    )
    try:
        with TestClient(create_server_app(settings=settings)) as client:
            created = client.post(
                "/v1/scopes", json={"title": "Admission", "summary": "Admission", "idempotency_key": "admission"}
            )
            assert created.status_code == 201, created.text
            scope = created.json()["scope_id"]
            base = f"/v1/scopes/{scope}/artifacts/topic-memory"
            seeded = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "topic-memory", "content": old})
            assert seeded.status_code == 201, seeded.text
            artifact = seeded.json()["artifact_id"]
            captured = client.post(
                "/v1/sources/content", json={"scope_id": scope, "source_id": "check-result", "content": valid["detail"]}
            )
            assert captured.is_success, captured.text
            flush = client.post("/v1/topic-memory/flush", json={"scope_id": scope})
            assert flush.status_code == 200, flush.text
            deadline = time.monotonic() + 60
            while True:
                with sqlite3.connect(database_path) as database:
                    failure = database.execute("SELECT failure_code FROM pc_topic_memory_work_budgets").fetchone()
                if failure and failure[0] == "invalid_topic_placeholder":
                    break
                assert time.monotonic() < deadline, "No durable content rejection observed"
                time.sleep(0.05)
            head = client.get(base).json()["items"]
            assert len(head) == 1 and head[0]["revision"] == 1
            previous = client.get(f"{base}/{artifact}/revisions/1")
            assert previous.status_code == 200, previous.text
            assert previous.json()["content"] == old
            permit_valid_output.set()
            deadline = time.monotonic() + 60
            while True:
                heads = client.get(base).json()["items"]
                if len(heads) == 1 and heads[0]["revision"] == 2:
                    break
                assert time.monotonic() < deadline, "Worker did not recover after content rejection"
                time.sleep(0.05)
            revised = client.get(f"{base}/{artifact}/revisions/2")
            assert revised.status_code == 200, revised.text
            assert revised.json()["content"] == valid
            assert client.get(f"{base}/{artifact}/revisions/1").json()["content"] == old
            assert len(global_calls) == 2
            assert any(getattr(record, "error_code", None) == "invalid_topic_placeholder" for record in caplog.records)
        child_output = capfd.readouterr().out
        assert 'error_code="invalid_topic_placeholder"' in child_output
        assert 'error_category="dependency"' in child_output
        assert "S-19 coordinated" not in child_output + caplog.text
    finally:
        permit_valid_output.set()
        provider.stop()
