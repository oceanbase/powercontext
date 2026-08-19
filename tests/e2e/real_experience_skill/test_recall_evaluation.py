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
