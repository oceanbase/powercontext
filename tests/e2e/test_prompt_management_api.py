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
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.artifacts.memory import MemoryExtractionProfile, memory_extraction_instructions
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.builtin.runtime.config import RuntimeConfig
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
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


def _content(instructions: str = "", *, mode: str = "auto") -> dict[str, object]:
    return {
        "schema_version": "powercontext.prompt.v1",
        "mode": mode,
        "instructions": instructions,
        "demonstrations": [],
    }


@pytest.mark.parametrize("profile", list(MemoryExtractionProfile))
def test_prompt_configuration_previews_defaults_without_inference(
    tmp_path: Path, profile: MemoryExtractionProfile
) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'preview.db'}"),
            runtime=RuntimeConfig(memory_extraction_profile=profile),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=tmp_path / "scheduler.db",
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            created = await transport.post(
                "/v1/scopes",
                json={"title": "Preview", "summary": "Default prompt preview", "idempotency_key": "preview"},
            )
            assert created.status_code == 201
            scope = created.json()["scope_id"]
            response = await transport.get(f"/v1/scopes/{scope}/prompts/memory.extract")
            assert response.status_code == 200
            value = response.json()
            assert value["mode"] == "auto"
            assert value["status"] == "disabled"
            assert value["artifact"] is None and value["artifact_etag"] is None
            assert value["effective"] == {"instructions": memory_extraction_instructions(profile), "demonstrations": []}
            assert value["builtin"]["instructions"] == value["effective"]["instructions"]
            assert value["builtin"]["profile"] == profile.value
            assert response.headers["cache-control"] == "no-store"
            records = await transport.get(f"/v1/scopes/{scope}/artifacts/prompt")
            assert records.json()["items"] == []
            assert (await transport.get("/v1/scopes/missing/prompts/memory.extract")).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("access_mode", ["disabled", "enforced"])
def test_prompt_http_history_generation_and_scoped_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, access_mode: str
) -> None:
    token = "prompt-test-token"  # noqa: S105 - disposable test credential

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
            auth=BearerAuthConfig(
                enabled=access_mode == "enforced", token=SecretStr(token) if access_mode == "enforced" else None
            ),
            access=AccessControlConfig.model_validate({"mode": access_mode}),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer prompt-test-token"},
            ) as transport,
        ):
            client = PowerContextClient(
                "http://testserver", token=token, http_client=transport, trust_transport_security=True
            )
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
            initial = await client.get_prompt_configuration(scope, "memory.extract")
            assert initial.mode == "auto" and initial.artifact is None
            assert initial.effective is not None and initial.builtin is not None
            assert initial.effective.instructions == initial.builtin.instructions
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
                configuration = await client.get_prompt_configuration(scoped, "memory.extract")
                assert configuration.mode == "custom" and configuration.artifact is not None
                assert configuration.artifact.revision == 1
                assert configuration.artifact_etag == '"revision:1"'
                assert configuration.effective is not None
                assert configuration.effective.instructions == f"{label} rule."
                assert configuration.builtin == initial.builtin
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
            auto_configuration = await client.get_prompt_configuration(scope, "memory.extract")
            assert auto_configuration.mode == "auto" and auto_configuration.artifact is not None
            assert auto_configuration.artifact.revision == 2
            assert auto_configuration.effective == initial.effective
            assert auto_configuration.artifact_etag == '"revision:2"'
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
            restored_configuration = await client.get_prompt_configuration(scope, "memory.extract")
            assert restored_configuration.artifact is not None and restored_configuration.artifact.revision == 3
            assert restored_configuration.effective is not None
            assert restored_configuration.effective.instructions == "Alpha rule."
            assert restored_configuration.builtin == initial.builtin
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
