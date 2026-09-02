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

import pytest

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.review import InvalidCandidateError
from powercontext.builtin.review.generation import GenerationCapabilityUnavailableError
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    ListArtifactCandidatesRequest,
    SkillGenerationOrigin,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft


async def _create_scope(runtime: BuiltinRuntime, idempotency_key: str = "project") -> str:
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(
        ScopeDraft(title="Project", summary="Review generation tests", idempotency_key=idempotency_key)
    )
    return scope.scope_id


def _experience() -> ExperienceContent:
    return ExperienceContent(
        situation="The checked-in HTTP client follows an OpenAPI contract.",
        action="Regenerate the client and run the contract tests.",
        outcome="The generated client and server contract remained aligned.",
        lesson="Regenerate checked-in clients before validating a changed contract.",
    )


def _skill() -> SkillContent:
    return SkillContent(
        name="regenerate-http-contract",
        description="Use after changing the PowerContext OpenAPI contract.",
        instructions="Regenerate checked-in transport code, inspect the diff, then run contract tests.",
        validation=("make api-generate-check passes", "make contract-test passes"),
    )


class _ExperienceGenerator:
    def __init__(self, proposal: ExperienceContent | None) -> None:
        self.proposal = proposal
        self.inputs: list[ArtifactGenerationInput] = []

    async def generate(self, value: ArtifactGenerationInput, /) -> ExperienceContent | None:
        self.inputs.append(value)
        return self.proposal


class _SkillGenerator:
    def __init__(self, proposal: SkillContent | None) -> None:
        self.proposal = proposal
        self.inputs: list[ArtifactGenerationInput] = []

    async def generate(self, value: ArtifactGenerationInput, /) -> SkillContent | None:
        self.inputs.append(value)
        return self.proposal


def test_generation_without_model_fails_before_candidate_persistence() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime)
            captured = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="bounded evidence", metadata={})
            )

            with pytest.raises(GenerationCapabilityUnavailableError):
                await runtime.experience.for_scope(scope_id).generate(
                    GenerateExperienceRequest(sources=(captured.source_ref,))
                )

            inbox = await runtime.review.for_scope(scope_id).list(ListArtifactCandidatesRequest())
            capabilities = await runtime.capabilities()
            assert inbox.candidates == ()
            assert capabilities.experience_generation is False
            assert capabilities.managed_skill_generation is False

    asyncio.run(scenario())


def test_experience_generation_uses_exact_source_and_supports_no_op() -> None:
    async def scenario() -> None:
        generator = _ExperienceGenerator(_experience())
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_generator=generator,
        ) as runtime:
            scope_id = await _create_scope(runtime)
            captured = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="contract validation passed", metadata={})
            )

            result = await runtime.experience.for_scope(scope_id).generate(
                GenerateExperienceRequest(
                    sources=(captured.source_ref,),
                    reason="Generate from a selected successful task.",
                )
            )

            assert result.generated is True
            assert result.candidate is not None
            assert result.candidate.sources == (captured.source_ref,)
            assert generator.inputs[0].evidence[0].evidence_id == "source:content/task-1"

        no_op = _ExperienceGenerator(None)
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_generator=no_op,
        ) as runtime:
            scope_id = await _create_scope(runtime)
            captured = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-2", content="nothing reusable", metadata={})
            )
            result = await runtime.experience.for_scope(scope_id).generate(
                GenerateExperienceRequest(sources=(captured.source_ref,))
            )
            inbox = await runtime.review.for_scope(scope_id).list(ListArtifactCandidatesRequest())

            assert result.generated is False
            assert inbox.candidates == ()

    asyncio.run(scenario())


def test_experience_generation_targets_an_exact_approved_revision() -> None:
    async def scenario() -> None:
        generator = _ExperienceGenerator(_experience())
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_generator=generator,
        ) as runtime:
            scope_id = await _create_scope(runtime)
            first_source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="contract validation passed", metadata={})
            )
            first_candidate = await runtime.experience.for_scope(scope_id).generate(
                GenerateExperienceRequest(sources=(first_source.source_ref,))
            )
            assert first_candidate.candidate is not None
            first_approval = await runtime.review.for_scope(scope_id).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=first_candidate.candidate.candidate_id,
                    expected_version=1,
                )
            )
            assert first_approval.result_artifact is not None
            later_source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-2", content="generation also had to run first", metadata={})
            )

            replacement = await runtime.experience.for_scope(scope_id).generate(
                GenerateExperienceRequest(
                    sources=(later_source.source_ref,),
                    artifacts=(first_approval.result_artifact,),
                    target=first_approval.result_artifact,
                )
            )

            assert replacement.candidate is not None
            assert replacement.candidate.target == first_approval.result_artifact
            assert generator.inputs[-1].target_evidence_id is not None
            with pytest.raises(InvalidCandidateError):
                await runtime.experience.for_scope(scope_id).generate(
                    GenerateExperienceRequest(
                        sources=(later_source.source_ref,),
                        target=first_approval.result_artifact,
                    )
                )
            assert len(generator.inputs) == 2

    asyncio.run(scenario())


def test_managed_skill_generation_enforces_origin_specific_lineage() -> None:
    async def scenario() -> None:
        experience_generator = _ExperienceGenerator(_experience())
        skill_generator = _SkillGenerator(_skill())
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_generator=experience_generator,
            skill_generator=skill_generator,
        ) as runtime:
            scope_id = await _create_scope(runtime)
            captured = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="contract validation passed", metadata={})
            )
            experience_candidate = await runtime.experience.for_scope(scope_id).generate(
                GenerateExperienceRequest(sources=(captured.source_ref,))
            )
            assert experience_candidate.candidate is not None
            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=experience_candidate.candidate.candidate_id,
                    expected_version=1,
                )
            )
            assert approved.result_artifact is not None

            generated = await runtime.skill.for_scope(scope_id).generate(
                GenerateSkillRequest(
                    origin=SkillGenerationOrigin.EXPERIENCE,
                    sources=(captured.source_ref,),
                    artifacts=(approved.result_artifact,),
                )
            )
            assert generated.candidate is not None
            assert generated.candidate.sources == (captured.source_ref,)
            assert generated.candidate.artifacts == (approved.result_artifact,)
            assert any(
                evidence.evidence_id.startswith("artifact:experience/")
                for evidence in skill_generator.inputs[0].evidence
            )
            skill_approval = await runtime.review.for_scope(scope_id).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=generated.candidate.candidate_id,
                    expected_version=1,
                )
            )
            assert skill_approval.result_artifact is not None
            usage = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-2", content="Skill validation passed.", metadata={})
            )
            replacement = await runtime.skill.for_scope(scope_id).generate(
                GenerateSkillRequest(
                    origin=SkillGenerationOrigin.USAGE,
                    sources=(usage.source_ref,),
                    artifacts=(skill_approval.result_artifact,),
                    target=skill_approval.result_artifact,
                )
            )
            assert replacement.candidate is not None
            assert replacement.candidate.target == skill_approval.result_artifact

            with pytest.raises(InvalidCandidateError):
                await runtime.skill.for_scope(scope_id).generate(
                    GenerateSkillRequest(
                        origin=SkillGenerationOrigin.EXPERIENCE,
                        sources=(captured.source_ref,),
                        artifacts=(),
                    )
                )
            assert len(skill_generator.inputs) == 2

    asyncio.run(scenario())
