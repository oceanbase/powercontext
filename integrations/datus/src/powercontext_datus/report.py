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

"""Fail-closed aggregation over a fixed, independently prepared task roster."""

# ruff: noqa: TRY003 - bounded validation errors are part of this bridge's diagnostics.

from __future__ import annotations

from dataclasses import dataclass, field

INVALIDATING = frozenset({
    "trace_missing",
    "oracle_conflict",
    "leakage/state_drift",
    "no_native_execution",
    "wrong_result/answer",
    "timeout/cancel",
    "environment/auth",
    "skill_delivery/load",
})


@dataclass(frozen=True)
class CaseVerdict:
    """Evaluator-owned verdict, not an Agent's self-reported success flag."""

    task_id: str
    correct: bool
    steps: int | None
    native_execution: bool
    answer_grounded: bool
    trace_complete: bool
    oracle_consistent: bool
    state_valid: bool
    failures: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task ID is required")
        if self.steps is not None and (type(self.steps) is not int or self.steps < 0):
            raise ValueError("steps must be a nonnegative integer or unknown")


def _case_failures(case: CaseVerdict, *, data_version_verified: bool) -> set[str]:
    failures = set(case.failures)
    checks = {
        "no_native_execution": not case.native_execution,
        "wrong_result/answer": not case.answer_grounded or not case.correct,
        "oracle_conflict": not case.oracle_consistent,
        "leakage/state_drift": not case.state_valid or not data_version_verified,
        "trace_missing": not case.trace_complete or case.steps is None,
        "step_overrun": case.steps is not None and case.steps > 2,
    }
    failures.update(label for label, failed in checks.items() if failed)
    return failures


def summarize(task_ids: list[str], verdicts: list[CaseVerdict], *, data_version_verified: bool) -> dict[str, object]:
    """Never shrink the denominator to available/successful output files.

    This consumes independently validated evidence. It does not authenticate
    arbitrary verdict JSON or turn synthetic/replay records into native evidence.
    """
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("task roster must be nonempty and unique")
    by_id = {case.task_id: case for case in verdicts}
    if len(by_id) != len(verdicts) or not by_id.keys() <= set(task_ids):
        raise ValueError("duplicate or unexpected task result")
    cases = []
    known_steps = []
    correct_count = 0
    joint_count = 0
    for task_id in task_ids:
        case = by_id.get(task_id)
        if case is None:
            cases.append({"task_id": task_id, "correct": False, "joint_pass": False, "failures": ["trace_missing"]})
            continue
        failures = _case_failures(case, data_version_verified=data_version_verified)
        if case.steps is not None and "trace_missing" not in failures:
            known_steps.append(case.steps)
        correct = case.correct and case.native_execution and case.answer_grounded and case.oracle_consistent
        correct_count += int(correct)
        joint = correct and not failures.intersection(INVALIDATING) and case.steps is not None and 1 <= case.steps <= 2
        joint_count += int(joint)
        cases.append({
            "task_id": task_id,
            "correct": correct,
            "steps": case.steps,
            "joint_pass": joint,
            "failures": sorted(failures),
        })
    total = len(task_ids)
    run_state_valid = data_version_verified and all(
        case.state_valid and "leakage/state_drift" not in case.failures for case in verdicts
    )
    oracle_valid = len(verdicts) == total and all(
        case.oracle_consistent and "oracle_conflict" not in case.failures for case in verdicts
    )
    # Failed cases still consume the full-roster budget. Missing evidence is
    # unknown, never a zero-step failure or a reason to shrink the denominator.
    total_steps = sum(known_steps) if len(known_steps) == total else None
    step_budget = 2 * total
    step_budget_pass = total_steps is not None and total_steps <= step_budget
    return {
        "total": total,
        "results_present": len(verdicts),
        "correct": correct_count,
        "accuracy": correct_count / total,
        "joint_pass": joint_count,
        "joint_rate": joint_count / total,
        "total_steps": total_steps,
        "step_budget": step_budget,
        "step_budget_pass": step_budget_pass,
        "mean_steps": total_steps / total if total_steps is not None else None,
        "step_coverage": len(known_steps) / total,
        "data_version_verified": data_version_verified,
        "run_state_valid": run_state_valid,
        "oracle_valid": oracle_valid,
        "accepted": run_state_valid and oracle_valid and joint_count * 10 >= total * 9 and step_budget_pass,
        "cases": cases,
    }
