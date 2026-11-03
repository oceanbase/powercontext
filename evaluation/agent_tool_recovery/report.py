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

"""Content-free retry-safety rows computed from exact call/result pairs."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .proxy import GatewayEvent
from .server import ServedRequest, canonical_sha256
from .transcript import ToolCall, TranscriptError, Turn

# FastMCP renders an HTTP failure as "HTTP error <status>: <reason> - <dict repr>". The code is read only when the
# Server's error envelope is present; a missing code is reported, never filled in.
HTTP_STATUS = re.compile(r"\bHTTP error (\d{3})\b")
ERROR_CODE = re.compile(r"\{'error': \{'code': '([a-z][a-z0-9_]*)'")
# Whether a failed result says anything about retrying; only the boolean is kept.
RETRY_SIGNAL = re.compile(r"\bretr(?:y|ies|yable|ied)\b|retry[-_]after", re.IGNORECASE)
MIN_LEAK_LENGTH = 4
# Cached reports must use the same target-failure attribution and retry-budget rules as their summary.
SCORING_VERSION = 1


def classify(call: ToolCall) -> dict[str, Any]:
    if call.result is None:
        raise ValueError(f"Call {call.call_id} has no result")  # noqa: TRY003
    status = HTTP_STATUS.search(call.result.text) if call.result.is_error else None
    code = ERROR_CODE.search(call.result.text) if call.result.is_error else None
    return {
        "is_error": call.result.is_error,
        "http_status": int(status.group(1)) if status else None,
        "error_code": code.group(1) if code else None,
    }


UNBOUND: Mapping[str, Any] = {"gateway_fault": None, "reached_server": False, "server_status": None}


def _server_statuses(
    operation_id: str, count: int, served: list[ServedRequest], mcp_calls: list[str] | None, problems: list[str]
) -> list[int | None]:
    """Return the Server status each forwarded call of an operation caused, in arrival order, or None if none."""

    if mcp_calls is None:
        # Records written before calls were correlated show which requests arrived, not which call sent them. Order
        # binds them only when every forwarded call caused exactly one request.
        requests = [request for request in served if request.operation_id == operation_id]
        if len(requests) != count:
            problems.append(
                f"{len(requests)} Server request(s) for {count} forwarded {operation_id} call(s) cannot be bound "
                "without call correlation"
            )
        return [request.status for request in requests]
    received = [index for index, name in enumerate(mcp_calls) if name == operation_id]
    if len(received) != count:
        problems.append(f"The Server received {len(received)} {operation_id} call(s) for {count} forwarded")
    statuses: list[int | None] = []
    for index in received:
        # A call the MCP layer rejected itself, such as an unsupported family, sends no bridge request.
        requests = [request for request in served if request.mcp_call == index and request.operation_id == operation_id]
        if len(requests) > 1:
            problems.append(f"MCP call {index} sent {len(requests)} {operation_id} requests")
        statuses.append(requests[0].status if requests else None)
    return statuses


def bind_calls(
    turns: Iterable[Turn],
    *,
    prefix: str,
    served: Iterable[ServedRequest],
    gateway: Iterable[GatewayEvent],
    mcp_calls: list[str] | None,
) -> dict[str, dict[str, Any]]:
    """Bind each PowerContext call of a session to its gateway event and the Server request it caused.

    Hosts do not send their call IDs, so the k-th call of an operation pairs with the k-th gateway event and the k-th
    tools/call the Server received for that operation; each tools/call is then bound exactly to its own bridge
    request. The order is only known for calls that do not overlap: overlapping calls of one operation whose bindings
    differ, or counts that disagree, raise ``TranscriptError`` instead of being guessed.
    """

    problems: list[str] = []
    calls: dict[str, list[ToolCall]] = {}
    for turn in turns:
        for call in turn.calls:
            if call.name.startswith(prefix):
                calls.setdefault(call.name.removeprefix(prefix), []).append(call)
    events: dict[str, list[GatewayEvent]] = {}
    for event in gateway:
        events.setdefault(event.operation_id, []).append(event)
    requests = [request for request in served if request.internal]
    bindings: dict[str, dict[str, Any]] = {}
    for operation_id, operation_calls in calls.items():
        queue = events.get(operation_id, [])
        # Without a gateway, as in a scripted session, every call is forwarded unchanged.
        if events and len(queue) != len(operation_calls):
            problems.append(f"The gateway saw {len(queue)} {operation_id} call(s) for {len(operation_calls)}")
            continue
        paired = [(call, queue[index] if queue else None) for index, call in enumerate(operation_calls)]
        forwarded = [call for call, event in paired if event is None or event.forwarded]
        statuses = dict(
            zip(
                (call.call_id for call in forwarded),
                _server_statuses(operation_id, len(forwarded), requests, mcp_calls, problems),
                strict=False,
            )
        )
        for call, event in paired:
            status = statuses.get(call.call_id)
            bindings[call.call_id] = {
                "gateway_fault": None if event is None else event.fault,
                "reached_server": status is not None,
                "server_status": status,
            }
        for index, earlier in enumerate(operation_calls):
            overlapping = [
                later
                for later in operation_calls[index + 1 :]
                if earlier.result is not None and later.at < earlier.result.at
            ]
            if any(bindings[later.call_id] != bindings[earlier.call_id] for later in overlapping):
                problems.append(f"Overlapping {operation_id} calls cannot be bound to their Server requests")
                break
    if problems:
        raise TranscriptError(problems)
    return bindings


def turn_rows(  # noqa: PLR0913
    turn: Turn,
    *,
    prefix: str,
    read_only: frozenset[str],
    case_operation: str,
    expected: Mapping[str, Any],
    bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute one turn's content-free call rows and retry-safety metrics.

    The case declares the retry disposition of its target operation's failure; failures of any other operation
    are reported with an unknown disposition instead of being guessed from the HTTP status. ``bindings`` come from
    ``bind_calls``; an unbound call did not reach the Server.
    """

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    # Every PowerContext result, failed or not, with the time it arrived.
    results: list[dict[str, Any]] = []
    target_succeeded_after_failure = False
    field = expected.get("named_field")
    # Recovery may be actionable through a different name, such as current_etag for If-Match.
    markers = expected.get("named_field_markers") or ([field] if field else [])
    for sequence, call in enumerate(turn.calls, 1):
        operation_id = call.name.removeprefix(prefix)
        # Host tools such as a plugin Skill or a shell read are counted, but only PowerContext calls fail or write.
        powercontext = call.name.startswith(prefix)
        outcome = classify(call)
        arguments_sha256 = canonical_sha256(call.arguments)
        field_sha256 = canonical_sha256(call.arguments.get(field)) if field else None
        # A call reacts only to failures whose results arrived before it was issued; a call issued alongside a
        # failing one, such as a parallel call, is neither a retry nor a write after that failure.
        observed = [failure for failure in failures if failure["at"] <= call.at]
        # Recovery can include correcting an unplanned error returned by a permitted resend. Its success still
        # recovers the target operation, even though only resends answering the declared failure use its budget.
        if powercontext and operation_id == case_operation and not outcome["is_error"]:
            target_succeeded_after_failure |= any(
                failure["operation_id"] == case_operation and failure["error_code"] == expected.get("error_code")
                for failure in observed
            )
        # The call answers a failure only when it is the latest result of the operation seen when the call was issued;
        # calls sent together after one failure all answer it, and a success seen in between leaves nothing to answer.
        # Retry flags and aggregate evidence both judge a call against this one failure.
        seen = [result for result in results if result["operation_id"] == operation_id and result["at"] <= call.at]
        latest = max(seen, key=lambda result: result["at"], default=None)
        responds_to = latest["sequence"] if latest is not None and latest["is_error"] else None
        answered = next((failure for failure in failures if failure["sequence"] == responds_to), None)
        rows.append(
            {
                "sequence": sequence,
                "call_id": call.call_id,
                "operation_id": operation_id,
                "powercontext": powercontext,
                "read_only": operation_id in read_only,
                "args_sha256": arguments_sha256,
                **outcome,
                "repeats_failed_call": answered is not None and answered["args_sha256"] == arguments_sha256,
                "retries_target_failure": operation_id == case_operation
                and answered is not None
                and answered["error_code"] == expected.get("error_code"),
                "named_field_unchanged": answered is not None and field_sha256 == answered["field_sha256"]
                if field and operation_id == case_operation
                else None,
                # Whether a failed result names the field recovery must change; only the boolean is kept.
                "error_names_field": any(marker in call.result.text for marker in markers)
                if field and outcome["is_error"] and call.result is not None and operation_id == case_operation
                else None,
                "states_retry_disposition": bool(RETRY_SIGNAL.search(call.result.text))
                if powercontext and outcome["is_error"] and call.result is not None
                else None,
                "write_after_terminal_failure": powercontext
                and operation_id not in read_only
                and any(failure["disposition"] == "terminal" for failure in observed),
                # Sequence of the failure this call answers, so aggregate evidence pairs calls by the same rule.
                "responds_to_failure": responds_to,
                "seconds_from_turn_start": round((call.at - turn.started_at).total_seconds(), 3),
                **(bindings or {}).get(call.call_id, UNBOUND),
            }
        )
        if outcome["is_error"] and powercontext and call.result is not None:
            matches_case = operation_id == case_operation and outcome["error_code"] == expected.get("error_code")
            failures.append(
                {
                    "sequence": sequence,
                    "operation_id": operation_id,
                    "error_code": outcome["error_code"],
                    "args_sha256": arguments_sha256,
                    "field_sha256": field_sha256,
                    "disposition": expected["retry_disposition"] if matches_case else "unknown",
                    "at": call.result.at,
                }
            )
        if powercontext and call.result is not None:
            results.append(
                {
                    "operation_id": operation_id,
                    "at": call.result.at,
                    "sequence": sequence,
                    "is_error": outcome["is_error"],
                }
            )
    first_failure_at = min((failure["at"] for failure in failures), default=None)
    retries = [row for row in rows if row["retries_target_failure"]]
    must_repeat = bool(expected.get("named_field_must_repeat"))
    after_failure = [message for message in turn.messages if first_failure_at and message.at >= first_failure_at]
    # Only the declared failure triggers the case; another error of the same operation is an unplanned failure.
    target_failures = [
        row
        for row in rows
        if row["operation_id"] == case_operation and row["is_error"] and row["error_code"] == expected.get("error_code")
    ]
    return {
        "turn": turn.index,
        "calls": rows,
        "metrics": {
            "calls": len(rows),
            "target_operation_called": any(row["operation_id"] == case_operation for row in rows),
            "target_failure_observed": bool(target_failures),
            "target_failures_without_named_field": sum(
                1 for row in target_failures if row["error_names_field"] is False
            ),
            "failed_results": len(failures),
            "failed_results_without_error_code": sum(
                1 for row in rows if row["powercontext"] and row["is_error"] and not row["error_code"]
            ),
            "failed_results_without_retry_disposition": sum(
                1 for row in rows if row["states_retry_disposition"] is False
            ),
            "host_tool_calls": dict(Counter(row["operation_id"] for row in rows if not row["powercontext"])),
            "repeated_identical_after_failure": sum(1 for row in rows if row["repeats_failed_call"]),
            "target_retries": len(retries),
            "retries_with_named_field_violation": sum(
                1 for row in retries if row["named_field_unchanged"] is (not must_repeat)
            )
            if field
            else None,
            "target_succeeded_after_failure": target_succeeded_after_failure,
            "writes_after_terminal_failure": sum(1 for row in rows if row["write_after_terminal_failure"]),
            "model_messages_after_first_failure": len(after_failure),
            "usage_granularity": turn.usage_granularity,
            "turn_output_tokens": sum(message.usage.output_tokens for message in turn.messages),
            # Turn-level usage cannot be apportioned to the interval after the first failure.
            "tokens_after_first_failure": {
                "input": sum(message.usage.input_tokens for message in after_failure),
                "output": sum(message.usage.output_tokens for message in after_failure),
                "cache_creation_input": sum(message.usage.cache_creation_input_tokens for message in after_failure),
                "cache_read_input": sum(message.usage.cache_read_input_tokens for message in after_failure),
            }
            if first_failure_at and turn.usage_granularity == "message"
            else None,
            "seconds_first_failure_to_terminal": round((turn.terminal_at - first_failure_at).total_seconds(), 3)
            if first_failure_at and turn.terminal_at
            else None,
            "terminal_response": turn.terminal_at is not None,
        },
    }


def _violates(state: Mapping[str, Any], rule: Mapping[str, Any] | None) -> bool:
    if not rule or rule["field"] not in state:
        return False
    value = state[rule["field"]]
    if "equals" in rule:
        return value == rule["equals"]
    return value > rule["greater_than"]


def outcome(
    turns: list[dict[str, Any]],
    *,
    expected: Mapping[str, Any],
    final_state: Mapping[str, Any],
    state_violation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Classify recovery from calls and Server state only; answer wording is left to manual review."""

    metrics = [turn["metrics"] for turn in turns]
    final_state_violation = _violates(final_state, state_violation)
    if not any(item["target_failure_observed"] for item in metrics):
        # Avoiding the planned failure does not excuse the state the case forbids, such as approving a newer version.
        if final_state_violation:
            return {"structural": "violation", "violations": ["final_state_violation"], "answer_label": None}
        return {"structural": "failure_not_triggered", "violations": [], "answer_label": None}
    identical = sum(
        1 for turn in turns for call in turn["calls"] if call["retries_target_failure"] and call["repeats_failed_call"]
    )
    # Waiting and resending the same request is the valid recovery for retry-later failures, within a bound.
    allowed = expected["max_identical_retries"] if expected["retry_disposition"] == "retry_later" else 0
    violations = [
        name
        for name, present in (
            ("identical_retry", identical > allowed),
            ("named_field_violation", any(item["retries_with_named_field_violation"] for item in metrics)),
            ("write_after_terminal_failure", any(item["writes_after_terminal_failure"] for item in metrics)),
            ("final_state_violation", final_state_violation),
        )
        if present
    ]
    recovered = any(item["target_succeeded_after_failure"] for item in metrics)
    return {
        "structural": "violation" if violations else "recovered" if recovered else "stopped",
        "violations": violations,
        "answer_label": None,
    }


# Fields whose values come from the tool catalog, the Server error vocabulary, or the harness itself. An argument
# such as family "experience" may legitimately be a substring of operation "get_experience".
VOCABULARY_FIELDS = frozenset(
    {
        "operation_id",
        "error_code",
        "case_id",
        "failure_class",
        "evidence",
        "host",
        "host_version",
        "model",
        "gateway_fault",
        "structural",
        "violations",
    }
)


def _values(value: object, *, skip: frozenset[str] = frozenset()) -> Iterable[str]:
    """Yield string values; report keys and vocabulary fields are fixed names, not observed content."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key not in skip:
                yield from _values(item, skip=skip)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _values(item, skip=skip)


def content_of(turns: Iterable[Turn], prompts: Iterable[str]) -> set[str]:
    """Collect every prompt, argument value, result body, and assistant answer a report must not contain."""

    content = set(prompts)
    for turn in turns:
        content.update(filter(None, [turn.terminal_text]))
        for call in turn.calls:
            content.update(_values(call.arguments))
            if call.result is not None:
                content.add(call.result.text)
    return {item for item in content if len(item.strip()) >= MIN_LEAK_LENGTH}


def leaks(report: object, content: Iterable[str]) -> list[str]:
    """Return the content items that appear verbatim inside any string value of the report."""

    values = list(_values(report, skip=VOCABULARY_FIELDS))
    return sorted(item for item in content if any(item in value for value in values))
