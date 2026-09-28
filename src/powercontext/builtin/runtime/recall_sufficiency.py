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

"""Model-free recall-sufficiency gate and its bounded expansion policy.

Everything in this module is pure: the gate, the expander, the candidate projection and the
identity function read only their arguments. They touch no database, no clock, no model and no
network, so they are tested directly without a Runtime. The Runtime layer owns the search loop
and feeds the accumulated candidates back into :meth:`RecallSufficiencyGate.assess`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from powercontext.builtin.artifacts.experience import ExperienceSearchHit, experience_search_text
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.artifacts.search import (
    AdmissionCounts,
    AdmissionFloor,
    analyze_text,
    fts_query_requirements,
)
from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchHit

if TYPE_CHECKING:
    from powercontext.builtin.runtime.config import RuntimeConfig
    from powercontext.builtin.runtime.prepared_context import PreparedContextOmissions

# ── Policy identifier and reason vocabulary ────────────────────────────────────────────────
POLICY_ID = "powercontext.recall-gate.v1"

REASON_SUFFICIENT = "sufficient"
REASON_NO_CONTENT = "no-content"
REASON_BUDGET_FLOOR = "budget-floor"
REASON_RERANK_ENABLED = "rerank-enabled"
REASON_THIN_CANDIDATES = "thin-candidates"
REASON_THIN_FAMILIES = "thin-families"
REASON_WEAK_TOP_ONE = "weak-top-1"
REASON_WEAK_LEXICAL = "weak-lexical"
REASON_AT_MAX_ROUNDS = "at-max-rounds"
REASON_EXPANSION_FAILED = "expansion-failed"

ACTION_ADMISSION = "admission"
ACTION_POLICY_FLOOR = "policy-floor"

MEMORY_FAMILY = "memory"
TOPIC_MEMORY_FAMILY = "topic-memory"
EXPERIENCE_FAMILY = "experience"
_SCORING_FAMILIES: tuple[str, ...] = (MEMORY_FAMILY, TOPIC_MEMORY_FAMILY)

# The byte budget floor declared by the request contract; a thin result at the floor is a
# budget property rather than a recall property.
BUDGET_FLOOR_BYTES = 512

# Mirrors ``memory/fusion.py``: the reciprocal-rank constant used to derive the analytic upper
# bound of a Memory RRF score.
_RRF_CONSTANT = 60
# Topic Memory relevance is already normalized against its reachable upper bound.
_TOPIC_SCORE_SCALE = 100.0


@dataclass(frozen=True)
class RecallCandidate:
    """One family-local retrieval result, projected for model-free assessment.

    ``score`` is a family-local relevance normalized into ``[0.0, 1.0]`` for the families that
    expose a real score. Families that expose only presence/counts (Experience) carry ``0.0``
    and never take part in the score signals — inventing a confidence for them would make the
    gate less honest, not more.
    """

    family: str
    artifact_id: str
    revision: int
    entry_id: str | None
    entry_version_id: str | None
    score: float
    text: str


@dataclass(frozen=True)
class RecallSignals:
    """Cheap, model-free signals derived from the accumulated candidate set.

    Score caveat (a known limitation, not a polished story): Memory's fused score is a
    reciprocal-rank score, so its discrimination is compressed by rank. The normalized Memory
    score saturates near ``1.0`` for a single strong channel hit, which is why the real
    load-bearing signals in v1 are ``candidate_count``, family coverage and ``lexical_overlap``;
    the score signal is driven mainly by Topic Memory. ``lexical_overlap`` is the query-term
    recall of the single best candidate (its overlap is the maximum over candidates), not the
    share of candidates sharing at least one term.

    ``distinct_source_count`` counts family-specific evidence identities via
    :func:`candidate_identity` — a Memory entry (``memory_ref`` + ``entry_id`` +
    ``entry_version_id``) or another family's Artifact revision. It is recorded for observation
    only; no branch of the v1 verdict reads it.

    ``families_expected`` is the number of caller-selected families where round zero retrieved
    candidates before admission. Configured callbacks with no retrieved rows are therefore not
    treated as thin recall, because there is no family evidence to cover.
    """

    candidate_count: int
    family_count: int
    distinct_source_count: int
    top_score: float
    mean_score: float
    top_gap: float
    lexical_overlap: float
    families_expected: int = 0
    scored_families: int = 0


@dataclass(frozen=True)
class GateAssessment:
    """One global verdict. ``sufficient=True`` means "do not expand"."""

    sufficient: bool
    reason: str
    signals: RecallSignals


@dataclass(frozen=True)
class SearchPlan:
    """Describes the next round.

    It never names a family and never sets ``limit``, ``mode`` or a rerank candidate bound.
    The last point is deliberate and load-bearing: ``MemoryService`` uses
    ``memory_rerank_candidate_limit`` to *size the backend request*
    (``coarse_limit`` → ``candidate_limit = max(coarse_limit * 4, 32)``), so raising it from
    30 to 100 would grow the backend pool from 120 to 400 candidates and break the same-pool
    guarantee the cost model rests on. RFC 1560 rejects it as an expansion action outright.
    """

    action: str
    admission: AdmissionFloor


@dataclass(frozen=True)
class ExpansionDecision:
    """The expander's request to (or refusal to) run another round."""

    expand: bool
    reason: str
    plan: SearchPlan | None


@dataclass(frozen=True)
class RecallSufficiencyPolicy:
    """A frozen, versioned bundle of thresholds and per-round expansion descriptors.

    Only the *expansion* rounds are tunable. Round 0 passes ``admission=None``, which already
    means "use this module's historical constants" and is therefore bit-identical to a
    disabled lookup; a separate ``base_admission`` field would only duplicate that default.

    ``rerank_enabled`` is read from Runtime configuration, not from whether a reranker
    instance exists, because the gate must decide *before* searching whether expansion is
    allowed at all. If configuration and reality ever diverge, the cost column stays honest
    separately: ``added_generation_calls`` is filled from the search-reported rerank counts.
    """

    policy_id: str = POLICY_ID
    max_rounds: int = 2
    round1_admission: AdmissionFloor = field(
        default_factory=lambda: AdmissionFloor(
            lexical_coverage=0.0, lexical_min_matched_terms=1, min_semantic_similarity=0.15
        )
    )
    round2_admission: AdmissionFloor = field(
        default_factory=lambda: AdmissionFloor(
            lexical_coverage=0.0, lexical_min_matched_terms=1, min_semantic_similarity=0.10
        )
    )
    min_candidates: int = 2
    min_top_score: float = 0.35
    min_top_gap: float = 0.02
    min_lexical_overlap: float = 0.5
    rerank_enabled: bool = False
    allow_expansion_with_rerank: bool = False

    @classmethod
    def from_runtime_config(cls, runtime_config: RuntimeConfig) -> RecallSufficiencyPolicy | None:
        """Build the policy, or return ``None`` when the feature is disabled."""

        if not runtime_config.recall_gate_enabled:
            return None
        return cls(
            max_rounds=runtime_config.recall_gate_max_rounds,
            round1_admission=AdmissionFloor(
                0.0,
                1,
                runtime_config.recall_gate_round1_min_semantic_similarity,
            ),
            round2_admission=AdmissionFloor(
                0.0,
                1,
                runtime_config.recall_gate_round2_min_semantic_similarity,
            ),
            min_candidates=runtime_config.recall_gate_min_candidates,
            min_top_score=runtime_config.recall_gate_min_top_score,
            min_top_gap=runtime_config.recall_gate_min_top_gap,
            min_lexical_overlap=runtime_config.recall_gate_min_lexical_overlap,
            rerank_enabled=runtime_config.memory_rerank_enabled,
            allow_expansion_with_rerank=runtime_config.recall_gate_allow_with_rerank,
        )


@dataclass(frozen=True)
class RecallBudgetView:
    """The byte-budget half of one round's assessment.

    Produced by :meth:`PreparedContextBuilder.probe_budget`: one pass of the Builder's own pure
    fitting code over the round's candidate set, rendered and then discarded. It exists so the
    gate can tell *budget-limited* thinness from *recall-limited* thinness — without it the
    512-byte-floor edge case is undecidable, and the gate would expand a query whose real
    constraint is the output budget.

    ``unused_bytes`` is the headroom the fit left inside ``max_bytes``. Every field is an
    aggregate: no query text, no entry identity, no per-entry attribution.
    """

    max_bytes: int = 0
    delivered_items: int = 0
    truncated_items: int = 0
    dropped_items: int = 0
    unused_bytes: int = 0

    @property
    def budget_bounded(self) -> bool:
        """Whether the request budget, not recall, is what limits delivery.

        The gate treats budget as binding only when the Builder observed fitting pressure:
        delivered content consumed all headroom, an item was truncated, or a whole item was
        dropped. A bare 512-byte request with no candidates is not budget-bound, because recall
        could still recover a short item that fits.
        """

        fitting_pressure = (
            self.dropped_items > 0 or self.truncated_items > 0 or (self.delivered_items > 0 and self.unused_bytes <= 0)
        )
        if self.max_bytes <= BUDGET_FLOOR_BYTES:
            return fitting_pressure
        return self.unused_bytes <= 0 and (self.dropped_items > 0 or self.truncated_items > 0)


@dataclass(frozen=True)
class RecallEffort:
    """In-process trace of the recall loop; never persisted and never added to the HTTP body.

    The trace is delivered to the Runtime's optional ``RecallEffortSink`` and to nothing else.
    It is **not** attached to ``PreparedContextBuild``: ``_prepare`` returns ``build.context``
    and discards the rest of the build result, so a field there would have no production
    observer.

    The fields are deliberately coarse — aggregate counts, one reason code, and per-family
    admission totals. There is no query text, no entry id and no per-entry attribution, so the
    value cannot leak evidence through a trace.

    ``rounds`` counts the search passes actually executed, so it is ``1 + len(expansion_actions)``
    (1..3): round 0 always runs and each *committed* expansion adds one. ``candidates_by_round``
    holds the accumulated candidate-pool size the gate saw after each committed round, so
    ``len(candidates_by_round) == rounds``; it measures the **un-truncated** accumulated pool
    the gate assessed, not the subset the Builder finally selected (the Builder is called once,
    after the loop, and applies the family ceilings there). ``expansion_actions`` is a prefix of
    ``("admission", "policy-floor")``.

    ``admission_by_family`` is measured in the **last committed round** (round 0 is always
    committed), which is the same committed-only basis as ``candidates_by_round`` and
    ``expansion_actions``.

    ``added_embeddings`` and ``added_generation_calls`` are **search-reported**: the Runtime
    copies what the searches said they paid. It never infers, interpolates or estimates them —
    a fabricated cost is worse than a missing one, so when a family cannot report, the count
    stays at its default of ``0``.

    ``dropped_items`` equals ``dropped_below_min_bytes + dropped_no_fitting_truncation``, so
    "the budget could not fit this item" can be told apart from "this item was too short to
    truncate into the remaining space".
    """

    policy: str
    assessment: str
    rounds: int
    expansion_actions: tuple[str, ...]
    candidates_by_round: tuple[int, ...]
    admission_by_family: tuple[AdmissionCounts, ...] = ()
    added_embeddings: int = 0
    added_generation_calls: int = 0
    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


def recall_effort(
    *,
    policy: RecallSufficiencyPolicy,
    assessment: str,
    expansion_actions: Sequence[str] = (),
    candidates_by_round: Sequence[int] = (),
    admission_by_family: Sequence[AdmissionCounts] = (),
    added_embeddings: int = 0,
    added_generation_calls: int = 0,
    omissions: PreparedContextOmissions | None = None,
) -> RecallEffort:
    """Assemble one :class:`RecallEffort`, folding a build's omission counts into it.

    The Builder already counts truncations and drops for its own callers; this is the single
    place those counts are copied onto the trace, so the two can never disagree about what
    "dropped" means. The caller supplies the round bookkeeping; ``rounds`` is derived from
    ``expansion_actions`` so the two can never drift apart.
    """

    actions = tuple(expansion_actions)
    return RecallEffort(
        policy=policy.policy_id,
        assessment=assessment,
        rounds=1 + len(actions),
        expansion_actions=actions,
        candidates_by_round=tuple(candidates_by_round),
        admission_by_family=tuple(admission_by_family),
        added_embeddings=added_embeddings,
        added_generation_calls=added_generation_calls,
        truncated_items=0 if omissions is None else omissions.truncated_items,
        dropped_items=0 if omissions is None else omissions.dropped_items,
        dropped_below_min_bytes=0 if omissions is None else omissions.dropped_below_min_bytes,
        dropped_no_fitting_truncation=0 if omissions is None else omissions.dropped_no_fitting_truncation,
    )


def candidate_identity(candidate: RecallCandidate, /) -> tuple[str, str, int, str | None, str | None]:
    """Return the Builder's origin identity for one candidate."""

    return (
        candidate.family,
        candidate.artifact_id,
        candidate.revision,
        candidate.entry_id,
        candidate.entry_version_id,
    )


def build_recall_candidates(
    *,
    memory_hits: Sequence[MemoryHit],
    topic_memory_hits: Sequence[TopicMemorySearchHit],
    experience_hits: Sequence[ExperienceSearchHit],
) -> tuple[RecallCandidate, ...]:
    """Project the participating families' hits into a flat, ordered candidate tuple."""

    candidates: list[RecallCandidate] = []
    for hit in memory_hits:
        candidates.append(
            RecallCandidate(
                family=MEMORY_FAMILY,
                artifact_id=hit.memory_ref.artifact_id,
                revision=hit.memory_ref.revision,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
                score=_normalize_memory_score(hit),
                text=hit.text,
            )
        )
    for topic_hit in topic_memory_hits:
        candidates.append(
            RecallCandidate(
                family=TOPIC_MEMORY_FAMILY,
                artifact_id=topic_hit.artifact_ref.artifact_id,
                revision=topic_hit.artifact_ref.revision,
                entry_id=None,
                entry_version_id=None,
                score=_normalize_topic_score(topic_hit),
                text="\n".join(part for part in (topic_hit.title, topic_hit.summary, topic_hit.snippet) if part),
            )
        )
    for experience_hit in experience_hits:
        candidates.append(
            RecallCandidate(
                family=EXPERIENCE_FAMILY,
                artifact_id=experience_hit.artifact_ref.artifact_id,
                revision=experience_hit.artifact_ref.revision,
                entry_id=None,
                entry_version_id=None,
                score=0.0,
                text=experience_search_text(experience_hit.content),
            )
        )
    return tuple(candidates)


def _normalize_memory_score(hit: MemoryHit) -> float:
    """Normalize an RRF score against its analytic per-channel-count upper bound."""

    channels = max(1, len(hit.matched_by))
    upper_bound = channels / (_RRF_CONSTANT + 1)
    if upper_bound <= 0.0:
        return 0.0
    return max(0.0, min(1.0, hit.score / upper_bound))


def _normalize_topic_score(hit: TopicMemorySearchHit) -> float:
    """Normalize Topic Memory relevance into ``[0.0, 1.0]``."""

    return max(0.0, min(1.0, hit.score / _TOPIC_SCORE_SCALE))


class RecallSufficiencyGate:
    """Pure, deterministic sufficiency assessment over accumulated candidates."""

    def assess(
        self,
        candidates: Sequence[RecallCandidate],
        query: str,
        policy: RecallSufficiencyPolicy,
        /,
        *,
        scope_has_content: bool = True,
        budget: RecallBudgetView | None = None,
        families_expected: int = 0,
    ) -> GateAssessment:
        """Assess one candidate set. No I/O, no clock, no model; deterministic in its inputs.

        ``budget`` carries the result of the round's budget probe, or ``None`` when no probe
        was run. The gate does not perform the probe itself; the Runtime does it once, on
        round 0, and reuses the view for every round.
        """

        signals = _build_signals(candidates, query, families_expected)
        if not scope_has_content:
            return GateAssessment(sufficient=True, reason=REASON_NO_CONTENT, signals=signals)
        if budget is not None and budget.budget_bounded:
            return GateAssessment(sufficient=True, reason=REASON_BUDGET_FLOOR, signals=signals)
        if policy.rerank_enabled and not policy.allow_expansion_with_rerank:
            return GateAssessment(sufficient=True, reason=REASON_RERANK_ENABLED, signals=signals)
        if signals.candidate_count < policy.min_candidates:
            return GateAssessment(sufficient=False, reason=REASON_THIN_CANDIDATES, signals=signals)
        if families_expected > 1 and signals.family_count < families_expected:
            return GateAssessment(sufficient=False, reason=REASON_THIN_FAMILIES, signals=signals)
        if signals.scored_families > 0 and (
            signals.top_score < policy.min_top_score or signals.top_gap < policy.min_top_gap
        ):
            return GateAssessment(sufficient=False, reason=REASON_WEAK_TOP_ONE, signals=signals)
        if signals.lexical_overlap < policy.min_lexical_overlap:
            return GateAssessment(sufficient=False, reason=REASON_WEAK_LEXICAL, signals=signals)
        return GateAssessment(sufficient=True, reason=REASON_SUFFICIENT, signals=signals)


class RecallExpander:
    """Pure mapping from an expansion round to the plan that describes it."""

    def plan(self, round_number: int, policy: RecallSufficiencyPolicy, /) -> SearchPlan:
        """Return the plan for round 1 or round 2; raise for any other round."""

        if round_number == 1:
            return SearchPlan(action=ACTION_ADMISSION, admission=policy.round1_admission)
        if round_number == 2:
            return SearchPlan(action=ACTION_POLICY_FLOOR, admission=policy.round2_admission)
        raise ValueError("recall expansion round must be 1 or 2")  # noqa: TRY003


def _build_signals(
    candidates: Sequence[RecallCandidate],
    query: str,
    families_expected: int,
) -> RecallSignals:
    candidate_count = len(candidates)
    families_with_candidates = len({candidate.family for candidate in candidates})
    distinct_source_count = len({candidate_identity(candidate) for candidate in candidates})
    lexical_overlap = _lexical_overlap(candidates, query)
    top_score, mean_score, top_gap, scored_families = _score_signals(candidates)
    return RecallSignals(
        candidate_count=candidate_count,
        family_count=families_with_candidates,
        distinct_source_count=distinct_source_count,
        top_score=top_score,
        mean_score=mean_score,
        top_gap=top_gap,
        lexical_overlap=lexical_overlap,
        families_expected=families_expected,
        scored_families=scored_families,
    )


def _lexical_overlap(candidates: Sequence[RecallCandidate], query: str) -> float:
    """Return the best candidate's query-term recall in ``[0.0, 1.0]``.

    This is the maximum over candidates of ``|query_terms ∩ candidate_terms| / |query_terms|``.
    A query with no Analyzer terms cannot be measured, so it is treated as fully covered.
    """

    query_terms = set(fts_query_requirements(query)[0])
    if not query_terms:
        return 1.0
    best = 0.0
    for candidate in candidates:
        candidate_terms = set(analyze_text(candidate.text).split())
        if not candidate_terms:
            continue
        best = max(best, len(query_terms.intersection(candidate_terms)) / len(query_terms))
    return best


def _score_signals(candidates: Sequence[RecallCandidate]) -> tuple[float, float, float, int]:
    """Aggregate score signals over the families that expose a real relevance score.

    Returns ``(top_score, mean_score, top_gap, scored_families)``. ``top_score`` is the maximum
    family top and ``top_gap`` the maximum family-local ``top - mean``. ``mean_score`` is the mean
    of all scored candidates pooled across scored families — a different basis from the
    family-local ``top_gap`` — and is reported **for observation only; it never takes part in the
    verdict**. When no family exposes a score the caller must skip the score signal entirely
    (``scored_families == 0``), so the aggregate values here stay ``0.0`` rather than fabricated.
    """

    top_score = 0.0
    top_gap = 0.0
    scored_families = 0
    all_scores: list[float] = []
    for family in _SCORING_FAMILIES:
        family_scores = [candidate.score for candidate in candidates if candidate.family == family]
        if not family_scores:
            continue
        scored_families += 1
        family_top = max(family_scores)
        family_mean = sum(family_scores) / len(family_scores)
        top_score = max(top_score, family_top)
        top_gap = max(top_gap, family_top - family_mean)
        all_scores.extend(family_scores)
    mean_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    return top_score, mean_score, top_gap, scored_families


__all__ = [
    "ACTION_ADMISSION",
    "ACTION_POLICY_FLOOR",
    "BUDGET_FLOOR_BYTES",
    "EXPERIENCE_FAMILY",
    "MEMORY_FAMILY",
    "POLICY_ID",
    "REASON_AT_MAX_ROUNDS",
    "REASON_BUDGET_FLOOR",
    "REASON_EXPANSION_FAILED",
    "REASON_NO_CONTENT",
    "REASON_RERANK_ENABLED",
    "REASON_SUFFICIENT",
    "REASON_THIN_CANDIDATES",
    "REASON_THIN_FAMILIES",
    "REASON_WEAK_LEXICAL",
    "REASON_WEAK_TOP_ONE",
    "TOPIC_MEMORY_FAMILY",
    "ExpansionDecision",
    "GateAssessment",
    "RecallBudgetView",
    "RecallCandidate",
    "RecallEffort",
    "RecallExpander",
    "RecallSignals",
    "RecallSufficiencyGate",
    "RecallSufficiencyPolicy",
    "SearchPlan",
    "build_recall_candidates",
    "candidate_identity",
    "recall_effort",
]
