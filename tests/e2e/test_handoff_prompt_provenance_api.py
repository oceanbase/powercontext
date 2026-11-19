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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    ContinueHandoffRequest,
    CreateArtifactRequest,
    CreateScopeRequest,
    CreateSourceRequest,
    FinalizeHandoffRequest,
    PrepareHandoffRequest,
    ReplaceArtifactRequest,
)
from powercontext.server.configuration import server_settings_context
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


@asynccontextmanager
async def _handoff_client(tmp_path: Path, inference: InferenceConfig) -> AsyncIterator[PowerContextClient]:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handoff.db'}"),
            inference=inference,
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver"
        ) as transport,
    ):
        yield PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)


def _script_handoff_model(monkeypatch: pytest.MonkeyPatch, respond) -> None:
    def model_response(messages, info) -> ModelResponse:
        request = next(
            json.loads(part.content)
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        return ModelResponse(parts=[TextPart(json.dumps(respond(request, info)))])

    model = FunctionModel(model_response)

    async def open_model(*args, **kwargs):
        return model, model

    monkeypatch.setattr("powercontext.builtin.runtime.composition._open_pydantic_ai_model", open_model)


def test_handoff_provenance_survives_separate_requests_and_rejects_forgery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def respond(request, info):
        return {
            "state": [
                {"text": "The integration test passed.", "evidence_ids": [request["evidence"][0]["evidence_id"]]}
            ],
            "disposition": "complete",
            "next_action": None,
            "omissions": [],
        }

    _script_handoff_model(monkeypatch, respond)

    async def scenario() -> None:
        async with _handoff_client(tmp_path, InferenceConfig(generation_model="test:handoff")) as client:
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


async def _compress_calibration_twice(client: PowerContextClient) -> None:
    scope = (await client.get_default_scope()).scope_id
    source = await client.create_source(
        scope,
        CreateSourceRequest(
            content=(
                "The calibration file is named calibration.json. I suspect the offset is 12 degrees because of "
                "the single reading CAL-17. The offset has not been confirmed."
            )
        ),
    )
    request = PrepareHandoffRequest.model_validate({
        "scope_id": scope,
        "objective": "Summarize the calibration investigation for the next shift. Keep file names, numbers and reading IDs.",
        "evidence": [{"kind": "source", "source_ref": {"name": "content", "source_id": source.source_id}}],
    })
    for selection in ("built_in", "artifact"):
        if selection == "artifact":
            # A saved v1 demonstration may leave out its empty omissions list.
            await client.create_artifact(
                scope,
                CreateArtifactRequest.model_validate({
                    "family": "prompt",
                    "prompt_key": "handoff.generate",
                    "content": {
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "custom",
                        "instructions": "Use concise English.",
                        "demonstrations": [
                            {
                                "input": {
                                    "objective": "Record the file name.",
                                    "max_bytes": 4096,
                                    "evidence": [
                                        {
                                            "evidence_id": "source:0",
                                            "evidence_type": "source",
                                            "content": "The file is a.txt.",
                                        }
                                    ],
                                },
                                "expected_output": {
                                    "disposition": "complete",
                                    "state": [{"text": "The file is a.txt.", "evidence_ids": ["source:0"]}],
                                },
                            }
                        ],
                    },
                }),
            )
        draft = await client.prepare_handoff(request)
        assert any("calibration.json" in item.text for item in draft.state)
        assert all("12" not in item.text for item in draft.state), "the suspected offset entered factual state"
        assert any("12" in item.text and "CAL-17" in item.text for item in draft.omissions), (
            "lost the offset or its basis"
        )
        prepared = await client.finalize_handoff(FinalizeHandoffRequest(scope_id=scope, draft=draft))
        committed = await client.commit_handoff(CommitHandoffRequest(scope_id=scope, handoff=prepared))
        assert committed.content.omissions == draft.omissions
        metadata = committed.content.generation
        assert metadata is not None and metadata.selection.value == selection
        assert metadata.edit_status.value == "unchanged"
        assert metadata.definition_version == "powercontext.prompt.handoff.generate.v2"
        assert metadata.builtin_version == "powercontext.handoff.generate.v3"
        request = PrepareHandoffRequest.model_validate({
            "scope_id": scope,
            "objective": request.objective,
            "evidence": [{"kind": "artifact", "artifact_ref": committed.reference.model_dump(mode="json")}],
        })


def test_handoff_omission_guidance_reaches_user_message_for_auto_and_custom_prompts(tmp_path, monkeypatch) -> None:
    observed = []

    def respond(request, info):
        observed.append((request, info.instructions))
        evidence = request["evidence"][0]
        text = "The offset may be 12 degrees, based on the single reading CAL-17."
        if evidence["evidence_type"] == "artifact":
            text = evidence["content"]["content"]["omissions"][0]["text"]
        return {
            "state": [{"text": "The file is named calibration.json.", "evidence_ids": [evidence["evidence_id"]]}],
            "disposition": "blocked",
            "omissions": [{"text": text, "evidence_id": evidence["evidence_id"]}],
        }

    _script_handoff_model(monkeypatch, respond)

    async def scenario() -> None:
        async with _handoff_client(tmp_path, InferenceConfig(generation_model="test:handoff")) as client:
            await _compress_calibration_twice(client)

    asyncio.run(scenario())
    assert len(observed) == 2
    for request, instructions in observed:
        rule = request["omission_guidance"]
        assert "stated basis" in rule and "missing evidence" in rule
        assert rule not in instructions
    assert "Use concise English." in observed[1][1]


def test_rejected_model_omission_stays_out_of_handoff_and_logs(tmp_path, monkeypatch, caplog) -> None:
    marker = "private-calibration-marker-7814"
    attempts = 0

    def respond(request, info):
        nonlocal attempts
        attempts += 1
        return {
            "state": [{"text": "A reading is available.", "evidence_ids": [request["evidence"][0]["evidence_id"]]}],
            "disposition": "blocked",
            "omissions": [{"text": "Offset unconfirmed.", "basis": marker}],
        }

    _script_handoff_model(monkeypatch, respond)

    async def scenario() -> None:
        inference = InferenceConfig(generation_model="test:handoff", generation_max_requests=2)
        async with _handoff_client(tmp_path, inference) as client:
            scope = (await client.get_default_scope()).scope_id
            source = await client.create_source(scope, CreateSourceRequest(content="A reading is available."))
            with pytest.raises(ServerResponseError) as rejected:
                await client.prepare_handoff(
                    PrepareHandoffRequest.model_validate({
                        "scope_id": scope,
                        "objective": "Summarize the reading.",
                        "evidence": [
                            {"kind": "source", "source_ref": {"name": "content", "source_id": source.source_id}}
                        ],
                    })
                )
            assert rejected.value.status_code == 500
            assert marker not in str(rejected.value)
            latest = await client.continue_handoff(
                ContinueHandoffRequest.model_validate({"scope_id": scope, "selection": "latest"})
            )
            assert latest.status.value == "empty"

    asyncio.run(scenario())
    assert attempts == 2
    assert marker not in caplog.text


@pytest.mark.real_e2e
def test_live_handoff_retains_calibration_markers_across_two_compressions(
    tmp_path: Path, pytestconfig: pytest.Config
) -> None:
    if not pytestconfig.getoption("run_real_e2e"):
        pytest.skip("requires --run-real-e2e and a configured generation model")
    env_file = Path(pytestconfig.getoption("real_e2e_env_file"))

    async def scenario() -> None:
        with server_settings_context(env_file=env_file, data_dir=tmp_path) as settings:
            model = settings.inference.generation_model
            assert model and model.split(":", 1)[0] != "test", (
                "configure a real generation model with --real-e2e-env-file"
            )
            async with _handoff_client(tmp_path, settings.inference) as client:
                await _compress_calibration_twice(client)

    asyncio.run(scenario())
