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

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    CreateArtifactRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    FlushMemoryRequest,
    GeneratePromptDemonstrationsRequest,
    ListArtifactRevisionsRequest,
    ReplaceArtifactRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def _content(instructions: str = "", *, mode: str = "auto") -> dict[str, object]:
    return {
        "schema_version": "powercontext.prompt.v1",
        "mode": mode,
        "instructions": instructions,
        "demonstrations": [],
    }


def test_prompt_http_history_generation_and_scoped_inference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(messages, info) -> ModelResponse:
        request = next(
            json.loads(part.content)
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        if "demonstration_count" in request:
            value = {
                "demonstrations": [
                    {"input": {"evidence": [], "current_entries": []}, "expected_output": {"candidates": []}}
                    for _ in range(request["demonstration_count"])
                ]
            }
        else:
            text = "Scope Alpha preference." if "Alpha rule." in info.instructions else "Scope Beta preference."
            value = {
                "candidates": [
                    {
                        "intent": "add",
                        "kind": "preference",
                        "text": text,
                        "evidence_ids": [request["evidence"][0]["evidence_id"]],
                    }
                ]
            }
        return ModelResponse(parts=[TextPart(json.dumps(value))])

    model = FunctionModel(respond)

    async def open_model(*args, **kwargs):
        return model, model

    monkeypatch.setattr("powercontext.builtin.runtime.composition._open_pydantic_ai_model", open_model)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'prompts.db'}"),
            inference=InferenceConfig(generation_model="test:prompt"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scopes = [
                (
                    await client.create_scope(
                        CreateScopeRequest(
                            title=label, summary="Prompt integration test", idempotency_key=f"prompt-{label}"
                        )
                    )
                ).scope_id
                for label in ("Alpha", "Beta")
            ]
            capabilities = (await transport.get("/v1/capabilities")).json()
            assert len(capabilities["prompts"]) == 6
            assert capabilities["prompts"]["memory.extract"]["status"] == "supported"
            scope = scopes[0]
            for scoped, label in zip(scopes, ("Alpha", "Beta"), strict=True):
                created = await client.create_artifact(
                    scoped,
                    CreateArtifactRequest.model_validate({
                        "family": "prompt",
                        "prompt_key": "memory.extract",
                        "content": _content(f"{label} rule.", mode="custom"),
                    }),
                )
                assert created.artifact_id == "memory.extract"
                assert created.revision == 1
            before = await client.get_artifact(scope, "prompt", "memory.extract")
            assert before is not None
            generated = await client.generate_prompt_demonstrations(
                scope,
                "memory.extract",
                GeneratePromptDemonstrationsRequest(instructions="Keep stable preferences.", demonstration_count=2),
            )
            assert len(generated.demonstrations) == 2
            assert await client.get_artifact(scope, "prompt", "memory.extract") == before
            with pytest.raises(ServerResponseError) as duplicate:
                await client.create_artifact(
                    scope,
                    CreateArtifactRequest.model_validate({
                        "family": "prompt",
                        "prompt_key": "memory.extract",
                        "content": _content(),
                    }),
                )
            assert duplicate.value.status_code == 409

            for scoped in scopes:
                await client.create_source(scoped, CreateSourceRequest(content="I prefer reproducible builds."))
                flushed = await client.flush_memory(FlushMemoryRequest(scope_id=scoped))
                assert flushed.memory is not None
                memory = await client.get_artifact(scoped, "memory", flushed.memory.artifact_id)
                assert memory is not None
                assert any(ref.family == "prompt" and ref.revision == 1 for ref in memory.artifacts)

            auto = await client.replace_artifact(
                scope,
                "prompt",
                "memory.extract",
                ReplaceArtifactRequest.model_validate({"content": _content()}),
                expected_etag='"revision:1"',
            )
            assert auto.revision == 2
            with pytest.raises(ServerResponseError) as stale:
                await client.replace_artifact(
                    scope,
                    "prompt",
                    "memory.extract",
                    ReplaceArtifactRequest.model_validate({"content": before.content}),
                    expected_etag='"revision:1"',
                )
            assert stale.value.status_code == 412
            restored = await client.replace_artifact(
                scope,
                "prompt",
                "memory.extract",
                ReplaceArtifactRequest.model_validate({"content": before.content}),
                expected_etag='"revision:2"',
            )
            assert restored.revision == 3
            assert restored.content_digest == before.content_digest
            page = await client.list_artifact_revisions(
                scope, "prompt", "memory.extract", ListArtifactRevisionsRequest(limit=1)
            )
            assert [item.revision for item in page.items] == [3]
            assert page.next_cursor is not None
            assert "content" not in page.items[0].model_dump()
            tail = await client.list_artifact_revisions(
                scope, "prompt", "memory.extract", ListArtifactRevisionsRequest(cursor=page.next_cursor)
            )
            assert [item.revision for item in tail.items] == [2, 1]
            with pytest.raises(ServerResponseError) as wrong_scope:
                await client.list_artifact_revisions(
                    scopes[1], "prompt", "memory.extract", ListArtifactRevisionsRequest(cursor=page.next_cursor)
                )
            assert wrong_scope.value.status_code == 400
            other_scope_prompt = await client.get_artifact(scopes[1], "prompt", "memory.extract")
            assert other_scope_prompt is not None and other_scope_prompt.revision == 1
            assert (
                await transport.post(
                    f"/v1/scopes/{scope}/prompts/memory.extract/demonstrations",
                    json={"instructions": "valid", "demonstration_count": 21},
                )
            ).status_code == 422
            assert (
                await transport.post(
                    "/v1/scopes/missing/prompts/memory.extract/demonstrations",
                    json={"instructions": "valid", "demonstration_count": 1},
                )
            ).status_code == 404

    asyncio.run(scenario())
