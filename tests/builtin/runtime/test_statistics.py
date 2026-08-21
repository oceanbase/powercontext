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

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
    ProposeExperienceRequest,
    RememberMemoryRequest,
    StatisticsPeriod,
    open_builtin_runtime,
)
from powercontext.builtin.sources import ContentSource


class _ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_scoped_statistics_reports_current_inventory_and_recall_reduction() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=_ContentCandidatePipeline(),
        ) as runtime:
            captured = await runtime.sources.for_scope("project").capture(
                CaptureSource(source_id="task-1", content="Remember the contract.", metadata={})
            )
            await runtime.memory.for_scope("project").flush()
            await runtime.memory.for_scope("project").remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="project_note", text="Keep kinds open."),))
            )
            candidate = await runtime.experience.for_scope("project").propose(
                ProposeExperienceRequest(
                    proposal=ExperienceContent(
                        situation="A statistics contract was needed.",
                        action="Define inventory and usage separately.",
                        outcome="The dashboard can project stable fields.",
                        lesson="Keep snapshots separate from period aggregates.",
                    ),
                    sources=(captured.source_ref,),
                )
            )
            await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            statistics = runtime.statistics.for_scope("project")
            prepared = await runtime.context.for_scope("project").prepare(PrepareContextRequest(query="contract"))

            result = await statistics.overview(period=StatisticsPeriod.TODAY)

        assert result.inventory.sources.model_dump() == {
            "total": 1,
            "memory_processed": 1,
            "memory_pending": 0,
        }
        assert [(item.family, item.total) for item in result.inventory.artifacts.by_family] == [
            ("experience", 1),
            ("memory", 1),
        ]
        assert result.inventory.candidates.model_dump(exclude={"by_family"}) == {
            "total": 1,
            "pending": 0,
            "approved": 1,
            "rejected": 0,
        }
        assert [(item.kind, item.total) for item in result.inventory.memory.entries.by_kind] == [
            ("fact", 1),
            ("project_note", 1),
        ]
        assert prepared.status == "ready"
        assert prepared.content is not None
        assert '"kind":"experience"' in prepared.content
        assert '"entry_id":"' in prepared.content
        token_estimator = character_token_estimator()
        assert result.recall.estimator == token_estimator.profile
        assert result.recall.totals.preparations == 1
        assert result.recall.totals.ready_preparations == 1
        assert result.recall.totals.comparable_preparations == 1
        assert result.recall.totals.baseline_tokens == token_estimator.estimate("Remember the contract.")
        assert result.recall.totals.recalled_tokens == token_estimator.estimate(prepared.content)
        assert result.recall.totals.token_reduction == (
            result.recall.totals.baseline_tokens - result.recall.totals.recalled_tokens
        )
        assert result.recall.totals.token_reduction < 0

    asyncio.run(scenario())


def test_recall_estimates_each_source_as_complete_text() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            sources = runtime.sources.for_scope("project")
            first = await sources.capture(CaptureSource(source_id="short-a", content="a", metadata={}))
            second = await sources.capture(CaptureSource(source_id="short-b", content="b", metadata={}))
            candidate = await runtime.experience.for_scope("project").propose(
                ProposeExperienceRequest(
                    proposal=ExperienceContent(
                        situation="A short Source recall baseline was needed.",
                        action="Estimate each complete Source separately.",
                        outcome="Per-Source rounding remains visible in statistics.",
                        lesson="Do not join independent Source text before estimation.",
                    ),
                    sources=(first.source_ref, second.source_ref),
                )
            )
            await runtime.review.for_scope("project").approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            prepared = await runtime.context.for_scope("project").prepare(
                PrepareContextRequest(query="estimate each complete Source separately")
            )
            result = await runtime.statistics.for_scope("project").overview(period=StatisticsPeriod.TODAY)

        estimator = character_token_estimator()
        assert prepared.status == "ready"
        assert result.recall.totals.comparable_preparations == 1
        assert result.recall.totals.baseline_tokens == estimator.estimate("a") + estimator.estimate("b") == 2

    asyncio.run(scenario())
