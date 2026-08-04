"""Explicitly enabled real Experience/Skill acceptance journeys."""

from pathlib import Path

import pytest

from tests.e2e.real_experience_skill.harness import main

pytestmark = pytest.mark.real_e2e


@pytest.mark.parametrize("mode", ("baseline", "configured"))
def test_real_codex_experience_skill_journey(mode: str, pytestconfig: pytest.Config) -> None:
    selected_mode = pytestconfig.getoption("real_e2e_mode")
    if selected_mode not in {mode, "all"}:
        pytest.skip(f"real E2E mode is {selected_mode}")

    arguments = [
        "--codex-timeout",
        str(pytestconfig.getoption("real_codex_timeout")),
        "--purge-existing",
        "--cleanup",
    ]
    if mode == "configured":
        env_file = Path(pytestconfig.getoption("real_e2e_env_file"))
        arguments.extend(("--configured", "--env-file", str(env_file)))

    assert main(arguments) == 0
