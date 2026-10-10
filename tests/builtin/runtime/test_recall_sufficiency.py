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

from dataclasses import fields, replace
from math import sqrt

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceSearchHit
from powercontext.builtin.artifacts.memory.fusion import _MIN_SEMANTIC_SIMILARITY, admit_vector_candidates
from powercontext.builtin.artifacts.memory.models import MemoryChannelHit, MemoryMatchedBy
from powercontext.builtin.artifacts.search import (
    _FTS_MIN_MATCHED_TERMS,
    _FTS_MIN_QUERY_COVERAGE,
    DEFAULT_ADMISSION_FLOOR,
    AdmissionCounts,
    AdmissionFloor,
    admits_fts_text,
    fts_query_requirements,
)
from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchHit
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexHit
from powercontext.builtin.runtime.application import (
    _families_with_recoverable_candidates,
    _families_with_retrieved_candidates,
)
from powercontext.builtin.runtime.atomic_memory import AtomicMemorySearchHit
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.runtime.prepared_context import PreparedContextOmissions
from powercontext.builtin.runtime.recall_sufficiency import (
    BUDGET_FLOOR_BYTES,
    EXPERIENCE_FAMILY,
    MEMORY_FAMILY,
    POLICY_ID,
    REASON_AT_MAX_ROUNDS,
    REASON_BUDGET_FLOOR,
    REASON_EXPANSION_FAILED,
    REASON_NO_CONTENT,
    REASON_RERANK_ENABLED,
    REASON_SUFFICIENT,
    REASON_THIN_CANDIDATES,
    REASON_THIN_FAMILIES,
    REASON_WEAK_LEXICAL,
    REASON_WEAK_TOP_ONE,
    RecallBudgetView,
    RecallCandidate,
    RecallEffort,
    RecallExpander,
    RecallSufficiencyGate,
    RecallSufficiencyPolicy,
    SearchPlan,
    build_recall_candidates,
    candidate_identity,
    recall_effort,
    recall_effort_measurement,
)

MEMORY_REF = ArtifactRef(family="atomic-memory", artifact_id="memory", revision=3)


def _budget_bound_view() -> RecallBudgetView:
    """A probe view at the declared byte floor, where the budget is the constraint."""

    return RecallBudgetView(max_bytes=512, delivered_items=1, unused_bytes=0)


def _budget_open_view() -> RecallBudgetView:
    """A probe view with unused headroom and no drops, so recall is the only candidate cause."""

    return RecallBudgetView(max_bytes=8000, delivered_items=2, unused_bytes=6000)


def _memory_candidate(text: str, *, artifact_id: str = "memory", revision: int = 1) -> RecallCandidate:
    return RecallCandidate(
        family="memory",
        artifact_id=artifact_id,
        revision=revision,
        entry_id=None,
        entry_version_id=None,
        score=1.0,
        text=text,
    )


def _topic_candidate(
    score: float,
    *,
    artifact_id: str = "topic",
    revision: int = 1,
    text: str = "zzz",
    relevance: float | None = None,
) -> RecallCandidate:
    return RecallCandidate(
        family="topic-memory",
        artifact_id=artifact_id,
        revision=revision,
        entry_id=None,
        entry_version_id=None,
        score=score,
        text=text,
        relevance=relevance,
    )


def _experience_candidate(text: str, *, artifact_id: str = "experience") -> RecallCandidate:
    return RecallCandidate(
        family="experience",
        artifact_id=artifact_id,
        revision=1,
        entry_id=None,
        entry_version_id=None,
        score=0.0,
        text=text,
    )


def _memory_hit(
    *,
    score: float,
    matched_by: tuple[MemoryMatchedBy, ...],
    text: str = "alpha beta",
    artifact_ref: ArtifactRef = MEMORY_REF,
    distance: float | None = None,
) -> AtomicMemorySearchHit:
    return AtomicMemorySearchHit(
        hit=AtomicMemoryIndexHit(
            artifact_ref=artifact_ref,
            state_version=0,
            kind="fact",
            text=text,
            score=score,
            distance=distance,
        ),
        matched_by=matched_by,
    )


def _topic_hit(
    *,
    score: float,
    artifact_id: str = "topic",
    revision: int = 1,
    title: str = "Title",
    summary: str = "Summary",
    snippet: str | None = None,
    relevance: float | None = None,
) -> TopicMemorySearchHit:
    return TopicMemorySearchHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id=artifact_id, revision=revision),
        title=title,
        summary=summary,
        snippet=snippet,
        score=score,
        matched_by=("topic_fts",),
        relevance=relevance,
    )


def _experience_hit(artifact_id: str = "experience") -> ExperienceSearchHit:
    return ExperienceSearchHit(
        artifact_ref=ArtifactRef(family="experience", artifact_id=artifact_id, revision=1),
        content=ExperienceContent(
            situation="Situation text",
            action="Action text",
            outcome="Outcome text",
            lesson="Lesson text",
        ),
    )


# ── Floor defaults ──────────────────────────────────────────────────────────────────────────


def test_default_admission_floor_matches_shared_constants() -> None:
    assert (
        DEFAULT_ADMISSION_FLOOR.lexical_coverage,
        DEFAULT_ADMISSION_FLOOR.lexical_min_matched_terms,
        DEFAULT_ADMISSION_FLOOR.min_semantic_similarity,
    ) == (0.25, 2, 0.3)
    assert DEFAULT_ADMISSION_FLOOR.lexical_coverage == _FTS_MIN_QUERY_COVERAGE
    assert DEFAULT_ADMISSION_FLOOR.lexical_min_matched_terms == _FTS_MIN_MATCHED_TERMS
    assert DEFAULT_ADMISSION_FLOOR.min_semantic_similarity == _MIN_SEMANTIC_SIMILARITY


# ── floor=None equivalence and lowering ─────────────────────────────────────────────────────


def test_floor_none_matches_omitted_on_the_term_side() -> None:
    query = "alpha beta gamma delta"
    assert fts_query_requirements(query, floor=None) == fts_query_requirements(query)
    assert admits_fts_text(query, "alpha only", floor=None) is admits_fts_text(query, "alpha only")


def test_lower_floor_admits_a_term_candidate_the_default_rejects() -> None:
    query = "alpha beta gamma delta"
    text = "alpha only"
    assert admits_fts_text(query, text) is False
    round_one = AdmissionFloor(lexical_coverage=0.0, lexical_min_matched_terms=1, min_semantic_similarity=0.15)
    assert admits_fts_text(query, text, floor=round_one) is True


def test_lower_floor_admits_a_vector_candidate_the_default_rejects() -> None:
    distance = 1.2649  # cosine similarity ~= 0.2, between the round-1 floor (0.15) and the default (0.3)
    candidates = (
        MemoryChannelHit(
            memory_ref=MEMORY_REF,
            entry_id="entry",
            entry_version_id="entry-v1",
            text="alpha beta",
            distance=distance,
        ),
    )
    assert admit_vector_candidates(candidates) == ()
    round_one = AdmissionFloor(lexical_coverage=0.0, lexical_min_matched_terms=1, min_semantic_similarity=0.15)
    assert admit_vector_candidates(candidates, admission=round_one) == candidates


# ── Gate verdict order ──────────────────────────────────────────────────────────────────────


def test_gate_reports_no_content_before_every_other_signal() -> None:
    policy = RecallSufficiencyPolicy()
    assessment = RecallSufficiencyGate().assess(
        (),
        "alpha beta",
        policy,
        scope_has_content=False,
        budget=_budget_bound_view(),
        families_expected=3,
    )
    assert assessment.sufficient is True
    assert assessment.reason == REASON_NO_CONTENT


def test_gate_reports_budget_floor_before_candidate_signals() -> None:
    policy = RecallSufficiencyPolicy()
    assessment = RecallSufficiencyGate().assess(
        (_memory_candidate("alpha beta"),),
        "alpha beta",
        policy,
        scope_has_content=True,
        budget=_budget_bound_view(),
    )
    assert assessment.sufficient is True
    assert assessment.reason == REASON_BUDGET_FLOOR


def test_gate_reports_rerank_enabled_when_expansion_is_not_allowed() -> None:
    policy = RecallSufficiencyPolicy(rerank_enabled=True, allow_expansion_with_rerank=False)
    assessment = RecallSufficiencyGate().assess(
        (_memory_candidate("alpha beta"),),
        "alpha beta",
        policy,
        scope_has_content=True,
        budget=_budget_open_view(),
    )
    assert assessment.sufficient is True
    assert assessment.reason == REASON_RERANK_ENABLED


def test_gate_reports_thin_candidates_before_thin_families() -> None:
    policy = RecallSufficiencyPolicy(min_candidates=2)
    assessment = RecallSufficiencyGate().assess(
        (),
        "alpha beta",
        policy,
        scope_has_content=True,
        families_expected=3,
    )
    assert assessment.sufficient is False
    assert assessment.reason == REASON_THIN_CANDIDATES


def test_gate_reports_thin_families_when_a_selected_family_returned_nothing() -> None:
    policy = RecallSufficiencyPolicy()
    candidates = (_memory_candidate("alpha beta"), _memory_candidate("alpha beta", artifact_id="memory-2"))
    assessment = RecallSufficiencyGate().assess(
        candidates,
        "alpha beta",
        policy,
        scope_has_content=True,
        families_expected=3,
    )
    assert assessment.sufficient is False
    assert assessment.reason == REASON_THIN_FAMILIES
    assert assessment.signals.family_count == 1
    assert assessment.signals.families_expected == 3


def test_gate_reports_weak_top_one_for_a_weak_scoring_family() -> None:
    policy = RecallSufficiencyPolicy(min_top_score=0.35)
    candidates = (
        _topic_candidate(1.0, relevance=0.2),
        _topic_candidate(0.9, artifact_id="topic-2", relevance=0.1),
    )
    assessment = RecallSufficiencyGate().assess(
        candidates,
        "alpha beta gamma delta",
        policy,
        scope_has_content=True,
        families_expected=1,
    )
    assert assessment.sufficient is False
    assert assessment.reason == REASON_WEAK_TOP_ONE
    assert assessment.signals.scored_families == 1


def test_gate_reports_thin_candidates_even_when_the_top_is_weak() -> None:
    """A thin and weak set still expands: lowering admission can recover
    round-zero-rejected evidence, so ``weak-top-1`` must not stop expansion
    before the thin check runs."""
    policy = RecallSufficiencyPolicy(min_candidates=3)
    candidates = (
        _topic_candidate(1.0, relevance=0.2),
        _topic_candidate(0.9, artifact_id="topic-2", relevance=0.1),
    )
    assessment = RecallSufficiencyGate().assess(
        candidates,
        "alpha beta gamma delta",
        policy,
        scope_has_content=True,
        families_expected=1,
    )
    assert assessment.sufficient is False
    assert assessment.reason == REASON_THIN_CANDIDATES


def _scoped_memory_candidate(
    text: str,
    *,
    scope_id: str,
    relevance: float | None,
) -> RecallCandidate:
    return RecallCandidate(
        family="memory",
        artifact_id=f"memory-{scope_id}",
        revision=1,
        entry_id="entry",
        entry_version_id="entry-v1",
        score=1.0,
        text=text,
        relevance=relevance,
        scope_id=scope_id,
    )


def test_gate_scores_each_scope_by_its_own_first_fused_hit() -> None:
    """Each Scope's fusion run applies the first-hit rule independently of Scope order."""
    policy = RecallSufficiencyPolicy()
    scope_a = (
        _scoped_memory_candidate("alpha beta", scope_id="scope-a", relevance=None),
        _scoped_memory_candidate("alpha beta", scope_id="scope-a", relevance=0.9),
    )
    scope_b = (_scoped_memory_candidate("alpha beta", scope_id="scope-b", relevance=0.31),)

    gate = RecallSufficiencyGate()
    a_first = gate.assess(scope_a + scope_b, "alpha beta", policy, scope_has_content=True, families_expected=1)
    b_first = gate.assess(scope_b + scope_a, "alpha beta", policy, scope_has_content=True, families_expected=1)

    assert a_first.reason == b_first.reason == REASON_WEAK_TOP_ONE
    assert a_first.signals.top_score == b_first.signals.top_score == pytest.approx(0.31)
    assert a_first.signals.scored_families == b_first.signals.scored_families == 1


def test_build_recall_candidates_wires_scope_ids_into_the_per_scope_rule() -> None:
    """Deleting ``memory_scope_ids`` must change observable behavior: without it every
    Memory hit shares one group and the flat first hit decides scoring for all Scopes."""
    fts_first = _memory_hit(
        score=1 / 61,
        matched_by=("fts",),
        artifact_ref=ArtifactRef(family="atomic-memory", artifact_id="a-fts", revision=1),
    )
    deep_vector = _memory_hit(
        score=1 / 62,
        matched_by=("vector",),
        distance=sqrt(2 * (1 - 0.9)),
        artifact_ref=ArtifactRef(family="atomic-memory", artifact_id="a-vector", revision=1),
    )
    ref_vector = _memory_hit(
        score=1 / 61,
        matched_by=("vector",),
        distance=sqrt(2 * (1 - 0.31)),
        artifact_ref=ArtifactRef(family="atomic-memory", artifact_id="b-vector", revision=1),
    )
    gate = RecallSufficiencyGate()
    policy = RecallSufficiencyPolicy()

    scoped = build_recall_candidates(
        memory_hits=(fts_first, deep_vector, ref_vector),
        memory_scope_ids=("scope-a", "scope-a", "scope-b"),
        topic_memory_hits=(),
        experience_hits=(),
    )
    unscoped = build_recall_candidates(
        memory_hits=(fts_first, deep_vector, ref_vector),
        topic_memory_hits=(),
        experience_hits=(),
    )

    scoped_assessment = gate.assess(scoped, "alpha beta", policy, scope_has_content=True, families_expected=1)
    unscoped_assessment = gate.assess(unscoped, "alpha beta", policy, scope_has_content=True, families_expected=1)

    assert scoped_assessment.reason == REASON_WEAK_TOP_ONE
    assert scoped_assessment.signals.top_score == pytest.approx(0.31)
    assert unscoped_assessment.reason != REASON_WEAK_TOP_ONE
    assert unscoped_assessment.signals.scored_families == 0


def test_gate_lexical_and_family_skip_for_a_scope_without_vector_evidence() -> None:
    """A Scope whose fused top is FTS-only contributes no scores; the verdict still uses the
    candidates it admitted for counts and lexical overlap."""
    policy = RecallSufficiencyPolicy(min_top_score=0.35, min_top_gap=0.02, min_lexical_overlap=0.5)
    candidates = (
        _scoped_memory_candidate("alpha beta", scope_id="scope-a", relevance=None),
        _scoped_memory_candidate("alpha beta", scope_id="scope-b", relevance=0.8),
        _scoped_memory_candidate("alpha beta", scope_id="scope-b", relevance=0.5),
    )
    assessment = RecallSufficiencyGate().assess(
        candidates,
        "alpha beta",
        policy,
        scope_has_content=True,
        families_expected=1,
    )
    assert assessment.sufficient is True
    assert assessment.signals.candidate_count == 3
    assert assessment.signals.top_score == pytest.approx(0.8)


def test_gate_reports_weak_lexical_when_the_best_candidate_misses_query_terms() -> None:
    policy = RecallSufficiencyPolicy(min_top_score=0.35, min_top_gap=0.02, min_lexical_overlap=0.5)
    candidates = (
        _topic_candidate(1.0, relevance=0.8),
        _topic_candidate(0.5, artifact_id="topic-2", relevance=0.5),
    )
    assessment = RecallSufficiencyGate().assess(
        candidates,
        "alpha beta gamma delta",
        policy,
        scope_has_content=True,
        families_expected=1,
    )
    assert assessment.sufficient is False
    assert assessment.reason == REASON_WEAK_LEXICAL
    assert assessment.signals.lexical_overlap == 0.0


def test_gate_reports_sufficient_when_every_signal_passes() -> None:
    policy = RecallSufficiencyPolicy()
    text = "alpha beta gamma delta"
    candidates = (
        _topic_candidate(1.0, text=text, relevance=0.8),
        _topic_candidate(0.5, artifact_id="topic-2", text=text, relevance=0.5),
    )
    assessment = RecallSufficiencyGate().assess(
        candidates,
        text,
        policy,
        scope_has_content=True,
        families_expected=1,
    )
    assert assessment.sufficient is True
    assert assessment.reason == REASON_SUFFICIENT


# ── Expander ────────────────────────────────────────────────────────────────────────────────


def test_expander_plan_maps_each_round_to_its_admission_floor() -> None:
    policy = RecallSufficiencyPolicy()
    expander = RecallExpander()
    round_one = expander.plan(1, policy)
    round_two = expander.plan(2, policy)
    assert round_one.action == "admission"
    assert round_one.admission == policy.round1_admission
    assert round_two.action == "policy-floor"
    assert round_two.admission == policy.round2_admission


@pytest.mark.parametrize("round_number", [0, 3, -1])
def test_expander_rejects_rounds_outside_one_and_two(round_number: int) -> None:
    with pytest.raises(ValueError, match="round must be 1 or 2"):
        RecallExpander().plan(round_number, RecallSufficiencyPolicy())


def test_search_plan_structurally_carries_no_limit_mode_or_family() -> None:
    names = {field.name for field in fields(SearchPlan)}
    assert names == {"action", "admission"}
    assert "limit" not in names
    assert "mode" not in names
    assert not any(name.endswith("family") for name in names)


# ── Policy construction from config ─────────────────────────────────────────────────────────


def test_policy_is_none_when_the_gate_is_disabled() -> None:
    assert RecallSufficiencyPolicy.from_runtime_config(RuntimeConfig()) is None


def test_policy_maps_every_threshold_when_enabled() -> None:
    config = RuntimeConfig(
        recall_gate_enabled=True,
        recall_gate_max_rounds=1,
        recall_gate_min_candidates=3,
        recall_gate_min_top_score=0.4,
        recall_gate_min_top_gap=0.05,
        recall_gate_min_lexical_overlap=0.6,
        recall_gate_round1_min_semantic_similarity=0.12,
        recall_gate_round2_min_semantic_similarity=0.08,
        memory_rerank_enabled=True,
        recall_gate_allow_with_rerank=True,
    )
    policy = RecallSufficiencyPolicy.from_runtime_config(config)
    assert policy is not None
    assert policy.max_rounds == 1
    assert policy.min_candidates == 3
    assert policy.min_top_score == 0.4
    assert policy.min_top_gap == 0.05
    assert policy.min_lexical_overlap == 0.6
    assert policy.round1_admission == AdmissionFloor(0.0, 1, 0.12)
    assert policy.round2_admission == AdmissionFloor(0.0, 1, 0.08)
    assert policy.rerank_enabled is True
    assert policy.allow_expansion_with_rerank is True


def test_runtime_config_rejects_expansion_thresholds_stricter_than_round_zero() -> None:
    with pytest.raises(ValueError, match="round1_min_semantic_similarity"):
        RuntimeConfig(recall_gate_enabled=True, recall_gate_round1_min_semantic_similarity=0.31)


def test_runtime_config_requires_round_two_to_widen_or_equal_round_one() -> None:
    with pytest.raises(ValueError, match="round2_min_semantic_similarity"):
        RuntimeConfig(
            recall_gate_enabled=True,
            recall_gate_round1_min_semantic_similarity=0.12,
            recall_gate_round2_min_semantic_similarity=0.13,
        )


def test_policy_carries_no_pool_size_knob_and_no_base_admission() -> None:
    config = RuntimeConfig(recall_gate_enabled=True, memory_rerank_enabled=True)
    policy = RecallSufficiencyPolicy.from_runtime_config(config)
    assert policy is not None
    assert policy.rerank_enabled is True
    assert policy.allow_expansion_with_rerank is False
    # RFC 1560 forbids expansion from touching the backend candidate pool, so the policy has
    # no knob that could resize it, and no `base_admission` that could duplicate `None`.
    policy_names = {item.name for item in fields(policy)}
    assert "round2_rerank_candidate_limit" not in policy_names
    assert "base_admission" not in policy_names


# ── Score honesty and normalization ─────────────────────────────────────────────────────────


def test_experience_only_candidates_never_expose_a_fabricated_score() -> None:
    policy = RecallSufficiencyPolicy()
    candidates = build_recall_candidates(
        memory_hits=(),
        topic_memory_hits=(),
        experience_hits=(_experience_hit(), _experience_hit("experience-2")),
    )
    assessment = RecallSufficiencyGate().assess(
        candidates,
        "Situation Action Outcome Lesson",
        policy,
        scope_has_content=True,
        families_expected=1,
    )
    assert assessment.signals.scored_families == 0
    assert assessment.signals.top_score == 0.0
    assert assessment.reason != REASON_WEAK_TOP_ONE


def test_topic_candidates_use_vector_relevance_as_top_score() -> None:
    candidates = build_recall_candidates(
        memory_hits=(),
        topic_memory_hits=(_topic_hit(score=42.0, relevance=0.78),),
        experience_hits=(),
    )
    signals = (
        RecallSufficiencyGate()
        .assess(
            candidates,
            "alpha beta",
            RecallSufficiencyPolicy(),
            scope_has_content=True,
            families_expected=1,
        )
        .signals
    )
    assert signals.scored_families == 1
    assert candidates[0].score == pytest.approx(0.42)
    assert signals.top_score == pytest.approx(0.78)


def test_gate_thresholds_use_cosine_relevance_independently_of_fused_rank() -> None:
    policy = RecallSufficiencyPolicy(min_candidates=2, min_top_score=0.35, min_top_gap=0.02)
    strong_rank_weak_cosine = (
        _topic_candidate(1.0, text="alpha beta", relevance=0.3),
        _topic_candidate(0.5, artifact_id="topic-2", text="alpha beta", relevance=0.1),
    )
    weak_rank_strong_cosine = (
        _topic_candidate(0.01, text="alpha beta", relevance=0.8),
        _topic_candidate(0.005, artifact_id="topic-2", text="alpha beta", relevance=0.5),
    )
    flat_cosine = (
        _topic_candidate(1.0, text="alpha beta", relevance=0.8),
        _topic_candidate(0.5, artifact_id="topic-2", text="alpha beta", relevance=0.8),
    )
    gate = RecallSufficiencyGate()
    assert gate.assess(strong_rank_weak_cosine, "alpha beta", policy).reason == REASON_WEAK_TOP_ONE
    assert gate.assess(weak_rank_strong_cosine, "alpha beta", policy).reason == REASON_SUFFICIENT
    assert gate.assess(flat_cosine, "alpha beta", policy).reason == REASON_SUFFICIENT


def test_lower_floor_candidates_cannot_manufacture_a_top_gap() -> None:
    policy = RecallSufficiencyPolicy(min_top_score=0.35, min_top_gap=0.02)
    baseline = (
        _topic_candidate(1.0, text="alpha beta", relevance=0.4),
        _topic_candidate(0.9, artifact_id="topic-2", text="alpha beta", relevance=0.39),
    )
    expanded = (*baseline, _topic_candidate(0.5, artifact_id="topic-3", text="alpha beta", relevance=0.15))
    gate = RecallSufficiencyGate()
    before = gate.assess(baseline, "alpha beta", policy)
    after = gate.assess(expanded, "alpha beta", policy)
    assert before.reason == after.reason == REASON_WEAK_TOP_ONE
    assert after.signals.top_gap == pytest.approx(before.signals.top_gap)


@pytest.mark.parametrize("fts_competitor", [False, True], ids=["single-candidate", "fts-competitor"])
def test_one_scored_candidate_skips_gap_even_with_fts_competitors(fts_competitor: bool) -> None:
    candidates = [_topic_candidate(1.0, text="alpha beta", relevance=0.5)]
    if fts_competitor:
        candidates.append(_topic_candidate(0.9, artifact_id="topic-2", text="alpha beta"))
    assessment = RecallSufficiencyGate().assess(
        candidates, "alpha beta", RecallSufficiencyPolicy(min_candidates=len(candidates))
    )
    assert assessment.reason == REASON_SUFFICIENT
    assert assessment.signals.scored_families == 1
    assert assessment.signals.gap_families == 0


def test_fts_first_family_skips_score_threshold_despite_lower_vector_hit() -> None:
    candidates = (
        _topic_candidate(1.0, text="alpha beta"),
        _topic_candidate(0.9, artifact_id="topic-2", text="alpha beta", relevance=0.31),
    )
    assessment = RecallSufficiencyGate().assess(candidates, "alpha beta", RecallSufficiencyPolicy())
    assert assessment.reason == REASON_SUFFICIENT
    assert assessment.signals.scored_families == 0


def test_fts_only_candidates_skip_relevance_thresholds() -> None:
    candidates = build_recall_candidates(
        memory_hits=(_memory_hit(score=1 / 61, matched_by=("fts",)),),
        topic_memory_hits=(_topic_hit(score=100.0, artifact_id="topic-2", title="alpha", summary="beta"),),
        experience_hits=(),
    )
    assessment = RecallSufficiencyGate().assess(candidates, "alpha beta", RecallSufficiencyPolicy())
    assert assessment.reason == REASON_SUFFICIENT
    assert assessment.signals.scored_families == 0
    assert assessment.signals.gap_families == 0


def test_memory_single_channel_top_rank_normalizes_to_one() -> None:
    candidates = build_recall_candidates(
        memory_hits=(_memory_hit(score=1 / 61, matched_by=("fts",)),),
        topic_memory_hits=(),
        experience_hits=(),
    )
    assert candidates[0].score == pytest.approx(1.0)


def test_memory_two_channel_hit_normalizes_against_its_wider_upper_bound() -> None:
    candidates = build_recall_candidates(
        memory_hits=(_memory_hit(score=1 / 61, matched_by=("fts", "vector")),),
        topic_memory_hits=(),
        experience_hits=(),
    )
    assert candidates[0].score == pytest.approx(0.5)


def test_memory_vector_relevance_reaches_gate_without_changing_rank_score() -> None:
    hits = (
        _memory_hit(score=2 / 61, matched_by=("fts", "vector"), text="alpha beta", distance=sqrt(2 * (1 - 0.3))),
        _memory_hit(
            score=1 / 62,
            matched_by=("vector",),
            text="alpha beta",
            distance=sqrt(2 * (1 - 0.1)),
            artifact_ref=ArtifactRef(family="atomic-memory", artifact_id="other", revision=1),
        ),
    )
    candidates = build_recall_candidates(memory_hits=hits, topic_memory_hits=(), experience_hits=())
    assessment = RecallSufficiencyGate().assess(candidates, "alpha beta", RecallSufficiencyPolicy())
    assert candidates[0].score == pytest.approx(1.0)
    assert assessment.signals.top_score == pytest.approx(0.3)
    assert assessment.reason == REASON_WEAK_TOP_ONE


# ── lexical_overlap semantics ───────────────────────────────────────────────────────────────


def test_lexical_overlap_is_the_best_candidate_query_term_recall() -> None:
    query = "alpha beta gamma delta"
    candidates = (_memory_candidate("alpha"), _memory_candidate("beta"))
    signals = (
        RecallSufficiencyGate()
        .assess(
            candidates,
            query,
            RecallSufficiencyPolicy(min_lexical_overlap=0.0),
            scope_has_content=True,
            families_expected=1,
        )
        .signals
    )
    # Both candidates share at least one query term, so "share >= 1 term" would be 1.0; the signal
    # the gate actually uses is the best candidate's recall, which here is only 1 of 4 terms.
    assert signals.lexical_overlap == pytest.approx(0.25)


def test_lexical_overlap_selects_the_maximum_over_candidates() -> None:
    query = "alpha beta gamma delta"
    candidates = (_memory_candidate("alpha"), _memory_candidate("alpha beta"))
    signals = (
        RecallSufficiencyGate()
        .assess(
            candidates,
            query,
            RecallSufficiencyPolicy(min_lexical_overlap=0.0),
            scope_has_content=True,
            families_expected=1,
        )
        .signals
    )
    assert signals.lexical_overlap == pytest.approx(0.5)


# ── Candidate projection and identity ───────────────────────────────────────────────────────


def test_candidate_identity_preserves_the_exact_artifact_revision() -> None:
    memory = build_recall_candidates(
        memory_hits=(_memory_hit(score=1 / 61, matched_by=("fts",)),),
        topic_memory_hits=(),
        experience_hits=(),
    )[0]
    assert candidate_identity(memory) == ("memory", "memory", 3, None, None)

    experience = build_recall_candidates(
        memory_hits=(),
        topic_memory_hits=(),
        experience_hits=(_experience_hit(),),
    )[0]
    assert candidate_identity(experience) == ("experience", "experience", 1, None, None)


def test_distinct_source_count_uses_each_atomic_memory_identity() -> None:
    memory_hits = tuple(
        _memory_hit(
            artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=f"memory-{index}", revision=3),
            text="alpha beta",
            score=1 / (61 + index),
            matched_by=("fts",),
        )
        for index in range(3)
    )
    candidates = build_recall_candidates(
        memory_hits=memory_hits,
        topic_memory_hits=(),
        experience_hits=(_experience_hit(),),
    )
    signals = (
        RecallSufficiencyGate()
        .assess(
            candidates,
            "alpha beta",
            RecallSufficiencyPolicy(min_lexical_overlap=0.0),
            scope_has_content=True,
            families_expected=2,
        )
        .signals
    )
    # Independent Atomic artifacts remain distinct even when they share a revision number.
    assert signals.distinct_source_count == 4


def test_build_recall_candidates_orders_memory_then_topic_then_experience() -> None:
    candidates = build_recall_candidates(
        memory_hits=(_memory_hit(score=1 / 61, matched_by=("fts",)),),
        topic_memory_hits=(_topic_hit(score=42.0),),
        experience_hits=(_experience_hit(),),
    )
    assert [candidate.family for candidate in candidates] == ["memory", "topic-memory", "experience"]


# ── Status vocabulary sanity ────────────────────────────────────────────────────────────────


def test_expansion_status_vocabulary_is_stable() -> None:
    assert REASON_AT_MAX_ROUNDS == "at-max-rounds"
    assert REASON_EXPANSION_FAILED == "expansion-failed"


# ── RecallEffort shape and invariants ───────────────────────────────────────────────────────


def test_effort_rounds_is_one_plus_the_committed_expansions() -> None:
    policy = RecallSufficiencyPolicy()
    none = recall_effort(policy=policy, assessment=REASON_SUFFICIENT, candidates_by_round=(7,))
    one = recall_effort(
        policy=policy,
        assessment=REASON_THIN_CANDIDATES,
        expansion_actions=("admission",),
        candidates_by_round=(3, 9),
    )
    two = recall_effort(
        policy=policy,
        assessment=REASON_AT_MAX_ROUNDS,
        expansion_actions=("admission", "policy-floor"),
        candidates_by_round=(3, 6, 9),
    )

    assert none.rounds == 1
    assert none.expansion_actions == ()
    assert one.rounds == 2
    assert one.expansion_actions == ("admission",)
    assert two.rounds == 3
    assert two.expansion_actions == ("admission", "policy-floor")
    for effort in (none, one, two):
        assert effort.rounds == 1 + len(effort.expansion_actions)
        assert len(effort.candidates_by_round) == effort.rounds
        assert effort.policy == "powercontext.recall-gate.v2"


def test_effort_folds_the_builder_omission_counts_and_keeps_their_sum() -> None:
    omissions = PreparedContextOmissions(
        truncated_items=2,
        dropped_items=3,
        dropped_below_min_bytes=1,
        dropped_no_fitting_truncation=2,
    )
    effort = recall_effort(
        policy=RecallSufficiencyPolicy(),
        assessment=REASON_SUFFICIENT,
        candidates_by_round=(4,),
        omissions=omissions,
    )

    assert effort.truncated_items == 2
    assert effort.dropped_items == 3
    assert effort.dropped_below_min_bytes == 1
    assert effort.dropped_no_fitting_truncation == 2
    assert effort.dropped_items == effort.dropped_below_min_bytes + effort.dropped_no_fitting_truncation


def test_effort_carries_per_family_admission_counts_and_search_reported_costs() -> None:
    counts = (
        AdmissionCounts(family=MEMORY_FAMILY, scope_id="project:demo", retrieved=64, admitted=3),
        AdmissionCounts(family=EXPERIENCE_FAMILY, scope_id="project:demo", retrieved=3, admitted=1),
    )
    effort = recall_effort(
        policy=RecallSufficiencyPolicy(),
        assessment=REASON_THIN_CANDIDATES,
        expansion_actions=("admission",),
        candidates_by_round=(3, 7),
        admission_by_family=counts,
        added_embeddings=0,
        added_generation_calls=1,
    )

    assert effort.admission_by_family == counts
    assert effort.added_embeddings == 0
    assert effort.added_generation_calls == 1


def test_effort_cost_counts_default_to_zero_rather_than_an_inference() -> None:
    effort = RecallEffort(
        policy=POLICY_ID,
        assessment=REASON_SUFFICIENT,
        rounds=1,
        expansion_actions=(),
        candidates_by_round=(1,),
    )

    assert effort.added_embeddings == 0
    assert effort.added_generation_calls == 0
    assert effort.admission_by_family == ()


def test_effort_projects_exact_additive_measurement_without_family_evidence() -> None:
    effort = recall_effort(
        policy=RecallSufficiencyPolicy(),
        assessment=REASON_AT_MAX_ROUNDS,
        expansion_actions=("admission", "policy-floor"),
        candidates_by_round=(3, 6, 9),
        admission_by_family=(AdmissionCounts(family="memory", scope_id="private-scope", retrieved=12, admitted=9),),
        added_embeddings=2,
        added_generation_calls=4,
        omissions=PreparedContextOmissions(
            truncated_items=2, dropped_items=5, dropped_below_min_bytes=2, dropped_no_fitting_truncation=3
        ),
    )
    assert recall_effort_measurement(effort).model_dump() == {
        "policy_id": POLICY_ID,
        "assessment": REASON_AT_MAX_ROUNDS,
        "preparations": 1,
        "rounds": 3,
        "expanded_preparations": 1,
        "admission_expansions": 1,
        "policy_floor_expansions": 1,
        "candidate_round_samples": 3,
        "candidates_assessed": 18,
        "final_candidate_pool": 9,
        "added_embeddings": 2,
        "added_generation_calls": 4,
        "truncated_items": 2,
        "dropped_items": 5,
        "dropped_below_min_bytes": 2,
        "dropped_no_fitting_truncation": 3,
    }


def test_effort_without_expansion_keeps_zero_costs_and_a_zero_candidate_sample() -> None:
    effort = recall_effort(policy=RecallSufficiencyPolicy(), assessment=REASON_NO_CONTENT, candidates_by_round=(0,))
    measurement = recall_effort_measurement(effort)
    assert measurement.preparations == measurement.rounds == measurement.candidate_round_samples == 1
    assert (
        measurement.expanded_preparations
        == measurement.admission_expansions
        == measurement.policy_floor_expansions
        == 0
    )
    assert measurement.candidates_assessed == measurement.final_candidate_pool == 0
    assert measurement.added_embeddings == measurement.added_generation_calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"rounds": 2},
        {"candidates_by_round": ()},
        {"candidates_by_round": (-1,)},
        {"candidates_by_round": (True,)},
        {"expansion_actions": ("unknown",)},
        {"rounds": 2, "expansion_actions": ("policy-floor",), "candidates_by_round": (1, 2)},
        {"added_embeddings": -1},
        {"added_generation_calls": -1},
        {"truncated_items": -1},
        {"dropped_items": 1},
        {"dropped_items": 0, "dropped_below_min_bytes": -1, "dropped_no_fitting_truncation": 1},
        {"policy": "x" * 129},
        {"assessment": "query text must not be telemetry"},
    ],
)
def test_effort_measurement_rejects_invalid_accounting_before_persistence(changes) -> None:
    effort = recall_effort(policy=RecallSufficiencyPolicy(), assessment=REASON_SUFFICIENT, candidates_by_round=(1,))
    with pytest.raises(ValueError):
        recall_effort_measurement(replace(effort, **changes))


def test_budget_view_is_bound_only_when_the_probe_observed_fitting_pressure() -> None:
    assert RecallBudgetView(max_bytes=BUDGET_FLOOR_BYTES).budget_bounded is False
    assert RecallBudgetView(max_bytes=BUDGET_FLOOR_BYTES, delivered_items=1, unused_bytes=0).budget_bounded is True
    assert RecallBudgetView(max_bytes=BUDGET_FLOOR_BYTES + 1, unused_bytes=0).budget_bounded is False
    assert RecallBudgetView(max_bytes=8000, dropped_items=1, unused_bytes=0).budget_bounded is True
    assert RecallBudgetView(max_bytes=8000, dropped_items=1, unused_bytes=1).budget_bounded is False
    assert RecallBudgetView(max_bytes=8000, truncated_items=1, dropped_items=0, unused_bytes=0).budget_bounded is True


def test_expected_families_include_retrieved_candidates_already_admitted_at_round_zero() -> None:
    admissions = (
        AdmissionCounts(family="memory", scope_id="scope-a", retrieved=4, admitted=4),
        AdmissionCounts(family="experience", scope_id="scope-a", retrieved=1, admitted=0),
    )

    assert _families_with_retrieved_candidates({"memory", "experience"}, admissions) == 2


def test_recoverable_families_exclude_fully_admitted_round_zero_candidates() -> None:
    admissions = (
        AdmissionCounts(family="memory", scope_id="scope-a", retrieved=4, admitted=4),
        AdmissionCounts(family="experience", scope_id="scope-a", retrieved=1, admitted=0),
    )

    assert _families_with_recoverable_candidates({"memory", "experience"}, admissions) == 1


def test_recoverable_families_use_explicit_admission_rejections_when_counts_are_not_comparable() -> None:
    admissions = (
        AdmissionCounts(family="topic-memory", scope_id="scope-a", retrieved=40, admitted=32, rejected=0),
        AdmissionCounts(family="experience", scope_id="scope-a", retrieved=1, admitted=0, rejected=1),
    )

    assert _families_with_recoverable_candidates({"topic-memory", "experience"}, admissions) == 1


def test_gate_reads_the_budget_view_and_ignores_a_missing_probe() -> None:
    policy = RecallSufficiencyPolicy()
    gate = RecallSufficiencyGate()
    candidates = (_memory_candidate("alpha beta"),)

    assert gate.assess(candidates, "alpha beta", policy, budget=_budget_bound_view()).reason == REASON_BUDGET_FLOOR
    open_policy = RecallSufficiencyPolicy(min_candidates=1, min_top_score=0.0, min_top_gap=0.0)
    assert gate.assess(candidates, "alpha beta", open_policy, budget=None).reason == REASON_SUFFICIENT
