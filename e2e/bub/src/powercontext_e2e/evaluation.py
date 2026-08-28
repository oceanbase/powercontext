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

"""Offline Memory evaluation over normalized workload evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .catalog import MemoryEvaluationSpec, OutcomeEvaluationSpec
from .models import (
    CaseEvaluation,
    EvaluationReport,
    EvaluationValue,
    HarborTrialObservation,
    TaskObservation,
)

CAPTURE_EVENTS = frozenset({"user_prompt", "llm_result", "tool_result"})
FailurePolicy = Literal["fail-fast", "collect-all"]


def evaluate_observation(
    observation: TaskObservation,
    *,
    experiment: str,
    failure_policy: FailurePolicy,
) -> EvaluationReport:
    evaluation = observation.task.evaluation
    if isinstance(evaluation, OutcomeEvaluationSpec):
        return _evaluate_execution(
            observation,
            evaluation,
            experiment=experiment,
            failure_policy=failure_policy,
        )
    return MemoryEvaluator.evaluate(observation, experiment=experiment)


def _evaluate_execution(
    observation: TaskObservation,
    evaluation: OutcomeEvaluationSpec,
    *,
    experiment: str,
    failure_policy: FailurePolicy,
) -> EvaluationReport:
    expected = (
        evaluation.expected_execution.collect_all
        if failure_policy == "collect-all"
        else evaluation.expected_execution.fail_fast
    )
    actual = observation.status
    expected_reward = {"completed": 1, "failed": 0}.get(expected)
    actual_reward = observation.harbor.rewards.get("reward")
    reward_matches = expected == "skipped" or actual_reward == expected_reward
    return EvaluationReport(
        experiment=experiment,
        cases=(
            CaseEvaluation(
                name=observation.task.id,
                assertions={
                    "execution_outcome": EvaluationValue(
                        value=actual == expected and reward_matches,
                        reason=(
                            f"Expected {expected!r}; observed {actual!r}."
                            if expected == "skipped"
                            else (
                                f"Expected {expected!r} with Harbor reward {expected_reward!r}; "
                                f"observed {actual!r} with Harbor reward {actual_reward!r}."
                            )
                        ),
                    )
                },
                labels={"task_outcome": EvaluationValue(value=actual)},
                attributes=_attributes(observation),
            ),
        ),
    )


class MemoryEvaluator:
    """Evaluate one workload through the common Memory acceptance contract."""

    @staticmethod
    def evaluate(observation: TaskObservation, *, experiment: str) -> EvaluationReport:
        task = observation.task
        attributes = _attributes(observation)
        if observation.status == "skipped":
            return EvaluationReport(
                experiment=experiment,
                cases=(
                    CaseEvaluation(
                        name=task.id,
                        assertions={
                            "execution_completed": EvaluationValue(
                                value=False,
                                reason="Skipped after an earlier task stopped the shared Harbor trial.",
                            )
                        },
                        labels={"task_outcome": EvaluationValue(value="skipped")},
                        attributes=attributes,
                    ),
                ),
            )
        evaluation = task.evaluation
        if not isinstance(evaluation, MemoryEvaluationSpec):
            raise TypeError("MemoryEvaluator requires a Memory evaluation task")  # noqa: TRY003
        eligible_records = [record for record in observation.capture_records if record.event in CAPTURE_EVENTS]
        captured_records = [record for record in eligible_records if record.status == "captured"]
        capture_coverage = len(captured_records) / len(eligible_records) if eligible_records else 0.0

        memory_before_ids = {entry.entry_id for entry in observation.memory_before.entries}
        new_memory = [entry for entry in observation.memory_after.entries if entry.entry_id not in memory_before_ids]
        captured_source_ids = {record.source_id for record in captured_records if record.source_id is not None}
        grounded_memory = [
            entry
            for entry in new_memory
            if entry.source_refs and all(source.source_id in captured_source_ids for source in entry.source_refs)
        ]
        groundedness = len(grounded_memory) / len(new_memory) if new_memory else 0.0

        probes_by_id = {probe.id: probe for probe in observation.probes}
        supported_probes = [
            probe_spec
            for probe_spec in evaluation.probes
            if (probe := probes_by_id.get(probe_spec.id)) is not None
            and probe.prepared_context.status == "ready"
            and bool(probe.prepared_context.content.strip())
            and _contains_fragments(probe.prepared_context.content, probe_spec.expected_context)
        ]
        probe_coverage = len(supported_probes) / len(evaluation.probes)
        in_run_contexts = sum(
            record.event == "context"
            and record.status == "ready"
            and record.content_bytes is not None
            and record.content_bytes > 0
            for record in observation.capture_records
        )
        completed_checkpoints = [
            record
            for record in observation.capture_records
            if record.event == "checkpoint"
            and record.status != "failed"
            and record.current_cursor is not None
            and record.target_position is not None
            and record.current_cursor >= record.target_position
        ]
        native_names = {Path(artifact.name).name for artifact in observation.native_artifacts}
        missing_native_artifacts = sorted(task.execution.native_artifact_names - native_names)
        summary_artifacts = {
            artifact.name for artifact in observation.native_artifacts if Path(artifact.name).name == "acp-summary.json"
        }
        instruction_artifacts = {instruction.artifact for instruction in observation.resolved_instructions}
        instructions_recorded = bool(summary_artifacts) and instruction_artifacts == summary_artifacts
        expected_memory_found = all(
            any(fragment.casefold() in entry.text.casefold() for entry in observation.memory_after.entries)
            for fragment in evaluation.expected_memory
        )
        observed_checksum = observation.harbor.source_task_checksum or observation.harbor.task_checksum
        thresholds = evaluation.thresholds

        metrics = {
            "capture_events": len(eligible_records),
            "captured_sources": len(captured_records),
            "completed_checkpoints": len(completed_checkpoints),
            "in_run_contexts": in_run_contexts,
            "resolved_instructions": len(observation.resolved_instructions),
            "memory_entries_after": len(observation.memory_after.entries),
            "memory_entries_created": len(new_memory),
            "recall_probes_supported": len(supported_probes),
        }

        assertions = {
            "collection_completed": EvaluationValue(
                value=observation.status == "completed",
                reason=None if observation.status == "completed" else "; ".join(observation.errors),
            ),
            "task_provenance_matches": EvaluationValue(
                value=observed_checksum == task.dataset.checksum,
                reason=(f"Expected {task.dataset.checksum!r}; observed {observed_checksum!r}."),
            ),
            "native_acp_evidence_recorded": EvaluationValue(
                value=not missing_native_artifacts,
                reason=(
                    "All required native ACP artifacts were recorded."
                    if not missing_native_artifacts
                    else f"Missing native ACP artifacts: {missing_native_artifacts!r}."
                ),
            ),
            "resolved_instructions_recorded": EvaluationValue(
                value=instructions_recorded,
                reason=(
                    f"Resolved {len(instruction_artifacts)} instructions from {len(summary_artifacts)} ACP summaries."
                ),
            ),
            "capture_coverage_accepted": EvaluationValue(
                value=capture_coverage >= thresholds.capture_coverage,
                reason=f"Observed {capture_coverage:.3f}; required {thresholds.capture_coverage:.3f}.",
            ),
            "memory_created_during_run": EvaluationValue(
                value=bool(new_memory) and (not evaluation.require_checkpoint or bool(completed_checkpoints)),
                reason=f"Created {len(new_memory)} Memory entries and completed {len(completed_checkpoints)} checkpoints.",
            ),
            "memory_grounded": EvaluationValue(
                value=groundedness >= thresholds.groundedness,
                reason=f"Observed {groundedness:.3f}; required {thresholds.groundedness:.3f}.",
            ),
            "recall_probes_supported": EvaluationValue(
                value=probe_coverage >= thresholds.probe_coverage,
                reason=f"Observed {probe_coverage:.3f}; required {thresholds.probe_coverage:.3f}.",
            ),
            "memory_recalled_during_run": EvaluationValue(
                value=in_run_contexts >= thresholds.minimum_in_run_contexts,
                reason=f"Observed {in_run_contexts}; required {thresholds.minimum_in_run_contexts}.",
            ),
            "scope_started_empty": EvaluationValue(
                value=not observation.memory_before.entries,
                reason=f"Scope started with {len(observation.memory_before.entries)} Memory entries.",
            ),
        }
        if evaluation.expected_memory:
            assertions["expected_memory_recorded"] = EvaluationValue(
                value=expected_memory_found,
                reason=f"Expected Memory fragments: {list(evaluation.expected_memory)!r}.",
            )
        scores = {
            "capture_coverage": EvaluationValue(value=capture_coverage),
            "groundedness": EvaluationValue(value=groundedness),
            "probe_coverage": EvaluationValue(value=probe_coverage),
            **{
                f"harbor_reward_{name}": EvaluationValue(value=float(reward))
                for name, reward in sorted(observation.harbor.rewards.items())
            },
        }
        labels = {"task_outcome": EvaluationValue(value=_task_outcome(observation.harbor, observation.status))}
        return EvaluationReport(
            experiment=experiment,
            cases=(
                CaseEvaluation(
                    name=task.id,
                    assertions=assertions,
                    scores=scores,
                    labels=labels,
                    metrics=metrics,
                    attributes=attributes,
                ),
            ),
        )


def _contains_fragments(value: str, expected: tuple[str, ...]) -> bool:
    folded = value.casefold()
    return all(fragment.casefold() in folded for fragment in expected)


def _attributes(observation: TaskObservation) -> dict[str, str]:
    task = observation.task
    return {
        "commit": observation.environment.commit,
        "database": observation.environment.database,
        "dataset": task.dataset.name or str(task.dataset.path),
        "execution_adapter": task.execution.type,
        "harbor_task_id": task.dataset.task_id,
        "run_id": observation.run_id,
        "workload_id": task.id,
    }


def _task_outcome(harbor: HarborTrialObservation, status: Literal["completed", "failed", "skipped"]) -> str:
    if status == "skipped":
        return "skipped"
    if harbor.exception_type is not None:
        return f"error:{harbor.exception_type}"
    if status == "failed":
        return "not_passed"
    if not harbor.rewards:
        return "unscored"
    return "passed" if all(float(reward) >= 1 for reward in harbor.rewards.values()) else "not_passed"
