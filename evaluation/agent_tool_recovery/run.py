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

"""Run failure cases through a real host, real MCP surface, and a disposable real Server."""

from __future__ import annotations

import argparse
import dataclasses
import asyncio
import hashlib
import json
import subprocess
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from . import claude_code, codex
from .claude_code import PluginSession
from .proxy import Fault, Gateway, GatewayEvent, intercept
from .report import ERROR_CODE, HTTP_STATUS, SCORING_VERSION, bind_calls, content_of, leaks, outcome, turn_rows
from .server import LOOPBACK_MCP_HTTP_CLIENT, EvalServer, ServedRequest, actions, observe, seed, serve
from .summary import summarize
from .transcript import TranscriptError

PACKAGE = Path(__file__).resolve().parent
REPOSITORY = PACKAGE.parents[1]
CASES = PACKAGE / "cases.json"
DEFAULT_RUNS = Path.home() / ".powercontext-eval" / "agent_tool_recovery"
# A plugin host runs the same CLI with the repository's PowerContext plugin (hooks and Skill) loaded.
HOSTS = {
    claude_code.HOST: claude_code,
    f"{claude_code.HOST}-plugin": claude_code,
    codex.HOST: codex,
    f"{codex.HOST}-plugin": codex,
}
PLUGIN_HOSTS = frozenset({f"{claude_code.HOST}-plugin", f"{codex.HOST}-plugin"})
DEFAULT_MODELS = {
    claude_code.HOST: "claude-opus-5-5",
    f"{claude_code.HOST}-plugin": "claude-opus-5-5",
    codex.HOST: "gpt-6.1-sol",
    f"{codex.HOST}-plugin": "gpt-6.1-sol",
}
# Server operations only a plugin's hooks call; such requests after the host starts show the hooks ran.
HOOK_OPERATIONS = frozenset(
    {"prepare_context", "prepare_bootstrap_context", "capture_content_source", "resolve_scope_binding", "get_stats"}
)
EVIDENCE = {
    "reset_before_forward": "injected_transport_fault_real_server",
    "drop_after_forward": "injected_transport_fault_real_server",
    "after_forward": "injected_concurrent_write_real_server",
}
TRANSPORT_FAULTS = frozenset({"reset_before_forward", "drop_after_forward"})
# A gateway action runs after the response was sent; a scripted client waits before its dependent call.
ACTION_SETTLE_SECONDS = 2


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases() -> dict[str, dict[str, Any]]:
    return load_manifest()[0]


def load_manifest() -> tuple[dict[str, dict[str, Any]], str]:
    """Return the cases and the hash of the exact bytes they were parsed from."""

    content = CASES.read_bytes()
    cases = {case["case_id"]: case for case in json.loads(content)["cases"]}
    return cases, hashlib.sha256(content).hexdigest()


def server_revision() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed git arguments
            ["git", *args],  # noqa: S607
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    return {"sha": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--", "src"))}


def substitute(value: Any, placeholders: dict[str, Any]) -> Any:
    """Fill ``{name}`` placeholders; a value that is exactly one placeholder keeps the seed's type."""

    if isinstance(value, str):
        name = value[1:-1] if value.startswith("{") and value.endswith("}") else None
        return placeholders[name] if name in placeholders else value.format(**placeholders)
    if isinstance(value, dict):
        return {key: substitute(item, placeholders) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, placeholders) for item in value]
    return value


def faults_of(case: dict[str, Any]) -> tuple[Fault, ...]:
    return (Fault(**case["fault"]),) if case.get("fault") else ()


async def _scripted_call(
    url: str, headers: dict[str, str], name: str, arguments: dict[str, Any], *, structured: bool = False
) -> dict[str, Any]:
    try:
        async with Client(
            StreamableHttpTransport(url, headers=headers, httpx_client_factory=LOOPBACK_MCP_HTTP_CLIENT)
        ) as client:
            result = await client.call_tool(name, arguments, raise_on_error=False)
    except Exception as error:  # noqa: BLE001 - a transport failure is the observation
        return {"transport_error": type(error).__name__}
    text = "\n".join(getattr(item, "text", "") or "" for item in result.content)
    status = HTTP_STATUS.search(text) if result.is_error else None
    code = ERROR_CODE.search(text) if result.is_error else None
    observed: dict[str, Any] = {
        "is_error": result.is_error,
        "http_status": int(status.group(1)) if status else None,
        "error_code": code.group(1) if code else None,
    }
    if structured:
        observed["structured"] = result.structured_content
    return observed


def _preflight_calls(
    case: dict[str, Any], server: EvalServer, gateway: Gateway, placeholders: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    def call(name: str, arguments: dict[str, Any], *, structured: bool = False) -> dict[str, Any]:
        return asyncio.run(_scripted_call(gateway.url, server.host_headers, name, arguments, structured=structured))

    prelude = case.get("preflight_prelude")
    if prelude:
        read = call(prelude["operation_id"], substitute(prelude["arguments"], placeholders), structured=True)
        time.sleep(ACTION_SETTLE_SECONDS)
        placeholders.update({name: read["structured"][key] for name, key in prelude["capture"].items()})
    arguments = substitute(case["preflight_arguments"], placeholders)
    first = call(case["operation_id"], arguments)
    transport = any(fault.mode in TRANSPORT_FAULTS for fault in faults_of(case))
    return first, call(case["operation_id"], arguments) if transport else None


def preflight_case(case: dict[str, Any]) -> dict[str, Any]:
    """Drive the case's target call with a scripted MCP client; no host or model is involved."""

    faults = faults_of(case)
    with serve(case["server_profile"]) as server:
        placeholders = seed(case["seed"], server)
        with intercept(server.mcp_url, faults, actions(server, placeholders)) as gateway:
            first, replay = _preflight_calls(case, server, gateway, placeholders)
            events = gateway.log.snapshot()
        served = [request for request in server.requests.snapshot() if request.internal]
        observed = observe(case.get("observe"), server, placeholders)
    expected = case["expected"]
    if any(fault.mode in TRANSPORT_FAULTS for fault in faults):
        target = [event for event in events if event.operation_id == case["operation_id"]]
        reproduced = "transport_error" in first and target[0].forwarded == (faults[0].mode == "drop_after_forward")
    else:
        reproduced = bool(first.get("is_error")) and (first.get("http_status"), first.get("error_code")) == (
            expected["http_status"],
            expected["error_code"],
        )
    return {
        "case_id": case["case_id"],
        "reproduced": reproduced,
        "first": first,
        "replay_after_fault": replay,
        "gateway": [{"operation_id": e.operation_id, "fault": e.fault, "forwarded": e.forwarded} for e in events],
        "server_requests": [{"operation_id": r.operation_id, "status": r.status} for r in served],
        "final_state": observed,
    }


def run_case(  # noqa: PLR0913
    case: dict[str, Any], *, host: str, model: str, run_dir: Path, attempt: int, manifest_sha256: str
) -> dict[str, Any]:
    """Execute one attempt, keep every input of its report privately, and score it."""

    adapter = HOSTS[host]
    private_dir = run_dir / case["case_id"] / f"attempt-{attempt}"
    private_dir.mkdir(parents=True)
    faults = faults_of(case)
    # The installed Codex plugin pins its endpoint, so its gateway listens on that fixed port.
    port = codex.PLUGIN_PORT if host == f"{codex.HOST}-plugin" else 0
    with serve(case["server_profile"]) as server, tempfile.TemporaryDirectory(prefix="tool-recovery-") as work:
        placeholders = seed(case["seed"], server)
        prompts = [substitute(template, placeholders) for template in case["turns"]]
        served_before_host = len(server.requests.snapshot())
        plugin = (
            PluginSession(
                server_url=server.base_url,
                scope_id=placeholders.get("scope_id"),
                authorization=server.host_headers.get("Authorization"),
                client_config=Path(work) / "clients.json",
            )
            if host in PLUGIN_HOSTS
            else None
        )
        with intercept(server.mcp_url, faults, actions(server, placeholders), port=port) as gateway:
            host_run = adapter.run(
                prompts,
                mcp_url=gateway.url,
                headers=server.host_headers,
                model=model,
                workdir=Path(work),
                private_dir=private_dir,
                plugin=plugin,
            )
            events: list[GatewayEvent] = gateway.log.snapshot()
        observation = {
            "prompts": prompts,
            "served_before_host": served_before_host,
            "served": [dataclasses.asdict(request) for request in server.requests.snapshot()],
            "mcp_calls": server.requests.snapshot_mcp_calls(),
            "gateway": [dataclasses.asdict(event) for event in events],
            "final_state": observe(case.get("observe"), server, placeholders),
            "read_only_operations": sorted(tool["name"] for tool in server.tool_catalog if tool["read_only"]),
        }
        catalog_sha256 = server.tool_catalog_sha256
    manifest = {
        "case_id": case["case_id"],
        "attempt": attempt,
        "host": host,
        "host_version": host_run.host_version,
        "model": host_run.model,
        "session_id": host_run.session_id,
        "exit_codes": list(host_run.exit_codes),
        "server": server_revision(),
        "server_profile": case["server_profile"],
        "fault": case.get("fault"),
        "tool_catalog_sha256": catalog_sha256,
        "case_manifest_sha256": manifest_sha256,
        "transcript": str(host_run.transcript),
        "transcript_sha256": file_sha256(host_run.transcript),
        "host_init": host_run.init,
        "plugin": adapter.plugin_identity() if host in PLUGIN_HOSTS else None,
    }
    write_json(private_dir / "manifest.json", manifest)
    # Private: prompts are content. Content-free Server and gateway records make the report reproducible offline.
    write_json(private_dir / "observation.json", observation)
    return score(case, private_dir)


def hook_requests(observation: dict[str, Any], *, failed: bool = False) -> dict[str, int]:
    """Count requests a plugin's hooks sent after the host started, or only those the Server rejected."""

    served = observation["served"][observation.get("served_before_host", 0) :]
    return dict(
        Counter(
            request["operation_id"]
            for request in served
            if not request["internal"]
            and request["operation_id"] in HOOK_OPERATIONS
            and (not failed or request["status"] >= 400)  # noqa: PLR2004 - HTTP error statuses
        )
    )


def _plugin_problems(adapter: Any, manifest: dict[str, Any], report: dict[str, Any], turns: list[Any]) -> list[str]:
    requests = report["hook_requests"]
    problems = adapter.plugin_problems(manifest["host_init"], turns, report["hook_failures"])
    # A hook may fail open after a denied request, such as Scope binding resolution for a read-only principal.
    if not requests:
        problems.append("The plugin's hooks did not reach the Server")
    return problems


def score(case: dict[str, Any], private_dir: Path) -> dict[str, Any]:
    """Compute the content-free report of one attempt from its private manifest, observation, and transcript."""

    manifest = json.loads((private_dir / "manifest.json").read_text(encoding="utf-8"))
    observation = json.loads((private_dir / "observation.json").read_text(encoding="utf-8"))
    adapter = HOSTS[manifest["host"]]
    transcript = private_dir / "transcript.jsonl"
    if file_sha256(transcript) != manifest["transcript_sha256"]:
        raise ValueError(f"Transcript changed after the run: {private_dir}")  # noqa: TRY003
    faults = faults_of(case)
    prompts = observation["prompts"]
    report: dict[str, Any] = {
        "scoring_version": SCORING_VERSION,
        "case_id": case["case_id"],
        "failure_class": case["failure_class"],
        "evidence": EVIDENCE[faults[0].mode] if faults else "real_server",
        "host": manifest["host"],
        "host_version": manifest["host_version"],
        "model": manifest["model"],
        "transcript_sha256": manifest["transcript_sha256"],
        "tool_catalog_sha256": manifest["tool_catalog_sha256"],
        "case_manifest_sha256": manifest["case_manifest_sha256"],
        "server_sha": manifest["server"]["sha"],
        "final_state": observation["final_state"],
    }
    plugin = manifest["host"] in PLUGIN_HOSTS
    if plugin:
        report.update(
            plugin=manifest["plugin"],
            hook_requests=hook_requests(observation),
            hook_failures=hook_requests(observation, failed=True),
        )
    try:
        turns = adapter.read_transcript(transcript, prompts)
        # A plugin session that did not run the plugin, or used tools beyond it, is not plugin evidence.
        problems = _plugin_problems(adapter, manifest, report, turns) if plugin else []
        if problems:
            raise TranscriptError(problems)
        bindings = bind_calls(
            turns,
            prefix=adapter.PREFIX,
            served=[ServedRequest(**request) for request in observation["served"]],
            gateway=[GatewayEvent(**event) for event in observation["gateway"]],
            mcp_calls=observation.get("mcp_calls"),
        )
    except TranscriptError as error:
        report.update(status="incomplete", problems=error.problems)
    else:
        report.update(
            status="complete",
            turns=[
                turn_rows(
                    turn,
                    prefix=adapter.PREFIX,
                    read_only=frozenset(observation["read_only_operations"]),
                    case_operation=case["operation_id"],
                    expected=case["expected"],
                    bindings=bindings,
                )
                for turn in turns
            ],
        )
        report["outcome"] = outcome(
            report["turns"],
            expected=case["expected"],
            final_state=observation["final_state"],
            state_violation=case.get("state_violation"),
        )
        found = leaks(report, content_of(turns, prompts))
        if found:
            # Never persist a leaking report; keep the private transcript for diagnosis and continue the run.
            report = {
                key: report[key] for key in ("scoring_version", "case_id", "failure_class", "host", "transcript_sha256")
            }
            report.update(status="leak_blocked", leaked_items=len(found))
    write_json(private_dir / "report.json", report)
    return report


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    case_ids = sorted(load_cases())
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run cases through a real host; this calls the host's model.")
    run.add_argument("--host", choices=sorted(HOSTS), required=True)
    run.add_argument("--case", action="append", choices=case_ids, dest="cases")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--model")
    run.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    preflight = commands.add_parser("preflight", help="Check each case's failure with a scripted MCP client; no model.")
    preflight.add_argument("--case", action="append", choices=case_ids, dest="cases")
    preflight.add_argument("--output", type=Path, required=True, help="JSON results; Server logs use stdout.")
    rescore = commands.add_parser("rescore", help="Recompute reports from private run records; no Server or model.")
    rescore.add_argument("--run-dir", type=Path, required=True)
    commands.add_parser(
        "prepare-codex-plugin", help="Write the codex-plugin marketplace copy; Codex configuration is unchanged."
    )
    summary = commands.add_parser("summarize", help="Aggregate private run directories; no Server, host, or model.")
    summary.add_argument("--run-dir", type=Path, action="append", dest="run_dirs", required=True)
    summary.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "prepare-codex-plugin":
        marketplace = codex.prepare_marketplace()
        print(f"Marketplace written: {marketplace}")
        print(f"Install it, then disable it so ordinary Codex sessions do not load {codex.PLUGIN_ID}:")
        print(f"  codex plugin marketplace add {marketplace}")
        print(f"  codex plugin add {codex.PLUGIN_ID}")
        print(f'  set enabled = false under [plugins."{codex.PLUGIN_ID}"] in ~/.codex/config.toml')
        return 0
    cases, manifest_sha256 = load_manifest()
    if arguments.command == "rescore":
        for private_dir in sorted(arguments.run_dir.glob("*/attempt-*")):
            report = score(cases[private_dir.parent.name], private_dir)
            print(json.dumps({"attempt": f"{private_dir.parent.name}/{private_dir.name}", "status": report["status"]}))
        return 0
    if arguments.command == "summarize":
        report, remediation = summarize(arguments.run_dirs, cases)
        arguments.output_dir.mkdir(parents=True, exist_ok=True)
        for name, value in (("summary.json", report), ("remediation.json", remediation)):
            (arguments.output_dir / name).write_text(
                json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        return 0
    if arguments.command == "preflight":
        results = []
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        for case_id in arguments.cases or sorted(cases):
            try:
                results.append(preflight_case(cases[case_id]))
            except Exception as error:  # noqa: BLE001 - report every case before failing
                results.append({"case_id": case_id, "reproduced": False, "error": f"{type(error).__name__}: {error}"})
            arguments.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        return 0 if all(result["reproduced"] for result in results) else 1
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    run_dir = arguments.runs_dir / run_id
    model = arguments.model or DEFAULT_MODELS[arguments.host]
    for case_id in arguments.cases or sorted(cases):
        for attempt in range(1, arguments.repeat + 1):
            report = run_case(
                cases[case_id],
                host=arguments.host,
                model=model,
                run_dir=run_dir,
                attempt=attempt,
                manifest_sha256=manifest_sha256,
            )
            print(json.dumps({"case_id": case_id, "attempt": attempt, "status": report["status"]}))
    print(f"Private run directory: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
