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
