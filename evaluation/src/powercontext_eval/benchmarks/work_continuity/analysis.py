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

"""Failure analysis for continuation methods, mapped back to the RFC contract.

Acceptance for the work-continuity benchmark is not a headline number. It is a
statement about which method failed on which task, why it failed, and which
requirement or assembly decision that failure argues against changing.

A failure is classified from the assembled context and the recorded outcome
only, never from a model judgement. Each class maps to one RFC 1783 requirement
or to one continuation-assembly decision, so the analysis closes the loop the
issue asks for instead of ending at a score.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from powercontext_eval.benchmarks.work_continuity.arms import TREATMENT_ARM_ID
from powercontext_eval.benchmarks.work_continuity.assembly import ContinuationContext
from powercontext_eval.benchmarks.work_continuity.catalog import ContinuationTask
from powercontext_eval.benchmarks.work_continuity.quality import (
    EVIDENCE_REQUIREMENT,
    NEXT_ACTION_REQUIREMENT,
    OMISSIONS_REQUIREMENT,
    STATE_REQUIREMENT,
)
from powercontext_eval.benchmarks.work_continuity.rubric import AttemptScore

BUDGET_TRUNCATION = "budget_truncation"
CONTEXT_ABSENT = "context_absent"
STALE_STATE = "stale_state"
MISSING_EVIDENCE = "missing_evidence"
UNVERIFIABLE_CLAIM = "unverifiable_claim"
VAGUE_NEXT_ACTION = "vague_next_action"
NO_RECORDING = "no_recording"

FAILURE_CLASSES: tuple[str, ...] = (
    BUDGET_TRUNCATION,
    CONTEXT_ABSENT,
    STALE_STATE,
    UNVERIFIABLE_CLAIM,
    MISSING_EVIDENCE,
    VAGUE_NEXT_ACTION,
)

# ``None`` means the finding asks for a recording rather than a contract change.
FAILURE_REQUIREMENTS: Mapping[str, str | None] = MappingProxyType(
    {
        BUDGET_TRUNCATION: STATE_REQUIREMENT,
        CONTEXT_ABSENT: STATE_REQUIREMENT,
        STALE_STATE: STATE_REQUIREMENT,
        MISSING_EVIDENCE: EVIDENCE_REQUIREMENT,
        UNVERIFIABLE_CLAIM: OMISSIONS_REQUIREMENT,
        VAGUE_NEXT_ACTION: NEXT_ACTION_REQUIREMENT,
        NO_RECORDING: None,
    }
)

FAILURE_RECOMMENDATIONS: Mapping[str, str] = MappingProxyType(
    {
        BUDGET_TRUNCATION: (
            "Order material the next action depends on ahead of droppable material, or raise the bounded "
            "continuation budget: the byte ceiling dropped a fact the next action needs."
        ),
        CONTEXT_ABSENT: (
            "Carry every fact the expected next action depends on, or widen the retained transcript window: "
            "the continuation context omitted required state."
        ),
        STALE_STATE: (
            "Exclude superseded state or mark it as superseded: the session continued from material the "
            "long session had already replaced."
        ),
        MISSING_EVIDENCE: (
            "Back every state statement the next action depends on with a captured pointer: the session "
            "relied on material the continuation context never delivered."
        ),
        UNVERIFIABLE_CLAIM: (
            "Record the unverifiable fact as an omission with its reason: its evidence cannot be produced, "
            "so it must not be presented as recoverable state."
        ),
        VAGUE_NEXT_ACTION: (
            "Make the next action concrete and singular: every fact the action depends on was delivered and "
            "the session still did not recover."
        ),
        NO_RECORDING: ("Record an attempt for this task and arm before drawing any conclusion from this comparison."),
    }
)


@dataclass(frozen=True)
class ArmOutcome:
    """One arm's assembled context and recorded result for one task.

    ``score`` is ``None`` when no attempt was recorded, so an absent recording is
    distinguishable from a recorded failure instead of being counted as one.
    """

    task_id: str
    arm_id: str
    host: str
    context: ContinuationContext
    score: AttemptScore | None

    @property
    def recorded(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class FailureFinding:
    """One classified failure with the decision it argues about."""

    task_id: str
    arm_id: str
    host: str
    failure_class: str
    requirement: str | None
    recommendation: str
    detail: str

    @property
    def is_contract_change(self) -> bool:
        return self.requirement is not None


@dataclass(frozen=True)
class Underperformance:
    """The treatment arm ranked below a baseline on one host for one task."""

    task_id: str
    host: str
    treatment_failure_class: str | None
    beaten_by: tuple[str, ...]

    @property
    def requirement(self) -> str | None:
        """Return the requirement the treatment's own failure argues about."""

        if self.treatment_failure_class is None:
            return None
        return FAILURE_REQUIREMENTS[self.treatment_failure_class]

    @property
    def recommendation(self) -> str | None:
        if self.treatment_failure_class is None:
            return None
        return FAILURE_RECOMMENDATIONS[self.treatment_failure_class]


@dataclass(frozen=True)
class TaskAnalysis:
    """Every arm and host outcome for one task, plus whether the treatment underperformed.

    Comparison is scoped to one host at a time. A baseline measured on one host
    is not evidence about the treatment on another, so pooling hosts would let a
    host difference read as a method difference.
    """

    task_id: str
    outcomes: tuple[ArmOutcome, ...]
    findings: tuple[FailureFinding, ...]

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(outcome.host for outcome in self.outcomes))

    def treatment_for(self, host: str) -> ArmOutcome | None:
        return next(
            (outcome for outcome in self.outcomes if outcome.arm_id == TREATMENT_ARM_ID and outcome.host == host),
            None,
        )

    def baselines_for(self, host: str) -> tuple[ArmOutcome, ...]:
        return tuple(
            outcome for outcome in self.outcomes if outcome.arm_id != TREATMENT_ARM_ID and outcome.host == host
        )

    @property
    def underperforming_baselines(self) -> tuple[ArmOutcome, ...]:
        """Return baseline outcomes that ranked above the treatment on the same host.

        Both sides must be recorded: an unrecorded arm has no outcome to rank, so
        it can neither win nor lose a comparison.
        """

        beaten: list[ArmOutcome] = []
        for host in self.hosts:
            treatment = self.treatment_for(host)
            if treatment is None or treatment.score is None:
                continue
            beaten.extend(
                outcome
                for outcome in self.baselines_for(host)
                if outcome.score is not None and outcome.score.outcome_rank > treatment.score.outcome_rank
            )
        return tuple(beaten)

    @property
    def treatment_underperforms(self) -> bool:
        return bool(self.underperforming_baselines)

    @property
    def underperformance(self) -> tuple[Underperformance, ...]:
        """Return one entry per host where the treatment ranked below a baseline.

        The treatment's failure class is read back from the findings already
        classified for this task, so the two can never disagree.
        """

        entries: list[Underperformance] = []
        for host in self.hosts:
            treatment = self.treatment_for(host)
            if treatment is None or treatment.score is None:
                continue
            beaten_by = tuple(
                outcome.arm_id
                for outcome in self.baselines_for(host)
                if outcome.score is not None and outcome.score.outcome_rank > treatment.score.outcome_rank
            )
            if not beaten_by:
                continue
            entries.append(
                Underperformance(
                    task_id=self.task_id,
                    host=host,
                    treatment_failure_class=next(
                        (
                            finding.failure_class
                            for finding in self.findings
                            if finding.arm_id == TREATMENT_ARM_ID and finding.host == host
                        ),
                        None,
                    ),
                    beaten_by=beaten_by,
                )
            )
        return tuple(entries)


@dataclass(frozen=True)
class WorkContinuityAnalysis:
    """Task analyses plus the aggregated findings and requirement pressure."""

    task_analyses: tuple[TaskAnalysis, ...]

    @property
    def findings(self) -> tuple[FailureFinding, ...]:
        return tuple(finding for analysis in self.task_analyses for finding in analysis.findings)

    @property
    def treatment_findings(self) -> tuple[FailureFinding, ...]:
        return tuple(finding for finding in self.findings if finding.arm_id == TREATMENT_ARM_ID)

    @property
    def underperforming_task_ids(self) -> tuple[str, ...]:
        return tuple(analysis.task_id for analysis in self.task_analyses if analysis.treatment_underperforms)

    @property
    def underperformance(self) -> tuple[Underperformance, ...]:
        """Return every host-scoped case where the treatment ranked below a baseline."""

        return tuple(entry for analysis in self.task_analyses for entry in analysis.underperformance)

    @property
    def findings_by_class(self) -> dict[str, int]:
        counts: dict[str, int] = {failure_class: 0 for failure_class in FAILURE_CLASSES}
        for finding in self.findings:
            if finding.failure_class != NO_RECORDING:
                counts[finding.failure_class] = counts.get(finding.failure_class, 0) + 1
        return counts

    @property
    def unrecorded_keys(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (outcome.task_id, outcome.arm_id, outcome.host)
            for analysis in self.task_analyses
            for outcome in analysis.outcomes
            if not outcome.recorded
        )


def classify_failure(
    task: ContinuationTask,
    context: ContinuationContext,
    score: AttemptScore | None,
) -> str | None:
    """Return the failure class for one arm outcome, or ``None`` when it recovered cleanly."""

    if score is None:
        return NO_RECORDING
    if (
        score.task_success
        and not score.incorrect_assumptions
        and not score.unverifiable_claims
        and not score.missing_evidence
    ):
        return None
    needed = set(task.expected_next_action.required_fact_ids)
    if needed & facts_lost_to_the_budget(task, context):
        return BUDGET_TRUNCATION
    if not needed <= set(context.delivered_fact_ids):
        return CONTEXT_ABSENT
    if score.incorrect_assumptions:
        return STALE_STATE
    if score.unverifiable_claims:
        return UNVERIFIABLE_CLAIM
    if score.missing_evidence:
        return MISSING_EVIDENCE
    return VAGUE_NEXT_ACTION


def facts_lost_to_the_budget(task: ContinuationTask, context: ContinuationContext) -> set[str]:
    """Return the required facts the byte ceiling removed together with their carrier.

    A method delivers a fact either as a state item or as the transcript turn its
    evidence lives in, so a turn dropped by ``_fit`` is a dropped fact. Counting
    only dropped ``state`` items would make budget truncation unreachable for every
    transcript method and would report a truncated transcript window as an absent
    context, which in turn advises widening a window that already selected every
    turn. Only items the budget removed are considered, so a turn the arm's own
    window never selected stays a context-absence question instead.
    """

    dropped = set(context.dropped_item_ids)
    lost = {item_id.split(":", 1)[1] for item_id in dropped if item_id.startswith("state:")}
    for fact in task.required_state_facts:
        if fact.evidence is None:
            continue
        if f"turn:{int(fact.evidence.split(':', 1)[1])}" in dropped:
            lost.add(fact.fact_id)
    return lost


def analyse_task_outcomes(task: ContinuationTask, outcomes: Sequence[ArmOutcome]) -> TaskAnalysis:
    """Classify every outcome for one task and attach the decision each failure argues about."""

    findings: list[FailureFinding] = []
    for outcome in outcomes:
        failure_class = classify_failure(task, outcome.context, outcome.score)
        if failure_class is None:
            continue
        findings.append(
            FailureFinding(
                task_id=task.task_id,
                arm_id=outcome.arm_id,
                host=outcome.host,
                failure_class=failure_class,
                requirement=FAILURE_REQUIREMENTS[failure_class],
                recommendation=FAILURE_RECOMMENDATIONS[failure_class],
                detail=_detail(failure_class, task, outcome),
            )
        )
    return TaskAnalysis(task_id=task.task_id, outcomes=tuple(outcomes), findings=tuple(findings))


def analyse_work_continuity(task_analyses: Sequence[TaskAnalysis]) -> WorkContinuityAnalysis:
    """Aggregate per-task analyses into the reportable failure picture."""

    return WorkContinuityAnalysis(task_analyses=tuple(task_analyses))


def _detail(failure_class: str, task: ContinuationTask, outcome: ArmOutcome) -> str:
    score = outcome.score
    if score is None:
        return "no attempt recorded for this task and method"
    if failure_class == BUDGET_TRUNCATION:
        lost = ", ".join(sorted(facts_lost_to_the_budget(task, outcome.context)))
        carriers = [item for item in outcome.context.dropped_item_ids if item.startswith("state:")]
        carried_by = f"state items {', '.join(carriers)}" if carriers else "the turns their evidence lives in"
        return f"the {score.max_bytes} byte ceiling dropped required facts ({lost}) together with {carried_by}"
    if failure_class == CONTEXT_ABSENT:
        missing = ", ".join(score.facts_missing_from_context)
        return f"the context never delivered facts the next action depends on: {missing}"
    if failure_class == STALE_STATE:
        first = next(scored for scored in score.steps if scored.superseded_reliance)
        return f"step {first.step} relied on superseded facts: {', '.join(first.superseded_reliance)}"
    if failure_class == UNVERIFIABLE_CLAIM:
        first = next(scored for scored in score.steps if scored.unavailable_reliance)
        return (
            f"step {first.step} relied on facts whose evidence is unavailable: {', '.join(first.unavailable_reliance)}"
        )
    if failure_class == MISSING_EVIDENCE:
        first = next(scored for scored in score.steps if scored.undelivered_reliance)
        return f"step {first.step} relied on facts the context did not deliver: {', '.join(first.undelivered_reliance)}"
    recovery = score.time_to_recover_state
    if recovery is None:
        return (
            "every fact the next action depends on was delivered, yet the session never reached the "
            f"expected next action within {len(score.steps)} recorded steps"
        )
    return f"every fact the next action depends on was delivered, yet recovery took {recovery} steps"
