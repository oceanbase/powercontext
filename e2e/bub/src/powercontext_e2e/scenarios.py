"""Scenario-first acceptance orchestration for Bub handoff and Memory reuse."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .artifacts import write_artifacts
from .catalog import E2ETask
from .evaluation import MemoryEvaluator
from .evidence import fingerprint, write_evidence
from .models import (
    BubMetrics,
    EvaluationValue,
    ScenarioComparison,
    ScenarioLegObservation,
    ScenarioSuiteObservation,
)
from .runner import TaskRunOptions, run_task
from .settings import HarnessSettings, ModelNotConfiguredError, bub_environment
from .usage import summarize_bub_metrics

ScenarioName = Literal["session-handoff", "container-handoff", "completed-reuse"]


@dataclass(frozen=True)
class ScenarioSelection:
    scenarios: tuple[ScenarioName, ...]
    handoff_steps: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.scenarios:
            raise ValueError("At least one scenario must be selected")  # noqa: TRY003
        if len(self.scenarios) != len(set(self.scenarios)):
            raise ValueError("Scenario names must be unique")  # noqa: TRY003
        if any(step < 1 for step in self.handoff_steps):
            raise ValueError("Handoff steps must be positive")  # noqa: TRY003
        if len(self.handoff_steps) != len(set(self.handoff_steps)):
            raise ValueError("Handoff steps must be unique")  # noqa: TRY003
        if {"session-handoff", "container-handoff"}.intersection(self.scenarios) and not self.handoff_steps:
            raise ValueError("Handoff scenarios require at least one handoff step")  # noqa: TRY003


async def run_scenario_tasks(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    selection: ScenarioSelection,
) -> bool:
    model_workload_ids = tuple(task.id for task in tasks if task.execution.model)
    if model_workload_ids and "BUB_MODEL" not in bub_environment():
        raise ModelNotConfiguredError(model_workload_ids)

    accepted = True
    for task in tasks:
        suite = await run_scenario_suite(
            task,
            output_dir=output_dir / task.id,
            settings=settings,
            selection=selection,
        )
        accepted = suite.accepted and accepted
    return accepted


async def run_scenario_suite(
    task: E2ETask,
    *,
    output_dir: Path,
    settings: HarnessSettings,
    selection: ScenarioSelection,
) -> ScenarioSuiteObservation:
    invalid_steps = tuple(step for step in selection.handoff_steps if step >= task.execution.max_steps)
    if invalid_steps:
        raise ValueError(  # noqa: TRY003
            f"Handoff steps must be smaller than the task max_steps ({task.execution.max_steps}): {invalid_steps}"
        )

    suite_id = f"{task.id}-scenarios-{uuid4().hex[:12]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    legs: dict[str, ScenarioLegObservation] = {}

    baseline_scope = f"e2e:{suite_id}:baseline"
    baseline = await _execute_leg(
        task,
        output_dir=output_dir,
        settings=settings,
        leg_id="baseline",
        scenario="uninterrupted",
        role="baseline",
        scope_id=baseline_scope,
        options=TaskRunOptions(run_id=f"{suite_id}-baseline", scope_id=baseline_scope),
    )
    legs[baseline.id] = baseline

    comparisons: list[ScenarioComparison] = []
    for checkpoint_steps in selection.handoff_steps:
        if "session-handoff" in selection.scenarios:
            scope_id = f"e2e:{suite_id}:session:{checkpoint_steps}"
            candidate = await _execute_leg(
                task,
                output_dir=output_dir,
                settings=settings,
                leg_id=f"session-handoff-{checkpoint_steps}",
                scenario="session-handoff",
                role="resume",
                scope_id=scope_id,
                options=TaskRunOptions(
                    run_id=f"{suite_id}-session-{checkpoint_steps}",
                    scope_id=scope_id,
                    handoff_after_steps=checkpoint_steps,
                ),
            )
            legs[candidate.id] = candidate
            comparisons.append(
                _handoff_comparison(
                    scenario="session-handoff",
                    checkpoint_steps=checkpoint_steps,
                    baseline=baseline,
                    source=None,
                    candidate=candidate,
                )
            )

        if "container-handoff" in selection.scenarios:
            scope_id = f"e2e:{suite_id}:container:{checkpoint_steps}"
            snapshot_dir = output_dir / "workspace-snapshots" / f"step-{checkpoint_steps}"
            source = await _execute_leg(
                task,
                output_dir=output_dir,
                settings=settings,
                leg_id=f"container-source-{checkpoint_steps}",
                scenario="container-handoff",
                role="source",
                scope_id=scope_id,
                options=TaskRunOptions(
                    run_id=f"{suite_id}-container-source-{checkpoint_steps}",
                    scope_id=scope_id,
                    segment_max_steps=checkpoint_steps,
                    workspace_snapshot_dir=snapshot_dir,
                    save_workspace=True,
                    disable_verifier=True,
                ),
            )
            legs[source.id] = source
            candidate = await _execute_leg(
                task,
                output_dir=output_dir,
                settings=settings,
                leg_id=f"container-resume-{checkpoint_steps}",
                scenario="container-handoff",
                role="resume",
                scope_id=scope_id,
                options=TaskRunOptions(
                    run_id=f"{suite_id}-container-resume-{checkpoint_steps}",
                    scope_id=scope_id,
                    prompt="continue",
                    segment_max_steps=task.execution.max_steps - checkpoint_steps,
                    workspace_snapshot_dir=snapshot_dir,
                    restore_workspace=True,
                ),
            )
            legs[candidate.id] = candidate
            comparisons.append(
                _handoff_comparison(
                    scenario="container-handoff",
                    checkpoint_steps=checkpoint_steps,
                    baseline=baseline,
                    source=source,
                    candidate=candidate,
                )
            )

    if "completed-reuse" in selection.scenarios:
        cold_scope = f"e2e:{suite_id}:completed-cold"
        cold = await _execute_leg(
            task,
            output_dir=output_dir,
            settings=settings,
            leg_id="completed-cold",
            scenario="completed-reuse",
            role="cold",
            scope_id=cold_scope,
            options=TaskRunOptions(run_id=f"{suite_id}-completed-cold", scope_id=cold_scope),
        )
        warm = await _execute_leg(
            task,
            output_dir=output_dir,
            settings=settings,
            leg_id="completed-warm",
            scenario="completed-reuse",
            role="warm",
            scope_id=baseline_scope,
            options=TaskRunOptions(run_id=f"{suite_id}-completed-warm", scope_id=baseline_scope),
        )
        legs[cold.id] = cold
        legs[warm.id] = warm
        comparisons.append(_completed_reuse_comparison(cold, warm))

    suite = ScenarioSuiteObservation(
        run_id=suite_id,
        task=task,
        handoff_steps=selection.handoff_steps,
        legs=tuple(legs.values()),
        comparisons=tuple(comparisons),
    )
    write_evidence(
        output_dir / "scenario.json",
        suite.model_dump_json(by_alias=True, indent=2) + "\n",
        settings,
    )
    write_evidence(output_dir / "scenario-report.md", _render_scenario_report(suite), settings)
    return suite


async def _execute_leg(
    task: E2ETask,
    *,
    output_dir: Path,
    settings: HarnessSettings,
    leg_id: str,
    scenario: Literal["uninterrupted", "session-handoff", "container-handoff", "completed-reuse"],
    role: Literal["baseline", "source", "resume", "cold", "warm"],
    scope_id: str,
    options: TaskRunOptions,
) -> ScenarioLegObservation:
    leg_dir = output_dir / "legs" / leg_id
    observation = await run_task(task, output_dir=leg_dir, settings=settings, options=options)
    report = MemoryEvaluator.evaluate(observation, experiment=f"scenario:{scenario}:{leg_id}")
    write_artifacts(observation, report, leg_dir, settings=settings)
    workspace_archive = (
        options.workspace_snapshot_dir / "workspace.tar" if options.workspace_snapshot_dir is not None else None
    )
    leg = ScenarioLegObservation(
        id=leg_id,
        scenario=scenario,
        role=role,
        scope_id=scope_id,
        prompt=options.prompt,
        workspace_restored=options.restore_workspace,
        workspace_saved=options.save_workspace,
        workspace_snapshot=(
            fingerprint(workspace_archive) if workspace_archive is not None and workspace_archive.is_file() else None
        ),
        replay=(Path("legs") / leg_id / "replay.json").as_posix(),
        status=observation.status,
        rewards=observation.harbor.rewards,
        memory_entries_before=len(observation.memory_before.entries),
        memory_entries_after=len(observation.memory_after.entries),
        agent_sessions=observation.harbor.agent_sessions,
        agent_prompts=observation.harbor.agent_prompts,
        metrics=summarize_bub_metrics(observation.capture_records),
    )
    return leg


def _handoff_comparison(
    *,
    scenario: Literal["session-handoff", "container-handoff"],
    checkpoint_steps: int,
    baseline: ScenarioLegObservation,
    source: ScenarioLegObservation | None,
    candidate: ScenarioLegObservation,
) -> ScenarioComparison:
    candidate_metrics = candidate.metrics if source is None else source.metrics.plus(candidate.metrics)
    candidate_legs = (candidate.id,) if source is None else (source.id, candidate.id)
    sessions_observed = (
        candidate.agent_sessions >= 2 if source is None else source.agent_sessions + candidate.agent_sessions >= 2
    )
    assertions = {
        "baseline_completed": EvaluationValue(value=baseline.status == "completed"),
        "segments_completed": EvaluationValue(
            value=candidate.status == "completed" and (source is None or source.status == "completed")
        ),
        "new_agent_session_observed": EvaluationValue(value=sessions_observed),
        "continue_prompt_observed": EvaluationValue(
            value=(
                "continue" in candidate.agent_prompts
                and (source is None or (candidate.prompt == "continue" and candidate.workspace_restored))
            )
        ),
        "handoff_scope_preserved": EvaluationValue(value=source is None or source.scope_id == candidate.scope_id),
        "workspace_snapshot_preserved": EvaluationValue(
            value=(
                source is None
                or (
                    source.workspace_snapshot is not None
                    and candidate.workspace_snapshot is not None
                    and source.workspace_snapshot.sha256 == candidate.workspace_snapshot.sha256
                )
            )
        ),
        "usage_complete": EvaluationValue(
            value=_usage_complete(baseline.metrics) and _usage_complete(candidate_metrics),
            reason=(
                f"Baseline usage {baseline.metrics.llm_calls_with_usage}/{baseline.metrics.llm_calls}; "
                f"candidate usage {candidate_metrics.llm_calls_with_usage}/{candidate_metrics.llm_calls}."
            ),
        ),
        "native_outcome_not_degraded": EvaluationValue(
            value=_rewards_not_degraded(baseline.rewards, candidate.rewards),
            reason=f"Baseline rewards {baseline.rewards!r}; candidate rewards {candidate.rewards!r}.",
        ),
    }
    return ScenarioComparison(
        scenario=scenario,
        checkpoint_steps=checkpoint_steps,
        reference_legs=(baseline.id,),
        candidate_legs=candidate_legs,
        assertions=assertions,
        token_delta=candidate_metrics.total_tokens - baseline.metrics.total_tokens,
        llm_call_delta=candidate_metrics.llm_calls - baseline.metrics.llm_calls,
        tool_call_delta=candidate_metrics.tool_calls - baseline.metrics.tool_calls,
    )


def _completed_reuse_comparison(
    cold: ScenarioLegObservation,
    warm: ScenarioLegObservation,
) -> ScenarioComparison:
    assertions = {
        "runs_completed": EvaluationValue(value=cold.status == "completed" and warm.status == "completed"),
        "existing_memory_attached": EvaluationValue(
            value=warm.memory_entries_before > 0,
            reason=f"Warm run started with {warm.memory_entries_before} Memory entries.",
        ),
        "cold_scope_isolated": EvaluationValue(value=cold.scope_id != warm.scope_id),
        "usage_complete": EvaluationValue(
            value=_usage_complete(cold.metrics) and _usage_complete(warm.metrics),
            reason=(
                f"Cold usage {cold.metrics.llm_calls_with_usage}/{cold.metrics.llm_calls}; "
                f"warm usage {warm.metrics.llm_calls_with_usage}/{warm.metrics.llm_calls}."
            ),
        ),
        "native_outcome_not_degraded": EvaluationValue(
            value=_rewards_not_degraded(cold.rewards, warm.rewards),
            reason=f"Cold rewards {cold.rewards!r}; warm rewards {warm.rewards!r}.",
        ),
        "tokens_reduced": EvaluationValue(
            value=warm.metrics.total_tokens < cold.metrics.total_tokens,
            reason=f"Cold {cold.metrics.total_tokens}; warm {warm.metrics.total_tokens}.",
        ),
        "agent_steps_reduced": EvaluationValue(
            value=warm.metrics.llm_calls < cold.metrics.llm_calls,
            reason=f"Cold {cold.metrics.llm_calls}; warm {warm.metrics.llm_calls}.",
        ),
    }
    return ScenarioComparison(
        scenario="completed-reuse",
        reference_legs=(cold.id,),
        candidate_legs=(warm.id,),
        assertions=assertions,
        token_delta=warm.metrics.total_tokens - cold.metrics.total_tokens,
        llm_call_delta=warm.metrics.llm_calls - cold.metrics.llm_calls,
        tool_call_delta=warm.metrics.tool_calls - cold.metrics.tool_calls,
    )


def _usage_complete(metrics: BubMetrics) -> bool:
    return metrics.llm_calls > 0 and metrics.llm_calls_with_usage == metrics.llm_calls


def _rewards_not_degraded(reference: dict[str, float | int], candidate: dict[str, float | int]) -> bool:
    return bool(reference) and all(
        name in candidate and float(candidate[name]) >= float(value) for name, value in reference.items()
    )


def _render_scenario_report(suite: ScenarioSuiteObservation) -> str:
    lines = [
        "# Bub handoff and Memory scenario acceptance",
        "",
        f"- Workload: `{suite.task.id}`",
        f"- Run: `{suite.run_id}`",
        f"- Accepted: `{str(suite.accepted).lower()}`",
        "",
        "## Comparisons",
        "",
    ]
    for comparison in suite.comparisons:
        checkpoint = f" at step {comparison.checkpoint_steps}" if comparison.checkpoint_steps is not None else ""
        lines.append(f"### {comparison.scenario}{checkpoint}")
        lines.append("")
        for name, assertion in comparison.assertions.items():
            status = "PASS" if assertion.value else "FAIL"
            reason = f" — {assertion.reason}" if assertion.reason else ""
            lines.append(f"- [{status}] `{name}`{reason}")
        lines.extend((
            f"- Token delta: `{comparison.token_delta}`",
            f"- LLM call delta: `{comparison.llm_call_delta}`",
            f"- Tool call delta: `{comparison.tool_call_delta}`",
            "",
        ))
    return "\n".join(lines)
