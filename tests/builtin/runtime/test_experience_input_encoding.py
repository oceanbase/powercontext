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

"""Configured Experience preparation preserves its reviewed generation contract."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Literal

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.generation import (
    ArtifactGenerationInput,
    GenerationEvidence,
    GenerationEvidenceKind,
)
from powercontext.builtin.inference.errors import InferencePrivacyError, InvalidInferenceOutputError
from powercontext.builtin.inference.input_encoding import INPUT_ENCODING_SCHEMA, INPUT_ENCODING_SCHEMA_V2, decode_input
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.privacy import InferenceProtectionConfig
from powercontext.builtin.inference.usage import bind_usage_reporter, collect_input_encodings
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.review import InvalidCandidateError
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    GenerateExperienceRequest,
    GetExperienceRequest,
    ListCandidatesRequest,
    composition,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose


def _proposal() -> ExperienceContent:
    return ExperienceContent(
        situation="A migration changes persisted data.",
        action="Run the migration and read the result after reopening the database.",
        outcome="The persisted evidence survived reopening.",
        lesson="Verify persisted reads before declaring a migration successful.",
    )


def _repeated_input() -> ArtifactGenerationInput:
    # A deterministic algorithm fixture, not a claim about dataset compression.
    content = "Run the migration and preserve the observed failure before retrying. " * 100
    return ArtifactGenerationInput(
        evidence=tuple(
            GenerationEvidence(
                evidence_id=f"source:content/task-{index}",
                kind=GenerationEvidenceKind.SOURCE,
                content=content,
                truncated=index == 2,
            )
            for index in range(3)
        ),
        target_evidence_id=None,
    )


def _prompt(messages) -> str:
    return next(part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart))


def _restored(prompt: str, stage: str = "experience.generate") -> str:
    document = json.loads(prompt)
    schema = document.get("schema")
    return (
        decode_input(prompt, schema=schema, stage=stage)
        if schema in {INPUT_ENCODING_SCHEMA, INPUT_ENCODING_SCHEMA_V2}
        else prompt
    )


@pytest.fixture
def controlled_model(monkeypatch):
    def configure(respond):
        async def open_model(*args, **kwargs):
            model = FunctionModel(respond)
            return model, model

        monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    return configure


@pytest.mark.parametrize("mode", ["off", "shadow", "active"])
def test_configured_experience_encoding_preserves_evidence_and_attributes_actual_usage(
    controlled_model, mode: Literal["off", "shadow", "active"]
) -> None:
    prompts: list[str] = []
    usages: list[InferenceUsage] = []

    async def respond(messages, info):
        prompts.append(_prompt(messages))
        return ModelResponse(
            parts=[TextPart(json.dumps({"proposal": _proposal().model_dump()}))],
            usage=RequestUsage(input_tokens=19, output_tokens=11),
        )

    async def report(purpose, operation, usage):
        assert purpose is ModelUsagePurpose.EXPERIENCE_GENERATION
        assert operation is ModelUsageOperation.GENERATION
        usages.append(usage)

    controlled_model(respond)

    async def scenario():
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(generation_model="test", input_encoding={"experience.generate": mode}),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            generator = pipelines[3]
            assert generator is not None
            value = _repeated_input()
            with (
                collect_input_encodings() as reports,
                bind_usage_reporter(report, generation_purpose=ModelUsagePurpose.EXPERIENCE_GENERATION),
            ):
                assert await generator.generate(value) == _proposal()
            assert len(prompts) == 1
            assert _restored(prompts[0]) == value.model_dump_json()
            assert len(usages) == 1
            assert (usages[0].requests, usages[0].input_tokens, usages[0].output_tokens) == (1, 19, 11)
            assert usages[0].input_encodings == tuple(reports)
            if mode == "off":
                assert reports == []
            else:
                assert len(reports) == 1
                assert reports[0].stage == "experience.generate"
                assert reports[0].candidate_bytes is not None
                assert reports[0].candidate_bytes < reports[0].original_bytes
                assert reports[0].reason == ("applied" if mode == "active" else "shadow_candidate")
            assert (prompts[0] != value.model_dump_json()) is (mode == "active")

    asyncio.run(scenario())


def test_shared_input_type_does_not_enable_skill_encoding(controlled_model) -> None:
    prompts: list[str] = []

    async def respond(messages, info):
        prompts.append(_prompt(messages))
        return ModelResponse(parts=[TextPart('{"proposal":null}')])

    controlled_model(respond)

    async def scenario():
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(generation_model="test", input_encoding={"experience.generate": "active"}),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            experience, skill = pipelines[3:5]
            assert experience is not None and skill is not None
            value = _repeated_input()
            with collect_input_encodings() as reports:
                assert await experience.generate(value) is None
                assert await skill.generate(value) is None
            assert len(prompts) == 2
            assert prompts[0] != value.model_dump_json()
            assert _restored(prompts[0]) == value.model_dump_json()
            assert prompts[1] == value.model_dump_json()
            assert [report.stage for report in reports] == ["experience.generate"]

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["off", "shadow", "active"])
def test_configured_experience_survives_review_approval_and_database_reopen(
    controlled_model, tmp_path, mode: Literal["off", "shadow", "active"]
) -> None:
    prompts: list[str] = []

    async def respond(messages, info):
        prompts.append(_prompt(messages))
        return ModelResponse(
            parts=[TextPart(json.dumps({"proposal": _proposal().model_dump()}))],
            usage=RequestUsage(input_tokens=23, output_tokens=17),
        )

    controlled_model(respond)

    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reviewed.db'}"),
            inference=InferenceConfig(generation_model="test", input_encoding={"experience.generate": mode}),
        )
        content = "Migration returned exit code 1. Reopening retained the old schema. " * 50
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Encoding", summary="Encoding", idempotency_key="encoding")
                )
            ).scope_id
            refs = tuple([
                (
                    await runtime.sources.for_scope(scope).capture(
                        CaptureSource(source_id=f"task-{index}", content=content, metadata={"attempt": index})
                    )
                ).source_ref
                for index in range(2)
            ])
            with collect_input_encodings() as reports:
                generated = await runtime.experience.for_scope(scope).generate(GenerateExperienceRequest(sources=refs))
            assert generated.candidate is not None
            candidate = generated.candidate
            assert candidate.sources == refs
            assert candidate.result_artifact is None
            assert len(prompts) == 1
            document = json.loads(_restored(prompts[0]))
            assert [item["evidence_id"] for item in document["evidence"]] == [
                f"source:{ref.source_type}/{ref.source_id}" for ref in refs
            ]
            assert [json.loads(item["content"])["content"] for item in document["evidence"]] == [content, content]
            assert all(not item["truncated"] for item in document["evidence"])
            assert all(report.stage == "experience.generate" for report in reports)
            assert len(reports) == (0 if mode == "off" else 1)
            approved = await runtime.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
            )
            assert approved.result_artifact is not None
            reference = approved.result_artifact
            experience = await runtime.experience.for_scope(scope).get(GetExperienceRequest(artifact=reference))
            assert experience.content == _proposal()
            assert experience.lineage.sources == refs

        # Reopening also flushes the existing usage recorder; estimates never
        # become persisted provider counts, even when representation changes.
        async with open_builtin_runtime(config) as reopened:
            stored = await reopened.experience.for_scope(scope).get(GetExperienceRequest(artifact=reference))
            assert stored == experience
            statistics = await reopened.statistics.for_scope(scope).overview()
            usage = statistics.usage.totals.generation
            assert (usage.requests, usage.input_tokens, usage.output_tokens) == (1, 23, 17)
            purpose = next(
                item for item in statistics.usage.by_purpose if item.purpose is ModelUsagePurpose.EXPERIENCE_GENERATION
            )
            assert purpose.generation == usage

    asyncio.run(scenario())


def test_scoped_custom_prompt_fallback_and_foreign_evidence_are_isolated(controlled_model, tmp_path) -> None:
    observed: list[tuple[str, str]] = []

    async def respond(messages, info):
        observed.append((_prompt(messages), info.instructions or ""))
        await asyncio.sleep(0)
        return ModelResponse(parts=[TextPart('{"proposal":null}')])

    controlled_model(respond)

    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scopes.db'}"),
            inference=InferenceConfig(generation_model="test", input_encoding={"experience.generate": "active"}),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scopes = [
                (await runtime.scopes.create(ScopeDraft(title=name, summary=name, idempotency_key=name))).scope_id
                for name in ("custom", "builtin")
            ]
            refs = [
                (
                    await runtime.sources.for_scope(scope).capture(
                        CaptureSource(
                            source_id=f"evidence-{index}", content=f"Scope {index}: keep exact evidence.", metadata={}
                        )
                    )
                ).source_ref
                for index, scope in enumerate(scopes)
            ]
            await runtime.records.for_scope(scopes[0]).create_artifact(
                "prompt",
                ArtifactWrite(
                    prompt_key="experience.generate",
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "custom",
                        "instructions": "CUSTOM_SCOPE: inspect evidence[].content as the original JSON string.",
                        "demonstrations": [],
                    },
                ),
            )

            async def generate(scope, reference):
                with collect_input_encodings() as reports:
                    result = await runtime.experience.for_scope(scope).generate(
                        GenerateExperienceRequest(sources=(reference,))
                    )
                assert result.candidate is None
                return reports

            custom, builtin = await asyncio.gather(
                *(generate(scope, ref) for scope, ref in zip(scopes, refs, strict=True))
            )
            assert len(custom) == len(builtin) == 1
            assert custom[0].reason == "unsupported_prompt"
            assert builtin[0].reason != "unsupported_prompt"
            custom_payload, _ = next(item for item in observed if "CUSTOM_SCOPE" in item[1])
            assert "schema" not in json.loads(custom_payload)
            assert json.loads(custom_payload)["evidence"][0]["evidence_id"] == "source:content/evidence-0"
            builtin_payload, _ = next(item for item in observed if "CUSTOM_SCOPE" not in item[1])
            assert json.loads(_restored(builtin_payload))["evidence"][0]["evidence_id"] == "source:content/evidence-1"
            with pytest.raises(InvalidCandidateError):
                await runtime.experience.for_scope(scopes[0]).generate(GenerateExperienceRequest(sources=(refs[1],)))
            assert len(observed) == 2
            for scope in scopes:
                assert (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("forbidden", ['"evidence":[', "sensitive\nvalue", 'sensitive"value'])
@pytest.mark.parametrize("nested_array", [False, True])
def test_experience_original_privacy_failure_never_calls_model_or_writes_candidate(
    controlled_model, tmp_path, forbidden: str, nested_array: bool
) -> None:
    async def respond(messages, info):
        pytest.fail("Forbidden evidence reached the controlled model")

    controlled_model(respond)

    async def scenario():
        content = "Safe evidence with a forbidden outer serialization." if forbidden == '"evidence":[' else forbidden
        if nested_array:
            content = json.dumps(
                [content, *("Long repeated evidence retained exactly. " * 100 for _ in range(3))],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'blocked.db'}"),
            inference=InferenceConfig(
                generation_model="test",
                input_encoding={"experience.generate": "active"},
                protection=InferenceProtectionConfig(mode="enforcing", forbidden_values=(SecretStr(forbidden),)),
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(ScopeDraft(title="Blocked", summary="Blocked", idempotency_key="blocked"))
            ).scope_id
            captured = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="secret", content=content, metadata={})
            )
            with collect_input_encodings() as reports, pytest.raises(InferencePrivacyError) as error:
                await runtime.experience.for_scope(scope).generate(
                    GenerateExperienceRequest(sources=(captured.source_ref,))
                )
            assert error.value.phase == "before_send" and error.value.decision == "block"
            assert reports == []
            assert (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates == ()
            # Rejection of generation must not consume or rewrite its source.
            source = await runtime.records.for_scope(scope).get_source("content", "secret")
            assert source.content == content

    asyncio.run(scenario())


def test_experience_candidate_outside_privacy_profile_falls_back_to_checked_original(controlled_model) -> None:
    prompts: list[str] = []

    async def respond(messages, info):
        prompts.append(_prompt(messages))
        return ModelResponse(parts=[TextPart('{"proposal":null}')])

    controlled_model(respond)

    async def scenario():
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    input_encoding={"experience.generate": "active"},
                    protection=InferenceProtectionConfig(mode="enforcing", max_json_depth=8),
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            generator = pipelines[3]
            assert generator is not None
            value = _repeated_input()
            with collect_input_encodings() as reports:
                assert await generator.generate(value) is None
            assert prompts == [value.model_dump_json()]
            assert len(reports) == 1
            assert reports[0].reason == "privacy_profile_unsupported"
            assert reports[0].candidate_bytes is not None and reports[0].candidate_bytes < reports[0].original_bytes
            assert reports[0].selected_bytes == reports[0].original_bytes

    asyncio.run(scenario())


def test_forbidden_candidate_is_rejected_instead_of_falling_back_to_original(controlled_model) -> None:
    async def respond(messages, info):
        pytest.fail("A prohibited candidate must not fall back and reach the model")

    controlled_model(respond)

    async def scenario():
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    input_encoding={"experience.generate": "active"},
                    protection=InferenceProtectionConfig(
                        mode="enforcing", forbidden_values=(SecretStr(INPUT_ENCODING_SCHEMA_V2),)
                    ),
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            generator = pipelines[3]
            assert generator is not None
            value = _repeated_input()
            assert INPUT_ENCODING_SCHEMA_V2 not in value.model_dump_json()
            with pytest.raises(InferencePrivacyError) as error:
                await generator.generate(value)
            assert error.value.decision == "block"
            assert error.value.reason_codes == ("forbidden_content",)

    asyncio.run(scenario())


def test_invalid_generated_experience_does_not_create_candidate(controlled_model, tmp_path) -> None:
    async def respond(messages, info):
        return ModelResponse(parts=[TextPart('{"proposal":{"situation":"Incomplete proposal"}}')])

    controlled_model(respond)

    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'invalid.db'}"),
            inference=InferenceConfig(
                generation_model="test",
                generation_max_requests=1,
                input_encoding={"experience.generate": "active"},
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(ScopeDraft(title="Invalid", summary="Invalid", idempotency_key="invalid"))
            ).scope_id
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="task", content="Migration failed; no successful check was observed.", metadata={}
                )
            )
            with pytest.raises(InvalidInferenceOutputError):
                await runtime.experience.for_scope(scope).generate(
                    GenerateExperienceRequest(sources=(source.source_ref,))
                )
            assert (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates == ()

    asyncio.run(scenario())


def test_configured_incubation_encoding_preserves_task_outcome_refs_and_cursor(controlled_model, tmp_path) -> None:
    prompts: list[str] = []

    async def respond(messages, info):
        prompt = _prompt(messages)
        prompts.append(prompt)
        document = json.loads(_restored(prompt, "experience.incubate"))
        output = {
            "candidates": [
                {
                    "proposal": _proposal().model_dump(),
                    "evidence_ids": [item["evidence_id"] for item in document["evidence"]],
                }
            ]
        }
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    controlled_model(respond)

    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'incubate.db'}"),
            inference=InferenceConfig(generation_model="test", input_encoding={"experience.incubate": "active"}),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Incubate", summary="Incubate", idempotency_key="incubate")
                )
            ).scope_id
            content = "The migration failed; preserve the observed failure and exact source evidence. " * 100
            receipts = [
                await runtime.sources.for_scope(scope).capture(
                    CaptureSource(source_id=f"task-{index}", content=content, metadata={"kind": "task-outcome"})
                )
                for index in range(3)
            ]
            with collect_input_encodings() as reports:
                result = await runtime.experience.for_scope(scope).incubate(limit=3)
            assert result.source_count == 3 and result.candidate_count == 1
            assert result.current_cursor == receipts[-1].sequence
            assert len(prompts) == len(reports) == 1
            assert reports[0].stage == "experience.incubate" and reports[0].reason == "applied"
            document = json.loads(_restored(prompts[0], "experience.incubate"))
            assert [item["content"] for item in document["evidence"]] == [content] * 3
            candidate = (await runtime.review.for_scope(scope).list(ListCandidatesRequest())).candidates[0]
            assert candidate.sources == tuple(item.source_ref for item in receipts)
            assert candidate.result_artifact is None
            assert not (await runtime.experience.for_scope(scope).incubate(limit=3)).processed
            assert len(prompts) == 1

    asyncio.run(scenario())
