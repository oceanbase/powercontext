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

"""Verify Harbor collect-all and fail-fast behavior with real trials."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, ResourceMode, TaskConfig
from harbor.models.trial.result import StepResult

from .catalog import E2ETask
from .evidence import redact, write_evaluation_report, write_evidence
from .models import CaseEvaluation, EvaluationReport, EvaluationValue
from .report import render_evaluation_summary
from .runner import FailurePolicy, prepare_runtime_task
from .settings import HarnessSettings

DATASET_PATH = Path("e2e/bub/harbor-tasks")
TASK_IDS = ("failure-policy-timeout", "failure-policy-followup")


async def run_failure_acceptance(output_dir: Path, settings: HarnessSettings) -> bool:
    """Run both policies and write one acceptance report."""

    cases = []
    for policy in ("collect-all", "fail-fast"):
        try:
            steps = await _run_policy(policy, output_dir / policy, settings)
            failed = steps.get("failure-policy-timeout-run")
            followup = steps.get("failure-policy-followup-run")
            failure_ok = failed is not None and failed.exception_info is not None and _reward(failed) == 0
            followup_ok = (
                followup is not None and followup.exception_info is None and _reward(followup) == 1
                if policy == "collect-all"
                else followup is None
            )
            failure_reason = _step_summary(failed)
            followup_reason = _step_summary(followup)
        except Exception as exc:
            failure_ok = followup_ok = False
            failure_reason = followup_reason = redact(f"{type(exc).__name__}: {exc}", settings)

        followup_name = "followup_executed" if policy == "collect-all" else "followup_skipped"
        accepted = failure_ok and followup_ok
        cases.append(
            CaseEvaluation(
                name=policy,
                assertions={
                    "failure_observed": EvaluationValue(value=failure_ok, reason=failure_reason),
                    followup_name: EvaluationValue(value=followup_ok, reason=followup_reason),
                },
                labels={"task_outcome": EvaluationValue(value="passed" if accepted else "not_passed")},
            )
        )

    report = EvaluationReport(experiment="e2e:failure-policy", cases=tuple(cases))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_evaluation_report(output_dir / "eval-report.json", report=report, settings=settings)
    write_evidence(
        output_dir / "report.md",
        render_evaluation_summary(report, title="PowerContext Harbor failure-policy acceptance"),
        settings,
    )
    return report.accepted


async def _run_policy(
    policy: FailurePolicy,
    output_dir: Path,
    settings: HarnessSettings,
) -> dict[str, StepResult]:
    runtime_id = f"failure-policy-{policy}-{uuid4().hex[:12]}"
    runtime = prepare_runtime_task(
        _source_tasks(settings),
        output_dir=output_dir,
        settings=settings,
        failure_policy=policy,
        runtime_id=runtime_id,
    )
    result = await (await Job.create(_job_config(runtime_id, output_dir, runtime.task_config))).run()
    if len(result.trial_results) != 1:
        return {}
    return {step.step_name: step for step in result.trial_results[0].step_results or ()}


def _source_tasks(settings: HarnessSettings) -> tuple[E2ETask, ...]:
    root = settings.repository_path() / DATASET_PATH
    return tuple(
        E2ETask.model_validate({
            "schema": "powercontext.e2e-task/v1",
            "id": task_id,
            "categories": ["batch:failure-policy"],
            "dataset": {"path": str(DATASET_PATH), "task_id": task_id, "checksum": HarborTask(root / task_id).checksum},
            "execution": {"type": "bub", "model": False},
            "evaluation": {"probes": [{"id": "policy-fixture", "query": "Run the policy fixture."}]},
        })
        for task_id in TASK_IDS
    )


def _job_config(runtime_id: str, output_dir: Path, task: TaskConfig) -> JobConfig:
    return JobConfig(
        job_name=runtime_id,
        jobs_dir=output_dir / "harbor-jobs",
        quiet=True,
        environment=EnvironmentConfig(
            type=EnvironmentType.DOCKER,
            cpu_enforcement_policy=ResourceMode.IGNORE,
            memory_enforcement_policy=ResourceMode.IGNORE,
        ),
        agents=[AgentConfig(name="oracle")],
        tasks=[task],
    )


def _reward(step: StepResult) -> float | int | None:
    return None if step.verifier_result is None else (step.verifier_result.rewards or {}).get("reward")


def _step_summary(step: StepResult | None) -> str:
    if step is None:
        return "The step was not executed."
    return f"Agent exception: {step.exception_info is not None}; verifier reward: {_reward(step)!r}."
