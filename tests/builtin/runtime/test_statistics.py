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
import tempfile
from pathlib import Path

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import (
    Experience,
    ExperienceContent,
    FailureRecord,
    FailureSignature,
    FailureVerification,
)
from powercontext.builtin.artifacts.experience.recurrence import (
    RecurrenceObservation,
    TaskOutcomeItemKind,
    TaskOutcomeItemRef,
    new_observation,
    signature_key,
)
from powercontext.builtin.artifacts.handoff import Handoff, HandoffArtifactCitation, HandoffContent, HandoffStatement
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence import RecurrenceRepository
from powercontext.builtin.persistence.artifacts import ArtifactRepository, RepositoryArtifactDraft
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
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
from powercontext.builtin.statistics import MAX_RECURRENCE_TOP_REVISIONS
from powercontext.sources import SourceRef

RECURRENCE_CUE = "openapi contract changed without regenerating the client"
_CUE_KEY = signature_key(RECURRENCE_CUE)
_RECEIPT_REF = SourceRef(source_type="content", source_id="receipt-1")
_HANDOFF_REF = ArtifactRef(family="handoff", artifact_id="handoff-1", revision=1)
_FAILURE_DIGEST = "sha256:" + "a" * 64
_MATCH_DIGEST = "sha256:" + "b" * 64


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


def _database_url() -> str:
    """Return a private file-backed SQLite URL shared by one runtime and one ledger writer."""

    directory = Path(tempfile.mkdtemp())
    return f"sqlite+aiosqlite:///{directory.as_posix()}/powercontext.sqlite"


def _experience_ref(artifact_id: str, /) -> ArtifactRef:
    return ArtifactRef(family="experience", artifact_id=artifact_id, revision=1)


def _outcome_ref(name: str, /) -> SourceRef:
    return SourceRef(source_type="content", source_id=name)


def _item_ref(outcome_ref: SourceRef, /, *, kind: TaskOutcomeItemKind = "check") -> TaskOutcomeItemRef:
    return TaskOutcomeItemRef(
        task_outcome_ref=outcome_ref,
        item_kind=kind,
        item_index=0,
        item_digest=_FAILURE_DIGEST,
    )


def _selected(scope_id: str, artifact_ref: ArtifactRef, /, *, outcome: str, position: int) -> RecurrenceObservation:
    return new_observation(
        event="selected",
        scope_id=scope_id,
        artifact_ref=artifact_ref,
        signature_key=_CUE_KEY,
        task_outcome_ref=_outcome_ref(outcome),
        task_outcome_position=position,
        handoff_receipt_ref=_RECEIPT_REF,
        handoff_ref=_HANDOFF_REF,
    )


def _recurred(scope_id: str, artifact_ref: ArtifactRef, /, *, outcome: str, position: int) -> RecurrenceObservation:
    return new_observation(
        event="recurred",
        scope_id=scope_id,
        artifact_ref=artifact_ref,
        signature_key=_CUE_KEY,
        task_outcome_ref=_outcome_ref(outcome),
        task_outcome_position=position,
        failure_ref=_item_ref(_outcome_ref(outcome)),
        recurrence_match_digest=_MATCH_DIGEST,
    )


def _avoided(scope_id: str, artifact_ref: ArtifactRef, /, *, outcome: str, position: int) -> RecurrenceObservation:
    return new_observation(
        event="avoided",
        scope_id=scope_id,
        artifact_ref=artifact_ref,
        signature_key=_CUE_KEY,
        task_outcome_ref=_outcome_ref(outcome),
        task_outcome_position=position,
        handoff_receipt_ref=_RECEIPT_REF,
        handoff_ref=_HANDOFF_REF,
        condition_ref=_item_ref(_outcome_ref(outcome), kind="observation"),
        check_ref=_item_ref(_outcome_ref(outcome)),
    )


async def _append_observations(url: str, observations: tuple[RecurrenceObservation, ...], /) -> int:
    """Append ledger events through an independent connection and report the stored count."""

    repository = RecurrenceRepository()
    scope_id = observations[0].scope_id
    async with (
        SQLiteProfile.open(SQLiteConfig(url=url), tables=()) as profile,
        profile.database.transaction() as connection,
    ):
        for observation in observations:
            assert await repository.append_observation(connection, observation)
        return await repository.count_observations(connection, scope_id)


def _experience_draft() -> RepositoryArtifactDraft:
    return RepositoryArtifactDraft(
        family="experience",
        content=ExperienceContent(
            situation="An OpenAPI contract changed and the generated client went stale.",
            action="Regenerate the client as part of every contract change.",
            outcome="The client matched the contract after regeneration.",
            lesson="Regeneration is part of the contract change, not a follow-up.",
            failure=FailureRecord(
                signature=FailureSignature(
                    recall_cue=RECURRENCE_CUE,
                    symptom="Generated client drifted from the contract.",
                ),
                repair_surface="experience_content",
                verification=FailureVerification(
                    condition="the contract changed",
                    check_subject="regenerate the client",
                ),
            ),
        ),
    )


def _handoff_draft(cited: ArtifactRef, /) -> RepositoryArtifactDraft:
    return RepositoryArtifactDraft(
        family="handoff",
        content=HandoffContent(
            objective="Regenerate the client after every contract change",
            state=(
                HandoffStatement(
                    text="The Experience below was selected for this task.",
                    citations=(HandoffArtifactCitation(artifact_ref=cited),),
                ),
            ),
            disposition="continuable",
        ),
    )


async def _create_artifact(url: str, scope_id: str, artifact_id: str, draft: RepositoryArtifactDraft, /) -> ArtifactRef:
    """Create one Artifact revision through an independent connection."""

    repository = ArtifactRepository((Handoff, Experience))
    async with (
        SQLiteProfile.open(SQLiteConfig(url=url), tables=()) as profile,
        profile.database.transaction() as connection,
    ):
        artifact = await repository.create(connection, scope_id, artifact_id, draft)
    return artifact.as_ref()


def test_scoped_statistics_reports_recurrence_readings_from_the_ledger() -> None:
    async def scenario() -> None:
        url = _database_url()
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=url))) as runtime:
            scope_id = await _create_scope(runtime, "statistics-recurrence")
            reference = _experience_ref("experience-1")
            stored = await _append_observations(
                url,
                (
                    _selected(scope_id, reference, outcome="outcome-1", position=1),
                    _recurred(scope_id, reference, outcome="outcome-2", position=2),
                    _avoided(scope_id, reference, outcome="outcome-3", position=3),
                ),
            )
            result = await runtime.statistics.for_scope(scope_id).overview(period=StatisticsPeriod.TODAY)

        assert stored == 3
        recurrence = result.by_scope[0].recurrence
        assert recurrence.model_dump(exclude={"top_revisions"}) == {
            "selected": 1,
            "recurred": 1,
            "avoided": 1,
            "unknown": 1,
            "unlinked_handoff_citations": 0,
            "needing_review": 0,
        }
        assert [
            (item.artifact_ref.artifact_id, item.terminal_recurred_streak) for item in recurrence.top_revisions
        ] == [("experience-1", 0)]

    asyncio.run(scenario())


def test_recurrence_streaks_are_never_merged_across_scopes() -> None:
    async def scenario() -> None:
        url = _database_url()
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=url))) as runtime:
            first = await _create_scope(runtime, "statistics-recurrence-first")
            second = await _create_scope(runtime, "statistics-recurrence-second")
            await _append_observations(
                url,
                tuple(
                    _recurred(first, _experience_ref("experience-1"), outcome=f"outcome-1-{index}", position=index)
                    for index in (1, 2, 3)
                ),
            )
            await _append_observations(
                url,
                (_recurred(second, _experience_ref("experience-2"), outcome="outcome-2-1", position=1),),
            )
            result = await runtime.statistics.overview(ScopeSelection(mode="all"), period=StatisticsPeriod.TODAY)

        by_scope = {item.scope_id: item.recurrence for item in result.by_scope}
        assert by_scope[first].needing_review == 1
        assert by_scope[second].needing_review == 0
        assert [item.artifact_ref.artifact_id for item in by_scope[first].top_revisions] == ["experience-1"]
        assert [item.artifact_ref.artifact_id for item in by_scope[second].top_revisions] == ["experience-2"]
        assert [item.terminal_recurred_streak for item in by_scope[first].top_revisions] == [3]
        assert [item.terminal_recurred_streak for item in by_scope[second].top_revisions] == [1]

    asyncio.run(scenario())


def test_top_revisions_sort_before_truncation() -> None:
    async def scenario() -> None:
        url = _database_url()
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=url))) as runtime:
            scope_id = await _create_scope(runtime, "statistics-recurrence-truncation")
            groups = MAX_RECURRENCE_TOP_REVISIONS + 1
            observations = tuple(
                _recurred(
                    scope_id,
                    _experience_ref(f"experience-{index:02d}"),
                    outcome=f"outcome-{index:02d}-{step}",
                    position=step,
                )
                for index in range(1, groups + 1)
                for step in range(1, index + 1)
            )
            await _append_observations(url, observations)
            result = await runtime.statistics.for_scope(scope_id).overview(period=StatisticsPeriod.TODAY)

        recurrence = result.by_scope[0].recurrence
        identifiers = [item.artifact_ref.artifact_id for item in recurrence.top_revisions]
        assert len(identifiers) == MAX_RECURRENCE_TOP_REVISIONS
        assert [item.terminal_recurred_streak for item in recurrence.top_revisions] == list(
            range(groups, groups - MAX_RECURRENCE_TOP_REVISIONS, -1)
        )
        assert identifiers == [f"experience-{index:02d}" for index in range(groups, 1, -1)]
        assert recurrence.needing_review == groups - 2

    asyncio.run(scenario())


def test_unlinked_handoff_citations_are_independent_of_selected() -> None:
    async def scenario() -> None:
        url = _database_url()
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=url))) as runtime:
            cited_scope = await _create_scope(runtime, "statistics-recurrence-unlinked")
            linked_scope = await _create_scope(runtime, "statistics-recurrence-linked")
            cited = await _create_artifact(url, cited_scope, "experience-cited", _experience_draft())
            await _create_artifact(url, cited_scope, "handoff-cited", _handoff_draft(cited))
            await _append_observations(
                url,
                (_selected(linked_scope, _experience_ref("experience-linked"), outcome="outcome-9", position=9),),
            )
            unlinked = await runtime.statistics.for_scope(cited_scope).overview(period=StatisticsPeriod.TODAY)
            linked = await runtime.statistics.for_scope(linked_scope).overview(period=StatisticsPeriod.TODAY)

        assert unlinked.by_scope[0].recurrence.unlinked_handoff_citations == 1
        assert unlinked.by_scope[0].recurrence.selected == 0
        assert unlinked.by_scope[0].recurrence.top_revisions == ()
        assert linked.by_scope[0].recurrence.unlinked_handoff_citations == 0
        assert linked.by_scope[0].recurrence.selected == 1
        assert linked.by_scope[0].recurrence.unknown == 1

    asyncio.run(scenario())
