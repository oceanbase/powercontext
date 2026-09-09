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

"""DESIGN-4's two independent gates; synthetic verdicts are not real QA evidence."""

from dataclasses import replace

import pytest
from powercontext_datus.report import CaseVerdict, summarize


def roster(correct_steps=2, failed_steps=(2, 2, 2, 2)):
    ids = [str(index) for index in range(46)]
    cases = [
        CaseVerdict(
            task_id, index < 42, correct_steps if index < 42 else failed_steps[index - 42], True, True, True, True, True
        )
        for index, task_id in enumerate(ids)
    ]
    return ids, cases


@pytest.mark.parametrize(
    "correct_steps,failed_steps,total,accepted",
    [
        (2, (2, 2, 2, 2), 92, True),
        (2, (2, 2, 2, 3), 93, False),
        (2, (3, 3, 3, 3), 96, False),
        (1, (3, 3, 3, 3), 54, True),
    ],
)
def test_all_attempts_count_toward_full_roster_budget(correct_steps, failed_steps, total, accepted):
    ids, cases = roster(correct_steps, failed_steps)
    report = summarize(ids, cases, data_version_verified=True)
    assert report["joint_pass"] == 42
    assert report["accepted"] is accepted
    assert report["total_steps"] == total
    assert report["mean_steps"] == total / 46
    assert report["step_budget"] == 92
    assert report["step_budget_pass"] is accepted


@pytest.mark.parametrize("missing", ["result", "steps", "trace", "trace_label"])
def test_unknown_steps_cannot_be_zero_filled(missing):
    ids, cases = roster()
    if missing == "result":
        cases.pop()
    elif missing == "steps":
        cases[-1] = replace(cases[-1], steps=None)
    elif missing == "trace":
        cases[-1] = replace(cases[-1], trace_complete=False)
    else:
        cases[-1] = replace(cases[-1], failures=("trace_missing",))
    report = summarize(ids, cases, data_version_verified=True)
    assert report["total"] == 46 and report["joint_pass"] == 42
    assert report["total_steps"] is None and report["mean_steps"] is None
    assert not report["step_budget_pass"] and not report["accepted"]


def test_low_mean_does_not_replace_joint_correctness_gate():
    ids, cases = roster(1)
    cases[41] = replace(cases[41], correct=False)
    report = summarize(ids, cases, data_version_verified=True)
    assert report["joint_pass"] == 41 and report["step_budget_pass"]
    assert not report["accepted"]


@pytest.mark.parametrize("flag", ["oracle_consistent", "failure_label"])
def test_oracle_conflict_invalidates_batch_not_only_one_failure(flag):
    ids, cases = roster()
    cases[-1] = replace(
        cases[-1],
        **({"oracle_consistent": False} if flag == "oracle_consistent" else {"failures": ("oracle_conflict",)}),
    )
    report = summarize(ids, cases, data_version_verified=True)
    assert report["joint_pass"] == 42 and report["total"] == 46
    assert not report["oracle_valid"] and not report["accepted"]
