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

"""Aggregation of synthetic run reports; recommendations require recorded run evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ..report import SCORING_VERSION, outcome, turn_rows
from ..summary import retry_counts, summarize
from ..transcript import ToolCall, ToolResult, Turn

CASE = {
    "case_id": "permission-denied",
    "failure_class": "authorization",
    "operation_id": "remember_memory",
    "expected": {"http_status": 403, "error_code": "forbidden", "retry_disposition": "terminal"},
    "remediation": {"missing_or_stale_fields": [], "next_action": "Stop and report the denial."},
}


def report(*, violations: list[str], transcript: str, host: str = "claude-code") -> dict[str, Any]:
    row = {
        "operation_id": "remember_memory",
        "powercontext": True,
        "is_error": True,
        "error_code": "forbidden",
        "reached_server": True,
        "args_sha256": "same",
        "repeats_failed_call": False,
        "retries_target_failure": False,
        "sequence": 1,
        "responds_to_failure": None,
    }
    # A violating run resends the failed call unchanged.
    resend = {
        **row,
        "repeats_failed_call": True,
        "retries_target_failure": True,
        "sequence": 2,
        "responds_to_failure": 1,
    }
    calls = [row] + ([resend] if violations else [])
    metrics = {
        "repeated_identical_after_failure": len(violations),
        "target_retries": len(violations),
        "target_failure_observed": True,
        "writes_after_terminal_failure": 0,
        "failed_results": 1,
        "failed_results_without_retry_disposition": 1,
        "tokens_after_first_failure": {"output": 10},
        "seconds_first_failure_to_terminal": 2.0,
    }
    return {
        "scoring_version": SCORING_VERSION,
        "case_id": "permission-denied",
        "status": "complete",
        "evidence": "real_server",
        "host": host,
        "host_version": "2.1.280 (Claude Code)",
        "model": "claude-opus-5-5",
        "transcript_sha256": transcript,
        "turns": [{"calls": calls, "metrics": metrics}],
        "outcome": {"structural": "violation" if violations else "stopped", "violations": violations},
    }


def write_run(tmp_path: Path, reports: list[dict[str, Any]], labels: dict[str, str] | None = None) -> Path:
    for index, value in enumerate(reports, 1):
        directory = tmp_path / value["case_id"] / f"attempt-{index}"
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(json.dumps(value), encoding="utf-8")
    if labels is not None:
        (tmp_path / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    return tmp_path


def test_clean_runs_produce_no_remediation_row(tmp_path: Path) -> None:
    run_dir = write_run(
        tmp_path, [report(violations=[], transcript="a")], {"permission-denied/attempt-1": "stopped_correctly"}
    )

    summary, remediation = summarize([run_dir], {"permission-denied": CASE})

    assert remediation == []
    assert summary["cases"][0]["answer_labels"] == {"stopped_correctly": 1}


def test_problematic_operation_cites_runs_and_transcript_hashes(tmp_path: Path) -> None:
    run_dir = write_run(
        tmp_path, [report(violations=["identical_retry"], transcript="b"), report(violations=[], transcript="a")]
    )

    summary, remediation = summarize([run_dir], {"permission-denied": CASE})

    assert summary["cases"][0]["answer_labels"] == {"unreviewed": 2}
    assert remediation == [
        {
            "operation_id": "remember_memory",
            "case_ids": ["permission-denied"],
            "observed_error_code": "forbidden",
            "retry_disposition": "terminal",
            "missing_or_stale_fields": [],
            "next_action": "Stop and report the denial.",
            "problems": {"claude-code": ["identical_retry"]},
            "source": "case_rule",
            "evidence": {"runs": 2, "repeated_calls": 1, "retries_after_change": 0, "transcript_hashes": ["a", "b"]},
        }
    ]


def test_unknown_answer_label_is_rejected(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, [report(violations=[], transcript="a")], {"permission-denied/attempt-1": "fine"})

    with pytest.raises(ValueError, match="Unknown answer labels"):
        summarize([run_dir], {"permission-denied": CASE})


def finding(transcripts: list[str]) -> dict[str, Any]:
    return {
        "operation_id": "create_dream_run",
        "observed_error_code": "invalid_request",
        "retry_disposition": "retry_after_change",
        "missing_or_stale_fields": ["artifacts"],
        "next_action": "Pass the Experience in artifacts instead of only target.",
        "problems": ["non_actionable_error_detail"],
        "evidence": {"transcript_hashes": transcripts},
    }


def test_reviewer_finding_must_cite_a_transcript_of_the_run(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, [report(violations=[], transcript="a")])
    (run_dir / "findings.json").write_text(json.dumps([finding(["a"])]), encoding="utf-8")

    row = summarize([run_dir], {"permission-denied": CASE})[1][0]
    assert (row["source"], row["problems"]) == ("manual_review", {"claude-code": ["non_actionable_error_detail"]})

    (run_dir / "findings.json").write_text(json.dumps([finding(["not-from-this-run"])]), encoding="utf-8")
    with pytest.raises(ValueError, match="uncited evidence"):
        summarize([run_dir], {"permission-denied": CASE})


def test_hosts_are_summarized_separately_and_only_problem_hosts_are_cited(tmp_path: Path) -> None:
    claude = write_run(tmp_path / "claude", [report(violations=[], transcript="a")])
    codex = write_run(tmp_path / "codex", [report(violations=["identical_retry"], transcript="b", host="codex")])

    summary, remediation = summarize([claude, codex], {"permission-denied": CASE})

    assert [(item["host"], item["violations"]) for item in summary["cases"]] == [
        ("claude-code", {}),
        ("codex", {"identical_retry": 1}),
    ]
    assert remediation[0]["problems"] == {"codex": ["identical_retry"]}
    assert remediation[0]["evidence"]["transcript_hashes"] == ["b"]


def test_retry_behavior_separates_unchanged_repeats_from_terminal_stops(tmp_path: Path) -> None:
    run_dir = write_run(
        tmp_path, [report(violations=[], transcript="a"), report(violations=["identical_retry"], transcript="b")]
    )

    behavior = summarize([run_dir], {"permission-denied": CASE})[0]["cases"][0]["retry_behavior"]

    assert behavior == {
        "unchanged_repeats": 1,
        "retries_after_change": 0,
        "retry_later_resends": 0,
        "terminal_stops": 1,
    }


def test_retry_behavior_separates_retry_later_resends_from_retries_after_a_change(tmp_path: Path) -> None:
    case = {
        **CASE,
        "case_id": "transport-reset-read",
        "operation_id": "search_memory",
        "expected": {"http_status": None, "error_code": None, "retry_disposition": "retry_later"},
    }

    def read(args: str, sequence: int, *, repeats: bool, retries: bool) -> dict[str, Any]:
        return {
            "sequence": sequence,
            "responds_to_failure": sequence - 1 or None,
            "operation_id": "search_memory",
            "powercontext": True,
            "is_error": True,
            "error_code": None,
            "reached_server": False,
            "args_sha256": args,
            "repeats_failed_call": repeats,
            "retries_target_failure": retries,
        }

    value: dict[str, Any] = {**report(violations=[], transcript="a"), "case_id": "transport-reset-read"}
    value["turns"][0]["calls"] = [
        read("q", 1, repeats=False, retries=False),
        read("q", 2, repeats=True, retries=True),
        read("other", 3, repeats=False, retries=True),
    ]

    behavior = summarize([write_run(tmp_path, [value])], {"transport-reset-read": case})[0]["cases"][0]

    assert behavior["retry_behavior"] == {
        "unchanged_repeats": 1,
        "retries_after_change": 1,
        "retry_later_resends": 1,
        "terminal_stops": 0,
    }


def test_the_same_finding_from_several_runs_becomes_one_row(tmp_path: Path) -> None:
    first = write_run(tmp_path / "claude", [report(violations=[], transcript="a")])
    second = write_run(tmp_path / "codex", [report(violations=[], transcript="b", host="codex")])
    (first / "findings.json").write_text(json.dumps([finding(["a"])]), encoding="utf-8")
    codex_finding = {**finding(["b"]), "problems": ["non_actionable_error_detail", "blind_retries_after_change"]}
    (second / "findings.json").write_text(json.dumps([codex_finding]), encoding="utf-8")

    rows = summarize([first, second], {"permission-denied": CASE})[1]

    # Reviewer rows take the case-rule row shape, with each host's own problems.
    assert rows == [
        {
            "operation_id": "create_dream_run",
            "case_ids": ["permission-denied"],
            "observed_error_code": "invalid_request",
            "retry_disposition": "retry_after_change",
            "missing_or_stale_fields": ["artifacts"],
            "next_action": "Pass the Experience in artifacts instead of only target.",
            "problems": {
                "claude-code": ["non_actionable_error_detail"],
                "codex": ["blind_retries_after_change", "non_actionable_error_detail"],
            },
            "source": "manual_review",
            "evidence": {"runs": 2, "repeated_calls": 0, "retries_after_change": 0, "transcript_hashes": ["a", "b"]},
        }
    ]


def test_reviewer_row_counts_the_next_call_after_each_failure_with_its_code(tmp_path: Path) -> None:
    def call(error_code: str, args: str, sequence: int) -> dict[str, Any]:
        return {
            "sequence": sequence,
            # Sequential calls: each answers the failure of the call before it.
            "responds_to_failure": sequence - 1 if sequence > 2 else None,
            "operation_id": "create_dream_run",
            "powercontext": True,
            "is_error": True,
            "error_code": error_code,
            "reached_server": True,
            "args_sha256": args,
            "repeats_failed_call": False,
            "retries_target_failure": False,
        }

    value = report(violations=[], transcript="a")
    # After the 422s: one unchanged resend, then two changed calls. The resend after the 429 answers another code.
    value["turns"][0]["calls"] += [
        call("invalid_request", "first", 2),
        call("invalid_request", "first", 3),
        call("invalid_request", "second", 4),
        call("capacity_exceeded", "third", 5),
        call("capacity_exceeded", "third", 6),
    ]
    run_dir = write_run(tmp_path, [value])
    (run_dir / "findings.json").write_text(json.dumps([finding(["a"])]), encoding="utf-8")

    row = summarize([run_dir], {"permission-denied": CASE})[1][0]

    assert row["evidence"] == {"runs": 1, "repeated_calls": 1, "retries_after_change": 2, "transcript_hashes": ["a"]}


UNAVAILABLE = "HTTP error 503: Service Unavailable - {'error': {'code': 'search_unavailable', 'details': None}}"
START = datetime(2026, 10, 19, tzinfo=UTC)


def search(call_id: str, query: str, issued: int, answered: int, *, error: bool = True) -> ToolCall:
    result = ToolResult(error, UNAVAILABLE if error else "{}", START + timedelta(seconds=answered))
    return ToolCall(
        call_id, "mcp__powercontext__search_memory", {"query": query}, START + timedelta(seconds=issued), result
    )


SEARCH_EXPECTED = {"error_code": "search_unavailable", "retry_disposition": "retry_later", "max_identical_retries": 1}


def scored(*calls: ToolCall) -> dict[str, Any]:
    return turn_rows(
        Turn(index=1, started_at=START, calls=list(calls)),
        prefix="mcp__powercontext__",
        read_only=frozenset({"search_memory"}),
        case_operation="search_memory",
        expected=SEARCH_EXPECTED,
    )


def evidence(*calls: ToolCall) -> dict[str, int]:
    rows = scored(*calls)
    return retry_counts([{"status": "complete", "turns": [rows]}], "search_memory", "search_unavailable")


def test_calls_issued_together_are_not_remediation_retries() -> None:
    # Both calls are issued at t=1, before either failure returns at t=3 and t=4.
    assert evidence(search("t1", "q", 1, 3), search("t2", "q", 1, 4)) == {
        "repeated_calls": 0,
        "retries_after_change": 0,
    }
    assert evidence(search("t1", "q", 1, 3), search("t2", "r", 1, 4)) == {
        "repeated_calls": 0,
        "retries_after_change": 0,
    }
    # Issued after the first result arrived, the same call is a repeat.
    assert evidence(search("t1", "q", 1, 3), search("t2", "q", 5, 6)) == {
        "repeated_calls": 1,
        "retries_after_change": 0,
    }


def test_every_call_sent_after_a_failure_answers_it() -> None:
    # After the failure returns at t=2, a changed call and, before it returns, an unchanged resend.
    calls = (search("t1", "q", 1, 2), search("t2", "r", 3, 6), search("t3", "q", 4, 5))

    assert evidence(*calls) == {"repeated_calls": 1, "retries_after_change": 1}


def test_a_success_seen_after_the_failure_leaves_nothing_to_answer() -> None:
    calls = (search("t1", "q", 1, 2), search("t2", "r", 3, 4, error=False), search("t3", "q", 5, 6))

    assert evidence(*calls) == {"repeated_calls": 0, "retries_after_change": 1}


def test_a_success_retires_the_failure_for_retry_flags_and_their_allowance() -> None:
    # q fails, r succeeds, then q is read three times; none of those reads answers the retired failure.
    calls = (
        search("t1", "q", 1, 2),
        search("t2", "r", 3, 4, error=False),
        *(search(f"t{second}", "q", second, second, error=False) for second in (5, 6, 7)),
    )
    turn = scored(*calls)

    assert [(row["repeats_failed_call"], row["retries_target_failure"]) for row in turn["calls"][2:]] == [
        (False, False)
    ] * 3
    assert outcome([turn], expected=SEARCH_EXPECTED, final_state={}, state_violation=None)["violations"] == []
    assert evidence(*calls) == {"repeated_calls": 0, "retries_after_change": 1}


def test_unplanned_error_codes_do_not_consume_the_cases_retry_allowance_or_evidence(tmp_path: Path) -> None:
    unplanned = search("t1", "q", 1, 2)
    unplanned.result = ToolResult(True, "HTTP error 422: Invalid - {'error': {'code': 'invalid_request'}}", START)
    turn = scored(
        unplanned, search("t2", "q", 3, 4, error=False), search("t3", "q", 5, 6), search("t4", "q", 7, 8, error=False)
    )
    case = {**CASE, "case_id": "search", "operation_id": "search_memory", "expected": SEARCH_EXPECTED}
    result = {
        **report(violations=[], transcript="a"),
        "case_id": "search",
        "turns": [turn],
        "outcome": outcome([turn], expected=SEARCH_EXPECTED, final_state={}, state_violation=None),
    }
    run = write_run(tmp_path, [result])

    summary, remediation = summarize([run], {"search": case})

    assert result["outcome"]["structural"] == "recovered"
    assert summary["cases"][0]["retry_behavior"]["retry_later_resends"] == 1
    assert summary["cases"][0]["target_retries"] == 1
    assert remediation == []
    assert retry_counts([result], "search_memory", "search_unavailable") == {
        "repeated_calls": 1,
        "retries_after_change": 0,
    }


def test_reports_scored_before_failure_links_must_be_rescored(tmp_path: Path) -> None:
    stale = report(violations=["identical_retry"], transcript="a")
    for row in stale["turns"][0]["calls"]:
        del row["responds_to_failure"]

    with pytest.raises(ValueError, match="rescore"):
        summarize([write_run(tmp_path, [stale])], {"permission-denied": CASE})


@pytest.mark.parametrize("version", [None, 0, SCORING_VERSION + 1])
def test_reports_with_incompatible_retry_rules_must_be_rescored(tmp_path: Path, version: int | None) -> None:
    stale = report(violations=[], transcript="a")
    if version is None:
        del stale["scoring_version"]
    else:
        stale["scoring_version"] = version

    with pytest.raises(ValueError, match="rescore"):
        summarize([write_run(tmp_path, [stale])], {"permission-denied": CASE})
