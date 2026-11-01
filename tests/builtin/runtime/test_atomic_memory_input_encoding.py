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

"""Configured Atomic Memory encoding reaches the model without changing its domain input."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Literal

import pytest
from pydantic import JsonValue, SecretStr
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    project_atomic_memory_evidence,
)
from powercontext.builtin.artifacts.atomic_memory.input_encoding import (
    collect_atomic_memory_input_encodings,
    decode_atomic_memory_input,
)
from powercontext.builtin.artifacts.prompt import Prompt, PromptContent, PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.artifacts.prompt.service import PromptService
from powercontext.builtin.inference.errors import InferencePrivacyError, InvalidInferenceOutputError
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, PrivacyPolicy, UpstreamProfile
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import composition
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, ContentCapture
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose
from powercontext.sources import SourceRef


def _value() -> AtomicMemoryExtractionInput:
    return AtomicMemoryExtractionInput(
        evidence=tuple(
            AtomicMemoryEvidence(
                evidence_id=f"source:{index}",
                source_ref=SourceRef(source_type="content", source_id=f"turn-{index}"),
                journal_position=index + 1,
                content={
                    "content": "Use migration tests.",
                    "metadata": {"repository_rules": "preserve evidence; " * 120},
                },
                source_metadata={"metadata": {"repository_rules": "preserve evidence; " * 120}},
            )
            for index in range(3)
        )
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "active"])
def test_configured_encoding_preserves_model_input_and_observes_only_generation(
    monkeypatch, mode: Literal["off", "shadow", "active"]
) -> None:
    prompts: list[str] = []
    usages: list[InferenceUsage] = []

    async def report(purpose, operation, usage):
        assert purpose is ModelUsagePurpose.MEMORY_EXTRACTION
        assert operation is ModelUsageOperation.GENERATION
        usages.append(usage)

    async def respond(messages, info):
        prompts.extend(part.content for msg in messages for part in msg.parts if isinstance(part, UserPromptPart))
        return ModelResponse(
            parts=[TextPart('{"candidates":[]}')], usage=RequestUsage(input_tokens=17, output_tokens=9)
        )

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario():
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(generation_model="test", atomic_memory_input_encoding=mode),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            pipeline = pipelines[1]
            assert pipeline is not None
            value = _value()
            with (
                collect_atomic_memory_input_encodings() as reports,
                bind_usage_reporter(report, generation_purpose=ModelUsagePurpose.MEMORY_EXTRACTION),
            ):
                budget_payload = (
                    value.model_dump_json() if pipeline.input_renderer is None else pipeline.input_renderer(value)
                )
                assert reports == []
                assert await pipeline.extract(value) == ()
            assert prompts == [budget_payload]
            assert len(usages) == 1
            assert usages[0].requests == 1
            assert usages[0].input_tokens == 17
            assert usages[0].output_tokens == 9
            assert usages[0].input_encodings == tuple(reports)
            if mode == "off":
                assert reports == []
            else:
                assert len(reports) == 1
                assert reports[0].candidate_bytes is not None
                assert reports[0].candidate_bytes < reports[0].original_bytes
            if mode == "active":
                assert decode_atomic_memory_input(prompts[0]) == value.model_dump_json()
            else:
                assert prompts[0] == value.model_dump_json()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["off", "active"])
def test_stage_budget_uses_the_exact_input_sent_and_rejects_uncompressed_overflow(
    monkeypatch, tmp_path, mode: Literal["off", "active"]
) -> None:
    prompts: list[str] = []

    async def respond(messages, info):
        prompts.extend(part.content for msg in messages for part in msg.parts if isinstance(part, UserPromptPart))
        return ModelResponse(parts=[TextPart('{"candidates":[]}')])

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario():
        runtime_config = RuntimeConfig(atomic_memory_input_tokens_limit=2500)
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(generation_model="test", atomic_memory_input_encoding=mode),
                runtime_config,
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            pipeline = pipelines[1]
            assert pipeline is not None
            config = BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'stage-budget.db'}"),
                runtime=runtime_config,
            )
            async with composition.open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
                context = await contexts.get("encoding-stage-budget")
                for index in range(3):
                    await context.sources.capture(
                        ContentCapture(
                            source_id=f"turn-{index}",
                            content=f"Preserve independent migration condition {index}.",
                            metadata={"repository_rules": "preserve evidence; " * 120},
                        )
                    )
                async with contexts.database.transaction() as connection:
                    stored = await contexts.repositories.sources.list_window(
                        connection, "encoding-stage-budget", after=0, through=3
                    )
                evidence = tuple([
                    await project_atomic_memory_evidence(
                        item, contexts.repositories.sources, evidence_id=f"source:{item.journal_position}"
                    )
                    for item in stored
                ])
                value = AtomicMemoryExtractionInput(evidence=evidence)
                original = value.model_dump_json()
                instructions = pipeline.extraction_instructions
                limit = runtime_config.atomic_memory_input_tokens_limit
                assert pipeline.estimator.estimate(f"{instructions}\n{original}") > limit

                with collect_atomic_memory_input_encodings() as reports:
                    if mode == "off":
                        with pytest.raises(InvalidInferenceOutputError, match="atomic-memory-input-budget"):
                            await context.triggers.flush(limit=3)
                        assert (await context.triggers.cursor()).sequence == 0
                        assert prompts == reports == []
                    else:
                        assert pipeline.input_renderer is not None
                        expected = pipeline.input_renderer(value)
                        assert pipeline.estimator.estimate(f"{instructions}\n{expected}") <= limit
                        assert reports == []
                        flushed = await context.triggers.flush(limit=3)
                        assert flushed.source_count == flushed.current_cursor == 3
                        assert (await context.triggers.cursor()).sequence == 3
                        assert prompts == [expected]
                        assert decode_atomic_memory_input(prompts[0]) == original
                        assert len(reports) == 1 and reports[0].reason == "applied"

    asyncio.run(scenario())


def test_custom_prompt_preserves_original_input_contract(monkeypatch) -> None:
    prompts: list[str] = []

    async def respond(messages, info):
        assert "Inspect evidence[].source_metadata" in info.instructions
        prompts.extend(part.content for msg in messages for part in msg.parts if isinstance(part, UserPromptPart))
        return ModelResponse(parts=[TextPart('{"candidates":[]}')])

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def read(scope, keys):
        return {
            "atomic_memory.extract": Prompt(
                artifact_id="atomic_memory.extract",
                revision=1,
                content=PromptContent(
                    schema_version="powercontext.prompt.v1",
                    mode="custom",
                    instructions="Inspect evidence[].source_metadata",
                    demonstrations=(),
                ),
            )
        }

    async def scenario():
        service = PromptService(
            PromptRegistry(builtin_prompt_definitions(), supported=frozenset({"atomic_memory.extract"})), read
        )
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(generation_model="test", atomic_memory_input_encoding="active"),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            pipeline = pipelines[1]
            assert pipeline is not None
            value = _value()
            async with service.bind("scope", "atomic_memory.extract"):
                with collect_atomic_memory_input_encodings() as reports:
                    await pipeline.extract(value)
            assert prompts == [value.model_dump_json()]
            assert len(reports) == 1 and reports[0].selected_bytes == reports[0].original_bytes

    asyncio.run(scenario())


def test_compaction_cannot_hide_a_forbidden_original_serialization(monkeypatch) -> None:
    async def respond(messages, info):
        pytest.fail("A prohibited original representation must never reach the model")

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario():
        # This key/value sequence changes in the typed codec, unlike an ordinary
        # secret string that remains intact in its shared-value table.
        forbidden = '"source_metadata":{"metadata":'
        value = _value()
        assert forbidden in value.model_dump_json()
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    atomic_memory_input_encoding="active",
                    protection=InferenceProtectionConfig(mode="enforcing", forbidden_values=(SecretStr(forbidden),)),
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            pipeline = pipelines[1]
            assert pipeline is not None
            with pytest.raises(InferencePrivacyError) as error:
                await pipeline.extract(value)
            assert error.value.phase == "before_send" and error.value.decision == "block"

    asyncio.run(scenario())


def _deep_value(leaf: str) -> AtomicMemoryExtractionInput:
    content: JsonValue = leaf
    for index in range(19):
        content = {f"level-{index}": content}
    return AtomicMemoryExtractionInput(
        evidence=tuple(
            AtomicMemoryEvidence(
                evidence_id=f"source:{index}",
                source_ref=SourceRef(source_type="content", source_id=f"turn-{index}"),
                journal_position=index + 1,
                content=content if index == 0 else "Different evidence.",
                source_metadata={"shared": "keep the exact rule; " * 300},
            )
            for index in range(3)
        )
    )


@pytest.mark.parametrize("secret", ["sensitive\nvalue", 'sensitive"value'])
def test_deep_encoding_cannot_hide_an_escaped_sensitive_scalar(monkeypatch, secret: str) -> None:
    async def respond(messages, info):
        pytest.fail("Decoded sensitive evidence must be blocked before a model call")

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario():
        value = _deep_value(secret)
        # The scalar is escaped in the JSON text. Checking only that raw text
        # misses it, while typed tree encoding exceeds the policy's JSON depth.
        assert secret not in value.model_dump_json()
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    atomic_memory_input_encoding="active",
                    protection=InferenceProtectionConfig(mode="enforcing", forbidden_values=(SecretStr(secret),)),
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            pipeline = pipelines[1]
            assert pipeline is not None
            with pytest.raises(InferencePrivacyError) as error:
                await pipeline.extract(value)
            assert error.value.phase == "before_send"
            assert error.value.decision == "block"

    asyncio.run(scenario())


def test_safe_deep_input_falls_back_before_model_and_budget_check(monkeypatch) -> None:
    prompts: list[str] = []

    async def respond(messages, info):
        prompts.extend(part.content for msg in messages for part in msg.parts if isinstance(part, UserPromptPart))
        return ModelResponse(parts=[TextPart('{"candidates":[]}')])

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario():
        value = _deep_value("safe evidence")
        original = value.model_dump_json()
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    atomic_memory_input_encoding="active",
                    protection=InferenceProtectionConfig(mode="enforcing"),
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            pipeline = pipelines[1]
            assert pipeline is not None and pipeline.input_renderer is not None
            with collect_atomic_memory_input_encodings() as reports:
                assert pipeline.input_renderer(value) == original
                assert reports == []
                assert await pipeline.extract(value) == ()
            assert prompts == [original]
            assert len(reports) == 1
            assert reports[0].reason == "privacy_profile_unsupported"
            assert reports[0].candidate_bytes is not None
            assert reports[0].candidate_bytes < reports[0].original_bytes
            assert reports[0].selected_bytes == reports[0].original_bytes

    asyncio.run(scenario())


def test_structured_content_allow_still_requires_full_wire_admission() -> None:
    policy = PrivacyPolicy(
        InferenceProtectionConfig(
            mode="enforcing",
            targets={
                "test": UpstreamProfile(
                    endpoint="https://model.invalid/v1/chat/completions",
                    model="test-model",
                    protocol="openai-chat",
                    context_window_tokens=100_000,
                    max_output_tokens=1000,
                    validated_utf8_token_bound=True,
                )
            },
        )
    )
    original = _deep_value("safe evidence").model_dump_json()
    content_check = policy.inspect_structured_input(original)
    assert content_check.decision == "allow"
    assert not content_check.coverage.complete

    body = {"model": "test-model", "messages": [{"role": "user", "content": original}], "max_tokens": 1000}
    approved = policy.inspect_json(json.dumps(body).encode(), phase="before_send", target_profile="test")
    assert approved.decision == "allow" and approved.coverage.complete
    body["model"] = "different-model"
    denied = policy.inspect_json(json.dumps(body).encode(), phase="before_send", target_profile="test")
    assert denied.decision == "withhold"
    assert denied.reason_codes == ("model_mismatch",)
    assert not denied.coverage.complete
