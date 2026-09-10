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

"""Configured spawned Workers honor persisted Prompts and count inference once."""

from __future__ import annotations

import asyncio
import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest
from pydantic import AnyHttpUrl
from sqlalchemy import func, select

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.artifacts.prompt import PromptError
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.usage import UsageReportingEmbeddingModel, bind_usage_reporter
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_HEADS_TABLE,
    BUILTIN_TABLES,
    MODEL_USAGE_DAILY_TABLE,
)
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import CaptureSource
from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
from powercontext.builtin.runtime.composition import _usage_reporting_embedding_model, open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS, FamilyWorkerSpec, run_family_worker
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.repository import ACCESS_OWNERS_TABLE, ACCESS_TABLES
from powercontext.server.processing_security import WorkerSecuritySpec


class _InferenceServer(ThreadingHTTPServer):
    requests: list[tuple[str, dict[str, Any]]]
    family: str


class _InferenceHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server = cast(_InferenceServer, self.server)
        server.requests.append((self.path, payload))
        if self.path == "/v1/embeddings":
            inputs = payload["input"]
            body = {
                "object": "list",
                "model": "test-embedding",
                "data": [
                    {"object": "embedding", "index": index, "embedding": [1.0, 0.0]} for index in range(len(inputs))
                ],
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            }
        else:
            if server.family == "memory":
                candidate = {
                    "intent": "add",
                    "kind": "preference",
                    "text": "The user requires spawned Worker validation.",
                    "evidence_ids": ["source:0"],
                }
            else:
                candidate = {
                    "proposal": {
                        "situation": "A configured Worker runs in another process.",
                        "action": "Restore its supported Prompt registry.",
                        "outcome": "The persisted guidance is honored.",
                        "lesson": "Validate reconstructed capabilities in a spawned process.",
                    },
                    "evidence_ids": ["source:content/evidence"],
                }
            body = {
                "id": "worker-prompt-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps({"candidates": [candidate]})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 13, "total_tokens": 24},
            }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib parameter
        pass


@pytest.mark.parametrize("family", ["memory", "experience"])
def test_spawned_worker_restores_custom_prompt_and_counts_actual_requests_once(tmp_path, monkeypatch, family):
    # A configured loopback provider crosses the real spawn/serialization boundary:
    # no injected pipeline, model monkeypatch, or preconstructed PromptRegistry reaches the child.
    monkeypatch.setenv("OPENAI_API_KEY", "hermetic-worker-key")
    server = _InferenceServer(("127.0.0.1", 0), _InferenceHandler)
    server.requests = []
    server.family = family
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    async def scenario():
        endpoint = AnyHttpUrl(f"http://127.0.0.1:{server.server_port}/v1")
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}"),
            inference=InferenceConfig(
                generation_model="openai-chat:gpt-4o-mini",
                generation_base_url=endpoint,
                embedding_model="openai:test-embedding" if family == "memory" else None,
                embedding_base_url=endpoint if family == "memory" else None,
                embedding_dimension=2 if family == "memory" else None,
                embedding_profile_id="worker-test" if family == "memory" else None,
            ),
            runtime=RuntimeConfig(
                artifact_processing_families=(family,),
                memory_extraction_profile=MemoryExtractionProfile.CONVERSATION,
            ),
        )
        prompt_key = "memory.extract" if family == "memory" else "experience.incubate"
        marker = f"PERSISTED_CUSTOM_{family.upper()}_GUIDANCE"
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(ScopeDraft(title="Worker", summary="Worker", idempotency_key="worker"))
            ).scope_id
            await runtime.records.for_scope(scope).create_artifact(
                "prompt",
                ArtifactWrite(
                    prompt_key=prompt_key,
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "custom",
                        "instructions": f"{marker}: Preserve the supplied evidence and return one bounded candidate.",
                        "demonstrations": [],
                    },
                ),
            )
            receipt = await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="evidence",
                    content="The user requires spawned Worker tests. Restoring the Prompt registry made one pass.",
                    metadata={"kind": "task-outcome"},
                )
            )
        assert server.requests == []
        assert isinstance(config.database, SQLiteConfig)
        async with SQLiteProfile.open(config.database, tables=(*BUILTIN_TABLES, *ACCESS_TABLES)) as profile:
            async with profile.database.transaction() as connection:
                lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "worker")
                intent = await ArtifactProcessingIntentRepository().request(connection, scope, FAMILY_BINDINGS[family])
            assignment = ArtifactProcessingWorkAssignment(
                binding_name=FAMILY_BINDINGS[family],
                scope_id=scope,
                artifact_family=family,
                claimed_request_generation=intent.requested_generation,
                fence=lease.fence("single-process"),
                worker_id="worker-prompt-test",
            )
            security = WorkerSecuritySpec(
                principal=PrincipalRef(type="service", id="worker-test"),
                deployment_id="test",
                static_preset=True,
            ).model_dump(mode="json")
            launcher = SpawnArtifactProcessingWorkerLauncher(
                partial(run_family_worker, FamilyWorkerSpec(config=config, worker_security=security))
            )
            for _ in range(2):
                worker = await launcher.start(assignment)
                try:
                    result = await asyncio.wait_for(worker.wait(), timeout=45)
                    assert result.outcome == "succeeded", result
                finally:
                    await worker.terminate()
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, scope, assignment.binding_name)
                    completed = await ArtifactProcessingIntentRepository().load(
                        connection, scope, assignment.binding_name
                    )
                    assert cursor is not None and cursor.cursor.sequence == receipt.sequence
                    assert (
                        completed is not None and completed.handled_generation == assignment.claimed_request_generation
                    )
                    assert completed.clean_generation == completed.dirty_generation
                    generated = ARTIFACT_HEADS_TABLE if family == "memory" else ARTIFACT_CANDIDATE_HEADS_TABLE
                    assert (
                        await connection.scalar(
                            select(func.count()).select_from(generated).where(generated.c.family == family)
                        )
                        == 1
                    )
                    assert await connection.scalar(select(func.count()).select_from(ACCESS_OWNERS_TABLE)) == 1
                    usage = (await connection.execute(select(MODEL_USAGE_DAILY_TABLE))).mappings().all()
                generation = [row for row in usage if row["operation"] == "generation"]
                assert len(generation) == 1
                assert generation[0]["scope_id"] == scope
                assert generation[0]["purpose"] == (
                    "memory_extraction" if family == "memory" else "experience_generation"
                )
                assert generation[0]["requests"] == 1
                assert generation[0]["input_tokens"] == 11
                assert generation[0]["output_tokens"] == 13
                embedding = [row for row in usage if row["operation"] == "embedding"]
                if family == "memory":
                    assert len(embedding) == 1
                    assert embedding[0]["scope_id"] == scope
                    assert embedding[0]["purpose"] == "memory_indexing"
                    assert embedding[0]["requests"] == 1
                    assert embedding[0]["input_tokens"] == 7
                else:
                    assert embedding == []
                calls = [payload for path, payload in server.requests if path == "/v1/chat/completions"]
                assert len(calls) == 1
                assert marker in json.dumps(calls[0])
                assert len([path for path, _ in server.requests if path == "/v1/embeddings"]) == (
                    1 if family == "memory" else 0
                )

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_operational_embedding_composition_does_not_double_count_an_existing_usage_adapter():
    class Embedding:
        profile = EmbeddingProfile(profile_id="test", model="test", dimension=2, distance="l2", normalization="unit")

        async def embed(self, texts):
            return EmbeddingResult(vectors=((1.0, 0.0),) * len(texts), usage=InferenceUsage(requests=1, input_tokens=3))

    async def scenario():
        reports = []

        async def report(purpose, operation, usage):
            reports.append((purpose, operation, usage))

        model = _usage_reporting_embedding_model(UsageReportingEmbeddingModel(Embedding()))
        assert model is not None
        with bind_usage_reporter(report, embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING):
            await model.embed(("One document",))
        assert len(reports) == 1
        assert reports[0][2].requests == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["memory", "experience"])
@pytest.mark.parametrize("injected", [False, True])
def test_shared_prompt_composition_preserves_missing_provider_and_injected_rejection(family, injected):
    class Pipeline:
        async def extract(self, request):
            return ()

        async def incubate(self, sources):
            return ()

    async def scenario():
        config = BuiltinConfig(
            inference=InferenceConfig(generation_model="test" if injected else None),
            runtime=RuntimeConfig(artifact_processing_families=()),
        )
        pipeline = Pipeline()
        async with open_builtin_runtime(
            config,
            candidate_pipeline=pipeline if injected and family == "memory" else None,
            experience_pipeline=pipeline if injected and family == "experience" else None,
        ) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(ScopeDraft(title="Prompt", summary="Prompt", idempotency_key="prompt"))
            ).scope_id
            key = "memory.extract" if family == "memory" else "experience.incubate"
            capability = await runtime.prompts.for_scope(scope).read_configuration(key)
            assert capability.status == ("unsupported" if injected else "disabled")
            assert capability.reason == ("injected_component" if injected else "provider_not_configured")
            disabled = await runtime.prompts.for_scope(scope).read_configuration("memory.rerank")
            assert disabled.status == "disabled" and disabled.reason == "operation_disabled"
            with pytest.raises(PromptError) as rejected:
                await runtime.records.for_scope(scope).create_artifact(
                    "prompt",
                    ArtifactWrite(
                        prompt_key=key,
                        content={
                            "schema_version": "powercontext.prompt.v1",
                            "mode": "custom",
                            "instructions": "This component cannot honor customized instructions.",
                            "demonstrations": [],
                        },
                    ),
                )
            assert rejected.value.code == "prompt_customization_unavailable"

    asyncio.run(scenario())
