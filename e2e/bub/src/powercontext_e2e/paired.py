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

"""Run continuation workloads with PowerContext off and on, and report the paired outcomes."""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import fmean
from typing import TYPE_CHECKING
from uuid import uuid4

from harbor.job import Job
from powercontext.http import CreateScopeRequest

from .catalog import ContinuationEvaluationSpec, E2ETask
from .evidence import redact, write_evidence
from .hosts import HostAdapter, host_adapter
from .models import (
    Arm,
    ArmOutcome,
    ArmSummary,
    HarborTrialObservation,
    PairedAgent,
    PairedArmObservation,
    PairedReport,
    PairedSummary,
    PairedTaskSummary,
    SessionSnapshot,
)
from .report import render_paired_report
from .runner import (
    _harbor_observation,
    _job_config,
    _load_source_task,
    _powercontext_client,
    _run_environment,
    require_runtime_models,
)
from .sessions import SessionRecorder
from .settings import HarnessSettings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from harbor.models.trial.result import StepResult
    from powercontext.client import PowerContextClient

SCORES: dict[ArmOutcome, int] = {"passed": 1, "failed": 0, "timeout": 0}
AGENT_TIMEOUT = "AgentTimeoutError"


class MemoryExtractionUnavailableError(RuntimeError):
    """Report a Server that cannot turn captured Sources into Memory, which the ON arm depends on."""

    def __init__(self) -> None:
        super().__init__("The PowerContext Server does not report memory_extraction; the ON arm cannot recall")


async def run_paired(
    tasks: tuple[E2ETask, ...],
    *,
    output_dir: Path,
    settings: HarnessSettings,
    trials: int,
    host: str = "bub",
) -> PairedReport:
    """Run every task ``trials`` times per arm on ``host``, alternating which arm goes first, and write the report.

    Continuation workloads are host-neutral, so the host is chosen for the run rather than by each manifest.
    """

    if not tasks or trials < 1:
        raise ValueError("At least one continuation workload and one trial are required")  # noqa: TRY003
    adapter = host_adapter(host)
    recall_sessions = {task.id: recall_session_index(task, settings) for task in tasks}
    recall_steps = {task.id: _continuation(task).recall_step for task in tasks}
    require_runtime_models(tasks, adapter)

    observations: list[PairedArmObservation] = []
    async with _powercontext_client() as client:
        await client.get_readiness()
        if not (await client.get_capabilities()).memory_extraction:
            raise MemoryExtractionUnavailableError
        for task in tasks:
            for trial in range(1, trials + 1):
                order: tuple[Arm, Arm] = ("off", "on") if trial % 2 else ("on", "off")
                for position, arm in enumerate(order, start=1):
                    arm_dir = output_dir / task.id / f"trial-{trial}" / arm
                    observation = await _run_arm(
                        client,
                        task,
                        host=adapter,
                        trial=trial,
                        arm=arm,
                        position=position,
                        recall_session=recall_sessions[task.id],
                        recall_step=recall_steps[task.id],
                        output_dir=arm_dir,
                        settings=settings,
                    )
                    write_evidence(
                        arm_dir / "observation.json",
                        observation.model_dump_json(by_alias=True, indent=2) + "\n",
                        settings,
                    )
                    observations.append(observation)

    report = summarize(observations, trials=trials, agent=_paired_agent(adapter))
    write_evidence(output_dir / "paired-report.json", report.model_dump_json(by_alias=True, indent=2) + "\n", settings)
    write_evidence(output_dir / "report.md", render_paired_report(report), settings)
    return report


def _paired_agent(host: HostAdapter) -> PairedAgent:
    return PairedAgent(
        host=host.name,
        version=host.version,
        model=host.agent_model(),
        settings=host.agent_settings(),
    )


def recall_session_index(task: E2ETask, settings: HarnessSettings) -> int:
    """Return the zero-based agent session that answers from earlier sessions."""

    evaluation = _continuation(task)
    source = _load_source_task(task, settings.repository_path())
    steps = source.source_steps
    if len(steps) < 2 or steps[-1] != evaluation.recall_step:
        raise ValueError(  # noqa: TRY003
            f"Workload {task.id!r} must end with its recall step {evaluation.recall_step!r} after an earlier session"
        )
    # Harbor skips the remaining steps when a step scores below its min_reward, so an earlier session's unrelated
    # job could keep the recall session from running at all.
    if gated := [step.name for step in (source.harbor_task.config.steps or ())[:-1] if step.min_reward is not None]:
        raise ValueError(  # noqa: TRY003
            f"Workload {task.id!r} sets min_reward on {gated!r}, which could stop its recall step from running"
        )
    return len(steps) - 1


def _continuation(task: E2ETask) -> ContinuationEvaluationSpec:
    if not isinstance(task.evaluation, ContinuationEvaluationSpec):
        raise TypeError(f"Workload {task.id!r} is not an OFF/ON continuation workload")  # noqa: TRY003
    return task.evaluation


async def _run_arm(
    client: PowerContextClient,
    task: E2ETask,
    *,
    host: HostAdapter,
    trial: int,
    arm: Arm,
    position: int,
    recall_session: int,
    recall_step: str,
    output_dir: Path,
    settings: HarnessSettings,
) -> PairedArmObservation:
    run_id = f"{task.id}-t{trial}-{arm}-{uuid4().hex[:12]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    scope_id: str | None = None
    recorder: SessionRecorder | None = None
    harbor = HarborTrialObservation()
    step_results: tuple[StepResult, ...] = ()
    errors: list[str] = []
    try:
        if arm == "on":
            scope_id = (
                await client.create_scope(
                    CreateScopeRequest(
                        title=f"E2E paired workload: {task.id}",
                        summary=f"Isolated Scope for the ON arm of {task.id}, trial {trial}, in E2E run {run_id}.",
                        idempotency_key=f"e2e:{run_id}:{task.id}",
                    )
                )
            ).scope_id
        job = await Job.create(_job_config(task, run_id, scope_id, output_dir, settings, host=host))
        if scope_id is not None:
            recorder = SessionRecorder(client, scope_id, final_session=recall_session)
            job.on_agent_ended(recorder)
        harbor, step_results, _ = _harbor_observation(await job.run(), settings)
    except Exception as exc:
        errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

    sessions = () if recorder is None else tuple(recorder.snapshots)
    treatment = (
        (
            *(redact(failure, settings) for failure in (recorder.failures if recorder is not None else ())),
            *treatment_failures(sessions, recall_session),
        )
        if arm == "on"
        else ()
    )
    return PairedArmObservation(
        run_id=run_id,
        task_id=task.id,
        trial=trial,
        arm=arm,
        position=position,
        environment=_run_environment(task, started_at, settings, host),
        scope_id=scope_id,
        harbor=harbor,
        step_rewards=step_rewards(step_results),
        outcome=arm_outcome(
            step_results,
            harbor,
            recall_step=recall_step,
            harness_failed=bool(errors),
            treatment_failures=treatment,
        ),
        errors=tuple(errors),
        sessions=sessions,
        treatment_failures=treatment,
    )


def arm_outcome(
    step_results: Sequence[StepResult],
    harbor: HarborTrialObservation,
    *,
    recall_step: str,
    harness_failed: bool,
    treatment_failures: Sequence[str],
) -> ArmOutcome:
    """Classify one arm from Harbor's results, scoring it by the recall step's own reward."""

    exception_types = tuple(
        name
        for name in (
            *(step.exception_info.exception_type for step in step_results if step.exception_info is not None),
            harbor.exception_type,
        )
        if name is not None
    )
    return classify_outcome(
        harness_failed=harness_failed,
        exception_types=exception_types,
        treatment_failures=treatment_failures,
        reward=step_rewards(step_results).get(recall_step),
    )


def step_rewards(step_results: Sequence[StepResult]) -> dict[str, float]:
    """Return each step's own reward.

    The recall step's reward decides an arm. Harbor's trial reward follows the task's multi-step strategy and can
    average in earlier steps, whose small jobs are unrelated to recall.
    """

    return {
        step.step_name: float(step.verifier_result.rewards["reward"])
        for step in step_results
        if step.verifier_result is not None
        and step.verifier_result.rewards
        and "reward" in step.verifier_result.rewards
    }


def treatment_failures(sessions: Sequence[SessionSnapshot], recall_session: int) -> tuple[str, ...]:
    """Explain why an ON run did not receive PowerContext's treatment, or return nothing when it did.

    The treatment is the integration capturing earlier sessions and asking PowerContext for context during the recall
    session. Whether a flush creates Memory and whether recall returns content are PowerContext's own behavior under
    that treatment, so they are recorded in the snapshots but do not decide whether a run counts.
    """

    by_session = {snapshot.session: snapshot for snapshot in sessions}
    before = by_session.get(recall_session - 1)
    recall = by_session.get(recall_session)
    if before is None or recall is None:
        return ("The Server was not observed after every session",)
    failures: list[str] = []
    if before.sources == 0:
        failures.append("No Sources were captured before the recall session")
    if recall.preparations <= before.preparations:
        failures.append("PowerContext was not asked for context during the recall session")
    return tuple(failures)


def classify_outcome(
    *,
    harness_failed: bool,
    exception_types: Sequence[str],
    treatment_failures: Sequence[str],
    reward: float | None,
) -> ArmOutcome:
    """Classify one arm run.

    An agent timeout counts as a failed attempt in both arms. Harness and infrastructure errors, and ON runs that did
    not receive PowerContext's treatment, are not measurements of the task, so they are counted separately and left
    out of the success rate and the paired difference.
    """

    if harness_failed:
        return "error"
    if AGENT_TIMEOUT in exception_types:
        return "timeout"
    if exception_types:
        return "error"
    if treatment_failures:
        return "integration_failed"
    return "passed" if reward is not None and reward >= 1 else "failed"


def summarize(observations: Sequence[PairedArmObservation], *, trials: int, agent: PairedAgent) -> PairedReport:
    task_ids = tuple(dict.fromkeys(observation.task_id for observation in observations))
    return PairedReport(
        experiment=f"e2e:paired:{agent.host}:" + ",".join(task_ids),
        agent=agent,
        trials=trials,
        tasks=tuple(
            PairedTaskSummary(
                task_id=task_id,
                **dict(_summary([o for o in observations if o.task_id == task_id])),
            )
            for task_id in task_ids
        ),
        total=_summary(observations),
    )


def _summary(observations: Sequence[PairedArmObservation]) -> PairedSummary:
    scores = {
        (observation.task_id, observation.trial, observation.arm): SCORES[observation.outcome]
        for observation in observations
        if observation.outcome in SCORES
    }
    deltas = [
        scores[task_id, trial, "on"] - scores[task_id, trial, "off"]
        for task_id, trial in dict.fromkeys((o.task_id, o.trial) for o in observations)
        if (task_id, trial, "on") in scores and (task_id, trial, "off") in scores
    ]
    return PairedSummary(
        off=_arm_summary([o for o in observations if o.arm == "off"]),
        on=_arm_summary([o for o in observations if o.arm == "on"]),
        pairs=len(deltas),
        mean_delta=fmean(deltas) if deltas else None,
    )


def _arm_summary(observations: Sequence[PairedArmObservation]) -> ArmSummary:
    outcomes = [observation.outcome for observation in observations]
    return ArmSummary(
        scored=sum(outcome in SCORES for outcome in outcomes),
        passed=outcomes.count("passed"),
        timeouts=outcomes.count("timeout"),
        errors=outcomes.count("error"),
        integration_failures=outcomes.count("integration_failed"),
    )
