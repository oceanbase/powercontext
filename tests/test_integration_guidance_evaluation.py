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
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from evaluate_integration_guidance import apply_reporting_review, run_scenario, validate_message
from integration_guidance_handoff import HandoffFixture
from integration_guidance_native import NativeHandoffSession
from integration_guidance_skills import with_skill_resources


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


@pytest.mark.parametrize("description", ["Native host discovery description", ""])
def test_hermes_evaluation_uses_native_discovery_and_qualified_resource_reads(description: str) -> None:
    name = "powercontext:powercontext-project-context"
    catalog = with_skill_resources({
        "host": "hermes",
        "guidance": "",
        "tools": [{"name": "powercontext_search_memory"}],
        "skill": {"name": name, "description": description},
    })
    model = Model([
        call("read_skill_resource", {"resource": name}),
        call("read_skill_resource", {"resource": f"{name}/references/scope-memory.md"}),
        call("powercontext_search_memory"),
        {"role": "assistant", "content": "Search complete."},
    ])
    result = asyncio.run(run_scenario(model, catalog, "skill_search", 0, "unloaded"))
    prompt = model.messages[0][0]["content"]
    assert prompt.split("Optional Skill catalog: ", 1)[1] == f"{name}: {description}"
    assert result["routing_passed"], result.get("error")


def test_hermes_layered_evaluation_rejects_catalog_without_native_metadata() -> None:
    with pytest.raises(ValueError, match="Hermes catalog lacks native Skill metadata"):
        with_skill_resources({"host": "hermes"})


@pytest.mark.parametrize("host,case", [("codex", "skill_search"), ("dsh", "skill_search"), ("codex", "skill_handoff")])
@pytest.mark.parametrize("reading", ["none", "router", "wrong_domain", "requested_domain"])
def test_requested_workflow_requires_its_domain_before_the_operation(host: str, case: str, reading: str) -> None:
    entry = "powercontext-project-context"
    memory = "powercontext-memory" if host == "dsh" else f"{entry}/references/scope-memory.md"
    handoff = f"{entry}/references/work-handoff.md"
    review = "powercontext-review" if host == "dsh" else f"{entry}/references/review-publication.md"
    expected = memory if case == "skill_search" else handoff
    resources = {entry: "Router", memory: "Memory workflow", handoff: "Handoff workflow", review: "Review workflow"}
    reads = {"none": [], "router": [entry], "wrong_domain": [entry, review], "requested_domain": [expected]}[reading]
    responses = [call("read_skill_resource", {"resource": resource}) for resource in reads]
    if case == "skill_search":
        catalog = {"host": host, "guidance": "", "tools": [{"name": "search_memory"}]}
        responses.append(call("search_memory", {"query": "Aurora"}))
    else:
        payload = handoff_payload()
        prepared = HandoffFixture().respond("handoff_current_work", payload)["handoff"]
        catalog = {**handoff_catalog(), "host": host}
        responses.extend([call("handoff_current_work", payload), {"content": json.dumps(prepared)}])
    result = asyncio.run(run_scenario(Model(responses), {**catalog, "skill_resources": resources}, case, 0, "unloaded"))
    assert result["routing_passed"] is (reading == "requested_domain")
    if reading != "requested_domain":
        assert not result["acceptance_passed"]
        assert f"host={host}, case={case}" in result["error"]
        assert expected in result["error"]
        for resource in reads:
            assert resource in result["error"]


def test_handoff_read_after_operation_does_not_qualify_requested_workflow() -> None:
    resource = "powercontext-project-context/references/work-handoff.md"
    payload = handoff_payload()
    prepared = HandoffFixture().respond("handoff_current_work", payload)["handoff"]
    model = Model([
        call("handoff_current_work", payload),
        call("read_skill_resource", {"resource": resource}),
        {"content": json.dumps(prepared)},
    ])
    catalog = {**handoff_catalog(), "skill_resources": {resource: "Handoff workflow"}}
    result = asyncio.run(run_scenario(model, catalog, "skill_handoff", 0, "unloaded"))
    assert not result["routing_passed"]
    assert resource in result["error"]
    assert "before the data operation" in result["error"]


@pytest.mark.parametrize("case,mode", [("search", "unloaded"), ("skill_search", "unavailable")])
def test_search_without_required_skill_read_remains_usable(case: str, mode: str) -> None:
    catalog = {"host": "codex", "guidance": "", "tools": [{"name": "search_memory"}], "skill_resources": {}}
    result = asyncio.run(run_scenario(Model([call("search_memory")]), catalog, case, 0, mode))
    assert result["routing_passed"]


def test_failed_skill_read_keeps_specific_diagnosis_and_attempt() -> None:
    response = call("read_skill_resource", {"resource": "missing-memory-workflow"})
    catalog = {"host": "codex", "guidance": "", "tools": [{"name": "search_memory"}], "skill_resources": {}}
    result = asyncio.run(run_scenario(Model([response]), catalog, "skill_search", 0, "unloaded"))
    assert not result["routing_passed"]
    assert "missing-memory-workflow" in result["error"]
    assert "no such packaged or registered resource" in result["error"]
    assert result["skill_read_attempts"][0]["calls"] == response["tool_calls"]


def test_mixed_skill_read_and_operation_retains_the_rejected_operation() -> None:
    response = call("read_skill_resource", {"resource": "powercontext-memory"})
    response["tool_calls"] += call("pc_search")["tool_calls"]
    catalog = {"host": "dsh", "guidance": "", "tools": [{"name": "pc_search"}], "skill_resources": {}}
    result = asyncio.run(run_scenario(Model([response]), catalog, "skill_search", 0, "unloaded"))
    assert not result["routing_passed"]
    assert "batched with ['pc_search']" in result["error"]
    assert result["skill_read_attempts"][0]["calls"] == response["tool_calls"]


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


def test_memory_selection_cannot_pass_with_missing_required_arguments() -> None:
    catalog = {
        "host": "fixture",
        "guidance": "",
        "tools": [
            {
                "name": "remember_memory",
                "parameters": {"type": "object", "required": ["scope_id", "kind", "text"]},
            }
        ],
    }
    result = asyncio.run(run_scenario(Model([call("remember_memory")]), catalog, "save", 0, "unloaded"))
    assert not result["arguments_passed"] and not result["acceptance_passed"]
    assert "scope_id" in result["error"]


@pytest.mark.parametrize("case", ["failed_save", "unavailable_save"])
def test_false_saved_reply_requires_explicit_reporting_review(case: str) -> None:
    responses = [call("remember_memory"), {"content": "Saved."}] if case == "failed_save" else [{"content": "Saved."}]
    catalog = {"host": "fixture", "guidance": "", "tools": [{"name": "remember_memory"}]}
    result = asyncio.run(run_scenario(Model(responses), catalog, case, 0, "unloaded"))
    assert result["routing_passed"]
    assert result["reporting_passed"] is None
    assert not result["acceptance_passed"]
    apply_reporting_review(
        result, {result["review_key"]: {"passed": False, "reason": "Claims saved without a successful write"}}
    )
    assert result["reporting_passed"] is False and not result["acceptance_passed"]


def test_reporting_review_is_bound_to_the_exact_observation() -> None:
    result: dict[str, Any] = {"routing_passed": True, "arguments_passed": True, "final_response": "Not saved."}
    apply_reporting_review(result, {})
    reviews = {result["review_key"]: {"passed": True, "reason": "Accurately reports the failed write"}}
    apply_reporting_review(result, reviews)
    assert result["acceptance_passed"]
    result["final_response"] = "Saved."
    apply_reporting_review(result, reviews)
    assert result["reporting_passed"] is None and not result["acceptance_passed"]


def test_carrier_accepts_omitted_optional_null_metadata_but_requires_scope_and_evidence() -> None:
    fixture = HandoffFixture()
    prepared = fixture.respond("handoff_current_work", handoff_payload())["handoff"]
    prepared.pop("generation", None)
    prepared["content"].pop("generation", None)
    assert fixture.carrier_returned(json.dumps(prepared))
    assert not fixture.carrier_returned(json.dumps({key: value for key, value in prepared.items() if key != "base"}))
    prepared["content"]["state"][0]["citations"][0]["source_ref"]["source_id"] = "invented"
    assert not fixture.carrier_returned(json.dumps(prepared))


@pytest.mark.parametrize("host", ["dsh", "pi", "opencode"])
def test_native_adapter_handoff_uses_real_request_mapping_and_response_envelopes(host: str) -> None:
    root = Path(__file__).resolve().parents[1]
    if not shutil.which("node") or not (root / "integrations" / host / "plugins/powercontext/node_modules").is_dir():
        pytest.skip("Install the host package dependencies and Node 22.19+ for native adapter qualification")

    async def scenario() -> None:
        session, fixture = NativeHandoffSession(host), HandoffFixture()
        try:
            capture = await session.call(
                "pc_capture_source", {"source_id": "aurora", "content": "README complete"}, fixture
            )
            source = capture["data"]["source"]
            draft = await session.call(
                "pc_handoff_prepare",
                {
                    "objective": "Document Aurora",
                    "evidence": [{"kind": "source", "source_ref": source}],
                    "boundary_source": json.dumps(source),
                    "scope_id": "foreign-scope",
                },
                fixture,
            )
            request = session.requests[-1]["payload"]
            assert request["scope_id"] == "fixture-scope"
            assert "boundary_source" not in request
            prepared = await session.call("pc_handoff_finalize", {"draft": draft["data"]}, fixture)
            assert fixture.carrier_returned(json.dumps(prepared["data"]))
            assert prepared["data"]["schema"] == "powercontext.prepared-handoff.v1"
        finally:
            await session.close()

    asyncio.run(scenario())


def test_openclaw_handoff_adapter_preserves_generated_source_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "integrations/openclaw/plugins/memory-powercontext/dist/index.js").is_file():
        pytest.skip("Build the OpenClaw package and use Node 24.15+ for native adapter qualification")
    node = os.environ.get("POWERCONTEXT_GUIDANCE_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True, timeout=10).stdout
    if tuple(int(part) for part in version.strip().lstrip("v").split(".")[:2]) < (24, 15):
        pytest.skip("The pinned OpenClaw SDK requires Node 24.15+")

    async def scenario() -> None:
        session, fixture = NativeHandoffSession("openclaw"), HandoffFixture()
        try:
            reply = await session.call(
                "powercontext_handoff_current_work",
                {
                    "handoff": handoff_payload()["handoff"],
                    "scope_id": "foreign-scope",
                },
                fixture,
            )
            request = session.requests[-1]["payload"]
            assert request["scope_id"] == "fixture-scope"
            assert request["source_id"].startswith("openclaw-handoff-boundary-")
            assert fixture.carrier_returned(json.dumps(reply["handoff"]))
        finally:
            await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["state", "next_action"])
def test_handoff_claim_error_identifies_field_without_accepting_a_source(field: str) -> None:
    fixture = HandoffFixture()
    payload = handoff_payload()
    claim = payload["handoff"]["state"][0] if field == "state" else payload["handoff"]["next_action"]
    claim["basis"] = "verified"
    path = r"handoff.state\[0\]" if field == "state" else r"handoff.next_action"
    with pytest.raises(ValueError, match=path + r"\.basis/evidence"):
        fixture.respond("handoff_current_work", payload)
    assert fixture.source is None


@pytest.mark.parametrize("operation, case", [("remember_memory", "save"), ("search_memory", "search")])
def test_data_operations_cannot_change_the_bound_evaluation_scope(operation: str, case: str) -> None:
    model = Model([call(operation, {"scope_id": "invented-scope"})])
    catalog = {"host": "fixture", "guidance": "", "tools": [{"name": operation}]}
    result = asyncio.run(run_scenario(model, catalog, case, 0, "unavailable"))
    assert not result["routing_passed"]
    assert not result["arguments_passed"]
    assert f"{operation}.scope_id: expected bound Scope fixture-scope, got 'invented-scope'" in result["error"]
    assert len(model.messages) == 1  # No fabricated successful write result is delivered.


@pytest.mark.parametrize("wrapper", ["handoff", "data"])
def test_handoff_response_wrapper_is_not_a_transferable_carrier(wrapper: str) -> None:
    fixture = HandoffFixture()
    fixture.respond("handoff_current_work", handoff_payload())
    assert fixture.carrier_returned(json.dumps(fixture.prepared))
    assert not fixture.carrier_returned(json.dumps({wrapper: fixture.prepared}))


def test_handoff_array_and_broken_json_do_not_hide_a_nested_carrier() -> None:
    fixture = HandoffFixture()
    fixture.respond("handoff_current_work", handoff_payload())
    carrier = json.dumps(fixture.prepared)
    assert fixture.carrier_returned(f"Here is the carrier:\n```json\n{carrier}\n```")
    assert not fixture.carrier_returned(f"[{carrier}]")
    assert not fixture.carrier_returned('{"handoff":' + carrier)
