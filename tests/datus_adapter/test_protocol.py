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

from dataclasses import replace
from decimal import Decimal

import pytest
from powercontext_datus.freeze import IntegrityError, snapshot, verify_snapshot
from powercontext_datus.oracle import ComparisonPolicy, Table, compare_tables
from powercontext_datus.report import CaseVerdict, summarize
from powercontext_datus.trace import EventJournal, OperationEvent, count_steps


def test_full_row_association_and_duplicate_multiplicity():
    gold = Table(("id", "value"), ((1, 10), (2, 20)))
    assert not compare_tables(Table(gold.columns, ((1, 20), (2, 10))), gold)
    assert compare_tables(Table(gold.columns, ((2, 20), (1, 10))), gold)
    assert not compare_tables(Table(gold.columns, ((1, 10), (1, 10))), gold)
    assert not compare_tables(Table(gold.columns, ((2, 20), (1, 10))), gold, ComparisonPolicy(ordered=True))
    assert not compare_tables(Table(("value", "id"), gold.rows), gold)


@pytest.mark.parametrize("left,right", [(None, ""), (None, 0), (False, 0), ("1", 1)])
def test_distinct_types_and_nulls(left, right):
    assert not compare_tables(Table(("v",), ((left,),)), Table(("v",), ((right,),)))


def test_empty_rows_and_finite_values():
    assert compare_tables(Table(("v",), ()), Table(("v",), ()))
    assert not compare_tables(Table(("v",), ()), Table(("v",), ((None,),)))
    assert compare_tables(Table(("v",), ((1,),)), Table(("v",), ((Decimal("1.0"),),)))
    for value in [float("nan"), float("inf"), Decimal("NaN")]:
        with pytest.raises(ValueError, match="nonfinite"):
            Table(("v",), ((value,),))


def test_tolerance_uses_full_rows_and_non_greedy_matching():
    # 1 matches 0 or 2, but 0 matches only 0: greedy matching would wrongly reject.
    policy = ComparisonPolicy(absolute_tolerance=Decimal("1"))
    assert compare_tables(Table(("v",), ((1,), (0,))), Table(("v",), ((0,), (2,))), policy)
    assert not compare_tables(Table(("v",), ((5,),)), Table(("v",), ((2,),)), policy)
    with pytest.raises(ValueError):
        ComparisonPolicy(absolute_tolerance=Decimal("-1"))


def event(operation, status="started", name="read_query"):
    return OperationEvent("run", "task", "attempt", operation, name, status)


def count(events):
    return count_steps(events, run_id="run", task_id="task", attempt_id="attempt")


def test_attempts_failures_and_wrapper_span_deduplication():
    events = [
        event("load", name="load_skill"),
        event("load", "success", "load_skill"),
        event("sql1"),
        event("sql1"),
        event("sql1", "failure"),
        event("sql2"),
        event("sql2", "success"),
        event("sql2", "success"),
    ]
    result = count(events)
    assert (result.steps, result.complete, result.failures) == (3, True, 1)
    assert count([event("sql1"), event("sql1", "success"), event("sql2"), event("sql2", "success")]).steps == 2


def test_missing_conflicting_or_mixed_lifecycle_cannot_certify_steps():
    assert not count([]).complete
    assert not count([event("one")]).complete
    assert not count([event("one", "success")]).complete
    with pytest.raises(ValueError, match="conflicting"):
        count([event("one"), event("one", "failure"), event("one", "success")])
    with pytest.raises(ValueError, match="mixed"):
        count([replace(event("one"), task_id="another")])


def test_journal_preserves_failure_before_retry_and_refuses_overwrite(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    journal.append(event("one"))
    journal.append(event("one", "failure"))
    journal.close()
    assert len(path.read_text().splitlines()) == 2
    with pytest.raises(FileExistsError):
        EventJournal(path)


def test_snapshot_detects_cross_case_mutation_addition_and_links(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "context.txt").write_text("frozen")
    frozen = snapshot(root)
    verify_snapshot(root, frozen)
    (root / "previous-answer.txt").write_text("contamination")
    with pytest.raises(IntegrityError, match="drifted"):
        verify_snapshot(root, frozen)
    (root / "link").symlink_to(root / "context.txt")
    with pytest.raises(IntegrityError, match="link"):
        snapshot(root)


def test_snapshot_detects_content_and_mode_changes(tmp_path):
    path = tmp_path / "context.txt"
    path.write_text("original")
    frozen = snapshot(tmp_path)
    path.write_text("changed")
    with pytest.raises(IntegrityError):
        verify_snapshot(tmp_path, frozen)
    path.write_text("original")
    path.chmod(0o400)
    with pytest.raises(IntegrityError):
        verify_snapshot(tmp_path, frozen)


def verdict(task_id="1", **changes):
    return replace(CaseVerdict(task_id, True, 2, True, True, True, True, True), **changes)


def test_fixed_denominator_missing_results_and_42_of_46_gate():
    ids = [str(i) for i in range(46)]
    cases = [verdict(i, correct=int(i) < 42) for i in ids]
    report = summarize(ids, cases, data_version_verified=True)
    assert report["accepted"] and report["joint_pass"] == 42 and report["mean_steps"] == 2
    missing = summarize(ids, cases[:42], data_version_verified=True)
    assert missing["total"] == 46 and missing["joint_pass"] == 42
    assert not missing["accepted"] and missing["mean_steps"] is None
    assert not summarize(ids, cases, data_version_verified=False)["accepted"]
    drifted = [*cases[:45], replace(cases[45], state_valid=False)]
    assert summarize(ids, drifted, data_version_verified=True)["joint_pass"] == 42
    assert not summarize(ids, drifted, data_version_verified=True)["accepted"]


@pytest.mark.parametrize(
    "changes",
    [
        {"steps": 3},
        {"steps": None},
        {"steps": 0},
        {"trace_complete": False},
        {"oracle_consistent": False},
        {"state_valid": False},
        {"native_execution": False},
        {"answer_grounded": False},
    ],
)
def test_invalid_evidence_never_passes(changes):
    report = summarize(["1"], [verdict(**changes)], data_version_verified=True)
    assert not report["accepted"] and report["joint_pass"] == 0


def test_recovered_failure_within_budget_and_no_duplicate_roster_entries():
    assert summarize(["1"], [verdict(failures=("sql_error",))], data_version_verified=True)["accepted"]
    with pytest.raises(ValueError):
        summarize(["1"], [verdict(), verdict()], data_version_verified=True)
    with pytest.raises(ValueError):
        summarize(["1"], [verdict("other")], data_version_verified=True)
