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

"""Run paired workloads with PowerContext off and on, and report the outcomes."""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import fmean
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

from harbor.job import Job
from harbor.models.trial.result import StepResult
from powercontext.client import PowerContextClient, UnauthorizedResponseError
from powercontext.client.settings import ClientSettings
from powercontext.http import CreateScopeRequest

from .catalog import ContinuationEvaluationSpec, E2ETask, TaskOutcomeComparisonSpec, is_paired
from .evidence import redact, write_evidence
from .hosts import HostAdapter, host_adapter
from .models import (
    Arm,
    ArmOutcome,
    ArmSummary,
    HarborTrialObservation,
    MetricSummary,
    PairedAgent,
    PairedArmObservation,
    PairedReport,
    PairedSummary,
    PairedTaskSummary,
    ServerUsageSummary,
    SessionSnapshot,
    StepObservation,
    StepSummary,
)
from .report import render_paired_report
from .runner import (
    _harbor_observation,
    _job_config,
    _load_harbor_task,
    _load_source_task,
    _powercontext_client,
    _run_environment,
    require_runtime_models,
)
from .sessions import SessionRecorder
from .settings import HarnessSettings
from .stats import bootstrap_mean_interval, wilson_interval

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from harbor.models.job.result import JobResult
    from powercontext.client import PowerContextClient

SCORES: dict[ArmOutcome, int] = {"passed": 1, "failed": 0, "timeout": 0}
AGENT_TIMEOUT = "AgentTimeoutError"


class UnauthenticatedServerError(RuntimeError):
    """Report a Server that answers without a token, which an OFF agent that reaches it could read."""

    def __init__(self) -> None:
        super().__init__(
            "The PowerContext Server answers unauthenticated requests, so an OFF agent that reaches it could read the "
            "ON arm's Memory. Start it with POWERCONTEXT_SERVER_ACCESS_MODE=enforced and POWERCONTEXT_SERVER_AUTH_TOKEN, "
            "and give the harness the same token as POWERCONTEXT_CLIENT_API_TOKEN"
        )


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

    Paired workloads are host-neutral, so the host is chosen for the run rather than by each manifest.
    """

    if not tasks or trials < 1:
        raise ValueError("At least one paired workload and one trial are required")  # noqa: TRY003
    adapter = host_adapter(host)
    scored = {task.id: scored_session(task, settings) for task in tasks}
    require_runtime_models(tasks, adapter)

    observations: list[PairedArmObservation] = []
    async with _powercontext_client() as client:
        await client.get_readiness()
        await require_authenticated_server()
        if not (await client.get_capabilities()).memory_extraction:
            raise MemoryExtractionUnavailableError
        for task in tasks:
            for trial in range(1, trials + 1):
                order: tuple[Arm, Arm] = ("off", "on") if trial % 2 else ("on", "off")
                mismatch = None
                for position, arm in enumerate(order, start=1):
                    arm_dir = output_dir / task.id / f"trial-{trial}" / arm
                    observation, mismatch = await _run_arm(
                        client,
                        task,
                        host=adapter,
                        trial=trial,
                        arm=arm,
                        position=position,
                        scored=scored[task.id],
                        output_dir=arm_dir,
                        settings=settings,
                    )
                    write_evidence(
                        arm_dir / "observation.json",
                        observation.model_dump_json(by_alias=True, indent=2) + "\n",
                        settings,
                    )
                    observations.append(observation)
                    if mismatch is not None:
                        break
                if mismatch is not None:
                    # The manifest does not describe the task Harbor ran; more trials would only repeat the error.
                    break

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


async def require_authenticated_server() -> None:
    """Refuse a Server that lists its Scopes to a client without a token.

    Both arms' containers can reach the Server, so only authentication keeps the OFF arm out of the ON arm's Memory;
    the token goes to the harness Client and the ON arm's integration only.
    """

    settings = ClientSettings()
    async with PowerContextClient(settings.server_url, timeout=settings.timeout) as anonymous:
        try:
            await anonymous.list_scopes()
        except UnauthorizedResponseError:
            return
    raise UnauthenticatedServerError


class ScoredSession(NamedTuple):
    """The zero-based agent session whose reward decides an arm, and the Harbor step that holds the reward.

    A continuation workload is scored by its recall step's own reward. A task-outcome workload has one session and
    is scored by the trial's reward, so it names no step.
    """

    session: int
    step: str | None


def scored_session(task: E2ETask, settings: HarnessSettings) -> ScoredSession:
    if not is_paired(task):
        raise TypeError(f"Workload {task.id!r} is not an OFF/ON comparison workload")  # noqa: TRY003
    evaluation = task.evaluation
    if isinstance(evaluation, ContinuationEvaluationSpec):
        return ScoredSession(recall_session_index(task, settings), evaluation.recall_step)
    if isinstance(evaluation, TaskOutcomeComparisonSpec):
        # A registry task is known only once Harbor downloads it, so the run also checks its step count afterwards.
        if task.dataset.path is not None and _load_harbor_task(task, settings.repository_path()).config.steps:
            raise ValueError(f"Workload {task.id!r} has Harbor steps; a task-outcome workload runs one session")  # noqa: TRY003
        return ScoredSession(0, None)
    raise AssertionError(f"Unhandled paired evaluation {type(evaluation).__name__}")  # noqa: TRY003


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
    scored: ScoredSession,
    output_dir: Path,
    settings: HarnessSettings,
) -> tuple[PairedArmObservation, str | None]:
    """Run one arm and return its observation, with the reason when Harbor's task is not the manifest's."""

    run_id = f"{task.id}-t{trial}-{arm}-{uuid4().hex[:12]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    scope_id: str | None = None
    recorder: SessionRecorder | None = None
    harbor = HarborTrialObservation()
    step_results: tuple[StepResult, ...] = ()
    agent_steps: tuple[StepResult, ...] = ()
    errors: list[str] = []
    mismatch: str | None = None
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
            recorder = SessionRecorder(client, scope_id, final_session=scored.session)
            job.on_agent_ended(recorder)
        result = await job.run()
        harbor, step_results, _ = _harbor_observation(result, settings)
        agent_steps = step_results or single_session_step(result)
        mismatch = checksum_failure(task, harbor)
        if mismatch is None and scored.step is None and step_results:
            mismatch = (
                f"Workload {task.id!r} has {len(step_results)} Harbor step(s); a task-outcome workload runs one "
                "session without steps"
            )
        if mismatch is not None:
            errors.append(mismatch)
    except Exception as exc:
        errors.append(redact(f"{type(exc).__name__}: {exc}", settings))

    sessions = () if recorder is None else tuple(recorder.snapshots)
    treatment = (
        (
            *(redact(failure, settings) for failure in (recorder.failures if recorder is not None else ())),
            *treatment_failures(sessions, scored.session),
        )
        if arm == "on"
        else ()
    )
    observation = PairedArmObservation(
        run_id=run_id,
        task_id=task.id,
        trial=trial,
        arm=arm,
        position=position,
        environment=_run_environment(task, started_at, settings, host),
        scope_id=scope_id,
        harbor=harbor,
        step_rewards=step_rewards(step_results),
        steps=step_observations(agent_steps),
        outcome=arm_outcome(
            step_results,
            harbor,
            scored_step=scored.step,
            harness_failed=bool(errors),
            treatment_failures=treatment,
        ),
        errors=tuple(errors),
        sessions=sessions,
        treatment_failures=treatment,
    )
    return observation, mismatch


def checksum_failure(task: E2ETask, harbor: HarborTrialObservation) -> str | None:
    """Explain a run whose Harbor task differs from the one the manifest pins, or return nothing.

    A local task is checked before it runs. Harbor downloads a registry task itself, so its checksum is known only
    from the trial result.
    """

    # Paired runs never aggregate tasks into a runtime task, whose checksum would differ from every manifest's.
    if harbor.task_checksum is None or harbor.task_checksum == task.dataset.checksum:
        return None
    return f"Harbor ran task checksum {harbor.task_checksum}, not the manifest's {task.dataset.checksum}"


def arm_outcome(
    step_results: Sequence[StepResult],
    harbor: HarborTrialObservation,
    *,
    scored_step: str | None,
    harness_failed: bool,
    treatment_failures: Sequence[str],
) -> ArmOutcome:
    """Classify one arm from Harbor's results, scoring it by the scored step's own reward or the trial's."""

    exception_types = tuple(
        name
        for name in (
            *(step.exception_info.exception_type for step in step_results if step.exception_info is not None),
            harbor.exception_type,
        )
        if name is not None
    )
    if scored_step is None:
        trial_reward = harbor.rewards.get("reward")
        reward = None if trial_reward is None else float(trial_reward)
    else:
        reward = step_rewards(step_results).get(scored_step)
    return classify_outcome(
        harness_failed=harness_failed,
        exception_types=exception_types,
        treatment_failures=treatment_failures,
        reward=reward,
    )


def step_rewards(step_results: Sequence[StepResult]) -> dict[str, float]:
    """Return each step's own reward.

    A continuation workload's recall step reward decides its arm. Harbor's trial reward follows the task's
    multi-step strategy and can average in earlier steps, whose small jobs are unrelated to recall.
    """

    return {
        step.step_name: float(step.verifier_result.rewards["reward"])
        for step in step_results
        if step.verifier_result is not None
        and step.verifier_result.rewards
        and "reward" in step.verifier_result.rewards
    }


def step_observations(step_results: Sequence[StepResult]) -> tuple[StepObservation, ...]:
    """Record each step's agent time and the host's own token and cost figures, as Harbor reports them.

    Harbor's installed agents read usage from the host's own output, so a host that reports nothing leaves the
    figures absent rather than zero.
    """

    observations: list[StepObservation] = []
    for step in step_results:
        timing = step.agent_execution
        seconds = None
        if timing is not None and timing.started_at is not None and timing.finished_at is not None:
            seconds = max((timing.finished_at - timing.started_at).total_seconds(), 0.0)
        context = step.agent_result
        observations.append(
            StepObservation(
                name=step.step_name,
                seconds=seconds,
                input_tokens=None if context is None else context.n_input_tokens,
                cache_tokens=None if context is None else context.n_cache_tokens,
                output_tokens=None if context is None else context.n_output_tokens,
                cost_usd=None if context is None else context.cost_usd,
            )
        )
    return tuple(observations)


def single_session_step(result: JobResult) -> tuple[StepResult, ...]:
    """Return a single-step trial's agent session as the step ``task``, or nothing when the trial has steps or no session.

    Harbor records a multi-step trial's agent time and usage on each step and a single-step trial's on the trial
    itself, so a task-outcome workload's one session is reported under the step name ``task``.
    """

    if not result.trial_results:
        return ()
    trial = result.trial_results[0]
    if trial.step_results or (trial.agent_result is None and trial.agent_execution is None):
        return ()
    return (StepResult(step_name="task", agent_result=trial.agent_result, agent_execution=trial.agent_execution),)


def treatment_failures(sessions: Sequence[SessionSnapshot], recall_session: int) -> tuple[str, ...]:
    """Explain why an ON run did not receive PowerContext's treatment, or return nothing when it did.

    ``recall_session`` is the scored session: a continuation workload's recall session, or 0 for a single-session
    workload. The treatment is the integration capturing Sources and asking PowerContext for context during that
    session: for a continuation workload, Sources from the earlier sessions; for a single-session workload, Sources
    from the session itself. Whether a flush creates Memory and whether recall returns content are PowerContext's own
    behavior under that treatment, so they are recorded in the snapshots but do not decide whether a run counts.
    """

    by_session = {snapshot.session: snapshot for snapshot in sessions}
    recall = by_session.get(recall_session)
    before = by_session.get(recall_session - 1) if recall_session else None
    if recall is None or (recall_session and before is None):
        return ("The Server was not observed after every session",)
    # A single-session workload captures and asks within the one session the Server was observed after.
    single = before is None
    captured = recall.sources if single else before.sources
    asked = recall.preparations - (0 if single else before.preparations)
    failures: list[str] = []
    if captured == 0:
        failures.append(
            "No Sources were captured during the session"
            if single
            else "No Sources were captured before the recall session"
        )
    if asked <= 0:
        failures.append(
            "PowerContext was not asked for context during the " + ("session" if single else "recall session")
        )
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
        delta_interval=bootstrap_mean_interval(deltas),
        on_better=sum(delta > 0 for delta in deltas),
        off_better=sum(delta < 0 for delta in deltas),
        tied=deltas.count(0),
    )


def _arm_summary(observations: Sequence[PairedArmObservation]) -> ArmSummary:
    """Summarize one arm; the metrics cover the same scored runs as the success rate, timeouts included."""

    outcomes = [observation.outcome for observation in observations]
    scored = [observation for observation in observations if observation.outcome in SCORES]
    passed = outcomes.count("passed")
    return ArmSummary(
        scored=len(scored),
        passed=passed,
        timeouts=outcomes.count("timeout"),
        errors=outcomes.count("error"),
        integration_failures=outcomes.count("integration_failed"),
        success_rate=passed / len(scored) if scored else None,
        success_rate_interval=wilson_interval(passed, len(scored)),
        steps=_step_summaries(scored),
        server=_server_usage(scored),
    )


def _step_summaries(scored: Sequence[PairedArmObservation]) -> dict[str, StepSummary]:
    summaries: dict[str, StepSummary] = {}
    for name in dict.fromkeys(step.name for observation in scored for step in observation.steps):
        steps = [step for observation in scored for step in observation.steps if step.name == name]
        summaries[name] = StepSummary(
            runs=len(steps),
            seconds=_metric(step.seconds for step in steps),
            input_tokens=_metric(step.input_tokens for step in steps),
            cache_tokens=_metric(step.cache_tokens for step in steps),
            output_tokens=_metric(step.output_tokens for step in steps),
            cost_usd=_metric(step.cost_usd for step in steps),
        )
    return summaries


def _metric(values: Iterable[float | int | None]) -> MetricSummary | None:
    known = [float(value) for value in values if value is not None]
    if not known:
        return None
    return MetricSummary(runs=len(known), mean=fmean(known), min=min(known), max=max(known))


def _server_usage(scored: Sequence[PairedArmObservation]) -> ServerUsageSummary | None:
    finals = [max(observation.sessions, key=lambda s: s.session) for observation in scored if observation.sessions]
    if not finals:
        return None
    return ServerUsageSummary(
        runs=len(finals),
        generation_requests=fmean(snapshot.generation_requests for snapshot in finals),
        generation_input_tokens=_metric(snapshot.generation_input_tokens for snapshot in finals),
        generation_output_tokens=_metric(snapshot.generation_output_tokens for snapshot in finals),
        embedding_requests=fmean(snapshot.embedding_requests for snapshot in finals),
        embedding_input_tokens=_metric(snapshot.embedding_input_tokens for snapshot in finals),
        recalled_tokens=fmean(snapshot.recalled_tokens for snapshot in finals),
    )
