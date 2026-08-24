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
from pathlib import Path

import pytest
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import StepResult
from harbor.models.verifier.result import VerifierResult

from powercontext_e2e.catalog import E2ETask, load_tasks
from powercontext_e2e.models import HarborTrialObservation
from powercontext_e2e.runner import (
    SourceResult,
    SourceTask,
    _load_task_artifacts,
    _source_harbor_observation,
    prepare_runtime_task,
)
from powercontext_e2e.settings import HarnessSettings

REPOSITORY = Path(__file__).resolve().parents[3]


def _locomo_tasks() -> tuple[E2ETask, ...]:
    return tuple(task for task in load_tasks(REPOSITORY / "e2e" / "bub" / "tasks") if "batch:locomo" in task.categories)


def test_selected_locomo_tasks_share_one_runtime_with_requested_failure_policy(tmp_path: Path) -> None:
    tasks = _locomo_tasks()
    settings = HarnessSettings(repository=REPOSITORY)

    collect_all = prepare_runtime_task(
        tasks,
        output_dir=tmp_path,
        settings=settings,
        failure_policy="collect-all",
    )
    assert collect_all.task_config.path is not None
    runtime_task = HarborTask(collect_all.task_config.path)
    runtime_step_names = tuple(f"{task.id}-{step}" for task in tasks for step in ("capture", "recall"))

    assert tuple(step.name for step in runtime_task.config.steps or ()) == runtime_step_names
    assert all(step.min_reward is None for step in runtime_task.config.steps or ())

    fail_fast = prepare_runtime_task(
        tasks,
        output_dir=tmp_path,
        settings=settings,
        failure_policy="fail-fast",
    )
    assert fail_fast.task_config.path is not None
    fail_fast_task = HarborTask(fail_fast.task_config.path)
    assert fail_fast.task_config.path != collect_all.task_config.path
    assert all(step.min_reward == 1.0 for step in fail_fast_task.config.steps or ())


def test_shared_trial_artifacts_stay_with_their_source_task(tmp_path: Path) -> None:
    tasks = _locomo_tasks()[:2]
    prepared = prepare_runtime_task(
        tasks,
        output_dir=tmp_path / "runtime",
        settings=HarnessSettings(repository=REPOSITORY),
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
    settings = HarnessSettings(repository=REPOSITORY)
    artifacts = {
        source.task.id: _load_task_artifacts(
            trial_dir,
            source.task.execution.native_artifact_names,
            settings,
            step_names=source.runtime_steps,
        )
        for source in prepared.sources
    }

    assert {task.id: [record.source_id for record in artifacts[task.id].capture_records] for task in tasks} == {
        task.id: [f"bub-event:{task.id}"] for task in tasks
    }


@pytest.mark.parametrize(
    ("task_id", "step_rewards", "expected"),
    [
        (
            "locomo-support-group",
            ({"reward": 1, "detail": 1}, {"reward": 0}),
            {"reward": 0.5, "detail": 0.5},
        ),
        ("project-database-decision", ({"reward": 1}, {"reward": 0}), {"reward": 0}),
    ],
)
def test_batch_source_evidence_preserves_harbor_reward_strategy(
    task_id: str,
    step_rewards: tuple[dict[str, int], ...],
    expected: dict[str, float | int],
) -> None:
    task = load_tasks(REPOSITORY / "e2e" / "bub" / "tasks" / f"{task_id}.yaml")[0]
    assert task.dataset.path is not None
    harbor_task = HarborTask(REPOSITORY / task.dataset.path / task.dataset.task_id)
    source = SourceTask(task, harbor_task, tuple(step.name for step in harbor_task.config.steps or ()))
    steps = tuple(
        StepResult(step_name=name, verifier_result=VerifierResult(rewards=rewards))
        for name, rewards in zip(source.runtime_steps, step_rewards, strict=True)
    )
    source_harbor = _source_harbor_observation(
        HarborTrialObservation(),
        source,
        SourceResult("completed", (), steps),
        HarnessSettings(repository=REPOSITORY),
    )

    assert source_harbor.rewards == expected
