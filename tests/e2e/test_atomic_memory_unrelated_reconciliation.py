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
import json

from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    InferenceConfig,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft


class _Extractor:
    async def generate(self, request: AtomicMemoryExtractionInput, /):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput.model_validate({
                "candidates": [
                    {
                        "kind": "decision",
                        "text": "The billing service uses PostgreSQL 16.",
                        "description": "Billing database is PostgreSQL 16",
                        "evidence_ids": [request.evidence[0].evidence_id],
                    }
                ]
            })
        )


class _Reconciler:
    def __init__(self) -> None:
        self.requests: list[AtomicMemoryReconciliationInput] = []

    async def generate(self, request: AtomicMemoryReconciliationInput, /):
        self.requests.append(request)
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create",
                compared_ids=(),
                content=AtomicMemoryReconciliationContent(
                    kind=request.proposal.kind,
                    text="Billing runs on PostgreSQL 16.",
                    description="Billing runs on PostgreSQL 16",
                ),
                evidence_ids=request.proposal.evidence_ids,
                reason="An injected policy rewrites every candidate.",
            )
        )


def test_injected_reconciler_receives_a_candidate_with_nothing_to_compare(tmp_path):
    # The built-in skip relies on built-in Prompts; an injected stage may apply its own policy to every candidate.
    reconciler = _Reconciler()

    async def scenario():
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'injected.db'}")),
            candidate_pipeline=AtomicMemoryGenerationPipeline(
                extractor=_Extractor(),
                reconciler=reconciler,
                estimator=character_token_estimator(),
            ),
        ) as contexts:
            await contexts.records.create_source("scope-a", "content", "We use PostgreSQL 16 for billing.")
            result = await contexts.process_memory("scope-a", 1)
            assert result.source_count == 1
            return (await contexts.atomic_memory.for_scope("scope-a").list(limit=10)).items

    memories = asyncio.run(scenario())

    assert len(reconciler.requests) == 1
    assert reconciler.requests[0].related == ()
    assert [memory.artifact.content.text for memory in memories] == ["Billing runs on PostgreSQL 16."]


def test_builtin_reconciliation_drops_only_candidates_it_compares(tmp_path, monkeypatch):
    # Built-in extraction decides what is worth keeping; the reconciler's unworthy noop applies only while comparing.
    reconciliations = []

    def respond(messages, _info) -> ModelResponse:
        prompt = next(
            part.content
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        if prompt == "Reply with one token.":
            return ModelResponse(parts=[TextPart("ok")])
        request = json.loads(prompt)
        if "proposal" in request:
            reconciliations.append(request)
            value = {
                "action": "noop",
                "compared_ids": [item["item_id"] for item in request["related"]],
                "content": None,
                "evidence_ids": [],
                "reason": "The candidate is not worth remembering.",
            }
        else:
            evidence_id = request["evidence"][0]["evidence_id"]
            value = {
                "candidates": [
                    {
                        "kind": "decision",
                        "text": "The billing service uses PostgreSQL 16.",
                        "description": "Billing database is PostgreSQL 16",
                        "evidence_ids": [evidence_id],
                    },
                    {
                        "kind": "fact",
                        "text": "The billing team had lunch today.",
                        "description": "Billing team lunch today",
                        "evidence_ids": [evidence_id],
                    },
                ]
            }
        return ModelResponse(parts=[TextPart(json.dumps(value))])

    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(respond))

    async def scenario():
        async with open_builtin_runtime(
            BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'builtin.db'}"),
                inference=InferenceConfig(generation_model="test"),
            ),
            scheduler_path=tmp_path / "scheduler.db",
        ) as runtime:
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            draft = ScopeDraft(title="Billing", summary="Billing decisions", idempotency_key="billing")
            scope_id = (await runtime.scopes.create(draft)).scope_id
            await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(
                    source_id="note-1",
                    content="We use PostgreSQL 16 for billing. The billing team had lunch today.",
                    metadata={},
                )
            )
            memory = runtime.atomic_memory.for_scope(scope_id)
            result = await memory.flush()
            assert result.source_count == 1
            return (await memory.list(limit=10)).items

    memories = asyncio.run(scenario())

    assert [memory.artifact.content.text for memory in memories] == ["The billing service uses PostgreSQL 16."]
    assert [request["proposal"]["text"] for request in reconciliations] == ["The billing team had lunch today."]
