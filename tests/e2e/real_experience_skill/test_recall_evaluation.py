"""Explicitly enabled real-Codex approved Experience recall evaluation."""

import pytest

from tests.e2e.real_experience_skill.recall_evaluation import main

pytestmark = pytest.mark.real_e2e


def test_approved_experience_improves_next_coding_task_success(pytestconfig: pytest.Config) -> None:
    if pytestconfig.getoption("real_e2e_mode") not in {"baseline", "all"}:
        pytest.skip("approved Experience recall evaluation runs in baseline mode")

    assert (
        main([
            "--timeout",
            str(pytestconfig.getoption("real_codex_timeout")),
            "--cleanup",
        ])
        == 0
    )
