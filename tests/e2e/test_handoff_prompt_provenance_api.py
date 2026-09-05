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
    CommitHandoffRequest,
    CreateArtifactRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    FinalizeHandoffRequest,
    PrepareHandoffRequest,
    ReplaceArtifactRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def test_handoff_provenance_survives_separate_requests_and_rejects_forgery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def respond(messages, info) -> ModelResponse:
        request = next(
            json.loads(part.content)
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps({
                        "state": [
                            {
                                "text": "The integration test passed.",
                                "evidence_ids": [request["evidence"][0]["evidence_id"]],
                            }
                        ],
                        "disposition": "complete",
                        "next_action": None,
                        "omissions": [],
                    })
                )
            ]
        )

    model = FunctionModel(respond)

    async def open_model(*args, **kwargs):
        return model, model

    monkeypatch.setattr("powercontext.builtin.runtime.composition._open_pydantic_ai_model", open_model)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handoff.db'}"),
            inference=InferenceConfig(generation_model="test:handoff"),
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
            scope = (await client.get_default_scope()).scope_id
            other = (
                await client.create_scope(
                    CreateScopeRequest(
                        title="Other", summary="Cross-Scope receipt validation", idempotency_key="receipt-other"
                    )
                )
            ).scope_id
            content = {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Keep verified test results.",
                "demonstrations": [],
            }
            await client.create_artifact(
                scope,
                CreateArtifactRequest.model_validate({
                    "family": "prompt",
                    "prompt_key": "handoff.generate",
                    "content": content,
                }),
            )
            source = await client.create_source(scope, CreateSourceRequest(content="The integration test passed."))
            request = PrepareHandoffRequest.model_validate({
                "scope_id": scope,
                "objective": "Continue integration testing.",
                "evidence": [{"kind": "source", "source_ref": {"name": "content", "source_id": source.source_id}}],
            })
            draft = await client.prepare_handoff(request)
            assert draft.generation is not None
            await client.replace_artifact(
                scope,
                "prompt",
                "handoff.generate",
                ReplaceArtifactRequest.model_validate({
                    "content": content | {"instructions": "Keep deployment results."}
                }),
                expected_etag='"revision:1"',
            )
            edited = draft.model_copy(update={"objective": "Continue with the corrected test objective."})
            prepared = await client.finalize_handoff(FinalizeHandoffRequest(scope_id=scope, draft=edited))
            assert prepared.generation == draft.generation
            committed = await client.commit_handoff(CommitHandoffRequest(scope_id=scope, handoff=prepared))
            metadata = committed.content.generation
            assert metadata is not None and metadata.artifact is not None
            assert metadata.artifact.revision == 1
            assert metadata.edit_status.value == "edited"
            assert any(ref.family == "prompt" and ref.revision == 1 for ref in committed.artifact_refs)
            exact = await client.get_artifact(scope, "handoff", committed.reference.artifact_id)
            assert exact is not None
            assert "receipt" not in json.dumps(exact.content)
            assert "generation" in exact.content
            replay = await client.commit_handoff(CommitHandoffRequest(scope_id=scope, handoff=prepared))
            assert replay.reference == committed.reference

            for bad_scope, bad_draft in (
                (other, draft),
                (
                    scope,
                    draft.model_copy(
                        update={
                            "generation": draft.generation.model_copy(
                                update={"receipt": draft.generation.receipt + "x"}
                            )
                        }
                    ),
                ),
            ):
                with pytest.raises(ServerResponseError) as invalid:
                    await client.finalize_handoff(FinalizeHandoffRequest(scope_id=bad_scope, draft=bad_draft))
                assert invalid.value.status_code == 422
                assert invalid.value.code == "invalid_handoff_generation"

            with pytest.raises(ServerResponseError) as raw:
                await client.replace_artifact(
                    scope,
                    "handoff",
                    committed.reference.artifact_id,
                    ReplaceArtifactRequest.model_validate({
                        "content": committed.content.model_dump(mode="json", by_alias=True)
                    }),
                    expected_etag=f'"revision:{committed.reference.revision}"',
                )
            assert raw.value.status_code == 422
            assert raw.value.code == "invalid_handoff_generation"

            manual_draft = edited.model_copy(update={"generation": None})
            manual = await client.finalize_handoff(FinalizeHandoffRequest(scope_id=scope, draft=manual_draft))
            manual_commit = await client.commit_handoff(CommitHandoffRequest(scope_id=scope, handoff=manual))
            assert manual_commit.reference.revision == committed.reference.revision + 1
            assert manual_commit.content.generation is None
            assert not any(ref.family == "prompt" for ref in manual_commit.artifact_refs)

    asyncio.run(scenario())
