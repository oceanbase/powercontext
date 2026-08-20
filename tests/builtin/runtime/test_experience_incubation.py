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

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput, ExperienceContent
from powercontext.builtin.inference import InferenceUnavailableError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    BuiltinConfigurationError,
    CaptureSource,
    GetExperienceRequest,
    ListArtifactCandidatesRequest,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.sources import ContentSource
from powercontext.sources import Source, SourceRef


class _TaskOutcomePipeline:
    def __init__(self, *, fail_once: bool = False) -> None:
        self._fail_once = fail_once
        self.calls: list[tuple[Source, ...]] = []

    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        self.calls.append(sources)
        if self._fail_once:
            self._fail_once = False
            raise InferenceUnavailableError("experience-incubate")
        task_outcomes = tuple(
            source
            for source in sources
            if isinstance(source, ContentSource) and source.metadata.get("kind") == "task-outcome"
        )
        return tuple(
            ExperienceCandidateInput(
                proposal=ExperienceContent(
                    situation="A strict configuration fixture failed.",
                    action="Set the configuration mode to strict.",
                    outcome=source.content,
                    lesson="Run the strict fixture after configuration changes.",
                ),
                sources=(SourceRef(source_type="content", source_id=source.name),),
            )
            for source in task_outcomes
        )


def test_incubation_uses_an_independent_cursor_and_keeps_candidates_gated() -> None:
    async def scenario() -> None:
        pipeline = _TaskOutcomePipeline()
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_pipeline=pipeline,
        ) as runtime:
            scope = "scheduled-experience"
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="prompt",
                    content="Please fix the configuration.",
                    metadata={"kind": "prompt"},
                )
            )
            outcome = await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="task-1",
                    content="python test_config.py passed",
                    metadata={"kind": "task-outcome"},
                )
            )

            ordinary = await runtime.experience.for_scope(scope).incubate(limit=1)
            memory_cursor = await runtime.memory.for_scope(scope).cursor()
            incubated = await runtime.experience.for_scope(scope).incubate(limit=1)
            replay = await runtime.experience.for_scope(scope).incubate(limit=1)
            inbox = await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest(family="experience"))

            assert ordinary.candidate_count == 0
            assert ordinary.current_cursor == 1
            assert memory_cursor.sequence == 0
            assert incubated.previous_cursor == 1
            assert incubated.current_cursor == outcome.sequence
            assert incubated.candidate_count == 1
            assert replay.processed is False
            assert len(inbox.candidates) == 1
            candidate = inbox.candidates[0]
            assert candidate.sources == (outcome.source_ref,)
            assert candidate.result_artifact is None

            approved = await runtime.review.for_scope(scope).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            assert approved.result_artifact is not None
            experience = await runtime.experience.for_scope(scope).get(
                GetExperienceRequest(artifact=approved.result_artifact)
            )
            assert experience.lineage.sources == (outcome.source_ref,)

    asyncio.run(scenario())


def test_incubation_retries_the_same_window_after_generation_failure() -> None:
    async def scenario() -> None:
        pipeline = _TaskOutcomePipeline(fail_once=True)
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_pipeline=pipeline,
        ) as runtime:
            scope = "retry-experience"
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="task-1",
                    content="python test_config.py passed",
                    metadata={"kind": "task-outcome"},
                )
            )

            with pytest.raises(InferenceUnavailableError):
                await runtime.experience.for_scope(scope).incubate()
            retried = await runtime.experience.for_scope(scope).incubate()
            inbox = await runtime.review.for_scope(scope).list(ListArtifactCandidatesRequest(family="experience"))

            assert retried.previous_cursor == 0
            assert retried.current_cursor == 1
            assert retried.candidate_count == 1
            assert len(pipeline.calls) == 2
            assert len(inbox.candidates) == 1

    asyncio.run(scenario())


def test_incubation_uses_the_fixed_source_window_budget() -> None:
    async def scenario() -> None:
        pipeline = _TaskOutcomePipeline()
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            experience_pipeline=pipeline,
        ) as runtime:
            scope = "bounded-experience"
            for index in range(33):
                await runtime.sources.for_scope(scope).capture(
                    CaptureSource(
                        source_id=f"prompt-{index}",
                        content="An ordinary prompt.",
                        metadata={"kind": "prompt"},
                    )
                )

            first = await runtime.experience.for_scope(scope).incubate()
            second = await runtime.experience.for_scope(scope).incubate()

            assert first.source_count == 32
            assert first.current_cursor == 32
            assert second.source_count == 1
            assert second.current_cursor == 33

    asyncio.run(scenario())


def test_scheduled_incubation_requires_a_configured_pipeline(tmp_path) -> None:
    async def scenario() -> None:
        with pytest.raises(BuiltinConfigurationError, match="Experience incubation"):
            async with open_builtin_runtime(
                BuiltinConfig(
                    database=SQLiteConfig(),
                    runtime=RuntimeConfig(experience_schedule_seconds=60),
                ),
                scheduler_path=tmp_path / "scheduler.db",
            ):
                pass

    asyncio.run(scenario())
