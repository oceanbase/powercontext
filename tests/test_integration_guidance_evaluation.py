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

"""Protect routing evaluation from false passes and mismatched controlled results."""

import asyncio
import json
from typing import Any

import pytest

from scripts.evaluate_integration_guidance import run_scenario, validate_message
from scripts.integration_guidance_handoff import HandoffFixture


def call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "reasoning": "fixture protocol state",
        "tool_calls": [
            {
                "id": "fixture-call",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            },
        ],
    }


@pytest.mark.parametrize(
    "response",
    [
        {"content": "<tool_call><function=remember_memory>saved</tool_call>"},
        {"content": "", "tool_calls": [{"function": {"name": "remember_memory", "arguments": "[]"}}]},
    ],
)
def test_rejects_simulated_calls_and_non_object_arguments(response: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        validate_message(response)


class Model:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = iter(responses)
        self.messages: list[list[dict[str, Any]]] = []

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.messages.append(list(messages))
        return next(self.responses)


def test_resolves_scope_before_delivering_write_result() -> None:
    model = Model([call("resolve_scope_binding"), call("remember_memory"), {"content": "Saved."}])
    catalog = {
        "host": "fixture",
        "guidance": "",
        "tools": [{"name": "resolve_scope_binding"}, {"name": "remember_memory"}],
    }
    result = asyncio.run(run_scenario(model, catalog, "save", 0, "unloaded"))
    assert result["routing_passed"]
    assert result["calls"][0]["function"]["name"] == "remember_memory"
    assert '"scope_id": "fixture-scope"' in model.messages[1][-1]["content"]
    assert '"status": "saved"' in model.messages[2][-1]["content"]
    # Preserve provider protocol state for the continuation, without publishing reasoning in the report.
    assert model.messages[1][-2]["reasoning"] == "fixture protocol state"
    assert "reasoning" not in result


@pytest.mark.parametrize("reply", [{"content": ""}, call("remember_memory")])
def test_missing_save_tool_does_not_pass_on_empty_output_or_invented_call(reply: dict[str, Any]) -> None:
    model = Model([reply])
    catalog = {"host": "fixture", "guidance": "", "tools": [{"name": "remember_memory"}]}
    result = asyncio.run(run_scenario(model, catalog, "unavailable_save", 0, "unavailable"))
    assert not result["routing_passed"]


def handoff_payload() -> dict[str, Any]:
    return {
        "scope_id": "fixture-scope",
        "source_id": "aurora-boundary",
        "handoff": {
            "schema": "powercontext.current-work-handoff.v1",
            "trust": "untrusted_input",
            "objective": "Document Aurora",
            "state": [{"text": "README complete", "basis": "declared", "evidence": []}],
            "disposition": "continuable",
            "next_action": {"text": "Review examples", "basis": "declared", "evidence": []},
            "omissions": [],
        },
    }


def handoff_catalog() -> dict[str, Any]:
    return {
        "host": "fixture",
        "guidance": "",
        "tools": [
            {"name": name}
            for name in (
                "handoff_current_work",
                "commit_handoff",
                "pc_capture_source",
                "pc_handoff_activate",
                "pc_handoff_finalize",
            )
        ],
    }


@pytest.mark.parametrize("case", ["handoff", "handoff_request"])
def test_handoff_requires_a_usable_carrier_after_success(case: str) -> None:
    payload = handoff_payload()
    prepared = HandoffFixture().respond("handoff_current_work", payload)["handoff"]
    model = Model([call("handoff_current_work", payload), {"content": json.dumps(prepared)}])
    result = asyncio.run(run_scenario(model, handoff_catalog(), case, 0, "loaded"))
    assert result["routing_passed"]
    assert result["controlled_results"]
    assert json.loads(result["final_response"]) == prepared


@pytest.mark.parametrize(
    "followup",
    [
        call("commit_handoff", {"handoff": {}}),
        {"content": ""},
        {"content": "Prepared."},
    ],
)
def test_handoff_does_not_pass_on_later_commit_or_missing_carrier(followup: dict[str, Any]) -> None:
    model = Model([call("handoff_current_work", handoff_payload()), followup])
    result = asyncio.run(run_scenario(model, handoff_catalog(), "handoff", 0, "loaded"))
    assert not result["routing_passed"]
    assert result["controlled_results"]


@pytest.mark.parametrize(
    "payload", [{}, {"source_id": "boundary", "handoff": {}}, {**handoff_payload(), "scope_id": "other"}]
)
def test_handoff_does_not_pass_on_invalid_arguments_or_foreign_scope(payload: dict[str, Any]) -> None:
    result = asyncio.run(
        run_scenario(Model([call("handoff_current_work", payload)]), handoff_catalog(), "handoff", 0, "loaded")
    )
    assert not result["routing_passed"]
    assert result["error"]


@pytest.mark.parametrize("fault", [None, "invented_source", "changed_draft", "only_capture"])
def test_native_handoff_preserves_evidence_through_finalization(fault: str | None) -> None:
    fixture = HandoffFixture()
    capture = {"source_id": "aurora", "content": "README complete. Review the examples."}
    source = fixture.respond("pc_capture_source", capture)["data"]["source"]
    activate = {"objective": "Document Aurora", "boundary_source": source}
    draft = fixture.respond("pc_handoff_activate", activate)["data"]["draft"]
    prepared = fixture.respond("pc_handoff_finalize", {"draft": draft})["data"]
    if fault == "invented_source":
        activate = {**activate, "boundary_source": {**source, "source_id": "invented"}}
    if fault == "changed_draft":
        draft = {**draft, "objective": "Different task"}
    responses = [
        call("pc_capture_source", capture),
        call("pc_handoff_activate", activate),
        call("pc_handoff_finalize", {"draft": draft}),
        {"content": json.dumps(prepared)},
    ]
    if fault == "only_capture":
        responses = [responses[0], {"content": "Handoff ready."}]
    result = asyncio.run(run_scenario(Model(responses), handoff_catalog(), "handoff", 0, "unloaded"))
    assert result["routing_passed"] is (fault is None)
