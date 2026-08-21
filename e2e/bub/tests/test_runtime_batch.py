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

from harbor.models.task.task import Task as HarborTask

from powercontext_e2e.catalog import E2ETask, load_tasks
from powercontext_e2e.runner import collect_task_artifacts, prepare_runtime_dataset
from powercontext_e2e.settings import HarnessSettings


def _repository() -> Path:
    return Path(__file__).resolve().parents[3]


def _locomo_tasks() -> tuple[E2ETask, ...]:
    return tuple(task for task in load_tasks(_repository() / "e2e" / "bub" / "tasks") if task.batch == "locomo")


def test_selected_locomo_tasks_form_one_runtime_harbor_task(tmp_path: Path) -> None:
    tasks = _locomo_tasks()
    settings = HarnessSettings(repository=_repository())

    collect_all = prepare_runtime_dataset(
        tasks,
        output_dir=tmp_path / "collect-all",
        settings=settings,
        failure_policy="collect-all",
    )
    runtime_task = HarborTask(collect_all.dataset_config.path / "batch-locomo")
    runtime_steps_dir = runtime_task.task_dir / "steps"
    runtime_step_names = tuple(f"{task.id}-{step}" for task in tasks for step in ("reset", "capture", "recall"))

    assert collect_all.dataset_config.task_names == ["batch-locomo"]
    assert tuple(step.name for step in runtime_task.config.steps or ()) == runtime_step_names
    assert {path.name for path in runtime_steps_dir.iterdir()} == set(runtime_step_names)
    assert all((runtime_steps_dir / step / "instruction.md").is_file() for step in runtime_step_names)
    assert [source.task for source in collect_all.sources] == list(tasks)
    assert runtime_task.checksum == collect_all.runtime_checksum
    assert all(step.min_reward is None for step in runtime_task.config.steps or ())

    fail_fast = prepare_runtime_dataset(
        tasks,
        output_dir=tmp_path / "fail-fast",
        settings=settings,
        failure_policy="fail-fast",
    )
    fail_fast_task = HarborTask(fail_fast.dataset_config.path / "batch-locomo")
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

    artifacts = collect_task_artifacts(
        prepared.sources,
        trial_dir,
        tasks[0].execution.native_artifact_names,
        HarnessSettings(repository=_repository()),
    )

    assert {task.id: [record.source_id for record in artifacts[task.id].capture_records] for task in tasks} == {
        task.id: [f"bub-event:{task.id}"] for task in tasks
    }
