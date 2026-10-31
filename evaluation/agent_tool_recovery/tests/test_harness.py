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

"""Case manifest and gateway parsing that decide which call a fault targets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import getproxies_environment, proxy_bypass

import pytest
import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from typing_extensions import override

from .. import claude_code, codex
from ..claude_code import child_environment
from ..environment import loopback_proxy_environment
from ..proxy import tool_calls
from ..run import load_cases, preflight_case, score, substitute
from ..server import seed, serve
from ..summary import summarize
from ..transcript import ToolCall, ToolResult, Turn


def test_gateway_targets_only_tools_call_messages() -> None:
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "create_scope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]

    assert tool_calls(json.dumps(batch).encode()) == ["create_scope"]
    assert tool_calls(b"not json") == []


def test_exact_placeholder_keeps_the_seeded_type() -> None:
    value = {"revision": "{revision}", "text": "Scope {scope_id}", "items": ["{scope_id}"]}

    assert substitute(value, {"revision": 3, "scope_id": "s1"}) == {"revision": 3, "text": "Scope s1", "items": ["s1"]}


def test_every_case_declares_a_reproducible_target_failure() -> None:
    for case in load_cases().values():
        expected = case["expected"]
        assert case["preflight_arguments"], case["case_id"]
        assert expected["retry_disposition"] in {"retry_later", "retry_after_change", "terminal", "unknown"}
        if expected["retry_disposition"] == "retry_later":
            assert isinstance(expected["max_identical_retries"], int)
            assert expected["max_identical_retries"] >= 0
        transport = case.get("fault", {}).get("mode") in {"reset_before_forward", "drop_after_forward"}
        # Only a transport fault or the MCP layer's own rejection lacks a Server status and code.
        assert transport == (expected["http_status"] is None) or case["case_id"] == "mcp-family-rejected"


def test_host_environment_drops_inherited_sessions_and_keeps_loopback_off_proxy() -> None:
    env = child_environment(
        {"PATH": "p", "CLAUDE_CODE_SESSION_ID": "x", "POWERCONTEXT_CLIENT_SERVER_URL": "u", "NO_PROXY": "corp"}
    )

    assert set(env) == {"PATH", "NO_PROXY", "no_proxy", "ENABLE_TOOL_SEARCH"}
    assert env["NO_PROXY"].split(",") == ["127.0.0.1", "corp", "localhost"]


@pytest.mark.parametrize(
    ("exclusions", "effective"),
    [
        ({"NO_PROXY": "corp"}, "corp"),
        ({"no_proxy": "corp"}, "corp"),
        ({"NO_PROXY": "ignored", "no_proxy": "corp"}, "corp"),
        ({"NO_PROXY": "*", "no_proxy": ""}, ""),
        ({"no_proxy": "*"}, "*"),
    ],
)
def test_host_proxy_exclusions_work_with_case_sensitive_environment_names(
    exclusions: dict[str, str], effective: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = child_environment({"HTTP_PROXY": "http://proxy.invalid:8080", **exclusions})
    # A plain mapping preserves POSIX's distinct upper/lowercase names even when this test runs on Windows.
    monkeypatch.setattr(os, "environ", env)
    proxies = getproxies_environment()

    assert proxy_bypass("127.0.0.1")
    assert proxy_bypass("localhost")
    assert proxy_bypass("external-model.invalid") == (effective == "*")
    if effective == "corp":
        assert proxy_bypass("corp")
    assert proxies["http"] == "http://proxy.invalid:8080"


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("exclusions", [{}, {"no_proxy": "corp"}, {"NO_PROXY": "ignored", "no_proxy": "corp"}])
def test_dream_proxy_environment_restores_settings_after_normal_and_failed_runs(
    fails: bool, exclusions: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = {"HTTP_PROXY": "http://proxy.invalid:8080", **exclusions}
    monkeypatch.setattr(os, "environ", dict(original))

    def run() -> None:
        with loopback_proxy_environment():
            settings = dict(os.environ)
            with loopback_proxy_environment():
                assert proxy_bypass("127.0.0.1")
            assert dict(os.environ) == settings
            assert os.environ["HTTP_PROXY"] == original["HTTP_PROXY"]
            if fails:
                raise ValueError

    if fails:
        with pytest.raises(ValueError):
            run()
    else:
        run()
    assert dict(os.environ) == original


@pytest.fixture
def recording_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
            requests.append(self.path)
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_GET

        @override
        def log_message(self, format: str, *args: Any) -> None:
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{proxy.server_port}"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        yield url, requests
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "case_id", ["generation-unavailable", "stale-etag-replace", "idempotent-replay", "dream-capacity"]
)
def test_preflight_reaches_its_local_server_without_using_environment_proxies(
    case_id: str, recording_proxy: tuple[str, list[str]]
) -> None:
    _, requests = recording_proxy

    result = preflight_case(load_cases()[case_id])

    assert result["reproduced"] is True
    assert requests == []


def test_external_host_traffic_keeps_the_environment_proxy(recording_proxy: tuple[str, list[str]]) -> None:
    url, requests = recording_proxy
    env = child_environment(dict(os.environ))

    assert env["HTTP_PROXY"] == url
    assert env["HTTPS_PROXY"] == url
    with httpx.Client(timeout=5) as client:
        assert client.get("http://external-model.invalid/v1").status_code == 502
    assert requests == ["http://external-model.invalid/v1"]


def test_codex_cli_runs_with_the_codex_home_its_plugin_is_checked_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stand-in CLI prints the home it received and an inherited Codex session variable.
    if os.name == "nt":
        cli = tmp_path / "codex.cmd"
        cli.write_text("@echo %CODEX_HOME%^|%CODEX_THREAD_ID%\r\n", encoding="utf-8")
    else:
        cli = tmp_path / "codex"
        cli.write_text('#!/bin/sh\necho "$CODEX_HOME|$CODEX_THREAD_ID"\n', encoding="utf-8")
        cli.chmod(0o755)
    home = tmp_path / "codex-home"
    monkeypatch.setenv(codex.EXECUTABLE_VARIABLE, str(cli))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_THREAD_ID", "inherited")

    received_home, inherited = codex.version().split("|")

    assert Path(received_home) == home
    assert codex.installed_plugin_root().is_relative_to(home)
    assert inherited == ""


PROMPT = "Generate an Experience."
START = datetime(2026, 10, 19, tzinfo=UTC)
GENERATION_FAILED = "HTTP error 503: Service Unavailable - {'error': {'code': 'generation_unavailable'}}"


def write_attempt(
    directory: Path,
    *,
    host: str = "claude-code",
    served: list[dict[str, Any]],
    init: dict[str, Any] | None = None,
    mcp_calls: list[str] | None = None,
) -> Path:
    events = [
        {"type": "user", "timestamp": "2026-10-19T00:00:00Z", "message": {"content": PROMPT}},
        {
            "type": "assistant",
            "timestamp": "2026-10-19T00:00:01Z",
            "message": {
                "id": "m1",
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "mcp__powercontext__generate_experience",
                        "input": {"scope_id": "s"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-19T00:00:02Z",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": GENERATION_FAILED}
                ]
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-19T00:00:03Z",
            "message": {
                "id": "m2",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Generation is unavailable."}],
            },
        },
    ]
    transcript = directory / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    manifest = {
        "host": host,
        "host_version": "test",
        "model": "test",
        "tool_catalog_sha256": "c",
        "case_manifest_sha256": "m",
        "server": {"sha": "s"},
        "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
        "host_init": init or {},
        "plugin": {"name": "powercontext"} if host.endswith("-plugin") else None,
    }
    observation = {
        "prompts": [PROMPT],
        "served_before_host": 1,
        "served": served,
        "gateway": [],
        "final_state": {},
        "read_only_operations": [],
    }
    if mcp_calls is not None:
        observation["mcp_calls"] = mcp_calls
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "observation.json").write_text(json.dumps(observation), encoding="utf-8")
    return transcript


SEED = {"operation_id": "create_scope", "status": 201, "internal": False}
TARGET = {"operation_id": "generate_experience", "status": 503, "internal": True}
HOOK = {"operation_id": "prepare_context", "status": 200, "internal": False}


@pytest.mark.parametrize("case_id", ["transport-reset-read", "idempotent-replay", "dream-capacity"])
@pytest.mark.parametrize("extra_resend", [False, True])
def test_scoring_enforces_each_cases_retry_budget(tmp_path: Path, case_id: str, extra_resend: bool) -> None:
    case = load_cases()[case_id]
    budget = 2 if case_id == "dream-capacity" else 1
    resends = budget + int(extra_resend)
    operation = case["operation_id"]
    attempt = tmp_path / case_id / "attempt-1"
    attempt.mkdir(parents=True)
    transcript = write_attempt(attempt, served=[SEED])
    template = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    arguments = substitute(
        case["preflight_arguments"],
        {"scope_id": "synthetic-scope", "artifact_id": "synthetic-artifact", "revision": 1},
    )

    def issued(index: int, second: int) -> dict[str, Any]:
        return {
            **template[1],
            "timestamp": f"2026-10-19T00:00:{second:02d}Z",
            "message": {
                "id": f"m{index}",
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"t{index}",
                        "name": f"mcp__powercontext__{operation}",
                        "input": arguments,
                    }
                ],
            },
        }

    def answered(index: int, second: int, *, failed: bool = False) -> dict[str, Any]:
        text = "Connection closed"
        if failed and case_id == "dream-capacity":
            text = "HTTP error 429: Too Many Requests - {'error': {'code': 'capacity_exceeded'}}"
        return {
            **template[2],
            "timestamp": f"2026-10-19T00:00:{second:02d}Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"t{index}",
                        "is_error": failed,
                        "content": text if failed else "{}",
                    }
                ]
            },
        }

    # Every resend is issued after the failure and before any successful result can retire it.
    events = [template[0], issued(1, 1), answered(1, 2, failed=True)]
    events.extend(issued(index + 2, index + 3) for index in range(resends))
    events.extend(answered(index + 2, index + 10) for index in range(resends))
    events.append(template[-1])
    events[-1]["timestamp"] = "2026-10-19T00:00:20Z"
    transcript.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    manifest_path = attempt / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["transcript_sha256"] = hashlib.sha256(transcript.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fault = case.get("fault")
    forwarded = case_id != "transport-reset-read"
    statuses = ([429 if not fault else 201] if forwarded else []) + [200] * resends
    observation = {
        "prompts": [PROMPT],
        "served_before_host": 1,
        "served": [SEED]
        + [
            {"operation_id": operation, "status": status, "internal": True, "mcp_call": index}
            for index, status in enumerate(statuses)
        ],
        "mcp_calls": [operation] * len(statuses),
        "gateway": (
            [{"operation_id": operation, "fault": fault["mode"], "forwarded": forwarded}]
            + [{"operation_id": operation, "fault": None, "forwarded": True}] * resends
        )
        if fault
        else [],
        "final_state": {},
        "read_only_operations": ["search_memory"],
    }
    (attempt / "observation.json").write_text(json.dumps(observation), encoding="utf-8")

    result = score(case, attempt)

    assert result["status"] == "complete"
    assert result["turns"][0]["metrics"]["target_retries"] == resends
    assert result["turns"][0]["metrics"]["repeated_identical_after_failure"] == resends
    assert result["outcome"]["structural"] == ("violation" if extra_resend else "recovered")
    assert result["outcome"]["violations"] == (["identical_retry"] if extra_resend else [])
    _, remediation = summarize([tmp_path], {case_id: case})
    assert len(remediation) == int(extra_resend)
    if extra_resend:
        assert remediation[0]["problems"] == {"claude-code": ["identical_retry"]}
        assert remediation[0]["evidence"]["repeated_calls"] == resends


def test_scoring_private_records_is_reproducible_and_bound_to_the_transcript(tmp_path: Path) -> None:
    transcript = write_attempt(tmp_path, served=[SEED, TARGET])
    case = load_cases()["generation-unavailable"]

    first = score(case, tmp_path)
    assert first["status"] == "complete"
    assert score(case, tmp_path) == first
    assert first["turns"][0]["calls"][0]["server_status"] == 503

    transcript.write_text(transcript.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Transcript changed"):
        score(case, tmp_path)


def test_call_that_cannot_be_bound_to_server_requests_makes_the_session_incomplete(tmp_path: Path) -> None:
    case = load_cases()["generation-unavailable"]
    # Recorded before calls were correlated: no request shows which call, if any, reached the Server.
    write_attempt(tmp_path, served=[SEED])

    assert score(case, tmp_path)["status"] == "incomplete"

    write_attempt(tmp_path, served=[SEED, {**TARGET, "mcp_call": 0}], mcp_calls=["generate_experience"])
    report = score(case, tmp_path)
    assert (report["status"], report["turns"][0]["calls"][0]["server_status"]) == ("complete", 503)


def test_a_new_leak_blocked_report_can_still_be_summarized(tmp_path: Path) -> None:
    case = load_cases()["generation-unavailable"]
    attempt = tmp_path / case["case_id"] / "attempt-1"
    attempt.mkdir(parents=True)
    write_attempt(attempt, served=[SEED, TARGET])
    path = attempt / "observation.json"
    observation = json.loads(path.read_text(encoding="utf-8"))
    observation["final_state"] = {"unexpected_content": PROMPT}
    path.write_text(json.dumps(observation), encoding="utf-8")

    assert score(case, attempt)["status"] == "leak_blocked"
    summary, remediation = summarize([tmp_path], {case["case_id"]: case})

    assert summary["cases"][0]["leak_blocked_runs"] == 1
    assert remediation == []


def test_plugin_session_counts_only_when_its_hooks_reached_the_server(tmp_path: Path) -> None:
    case = load_cases()["generation-unavailable"]
    init = {"plugins": [{"name": "powercontext", "path": str(claude_code.PLUGIN_DIR)}]}
    # A seed request before the host started is not hook evidence.
    write_attempt(tmp_path, host="claude-code-plugin", served=[HOOK, TARGET], init=init)

    assert score(case, tmp_path)["problems"] == ["The plugin's hooks did not reach the Server"]

    write_attempt(tmp_path, host="claude-code-plugin", served=[SEED, HOOK, TARGET], init=init)
    report = score(case, tmp_path)
    assert (report["status"], report["hook_requests"]) == ("complete", {"prepare_context": 1})


def test_claude_plugin_session_exposes_only_the_repository_plugin_skill() -> None:
    init = {
        "plugins": [{"name": "powercontext", "path": str(claude_code.PLUGIN_DIR)}],
        "skills": [claude_code.PLUGIN_SKILL, "debug"],
    }
    other_skill = ToolCall("t1", "Skill", {"skill": "debug"}, START, ToolResult(True, "blocked", START))

    assert claude_code.plugin_problems(init, [], {}) == []
    assert claude_code.plugin_problems({**init, "plugins": []}, [], {}) == [
        "The repository PowerContext plugin was not loaded"
    ]
    assert claude_code.plugin_problems({**init, "skills": ["personal"]}, [], {}) == [
        "Skills outside the plugin were exposed: ['personal']"
    ]
    assert claude_code.plugin_problems(init, [Turn(1, START, calls=[other_skill])], {}) == [
        "Tool call t1 invoked a Skill outside the plugin"
    ]


def test_codex_plugin_shell_use_is_limited_to_the_plugin_files() -> None:
    def shell(command: str) -> Turn:
        return Turn(
            1, START, calls=[ToolCall("c1", "shell", {"command": command}, START, ToolResult(False, "", START))]
        )

    skill = f"Get-Content -LiteralPath '{codex.installed_plugin_root().as_posix()}/skills/x/SKILL.md'"
    assert codex.plugin_problems({}, [shell(skill)], {}) == []
    assert codex.plugin_problems({}, [shell("Get-Content C:/Users/someone/notes.md")], {}) == [
        "Shell command c1 reached outside the plugin"
    ]


def test_codex_plugin_marketplace_copies_the_repository_plugin_with_a_pinned_endpoint(tmp_path: Path) -> None:
    marketplace = codex.prepare_marketplace(tmp_path / "marketplace")
    plugin = marketplace / "plugins" / "powercontext"
    exclude = frozenset({".mcp.json"})

    assert claude_code.tree_sha256(plugin, exclude=exclude) == claude_code.tree_sha256(
        codex.REPOSITORY_PLUGIN, exclude=exclude
    )
    endpoint = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    assert endpoint["mcpServers"]["powercontext"]["url"] == f"http://127.0.0.1:{codex.PLUGIN_PORT}/mcp/"
    with pytest.raises(FileExistsError):
        codex.prepare_marketplace(marketplace)


def test_codex_plugin_session_with_a_denied_binding_is_not_bound_call_by_call() -> None:
    # The PreToolUse hook denies the model's call without any exec --json event.
    assert codex.plugin_problems({}, [], {"resolve_scope_binding": 1}) == [
        "The PreToolUse hook could not resolve the Scope binding; Codex records no denied call"
    ]


def test_real_server_attributes_bridge_requests_to_the_mcp_call_that_sent_them() -> None:
    async def replace(url: str, arguments: list[dict[str, Any]]) -> list[bool]:
        async with Client(StreamableHttpTransport(url)) as client:
            return [
                (await client.call_tool("replace_artifact", item, raise_on_error=False)).is_error for item in arguments
            ]

    with serve("default") as server:
        placeholders = seed("atomic_memory", server)
        before = len(server.requests.snapshot())
        content = {"kind": "decision", "text": "We pin the uv version in CI."}
        arguments = {**placeholders, "If-Match": '"revision:1"', "content": content}
        # The MCP layer rejects the first call itself; only the second reaches the Server.
        errors = asyncio.run(
            replace(server.mcp_url, [{**arguments, "family": "experience"}, {**arguments, "family": "atomic-memory"}])
        )
        calls = server.requests.snapshot_mcp_calls()
        bridged = [request for request in server.requests.snapshot()[before:] if request.internal]

    assert errors == [True, False]
    assert calls == ["replace_artifact", "replace_artifact"]
    assert [(request.mcp_call, request.status) for request in bridged] == [(1, 200)]
