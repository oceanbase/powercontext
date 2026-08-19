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
from typing import cast

import httpx
import pytest

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    CaptureContentSourceRequest,
    GeneratedCandidateStatus,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    SkillGenerationOrigin,
)
from powercontext.server.app import ServerApplication, create_app


class _ExperienceGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> ExperienceContent:
        return ExperienceContent(
            situation="The public OpenAPI contract changes.",
            action="Regenerate checked-in clients and run contract tests.",
            outcome="The generated client and server contract remained aligned.",
            lesson="Regenerate the client before validating a changed contract.",
        )


class _SkillGenerator:
    async def generate(self, _value: ArtifactGenerationInput, /) -> SkillContent:
        return SkillContent(
            name="regenerate-http-contract",
            description="Use after changing the PowerContext OpenAPI contract.",
            instructions="Regenerate checked-in transport code, inspect the diff, then run contract tests.",
            validation=("make api-generate-check passes", "make contract-test passes"),
        )


def test_http_sdk_generates_reviewed_experience_and_managed_skill_candidates() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_generator=_ExperienceGenerator(),
            skill_generator=_SkillGenerator(),
        ) as runtime:
            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport:
                client = PowerContextClient("http://testserver", http_client=transport)
                captured = await client.capture_content_source(
                    CaptureContentSourceRequest(
                        scope_id="project",
                        source_id="task-1",
                        content="The contract checks passed.",
                    )
                )
                generated_experience = await client.generate_experience(
                    GenerateExperienceRequest(
                        scope_id="project",
                        source_refs=[captured.source],
                        artifact_refs=[],
                    )
                )
                assert generated_experience.status is GeneratedCandidateStatus.PENDING
                assert generated_experience.candidate is not None
                approved_experience = await client.approve_artifact_candidate(
                    ApproveArtifactCandidateRequest(
                        scope_id="project",
                        candidate_id=generated_experience.candidate.candidate_id,
                        expected_version=1,
                    )
                )
                assert approved_experience.result_artifact is not None

                generated_skill = await client.generate_skill(
                    GenerateSkillRequest(
                        scope_id="project",
                        origin=SkillGenerationOrigin.EXPERIENCE,
                        source_refs=[],
                        artifact_refs=[approved_experience.result_artifact],
                    )
                )

                assert generated_skill.status is GeneratedCandidateStatus.PENDING
                assert generated_skill.candidate is not None
                assert generated_skill.candidate.family == "skill"
                assert generated_skill.candidate.artifact_refs == [approved_experience.result_artifact]

    asyncio.run(scenario())


def test_http_generation_reports_missing_model_without_creating_a_candidate() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport:
                client = PowerContextClient("http://testserver", http_client=transport)
                captured = await client.capture_content_source(
                    CaptureContentSourceRequest(
                        scope_id="project",
                        source_id="task-1",
                        content="Bounded evidence.",
                    )
                )
                with pytest.raises(ServerResponseError) as unavailable:
                    await client.generate_experience(
                        GenerateExperienceRequest(
                            scope_id="project",
                            source_refs=[captured.source],
                            artifact_refs=[],
                        )
                    )

                assert (unavailable.value.status_code, unavailable.value.code) == (503, "generation_unavailable")

    asyncio.run(scenario())
