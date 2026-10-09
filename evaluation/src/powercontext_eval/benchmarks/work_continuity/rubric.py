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

"""The work-continuity scoring rubric.

The rubric answers the question a continuation method is supposed to improve:
after receiving one arm's continuation context, did the fresh session continue
correctly, and what did that cost? Every metric is computed from the recorded
step's declared facts and the assembled context, so two runs of the same arm over
the same task produce identical numbers.

Injected bytes are reported next to the outcome metrics and never folded into
them. A method that injects less is not thereby more successful, and a method
that recovers state with more bytes is not thereby worse; collapsing the two
would hide exactly the trade-off this benchmark measures.
"""

from __future__ import annotations

from dataclasses import dataclass

from powercontext_eval.benchmarks.work_continuity.assembly import ContinuationContext
from powercontext_eval.benchmarks.work_continuity.attempts import RecordedAttempt
from powercontext_eval.benchmarks.work_continuity.catalog import ContinuationTask

# A step that number was never reached counts as the worst possible recovery
# time, so an unrecovered attempt never sorts above a slow recovery.
UNRECOVERED_STEP_SENTINEL = 1_000_000


@dataclass(frozen=True)
class StepScore:
    """The rubric outcome for one recorded step."""

    step: int
    action_text: str
    relied_on: tuple[str, ...]
    performed_action_id: str | None
    superseded_reliance: tuple[str, ...]
    undelivered_reliance: tuple[str, ...]
    unavailable_reliance: tuple[str, ...]
    correction: bool
    performs_expected_action: bool
    is_recovery: bool


@dataclass(frozen=True)
class AttemptScore:
    """The rubric outcome for one recorded attempt against one assembled context."""

    task_id: str
    arm_id: str
    host: str
    task_success: bool
    time_to_recover_state: int | None
    incorrect_assumptions: int
    missing_evidence: int
    unverifiable_claims: int
    user_correction_burden: int
    injected_bytes: int
    max_bytes: int
    context_truncated: bool
    delivered_fact_ids: tuple[str, ...]
    expected_fact_ids: tuple[str, ...]
    steps: tuple[StepScore, ...]

    @property
    def facts_missing_from_context(self) -> tuple[str, ...]:
        """Return the expected-action facts this arm's context never delivered."""

        delivered = set(self.delivered_fact_ids)
        return tuple(fact_id for fact_id in self.expected_fact_ids if fact_id not in delivered)

    @property
    def has_assembly_gap(self) -> bool:
        """Return whether the context omitted something the next action depends on."""

        return bool(self.facts_missing_from_context)

    @property
    def outcome_rank(self) -> tuple[int, int, int, int, int, int]:
        """Rank the continuation outcome alone, with injected bytes excluded.

        Higher is better. Injected bytes are deliberately absent: this ordering
        exists to find cases where the treatment underperforms, not to reward a
        method for injecting less.
        """

        recovery = self.time_to_recover_state if self.time_to_recover_state is not None else UNRECOVERED_STEP_SENTINEL
        return (
            int(self.task_success),
            -self.incorrect_assumptions,
            -self.unverifiable_claims,
            -self.missing_evidence,
            -self.user_correction_burden,
            -recovery,
        )


def score_attempt(
    task: ContinuationTask,
    context: ContinuationContext,
    attempt: RecordedAttempt,
) -> AttemptScore:
    """Score one recorded attempt against the context its arm delivered."""

    if (attempt.task_id, attempt.arm_id) != (task.task_id, context.arm_id):
        raise ValueError(
            "attempt does not belong to this task and arm: "
            f"{attempt.task_id}/{attempt.arm_id} against {task.task_id}/{context.arm_id}"
        )
    expected = set(task.expected_next_action.required_fact_ids)
    unavailable = set(context.unavailable_fact_ids)
    delivered = set(context.delivered_fact_ids)
    steps: list[StepScore] = []
    for step in attempt.steps:
        relied = set(step.relied_on)
        undelivered = relied & (set(task.required_fact_ids) - delivered - unavailable)
        superseded = tuple(fact for fact in task.obsolete_fact_ids if fact in relied)
        performs = step.performed_action_id == task.expected_next_action.action_id
        steps.append(
            StepScore(
                step=step.step,
                action_text=step.action_text,
                relied_on=step.relied_on,
                performed_action_id=step.performed_action_id,
                superseded_reliance=superseded,
                undelivered_reliance=tuple(fact for fact in task.required_fact_ids if fact in undelivered),
                unavailable_reliance=tuple(fact for fact in context.unavailable_fact_ids if fact in relied),
                correction=step.correction,
                performs_expected_action=performs,
                # Reading the facts an action depends on is not continuing the
                # work, continuing from a replaced plan is not continuing the
                # declared one, and relying on material the context never
                # delivered is a contradiction rather than a continuation. Only a
                # step that does the expected action, on state that is still
                # current *and was actually delivered*, counts as a recovery: a
                # recording cannot certify that it continued from a context by
                # naming a fact that context never carried.
                is_recovery=expected <= relied and not superseded and performs and not undelivered,
            )
        )
    recoveries = [scored.step for scored in steps if scored.is_recovery]
    return AttemptScore(
        task_id=task.task_id,
        arm_id=context.arm_id,
        host=attempt.host,
        task_success=bool(recoveries),
        time_to_recover_state=min(recoveries) if recoveries else None,
        incorrect_assumptions=sum(1 for scored in steps if scored.superseded_reliance),
        missing_evidence=sum(1 for scored in steps if scored.undelivered_reliance),
        unverifiable_claims=sum(1 for scored in steps if scored.unavailable_reliance),
        user_correction_burden=sum(1 for scored in steps if scored.correction),
        injected_bytes=context.injected_bytes,
        max_bytes=context.max_bytes,
        context_truncated=context.truncated,
        delivered_fact_ids=context.delivered_fact_ids,
        expected_fact_ids=task.expected_next_action.required_fact_ids,
        steps=tuple(steps),
    )


def score_attempts(
    task: ContinuationTask,
    context: ContinuationContext,
    attempts: tuple[RecordedAttempt, ...],
) -> tuple[AttemptScore, ...]:
    """Score every recorded attempt for one task and arm."""

    return tuple(score_attempt(task, context, attempt) for attempt in attempts)
