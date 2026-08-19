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

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import ExceptionInfo, StepResult
from harbor.models.verifier.result import VerifierResult

from powercontext_e2e.catalog import E2ETask, load_tasks
from powercontext_e2e.evaluation import MemoryEvaluator
from powercontext_e2e.models import (
    SHARED_TRIAL_SKIPPED_ERROR,
    HarborTrialObservation,
    MemorySnapshot,
    RunEnvironment,
    TaskObservation,
)
from powercontext_e2e.runner import _source_results, collect_task_artifacts, prepare_runtime_dataset
from powercontext_e2e.settings import HarnessSettings


def _repository() -> Path:
    return Path(__file__).resolve().parents[3]


def _locomo_tasks() -> tuple[E2ETask, ...]:
    return tuple(
        task for task in load_tasks(_repository() / "e2e" / "bub" / "tasks") if "batch:locomo" in task.categories
    )


def test_selected_locomo_tasks_share_one_runtime_with_requested_failure_policy(tmp_path: Path) -> None:
    tasks = _locomo_tasks()
    settings = HarnessSettings(repository=_repository())

    collect_all = prepare_runtime_dataset(
        tasks,
        output_dir=tmp_path,
        settings=settings,
        failure_policy="collect-all",
    )
    runtime_task_id = collect_all.dataset_config.task_names[0]
    runtime_task = HarborTask(collect_all.dataset_config.path / runtime_task_id)
    runtime_step_names = tuple(f"{task.id}-{step}" for task in tasks for step in ("reset", "capture", "recall"))

    assert len(collect_all.dataset_config.task_names) == 1
    assert tuple(step.name for step in runtime_task.config.steps or ()) == runtime_step_names
    assert all(step.min_reward is None for step in runtime_task.config.steps or ())

    fail_fast = prepare_runtime_dataset(
        tasks,
        output_dir=tmp_path,
        settings=settings,
        failure_policy="fail-fast",
    )
    fail_fast_task_id = fail_fast.dataset_config.task_names[0]
    fail_fast_task = HarborTask(fail_fast.dataset_config.path / fail_fast_task_id)
    assert fail_fast_task_id != runtime_task_id
    assert all(step.min_reward == 1.0 for step in fail_fast_task.config.steps or ())


def test_shared_trial_artifacts_stay_with_their_source_task(tmp_path: Path) -> None:
    tasks = _locomo_tasks()[:2]
    prepared = prepare_runtime_dataset(
        tasks,
        output_dir=tmp_path / "runtime",
        settings=HarnessSettings(repository=_repository()),
        failure_policy="collect-all",
    )
    trial_dir = tmp_path / "trial"
    for source in prepared.sources:
        agent_dir = trial_dir / "steps" / f"{source.task.id}-recall" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "powercontext-capture.jsonl").write_text(
            json.dumps({
                "schema": "powercontext.bub-capture-event/v1",
                "recorded_at": "2026-08-19T00:00:00Z",
                "event": "user_prompt",
                "status": "captured",
                "source_id": f"bub-event:{source.task.id}",
            })
            + "\n",
            encoding="utf-8",
        )
    malformed_path = (
        trial_dir / "steps" / f"{prepared.sources[0].task.id}-capture" / "agent" / "powercontext-capture.jsonl"
    )
    malformed_path.parent.mkdir(parents=True)
    malformed_path.write_text("{truncated\n", encoding="utf-8")

    artifacts = collect_task_artifacts(
        prepared.sources,
        trial_dir,
        tasks[0].execution.native_artifact_names,
        HarnessSettings(repository=_repository()),
    )

    assert {task.id: [record.source_id for record in artifacts[task.id].capture_records] for task in tasks} == {
        task.id: [f"bub-event:{task.id}"] for task in tasks
    }
    assert len(artifacts[tasks[0].id].errors) == 1
    assert artifacts[tasks[1].id].errors == ()


def test_collect_all_keeps_later_tasks_after_an_agent_failure(tmp_path: Path) -> None:
    tasks = _locomo_tasks()[:2]
    prepared = prepare_runtime_dataset(
        tasks,
        output_dir=tmp_path,
        settings=HarnessSettings(repository=_repository()),
        failure_policy="collect-all",
    )
    failed_step = prepared.sources[0].runtime_steps[1]
    step_results = tuple(
        StepResult(
            step_name=step,
            verifier_result=VerifierResult(rewards={"reward": 0 if step == failed_step else 1}),
            exception_info=(
                ExceptionInfo(
                    exception_type="AgentError",
                    exception_message="agent failed",
                    exception_traceback="",
                    occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
                )
                if step == failed_step
                else None
            ),
        )
        for source in prepared.sources
        for step in source.runtime_steps
    )

    results = _source_results(prepared.sources, step_results, (), None)

    assert results[tasks[0].id].status == "completed"
    assert results[tasks[0].id].errors == ()
    assert results[tasks[1].id].status == "completed"
    assert len(results[tasks[1].id].steps) == 3


def test_fail_fast_marks_unexecuted_tasks_as_skipped(tmp_path: Path) -> None:
    tasks = _locomo_tasks()[:2]
    prepared = prepare_runtime_dataset(
        tasks,
        output_dir=tmp_path,
        settings=HarnessSettings(repository=_repository()),
        failure_policy="fail-fast",
    )
    failed_step = prepared.sources[0].runtime_steps[0]
    step_results = (
        StepResult(
            step_name=failed_step,
            verifier_result=VerifierResult(rewards={"reward": 0}),
            exception_info=ExceptionInfo(
                exception_type="AgentError",
                exception_message="agent failed",
                exception_traceback="",
                occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
            ),
        ),
    )

    results = _source_results(prepared.sources, step_results, (), None)

    assert results[tasks[0].id].status == "failed"
    assert "did not execute steps" in results[tasks[0].id].errors[0]
    assert results[tasks[1].id].status == "skipped"
    assert results[tasks[1].id].errors == (SHARED_TRIAL_SKIPPED_ERROR,)


def test_skipped_shared_task_has_only_an_execution_result() -> None:
    task = _locomo_tasks()[0]
    recorded_at = datetime(2026, 8, 24, tzinfo=UTC)
    observation = TaskObservation(
        run_id="batch-locomo-test",
        environment=RunEnvironment(
            commit="abcdef0",
            database="sqlite",
            adapter_version="test-adapter",
            adapter_protocol_version="test-protocol",
            started_at=recorded_at,
            finished_at=recorded_at,
        ),
        task=task,
        status="failed",
        errors=(SHARED_TRIAL_SKIPPED_ERROR,),
        harbor=HarborTrialObservation(task_checksum=task.dataset.checksum),
        memory_before=MemorySnapshot(),
        memory_after=MemorySnapshot(),
    )

    report = MemoryEvaluator.evaluate(observation, experiment="e2e:batch:locomo")
    case = report.cases[0]

    assert set(case.assertions) == {"execution_completed"}
    assert case.assertions["execution_completed"].value is False
    assert case.labels["task_outcome"].value == "skipped"
