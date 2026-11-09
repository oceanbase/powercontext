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

from pathlib import Path

import pytest
from harbor.models.task.task import Task as HarborTask

from powercontext_e2e.catalog import E2ETask, load_tasks

_REPOSITORY = Path(__file__).resolve().parents[3]
_LOCAL_TASKS = tuple(
    task
    for catalog in ("tasks", "paired-tasks")
    for task in load_tasks(_REPOSITORY / "e2e" / "bub" / catalog)
    if task.dataset.path == Path("e2e/bub/harbor-tasks")
)


@pytest.mark.parametrize("task", _LOCAL_TASKS, ids=lambda task: task.id)
def test_shipped_workload_pins_its_current_harbor_task(task: E2ETask) -> None:
    assert task.dataset.path is not None
    source = HarborTask(_REPOSITORY / task.dataset.path / task.dataset.task_id)
    assert source.checksum == task.dataset.checksum
