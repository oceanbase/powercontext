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
    BuiltinRuntime,
    CaptureSource,
    PrepareContextRequest,
    ProposeExperienceRequest,
    RememberMemoryRequest,
    StatisticsPeriod,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft, ScopeSelection
from powercontext.builtin.sources import ContentSource


class _ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


async def _create_scope(runtime: BuiltinRuntime, idempotency_key: str) -> str:
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(
        ScopeDraft(title="Statistics Test", summary="Runtime statistics test", idempotency_key=idempotency_key)
    )
    return scope.scope_id


def test_scoped_statistics_reports_current_inventory_and_recall_reduction() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=_ContentCandidatePipeline(),
        ) as runtime:
            scope_id = await _create_scope(runtime, "statistics-inventory")
            captured = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="task-1", content="Remember the contract.", metadata={})
            )
            await runtime.memory.for_scope(scope_id).flush()
            await runtime.memory.for_scope(scope_id).remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="project_note", text="Keep kinds open."),))
            )
            candidate = await runtime.experience.for_scope(scope_id).propose(
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
            await runtime.review.for_scope(scope_id).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            statistics = runtime.statistics.for_scope(scope_id)
            prepared = await runtime.context.for_scope(scope_id).prepare(PrepareContextRequest(query="contract"))

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
            scope_id = await _create_scope(runtime, "statistics-recall")
            sources = runtime.sources.for_scope(scope_id)
            first = await sources.capture(CaptureSource(source_id="short-a", content="a", metadata={}))
            second = await sources.capture(CaptureSource(source_id="short-b", content="b", metadata={}))
            candidate = await runtime.experience.for_scope(scope_id).propose(
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
            await runtime.review.for_scope(scope_id).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            prepared = await runtime.context.for_scope(scope_id).prepare(
                PrepareContextRequest(query="estimate each complete Source separately")
            )
            result = await runtime.statistics.for_scope(scope_id).overview(period=StatisticsPeriod.TODAY)

        estimator = character_token_estimator()
        assert prepared.status == "ready"
        assert result.recall.totals.comparable_preparations == 1
        assert result.recall.totals.baseline_tokens == estimator.estimate("a") + estimator.estimate("b") == 2

    asyncio.run(scenario())


def test_statistics_uses_the_same_all_exact_and_subtree_selection() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            root = await runtime.scopes.create(ScopeDraft(title="Root", summary="Root result", idempotency_key="root"))
            child = await runtime.scopes.create(
                ScopeDraft(
                    title="Child",
                    summary="Child result",
                    parent_scope_id=root.scope_id,
                    idempotency_key="child",
                )
            )
            other = await runtime.scopes.create(
                ScopeDraft(title="Other", summary="Other result", idempotency_key="other")
            )
            for scope_id in (root.scope_id, child.scope_id, other.scope_id):
                await runtime.memory.for_scope(scope_id).remember(
                    RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=f"Fact for {scope_id}."),))
                )

            all_statistics = await runtime.statistics.overview(
                ScopeSelection(mode="all"),
                period=StatisticsPeriod.TODAY,
            )
            subtree = await runtime.statistics.overview(
                ScopeSelection(mode="subtree", root_scope_id=root.scope_id),
                period=StatisticsPeriod.TODAY,
            )
            exact = await runtime.statistics.overview(
                ScopeSelection(mode="exact", scope_ids=(child.scope_id,)),
                period=StatisticsPeriod.TODAY,
            )

        assert set(all_statistics.scope_ids) >= {root.scope_id, child.scope_id, other.scope_id}
        assert all_statistics.inventory.memory.entries.total == 3
        assert subtree.scope_ids == (root.scope_id, child.scope_id)
        assert subtree.inventory.memory.entries.total == 2
        assert tuple(item.scope_id for item in subtree.by_scope) == (root.scope_id, child.scope_id)
        assert [item.inventory.memory.entries.total for item in subtree.by_scope] == [1, 1]
        assert exact.scope_ids == (child.scope_id,)
        assert exact.inventory.memory.entries.total == 1
        assert exact.by_scope[0].scope_id == child.scope_id
        assert exact.by_scope[0].inventory == exact.inventory

    asyncio.run(scenario())
