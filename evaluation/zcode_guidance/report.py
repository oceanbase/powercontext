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

"""Replay deterministic routing rules against retained native events and MCP wire replies."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .fixture import CANDIDATE, PREFIX, SCOPE


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scheduled(events: list[dict]) -> list[dict]:
    """Include structured requests rejected before scheduling, without treating them as successful calls."""
    streamed = {}
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") == "model.streaming" and payload.get("kind") == "tool_call":
            identity = payload.get("toolCallId")
            if identity:
                if identity in streamed and streamed[identity] != payload:
                    raise ValueError("conflicting_native_tool_input")
                streamed[identity] = payload
    calls = {}
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") == "tool.updated" and payload.get("kind") == "scheduled":
            identity = payload.get("toolCallId")
            if payload.get("inputOmitted") is True and payload.get("inputRef") == "model_stream":
                stream = streamed.get(identity, {})
                if stream.get("toolName") != payload.get("toolName") or "input" not in stream:
                    raise ValueError("missing_native_tool_input")
                payload = {**payload, "input": stream["input"]}
            if not identity or "input" not in payload or not payload.get("toolName"):
                raise ValueError("invalid_native_tool_event")
            if identity in calls and calls[identity] != payload:
                raise ValueError("conflicting_native_tool_event")
            calls[identity] = payload
    for identity, payload in streamed.items():
        if identity not in calls:
            if "input" not in payload or not payload.get("toolName"):
                raise ValueError("invalid_native_tool_event")
            calls[identity] = payload
    return list(calls.values())


def rejected_inputs(events: list[dict], calls: list[dict]) -> set[str]:
    """Recognize the pinned host's input-schema rejection before handler execution."""
    lifecycle = {}
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") == "tool.updated" and payload.get("kind") != "scheduled":
            lifecycle.setdefault(payload.get("toolCallId"), []).append(payload)
    rejected = set()
    for call in calls:
        observed = lifecycle.get(call["toolCallId"], [])
        # A started handler, another terminal event or contradictory evidence leaves the wire requirement intact.
        if len(observed) != 1:
            continue
        terminal = observed[0]
        error = terminal.get("error", {})
        if (
            terminal.get("kind") == "error"
            and isinstance(error, dict)
            and error.get("type") == "tool_execution_failed"
            and error.get("message") == "Tool input failed inputSchema validation"
        ):
            rejected.add(call["toolCallId"])
    return rejected


def grade(case: str, turns: list[dict]) -> list[str]:
    """Return failures; an absent call, unfinished turn or wire-only call cannot pass."""
    failures = []
    native, wire = [], []
    for index, turn in enumerate(turns):
        events = turn.get("native_events", [])
        completed = [event for event in events if event.get("type") == "turn.completed"]
        if turn.get("error") or len(completed) != 1 or any(event.get("type") == "turn.failed" for event in events):
            failures.append(f"turn_{index + 1}_incomplete")
        if not completed or completed[0].get("payload", {}).get("response") != turn.get("response"):
            failures.append(f"turn_{index + 1}_response_unverified")
        model_requests = [
            event.get("payload", {})
            for event in events
            if event.get("type") == "session.updated"
            and event.get("payload", {}).get("providerId")
            and event.get("payload", {}).get("modelId")
            and "messageCount" in event.get("payload", {})
        ]
        if not model_requests:
            failures.append(f"turn_{index + 1}_model_unobserved")
        attempts = scheduled(events)
        calls = [item for item in attempts if item["toolName"].startswith(PREFIX)]
        rejected = rejected_inputs(events, calls)
        if rejected:
            failures.append(f"turn_{index + 1}_native_input_rejected")
        if any(
            "powercontext" in item["toolName"].lower() and not item["toolName"].startswith(PREFIX) for item in attempts
        ):
            failures.append(f"turn_{index + 1}_unknown_tool_namespace")
        replies = turn.get("mcp_calls", [])

        def encode(name: str, args: Any) -> str:
            return name + ":" + json.dumps(args, sort_keys=True)

        if (
            any(not isinstance(item["input"], dict) for item in calls if item["toolCallId"] not in rejected)
            or any(not isinstance(item.get("arguments"), dict) for item in replies)
            or Counter(
                encode(item["toolName"][len(PREFIX) :], item["input"])
                for item in calls
                if item["toolCallId"] not in rejected
            )
            != Counter(encode(item["name"], item["arguments"]) for item in replies)
        ):
            failures.append(f"turn_{index + 1}_native_wire_mismatch")
        if any(item.get("decision") == "deny" for item in turn.get("permissions", [])):
            failures.append(f"turn_{index + 1}_out_of_bounds_attempt")
        # Scope verification is permitted, but cannot satisfy a required data call.
        native.append(
            [item for item in calls if item["toolName"][len(PREFIX) :] not in {"get_scope", "resolve_scope_binding"}]
        )
        # Invalid wire arguments make execution incomplete and cannot satisfy a case's required data call.
        valid_replies = [item for item in replies if isinstance(item.get("arguments"), dict)]
        wire.append([item for item in valid_replies if item["name"] not in {"get_scope", "resolve_scope_binding"}])
        if any(
            item["name"] != "resolve_scope_binding" and item["arguments"].get("scope_id") != SCOPE
            for item in valid_replies
        ):
            failures.append(f"turn_{index + 1}_wrong_scope")

    def only(index: int, names: list[str]) -> bool:
        return [item["name"] for item in wire[index]] == names and len(native[index]) == len(names)

    def answer(index: int) -> str:
        return turns[index].get("response", "").strip()

    def delays(index: int) -> bool:
        value = answer(index)
        fenced = re.fullmatch(r"```(?:python|json)?\s*(\[\s*1\s*,\s*2\s*\])\s*```", value, flags=re.IGNORECASE)
        if fenced:
            value = fenced.group(1)
        return re.fullmatch(r"`?\[\s*1\s*,\s*2\s*\]`?", value) is not None

    if case == "ordinary-coding":
        if any(turn.get("mcp_calls") for turn in turns) or any(
            scheduled(turn.get("native_events", [])) for turn in turns
        ):
            failures.append("unnecessary_powercontext_call")
        if not delays(0):
            failures.append("coding_answer_incorrect")
    elif case in {"explicit-save", "failed-save"}:
        if not only(0, ["remember_memory"]):
            failures.append("save_not_single_native_call")
        else:
            call = wire[0][0]
            if (
                call["arguments"].get("kind") != "decision"
                or call["arguments"].get("text") != "Project Atlas uses UTC for all timestamps."
            ):
                failures.append("saved_decision_changed")
            if case == "explicit-save":
                if call["is_error"] or not call["result"].get("entry") or not answer(0).startswith("STATUS: SAVED"):
                    failures.append("save_success_not_confirmed")
            elif (
                not call["is_error"]
                or call["result"].get("error", {}).get("code") != "FIXTURE_WRITE_DENIED"
                or not answer(0).startswith("STATUS: FAILED")
                or "FIXTURE_WRITE_DENIED" not in answer(0)
                or "STATUS: SAVED" in answer(0)
            ):
                failures.append("failed_save_misreported")
    elif case == "empty-search":
        if not only(0, ["search_memory"]) or wire[0][0]["is_error"] or wire[0][0]["result"].get("hits") != []:
            failures.append("empty_search_not_single_native_call")
        if "NO_MATCHES" not in answer(0):
            failures.append("empty_search_misreported")
        if len(turns) != 2 or turns[1].get("mcp_calls") or scheduled(turns[1].get("native_events", [])):
            failures.append("empty_search_expanded_or_not_stopped")
        if len(turns) == 2 and not delays(1):
            failures.append("coding_answer_incorrect")
    elif case == "stale-approval":
        if not only(0, ["get_artifact_candidate"]) or wire[0][0]["result"].get("version") != 1:
            failures.append("review_not_version_1")
        if len(turns) != 2 or not only(1, ["approve_artifact_candidate", "get_artifact_candidate"]):
            failures.append("approval_retried_or_reread_missing")
        else:
            approval, reread = wire[1]
            if (
                approval["arguments"].get("expected_version") != 1
                or not approval["is_error"]
                or approval["result"].get("error", {}).get("code") != "candidate_conflict"
            ):
                failures.append("stale_approval_not_conflicted")
            current = reread["result"]
            if (
                reread["is_error"]
                or current.get("version") != 2
                or current.get("status") != "pending"
                or current.get("result_artifact") is not None
            ):
                failures.append("changed_proposal_not_pending")
            if "candidate_conflict" not in answer(1) or "pending" not in answer(1).lower():
                failures.append("conflict_misreported")
        if any(call["arguments"].get("candidate_id") != CANDIDATE for page in wire for call in page):
            failures.append("wrong_candidate")
    else:
        failures.append("unknown_case")
    return failures


def replay(root: Path) -> dict:
    """Use only the run's input snapshot, checking all referenced bytes before grading."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("evidence_digest_mismatch")
    required = {"inputs/cases.json", "inputs/skill-lock.json", "inputs/catalog.json", "provenance.json"}
    if not required.issubset(manifest):
        raise ValueError("missing_input_evidence")
    lock = json.loads((root / "inputs/skill-lock.json").read_text(encoding="utf-8"))
    for name, digest in lock["files"].items():
        if manifest.get("inputs/skill/" + name) != digest:
            raise ValueError("archived_skill_pin_mismatch")
    cases = json.loads((root / "inputs/cases.json").read_text(encoding="utf-8"))
    expected = {"ordinary-coding": 1, "explicit-save": 1, "empty-search": 2, "failed-save": 1, "stale-approval": 2}
    if len(cases) != len(expected) or {case["id"]: len(case["turns"]) for case in cases} != expected:
        raise ValueError("incomplete_case_suite")
    tools = json.loads((root / "inputs/catalog.json").read_text(encoding="utf-8"))
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    selection = provenance.get("selected_model", {})
    if provenance.get("model_mode") != "live" or provenance.get("server_mode") != "controlled-mcp" or not selection:
        raise ValueError("live_model_provenance_required")
    if not {
        "remember_memory",
        "search_memory",
        "list_memory_entries",
        "approve_artifact_candidate",
        "revise_artifact_candidate",
        "publish_artifact",
    }.issubset({tool["name"] for tool in tools}):
        raise ValueError("incomplete_tool_catalog")
    results = []
    for arm in ("with_skill", "without_skill"):
        for case in cases:
            observed = set()
            skill_loaded = False
            name = f"{arm}/{case['id']}/turns.json"
            if name not in manifest:
                failures = ["missing_native_evidence"]
            else:
                turns = json.loads((root / name).read_text(encoding="utf-8"))
                if len(turns) != len(case["turns"]) or any(
                    item.get("prompt") != prompt for item, prompt in zip(turns, case["turns"], strict=False)
                ):
                    failures = ["turn_input_mismatch"]
                else:
                    failures = grade(case["id"], turns)
                    for turn in turns:
                        for event in turn["native_events"]:
                            payload = event.get("payload", {})
                            if (
                                event.get("type") == "session.updated"
                                and payload.get("providerId")
                                and payload.get("modelId")
                                and "messageCount" in payload
                            ):
                                observed.add((payload["providerId"], payload["modelId"]))
                            if (
                                event.get("type") == "tool.updated"
                                and payload.get("kind") == "result"
                                and payload.get("skillMetadata", {}).get("qualifiedName")
                                == "powercontext:powercontext-project-context"
                                and payload.get("result", {}).get("success") is True
                            ):
                                skill_loaded = True
                    if observed != {(selection.get("providerId"), selection.get("modelId"))}:
                        failures.append("observed_model_mismatch")
                    if arm == "with_skill" and case["id"] != "ordinary-coding" and not skill_loaded:
                        failures.append("skill_body_not_observed")
            for filename in ("installation.json", "mcp-discovery.json"):
                ref = f"{arm}/{case['id']}/{filename}"
                if ref not in manifest:
                    failures.append("missing_native_discovery")
                    continue
                value = json.loads((root / ref).read_text(encoding="utf-8"))
                if filename == "installation.json":
                    if (
                        value.get("enabled") is not True
                        or value.get("skillCount") != (1 if arm == "with_skill" else 0)
                        or value.get("diagnostics")
                    ):
                        failures.append("invalid_native_plugin_discovery")
                else:
                    status = value.get("statuses", {}).get("plugin:powercontext:powercontext", {})
                    if status.get("status") != "connected" or status.get("toolCount") != len(tools):
                        failures.append("incomplete_native_mcp_discovery")
            if f"{arm}/{case['id']}/failure.json" in manifest:
                failures.append("execution_failed")
            incomplete = {
                "execution_failed",
                "missing_native_evidence",
                "turn_input_mismatch",
                "missing_native_discovery",
                "invalid_native_plugin_discovery",
                "incomplete_native_mcp_discovery",
                "observed_model_mismatch",
            }
            complete = not any(
                failure in incomplete
                or re.fullmatch(
                    r"turn_\d+_(incomplete|response_unverified|model_unobserved|native_wire_mismatch)", failure
                )
                for failure in failures
            )
            results.append(
                {
                    "arm": arm,
                    "case": case["id"],
                    "status": "incomplete" if not complete else "failed" if failures else "passed",
                    "execution_complete": complete,
                    "failures": failures,
                    "evidence": name,
                    "observed_models": [
                        {"providerId": provider, "modelId": model} for provider, model in sorted(observed)
                    ],
                    "skill_body_loaded": skill_loaded,
                }
            )
    return {
        "schema": "powercontext.zcode.guidance-report.v1",
        "grader_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "qualified": all(item["execution_complete"] for item in results)
        and all(item["status"] == "passed" for item in results if item["arm"] == "with_skill"),
        "results": results,
        "limitations": [
            "Live ZCode model, controlled MCP replies; no backend or desktop qualification.",
            "One paired run is not a causal or statistical improvement claim.",
            "Answer checks are literal rules; retain raw responses for semantic review.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.run)
    write_json(args.output, report)
    print(json.dumps({"qualified": report["qualified"], "results": report["results"]}, indent=2))
    raise SystemExit(0 if report["qualified"] else 1)


if __name__ == "__main__":
    main()
