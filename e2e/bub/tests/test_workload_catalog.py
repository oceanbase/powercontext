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

from powercontext_e2e.catalog import load_tasks, select_tasks
from powercontext_e2e.runner import group_tasks


def test_workloads_can_be_selected_by_multiple_ids_or_category() -> None:
    repository = Path(__file__).resolve().parents[3]
    tasks = load_tasks(repository / "e2e" / "bub" / "tasks")

    assert [task.id for task in tasks] == [
        "locomo-multihop-football",
        "locomo-open-pastries",
        "locomo-support-group",
        "locomo-temporal-banker",
        "project-database-decision",
        "terminal-bench-db-wal-recovery",
    ]
    assert {task.execution.type for task in tasks} == {"bub"}
    assert {task.id for task in tasks if task.execution.model} == {"terminal-bench-db-wal-recovery"}

    selected_ids = select_tasks(
        tasks,
        ids=("locomo-support-group", "terminal-bench-db-wal-recovery"),
    )
    acceptance = select_tasks(tasks, categories=("acceptance",))

    assert [task.id for task in selected_ids] == [
        "locomo-support-group",
        "terminal-bench-db-wal-recovery",
    ]
    assert [task.id for task in acceptance] == [
        "locomo-multihop-football",
        "locomo-open-pastries",
        "locomo-support-group",
        "locomo-temporal-banker",
        "project-database-decision",
    ]
    locomo = tuple(task for task in tasks if task.batch == "locomo")
    assert [task.id for task in locomo] == [
        "locomo-multihop-football",
        "locomo-open-pastries",
        "locomo-support-group",
        "locomo-temporal-banker",
    ]
    assert len(group_tasks(tuple(task for task in acceptance if task.batch == "locomo"))) == 1
    assert group_tasks((next(task for task in locomo if task.id == "locomo-support-group"),))[0].batch is None
