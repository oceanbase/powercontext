from __future__ import annotations

from pathlib import Path

from powercontext_e2e.catalog import load_tasks, select_tasks


def test_workloads_can_be_selected_by_multiple_ids_or_category() -> None:
    repository = Path(__file__).resolve().parents[3]
    tasks = load_tasks(repository / "e2e" / "bub" / "tasks")

    assert [task.id for task in tasks] == [
        "locomo-support-group",
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
        "locomo-support-group",
        "project-database-decision",
    ]
