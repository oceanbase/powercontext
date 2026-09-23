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

"""Opt-in live-model routing evaluation of catalogs exported by host registration tests.

Tool selection is made by the real model with no forced tool choice. The evaluator
does not execute mutations: controlled tool replies isolate routing and reporting
from backend availability. Actual host/runtime tests separately verify execution.
"""

# Chinese user prompts deliberately retain native punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import jsonschema
from dotenv import dotenv_values
from integration_guidance_handoff import HandoffFixture
from integration_guidance_native import NATIVE_HOSTS, NativeHandoffSession
from integration_guidance_skills import SkillReadingModel, with_skill_resources

ROUTES = {
    "search": {"pc_search", "search_memory", "powercontext_search_memory", "powercontext_memory_search"},
    "save": {"pc_remember", "remember_memory", "powercontext_remember", "powercontext_memory_store"},
    "inventory": {
        "pc_memory_list",
        "list_memory_entries",
        "powercontext_list_memory_entries",
        "powercontext_memory_list",
    },
    "handoff": {
        "handoff_current_work",
        "powercontext_handoff_current_work",
        "pc_handoff_current",
    },
    "review": {
        "pc_review_list",
        "list_artifact_candidates",
        "powercontext_list_artifact_candidates",
        "powercontext_review_list",
    },
}
CASES = {
    "ordinary": (
        "none",
        (
            "Write a pure Python function that adds two numbers. Show the code in your reply.",
            "写一个将两个数相加的纯 Python 函数，在回复中展示代码。",
        ),
    ),
    "sufficient_context": (
        "none",
        (
            "Current verified context: Aurora uses OceanBase and its README is complete. Summarize these facts in one sentence.",
            "当前已核实的上下文：Aurora 使用 OceanBase，README 已完成。用一句话概括这些事实。",
        ),
    ),
    "search": (
        "search",
        (
            "Search my saved memories for Aurora's deployment decision.",
            "搜索已保存的记忆，查找 Aurora 的部署决定。",
        ),
    ),
    "inventory": (
        "inventory",
        (
            "List the active saved Memory entries in this Scope so I can inspect the collection.",
            "列出这个 Scope 已保存的有效 Memory 条目，让我盘点整个记忆清单。",
        ),
    ),
    "save": (
        "save",
        (
            "Remember for future Aurora work that the deployment color is violet-cedar-1520.",
            "请记住，供今后的 Aurora 工作使用：部署颜色是 violet-cedar-1520。",
        ),
    ),
    "preview": (
        "none",
        (
            "Draft a Memory entry about Aurora using OceanBase, but do not save it.",
            "起草一条关于 Aurora 使用 OceanBase 的 Memory，但不要保存。",
        ),
    ),
    "handoff": (
        "handoff",
        (
            "Prepare a temporary handoff of this work; do not commit a durable milestone. Objective: document Aurora. "
            "Verified progress: the README is complete and its links passed validation. Next action: review the examples. "
            "No blockers or code changes. Use these inspected facts. Return the complete prepared carrier as JSON.",
            "把这项工作准备成临时交接，不要提交持久里程碑。目标是编写 Aurora 文档；已核实进展：README 已完成，链接验证通过。"
            "下一步是审查示例。没有阻塞，也没有代码改动。使用这些已检查的事实。以 JSON 返回完整的临时交接载体。",
        ),
    ),
    "handoff_request": (
        "handoff",
        (
            "Handoff this work. Objective: document Aurora. Inspected progress: the README is complete and its links "
            "passed validation. Next action: review the examples. No blockers or code changes. "
            "Return the complete prepared carrier as JSON.",
            "交接当前工作。目标是编写 Aurora 文档；已检查的进展：README 已完成，链接验证通过。下一步是审查示例。"
            "没有阻塞，也没有代码改动。以 JSON 返回完整的临时交接载体。",
        ),
    ),
    "review": (
        "review",
        (
            "Show the pending PowerContext artifact candidates for review. Do not approve or modify them.",
            "展示 PowerContext 待审核的产物候选，不要批准或修改它们。",
        ),
    ),
    "empty_search": (
        "search",
        (
            "Search my saved memories for Aurora's deployment color and tell me what you found.",
            "搜索已保存的记忆，查找 Aurora 的部署颜色，并告诉我查到了什么。",
        ),
    ),
    "failed_save": (
        "save",
        (
            "Save this Memory for future use: Aurora deploys on violet-cedar-1520. Tell me whether it was saved.",
            "保存这条 Memory 供以后使用：Aurora 部署在 violet-cedar-1520。告诉我是否保存成功。",
        ),
    ),
    "unavailable_save": (
        "save",
        (
            "Remember for future work that Aurora uses OceanBase. Tell me whether you saved it.",
            "请记住 Aurora 使用 OceanBase，供以后的工作使用。告诉我是否已保存。",
        ),
    ),
}


for _case in ("search", "handoff"):
    _route, _prompts = CASES[_case]
    CASES[f"skill_{_case}"] = (
        _route,
        (
            "First read the relevant available PowerContext Skill workflow for this request. " + _prompts[0],
            "先阅读与此请求相关的可用 PowerContext Skill 流程。" + _prompts[1],
        ),
    )


def skill_description(skill: dict[str, Any]) -> str:
    if "description" in skill:
        return str(skill["description"])
    match = re.search(r"^description: (.+)$", skill.get("content", ""), re.MULTILINE)
    return match[1] if match else "PowerContext workflow guidance."


class CompletionModel(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


class ModelClient:
    def __init__(self, client: httpx.AsyncClient, url: str, model: str, max_tokens: int) -> None:
        self.client, self.url, self.model, self.max_tokens = client, url, model, max_tokens

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
            payload["tool_choice"] = "auto"
        response = await self.client.post(self.url, json=payload)
        if not response.is_success:
            message = f"Model endpoint returned HTTP {response.status_code}"
            raise RuntimeError(message)
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            message = "Model output was truncated; increase --max-tokens before judging routing"
            raise RuntimeError(message)
        return choice["message"]


def validate_message(response: dict[str, Any]) -> list[dict[str, Any]]:
    if re.search(r"<\s*(?:tool_call|function[=>])", response.get("content") or "", re.IGNORECASE):
        message = "Model simulated a tool call in text instead of using the available tool protocol"
        raise ValueError(message)
    calls = response.get("tool_calls") or []
    for call in calls:
        if not isinstance(json.loads(call["function"]["arguments"]), dict):
            message = f"Arguments for {call['function']['name']} must be a JSON object"
            raise TypeError(message)
    return calls


def scenario_messages(catalog: dict[str, Any], prompt: str, skill_mode: str) -> list[dict[str, Any]]:
    guidance = catalog["guidance"] + (
        "\nThis evaluation session is already bound to Scope fixture-scope. Preserve that Scope in "
        "arguments when a tool requires it. Use only the tools in this request."
    )
    skill = catalog.get("skill")
    if skill and skill_mode == "loaded":
        guidance += f"\nLoaded Skill {skill['name']}:\n{skill['content']}"
    elif skill and skill_mode == "unloaded":
        for visible in catalog.get("skills", [skill]):
            guidance += f"\nOptional Skill catalog: {visible['name']}: {skill_description(visible)}"
    return [{"role": "system", "content": guidance}, {"role": "user", "content": prompt}]


def controlled_reply(case: str) -> dict[str, Any]:
    if case == "scope":
        return {
            "scope_id": "fixture-scope",
            "title": "Aurora",
            "summary": "Evaluation fixture",
            "parent_scope_id": None,
        }
    if case == "failed_save":
        return {"ok": False, "code": "unavailable", "message": "Memory write did not complete."}
    if case == "empty_search":
        return {"ok": True, "data": {"hits": []}}
    return {"ok": True, "data": {"status": "saved", "entry": {"text": "Aurora deploys on violet-cedar-1520."}}}


def catalog_arguments(call: dict[str, Any], catalog: dict[str, Any], *, native: bool = False) -> dict[str, Any]:
    name = call["function"]["name"]
    if name not in catalog:
        raise ValueError("Selected an unavailable tool: " + name)
    arguments = json.loads(call["function"]["arguments"])
    schema = catalog[name].get("parameters", {})
    # Native adapters select declared arguments when constructing an HTTP request.
    # Validate their declared inputs here; execute the original call through the
    # adapter below so the HTTP check observes its actual mapping and defaults.
    declared = (
        {key: value for key, value in arguments.items() if key in schema["properties"]}
        if (native and isinstance(arguments, dict) and "properties" in schema)
        else arguments
    )
    jsonschema.validate(declared, schema)
    for field in ("scope_id", "explicit_scope_id"):
        if declared.get(field) is not None and declared[field] != "fixture-scope":
            message = f"{name}.{field}: expected bound Scope fixture-scope, got {declared[field]!r}"
            raise ValueError(message)
    return arguments


def append_results(messages: list[dict[str, Any]], response: dict[str, Any], case: str) -> None:
    calls = validate_message(response)
    messages.append(response)
    messages.extend(
        {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(controlled_reply(case))} for call in calls
    )


async def check_result_reporting(
    model: CompletionModel,
    record: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    response: dict[str, Any],
    expected: set[str],
) -> None:
    case = record["case"]
    catalog = {tool["name"]: tool for tool in tools}
    append_results(messages, response, case)
    followup = await model.complete(messages, tools)
    extra = validate_message(followup)
    for call in extra:
        catalog_arguments(call, catalog)
    # A focused reformulation after empty search is reasonable; inventory or writing is not.
    if case == "empty_search" and extra and {call["function"]["name"] for call in extra} <= expected:
        record["search_reformulation"] = extra
        append_results(messages, followup, case)
        followup = await model.complete(messages, tools)
    record["controlled_results"] = [message for message in messages if message["role"] == "tool"]
    record["final_response"] = followup.get("content")
    record["followup_calls"] = validate_message(followup)
    for call in record["followup_calls"]:
        catalog_arguments(call, catalog)
    # A confirmed write/failure is terminal; empty retrieval has a bounded evaluation budget.
    record["routing_passed"] &= not record["followup_calls"] and bool(record["final_response"])


async def run_scenario(
    model: CompletionModel,
    catalog: dict[str, Any],
    case: str,
    language: int,
    skill_mode: str,
) -> dict[str, Any]:
    route, prompts = CASES[case]
    record: dict[str, Any] = {
        "host": catalog["host"],
        "case": case,
        "language": ("en", "zh")[language],
        "skill_mode": skill_mode,
        "prompt": prompts[language],
        "arguments_passed": False,
    }
    reader = None
    if "skill_resources" in catalog:
        reader = SkillReadingModel(model, catalog["skill_resources"], available=skill_mode != "unavailable")
        model = reader
    try:
        catalog = {**catalog, **catalog.get("variants", {}).get(case, {})}
        tools = [tool for tool in catalog["tools"] if case != "unavailable_save" or tool["name"] not in ROUTES["save"]]
        expected = ROUTES.get(route, set()) & {tool["name"] for tool in tools}
        messages = scenario_messages(catalog, prompts[language], skill_mode)
        response = await model.complete(messages, tools)
        record["model_response"] = {key: response.get(key) for key in ("content", "tool_calls")}
        initial = validate_message(response)
        if (
            route in ROUTES
            and initial
            and {call["function"]["name"] for call in initial}
            <= {
                "resolve_scope_binding",
                "powercontext_resolve_scope_binding",
            }
        ):
            # MCP Skills legitimately resolve the host binding before selecting a scoped operation.
            record["scope_resolution"] = initial
            catalog_tools = {tool["name"]: tool for tool in tools}
            for call in initial:
                catalog_arguments(call, catalog_tools)
            append_results(messages, response, "scope")
            response = await model.complete(messages, tools)
        record.update(
            expected=sorted(expected), first_response=response.get("content"), calls=response.get("tool_calls") or []
        )
        calls = validate_message(response)
        catalog_tools = {tool["name"]: tool for tool in tools}
        for call in calls:
            catalog_arguments(call, catalog_tools, native=route == "handoff" and catalog["host"] in NATIVE_HOSTS)
        record["arguments_passed"] = True
        actual = {call["function"]["name"] for call in calls}
        record["routing_passed"] = (
            bool(actual) and actual <= expected if expected else not actual and bool(response.get("content"))
        )
        record["final_response"] = response.get("content")
        if route == "handoff" and calls:
            await check_handoff_sequence(model, record, messages, tools, response)
        elif calls and case in ("save", "failed_save", "empty_search"):
            await check_result_reporting(model, record, messages, tools, response, expected)
    except (
        TypeError,
        ValueError,
        KeyError,
        RuntimeError,
        TimeoutError,
        httpx.HTTPError,
        jsonschema.ValidationError,
    ) as error:
        detail = (
            f"{list(error.absolute_path)}: {error.message}"
            if isinstance(error, jsonschema.ValidationError)
            else str(error)
        )
        record.update(routing_passed=False, arguments_passed=False, error=type(error).__name__ + ": " + detail)
    if reader is not None:
        reader.record_into(record, case)
    apply_reporting_review(record, {})
    return record


def apply_reporting_review(record: dict[str, Any], reviews: dict[str, Any]) -> None:
    """Bind semantic review to the exact transcript; routing alone cannot qualify a case."""
    ignored = {"review_key", "reporting_passed", "reporting_review", "acceptance_passed"}
    transcript = {key: value for key, value in record.items() if key not in ignored}
    key = hashlib.sha256(json.dumps(transcript, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    review = reviews.get(key)
    if review is not None and (
        not isinstance(review, dict)
        or type(review.get("passed")) is not bool
        or not isinstance(review.get("reason"), str)
        or not review["reason"].strip()
    ):
        message = f"Invalid reporting review for {key}: require passed (boolean) and a nonempty reason"
        raise ValueError(message)
    record.update(
        review_key=key,
        reporting_passed=None if review is None else review["passed"],
        reporting_review=review,
        acceptance_passed=bool(
            record.get("routing_passed") and record.get("arguments_passed") and review and review["passed"]
        ),
    )


async def check_handoff_sequence(
    model: CompletionModel,
    record: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    response: dict[str, Any],
) -> None:
    native = NativeHandoffSession(record["host"]) if record["host"] in NATIVE_HOSTS else None
    try:
        await _handoff_sequence(model, record, messages, tools, response, native)
    finally:
        if native is not None:
            record["http_requests"] = native.requests
            await native.close()


async def _handoff_sequence(
    model: CompletionModel,
    record: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    response: dict[str, Any],
    native: NativeHandoffSession | None,
) -> None:
    fixture = HandoffFixture()
    catalog = {tool["name"]: tool for tool in tools}
    record["handoff_steps"] = steps = []
    record["controlled_results"] = results = []
    record["routing_passed"] = False
    # Capture, activate/prepare, finalize, and a terminal response fit this budget.
    # Inspect every response, including calls after the successful preparation.
    for _ in range(6):
        calls = validate_message(response)
        record["final_response"] = response.get("content")
        if not calls:
            record["routing_passed"] = fixture.carrier_returned(response.get("content") or "")
            if not record["routing_passed"]:
                record["handoff_failure"] = "No complete, exact prepared carrier was returned"
            return
        steps.append({"content": response.get("content"), "calls": calls})
        if len(calls) != 1:
            message = "Handoff operations require the preceding result; parallel writes are not valid"
            raise ValueError(message)
        call = calls[0]
        name = call["function"]["name"]
        arguments = catalog_arguments(call, catalog, native=native is not None)
        result = await native.call(name, arguments, fixture) if native else fixture.respond(name, arguments)
        reply = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
        results.append(reply)
        messages.extend([response, reply])
        response = await model.complete(messages, tools)
    record["handoff_failure"] = "Handoff exceeded the bounded multi-turn evaluation budget"


async def evaluate(args: argparse.Namespace) -> int:
    settings = dotenv_values(args.env_file)
    model_name = args.model or settings.get("LLM_MODEL")
    base_url = args.base_url or settings.get("OPENAI_LLM_BASE_URL")
    key = settings.get("LLM_API_KEY")
    if not model_name or not base_url or not key:
        message = "Provide LLM_MODEL, OPENAI_LLM_BASE_URL, and LLM_API_KEY in the selected environment file"
        raise ValueError(message)
    catalogs = [json.loads(path.read_text(encoding="utf-8")) for path in args.catalog]
    if args.layered_skills:
        catalogs = [with_skill_resources(catalog) for catalog in catalogs]
    output: list[dict[str, Any]] = []
    gate = asyncio.Semaphore(args.concurrency)
    report = {
        "evaluation_version": "layered-skill-workflows-v2"
        if args.layered_skills
        else "native-adapter-reporting-review-v2",
        "model": model_name,
        "provider_host": urlsplit(base_url).hostname,
        "method": "Live model over exported host catalogs; controlled tool replies; no mutations executed",
        "limits": "Routing and argument checks are automated. Result truthfulness requires transcript-bound review; "
        "unreviewed cases never count as acceptance passes. Native Handoff adapters use controlled HTTP replies and "
        "fixture approval, not a real host permission channel. "
        "Skill body presence is controlled; this is not Skill-discovery or full-host execution acceptance.",
        "skill_resource_reads": "Optional evaluation-only reader of shipped resources"
        if args.layered_skills
        else "disabled",
        "max_tokens": args.max_tokens,
        "tool_choice": "auto",
        "catalogs": catalogs,
        "results": output,
    }
    async with httpx.AsyncClient(timeout=90, headers={"Authorization": f"Bearer {key}"}) as client:
        model = ModelClient(client, base_url.rstrip("/") + "/chat/completions", model_name, args.max_tokens)

        async def run(catalog: dict[str, Any], case: str, language: int, skill_mode: str) -> None:
            async with gate:
                record = await run_scenario(model, catalog, case, language, skill_mode)
                output.append(record)
                print(
                    f"{len(output)} {record['host']} {case} {record['language']} {skill_mode}: "
                    f"routing={'PASS' if record['routing_passed'] else 'FAIL'}; acceptance=UNREVIEWED",
                    flush=True,
                )
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        # Fail immediately on missing access/connectivity, before recording any routing judgments.
        await model.complete([{"role": "user", "content": "Reply with OK."}], [])
        await asyncio.gather(
            *(
                run(catalog, case, language, mode)
                for catalog in catalogs
                for case in args.cases
                for language in range(2)
                for mode in args.skill_modes
            )
        )
    failures = sum(not record["routing_passed"] for record in output)
    print(f"Routing: {len(output) - failures}/{len(output)} passed; review final replies in {args.output}")
    print("Acceptance is pending. Review recorded replies, then apply --review-report with --reporting-review.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, action="append")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--review-report", type=Path, help="Apply reporting reviews to an existing report without model calls"
    )
    parser.add_argument(
        "--reporting-review", type=Path, help="JSON mapping review_key to {passed: boolean, reason: string}"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layered-skills", action="store_true", help="Allow recorded reads of shipped Skill resources")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument(
        "--cases", nargs="+", choices=list(CASES), default=[case for case in CASES if not case.startswith("skill_")]
    )
    parser.add_argument(
        "--skill-modes", nargs="+", choices=("loaded", "unloaded", "unavailable"), default=["loaded", "unloaded"]
    )
    args = parser.parse_args()
    if args.review_report:
        if not args.reporting_review:
            parser.error("--review-report requires --reporting-review")
        report = json.loads(args.review_report.read_text(encoding="utf-8"))
        reviews = json.loads(args.reporting_review.read_text(encoding="utf-8"))
        if not report.get("results") or not isinstance(reviews, dict):
            parser.error("Reporting review requires a nonempty results list and a review-key mapping")
        for record in report["results"]:
            apply_reporting_review(record, reviews)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return int(any(not record["acceptance_passed"] for record in report["results"]))
    if not args.catalog or not args.env_file:
        parser.error("Live evaluation requires --catalog and --env-file")
    return asyncio.run(evaluate(args))


if __name__ == "__main__":
    raise SystemExit(main())
