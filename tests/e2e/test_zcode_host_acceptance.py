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

"""Explicitly selected actual CLI + real Server acceptance, with no skip-as-pass."""

import pytest

from .zcode_acceptance.runner import AcceptanceRun


@pytest.mark.zcode_host_acceptance
@pytest.mark.parametrize("repeat", [1, 2])
def test_zcode_controlled_host_acceptance(repeat: int, request: pytest.FixtureRequest) -> None:
    """Independent identities on each repeat; inference fixture, real persistence and native MCP."""
    run = AcceptanceRun(
        request.config.getoption("zcode_acceptance_output"),
        live=False,
        model_config=None,
        generation_env=None,
    )
    run.run()
    print(f"ZCode fixture run {repeat}: {run.run_id}; core scenarios passed (see recorded limitations).")


@pytest.mark.zcode_host_acceptance
@pytest.mark.zcode_live_model
@pytest.mark.parametrize("repeat", [1, 2])
def test_zcode_live_model_acceptance(repeat: int, request: pytest.FixtureRequest) -> None:
    """Live host model and live Generation must both be explicitly configured."""
    run = AcceptanceRun(
        request.config.getoption("zcode_acceptance_output"),
        live=True,
        model_config=request.config.getoption("zcode_model_config"),
        generation_env=request.config.getoption("zcode_generation_env"),
        host_model=request.config.getoption("zcode_host_model"),
    )
    run.run()
    print(f"ZCode live run {repeat}: {run.run_id}; core scenarios passed (see recorded limitations).")
