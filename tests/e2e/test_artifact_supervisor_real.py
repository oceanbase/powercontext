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

"""Hermetic regression for the real-provider Supervisor acceptance CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from tests.e2e.topic_memory_product.common import FakeInference, start_loopback_server


def _source_family_output(schema: str, request: dict[str, Any]) -> dict[str, Any]:
    if schema == "ProfileGenerationOutput":
        return {"content": "# Engineering profile\n\nPrefers pytest and narrowly scoped changes."}
    if schema == "ExperienceIncubationOutput":
        return {
            "candidates": [
                {
                    "proposal": {
                        "situation": "A configuration change bypassed validation.",
                        "action": "Added a strict configuration check.",
                        "outcome": "The check passed and the release succeeded.",
                        "lesson": "Run the strict configuration test after changing configuration.",
                    },
                    "evidence_ids": [item["evidence_id"] for item in request["evidence"]],
                }
            ]
        }
    if schema == "AtomicMemoryExtractionOutput":
        return {
            "candidates": [
                {
                    "kind": "preference",
                    "text": "The user prefers narrowly scoped code changes.",
                    "description": "Code change scope preference",
                    "evidence_ids": [item["evidence_id"] for item in request["evidence"]],
                }
            ]
        }
    assert schema == "AtomicMemoryReconciliationOutput"
    return {
        "action": "create",
        "compared_ids": [item["item_id"] for item in request["related"]],
        "content": {
            "kind": request["proposal"]["kind"],
            "text": request["proposal"]["text"],
            "description": request["proposal"]["description"],
        },
        "evidence_ids": request["proposal"]["evidence_ids"],
        "reason": "Keep the preference with its Source evidence.",
    }


def test_supervisor_acceptance_retrieves_scope_owned_topics(tmp_path, monkeypatch) -> None:
    app = FakeInference(canary="strict configuration validation", generation_delay_seconds=0).app()

    @app.middleware("http")
    async def source_family_generation(request: Request, call_next):
        if request.url.path == "/v1/chat/completions":
            payload = await request.json()
            rendered = json.dumps(payload)
            schemas = (
                "AtomicMemoryExtractionOutput",
                "AtomicMemoryReconciliationOutput",
                "ExperienceIncubationOutput",
                "ProfileGenerationOutput",
            )
            schema = next((name for name in schemas if name in rendered), None)
            if schema is not None:
                content = next(message["content"] for message in payload["messages"] if message["role"] == "user")
                output = _source_family_output(schema, json.loads(content))
                return JSONResponse({
                    "id": "supervisor-generation-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": json.dumps(output)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })
        return await call_next(request)

    monkeypatch.setenv("OPENAI_API_KEY", "hermetic-supervisor-key")
    server = start_loopback_server(app)
    env_file = tmp_path / "inference.env"
    directory = tmp_path / "acceptance"
    script = Path(__file__).with_name("artifact_supervisor_real.py")
    try:
        env_file.write_text(
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:gpt-4o-mini\n"
            f"POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL={server.base_url}/v1\n"
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openai:text-embedding-3-small\n"
            f"POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL={server.base_url}/v1\n"
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=supervisor-test\n"
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=4\n",
            encoding="utf-8",
        )
        process = subprocess.run(
            [
                sys.executable,
                str(script),
                "--backend",
                "sqlite",
                "--env-file",
                str(env_file),
                "--output",
                str(directory),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    finally:
        server.stop()

    assert process.returncode == 0, f"{process.stdout}\n{process.stderr}"
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert report["artifact_counts"]["topic-memory"] >= 1
    assert report["ownership_counts"].get("topic-memory", 0) == 0
    assert all(report["ownership_counts"][family] >= 1 for family in ("atomic-memory", "experience", "profile"))
    assert report["cursors"]["topic-memory"] == report["source_journal_position"] == 2
    assert report["retrieval"]["mode"] == "hybrid"
    assert report["retrieval"]["vector_hit_count"] > 0
    assert report["retrieval"]["all_hits_belong_to_expected_scope"]
