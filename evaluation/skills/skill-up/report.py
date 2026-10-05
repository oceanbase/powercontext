#!/usr/bin/env python3
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

"""Qualify skill-up v0.12.0 results without turning missing evidence into a pass.

Only Claude Code's archived session JSONL is accepted as tool-call evidence.
result.json supplies grading; it does not contain the agent transcript. Tests use
synthetic JSONL and establish parser behavior, not a live model qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

ARMS = ("with_skill", "without_skill")
CASE_TURNS = {"ordinary-coding": 1, "explicit-save": 1, "empty-search": 2, "inspect-candidates": 1, "failed-save": 1}
PREFIX = "mcp__powercontext__"
FIXTURE_SCOPE = "skill-up-fixture-scope"
REQUIRED_CALLS = {
    "explicit-save": {"remember_memory"},
    "empty-search": {"search_memory"},
    "inspect-candidates": {"list_artifact_candidates", "get_artifact_candidate"},
    "failed-save": {"remember_memory"},
}
LIMITATIONS = {
    "C1": (
        "Mocked MCP config_ref supplies static responses; HTTP authentication is not exercised. "
        "Real HTTP headers belong in config_ref, not mcp.servers[]. POWERCONTEXT_CLAUDE_AUTHORIZATION "
        "must be the complete Bearer header, not the Python SDK bare token."
    ),
    "C2": (
        "Tool matching is exact. Recorded names are listed verbatim. Unobserved forbidden names are derived from "
        "the catalog and remain unverified in a real transcript; their absence alone cannot prove name matching."
    ),
    "C3": (
        "Claude Code hooks are disabled and context preparation is not an MCP tool. "
        "Bounded recall, automatic Capture/Flush and memory quality are not measured."
    ),
    "C4": "Claude Code runs with bypassPermissions; this does not qualify interactive approval behavior.",
    "C5": (
        "Fixtures are stateless and parallelism is one; there is no live persistence or cross-session isolation proof. "
        "Fresh workspaces still inherit Claude user settings, native memory and global Skills/MCP."
    ),
    "C6": "skill-up owns the built-in Node mock lifecycle; no live PowerContext Server is started or qualified.",
    "C7": "A deterministic fixture replaces the scope helper; packaged helper/runtime behavior is not qualified.",
    "C8": "Only Claude Code + MCP Skill guidance is in scope; Codex, WorkBuddy and other hosts are not qualified.",
    "mock_contract": (
        "Built-in mock descriptions and argument schemas are generic. Both arms receive the same neutral argument "
        "guidance; this does not validate the live MCP contract or server-side argument validation."
    ),
    "skill_activation": (
        "with_skill means the Skill was installed and available. It does not establish that the agent read the "
        "Skill body or followed its instructions."
    ),
    "failure_semantics": "The failed-save fixture returns a failure payload, not a protocol-level MCP isError.",
    "grading": "Output regular expressions are imperfect semantic checks; retain transcripts for human review.",
    "identity": "Requested/applied model values are configuration, not independent proof of observed model identity.",
}


def native_path(path: Path) -> Path:
    """Allow Windows to open the long artifact paths that glob can enumerate."""
    if os.name != "nt":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def read_json(path: Path, errors: list[str]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"Cannot read {path}: {exc}")
        return None


def read_session(path: Path, prompts: list[str], errors: list[str]) -> list[dict]:
    """Extract raw calls with line references; never infer calls from assistant prose."""
    calls = []
    completed_turns = set()
    pending = {}
    current_turn = 0
    seen = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"Cannot read transcript {path}: {exc}")
        return calls
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            errors.append(f"Invalid transcript JSON at {path}:{line_number}")
            continue
        if not isinstance(event, dict):
            errors.append(f"Invalid transcript event at {path}:{line_number}")
            continue
        if event.get("isApiErrorMessage") or event.get("type") == "error":
            errors.append(f"Engine error recorded in transcript at {path}:{line_number}")
        message = event.get("message") or {}
        content = message.get("content", []) if isinstance(message, dict) else []
        text = (
            content
            if isinstance(content, str)
            else "\n".join(
                block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
            )
            if isinstance(content, list)
            else ""
        )
        if event.get("type") == "user" and text.strip() in prompts:
            next_turn = prompts.index(text.strip()) + 1
            if next_turn != current_turn + 1:
                errors.append(f"Ambiguous/out-of-order logical prompt at {path}:{line_number}")
            current_turn = next_turn
        blocks = content if isinstance(content, list) else []
        results = [
            block
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "tool_result" and event.get("type") == "user"
        ]
        if event.get("type") == "tool_result":
            results = [{"tool_use_id": (event.get("tool_result") or {}).get("call_id")}]
        for result in results:
            if pending.pop(result.get("tool_use_id"), None) != current_turn:
                errors.append(f"Unmatched tool result at {path}:{line_number}")
        if event.get("type") == "assistant":
            completed_turns.discard(current_turn)
            if message.get("stop_reason") == "end_turn" and text.strip() and not pending:
                completed_turns.add(current_turn)
            blocks = [block for block in blocks if isinstance(block, dict) and block.get("type") == "tool_use"]
        elif event.get("type") == "tool_call":
            blocks = [event.get("tool_call")]
        else:
            blocks = []
        for block in blocks:
            if (
                not isinstance(block, dict)
                or not isinstance(block.get("id"), str)
                or not block.get("id")
                or not isinstance(block.get("name"), str)
                or not block.get("name")
            ):
                errors.append(f"Tool call missing id/name at {path}:{line_number}")
                continue
            if block["id"] in seen:
                errors.append(f"Duplicate/ambiguous tool call ID at {path}:{line_number}")
                continue
            seen[block["id"]] = block["name"]
            pending[block["id"]] = current_turn
            completed_turns.discard(current_turn)
            if not current_turn:
                errors.append(f"Tool call before a known logical prompt at {path}:{line_number}")
            arguments = block.get("input", {})
            if not isinstance(arguments, dict):
                errors.append(f"Invalid tool arguments at {path}:{line_number}")
                arguments = {}
            calls.append(
                {
                    "id": block["id"],
                    "name": block["name"],
                    "arguments": arguments,
                    "line": line_number,
                    "turn": current_turn,
                }
            )
    if current_turn != len(prompts) or not prompts:
        errors.append(f"Missing logical user prompt boundaries in {path}")
    for turn in range(1, len(prompts) + 1):
        if turn not in completed_turns:
            errors.append(f"No completed assistant response for turn {turn} in {path}")
    if pending:
        errors.append(f"Missing tool results in {path}: {sorted(pending)}")
    return calls


def case_inventory(iteration: Path, case_id: str, arm: str, row: dict, errors: list[str]) -> list[dict]:
    """Read Claude's final cumulative session, using exact user prompts as boundaries.

    Tool results and injected skill bodies also have role=user, so counting user
    events would assign tool calls to the wrong logical turns.
    """
    turns = []
    run_dir = iteration / case_id / arm / "outputs" / "agent" / "run"
    paths = sorted(run_dir.glob("*.jsonl"))
    all_calls = []
    transcript_hash = None
    if len(paths) != 1:
        errors.append(f"Expected one Claude session JSONL for {case_id}/{arm}, found {len(paths)}")
    else:
        prompts = [item.get("content", "").strip() for item in row.get("turn_results", [])]
        if len(set(prompts)) != len(prompts) or any(not prompt for prompt in prompts):
            errors.append(f"Missing/ambiguous turn prompts for {case_id}/{arm}")
        all_calls = read_session(paths[0], prompts, errors)
        try:
            transcript_hash = hashlib.sha256(paths[0].read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"Cannot hash transcript {paths[0]}: {exc}")
    for turn in range(1, CASE_TURNS[case_id] + 1):
        calls = [call for call in all_calls if call["turn"] == turn]
        turns.append(
            {
                "turn": turn,
                "transcript": str(paths[0].relative_to(iteration)) if len(paths) == 1 else None,
                "transcript_sha256": transcript_hash,
                "recorded_calls": calls,
                "tool_names": dict(sorted(Counter(call["name"] for call in calls).items())),
            }
        )
    return turns


def validate_result(result: dict, errors: list[str], failures: list[str]) -> dict[tuple[str, str], dict]:
    indexed = {}
    rows = result.get("case_results")
    if not isinstance(rows, list):
        errors.append("result.json has no case_results array")
        return indexed
    for row in rows:
        if not isinstance(row, dict):
            errors.append("Invalid case_results entry")
            continue
        key = (row.get("case_id"), row.get("configuration"))
        if key[0] not in CASE_TURNS or key[1] not in ARMS:
            errors.append(f"Unexpected case/configuration: {key}")
            continue
        if key in indexed:
            errors.append(f"Duplicate case/configuration: {key}")
        indexed[key] = row
        if row.get("status") not in {"PASS", "FAIL"} or row.get("error"):
            errors.append(f"Incomplete/errored case {key}: {row.get('status')}: {row.get('error', '')}")
        grading = row.get("grading") or {}
        if not grading.get("assertion_results") or grading.get("status") not in {"PASS", "FAIL"}:
            errors.append(f"Missing completed grading for {key}")
        if row.get("status") == "PASS" and (
            grading.get("status") != "PASS"
            or any(assertion.get("passed") is not True for assertion in grading.get("assertion_results", []))
        ):
            errors.append(f"PASS contradicts grading for {key}")
        if key[1] == "with_skill" and (row.get("status") == "FAIL" or grading.get("status") == "FAIL"):
            failures.append(f"Case/grading failed for {key[0]}/with_skill")
        turn_results = row.get("turn_results") or []
        expected = list(range(1, CASE_TURNS[key[0]] + 1))
        if [item.get("turn_number") for item in turn_results] != expected:
            errors.append(f"Missing/mismatched logical turns for {key}")
        for item in turn_results:
            if item.get("status") not in {"completed", "failed"}:
                errors.append(f"Incomplete/errored turn in {key}: {item}")
    for case_id in CASE_TURNS:
        for arm in ARMS:
            if (case_id, arm) not in indexed:
                errors.append(f"Missing {arm} result for {case_id}")
    return indexed


def build_manifest(iteration: Path, skill_lock: Path, engine_exit_code: int = 0) -> dict:
    """Build a report even for incomplete runs, with a failing evidence gate."""
    errors = []
    failures = []
    source_result = str(iteration / "result.json")
    iteration = native_path(iteration)
    skill_lock = native_path(skill_lock)
    result = read_json(iteration / "result.json", errors)
    if not isinstance(result, dict):
        errors.append("Missing result.json object")
        result = {}
    lock = read_json(skill_lock, errors)
    if (
        not isinstance(lock, dict)
        or lock.get("schema_version") != 1
        or not all(lock.get(key) for key in ("source_path", "source_commit", "files", "content_sha256"))
    ):
        errors.append("Missing/invalid skill-lock pin metadata")
        lock = None
    if engine_exit_code:
        failures.append(f"skill-up exited with code {engine_exit_code}")
    # skill-up preserves the configured alias in result.json. Its factory
    # accepts both names; the checked-in v0.12.0 config uses claude_code.
    if result.get("engine_name") not in {"claude_code", "claude-code"}:
        errors.append(f"Unsupported/unverified engine: {result.get('engine_name')}")
    indexed = validate_result(result, errors, failures)
    fixture = read_json(native_path(Path(__file__).parent / "evals/fixtures/mcp/powercontext-default.json"), errors)
    operations = set(fixture.get("tool_responses", {})) if isinstance(fixture, dict) else set()
    if not operations:
        errors.append("Missing declared MCP fixture catalog")
    catalog = {PREFIX + operation for operation in operations}
    scoped = {PREFIX + name for names in REQUIRED_CALLS.values() for name in names} | {PREFIX + "get_scope"}
    cases = []
    inventory = {arm: Counter() for arm in ARMS}
    for case_id in CASE_TURNS:
        for arm in ARMS:
            row = indexed.get((case_id, arm), {})
            turns = case_inventory(iteration, case_id, arm, row, errors)
            scope_failures = []
            for turn in turns:
                inventory[arm].update(turn["tool_names"])
                for call in turn["recorded_calls"]:
                    arguments = call["arguments"]
                    if (call["name"] in scoped and arguments.get("scope_id") != FIXTURE_SCOPE) or (
                        call["name"] == PREFIX + "resolve_scope_binding"
                        and arguments.get("explicit_scope_id") not in (None, FIXTURE_SCOPE)
                    ):
                        scope_failures.append(
                            f"Wrong/missing Scope: {case_id}/{arm}: {call['name']} at line {call['line']}"
                        )
                for name in turn["tool_names"]:
                    known_operation = any(
                        name == operation or name.endswith(("__" + operation, "." + operation, "/" + operation))
                        for operation in operations
                    )
                    if (known_operation and name not in catalog) or (name.startswith(PREFIX) and name not in catalog):
                        failures.append(f"Unqualified/mismatched tool namespace: {case_id}/{arm}: {name}")
            if arm == "with_skill":
                failures.extend(scope_failures)
                required = {PREFIX + name for name in REQUIRED_CALLS.get(case_id, set())}
                missing = required - set(turns[0]["tool_names"])
                if missing:
                    failures.append(f"Required positive calls not recorded for {case_id}: {sorted(missing)}")
                for turn in turns:
                    allowed = required | {PREFIX + "get_scope", PREFIX + "resolve_scope_binding"}
                    if case_id == "ordinary-coding" or turn["turn"] > 1:
                        allowed = set()
                    forbidden = set(turn["tool_names"]) & catalog - allowed
                    if forbidden:
                        failures.append(
                            f"Forbidden calls recorded in {case_id}/turn-{turn['turn']}: {sorted(forbidden)}"
                        )
            cases.append(
                {
                    "case_id": case_id,
                    "configuration": arm,
                    "status": "FAIL" if scope_failures else row.get("status", "MISSING"),
                    "native_status": row.get("status", "MISSING"),
                    "scope_failures": scope_failures,
                    "error": row.get("error"),
                    "grading": row.get("grading"),
                    "turns": turns,
                }
            )
    if not sum(inventory["with_skill"].values()):
        failures.append("Zero recorded calls in the Skill-installed arm; positive call requirements are not satisfied")
    summaries = {}
    for arm in ARMS:
        passed = sum(case["status"] == "PASS" for case in cases if case["configuration"] == arm)
        summaries[arm] = {"passed": passed, "total": len(CASE_TURNS), "pass_rate": passed / len(CASE_TURNS)}
    observed = set(inventory["with_skill"]) | set(inventory["without_skill"])
    suite_pass = not errors and not failures and summaries["with_skill"]["passed"] == len(CASE_TURNS)
    return {
        "schema_version": 1,
        "qualification_scope": "Claude Code behavior with Skill installed/absent and stateless mocked MCP only",
        "status": "PASS" if suite_pass else "FAIL",
        "evidence_complete": not errors,
        "evidence_errors": errors,
        "qualification_failures": failures,
        "engine_exit_code": engine_exit_code,
        "source_result": source_result,
        "skill_pin": lock,
        "engine_configuration": {
            key: result.get(key)
            for key in (
                "engine_name",
                "model_name",
                "requested_configuration",
                "applied_configuration",
                "observed_configuration",
            )
        },
        "arms": summaries,
        "pass_rate_delta": summaries["with_skill"]["pass_rate"] - summaries["without_skill"]["pass_rate"],
        "baseline_policy": "without_skill FAIL is comparison data; ERROR, SKIP and missing evidence invalidate the run",
        "recorded_tool_inventory": {arm: dict(sorted(counts.items())) for arm, counts in inventory.items()},
        "declared_catalog_names": sorted(catalog),
        "declared_names_without_recorded_verification": sorted(catalog - observed),
        "cases": cases,
        "limitations": LIMITATIONS,
    }


def render_markdown(manifest: dict) -> str:
    lines = [
        "# PowerContext skill behavior qualification",
        "",
        f"Status: **{manifest['status']}**. Scope: {manifest['qualification_scope']}.",
        "",
        "| Arm | Passed / cases | Pass rate |",
        "| --- | --- | --- |",
    ]
    for arm, summary in manifest["arms"].items():
        lines.append(f"| {arm} | {summary['passed']} / {summary['total']} | {summary['pass_rate']:.0%} |")
    lines += [
        "",
        f"Skill-installed minus Skill-absent delta: **{manifest['pass_rate_delta']:+.0%}**.",
        manifest["baseline_policy"],
        "",
        "## Evidence",
        "",
    ]
    lines += [f"- {error}" for error in manifest["evidence_errors"]] or ["Evidence completeness checks passed."]
    lines += ["", "## Qualification failures", ""]
    lines += [f"- {failure}" for failure in manifest["qualification_failures"]] or [
        "No qualification failures recorded."
    ]
    pin = manifest.get("skill_pin") or {}
    lines += [
        "",
        f"Pinned source: `{pin.get('source_path')}` at `{pin.get('source_commit')}`.",
        f"Skill content SHA-256: `{pin.get('content_sha256')}`.",
        "",
        "## Recorded tool names",
        "",
        "Names below are verbatim session records. Logical turns follow exact recorded user prompts.",
        "",
        "| Case | Arm | Turn | Recorded name × count |",
        "| --- | --- | --- | --- |",
    ]
    for case in manifest["cases"]:
        for turn in case["turns"]:
            names = ", ".join(f"`{name}` × {count}" for name, count in turn["tool_names"].items()) or "(none)"
            lines.append(f"| {case['case_id']} | {case['configuration']} | {turn['turn']} | {names} |")
    lines += ["", "Catalog-derived names without recorded verification (including uncalled forbidden operations):", ""]
    lines += [f"- `{name}`" for name in manifest["declared_names_without_recorded_verification"]]
    lines += ["", "## Qualification limits", ""]
    lines += [f"- **{key}**: {value}" for key, value in manifest["limitations"].items()]
    lines += [
        "",
        "See qualification.json for per-call transcript paths, line numbers, hashes, grading and configuration.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteration", required=True, type=Path)
    parser.add_argument("--skill-lock", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--engine-exit-code", type=int, default=0)
    args = parser.parse_args()
    manifest = build_manifest(args.iteration, args.skill_lock, args.engine_exit_code)
    output_dir = native_path(args.output_dir or args.iteration)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "qualification.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "qualification.md").write_text(render_markdown(manifest), encoding="utf-8")
    print(f"{manifest['status']}: {output_dir / 'qualification.md'}")
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
