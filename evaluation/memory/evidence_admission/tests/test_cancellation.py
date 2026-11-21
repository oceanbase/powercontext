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

import asyncio

import pytest

from powercontext_eval_evidence_admission.replay import replay_case
from powercontext_eval_evidence_admission.runner import run_sample


@pytest.mark.parametrize("phase", ["before_read", "projection", "generation"])
@pytest.mark.parametrize("arm", ["complete-projection-v1", "window-reduction-v1", "bounded-fragments-v1"])
def test_real_task_cancellation_never_publishes_memory_or_completed_sample(suite, phase, arm):
    case = next(c for c in suite.cases if c.cancel_phase == phase)
    result = asyncio.run(run_sample(suite, case, arm, 2048, 0))
    assert result.status == result.completeness == "cancelled"
    assert result.memory_mutations == result.published_cursor == 0
    assert result.preparation is result.generation is None
    assert result.input_hashes == result.output_hashes == ()
    assert result.cancellation_seconds is not None and result.cancellation_seconds >= 0
    assert result.materialization_seconds > 0
    if phase == "generation":
        assert result.generation_seconds > 0


def test_caller_cancellation_propagates_instead_of_becoming_fixture_success(small_case):
    async def scenario():
        entered = asyncio.Event()

        async def phase(name):
            entered.set()
            await asyncio.Event().wait()

        async def run():
            async with replay_case(small_case, on_phase=phase):
                raise AssertionError("cancelled read must not yield")

        task = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
