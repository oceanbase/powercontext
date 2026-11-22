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

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel

from powercontext.builtin.artifacts.memory import MemoryExtractionInput, MemoryExtractionOutput
from powercontext.builtin.artifacts.prompt import Prompt, PromptContent, PromptError, PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.artifacts.prompt.service import PromptService, current_prompt
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator


def _prompt(text: str, revision: int = 1) -> Prompt:
    return Prompt(
        artifact_id="memory.extract",
        revision=revision,
        content=PromptContent(
            schema_version="powercontext.prompt.v1", mode="custom", instructions=text, demonstrations=()
        ),
    )


@pytest.mark.parametrize("injected", [False, True])
def test_configuration_read_preserves_saved_content_when_component_cannot_run(injected: bool) -> None:
    async def scenario() -> None:
        head = _prompt("Saved guidance remains readable.", revision=4)

        async def read(scope: str, keys: tuple[str, ...]) -> dict[str, Prompt]:
            return dict.fromkeys(keys, head)

        registry = PromptRegistry(
            builtin_prompt_definitions(), injected=frozenset({"memory.extract"}) if injected else frozenset()
        )
        service = PromptService(registry, read)
        result = await service.read_configuration("test", "memory.extract")
        assert result.artifact == head.as_ref() and result.mode == "custom"
        if injected:
            assert result.status == "unsupported" and result.reason == "injected_component"
            assert result.effective is None and result.builtin is None
        else:
            assert result.status == "disabled"
            assert result.effective is not None and result.effective.instructions == head.content.instructions
            assert result.builtin is not None
            assert result.builtin.instructions == registry.get("memory.extract").default_instructions

    asyncio.run(scenario())


@pytest.mark.parametrize("custom", [False, True])
def test_concurrent_scopes_and_retries_use_frozen_prompt_selections(custom: bool) -> None:
    async def scenario() -> None:
        heads = {"a": _prompt("Scope Alpha only."), "b": _prompt("Scope Beta only.")} if custom else {}
        entered = {scope: asyncio.Event() for scope in ("a", "b")}
        seen: dict[str, list[str]] = {"a": [], "b": []}

        async def read(scope: str, keys: tuple[str, ...]) -> dict[str, Prompt]:
            return dict.fromkeys(keys, heads[scope]) if scope in heads else {}

        async def respond(messages, info) -> ModelResponse:
            selection = current_prompt("memory.extract")
            assert selection is not None
            scope = selection.scope_id
            seen[scope].append(info.instructions)
            if len(seen[scope]) == 1:
                entered[scope].set()
                await entered["b" if scope == "a" else "a"].wait()
                heads[scope] = _prompt(f"Next revision for {scope}.", revision=2)
                return ModelResponse(parts=[TextPart("invalid output forces a retry")])
            return ModelResponse(parts=[TextPart('{"candidates": []}')])

        registry = PromptRegistry(builtin_prompt_definitions(), supported=frozenset({"memory.extract"}))
        service = PromptService(registry, read)
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions=registry.get("memory.extract").default_instructions,
            input_type=MemoryExtractionInput,
            output_type=MemoryExtractionOutput,
            prompt_key="memory.extract",
        )

        async def invoke(scope: str) -> None:
            async with service.snapshot(scope, ("memory.extract",)), service.bind(scope, "memory.extract") as selection:
                assert selection is not None
                assert selection.artifact == (heads[scope].as_ref() if custom else None)
                await generator.generate(MemoryExtractionInput(evidence=(), current_entries=()))
                other = "b" if scope == "a" else "a"
                async with (
                    service.snapshot(other, ("memory.extract",)),
                    service.bind(other, "memory.extract") as nested,
                ):
                    assert nested is not None and nested.scope_id == other
                assert current_prompt("memory.extract") == selection
                assert await service.resolve(scope, "memory.extract") == selection
            assert current_prompt("memory.extract") is None

        await asyncio.wait_for(asyncio.gather(invoke("a"), invoke("b")), timeout=10)
        assert seen["a"][0] == seen["a"][1]
        assert seen["b"][0] == seen["b"][1]
        if custom:
            assert "Scope Alpha only." in seen["a"][0] and "Scope Beta only." not in seen["a"][0]
            assert "Scope Beta only." in seen["b"][0] and "Scope Alpha only." not in seen["b"][0]
        else:
            assert seen["a"][0] == seen["b"][0]
            assert registry.get("memory.extract").default_instructions in seen["a"][0]
        next_selection = await service.resolve("a", "memory.extract")
        assert next_selection is not None and next_selection.artifact is not None
        assert next_selection.artifact.revision == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("allow_continuations", [True, False])
def test_prompt_traces_include_identity_but_exclude_custom_content(allow_continuations: bool) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    private_guidance = "PRIVATE_TEST_PROMPT_BODY_NOT_FOR_TRACES"
    private_demo = "PRIVATE_TEST_DEMONSTRATION_NOT_FOR_TRACES"
    content = PromptContent.model_validate_json(
        json.dumps({
            "schema_version": "powercontext.prompt.v1",
            "mode": "custom",
            "instructions": private_guidance,
            "demonstrations": [
                {
                    "input": {
                        "evidence": [{"evidence_id": "s1", "evidence_type": "source", "content": private_demo}],
                        "current_entries": [],
                    },
                    "expected_output": {"candidates": []},
                }
            ],
        })
    )
    prompt = Prompt(artifact_id="memory.extract", revision=7, content=content)

    async def read(scope: str, keys: tuple[str, ...]) -> dict[str, Prompt]:
        return dict.fromkeys(keys, prompt)

    async def reply(messages, info) -> ModelResponse:
        assert private_guidance in info.instructions and private_demo in info.instructions
        return ModelResponse(parts=[TextPart('{"candidates": []}')])

    async def scenario() -> None:
        registry = PromptRegistry(builtin_prompt_definitions(), supported=frozenset({"memory.extract"}))
        service = PromptService(registry, read)
        generator = PydanticAIStructuredGenerator(
            model=InstrumentedModel(
                FunctionModel(reply),
                InstrumentationSettings(
                    tracer_provider=provider,
                    include_content=False,
                    include_binary_content=False,
                    include_model_request_parameters=False,
                ),
            ),
            instructions=registry.get("memory.extract").default_instructions,
            input_type=MemoryExtractionInput,
            output_type=MemoryExtractionOutput,
            prompt_key="memory.extract",
            limits=InferenceLimits(allow_continuations=allow_continuations),
        )
        with provider.get_tracer("test").start_as_current_span("prompt operation"):
            async with service.bind("scope", "memory.extract"):
                await generator.generate(MemoryExtractionInput(evidence=(), current_entries=()))

    try:
        asyncio.run(scenario())
        spans = exporter.get_finished_spans()
        operation = next(span for span in spans if span.name == "prompt operation")
        attributes = operation.attributes or {}
        assert attributes["powercontext.prompt.memory.extract.version"] == "7"
        assert attributes["powercontext.prompt.memory.extract.demonstration_count"] == 1
        assert attributes["powercontext.prompt.memory.extract.selection"] == "artifact"
        serialized = "\n".join(span.to_json() for span in spans)
        assert private_guidance not in serialized and private_demo not in serialized
        inference = next(span for span in spans if span.name.startswith("invoke_agent "))
        metadata = json.loads(str((inference.attributes or {})["metadata"]))
        assert metadata["powercontext.prompt.key"] == "memory.extract"
        assert metadata["powercontext.prompt.artifact.family"] == "prompt"
        assert metadata["powercontext.prompt.artifact.id"] == "memory.extract"
        assert metadata["powercontext.prompt.artifact.revision"] == 7
    finally:
        provider.shutdown()


def test_injected_component_blocks_existing_custom_head_and_allows_explicit_auto() -> None:
    async def scenario() -> None:
        head = _prompt("Keep release constraints.")

        async def read(scope: str, keys: tuple[str, ...]) -> dict[str, Prompt]:
            return dict.fromkeys(keys, head)

        service = PromptService(
            PromptRegistry(builtin_prompt_definitions(), injected=frozenset({"memory.extract"})), read
        )
        with pytest.raises(PromptError) as caught:
            async with service.bind("scope", "memory.extract"):
                pytest.fail("An incompatible component must not be called")
        assert caught.value.during_inference
        assert caught.value.code == "prompt_customization_unavailable"
        head = Prompt(
            artifact_id="memory.extract",
            revision=2,
            content=PromptContent.model_validate_json(
                json.dumps({
                    "schema_version": "powercontext.prompt.v1",
                    "mode": "auto",
                    "instructions": "",
                    "demonstrations": [],
                })
            ),
        )
        async with service.bind("scope", "memory.extract") as selection:
            assert selection is None
            assert current_prompt("memory.extract") is None

    asyncio.run(scenario())
