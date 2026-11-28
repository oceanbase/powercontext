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

"""Run the Gateway recall comparison against an isolated Core and a paid model API."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import httpx

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "gateway/crates/pc-gateway-app/tests/fixtures/context_disambiguation.json"
LOCAL_HEADERS = {"x-powercontext-scope-mode": "current"}
INSTRUCTION_PREFIX = "Decide whether the latest user task"
TOOL = {"name": "inspect", "description": "Inspect the requested component", "parameters": {"type": "object"}}


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def process(command, env, directory, name, health, client):
    with (
        (directory / f"{name}.log").open("wb") as log,
        subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=log) as child,
    ):
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and child.poll() is None:
                try:
                    if client.get(health, timeout=0.5).is_success:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                log.flush()
                raise RuntimeError(name, (directory / f"{name}.log").read_text()[-4000:])
            yield
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


@contextmanager
def observe_core(core_base, client):
    # All observations are kept in memory and contain only the synthetic evaluation data.
    checks = []
    preparations = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

        def forward(self):
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            headers = {
                name: self.headers[name]
                for name in ("content-type", "x-powercontext-scope-mode")
                if name in self.headers
            }
            response = client.request(self.command, core_base + self.path, content=body, headers=headers)
            if self.path == "/v1/context/prepare":
                preparations.append((json.loads(body), response.json()))
            if self.path == "/v1/context/check":
                check = json.loads(body)
                checks.append((check["phase"], json.loads(check["payload"]["body"]), response.json()))
            self.send_response(response.status_code)
            self.send_header("content-type", response.headers.get("content-type", "application/json"))
            self.send_header("content-length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = forward
        do_POST = forward

    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", checks, preparations
        finally:
            server.shutdown()
            worker.join()


def seed_memories(client, base, fixture):
    response = client.post(
        base + "/v1/scopes",
        json={"title": "Recall evaluation", "summary": "Synthetic fixtures", "idempotency_key": "recall-evaluation"},
    )
    scope = response.raise_for_status().json()["scope_id"]
    records = {}
    for memory in fixture["memories"]:
        response = client.post(
            base + "/v1/memory/remember",
            headers=LOCAL_HEADERS,
            json={"scope_id": scope, "kind": "constraint", "text": memory["text"]},
        )
        records[memory["id"]] = response.raise_for_status().json()["records"][0]["artifact"]["artifact_id"]
    return scope, records


def model_request(case, model, prefix, deepseek, route):
    messages = [
        {
            "role": "system",
            "content": (
                f"Benchmark session {prefix}. You are a coding assistant. Reply in one short sentence. "
                "Treat prior messages as the active work context.\n"
                + "Use the repository's tests, logs, and stated constraints when they apply.\n"
                * 90
            ),
        },
        *({"role": role, "content": text} for role, text in case["history"]),
    ]
    responses = route == "responses"
    if responses:
        request = {"model": model, "input": messages, "max_output_tokens": 96, "store": False}
    else:
        request = {"model": model, "messages": messages, "max_tokens": 96}
    if deepseek and responses:
        request["reasoning"] = {"effort": "none"}
    elif deepseek:
        request["thinking"] = {"type": "disabled"}
    if case["category"] != "tool-continuation":
        messages.append({"role": "user", "content": case["current"]})
    elif responses:
        request["tools"] = [{"type": "function", **TOOL}]
        messages.extend([
            {"type": "function_call", "name": "inspect", "call_id": "call_inspect", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_inspect", "output": case["current"]},
        ])
    else:
        request["tools"] = [{"type": "function", "function": TOOL}]
        messages.extend([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_inspect", "type": "function", "function": {"name": "inspect", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_inspect", "content": case["current"]},
        ])
    return request


def input_tail(payload):
    messages = payload.get("messages") or payload["input"]
    content = messages[-1].get("content")
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in (content or []) if isinstance(part, dict))


def call_result(payload, verdict, purpose, route):
    usage = payload.get("usage") or {}
    input_key, output_key = (
        ("input_tokens", "output_tokens") if route == "responses" else ("prompt_tokens", "completion_tokens")
    )
    cache = (usage.get(input_key + "_details") or {}).get("cached_tokens")
    if cache is None:
        cache = usage.get("prompt_cache_hit_tokens")
    result = {
        "purpose": purpose,
        "decision": verdict.get("decision"),
        "input_tokens": usage.get(input_key),
        "output_tokens": usage.get(output_key),
        "cached_tokens": cache,
    }
    if purpose == "auxiliary" and verdict.get("decision") == "allow":
        if route == "responses":
            result["output"] = "".join(
                part["text"]
                for item in payload.get("output", [])
                if item["type"] == "message"
                for part in item.get("content", [])
                if part["type"] == "output_text"
            )
        else:
            choices = payload.get("choices") or []
            if choices:
                result["output"] = choices[0].get("message", {}).get("content")
    return result


def run_case(client, base, arm, case, iteration, prefix, args, checks, preparations, memories):
    checks.clear()
    preparations.clear()
    started = time.perf_counter()
    response = client.post(
        f"{base}/gateway/v1/{args.route}",
        headers={"x-powercontext-conversation-id": str(uuid.uuid4())},
        json=model_request(case, args.model, prefix, args.deepseek, args.route),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if response.status_code == 402:
        response.raise_for_status()
    calls = []
    injected = ""
    current_purpose = "main"
    for phase, payload, verdict in checks:
        if phase == "before_send":
            last = input_tail(payload)
            auxiliary = last.startswith(INSTRUCTION_PREFIX)
            current_purpose = "auxiliary" if auxiliary else "main"
            if not auxiliary and verdict.get("decision") == "allow":
                injected = last
        if phase == "before_release":
            calls.append(call_result(payload, verdict, current_purpose, args.route))
    content = "\n".join((result.get("context") or {}).get("content") or "" for _, result in preparations)
    returned = [memory["id"] for memory in memories if memory["text"] in content]
    occurrences = [memory["id"] for memory in memories for _ in range(injected.count(memory["text"]))]
    expected = set(case["expected_memory_ids"])
    return {
        "arm": arm,
        "temperature": "cold" if iteration == 0 else "warm",
        "repetition": iteration + 1,
        "id": case["id"],
        "category": case["category"],
        "latency_ms": round(elapsed_ms, 2),
        "status": response.status_code,
        "error": response.json() if response.is_error else None,
        "expected_recall": case["recall"],
        "queries": [request["query"] for request, _ in preparations],
        "returned_memory_ids": returned,
        "injected_memory_ids": occurrences,
        "expected_memory_ids": sorted(expected),
        "hit": expected.issubset(occurrences) if expected else None,
        "missed_prepare": bool(expected and not preparations),
        "unexpected_injection": sorted(set(occurrences) - expected),
        "duplicates": len(occurrences) - len(set(occurrences)),
        "attribution_label": case["attribution"],
        "calls": calls,
    }


def summarize(rows, rates):
    calls = [call for row in rows for call in row["calls"]]
    known = [call for call in calls if call["cached_tokens"] is not None and call["input_tokens"] is not None]
    billable = [call for call in known if call["output_tokens"] is not None]
    input_tokens = sum(call["input_tokens"] for call in known)
    cache_tokens = sum(call["cached_tokens"] for call in known)
    latency = sorted(row["latency_ms"] for row in rows)
    return {
        "requests": len(rows),
        "model_calls_observed_at_release": len(calls),
        "failed_requests": sum(row["status"] >= 300 for row in rows),
        "recall_hits": sum(row["hit"] is True for row in rows),
        "recall_expected": sum(row["expected_recall"] for row in rows),
        "missed_prepares": sum(row["missed_prepare"] for row in rows),
        "unexpected_injections": sum(bool(row["unexpected_injection"]) for row in rows),
        "duplicates": sum(row["duplicates"] for row in rows),
        "cache_observations": len(known),
        "cache_hit_calls": sum(call["cached_tokens"] > 0 for call in known),
        "cached_tokens": cache_tokens,
        "known_input_tokens": input_tokens,
        "costed_calls": len(billable),
        "observed_token_cost_usd": sum(
            (
                call["cached_tokens"] * rates[0]
                + (call["input_tokens"] - call["cached_tokens"]) * rates[1]
                + call["output_tokens"] * rates[2]
            )
            / 1_000_000
            for call in billable
        )
        if rates and billable
        else None,
        "p50_ms": latency[math.ceil(len(latency) * 0.5) - 1],
        "p95_ms": latency[math.ceil(len(latency) * 0.95) - 1],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", type=Path, default=ROOT / "gateway/target/debug/pc-gateway")
    parser.add_argument("--upstream", required=True, help="Exact upstream URL for the selected route")
    parser.add_argument("--route", choices=("chat/completions", "responses"), default="chat/completions")
    parser.add_argument("--model", required=True)
    parser.add_argument("--deepseek", action="store_true", help="Disable DeepSeek thinking mode in both arms")
    parser.add_argument(
        "--enforce", action="store_true", help="Enable Core carrier enforcement for standard wire fields"
    )
    parser.add_argument("--runs", type=int, default=3, help="One cold run and subsequent warm runs per arm")
    parser.add_argument("--cases", type=int, default=24, help="Limit cases for a smoke run")
    parser.add_argument("--rates", type=float, nargs=3, metavar=("CACHE_HIT", "CACHE_MISS", "OUTPUT"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = os.environ.get("PC_GATEWAY_UPSTREAM_API_KEY")
    if not key or args.runs < 1 or not 1 <= args.cases <= 24:
        parser.error("Set PC_GATEWAY_UPSTREAM_API_KEY, use at least one run, and choose 1-24 cases")
    upstream_env = "PC_GATEWAY_UPSTREAM_URL_RESPONSES" if args.route == "responses" else "PC_GATEWAY_UPSTREAM_URL_CHAT"
    protocol = "openai-responses" if args.route == "responses" else "openai-chat"
    fixture = json.loads(FIXTURE.read_text())
    protection = {
        "mode": "enforcing" if args.enforce else "disabled",
        "targets": {
            "default": {
                "endpoint": args.upstream,
                "model": args.model,
                "protocol": protocol,
                "context_window_tokens": 65536,
                "max_output_tokens": 512,
                "validated_utf8_token_bound": True,
            }
        },
    }
    run_id = uuid.uuid4().hex
    rows = []
    with TemporaryDirectory(prefix="pc-gateway-recall-") as temp, ExitStack() as stack:
        directory = Path(temp)
        client = stack.enter_context(httpx.Client(trust_env=False, timeout=30))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PC_GATEWAY_", "POWERCONTEXT_", "OTEL_"))}
        env["no_proxy"] = env["NO_PROXY"] = "127.0.0.1,localhost"
        core_port = free_port()
        core_base = f"http://127.0.0.1:{core_port}"
        stack.enter_context(
            process(
                [
                    sys.executable,
                    "-c",
                    "from powercontext.cli.app import main; main()",
                    "server",
                    "run",
                    "--no-env-file",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(core_port),
                ],
                dict(
                    env,
                    POWERCONTEXT_HOME=str(directory / "home"),
                    POWERCONTEXT_SERVER_DATABASE_KIND="sqlite",
                    POWERCONTEXT_SERVER_DATABASE_URL=f"sqlite+aiosqlite:///{directory / 'core.db'}",
                    POWERCONTEXT_SERVER_ACCESS_MODE="disabled",
                    POWERCONTEXT_SERVER_MCP_ENABLED="false",
                    POWERCONTEXT_SERVER_DASHBOARD_ENABLED="false",
                    POWERCONTEXT_SERVER_LOGGING_ACCESS="false",
                    POWERCONTEXT_SERVER_RUNTIME='{"artifact_processing_families":[]}',
                    POWERCONTEXT_SERVER_INFERENCE=json.dumps({"protection": protection}),
                ),
                directory,
                "core",
                core_base + "/health/ready",
                client,
            )
        )
        scope, records = seed_memories(client, core_base, fixture)
        proxy, checks, preparations = stack.enter_context(observe_core(core_base, client))
        gateways = {}
        for arm, setting in [("baseline", "off"), ("experiment", "on")]:
            port = free_port()
            base = f"http://127.0.0.1:{port}"
            stack.enter_context(
                process(
                    [str(args.gateway.resolve())],
                    dict(
                        env,
                        PC_GATEWAY_LISTEN=f"127.0.0.1:{port}",
                        PC_GATEWAY_CORE_BASE=proxy,
                        PC_GATEWAY_UPSTREAM_API_KEY=key,
                        PC_GATEWAY_DEFAULT_SCOPE=scope,
                        PC_GATEWAY_INJECTION="on",
                        PC_GATEWAY_STEERING="off",
                        PC_GATEWAY_CONTEXT_DISAMBIGUATION=setting,
                        **{upstream_env: args.upstream},
                    ),
                    directory,
                    arm,
                    base + "/healthz",
                    client,
                )
            )
            gateways[arm] = base
        for case in fixture["cases"][: args.cases]:
            for iteration in range(args.runs):
                for arm, base in gateways.items():
                    prefix = f"{arm} {run_id} {case['id']}"
                    rows.append(
                        run_case(
                            client, base, arm, case, iteration, prefix, args, checks, preparations, fixture["memories"]
                        )
                    )
            print(case["id"], flush=True)
    pairs = zip(
        (row for row in rows if row["arm"] == "baseline"),
        (row for row in rows if row["arm"] == "experiment"),
        strict=True,
    )
    regressions = [
        experiment
        for baseline, experiment in pairs
        if (baseline["hit"] and not experiment["hit"])
        or (baseline["status"] < 300 <= experiment["status"])
        or (set(experiment["unexpected_injection"]) - set(baseline["unexpected_injection"]))
        or experiment["duplicates"] > baseline["duplicates"]
    ]
    summary = {}
    for arm in gateways:
        arm_rows = [row for row in rows if row["arm"] == arm]
        groups = {
            "all": arm_rows,
            **{name: [row for row in arm_rows if row["temperature"] == name] for name in ("cold", "warm")},
            **{
                name: [row for row in arm_rows if row["category"] == name]
                for name in ("ambiguous", "specified", "task-switch", "tool-continuation")
            },
        }
        summary[arm] = {group: summarize(items, args.rates) for group, items in groups.items() if items}
    args.output.write_text(
        json.dumps(
            {
                "model": args.model,
                "route": args.route,
                "core_enforcement": args.enforce,
                "runs": args.runs,
                "token_rates_per_million": args.rates,
                "memory_artifact_ids": records,
                "summary": summary,
                "regressions": regressions,
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
