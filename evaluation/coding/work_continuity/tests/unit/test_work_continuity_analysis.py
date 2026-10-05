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

"""Failure classification and the host-scoped comparison.

Failure analysis is what closes the loop from a score back to a requirement, so
these tests check both halves: that each failure class is reached from a real
assembled context and recorded attempt, and that the treatment is only ever
compared against baselines measured on the same host.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from powercontext_eval_work_continuity.analysis import (
    BUDGET_TRUNCATION,
    CONTEXT_ABSENT,
    FAILURE_REQUIREMENTS,
    MISSING_EVIDENCE,
    NO_RECORDING,
    STALE_STATE,
    UNVERIFIABLE_CLAIM,
    VAGUE_NEXT_ACTION,
    ArmOutcome,
    analyse_task_outcomes,
    analyse_work_continuity,
    classify_failure,
    facts_lost_to_the_budget,
)
from powercontext_eval_work_continuity.arms import (
    COMPACTED_TRANSCRIPT,
    FULL_TRANSCRIPT,
    INFORMAL_SUMMARY,
    ROLLOVER_HANDOFF,
    TREATMENT_ARM_ID,
    ContinuationArm,
)
from powercontext_eval_work_continuity.assembly import ContinuationContext, assemble_context
from powercontext_eval_work_continuity.attempts import RecordedAttempt, RecordedStep
from powercontext_eval_work_continuity.catalog import ContinuationTask, TaskCatalog
from powercontext_eval_work_continuity.quality import (
    EVIDENCE_REQUIREMENT,
    NEXT_ACTION_REQUIREMENT,
    OMISSIONS_REQUIREMENT,
    STATE_REQUIREMENT,
)
from powercontext_eval_work_continuity.rubric import AttemptScore, score_attempt

from .work_continuity_fixtures import UNBOUND_CONTEXT_SHA256, analysis_lock, budget_through

GENEROUS_BUDGET = 16_000
HOST_A = "host-a"
HOST_B = "host-b"


@pytest.fixture
def catalog(tmp_path: Path) -> TaskCatalog:
    return TaskCatalog.load(analysis_lock(tmp_path))


def context_for(
    catalog: TaskCatalog, task_id: str, arm: ContinuationArm, *, max_bytes: int = GENEROUS_BUDGET
) -> ContinuationContext:
    return assemble_context(catalog.require(task_id), arm, max_bytes=max_bytes)


def recorded(
    *steps: dict[str, Any],
    task_id: str = "t-audit",
    arm_id: str = "full-transcript-v1",
    host: str = HOST_A,
) -> RecordedAttempt:
    return RecordedAttempt(
        task_id=task_id,
        arm_id=arm_id,
        host=host,
        host_revision="declared-host@1",
        model="declared-model",
        # Binding a recording to its delivered context is enforced by the run, not
        # by scoring, so these tests are free to score against any context.
        context_sha256=UNBOUND_CONTEXT_SHA256,
        steps=tuple(RecordedStep(**step) for step in steps),
    )


def step(
    number: int,
    relied_on: list[str],
    *,
    performed: str | None = None,
    correction: bool = False,
) -> dict[str, Any]:
    """One recorded step. ``performed`` names the declared action the step carried out."""

    return {
        "step": number,
        "action_text": f"action {number}",
        "relied_on": relied_on,
        "performed_action_id": performed,
        "correction": correction,
    }


def fabricated_score(
    task: ContinuationTask,
    context: ContinuationContext,
    *,
    host: str = HOST_A,
    success: bool,
    recovery: int | None,
) -> AttemptScore:
    """Build a score with no steps, for comparisons that only read the ranking."""

    return AttemptScore(
        task_id=task.task_id,
        arm_id=context.arm_id,
        host=host,
        task_success=success,
        time_to_recover_state=recovery,
        incorrect_assumptions=0,
        missing_evidence=0,
        unverifiable_claims=0,
        user_correction_burden=0,
        injected_bytes=context.injected_bytes,
        max_bytes=context.max_bytes,
        context_truncated=context.truncated,
        delivered_fact_ids=context.delivered_fact_ids,
        expected_fact_ids=task.expected_next_action.required_fact_ids,
        steps=(),
    )


def test_a_clean_recovery_is_not_a_finding(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)
    score = score_attempt(task, context, recorded(step(1, ["h1", "h2"], performed="c1")))

    assert score.task_success is True
    assert classify_failure(task, context, score) is None


def test_an_absent_recording_is_its_own_class_and_asks_for_a_recording_not_a_contract_change(
    catalog: TaskCatalog,
) -> None:
    task = catalog.require("t-audit")
    context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)

    assert classify_failure(task, context, None) == NO_RECORDING

    analysis = analyse_task_outcomes(
        task, [ArmOutcome(task_id="t-audit", arm_id=FULL_TRANSCRIPT.arm_id, host=HOST_A, context=context, score=None)]
    )

    assert analysis.findings[0].requirement is None
    assert analysis.findings[0].is_contract_change is False
    assert "no attempt recorded" in analysis.findings[0].detail


def test_a_dropped_required_state_item_is_budget_truncation(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    ceiling = budget_through(task, ROLLOVER_HANDOFF, "state:h1")
    context = context_for(catalog, "t-audit", ROLLOVER_HANDOFF, max_bytes=ceiling)
    score = score_attempt(task, context, recorded(step(1, ["h1"]), arm_id=ROLLOVER_HANDOFF.arm_id))

    assert "state:h2" in context.dropped_item_ids
    assert classify_failure(task, context, score) == BUDGET_TRUNCATION


def test_dropped_transcript_evidence_is_budget_truncation(catalog: TaskCatalog) -> None:
    """A transcript arm's dropped turn is a dropped fact, not an absent context."""

    task = catalog.require("t-audit")
    ceiling = budget_through(task, FULL_TRANSCRIPT, "turn:2")
    context = context_for(catalog, "t-audit", FULL_TRANSCRIPT, max_bytes=ceiling)
    score = score_attempt(task, context, recorded(step(1, ["h1"])))

    # Every fact the next action needs had its evidence in a dropped turn, so the
    # loss belongs to the byte ceiling rather than to the window the arm selected.
    assert "turn:7" in context.dropped_item_ids
    assert "turn:8" in context.dropped_item_ids
    assert classify_failure(task, context, score) == BUDGET_TRUNCATION

    analysis = analyse_task_outcomes(
        task,
        [ArmOutcome(task.task_id, FULL_TRANSCRIPT.arm_id, HOST_A, context, score)],
    )
    assert "turn" in analysis.findings[0].detail


def test_required_state_never_delivered_is_context_absent(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    context = context_for(catalog, "t-audit", INFORMAL_SUMMARY)
    score = score_attempt(task, context, recorded(step(1, ["h2"]), arm_id=INFORMAL_SUMMARY.arm_id))

    assert "h1" not in context.delivered_fact_ids
    assert classify_failure(task, context, score) == CONTEXT_ABSENT


def test_continuing_from_a_superseded_fact_is_stale_state(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)
    score = score_attempt(task, context, recorded(step(1, ["o1", "h1", "h2"], performed="c1")))

    assert score.incorrect_assumptions == 1
    assert classify_failure(task, context, score) == STALE_STATE


def test_relying_on_unavailable_evidence_is_an_unverifiable_claim(catalog: TaskCatalog) -> None:
    task = catalog.require("t-doc")
    context = context_for(catalog, "t-doc", ROLLOVER_HANDOFF)
    score = score_attempt(
        task,
        context,
        recorded(
            step(1, ["g3"]),
            step(2, ["g1", "g2"], performed="b1"),
            task_id="t-doc",
            arm_id=ROLLOVER_HANDOFF.arm_id,
        ),
    )

    assert score.unverifiable_claims == 1
    assert classify_failure(task, context, score) == UNVERIFIABLE_CLAIM


def test_relying_on_a_fact_the_context_dropped_is_missing_evidence(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    context = context_for(catalog, "t-audit", COMPACTED_TRANSCRIPT)
    score = score_attempt(
        task,
        context,
        recorded(
            step(1, ["h1", "h2"], performed="c1"),
            step(2, ["h3"]),
            arm_id=COMPACTED_TRANSCRIPT.arm_id,
        ),
    )

    assert score.task_success is True
    assert score.missing_evidence == 1
    # The action's own facts were delivered, so this is not an assembly gap.
    assert score.has_assembly_gap is False
    assert classify_failure(task, context, score) == MISSING_EVIDENCE


def test_delivering_every_fact_without_recovery_is_a_vague_next_action(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)
    score = score_attempt(task, context, recorded(step(1, ["h1", "h2"])))

    assert score.task_success is False
    assert (score.incorrect_assumptions, score.missing_evidence, score.unverifiable_claims) == (0, 0, 0)
    assert classify_failure(task, context, score) == VAGUE_NEXT_ACTION


def test_a_ceiling_that_drops_only_the_next_action_is_budget_truncation(catalog: TaskCatalog) -> None:
    """A ceiling that stops before the action removes the action, not its facts.

    Every fact the next action depends on survives this ceiling, so a classifier
    that only subtracts dropped facts from the needed set finds nothing missing and
    reports a vague action — recommending a contract change for a loss the byte
    ceiling caused, while the quality table already flags the missing next action.
    """

    task = catalog.require("t-audit")
    ceiling = budget_through(task, ROLLOVER_HANDOFF, "label:next_action")
    context = context_for(catalog, "t-audit", ROLLOVER_HANDOFF, max_bytes=ceiling)
    score = score_attempt(task, context, recorded(step(1, ["h1", "h2"]), arm_id=ROLLOVER_HANDOFF.arm_id))

    assert "next_action" in context.dropped_item_ids
    assert context.next_action_lost_to_the_budget is True
    assert context.carries_next_action is False
    # Nothing the action depends on was lost, which is exactly why the fact-only
    # reading of the budget fell through to the vague-action fallback.
    assert context.delivered_fact_ids == ("h1", "h2", "h3")
    assert facts_lost_to_the_budget(task, context) == set()
    assert context.quality is not None
    assert NEXT_ACTION_REQUIREMENT in context.quality.violations_by_requirement
    assert classify_failure(task, context, score) == BUDGET_TRUNCATION

    analysis = analyse_task_outcomes(
        task,
        [ArmOutcome(task.task_id, ROLLOVER_HANDOFF.arm_id, HOST_A, context, score)],
    )
    assert "the next action itself" in analysis.findings[0].detail


def test_a_ceiling_that_keeps_the_next_action_leaves_the_outcome_to_the_session(catalog: TaskCatalog) -> None:
    """The added carrier must not swallow failures the ceiling did not cause.

    The same context one byte above the cut keeps the next action, so the session
    that read every fact and still did not continue is a vague action again.
    """

    task = catalog.require("t-audit")
    ceiling = budget_through(task, ROLLOVER_HANDOFF, "next_action")
    context = context_for(catalog, "t-audit", ROLLOVER_HANDOFF, max_bytes=ceiling)
    score = score_attempt(task, context, recorded(step(1, ["h1", "h2"]), arm_id=ROLLOVER_HANDOFF.arm_id))

    assert context.next_action_lost_to_the_budget is False
    assert context.carries_next_action is True
    assert classify_failure(task, context, score) == VAGUE_NEXT_ACTION


@pytest.mark.parametrize(
    ("failure_class", "requirement"),
    [
        (BUDGET_TRUNCATION, STATE_REQUIREMENT),
        (CONTEXT_ABSENT, STATE_REQUIREMENT),
        (STALE_STATE, STATE_REQUIREMENT),
        (MISSING_EVIDENCE, EVIDENCE_REQUIREMENT),
        (UNVERIFIABLE_CLAIM, OMISSIONS_REQUIREMENT),
        (VAGUE_NEXT_ACTION, NEXT_ACTION_REQUIREMENT),
    ],
)
def test_every_failure_class_names_the_requirement_it_argues_about(failure_class: str, requirement: str) -> None:
    assert FAILURE_REQUIREMENTS[failure_class] == requirement
    assert FAILURE_REQUIREMENTS[NO_RECORDING] is None


def test_a_finding_carries_a_recommendation_and_a_detail(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)
    score = score_attempt(task, context, recorded(step(1, ["o1"]), step(2, ["h1", "h2"])))

    analysis = analyse_task_outcomes(
        task,
        [ArmOutcome(task_id="t-audit", arm_id=FULL_TRANSCRIPT.arm_id, host=HOST_A, context=context, score=score)],
    )
    finding = analysis.findings[0]

    assert finding.failure_class == STALE_STATE
    assert "o1" in finding.detail
    assert finding.recommendation
    assert finding.is_contract_change is True


def test_the_treatment_is_only_compared_against_baselines_on_the_same_host(catalog: TaskCatalog) -> None:
    """A baseline measured on one host is not evidence about the treatment on another."""

    task = catalog.require("t-audit")
    treatment_context = context_for(catalog, "t-audit", ROLLOVER_HANDOFF)
    baseline_context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)

    outcomes = [
        # On host-a the treatment never recovered while a baseline did.
        ArmOutcome(
            task_id="t-audit",
            arm_id=ROLLOVER_HANDOFF.arm_id,
            host=HOST_A,
            context=treatment_context,
            score=fabricated_score(task, treatment_context, host=HOST_A, success=False, recovery=None),
        ),
        ArmOutcome(
            task_id="t-audit",
            arm_id=FULL_TRANSCRIPT.arm_id,
            host=HOST_A,
            context=baseline_context,
            score=fabricated_score(task, baseline_context, host=HOST_A, success=True, recovery=1),
        ),
        # On host-b the treatment recovered first, so no baseline beat it there.
        ArmOutcome(
            task_id="t-audit",
            arm_id=ROLLOVER_HANDOFF.arm_id,
            host=HOST_B,
            context=treatment_context,
            score=fabricated_score(task, treatment_context, host=HOST_B, success=True, recovery=1),
        ),
        ArmOutcome(
            task_id="t-audit",
            arm_id=FULL_TRANSCRIPT.arm_id,
            host=HOST_B,
            context=baseline_context,
            score=fabricated_score(task, baseline_context, host=HOST_B, success=True, recovery=3),
        ),
    ]

    analysis = analyse_task_outcomes(task, outcomes)

    assert analysis.hosts == (HOST_A, HOST_B)
    assert [outcome.host for outcome in analysis.underperforming_baselines] == [HOST_A]
    assert analysis.treatment_underperforms is True
    assert [entry.host for entry in analysis.underperformance] == [HOST_A]
    assert analysis.underperformance[0].beaten_by == (FULL_TRANSCRIPT.arm_id,)
    assert analysis.underperformance[0].treatment_failure_class == VAGUE_NEXT_ACTION
    assert analysis.underperformance[0].requirement == NEXT_ACTION_REQUIREMENT


def test_a_baseline_that_only_leads_on_another_host_is_not_an_underperformance(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    treatment_context = context_for(catalog, "t-audit", ROLLOVER_HANDOFF)
    baseline_context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)

    outcomes = [
        ArmOutcome(
            task_id="t-audit",
            arm_id=ROLLOVER_HANDOFF.arm_id,
            host=HOST_A,
            context=treatment_context,
            score=fabricated_score(task, treatment_context, host=HOST_A, success=True, recovery=1),
        ),
        ArmOutcome(
            task_id="t-audit",
            arm_id=FULL_TRANSCRIPT.arm_id,
            host=HOST_B,
            context=baseline_context,
            score=fabricated_score(task, baseline_context, host=HOST_B, success=True, recovery=1),
        ),
    ]

    analysis = analyse_task_outcomes(task, outcomes)

    assert analysis.underperforming_baselines == ()
    assert analysis.underperformance == ()
    assert analysis.treatment_underperforms is False


def test_an_unrecorded_arm_can_neither_win_nor_lose_a_comparison(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    treatment_context = context_for(catalog, "t-audit", ROLLOVER_HANDOFF)
    baseline_context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)

    baseline_wins = analyse_task_outcomes(
        task,
        [
            ArmOutcome("t-audit", ROLLOVER_HANDOFF.arm_id, HOST_A, treatment_context, None),
            ArmOutcome(
                "t-audit",
                FULL_TRANSCRIPT.arm_id,
                HOST_A,
                baseline_context,
                fabricated_score(task, baseline_context, host=HOST_A, success=True, recovery=1),
            ),
        ],
    )
    treatment_wins = analyse_task_outcomes(
        task,
        [
            ArmOutcome(
                "t-audit",
                ROLLOVER_HANDOFF.arm_id,
                HOST_A,
                treatment_context,
                fabricated_score(task, treatment_context, host=HOST_A, success=True, recovery=1),
            ),
            ArmOutcome("t-audit", FULL_TRANSCRIPT.arm_id, HOST_A, baseline_context, None),
        ],
    )

    assert baseline_wins.underperforming_baselines == ()
    assert treatment_wins.underperforming_baselines == ()


def test_the_aggregate_counts_findings_by_class_and_lists_unrecorded_combinations(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    full_context = context_for(catalog, "t-audit", FULL_TRANSCRIPT)
    informal_context = context_for(catalog, "t-audit", INFORMAL_SUMMARY)

    recorded_cleanly = analyse_task_outcomes(
        task,
        [
            ArmOutcome(
                "t-audit",
                FULL_TRANSCRIPT.arm_id,
                HOST_A,
                full_context,
                score_attempt(task, full_context, recorded(step(1, ["h1", "h2"], performed="c1"))),
            ),
            ArmOutcome(
                "t-audit",
                INFORMAL_SUMMARY.arm_id,
                HOST_A,
                informal_context,
                score_attempt(task, informal_context, recorded(step(1, ["h2"]), arm_id=INFORMAL_SUMMARY.arm_id)),
            ),
        ],
    )
    unrecorded = analyse_task_outcomes(
        task,
        [ArmOutcome("t-audit", ROLLOVER_HANDOFF.arm_id, HOST_A, full_context, None)],
    )

    analysis = analyse_work_continuity([recorded_cleanly, unrecorded])

    assert analysis.findings_by_class == {
        BUDGET_TRUNCATION: 0,
        CONTEXT_ABSENT: 1,
        STALE_STATE: 0,
        UNVERIFIABLE_CLAIM: 0,
        MISSING_EVIDENCE: 0,
        VAGUE_NEXT_ACTION: 0,
    }
    assert analysis.unrecorded_keys == (("t-audit", ROLLOVER_HANDOFF.arm_id, HOST_A),)
    # The treatment's own finding here is only a recording gap, which asks for
    # evidence rather than for a contract change.
    assert [finding.failure_class for finding in analysis.treatment_findings] == [NO_RECORDING]
    assert analysis.treatment_findings[0].requirement is None
    assert [finding.failure_class for finding in analysis.findings] == [CONTEXT_ABSENT, NO_RECORDING]


def test_treatment_findings_select_only_the_treatment_arm(catalog: TaskCatalog) -> None:
    task = catalog.require("t-audit")
    informal_context = context_for(catalog, "t-audit", INFORMAL_SUMMARY)
    treatment_context = context_for(catalog, "t-audit", ROLLOVER_HANDOFF)

    analysis = analyse_work_continuity(
        [
            analyse_task_outcomes(
                task,
                [
                    ArmOutcome(
                        "t-audit",
                        INFORMAL_SUMMARY.arm_id,
                        HOST_A,
                        informal_context,
                        score_attempt(
                            task, informal_context, recorded(step(1, ["h2"]), arm_id=INFORMAL_SUMMARY.arm_id)
                        ),
                    ),
                    ArmOutcome(
                        "t-audit",
                        TREATMENT_ARM_ID,
                        HOST_A,
                        treatment_context,
                        score_attempt(
                            task,
                            treatment_context,
                            recorded(step(1, ["h1"]), arm_id=TREATMENT_ARM_ID),
                        ),
                    ),
                ],
            )
        ]
    )

    assert [finding.arm_id for finding in analysis.treatment_findings] == [TREATMENT_ARM_ID]
    assert analysis.treatment_findings[0].failure_class == VAGUE_NEXT_ACTION
