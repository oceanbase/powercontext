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

"""Live, opt-in subscription acceptance using existing Codex and gh credentials.

Run from the repository root after building gateway/target/debug/pc-gateway:
    uv run python tests/e2e/oauth_subscription_acceptance.py

This spends a small amount of subscription usage. It never starts a login flow.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from powercontext.client import PowerContextClient
from powercontext.http import ApproveCandidateRequest, CaptureContentSourceRequest, GenerateExperienceRequest

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "gateway/target/debug/pc-gateway"
REPORT = ROOT / ".powercontext/oauth-live/report.json"
CHANNELS = {
    "codex": ("gpt-6-luna", "openai-codex", "responses", "https://chatgpt.com/backend-api/codex/responses"),
    "copilot": (
        "claude-haiku-5.5",
        "github-copilot",
        "chat/completions",
        "https://api.githubcopilot.com/chat/completions",
    ),
}


class AcceptanceFailure(Exception):
    """A content-free failure code safe to include in the report."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AcceptanceFailure(reason)


def environment() -> dict[str, str]:
    excluded = ("POWERCONTEXT_", "PC_GATEWAY_", "OPENAI_", "GITHUB_COPILOT_", "COPILOT_", "OTEL_")
    return {key: value for key, value in os.environ.items() if not key.startswith(excluded)}


@contextmanager
def process(command: list[str], env: dict[str, str], directory: Path, name: str):
    with (directory / f"{name}.log").open("w") as output:
        child = subprocess.Popen(
            command, env=env, cwd=directory, stdin=subprocess.DEVNULL, stdout=output, stderr=output
        )
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            if name.startswith("gateway-"):
                output.flush()
                REPORT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(directory / f"{name}.log", REPORT.parent / f"{directory.name}-{name}.log")


async def wait_ready(child: subprocess.Popen[bytes], client: httpx.AsyncClient, path: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        require(child.poll() is None, "service-exited-before-ready")
        try:
            if (await client.get(path)).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.1)
    raise AcceptanceFailure("service-startup-timeout")


def core_environment(directory: Path, channel: str, token: str, gateway_token: str, port: int, via_gateway: bool):
    model, prefix, route, target = CHANNELS[channel]
    inference = {
        "generation_model": f"openai:{model}"
        if via_gateway and channel == "codex"
        else f"openai-chat:{model}"
        if via_gateway
        else f"{prefix}:{model}",
        "generation_max_requests": 1,
        "generation_timeout_seconds": 90,
        "generation_model_settings": {"openai_reasoning_effort": "low"} if channel == "codex" else {"max_tokens": 512},
        "protection": {
            "mode": "disabled",
            "targets": {
                "oauth-live": {
                    "endpoint": target,
                    "model": model,
                    "protocol": "openai-responses" if route == "responses" else "openai-chat",
                    "allow_streaming": True,
                }
            },
        },
    }
    if via_gateway:
        inference.update({
            "generation_base_url": f"http://127.0.0.1:{port}/gateway/v1",
            "generation_headers": {
                "Authorization": f"Bearer {gateway_token}",
                "X-PowerContext-Conversation-ID": "oauth-internal-generation",
            },
            "generation_streaming": "auto" if channel == "codex" else "disabled",
        })
    env = environment()
    env.update({
        "POWERCONTEXT_HOME": str(directory / "home"),
        "POWERCONTEXT_SERVER_DATABASE": json.dumps({
            "kind": "sqlite",
            "url": f"sqlite+aiosqlite:///{directory / 'runtime.db'}",
        }),
        "POWERCONTEXT_SERVER_AUTH_TOKEN": token,
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_HTTP_UDS": str(directory / "core.sock"),
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        "POWERCONTEXT_SERVER_LOGGING_ACCESS": "false",
        "POWERCONTEXT_SERVER_RUNTIME": json.dumps({"artifact_processing_families": []}),
        "POWERCONTEXT_SERVER_INFERENCE": json.dumps(inference),
        "POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET": secrets.token_hex(32),
        "PYDANTIC_AI_NO_BANNER": "1",
    })
    return env


async def generate(client: PowerContextClient, scope: str, source, key: str) -> None:
    generated = await client.generate_experience(
        GenerateExperienceRequest(scope_id=scope, source_refs=[source], artifact_refs=[])
    )
    if generated.candidate is None:
        raise AcceptanceFailure("generation-returned-no-candidate")
    candidate = generated.candidate
    require(candidate.family == "experience" and bool(candidate.proposal), "invalid-experience-candidate")
    approved = await client.approve_candidate(
        ApproveCandidateRequest(scope_id=scope, candidate_id=candidate.candidate_id, expected_version=1)
    )
    if approved.result_artifact is None:
        raise AcceptanceFailure("approval-returned-no-artifact")
    artifact = await client.get_artifact(scope, approved.result_artifact.family, approved.result_artifact.artifact_id)
    require(artifact is not None, f"{key}-artifact-unreadable")


async def exercise(directory: Path, channel: str, credentials: Path, github_token: str, result: dict[str, Any]) -> None:
    model, _, route, _ = CHANNELS[channel]
    core_token, gateway_token = secrets.token_hex(24), secrets.token_hex(24)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {core_token}"}
    transport = httpx.AsyncHTTPTransport(uds=str(directory / "core.sock"), retries=0)
    command = [sys.executable, "-c", "from powercontext.cli.app import main; main()", "server", "run", "--no-env-file"]
    scope = None
    source = None
    for via_gateway in [False, True]:
        phase = "powercontext-via-gateway" if via_gateway else "native-and-combined"
        result["phase"] = phase
        env = core_environment(directory, channel, core_token, gateway_token, port, via_gateway)
        env["GITHUB_COPILOT_API_KEY"] = github_token
        with process(command, env, directory, f"core-{phase}") as core:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost", headers=headers, trust_env=False, timeout=5
            ) as bootstrap:
                # Capabilities are local; health/ready can make a billed model probe.
                await wait_ready(core, bootstrap, "/v1/capabilities")
                if scope is None:
                    created = await bootstrap.post(
                        "/v1/scopes",
                        json={
                            "title": "OAuth live acceptance",
                            "summary": "Disposable acceptance data",
                            "idempotency_key": "oauth-live-scope",
                        },
                    )
                    require(created.status_code == 201, "scope-creation-failed")
                    scope = created.json()["scope_id"]
                    granted = await bootstrap.post(
                        "/v1/access/bindings/create",
                        json={
                            "subject": {"type": "service", "id": "server-token"},
                            "resource": {"type": "scope", "scope_id": scope},
                            "role": "scope.contributor",
                            "idempotency_key": "oauth-live-grant",
                        },
                    )
                    require(granted.status_code == 201, "scope-grant-failed")
                gateway_env = environment() | {
                    "PC_GATEWAY_CORE_UDS": str(directory / "core.sock"),
                    "PC_GATEWAY_CORE_TOKEN": core_token,
                    "PC_GATEWAY_LISTEN": f"127.0.0.1:{port}",
                    "PC_GATEWAY_API_KEY": gateway_token,
                    "PC_GATEWAY_DEFAULT_SCOPE": scope,
                    "PC_GATEWAY_UPSTREAM_PROFILE": "oauth-live",
                    "PC_GATEWAY_SUBSCRIPTION": channel,
                    "PC_GATEWAY_SUBSCRIPTION_CREDENTIALS": str(credentials),
                    "PC_GATEWAY_MAX_ATTEMPTS": "1",
                    "PC_GATEWAY_INJECTION": "off" if via_gateway else "on",
                    "PC_GATEWAY_SEDIMENT": "off" if via_gateway else "on",
                    "RUST_LOG": "warn",
                }
                with process([str(BINARY)], gateway_env, directory, f"gateway-{phase}") as gateway:
                    async with httpx.AsyncClient(base_url=base, timeout=100, trust_env=False) as http:
                        await wait_ready(gateway, http, "/healthz")
                        require(
                            (await http.get("/v1/capabilities")).status_code == 401, "core-proxy-lost-authentication"
                        )
                        require(
                            (
                                await http.get("/v1/capabilities", headers={"Authorization": f"Bearer {gateway_token}"})
                            ).status_code
                            == 401,
                            "gateway-key-became-core-key",
                        )
                        require(
                            (await http.get("/v1/capabilities", headers=headers)).status_code == 200,
                            "core-proxy-unavailable",
                        )
                        async with httpx.AsyncClient(
                            base_url=base, headers=headers, trust_env=False, timeout=100
                        ) as wire:
                            client = PowerContextClient(
                                base, timeout=100, http_client=wire, trust_transport_security=True
                            )
                            if source is None:
                                captured = await client.capture_content_source(
                                    CaptureContentSourceRequest(
                                        scope_id=scope,
                                        source_id="oauth-live-task",
                                        content="A schema change broke a deployed API client. We regenerated the client from the authoritative schema, ran contract checks, and restored compatibility. Regenerate clients and validate the contract before releasing schema changes.",
                                    )
                                )
                                source = captured.source
                            before = await client.list_sources(scope)
                            await generate(client, scope, source, phase)
                            result["powercontext-via-gateway" if via_gateway else "powercontext-native"] = "passed"
                            if not via_gateway:
                                before = await client.list_sources(scope)
                                marker = "PC_OAUTH_" + secrets.token_hex(6)
                                remembered = await http.post(
                                    "/v1/memory/remember",
                                    headers=headers,
                                    json={
                                        "scope_id": scope,
                                        "kind": "fact",
                                        "text": f"The subscription acceptance marker is {marker}.",
                                    },
                                )
                                require(remembered.status_code == 200, "memory-remember-failed")
                                prompt = "What is the subscription acceptance marker? Return only its exact value from the recalled context."
                                body = {"model": model, "stream": False}
                                if route == "responses":
                                    body["input"] = [{"role": "user", "content": prompt}]
                                else:
                                    body.update({"messages": [{"role": "user", "content": prompt}], "max_tokens": 256})
                                response = await http.post(
                                    f"/gateway/v1/{route}",
                                    headers={
                                        "Authorization": f"Bearer {gateway_token}",
                                        "X-PowerContext-Conversation-ID": secrets.token_hex(12),
                                    },
                                    json=body,
                                )
                                result["gateway_http_status"] = response.status_code
                                if response.status_code != 200:
                                    diagnostic = response.json()
                                    result["gateway_decision"] = diagnostic.get("decision")
                                    code = diagnostic.get("error")
                                    if isinstance(code, str) and code.replace("_", "").isalnum():
                                        result["gateway_error"] = code
                                require(response.status_code == 200, "gateway-model-request-failed")
                                value = response.json()
                                usage = value.get("usage") or {}
                                result["gateway_usage"] = {
                                    key: usage[key]
                                    for key in [
                                        "input_tokens",
                                        "output_tokens",
                                        "prompt_tokens",
                                        "completion_tokens",
                                        "total_tokens",
                                    ]
                                    if key in usage
                                }
                                text = (
                                    value["choices"][0]["message"]["content"]
                                    if route != "responses"
                                    else "".join(
                                        part.get("text", "")
                                        for item in value.get("output", [])
                                        for part in item.get("content", [])
                                    )
                                )
                                require(marker in text, "gateway-recall-marker-missing")
                                after = await client.list_sources(scope)
                                captured_turns = [item for item in after.items if item.source_id.startswith("gw-turn-")]
                                require(
                                    len(captured_turns) == 1,
                                    "gateway-turn-not-captured-once",
                                )
                                persisted = await client.get_source(
                                    scope, captured_turns[0].source_type, captured_turns[0].source_id
                                )
                                require(
                                    prompt in json.dumps(persisted.content) and marker in json.dumps(persisted.content),
                                    "captured-conversation-unreadable",
                                )
                                result["gateway-recall-and-capture"] = "passed"
                            else:
                                after = await client.list_sources(scope)
                                require(
                                    {item.source_id for item in after.items if item.source_id.startswith("gw-turn-")}
                                    == {
                                        item.source_id for item in before.items if item.source_id.startswith("gw-turn-")
                                    },
                                    "internal-generation-captured-recursively",
                                )
                                result["internal-generation-no-recursive-capture"] = "passed"
                                require(
                                    any(item.source_id.startswith("gw-turn-") for item in after.items),
                                    "captured-turn-lost-after-restart",
                                )
        # Each bootstrap client owns and closes its transport.
        transport = httpx.AsyncHTTPTransport(uds=str(directory / "core.sock"), retries=0)
    result["phase"] = "completed"


async def main(report: dict[str, Any], channels: Sequence[str]) -> None:
    require(BINARY.is_file(), "build-gateway-first")
    codex = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
    original_codex = hashlib.sha256(codex.read_bytes()).digest() if "codex" in channels else None
    gh = shutil.which("gh")
    if gh is None:
        raise AcceptanceFailure("github-cli-unavailable")
    cli = await asyncio.create_subprocess_exec(
        gh,
        "auth",
        "token",
        "--hostname",
        "github.com",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await cli.communicate()
    require(cli.returncode == 0, "existing-github-login-unavailable")
    github = stdout.decode().strip()
    require(bool(github), "existing-github-token-unavailable")
    with tempfile.TemporaryDirectory(prefix="pc-oauth-live-") as temporary:
        root = Path(temporary)
        copilot = root / "copilot.json"
        copilot.touch(mode=0o600, exist_ok=False)
        copilot.write_text(json.dumps({"oauth_token": github}))
        original_copilot = copilot.read_bytes()
        for channel in channels:
            directory = root / channel
            directory.mkdir(mode=0o700)
            result = report["channels"][channel] = {"model": CHANNELS[channel][0]}
            await exercise(directory, channel, codex if channel == "codex" else copilot, github, result)
            print(json.dumps({"channel": channel, "result": result}), flush=True)
        require(copilot.read_bytes() == original_copilot, "copilot-credentials-modified")
    report["codex_credentials_unchanged_during_test"] = (
        original_codex is None or hashlib.sha256(codex.read_bytes()).digest() == original_codex
    )
    report["copilot_credentials_unchanged"] = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=tuple(CHANNELS))
    arguments = parser.parse_args()
    report = {"channels": {}, "login_performed": False, "protection_mode": "disabled"}
    try:
        asyncio.run(main(report, [arguments.channel] if arguments.channel else list(CHANNELS)))
        report["status"] = "passed"
    except Exception as error:
        report.update({"status": "failed", "error_type": type(error).__name__})
        if isinstance(error, AcceptanceFailure):
            report["error_code"] = str(error)
        print(json.dumps(report), flush=True)
        sys.exit(1)
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
