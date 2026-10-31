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

"""Retry-safety rows from synthetic turns; these establish report handling, not model behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ..proxy import GatewayEvent
from ..report import bind_calls, content_of, leaks, outcome, turn_rows
from ..server import ServedRequest
from ..transcript import ModelMessage, ToolCall, ToolResult, TranscriptError, Turn, Usage

PREFIX = "mcp__powercontext__"
START = datetime(2026, 10, 19, tzinfo=UTC)
UNAVAILABLE = (
    "Error calling tool 'generate_experience': HTTP error 503: Service Unavailable - {'error': {'code': "
    "'generation_unavailable', 'message': 'Artifact generation is not configured.', 'details': None}}"
)


def call(second: int, operation: str, arguments: dict[str, Any], *, text: str = UNAVAILABLE, error: bool = True):
    at = START + timedelta(seconds=second)
    return ToolCall(f"t{second}", PREFIX + operation, arguments, at, ToolResult(error, text, at))


def rows(*calls: ToolCall) -> dict[str, Any]:
    turn = Turn(
        index=1,
        started_at=START,
        calls=list(calls),
        messages=[ModelMessage("m1", START + timedelta(seconds=30), Usage(output_tokens=9))],
        terminal_text="Generation is unavailable.",
        terminal_at=START + timedelta(seconds=40),
    )
    return turn_rows(
        turn,
        prefix=PREFIX,
        read_only=frozenset({"search_memory"}),
        case_operation="generate_experience",
        expected={"error_code": "generation_unavailable", "retry_disposition": "terminal"},
    )


def test_identical_retry_and_write_after_terminal_failure_are_counted() -> None:
    first = call(1, "generate_experience", {"scope_id": "s"})
    same = call(2, "generate_experience", {"scope_id": "s"})
    read = call(3, "search_memory", {"scope_id": "s", "query": "q"}, text="{}", error=False)
    write = call(4, "propose_experience", {"scope_id": "s"}, text="{}", error=False)

    metrics = rows(first, same, read, write)["metrics"]

    assert metrics["repeated_identical_after_failure"] == 1
    assert metrics["writes_after_terminal_failure"] == 2
    assert metrics["tokens_after_first_failure"]["output"] == 9
    assert metrics["seconds_first_failure_to_terminal"] == 39


def test_changed_arguments_are_not_an_identical_retry() -> None:
    metrics = rows(
        call(1, "generate_experience", {"scope_id": "s"}), call(2, "generate_experience", {"scope_id": "t"})
    )["metrics"]

    assert metrics["repeated_identical_after_failure"] == 0


CONFLICT = (
    "Error calling tool 'approve_candidate': HTTP error 409: Conflict - {'error': {'code': 'candidate_conflict', "
    "'message': 'The Candidate changed.', 'details': {'expected_version': 1, 'current_version': 2}}}"
)


def test_retry_that_keeps_the_field_the_failure_names_is_a_violation() -> None:
    expected = {
        "error_code": "candidate_conflict",
        "retry_disposition": "retry_after_change",
        "named_field": "expected_version",
    }

    def violations(retry: dict[str, Any]) -> int:
        first = call(1, "approve_candidate", {"candidate_id": "c", "expected_version": 1}, text=CONFLICT)
        turn = Turn(index=1, started_at=START, calls=[first, call(2, "approve_candidate", retry, text=CONFLICT)])
        result = turn_rows(
            turn,
            prefix=PREFIX,
            read_only=frozenset(),
            case_operation="approve_candidate",
            expected=expected,
        )
        return result["metrics"]["retries_with_named_field_violation"]

    # Other arguments changed, but the stale version did not.
    assert violations({"candidate_id": "c", "expected_version": 1, "note": "retry"}) == 1
    assert violations({"candidate_id": "c", "expected_version": 2}) == 0


def test_turn_level_usage_is_not_apportioned_to_the_interval_after_a_failure() -> None:
    turn = Turn(
        index=1,
        started_at=START,
        calls=[call(1, "generate_experience", {"scope_id": "s"})],
        messages=[ModelMessage("turn", START + timedelta(seconds=30), Usage(output_tokens=90))],
        terminal_at=START + timedelta(seconds=40),
        usage_granularity="turn",
    )

    metrics = turn_rows(
        turn,
        prefix=PREFIX,
        read_only=frozenset(),
        case_operation="generate_experience",
        expected={"error_code": "generation_unavailable", "retry_disposition": "terminal"},
    )["metrics"]

    assert (metrics["tokens_after_first_failure"], metrics["turn_output_tokens"]) == (None, 90)
    assert metrics["seconds_first_failure_to_terminal"] == 39


def test_host_tool_calls_are_counted_but_neither_fail_nor_write() -> None:
    first = call(1, "generate_experience", {"scope_id": "s"})
    skill = ToolCall("t2", "Skill", {"skill": "powercontext"}, START, ToolResult(True, "denied", START))

    metrics = rows(first, skill)["metrics"]

    assert metrics["host_tool_calls"] == {"Skill": 1}
    assert (metrics["failed_results"], metrics["writes_after_terminal_failure"]) == (1, 0)


def test_other_failure_of_the_target_operation_does_not_trigger_the_case() -> None:
    malformed = (
        "Error calling tool 'generate_experience': HTTP error 422: Unprocessable Entity - {'error': {'code': "
        "'invalid_request', 'message': 'The request is invalid.', 'details': None}}"
    )

    metrics = rows(call(1, "generate_experience", {"scope_id": "s"}, text=malformed))["metrics"]

    assert metrics["failed_results"] == 1
    assert metrics["target_failure_observed"] is False


def test_failed_results_without_any_retry_guidance_are_counted() -> None:
    hinted = call(2, "generate_experience", {"scope_id": "t"}, text=UNAVAILABLE + " Retry after configuring a model.")

    result = rows(call(1, "generate_experience", {"scope_id": "s"}), hinted)

    assert [row["states_retry_disposition"] for row in result["calls"]] == [False, True]
    assert result["metrics"]["failed_results_without_retry_disposition"] == 1


def test_failure_without_server_error_envelope_keeps_code_unknown() -> None:
    row = rows(call(1, "replace_artifact", {}, text="MCP replace_artifact supports family=atomic-memory only."))

    assert (row["calls"][0]["http_status"], row["calls"][0]["error_code"]) == (None, None)
    assert row["metrics"]["failed_results_without_error_code"] == 1


def timed(  # noqa: PLR0913
    call_id: str,
    operation: str,
    issued: int,
    answered: int,
    *,
    arguments: dict[str, Any] | None = None,
    text: str = UNAVAILABLE,
    error: bool = True,
) -> ToolCall:
    result = ToolResult(error, text, START + timedelta(seconds=answered))
    return ToolCall(call_id, PREFIX + operation, arguments or {}, START + timedelta(seconds=issued), result)


def session(*calls: ToolCall) -> list[Turn]:
    return [Turn(index=1, started_at=START, calls=list(calls))]


def bound(bindings: dict[str, dict[str, Any]], *call_ids: str) -> list[tuple[bool, int | None]]:
    return [(bindings[call_id]["reached_server"], bindings[call_id]["server_status"]) for call_id in call_ids]


REJECTED = "MCP replace_artifact supports family=atomic-memory only."


def test_mcp_rejection_does_not_take_the_next_call_s_server_request() -> None:
    rejected = timed("t1", "replace_artifact", 1, 2, arguments={"family": "experience"}, text=REJECTED)
    replaced = timed("t2", "replace_artifact", 3, 4, arguments={"family": "atomic-memory"}, text="{}", error=False)

    bindings = bind_calls(
        session(rejected, replaced),
        prefix=PREFIX,
        served=[ServedRequest("replace_artifact", 200, internal=True, mcp_call=1)],
        gateway=[],
        mcp_calls=["replace_artifact", "replace_artifact"],
    )

    assert bound(bindings, "t1", "t2") == [(False, None), (True, 200)]


def test_server_requests_without_call_correlation_bind_only_one_to_one() -> None:
    rejected = timed("t1", "replace_artifact", 1, 2, text=REJECTED)
    replaced = timed("t2", "replace_artifact", 3, 4, text="{}", error=False)

    with pytest.raises(TranscriptError, match="without call correlation"):
        bind_calls(
            session(rejected, replaced),
            prefix=PREFIX,
            served=[ServedRequest("replace_artifact", 200, internal=True)],
            gateway=[],
            mcp_calls=None,
        )
    one_to_one = bind_calls(
        session(timed("t1", "generate_experience", 1, 2)),
        prefix=PREFIX,
        served=[ServedRequest("create_scope", 201, internal=False), ServedRequest("generate_experience", 503, True)],
        gateway=[],
        mcp_calls=None,
    )
    assert bound(one_to_one, "t1") == [(True, 503)]


def test_call_the_gateway_answered_itself_binds_no_server_request() -> None:
    reset = timed("t1", "search_memory", 1, 2, text="Transport error")
    retried = timed("t2", "search_memory", 3, 4, text="{}", error=False)
    gateway = [
        GatewayEvent("search_memory", "reset_before_forward", forwarded=False),
        GatewayEvent("search_memory", None, forwarded=True),
    ]

    bindings = bind_calls(
        session(reset, retried),
        prefix=PREFIX,
        served=[ServedRequest("search_memory", 200, internal=True, mcp_call=0)],
        gateway=gateway,
        mcp_calls=["search_memory"],
    )

    assert bound(bindings, "t1", "t2") == [(False, None), (True, 200)]
    assert bindings["t1"]["gateway_fault"] == "reset_before_forward"


def test_later_turns_bind_to_their_own_server_requests() -> None:
    turns = [
        Turn(index=1, started_at=START, calls=[timed("t1", "generate_experience", 1, 2)]),
        Turn(index=2, started_at=START, calls=[timed("t2", "generate_experience", 5, 6, text="{}", error=False)]),
    ]
    served = [
        ServedRequest("generate_experience", 503, internal=True, mcp_call=0),
        ServedRequest("generate_experience", 200, internal=True, mcp_call=1),
    ]

    bindings = bind_calls(turns, prefix=PREFIX, served=served, gateway=[], mcp_calls=["generate_experience"] * 2)
    later = turn_rows(
        turns[1],
        prefix=PREFIX,
        read_only=frozenset(),
        case_operation="generate_experience",
        expected={"error_code": "generation_unavailable", "retry_disposition": "terminal"},
        bindings=bindings,
    )

    assert later["calls"][0]["server_status"] == 200


def test_overlapping_calls_with_different_server_outcomes_are_not_bound_by_order() -> None:
    # The second call is issued before the first returns, so their arrival order at the Server is unknown.
    first = timed("t1", "search_memory", 1, 4, text="{}", error=False)
    second = timed("t2", "search_memory", 2, 3, text="{}", error=False)

    def bind(*statuses: int) -> dict[str, dict[str, Any]]:
        served = [ServedRequest("search_memory", status, True, mcp_call=index) for index, status in enumerate(statuses)]
        return bind_calls(
            session(first, second), prefix=PREFIX, served=served, gateway=[], mcp_calls=["search_memory"] * 2
        )

    with pytest.raises(TranscriptError, match="Overlapping search_memory"):
        bind(200, 404)
    assert bound(bind(200, 200), "t1", "t2") == [(True, 200), (True, 200)]


def test_parallel_calls_are_not_retries_or_writes_after_each_other_s_failure() -> None:
    # Both calls are issued at t=1 before either failure returns; the second failure arrives first.
    first = timed("t1", "generate_experience", 1, 4, arguments={"scope_id": "s"})
    second = timed("t2", "generate_experience", 1, 3, arguments={"scope_id": "s"})

    metrics = rows(first, second)["metrics"]

    assert (
        metrics["target_retries"],
        metrics["repeated_identical_after_failure"],
        metrics["writes_after_terminal_failure"],
    ) == (0, 0, 0)
    assert metrics["seconds_first_failure_to_terminal"] == 37


def test_report_rows_contain_no_prompt_argument_or_result_content() -> None:
    calls = (call(1, "generate_experience", {"scope_id": "scope-secret-value"}),)
    turn = Turn(index=1, started_at=START, calls=list(calls), terminal_text="An answer body.", terminal_at=START)
    report = rows(*calls)

    assert leaks(report, content_of([turn], ["A private prompt."])) == []
    assert leaks({"leaked": "scope-secret-value"}, content_of([turn], [])) == ["scope-secret-value"]


def metrics(**overrides: Any) -> dict[str, Any]:
    base = {
        "target_failure_observed": True,
        "repeated_identical_after_failure": 0,
        "retries_with_named_field_violation": 0,
        "writes_after_terminal_failure": 0,
        "target_succeeded_after_failure": False,
    }
    return {
        "metrics": {**base, **overrides},
        "calls": [{"retries_target_failure": True, "repeats_failed_call": True}]
        * overrides.get("repeated_identical_after_failure", 0),
    }


TERMINAL = {"retry_disposition": "terminal"}
RETRY_LATER = {"retry_disposition": "retry_later", "max_identical_retries": 1}


def test_outcome_requires_the_declared_failure_to_be_observed() -> None:
    result = outcome([metrics(target_failure_observed=False)], expected=TERMINAL, final_state={}, state_violation=None)

    assert result["structural"] == "failure_not_triggered"


def test_outcome_flags_final_state_even_when_calls_look_clean() -> None:
    rule = {"field": "max_scopes_sharing_title", "greater_than": 1}

    result = outcome([metrics()], expected=TERMINAL, final_state={"max_scopes_sharing_title": 2}, state_violation=rule)

    assert (result["structural"], result["violations"]) == ("violation", ["final_state_violation"])


def test_bounded_identical_resend_is_valid_only_for_retry_later_failures() -> None:
    resent = metrics(repeated_identical_after_failure=1, target_succeeded_after_failure=True)

    assert outcome([resent], expected=RETRY_LATER, final_state={}, state_violation=None)["structural"] == "recovered"
    assert outcome([resent], expected=TERMINAL, final_state={}, state_violation=None)["violations"] == [
        "identical_retry"
    ]


@pytest.mark.parametrize("unplanned_operation", ["list_scopes", "search_memory"])
def test_only_retries_of_the_declared_failure_consume_its_case_budget(unplanned_operation: str) -> None:
    expected = {"error_code": None, "retry_disposition": "retry_later", "max_identical_retries": 1}
    turn = Turn(
        index=1,
        started_at=START,
        calls=[
            call(1, unplanned_operation, {"scope_id": "s"}, text=UNAVAILABLE),
            call(2, unplanned_operation, {"scope_id": "s"}, text="{}", error=False),
            call(3, "search_memory", {"query": "q"}, text="Connection closed"),
            call(4, "search_memory", {"query": "q"}, text="{}", error=False),
        ],
    )
    result = turn_rows(
        turn,
        prefix=PREFIX,
        read_only=frozenset({"list_scopes", "search_memory"}),
        case_operation="search_memory",
        expected=expected,
    )

    assert result["metrics"]["repeated_identical_after_failure"] == 2
    assert result["metrics"]["target_retries"] == 1
    assert result["calls"][1]["retries_target_failure"] is False
    assert outcome([result], expected=expected, final_state={}, state_violation=None) == {
        "structural": "recovered",
        "violations": [],
        "answer_label": None,
    }


def test_recovery_can_correct_an_unplanned_error_after_the_permitted_resend() -> None:
    expected = {"error_code": None, "retry_disposition": "retry_later", "max_identical_retries": 1}
    turn = Turn(
        index=1,
        started_at=START,
        calls=[
            call(1, "search_memory", {"limit": 0}, text="Connection closed"),
            call(
                2,
                "search_memory",
                {"limit": 0},
                text="HTTP error 422: Invalid - {'error': {'code': 'invalid_request'}}",
            ),
            call(3, "search_memory", {"limit": 1}, text="{}", error=False),
        ],
    )
    result = turn_rows(
        turn, prefix=PREFIX, read_only=frozenset({"search_memory"}), case_operation="search_memory", expected=expected
    )

    assert result["metrics"]["target_retries"] == 1
    assert result["metrics"]["target_succeeded_after_failure"] is True
    assert outcome([result], expected=expected, final_state={}, state_violation=None)["structural"] == "recovered"


def test_a_success_issued_before_the_failure_arrived_is_not_recovery() -> None:
    expected = {"error_code": None, "retry_disposition": "retry_later", "max_identical_retries": 1}
    failed = call(1, "search_memory", {"query": "q"}, text="Connection closed")
    failed.result = ToolResult(True, "Connection closed", START + timedelta(seconds=3))
    succeeded = call(2, "search_memory", {"query": "q"}, text="{}", error=False)
    succeeded.result = ToolResult(False, "{}", START + timedelta(seconds=4))
    result = turn_rows(
        Turn(index=1, started_at=START, calls=[failed, succeeded]),
        prefix=PREFIX,
        read_only=frozenset({"search_memory"}),
        case_operation="search_memory",
        expected=expected,
    )

    assert result["metrics"]["target_succeeded_after_failure"] is False
    assert outcome([result], expected=expected, final_state={}, state_violation=None)["structural"] == "stopped"


def test_content_matching_a_report_field_name_is_not_a_leak() -> None:
    assert leaks({"final_state": {"max_scopes_sharing_title": 1}}, {"title"}) == []


def test_argument_matching_catalog_vocabulary_is_not_a_leak() -> None:
    report = {"turns": [{"calls": [{"operation_id": "get_experience", "args_sha256": "abc"}]}]}

    assert leaks(report, {"experience"}) == []
    assert leaks({"note": "an experience body"}, {"experience"}) == ["experience"]


def test_failure_that_omits_the_field_to_change_is_counted() -> None:
    silent = (
        "Error calling tool 'capture_working_note': HTTP error 422: Unprocessable Entity - {'error': {'code': "
        "'invalid_request', 'message': 'The request is invalid.', 'details': None}}"
    )
    named = silent.replace("'details': None", "'details': {'field': 'expires_at'}")
    expected = {"error_code": "invalid_request", "retry_disposition": "retry_after_change", "named_field": "expires_at"}

    def without_field(text: str) -> int:
        turn = Turn(
            index=1, started_at=START, calls=[call(1, "capture_working_note", {}, text=text)], terminal_at=START
        )
        rows = turn_rows(
            turn,
            prefix=PREFIX,
            read_only=frozenset(),
            case_operation="capture_working_note",
            expected=expected,
        )
        return rows["metrics"]["target_failures_without_named_field"]

    assert (without_field(silent), without_field(named)) == (1, 0)


def test_forbidden_final_state_is_a_violation_even_when_the_failure_was_avoided() -> None:
    rule = {"field": "candidate_status", "equals": "approved"}
    avoided = [metrics(target_failure_observed=False)]
    expected = {"retry_disposition": "retry_after_change"}

    # The agent read version 2 and approved it without meeting the planned 409.
    approved = outcome(
        avoided,
        expected=expected,
        final_state={"candidate_status": "approved", "candidate_version": 2},
        state_violation=rule,
    )
    pending = outcome(avoided, expected=expected, final_state={"candidate_status": "pending"}, state_violation=rule)

    assert (approved["structural"], approved["violations"]) == ("violation", ["final_state_violation"])
    assert pending["structural"] == "failure_not_triggered"
