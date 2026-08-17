"""Offline Memory evaluation over normalized workload evidence."""

from __future__ import annotations

from pathlib import Path

from .models import (
    CaseEvaluation,
    EvaluationReport,
    EvaluationValue,
    HarborTrialObservation,
    TaskObservation,
)

CAPTURE_EVENTS = frozenset({"user_prompt", "llm_result", "tool_result"})


class MemoryEvaluator:
    """Evaluate one workload through the common Memory acceptance contract."""

    @staticmethod
    def evaluate(observation: TaskObservation, *, experiment: str) -> EvaluationReport:
        task = observation.task
        evaluation = task.evaluation
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
        thresholds = evaluation.thresholds

        attributes = {
            "commit": observation.environment.commit,
            "database": observation.environment.database,
            "dataset": task.dataset.name or str(task.dataset.path),
            "execution_adapter": task.execution.type,
            "harbor_task_id": task.dataset.task_id,
            "run_id": observation.run_id,
            "workload_id": task.id,
        }
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
                value=observation.harbor.task_checksum == task.dataset.checksum,
                reason=f"Expected {task.dataset.checksum!r}; observed {observation.harbor.task_checksum!r}.",
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
        labels = {"task_outcome": EvaluationValue(value=_task_outcome(observation.harbor))}
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


def _task_outcome(harbor: HarborTrialObservation) -> str:
    if harbor.exception_type is not None:
        return f"error:{harbor.exception_type}"
    if not harbor.rewards:
        return "unscored"
    return "passed" if all(float(reward) >= 1 for reward in harbor.rewards.values()) else "not_passed"
