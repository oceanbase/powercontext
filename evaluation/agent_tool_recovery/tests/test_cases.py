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

"""The case manifest and committed remediation rows stay within the current MCP surface and #2067's limits."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from powercontext.server import mcp

from ..run import load_cases

REMEDIATION = Path(__file__).parents[1] / "results" / "remediation.json"
REQUIRED_CLASSES = {
    "request_schema",
    "stale_version",
    "capability_unavailable",
    "transient",
    "idempotent_replay",
    "authorization",
}
SNAKE_CASE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


def argument_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value} | {name for item in value.values() for name in argument_names(item)}
    if isinstance(value, list):
        return {name for item in value for name in argument_names(item)}
    return set()


def actions() -> list[tuple[str, str, str, set[str]]]:
    """Each next action with its owner, retry disposition, and the field and code names it may mention."""

    rows = [
        (
            case["case_id"],
            case["expected"]["retry_disposition"],
            case["remediation"]["next_action"],
            set(case["remediation"]["missing_or_stale_fields"])
            | {case["expected"].get("named_field"), case["expected"]["error_code"]}
            | argument_names(case["preflight_arguments"]),
        )
        for case in load_cases().values()
    ]
    rows += [
        (
            row["operation_id"],
            row["retry_disposition"],
            row["next_action"],
            set(row["missing_or_stale_fields"]) | {row["observed_error_code"]},
        )
        for row in json.loads(REMEDIATION.read_text(encoding="utf-8"))
    ]
    return [
        (owner, disposition, action, {name for name in names if name}) for owner, disposition, action, names in rows
    ]


def test_cases_cover_every_required_failure_class_through_current_mcp_operations() -> None:
    cases = load_cases().values()

    assert {case["failure_class"] for case in cases} == REQUIRED_CLASSES
    assert sorted(case["operation_id"] for case in cases if case["operation_id"] not in mcp._MCP_OPERATION_IDS) == []


def test_next_actions_name_only_current_operations_and_the_failure_s_own_fields() -> None:
    unknown = {
        owner: sorted(set(SNAKE_CASE.findall(action)) - mcp._MCP_OPERATION_IDS - names)
        for owner, _, action, names in actions()
    }

    assert {owner: values for owner, values in unknown.items() if values} == {}


def test_next_actions_stop_on_terminal_failures_and_bound_every_retry() -> None:
    for owner, disposition, action, _ in actions():
        if disposition == "terminal":
            assert action.startswith("Stop"), owner
        if disposition == "retry_later":
            assert re.search(r"\b(once|bounded)\b", action), owner
