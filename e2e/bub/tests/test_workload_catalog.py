from __future__ import annotations

from pathlib import Path

import yaml

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


def test_task_overlay_forwards_the_proxy_to_agent_and_verifier_phases() -> None:
    repository = Path(__file__).resolve().parents[3]
    overlay = yaml.safe_load((repository / "e2e" / "bub" / "harbor-task-overlay.yaml").read_text())
    environment = overlay["services"]["main"]["environment"]

    proxy_reference = "${POWERCONTEXT_E2E_AGENT_PROXY_URL:-}"
    for name in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
        assert environment[name] == proxy_reference
    assert "host-gateway" in environment["NO_PROXY"]
    assert "powercontext" in environment["NO_PROXY"]
    assert environment["no_proxy"] == environment["NO_PROXY"]
