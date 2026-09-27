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

"""Reject known skill-up v0.12.0 false-green configurations before a model run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from sync_skill import PROJECT, REPOSITORY, check

NAMESPACE = "mcp__powercontext__"
SCOPE = "skill-up-fixture-scope"
CONTRACT_FIXTURE = "evals/fixtures/repos/powercontext-tool-contract"
REQUIRED_CALLS = {
    "ordinary-coding": set(),
    "explicit-save": {"remember_memory"},
    "empty-search": {"search_memory"},
    "inspect-candidates": {"list_artifact_candidates", "get_artifact_candidate"},
    "failed-save": {"remember_memory"},
}
RULE_TYPES = {
    "tool_called_in_turn",
    "tool_not_called_in_turn",
    "turn_response_contains",
    "turn_response_not_contains",
    "output_contains",
    "output_matches",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def local_file(project: Path, reference: str) -> Path:
    require(isinstance(reference, str) and bool(reference), "A project-relative file reference is required")
    require(not any(char in reference for char in "*?[]"), f"skill-up does not expand case/path globs: {reference}")
    path = (project / reference).resolve()
    require(not Path(reference).is_absolute() and path.is_relative_to(project.resolve()), f"Nonlocal path: {reference}")
    require(path.is_file(), f"Missing suite file: {reference}")
    return path


def document(project: Path, reference: str) -> dict:
    path = local_file(project, reference)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        message = f"Invalid YAML in {reference}: {error}"
        raise ValueError(message) from error
    require(isinstance(value, dict), f"Expected a mapping in {reference}")
    return value


def mocked_fixture(project: Path, mcp: dict) -> dict:
    servers = mcp.get("servers", [])
    require(len(servers) == 1, "The v1 suite requires exactly one powercontext MCP server")
    server = servers[0]
    require("headers" not in server, "MCP headers belong in config_ref, never inline under servers")
    require(
        server.get("name") == "powercontext" and server.get("mode") == "mocked",
        "The v1 suite requires name: powercontext and mode: mocked",
    )
    require(
        not (set(server) - {"name", "mode", "transport", "config_ref"}) and server.get("transport", "stdio") == "stdio",
        "Mocked MCP supplies its own stdio command; endpoint/command/args are unsupported",
    )
    fixture = document(project, server.get("config_ref", ""))
    require(
        set(fixture) == {"tool_responses"},
        "Mocked fixtures must contain only tool_responses; metadata/auth are ignored",
    )
    responses = fixture["tool_responses"]
    require(isinstance(responses, dict) and bool(responses), "Mocked fixture must advertise its tool catalog")
    return responses


def render_tool_contract(catalog: set[str]) -> str:
    """Render neutral top-level argument signatures from the public OpenAPI contract."""
    api = yaml.safe_load((REPOSITORY / "openapi/powercontext.yaml").read_text(encoding="utf-8"))
    schemas = api["components"]["schemas"]

    def resolve(schema: dict) -> dict:
        while "$ref" in schema:
            schema = schemas[schema["$ref"].rsplit("/", 1)[-1]] | {
                key: value for key, value in schema.items() if key != "$ref"
            }
        return schema

    def type_name(schema: dict) -> str:
        resolved = resolve(schema)
        if "enum" in resolved:
            name = " | ".join(json.dumps(value) for value in resolved["enum"])
        elif "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
        elif resolved.get("type") == "array":
            name = f"array<{type_name(resolved['items'])}>"
        elif "oneOf" in resolved or "anyOf" in resolved:
            name = " | ".join(type_name(item) for item in resolved.get("oneOf", resolved.get("anyOf", [])))
        else:
            name = resolved.get("type", "any")
        return f"{name} | null" if resolved.get("nullable") else name

    operations = {}
    for path_item in api["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or operation.get("operationId") not in catalog:
                continue
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            body = resolve(body)
            properties = dict(body.get("properties", {}))
            required = set(body.get("required", []))
            for parameter in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
                if parameter["in"] in {"path", "query"}:
                    properties[parameter["name"]] = parameter["schema"]
                    if parameter.get("required"):
                        required.add(parameter["name"])
            operations[operation["operationId"]] = (properties, required)
    require(set(operations) == catalog, "The mock tool catalog must map to public OpenAPI operations")
    lines = [
        "# PowerContext tool argument reference",
        "",
        "This API reference is identical in both evaluation configurations. It supplies the top-level JSON argument",
        "names and types omitted by the built-in mock tool schemas. It does not prescribe which operations to select.",
        "",
        "Source: `openapi/powercontext.yaml`; validated by `validate_suite.py`.",
        "",
        "Fields listed as required must be present in the tool's argument object. Optional fields may be omitted.",
        "Defaults are shown after `=`. Named types refer to OpenAPI component schemas; this concise reference does",
        "not replace those schemas or enforce their constraints. It contains no scenario values or expected results.",
        "",
    ]
    for operation, (properties, required) in sorted(operations.items()):
        lines.extend([f"## {NAMESPACE}{operation}", ""])
        for label, selected in (("Required", required), ("Optional", set(properties) - required)):
            fields = []
            for name in sorted(selected):
                schema = properties[name]
                default = resolve(schema).get("default")
                suffix = f" = {json.dumps(default)}" if "default" in resolve(schema) else ""
                fields.append(f"`{name}: {type_name(schema)}{suffix}`")
            lines.append(f"- {label}: {', '.join(fields) or '(none)'}.")
        lines.append("")
    return "\n".join(lines)


def case_rules(case: dict, catalog: set[str]) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
    case_id = case["id"]
    turns = case.get("input", {}).get("turns", [])
    require(not case.get("input", {}).get("prompt") and bool(turns), f"{case_id}: use input.turns for turn assertions")
    expected_turns = 2 if case_id == "empty-search" else 1
    require(len(turns) == expected_turns, f"{case_id}: expected {expected_turns} logical turns")
    judge = case.get("judge", {})
    require(
        judge.get("type") == "rule_based" and not judge.get("failure"), f"{case_id}: use rule_based success assertions"
    )
    called = {turn: set() for turn in range(1, len(turns) + 1)}
    forbidden = {turn: set() for turn in called}
    for rule in judge.get("success", []):
        require(
            isinstance(rule, dict) and len(rule) == 1 and set(rule) <= RULE_TYPES, f"{case_id}: unsupported assertion"
        )
        kind, value = next(iter(rule.items()))
        if kind.startswith(("tool_", "turn_")):
            require(value.get("turn") in called, f"{case_id}: assertion references a nonexistent turn")
        if not kind.startswith("tool_"):
            continue
        name = value.get("name", "")
        require(
            name.startswith(NAMESPACE) and name.removeprefix(NAMESPACE) in catalog,
            f"{case_id}: unknown exact tool name {name!r}",
        )
        tool = name.removeprefix(NAMESPACE)
        target = called if kind == "tool_called_in_turn" else forbidden
        target[value["turn"]].add(tool)
        if kind == "tool_called_in_turn":
            require(
                value.get("args", {}).get("scope_id") == SCOPE,
                f"{case_id}: positive control must assert the fixture Scope",
            )
    return called, forbidden


def validate_case(case: dict, fixture: dict, catalog: set[str]) -> None:
    case_id = case["id"]
    called, forbidden = case_rules(case, catalog)
    required = REQUIRED_CALLS[case_id]
    require(required <= called[1], f"{case_id}: required positive tool control is missing")
    allowed = required | {"get_scope", "resolve_scope_binding"} if required else set()
    require(catalog - allowed <= forbidden[1], f"{case_id}: negative tool coverage is incomplete")
    require(all(not called[turn] & forbidden[turn] for turn in called), f"{case_id}: conflicting tool assertions")
    if case_id == "empty-search":
        require(catalog <= forbidden[2], "empty-search: follow-up coding turn must forbid every PowerContext tool")
        require(fixture["search_memory"]["default"].get("hits") == [], "empty-search: fixture must return no hits")
    if case_id == "failed-save":
        require(
            bool(fixture["remember_memory"]["default"].get("error")),
            "failed-save: write must return a controlled failure",
        )
        rules = case["judge"]["success"]
        require(
            any(rule.get(kind, {}).get("not") for rule in rules for kind in ("output_contains", "output_matches")),
            "failed-save: final output must forbid persistence claims",
        )
    if case_id == "explicit-save":
        require(
            not fixture["remember_memory"]["default"].get("error"),
            "explicit-save: positive control must return success",
        )


def validate(project: Path = PROJECT) -> None:
    """Validate the pinned, mocked Claude Code suite, supplementing native validate."""
    check()
    config = document(project, "evals/eval.yaml")
    require(config.get("environment", {}).get("type") == "none", "The v1 mocked suite uses environment.type: none")
    require(config.get("engine", {}).get("name") == "claude_code", "The v1 host boundary is claude_code only")
    require(config.get("judge", {}).get("type") == "rule_based", "The suite requires deterministic rule_based judging")
    require(config.get("cases", {}).get("parallelism") == 1, "cases.parallelism must remain 1")
    require(config.get("benchmark", {}).get("enabled") is True, "Both Skill-loaded and baseline arms are required")
    report = config.get("report", {})
    require({"json", "junit", "html"} <= set(report.get("formats", [])), "Reports require json, junit and html")
    require("transcript" in report.get("artifacts", []), "Transcript evidence is required")
    skills = config.get("skills", [])
    require(len(skills) == 1, "Install exactly the pinned integration Skill")
    skill = skills[0]
    require(
        skill.get("source") == "local_path" and skill.get("path") == "vendor/powercontext-project-context",
        "Skill input must be the pinned vendor copy",
    )
    require(
        not skill.get("exclude")
        and (not skill.get("include") or set(skill["include"]) == {"SKILL.md", "references/**"}),
        "Do not filter out the pinned Skill guidance",
    )
    local_file(project, skill["path"] + "/SKILL.md")
    fixture = mocked_fixture(project, config.get("mcp", {}))
    catalog = set(fixture)
    require(len(catalog) == 34, "The pinned mock catalog must retain all 34 PowerContext operations")
    contract = local_file(project, CONTRACT_FIXTURE + "/CLAUDE.md")
    require(
        contract.read_text(encoding="utf-8") == render_tool_contract(catalog),
        "Tool argument reference differs from OpenAPI; run validate_suite.py --write-tool-contract",
    )
    files = config.get("cases", {}).get("files", [])
    cases = [document(project, reference) for reference in files]
    require(
        len(cases) == len(REQUIRED_CALLS) and {case.get("id") for case in cases} == set(REQUIRED_CALLS),
        "Keep all five routing/authorization cases, including both positive and negative controls",
    )
    for case in cases:
        require(
            case.get("context") == {"repo_fixture": CONTRACT_FIXTURE},
            f"{case['id']}: both arms need the same unmodified tool argument reference",
        )
        effective = mocked_fixture(project, case["mcp"]) if "mcp" in case else fixture
        require(set(effective) == catalog, f"{case['id']}: override must preserve the complete tool catalog")
        validate_case(case, effective, catalog)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-tool-contract", action="store_true", help="refresh the shared OpenAPI argument reference"
    )
    args = parser.parse_args()
    try:
        if args.write_tool_contract:
            config = document(PROJECT, "evals/eval.yaml")
            catalog = set(mocked_fixture(PROJECT, config.get("mcp", {})))
            target = PROJECT / CONTRACT_FIXTURE / "CLAUDE.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_tool_contract(catalog), encoding="utf-8", newline="\n")
        validate()
    except ValueError as error:
        sys.exit(str(error))
    print("Suite controls, fixtures and Skill pin verified")
