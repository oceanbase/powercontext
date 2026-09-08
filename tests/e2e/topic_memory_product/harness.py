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

"""Bounded R8 Topic Memory product-chain acceptance harness."""

# ruff: noqa: TRY003

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import logging
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Request
from pydantic import AnyHttpUrl, SecretStr
from sqlalchemy.engine import make_url
from starlette.middleware import Middleware

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryEvolveOutput,
    TopicMemoryGlobalOutput,
    TopicMemoryPlannerOutput,
    TopicMemoryProbeOutput,
    TopicMemoryReconcileOutput,
    TopicMemoryTemporaryOutput,
)
from powercontext.builtin.inference import EmbeddingResult, InferenceUnavailableError
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime import HandoffReportConfig, InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http import CaptureContentSourceRequest, FlushTopicMemoryRequest, SearchTopicMemoryRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    ServerSettings,
)
from tests.e2e.topic_memory_product.common import (
    AccessTimeline,
    AccessTimelineMiddleware,
    ArtifactIdentity,
    FakeInference,
    PreparedContextAudit,
    PreparedContextAuditMiddleware,
    ProductChainError,
    default_scope_id,
    digest_text,
    exercise_http_mcp_prepared_web_chain,
    require_no_worker_failures,
    run_e0,
    start_loopback_server,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SELECTOR = "powercontext@powercontext"
E1_SCOPE_ID = "project:r8-real-codex"
E1_CANARY = "TOPAZ-R8-REAL-CODEX-CANARY"
E2_CANARY = "BERYL-R8-REAL-EMBEDDING-CANARY"
E3_CANARY = "ONYX-R8-OCEANBASE-RECOVERY-CANARY"
E4_CANARY = "JADE-R8-SEEKDB-CANARY"
_E2_TOKEN = "r8-e2-one-time-token"  # noqa: S105 - synthetic loopback-only credential.
_E4_TOKEN = "r8-e4-one-time-token"  # noqa: S105 - synthetic loopback-only credential.
DEFAULT_CODEX_MODEL = "gpt-5.6-luna"
DEFAULT_GENERATION_MODEL = "gpt-5.6-luna"
_E1_SUBPROCESS_ENV_ALLOWLIST = frozenset({
    "ALL_PROXY",
    "CURL_CA_BUNDLE",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LD_LIBRARY_PATH",
    "LOGNAME",
    "NO_COLOR",
    "NO_PROXY",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "TZ",
    "USER",
    "UV_CACHE_DIR",
    "UV_NATIVE_TLS",
    "UV_NO_PROGRESS",
    "UV_PYTHON",
    "UV_PYTHON_INSTALL_DIR",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
})


@dataclass(frozen=True, slots=True)
class _RealEmbeddingConfig:
    model: str
    profile_id: str
    dimension: int
    base_url: AnyHttpUrl | None
    headers: dict[str, SecretStr]
    normalization: Literal["none", "unit"]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class _OceanBaseLayerConfig:
    database: OceanBaseConfig
    schema_fingerprint: str


@dataclass(frozen=True, slots=True)
class _CodexMcpCall:
    sequence: int
    server: str
    tool: str
    arguments: Mapping[str, object]
    result: object
    status: str


class _FallbackEmbedding:
    """Deterministically inject one unavailable query embedding for E2."""

    def __init__(self, config: _RealEmbeddingConfig) -> None:
        self.profile = EmbeddingProfile(
            profile_id=config.profile_id,
            model=config.model,
            dimension=config.dimension,
            normalization=config.normalization,
        )

    async def embed(self, _texts: tuple[str, ...], /) -> EmbeddingResult:
        raise InferenceUnavailableError("embed")


class _EmbeddingFallbackCapture(logging.Handler):
    """Retain only the stable fallback classification, never query/provider data."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.events: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) != "topic_memory.search.embedding_fallback":
            return
        self.events.append({
            "event": "topic_memory.search.embedding_fallback",
            "mode": str(getattr(record, "mode", "unknown")),
            "error_code": str(getattr(record, "error_code", "unknown")),
        })


class _WorkerFailureCapture(logging.Handler):
    """Capture only the worker's content-free failure classification."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.failures: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) != "artifact_processing.worker.failed":
            return
        self.failures.append({
            "stage": str(getattr(record, "stage", "unknown")),
            "error_code": str(getattr(record, "error_code", "unknown")),
            "exception_type": str(getattr(record, "exception_type", "unknown")),
            "failure_count": int(getattr(record, "failure_count", 0)),
        })


@dataclass(slots=True)
class _RealCodexChatBridge:
    """Expose bounded real Codex generations through an OpenAI-compatible loopback API."""

    codex_home: Path
    fixture: Path
    environment: Mapping[str, str]
    model: str
    timeout: float
    max_calls: int = 4
    calls: list[dict[str, object]] = field(default_factory=list)
    _external_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> dict[str, object]:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=422, detail="invalid generation request")
            if "response_format" not in payload and "tools" not in payload:
                with self._lock:
                    self.calls.append({"stage": "readiness", "external_generation": False})
                return _chat_completion("READY", model=self.model, call_id="readiness")
            with self._lock:
                call_number = self._external_calls + 1
                if call_number > self.max_calls:
                    raise HTTPException(status_code=429, detail="R8 generation call budget exhausted")
                self._external_calls = call_number
            started = time.monotonic()
            try:
                content = await asyncio.to_thread(self._generate, payload, call_number)
            except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
                with self._lock:
                    self.calls.append({
                        "call": call_number,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "outcome": "failure",
                        "exception_type": type(exc).__name__,
                        "request_fields": sorted(payload),
                        "response_format_present": "response_format" in payload,
                        "response_format_shape": _mapping_shape(payload.get("response_format")),
                        "tool_count": len(payload.get("tools", [])) if isinstance(payload.get("tools"), list) else 0,
                    })
                raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
            duration = round(time.monotonic() - started, 3)
            with self._lock:
                self.calls.append({
                    "call": call_number,
                    "duration_seconds": duration,
                    "structured_output": True,
                    "output_bytes": len(content.encode()),
                })
            return _chat_completion(content, model=self.model, call_id=str(call_number))

        return app

    def _generate(self, payload: Mapping[str, object], call_number: int) -> str:
        schema = _generation_schema(payload)
        output_path = self.codex_home / f"generation-output-{call_number}.json"
        prompt = (
            "Act as a bounded structured-generation backend for a synthetic PowerContext acceptance run. "
            "Follow the supplied messages exactly, use no tools, and return only the JSON value required by "
            "the output schema, with no Markdown fence or explanation. The output schema is:\n"
            + json.dumps(schema, ensure_ascii=False)
            + "\nThe request is:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            _run(
                (
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--json",
                    "--color",
                    "never",
                    "--sandbox",
                    "read-only",
                    "--model",
                    self.model,
                    "--config",
                    'model_reasoning_effort="low"',
                    "--output-last-message",
                    str(output_path),
                    "--cd",
                    str(self.fixture),
                    "-",
                ),
                cwd=self.fixture,
                env={**self.environment, "CODEX_HOME": str(self.codex_home)},
                timeout=self.timeout,
                input_data=prompt,
            )
            output = output_path.read_text(encoding="utf-8").strip()
            decoded = json.loads(output)
            if not isinstance(decoded, dict):
                raise TypeError("real Codex generation output was not a JSON object")
            return json.dumps(decoded, separators=(",", ":"), ensure_ascii=False)
        finally:
            output_path.unlink(missing_ok=True)

    def redacted_calls(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(call) for call in self.calls]


def _chat_completion(content: str, *, model: str, call_id: str) -> dict[str, object]:
    return {
        "id": f"r8-real-codex-generation-{call_id}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _generation_schema(payload: Mapping[str, object]) -> Mapping[str, object]:
    response_format = payload.get("response_format")
    if isinstance(response_format, dict):
        json_schema = response_format.get("json_schema")
        schema = json_schema.get("schema") if isinstance(json_schema, dict) else None
        if isinstance(schema, dict):
            return cast(Mapping[str, object], schema)
    tools = payload.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            parameters = function.get("parameters") if isinstance(function, dict) else None
            if isinstance(parameters, dict):
                return cast(Mapping[str, object], parameters)
    rendered_messages = json.dumps(payload.get("messages"), ensure_ascii=False)
    stage_schemas = (
        ("Identify up to 20 durable topic probes", TopicMemoryProbeOutput),
        ("Evolve the whole Window into at most 20 topics", TopicMemoryGlobalOutput),
        ("Plan historical matches for each Topic probe", TopicMemoryPlannerOutput),
        ("Evolve one Topic probe against its candidate set", TopicMemoryEvolveOutput),
        ("Draft temporary Topics for unresolved evidence", TopicMemoryTemporaryOutput),
        ("Reconcile Topic proposals into one publication set", TopicMemoryReconcileOutput),
    )
    for marker, output_type in stage_schemas:
        if marker in rendered_messages:
            return cast(Mapping[str, object], output_type.model_json_schema())
    raise ValueError("generation request omitted a machine-readable output schema")


def _mapping_shape(value: object, *, depth: int = 0) -> object:
    if not isinstance(value, dict):
        return type(value).__name__
    if depth >= 2:
        return sorted(str(key) for key in value)
    return {str(key): _mapping_shape(child, depth=depth + 1) for key, child in value.items()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layers",
        default="e0,e1,e2,e3,e4",
        help="Comma-separated layers. E0 is always run and cannot be omitted.",
    )
    parser.add_argument("--output", type=Path, default=Path(".artifacts/topic-memory-r8"))
    parser.add_argument("--codex-timeout", type=float, default=180.0)
    parser.add_argument("--generation-timeout", type=float, default=240.0)
    parser.add_argument("--codex-model", default=os.environ.get("POWERCONTEXT_R8_CODEX_MODEL", DEFAULT_CODEX_MODEL))
    parser.add_argument(
        "--generation-model",
        default=os.environ.get("POWERCONTEXT_R8_GENERATION_MODEL", DEFAULT_GENERATION_MODEL),
    )
    return parser.parse_args()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(value, indent=2, sort_keys=True)}\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    input_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=dict(env),
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
        input=input_data,
        stdin=subprocess.DEVNULL if input_data is None else None,
    )


def _codex_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    if not events:
        raise ProductChainError("Codex emitted no machine-readable JSONL events")
    return events


def _walk_mappings(value: object) -> list[Mapping[str, object]]:
    found: list[Mapping[str, object]] = []
    if isinstance(value, dict):
        found.append(cast(Mapping[str, object], value))
        for child in value.values():
            found.extend(_walk_mappings(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_mappings(child))
    return found


def _completed_codex_mcp_calls(events: Sequence[Mapping[str, object]]) -> list[_CodexMcpCall]:
    calls: list[_CodexMcpCall] = []
    for sequence, event in enumerate(events, start=1):
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            continue
        server = item.get("server")
        tool = item.get("tool")
        arguments = item.get("arguments")
        status = item.get("status")
        if not isinstance(server, str) or not isinstance(tool, str):
            raise ProductChainError("Codex completed MCP event omitted its server or tool")
        if not isinstance(arguments, dict):
            raise ProductChainError(f"Codex completed MCP event for {server}.{tool} omitted structured arguments")
        if not isinstance(status, str):
            raise ProductChainError(f"Codex completed MCP event for {server}.{tool} omitted its status")
        calls.append(
            _CodexMcpCall(
                sequence=sequence,
                server=server,
                tool=tool,
                arguments=cast(Mapping[str, object], arguments),
                result=item.get("result"),
                status=status,
            )
        )
    return calls


def _artifact_identities(value: object) -> set[ArtifactIdentity]:
    identities: set[ArtifactIdentity] = set()
    if isinstance(value, dict):
        if {"family", "artifact_id", "revision"}.issubset(value):
            with contextlib.suppress(ProductChainError):
                identities.add(ArtifactIdentity.from_mapping(cast(Mapping[str, object], value)))
        for child in value.values():
            identities.update(_artifact_identities(child))
    elif isinstance(value, list):
        for child in value:
            identities.update(_artifact_identities(child))
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return identities
        identities.update(_artifact_identities(decoded))
    return identities


def _validate_codex_search_get_binding(
    events: Sequence[Mapping[str, object]],
    *,
    scope_id: str,
    query: str,
    exact_ref: ArtifactIdentity,
) -> dict[str, object]:
    """Prove one adjacent successful search/get pair reused the full scoped ref."""

    calls = _completed_codex_mcp_calls(events)
    observed = [(call.server, call.tool) for call in calls]
    expected = [
        ("powercontext", "search_topic_memory"),
        ("powercontext", "get_topic_memory"),
    ]
    if observed != expected:
        raise ProductChainError(
            "real Codex did not make exactly one adjacent powercontext search_topic_memory/get_topic_memory pair; "
            f"observed={observed}"
        )
    search, get = calls
    if search.status != "completed" or get.status != "completed":
        raise ProductChainError(
            f"real Codex MCP search/get did not both complete successfully; statuses={[search.status, get.status]}"
        )
    if search.arguments.get("scope_id") != scope_id:
        raise ProductChainError("real Codex search_topic_memory used the wrong scope_id")
    if search.arguments.get("query") != query or search.arguments.get("limit") != 8:
        raise ProductChainError("real Codex search_topic_memory used the wrong query or limit")
    if exact_ref not in _artifact_identities(search.result):
        raise ProductChainError("real Codex search_topic_memory result omitted the complete expected ArtifactRef")
    if get.arguments.get("scope_id") != scope_id:
        raise ProductChainError("real Codex get_topic_memory used a different scope_id from search_topic_memory")
    artifact = get.arguments.get("artifact")
    if (
        not isinstance(artifact, dict)
        or ArtifactIdentity.from_mapping(cast(Mapping[str, object], artifact)) != exact_ref
    ):
        raise ProductChainError("real Codex get_topic_memory did not reuse the complete expected ArtifactRef")
    return {
        "completed_mcp_call_sequences": [search.sequence, get.sequence],
        "adjacent_completed_mcp_calls": True,
        "tools": [search.tool, get.tool],
        "same_scope": True,
        "scope_id_sha256": digest_text(scope_id),
        "search_query_sha256": digest_text(query),
        "search_limit": 8,
        "search_result_exact_ref": exact_ref.as_dict(),
        "get_argument_exact_ref": exact_ref.as_dict(),
    }


def _codex_summary(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    event_types = [value for event in events if isinstance((value := event.get("type")), str)]
    tools = [call.tool for call in _completed_codex_mcp_calls(events)]
    return {
        "event_count": len(events),
        "event_types": sorted(set(event_types)),
        "mcp_tools_in_observed_order": tools,
        "raw_jsonl_retained": False,
        "full_model_output_retained": False,
    }


def _write_redacted_codex_jsonl(
    path: Path,
    events: Sequence[Mapping[str, object]],
    *,
    exact_ref: ArtifactIdentity | None = None,
) -> dict[str, object]:
    """Write event topology and allowed identity/tool facts, never event payloads."""

    exact_artifact_id = None if exact_ref is None else exact_ref.artifact_id
    records: list[dict[str, object]] = []
    for sequence, event in enumerate(events, start=1):
        event_type = event.get("type")
        tools: list[str] = []
        for item in _walk_mappings(event):
            for key in ("tool", "tool_name", "name"):
                value = item.get(key)
                if value in {"search_topic_memory", "get_topic_memory"} and value not in tools:
                    tools.append(str(value))
        record: dict[str, object] = {
            "sequence": sequence,
            "type": event_type if isinstance(event_type, str) else "unknown",
            "mcp_tools": tools,
        }
        if exact_artifact_id is not None:
            record["exact_artifact_id_observed"] = exact_artifact_id in json.dumps(event, sort_keys=True)
        records.append(record)
    path.write_text("".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records), encoding="utf-8")
    return {
        "file": path.name,
        "event_count": len(records),
        "prompt_or_model_text_retained": False,
        "tool_arguments_retained": False,
    }


def _run_codex(
    *,
    codex_home: Path,
    fixture: Path,
    environment: Mapping[str, str],
    prompt: str,
    model: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], float]:
    started = time.monotonic()
    completed = _run(
        (
            "codex",
            "exec",
            "--ephemeral",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "read-only",
            "--dangerously-bypass-hook-trust",
            "--model",
            model,
            "--config",
            'model_reasoning_effort="low"',
            "--cd",
            str(fixture),
            "-",
        ),
        cwd=fixture,
        env={**environment, "CODEX_HOME": str(codex_home)},
        timeout=timeout,
        input_data=prompt,
    )
    return _codex_events(completed.stdout), round(time.monotonic() - started, 3)


def _install_current_plugin(*, codex_home: Path, environment: Mapping[str, str], timeout: float) -> dict[str, object]:
    env = {**environment, "CODEX_HOME": str(codex_home)}
    marketplace = _run(
        ("codex", "plugin", "marketplace", "add", str(PROJECT_ROOT), "--json"),
        cwd=PROJECT_ROOT,
        env=env,
        timeout=timeout,
    )
    installed = _run(
        ("codex", "plugin", "add", PLUGIN_SELECTOR, "--json"),
        cwd=PROJECT_ROOT,
        env=env,
        timeout=timeout,
    )
    marketplace_result = json.loads(marketplace.stdout)
    installed_result = json.loads(installed.stdout)
    installed_path_value = installed_result.get("installedPath")
    if not isinstance(installed_path_value, str):
        raise ProductChainError("Codex plugin install did not return an installed path")
    installed_path = Path(installed_path_value).resolve()
    manifest_path = installed_path / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("name") != "powercontext" or not isinstance(manifest.get("version"), str):
        raise ProductChainError("installed Codex plugin manifest is invalid")
    return {
        "installed_path": installed_path,
        "marketplace_name": marketplace_result.get("name", "powercontext"),
        "plugin_name": manifest["name"],
        "plugin_version": manifest["version"],
        "plugin_manifest_sha256": _sha256_file(manifest_path),
    }


def _configure_installed_mcp(installed_path: Path, *, base_url: str) -> None:
    configuration_path = installed_path / ".mcp.json"
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    server = configuration["mcpServers"]["powercontext"]
    server["url"] = f"{base_url}/mcp"
    configuration_path.write_text(f"{json.dumps(configuration, indent=2)}\n", encoding="utf-8")


def _browser_python() -> Path:
    configured = os.environ.get("POWERCONTEXT_R8_PLAYWRIGHT_PYTHON")
    if configured:
        candidate = Path(configured)
    else:
        executable = shutil.which("playwright")
        if executable is None:
            raise ProductChainError("Playwright CLI is unavailable for E1 screenshot evidence")
        first_line = Path(executable).read_text(encoding="utf-8").splitlines()[0]
        if not first_line.startswith("#!"):
            raise ProductChainError("Playwright CLI has no discoverable Python interpreter")
        candidate = Path(first_line.removeprefix("#!"))
    if not candidate.is_file():
        raise ProductChainError("Playwright Python interpreter is unavailable")
    return candidate


def _browser_executable() -> Path:
    configured = os.environ.get("POWERCONTEXT_R8_BROWSER_EXECUTABLE")
    if configured:
        candidate = Path(configured)
    else:
        candidates = sorted((Path.home() / ".cache" / "ms-playwright").glob("chromium-*/chrome-linux64/chrome"))
        if not candidates:
            raise ProductChainError("no preinstalled Chromium executable is available")
        candidate = candidates[-1]
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ProductChainError("configured Chromium executable is unavailable")
    return candidate


def _capture_browser_evidence(
    *,
    directory: Path,
    base_url: str,
    token: str,
    artifact_ref: str,
    source_ref: str,
    environment: Mapping[str, str],
) -> dict[str, object]:
    desktop = directory / "topics-desktop-redacted.png"
    narrow = directory / "topics-narrow-redacted.png"
    audit = directory / "browser-audit.json"
    try:
        completed = _run(
            (
                str(_browser_python()),
                str(Path(__file__).with_name("browser_capture.py")),
                "--base-url",
                base_url,
                "--artifact-ref",
                artifact_ref,
                "--source-ref",
                source_ref,
                "--desktop",
                str(desktop),
                "--narrow",
                str(narrow),
                "--audit",
                str(audit),
            ),
            cwd=PROJECT_ROOT,
            env={
                **environment,
                "POWERCONTEXT_R8_BROWSER_TOKEN": token,
                "POWERCONTEXT_R8_BROWSER_EXECUTABLE": str(_browser_executable()),
            },
            timeout=90,
        )
    except subprocess.CalledProcessError as exc:
        raise ProductChainError(f"browser evidence failed: {exc.stderr[-2000:]}") from exc
    if completed.stdout or completed.stderr:
        # Browser output is intentionally discarded because it is not acceptance evidence.
        pass
    return {
        "status": "PASS",
        "desktop": desktop.name,
        "narrow": narrow.name,
        "audit": audit.name,
        "generated_content_redacted": True,
    }


def _e1_subprocess_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Allowlist the generic process environment and exclude all R8 layer inputs."""

    environment = {name: source[name] for name in _E1_SUBPROCESS_ENV_ALLOWLIST if name in source}
    # This value only satisfies the loopback Pydantic AI provider. Real Codex
    # authentication comes from its isolated home or the selected provider key.
    environment["OPENAI_API_KEY"] = "r8-loopback-only"
    return environment


def _codex_provider_environment(source: Mapping[str, str], env_key: str | None) -> dict[str, str]:
    if env_key is None:
        return {}
    value = source.get(env_key)
    if not value:
        raise ProductChainError("selected Codex provider environment credential is unavailable")
    return {env_key: value}


def _prepare_codex_home(
    temp_root: Path,
    *,
    source_environment: Mapping[str, str],
) -> tuple[Path, dict[str, object], str | None]:
    source_home = Path(source_environment.get("CODEX_HOME", str(Path.home() / ".codex")))
    source_auth = source_home / "auth.json"
    if not source_auth.is_file():
        raise ProductChainError("Codex auth.json is unavailable for isolated E1 execution")
    source_digest = _sha256_file(source_auth)
    codex_home = temp_root / "codex-home"
    codex_home.mkdir(mode=0o700)
    destination = codex_home / "auth.json"
    shutil.copyfile(source_auth, destination)
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    if stat.S_IMODE(destination.stat().st_mode) != 0o600:
        raise ProductChainError("isolated Codex auth copy is not mode 0600")
    provider_keys, provider_env_key = _copy_codex_provider_config(
        source_home / "config.toml",
        codex_home / "config.toml",
    )
    return (
        codex_home,
        {
            "source_auth_sha256_before": source_digest,
            "source_auth_path_recorded": False,
            "temporary_auth_mode": "0600",
            "temporary_home_mode": oct(stat.S_IMODE(codex_home.stat().st_mode)),
            "provider_config_keys_copied": provider_keys,
            "unrelated_user_config_copied": False,
        },
        provider_env_key,
    )


def _copy_codex_provider_config(source: Path, destination: Path) -> tuple[list[str], str | None]:
    if not source.is_file():
        raise ProductChainError("Codex provider config is unavailable for isolated E1 execution")
    configuration = tomllib.loads(source.read_text(encoding="utf-8"))
    selected = configuration.get("model_provider")
    providers = configuration.get("model_providers")
    if selected is None:
        # Codex's built-in OpenAI provider needs no explicit provider block;
        # the isolated auth.json copy is sufficient and no unrelated config
        # should cross the acceptance boundary.
        destination.write_text("", encoding="utf-8")
        destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return [], None
    if not isinstance(selected, str) or not isinstance(providers, dict):
        raise ProductChainError("Codex selected provider config is invalid")
    provider = providers.get(selected)
    if not isinstance(provider, dict):
        raise ProductChainError("selected Codex provider definition is unavailable")
    allowed = ("name", "base_url", "wire_api", "requires_openai_auth", "env_key")
    values = {key: provider[key] for key in allowed if key in provider}
    if not isinstance(values.get("base_url"), str) or not isinstance(values.get("env_key"), str):
        raise ProductChainError("selected Codex provider is missing its endpoint or environment key")

    def encode(value: object) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, str):
            return json.dumps(value)
        raise ProductChainError("selected Codex provider contains an unsupported value")

    lines = [f"model_provider = {json.dumps(selected)}", "", f"[model_providers.{selected}]"]
    lines.extend(f"{key} = {encode(value)}" for key, value in values.items())
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return sorted(values), cast(str, values["env_key"])


def run_e1(  # noqa: C901
    directory: Path,
    *,
    codex_model: str,
    generation_model: str,
    codex_timeout: float,
    generation_timeout: float,
) -> dict[str, object]:
    """Run real Codex, plugin hook, MCP, generation, HTTP, and browser acceptance."""

    directory.mkdir(parents=True, exist_ok=True)
    if shutil.which("codex") is None:
        return _unavailable("E1", "Codex CLI is not installed")
    source_environment = dict(os.environ)
    environment = _e1_subprocess_environment(source_environment)
    original_openai_key = source_environment.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "r8-loopback-only"

    token = secrets.token_urlsafe(32)
    timeline = AccessTimeline()
    prepared_audit = PreparedContextAudit()
    server = None
    generation_server = None
    temp_root_value = ""
    source_auth: Path | None = None
    source_auth_digest = ""
    result: dict[str, object]
    failure_capture = _WorkerFailureCapture()
    worker_logger = logging.getLogger("powercontext.builtin.runtime.artifact_processing")
    worker_logger.addHandler(failure_capture)
    try:
        with tempfile.TemporaryDirectory(prefix="powercontext-r8-e1-") as temp_value:
            temp_root = Path(temp_value)
            temp_root_value = temp_value
            codex_home, auth_audit, provider_env_key = _prepare_codex_home(
                temp_root,
                source_environment=source_environment,
            )
            provider_environment = _codex_provider_environment(source_environment, provider_env_key)
            source_home = Path(source_environment.get("CODEX_HOME", str(Path.home() / ".codex")))
            source_auth = source_home / "auth.json"
            source_auth_digest = str(auth_audit["source_auth_sha256_before"])
            fixture = temp_root / "fixture"
            fixture.mkdir()
            _run(("git", "init", "--quiet"), cwd=fixture, env=environment, timeout=20)
            _run(("git", "config", "user.email", "r8@example.invalid"), cwd=fixture, env=environment, timeout=20)
            _run(("git", "config", "user.name", "R8 Fixture"), cwd=fixture, env=environment, timeout=20)
            generation_root = temp_root / "generation"
            generation_root.mkdir()
            generation_home, generation_auth_audit, generation_provider_env_key = _prepare_codex_home(
                generation_root,
                source_environment=source_environment,
            )
            if generation_provider_env_key != provider_env_key:
                raise ProductChainError("isolated Codex homes selected different provider credentials")
            generation_fixture = generation_root / "fixture"
            generation_fixture.mkdir()
            _run(("git", "init", "--quiet"), cwd=generation_fixture, env=environment, timeout=20)
            generation_bridge = _RealCodexChatBridge(
                codex_home=generation_home,
                fixture=generation_fixture,
                environment={**environment, **provider_environment},
                model=generation_model,
                timeout=codex_timeout,
            )
            generation_server = start_loopback_server(generation_bridge.app())
            generation_header = {"Authorization": SecretStr("Bearer r8-real-codex-loopback")}

            settings = ServerSettings(
                auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{temp_root / 'runtime.db'}"),
                runtime=RuntimeConfig(
                    topic_memory_source_window_limit=1,
                    artifact_processing_worker_timeout_seconds=generation_timeout,
                ),
                inference=InferenceConfig(
                    generation_model="openai-chat:r8-real-codex-generation",
                    generation_base_url=AnyHttpUrl(f"{generation_server.base_url}/v1"),
                    generation_headers=generation_header,
                    generation_timeout_seconds=generation_timeout,
                    generation_max_requests=1,
                ),
                mcp=McpConfig(enabled=True),
                dashboard=DashboardConfig(enabled=True),
            )
            app = create_server_app(
                settings=settings,
                scheduler_path=temp_root / "scheduler.db",
                middleware=(
                    Middleware(AccessTimelineMiddleware, timeline=timeline),
                    Middleware(PreparedContextAuditMiddleware, audit=prepared_audit),
                ),
            )
            server = start_loopback_server(app, startup_timeout=60)
            scope_id = asyncio.run(default_scope_id(server.base_url, token=token))
            fixture_instructions = (
                "# R8 synthetic acceptance fixture\n\n"
                f"When the complete user prompt is exactly `{E1_CANARY}`, use only the powercontext MCP server. "
                f"First call search_topic_memory with scope_id `{scope_id}`, query `{E1_CANARY}`, and limit 8. "
                "Copy the first hit's artifact object exactly into get_topic_memory with the same scope_id. "
                "Finally reply only with that exact family, artifact_id, and revision. Do not use shell tools.\n"
            )
            (fixture / "AGENTS.md").write_text(fixture_instructions, encoding="utf-8")
            _run(("git", "add", "AGENTS.md"), cwd=fixture, env=environment, timeout=20)
            _run(
                ("git", "commit", "--quiet", "-m", "test: configure synthetic R8 fixture"),
                cwd=fixture,
                env=environment,
                timeout=20,
            )
            plugin = _install_current_plugin(
                codex_home=codex_home,
                environment=environment,
                timeout=codex_timeout,
            )
            installed_path = plugin.pop("installed_path")
            if not isinstance(installed_path, Path):
                raise ProductChainError("installed plugin path is invalid")
            _configure_installed_mcp(installed_path, base_url=server.base_url)
            codex_environment = {
                **environment,
                **provider_environment,
                "POWERCONTEXT_CODEX_AUTHORIZATION": f"Bearer {token}",
                "POWERCONTEXT_CODEX_SCOPE_ID": scope_id,
                "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "true",
                "POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE": "false",
                "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "5",
                "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "12",
            }
            first_prompt = (
                f"Synthetic acceptance input. The durable release sentinel is {E1_CANARY}. "
                "Remember only this fabricated project decision. Reply exactly ACK and do not use tools."
            )
            first_events, first_duration = _run_codex(
                codex_home=codex_home,
                fixture=fixture,
                environment=codex_environment,
                prompt=first_prompt,
                model=codex_model,
                timeout=codex_timeout,
            )
            first_timeline = timeline.snapshot()
            first_paths = [entry.get("path") for entry in first_timeline]
            if "/v1/context/prepare" not in first_paths or "/v1/sources/content" not in first_paths:
                observed_paths = sorted({str(path) for path in first_paths})
                raise ProductChainError(
                    f"real Codex UserPromptSubmit hook did not prepare and capture; paths={observed_paths}"
                )

            try:
                chain = asyncio.run(
                    exercise_http_mcp_prepared_web_chain(
                        base_url=server.base_url,
                        token=token,
                        scope_id=scope_id,
                        query=E1_CANARY,
                        source_id=None,
                        source_content=None,
                        expected_detail_marker=None,
                        timeline=timeline,
                        search_timeout_seconds=generation_timeout,
                    )
                )
            except ProductChainError as exc:
                raise ProductChainError(
                    f"{exc}; redacted_worker_failures={failure_capture.failures[-3:]}; "
                    f"redacted_generation_calls={generation_bridge.redacted_calls()}"
                ) from exc
            if not chain.source_ref.startswith("content:codex-user-prompt:"):
                raise ProductChainError("generated Topic did not retain the first Codex hook SourceRef")

            second_prompt = E1_CANARY
            prior_prepared_observations = len(prepared_audit.for_query(second_prompt))
            second_events, second_duration = _run_codex(
                codex_home=codex_home,
                fixture=fixture,
                environment=codex_environment,
                prompt=second_prompt,
                model=codex_model,
                timeout=codex_timeout,
            )
            second_summary = _codex_summary(second_events)
            mcp_binding = _validate_codex_search_get_binding(
                second_events,
                scope_id=scope_id,
                query=E1_CANARY,
                exact_ref=chain.exact_ref,
            )
            prepared_observations = prepared_audit.for_query(second_prompt)[prior_prepared_observations:]
            expected_ref = chain.exact_ref.as_dict()
            if not any(
                isinstance(observation.get("topic_refs"), list)
                and expected_ref in cast(list[object], observation["topic_refs"])
                and observation.get("full_detail_absent") is True
                for observation in prepared_observations
            ):
                raise ProductChainError(
                    "the second real Codex prompt did not receive the same compact Topic ref from its hook"
                )
            first_jsonl = _write_redacted_codex_jsonl(directory / "first-codex-redacted.jsonl", first_events)
            second_jsonl = _write_redacted_codex_jsonl(
                directory / "second-codex-redacted.jsonl",
                second_events,
                exact_ref=chain.exact_ref,
            )

            browser = _capture_browser_evidence(
                directory=directory,
                base_url=server.base_url,
                token=token,
                artifact_ref=chain.exact_ref.display(),
                source_ref=chain.source_ref,
                environment=environment,
            )
            server.stop()
            port_closed = server.port_is_closed()
            server = None
            if not port_closed:
                raise ProductChainError("E1 PowerContext port remained open after shutdown")
            generation_server.stop()
            generation_port_closed = generation_server.port_is_closed()
            generation_server = None
            if not generation_port_closed:
                raise ProductChainError("E1 generation bridge port remained open after shutdown")
            if source_auth is None or _sha256_file(source_auth) != source_auth_digest:
                raise ProductChainError("E1 changed the operator's original Codex auth file")
            require_no_worker_failures("E1", failure_capture.failures)

            result = {
                "schema": "powercontext.topic-memory-r8.e1.v1",
                "status": "PASS",
                "environment": {
                    "codex_cli": _version(("codex", "--version"), environment=environment),
                    "codex_model": codex_model,
                    "plugin": plugin,
                    "hooks_enabled": True,
                    "mcp_enabled": True,
                    "generation_provider": "real Codex provider through a content-free loopback protocol bridge",
                    "generation_model": generation_model,
                    "generation_calls": generation_bridge.redacted_calls(),
                    "database": "isolated temporary file SQLite",
                    "server": "loopback random port",
                    "authentication": "one-time bearer; value not retained",
                    "fixture": "isolated temporary Git repository",
                    "subprocess_environment": {
                        "policy": "explicit allowlist plus selected Codex provider credential",
                        "r8_layer_configuration_keys_forwarded": [],
                        "selected_provider_credential_forwarded": provider_env_key is not None,
                    },
                },
                "first_codex": {
                    "prompt_sha256": digest_text(first_prompt),
                    "prompt_retained": False,
                    "duration_seconds": first_duration,
                    **_codex_summary(first_events),
                    "hook_paths": ["/v1/context/prepare", "/v1/sources/content"],
                    "redacted_jsonl": first_jsonl,
                },
                "chain": chain.as_dict(),
                "second_codex": {
                    "prompt_sha256": digest_text(second_prompt),
                    "prompt_retained": False,
                    "duration_seconds": second_duration,
                    **second_summary,
                    "exact_ref": chain.exact_ref.as_dict(),
                    "mcp_binding": mcp_binding,
                    "redacted_jsonl": second_jsonl,
                    "prepared_context": {
                        "observations": list(prepared_observations),
                        "same_exact_ref": True,
                        "full_detail_absent": True,
                    },
                },
                "browser": browser,
                "auth_audit": auth_audit,
                "generation_auth_audit": generation_auth_audit,
                "redaction": {
                    "prompt_content_recorded": False,
                    "full_model_output_recorded": False,
                    "credentials_recorded": False,
                    "provider_body_recorded": False,
                    "screenshots_mask_generated_content": True,
                },
                "cleanup": {
                    "powercontext_port_closed": True,
                    "generation_bridge_port_closed": True,
                    "original_auth_unchanged": True,
                },
                "worker_failures": list(failure_capture.failures),
            }
        result["cleanup"]["temporary_tree_removed"] = not Path(temp_root_value).exists()  # type: ignore[index]
        if result["cleanup"]["temporary_tree_removed"] is not True:  # type: ignore[index]
            raise ProductChainError("E1 temporary tree was not removed")
        _write_json(directory / "e1-report.json", result)
        return result
    finally:
        worker_logger.removeHandler(failure_capture)
        if server is not None:
            server.stop()
        if generation_server is not None:
            generation_server.stop()
        if original_openai_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original_openai_key


def run_e2(  # noqa: C901
    directory: Path,
    *,
    config: _RealEmbeddingConfig,
    e1_status: str,
    generation_timeout: float,
) -> dict[str, object]:
    """Run the E1-compatible chain with a real embedding profile and fallback."""

    if e1_status != "PASS":
        raise ProductChainError(f"E2 requires E1 PASS; observed {e1_status}")
    directory.mkdir(parents=True, exist_ok=True)
    runtime_directory = Path(tempfile.mkdtemp(prefix=".runtime-", dir=directory))
    fake = FakeInference(canary=E2_CANARY, detail_marker="R8-E2-DETAIL-MUST-NOT-BE-PREPARED")
    inference_server = start_loopback_server(fake.app())
    timeline = AccessTimeline()
    server = None
    fallback_server = None
    fallback_capture = _EmbeddingFallbackCapture()
    search_logger = logging.getLogger("powercontext.builtin.runtime.application")
    search_logger.addHandler(fallback_capture)
    temporary_openai_key = False
    result: dict[str, object] | None = None
    try:
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = "r8-loopback-only"
            temporary_openai_key = True
        provider_header = {"Authorization": SecretStr("Bearer r8-fake-provider")}
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{runtime_directory / 'runtime.db'}")
        settings = ServerSettings(
            auth=BearerAuthConfig(enabled=True, token=SecretStr(_E2_TOKEN)),
            database=database,
            runtime=RuntimeConfig(
                topic_memory_source_window_limit=1,
                artifact_processing_worker_timeout_seconds=generation_timeout,
            ),
            inference=InferenceConfig(
                generation_model="openai-chat:r8-fake-generation",
                generation_base_url=AnyHttpUrl(f"{inference_server.base_url}/v1"),
                generation_headers=provider_header,
                generation_timeout_seconds=10,
                generation_max_requests=1,
                generation_model_settings={"max_tokens": 1024},
                embedding_model=config.model,
                embedding_base_url=config.base_url,
                embedding_headers=config.headers,
                embedding_profile_id=config.profile_id,
                embedding_dimension=config.dimension,
                embedding_normalization=config.normalization,
                embedding_timeout_seconds=config.timeout_seconds,
            ),
            mcp=McpConfig(enabled=True),
            dashboard=DashboardConfig(enabled=True),
        )
        app = create_server_app(
            settings=settings,
            scheduler_path=runtime_directory / "scheduler.db",
            middleware=(Middleware(AccessTimelineMiddleware, timeline=timeline),),
        )
        server = start_loopback_server(app, startup_timeout=60)
        scope_id = asyncio.run(default_scope_id(server.base_url, token=_E2_TOKEN))
        chain = asyncio.run(
            exercise_http_mcp_prepared_web_chain(
                base_url=server.base_url,
                token=_E2_TOKEN,
                scope_id=scope_id,
                query=E2_CANARY,
                source_id="r8-real-embedding-source",
                source_content=f"Synthetic durable embedding decision: use {E2_CANARY} for the R8 E2 chain.",
                expected_detail_marker=fake.detail_marker,
                timeline=timeline,
                generation=fake,
                search_timeout_seconds=generation_timeout,
            )
        )
        if chain.search_mode != "hybrid":
            raise ProductChainError(f"E2 real embedding search used {chain.search_mode}, not hybrid")
        server.stop()
        server_closed = server.port_is_closed()
        server = None
        if not server_closed:
            raise ProductChainError("E2 hybrid server port remained open")

        fallback_settings = ServerSettings(
            auth=BearerAuthConfig(enabled=True, token=SecretStr(_E2_TOKEN)),
            database=database,
            inference=InferenceConfig(),
            mcp=McpConfig(enabled=False),
            dashboard=DashboardConfig(enabled=False),
            handoff_report=HandoffReportConfig(enabled=False),
        )
        fallback_server = start_loopback_server(
            create_server_app(settings=fallback_settings, embedding_model=_FallbackEmbedding(config)),
            startup_timeout=60,
        )
        fallback_search = asyncio.run(
            _search_topic_once(
                fallback_server.base_url,
                token=_E2_TOKEN,
                scope_id=scope_id,
                query=E2_CANARY,
            )
        )
        fallback_ref = ArtifactIdentity.from_mapping(cast(Mapping[str, object], fallback_search["artifact"]))
        if fallback_search["mode"] != "fts" or fallback_ref != chain.exact_ref:
            raise ProductChainError("E2 controlled embedding outage did not preserve the exact ref through FTS")
        expected_fallback = {
            "event": "topic_memory.search.embedding_fallback",
            "mode": "fts",
            "error_code": "inference_unavailable",
        }
        if expected_fallback not in fallback_capture.events:
            raise ProductChainError("E2 controlled embedding outage emitted no stable fallback signal")
        fallback_server.stop()
        fallback_closed = fallback_server.port_is_closed()
        fallback_server = None
        if not fallback_closed:
            raise ProductChainError("E2 fallback server port remained open")
        result = {
            "schema": "powercontext.topic-memory-r8.e2.v1",
            "status": "PASS",
            "inherits": {"E1": "PASS"},
            "environment": {
                "database": "isolated temporary file SQLite with production sqlite-vec",
                "embedding": {
                    "provider": "real configured provider",
                    "model": config.model,
                    "profile": config.profile_id,
                    "dimension": config.dimension,
                    "normalization": config.normalization,
                    "custom_base_url": config.base_url is not None,
                    "configured_header_names": sorted(config.headers),
                },
            },
            "hybrid_chain": chain.as_dict(),
            "controlled_fallback": {
                "injection": "InferenceUnavailableError at query embedding boundary",
                "search_mode": "fts",
                "exact_ref": fallback_ref.as_dict(),
                "signal": expected_fallback,
                "query_content_retained": False,
                "provider_body_retained": False,
            },
            "redaction": {
                "embedding_request_body_recorded": False,
                "embedding_response_body_recorded": False,
                "credentials_recorded": False,
            },
        }
    finally:
        search_logger.removeHandler(fallback_capture)
        if fallback_server is not None:
            fallback_server.stop()
        if server is not None:
            server.stop()
        inference_server.stop()
        shutil.rmtree(runtime_directory)
        if temporary_openai_key:
            os.environ.pop("OPENAI_API_KEY", None)
    if result is None:
        raise ProductChainError("E2 did not produce a result")
    result["cleanup"] = {
        "hybrid_server_port_closed": True,
        "fallback_server_port_closed": True,
        "fake_generation_port_closed": inference_server.port_is_closed(),
        "temporary_runtime_removed": not runtime_directory.exists(),
    }
    cleanup = cast(dict[str, object], result["cleanup"])
    if not all(value is True for value in cleanup.values()):
        raise ProductChainError("E2 cleanup left a listener or temporary runtime behind")
    _write_json(directory / "e2-report.json", result)
    return result


async def _search_topic_once(
    base_url: str,
    *,
    token: str | None,
    scope_id: str,
    query: str,
) -> dict[str, object]:
    async with PowerContextClient(base_url, token=token, timeout=10) as client:
        search = await client.search_topic_memory(SearchTopicMemoryRequest(scope_id=scope_id, query=query, limit=8))
    if not search.hits:
        raise ProductChainError("Topic Memory search returned no hit")
    return {
        "mode": str(search.mode),
        "artifact": search.hits[0].artifact.model_dump(mode="json"),
    }


async def _capture_and_flush(
    base_url: str,
    *,
    scope_id: str,
    source_id: str,
    content: str,
) -> dict[str, object]:
    async with PowerContextClient(base_url, timeout=10) as client:
        await client.capture_content_source(
            CaptureContentSourceRequest(
                scope_id=scope_id,
                source_id=source_id,
                content=content,
                metadata={"origin": "r8-oceanbase", "synthetic": True},
            )
        )
        started = time.monotonic()
        response = await client.flush_topic_memory(FlushTopicMemoryRequest(scope_id=scope_id))
        duration = round(time.monotonic() - started, 3)
    if response.status != "accepted":
        raise ProductChainError(f"E3 Topic Memory flush was not accepted: {response.status}")
    return {"status": str(response.status), "duration_seconds": duration}


async def _oceanbase_processing_state(
    config: _OceanBaseLayerConfig,
    *,
    scope_id: str,
) -> dict[str, object]:
    async with (
        OceanBaseProfile.open(config.database, tables=BUILTIN_TABLES) as profile,
        profile.database.transaction() as connection,
    ):
        pending = await ArtifactProcessingPendingRepository().load(
            connection,
            scope_id,
            TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
        )
        cursor = await SourceCursorRepository().load(
            connection,
            scope_id,
            TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
        )
    return {
        "pending": None
        if pending is None
        else {
            "source_through": pending.source_through,
            "flush_generation": pending.flush_generation,
            "handled_flush_generation": pending.handled_flush_generation,
        },
        "cursor_sequence": None if cursor is None else cursor.cursor.sequence,
        "cursor_generation": None if cursor is None else cursor.generation,
    }


async def _require_empty_oceanbase_schema(config: _OceanBaseLayerConfig) -> None:
    async with (
        OceanBaseProfile.open(config.database, tables=()) as profile,
        profile.database.transaction() as connection,
    ):
        result = await connection.exec_driver_sql(
            "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"
        )
        count = result.scalar_one()
    if int(count or 0) != 0:
        raise ProductChainError("E3 dedicated OceanBase schema must be empty before acceptance")


async def _drop_oceanbase_runtime_tables(config: _OceanBaseLayerConfig) -> bool:
    async with (
        OceanBaseProfile.open(config.database, tables=()) as profile,
        profile.database.transaction() as connection,
    ):
        await connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in reversed(BUILTIN_TABLES):
                await connection.exec_driver_sql(f"DROP TABLE IF EXISTS `{table.name}`")
        finally:
            await connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
        result = await connection.exec_driver_sql(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'pc\\_%'"
        )
        count = result.scalar_one()
    return int(count) == 0


def _background_environment(config: _OceanBaseLayerConfig, *, inference_base_url: str) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
    environment.update({
        "OPENAI_API_KEY": "r8-loopback-only",
        "POWERCONTEXT_SERVER_DATABASE_KIND": "oceanbase",
        "POWERCONTEXT_SERVER_DATABASE_URL": config.database.url.get_secret_value(),
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE": "background",
        "POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SOURCE_WINDOW_LIMIT": "1",
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_WORKER_TIMEOUT_SECONDS": "60",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL": "openai-chat:r8-fake-generation",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL": f"{inference_base_url}/v1",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS": json.dumps({"Authorization": "Bearer r8-fake-provider"}),
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS": "30",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS": "1",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS": json.dumps({"max_tokens": 1024}),
        "POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED": "false",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "false",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_LOGGING_ACCESS": "false",
        "POWERCONTEXT_SERVER_LOGGING_FORMAT": "json",
    })
    return environment


def _start_background_process(config: _OceanBaseLayerConfig, *, inference_base_url: str) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        (
            sys.executable,
            "-c",
            "from powercontext.server.cli import app; app()",
            "run",
            "--role",
            "background",
        ),
        cwd=PROJECT_ROOT,
        env=_background_environment(config, inference_base_url=inference_base_url),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process


def _stop_background_process(process: subprocess.Popen[bytes]) -> int:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    return int(process.returncode or 0)


async def _wait_for_topic_after_restart(
    base_url: str,
    *,
    process: subprocess.Popen[bytes],
    scope_id: str,
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    async with PowerContextClient(base_url, timeout=10) as client:
        while time.monotonic() < deadline:
            returncode = process.poll()
            if returncode is not None:
                raise ProductChainError(f"E3 background process exited before recovery: {returncode}")
            search = await client.search_topic_memory(
                SearchTopicMemoryRequest(scope_id=scope_id, query=E3_CANARY, limit=8)
            )
            if search.hits:
                return {
                    "mode": str(search.mode),
                    "artifact": search.hits[0].artifact.model_dump(mode="json"),
                }
            await asyncio.sleep(0.25)
    raise ProductChainError("E3 restarted background process did not recover durable pending work")


def run_e3(  # noqa: C901
    directory: Path,
    *,
    config: _OceanBaseLayerConfig,
    generation_timeout: float,
) -> dict[str, object]:
    """Exercise OceanBase API/background process separation and restart recovery."""

    directory.mkdir(parents=True, exist_ok=True)
    asyncio.run(_require_empty_oceanbase_schema(config))
    fake = FakeInference(
        canary=E3_CANARY,
        detail_marker="R8-E3-DETAIL-MUST-NOT-BE-PREPARED",
        generation_delay_seconds=3.0,
    )
    inference_server = start_loopback_server(fake.app())
    timeline = AccessTimeline()
    api_server = None
    first_background = None
    restarted_background = None
    schema_cleaned = False
    temporary_openai_key = False
    result: dict[str, object] | None = None
    try:
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = "r8-loopback-only"
            temporary_openai_key = True
        provider_header = {"Authorization": SecretStr("Bearer r8-fake-provider")}
        settings = ServerSettings(
            auth=BearerAuthConfig(enabled=False),
            database=config.database,
            runtime=RuntimeConfig(
                artifact_processing_role="api",
                topic_memory_source_window_limit=1,
                artifact_processing_worker_timeout_seconds=60,
            ),
            inference=InferenceConfig(
                generation_model="openai-chat:r8-fake-generation",
                generation_base_url=AnyHttpUrl(f"{inference_server.base_url}/v1"),
                generation_headers=provider_header,
                generation_timeout_seconds=30,
                generation_max_requests=1,
                generation_model_settings={"max_tokens": 1024},
            ),
            mcp=McpConfig(enabled=False),
            dashboard=DashboardConfig(enabled=False),
            handoff_report=HandoffReportConfig(enabled=False),
        )
        api_server = start_loopback_server(
            create_server_app(
                settings=settings,
                middleware=(Middleware(AccessTimelineMiddleware, timeline=timeline),),
            ),
            startup_timeout=90,
        )
        scope_id = asyncio.run(default_scope_id(api_server.base_url, token=None))
        flush = asyncio.run(
            _capture_and_flush(
                api_server.base_url,
                scope_id=scope_id,
                source_id="r8-oceanbase-source",
                content=f"Synthetic OceanBase recovery decision: use {E3_CANARY} for the R8 E3 chain.",
            )
        )
        before_worker = asyncio.run(_oceanbase_processing_state(config, scope_id=scope_id))
        pending_before = before_worker.get("pending")
        if not isinstance(pending_before, dict):
            raise ProductChainError("E3 API flush did not persist Pending state")
        if pending_before.get("flush_generation") == pending_before.get("handled_flush_generation"):
            raise ProductChainError("E3 API flush did not persist an unhandled generation")

        first_background = _start_background_process(config, inference_base_url=inference_server.base_url)
        if not fake.wait_for_topic_generation_started(min(generation_timeout, 60)):
            returncode = first_background.poll()
            raise ProductChainError(f"E3 first background process did not start Topic generation: {returncode}")
        first_exit = _stop_background_process(first_background)
        first_background = None
        after_interruption = asyncio.run(_oceanbase_processing_state(config, scope_id=scope_id))
        interrupted_pending = after_interruption.get("pending")
        if not isinstance(interrupted_pending, dict):
            raise ProductChainError("E3 interrupted worker lost durable Pending state")

        restarted_background = _start_background_process(config, inference_base_url=inference_server.base_url)
        recovered = asyncio.run(
            _wait_for_topic_after_restart(
                api_server.base_url,
                process=restarted_background,
                scope_id=scope_id,
                timeout_seconds=generation_timeout,
            )
        )
        recovered_ref = ArtifactIdentity.from_mapping(cast(Mapping[str, object], recovered["artifact"]))
        after_recovery = asyncio.run(_oceanbase_processing_state(config, scope_id=scope_id))
        cursor_sequence = after_recovery.get("cursor_sequence")
        source_through = pending_before.get("source_through")
        if (
            not isinstance(cursor_sequence, int)
            or not isinstance(source_through, int)
            or cursor_sequence < source_through
        ):
            raise ProductChainError("E3 recovered cursor did not cover the durable Pending watermark")
        final_pending = after_recovery.get("pending")
        if isinstance(final_pending, dict) and final_pending.get("flush_generation") != final_pending.get(
            "handled_flush_generation"
        ):
            raise ProductChainError("E3 recovered worker left the explicit flush generation unhandled")
        restarted_exit = _stop_background_process(restarted_background)
        restarted_background = None
        result = {
            "schema": "powercontext.topic-memory-r8.e3.v1",
            "status": "PASS",
            "environment": {
                "database": "caller-provided empty disposable OceanBase schema",
                "schema_fingerprint": config.schema_fingerprint,
                "api_role": "harness process",
                "background_role": "separate production CLI process",
                "generation": "deterministic loopback provider",
            },
            "flush": flush,
            "durability": {
                "before_background": before_worker,
                "after_forced_background_stop": after_interruption,
                "after_restart_recovery": after_recovery,
                "pending_survived_process_stop": True,
                "cursor_covered_pending_watermark": True,
            },
            "recovery": {
                "first_background_exit_code": first_exit,
                "restarted_background_exit_code": restarted_exit,
                "search_mode": recovered["mode"],
                "exact_ref": recovered_ref.as_dict(),
            },
            "process_boundary": {
                "api_and_background_are_distinct_processes": True,
                "background_restart_count": 1,
            },
            "redaction": {
                "database_url_recorded": False,
                "source_content_recorded": False,
                "provider_body_recorded": False,
            },
        }
    finally:
        if restarted_background is not None:
            _stop_background_process(restarted_background)
        if first_background is not None:
            _stop_background_process(first_background)
        if api_server is not None:
            api_server.stop()
        inference_server.stop()
        schema_cleaned = asyncio.run(_drop_oceanbase_runtime_tables(config))
        if temporary_openai_key:
            os.environ.pop("OPENAI_API_KEY", None)
    if result is None:
        raise ProductChainError("E3 did not produce a result")
    result["cleanup"] = {
        "api_port_closed": api_server is not None and api_server.port_is_closed(),
        "fake_provider_port_closed": inference_server.port_is_closed(),
        "background_processes_stopped": True,
        "dedicated_schema_powercontext_tables_removed": schema_cleaned,
    }
    cleanup = cast(dict[str, object], result["cleanup"])
    if not all(value is True for value in cleanup.values()):
        raise ProductChainError("E3 cleanup left a process, listener, or PowerContext table behind")
    _write_json(directory / "e3-report.json", result)
    return result


def run_e4(directory: Path, *, generation_timeout: float) -> dict[str, object]:  # noqa: C901
    """Run the full product chain on one real embedded seekDB all-role runtime."""

    directory.mkdir(parents=True, exist_ok=True)
    runtime_directory = Path(tempfile.mkdtemp(prefix=".runtime-", dir=directory))
    fake = FakeInference(canary=E4_CANARY, detail_marker="R8-E4-DETAIL-MUST-NOT-BE-PREPARED")
    inference_server = start_loopback_server(fake.app())
    timeline = AccessTimeline()
    server = None
    fts_server = None
    temporary_openai_key = False
    result: dict[str, object] | None = None
    try:
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = "r8-loopback-only"
            temporary_openai_key = True
        database = SeekDBConfig(path=runtime_directory / "seekdb")
        provider_header = {"Authorization": SecretStr("Bearer r8-fake-provider")}
        settings = ServerSettings(
            auth=BearerAuthConfig(enabled=True, token=SecretStr(_E4_TOKEN)),
            database=database,
            runtime=RuntimeConfig(
                artifact_processing_role="all",
                topic_memory_source_window_limit=1,
                artifact_processing_worker_timeout_seconds=generation_timeout,
            ),
            inference=InferenceConfig(
                generation_model="openai-chat:r8-fake-generation",
                generation_base_url=AnyHttpUrl(f"{inference_server.base_url}/v1"),
                generation_headers=provider_header,
                generation_timeout_seconds=10,
                generation_max_requests=1,
                generation_model_settings={"max_tokens": 1024},
                embedding_model="openai:r8-fake-embedding",
                embedding_base_url=AnyHttpUrl(f"{inference_server.base_url}/v1"),
                embedding_headers=provider_header,
                embedding_profile_id="r8-seekdb-embedding-4-unit",
                embedding_dimension=4,
                embedding_timeout_seconds=5,
            ),
            mcp=McpConfig(enabled=True),
            dashboard=DashboardConfig(enabled=True),
            handoff_report=HandoffReportConfig(enabled=False),
        )
        server = start_loopback_server(
            create_server_app(
                settings=settings,
                scheduler_path=runtime_directory / "scheduler.db",
                middleware=(Middleware(AccessTimelineMiddleware, timeline=timeline),),
            ),
            startup_timeout=90,
        )
        scope_id = asyncio.run(default_scope_id(server.base_url, token=_E4_TOKEN))
        chain = asyncio.run(
            exercise_http_mcp_prepared_web_chain(
                base_url=server.base_url,
                token=_E4_TOKEN,
                scope_id=scope_id,
                query=E4_CANARY,
                source_id="r8-seekdb-source",
                source_content=f"Synthetic seekDB release decision: use {E4_CANARY} for the R8 E4 chain.",
                expected_detail_marker=fake.detail_marker,
                timeline=timeline,
                generation=fake,
                search_timeout_seconds=generation_timeout,
            )
        )
        if chain.search_mode != "hybrid":
            raise ProductChainError(f"E4 seekDB vector path used {chain.search_mode}, not hybrid")
        server.stop()
        hybrid_closed = server.port_is_closed()
        server = None
        if not hybrid_closed:
            raise ProductChainError("E4 hybrid server port remained open")

        fts_settings = ServerSettings(
            auth=BearerAuthConfig(enabled=True, token=SecretStr(_E4_TOKEN)),
            database=database,
            runtime=RuntimeConfig(artifact_processing_role="all"),
            inference=InferenceConfig(),
            mcp=McpConfig(enabled=False),
            dashboard=DashboardConfig(enabled=False),
            handoff_report=HandoffReportConfig(enabled=False),
        )
        fts_server = start_loopback_server(create_server_app(settings=fts_settings), startup_timeout=90)
        fts_search = asyncio.run(
            _search_topic_once(
                fts_server.base_url,
                token=_E4_TOKEN,
                scope_id=scope_id,
                query=E4_CANARY,
            )
        )
        fts_ref = ArtifactIdentity.from_mapping(cast(Mapping[str, object], fts_search["artifact"]))
        if fts_search["mode"] != "fts" or fts_ref != chain.exact_ref:
            raise ProductChainError("E4 seekDB FTS restart did not preserve the exact ref")
        fts_server.stop()
        fts_closed = fts_server.port_is_closed()
        fts_server = None
        if not fts_closed:
            raise ProductChainError("E4 FTS server port remained open")
        result = {
            "schema": "powercontext.topic-memory-r8.e4.v1",
            "status": "PASS",
            "environment": {
                "database": "real embedded seekDB in an isolated temporary path",
                "runtime_role": "all",
                "generation_and_embedding": "deterministic loopback provider",
            },
            "hybrid_chain": chain.as_dict(),
            "dialect_checks": {
                "hybrid_mode": "hybrid",
                "fts_mode_after_restart": "fts",
                "same_exact_ref": fts_ref.as_dict(),
            },
            "redaction": {
                "prompt_content_recorded": False,
                "provider_body_recorded": False,
                "credentials_recorded": False,
            },
        }
    finally:
        if fts_server is not None:
            fts_server.stop()
        if server is not None:
            server.stop()
        inference_server.stop()
        shutil.rmtree(runtime_directory)
        if temporary_openai_key:
            os.environ.pop("OPENAI_API_KEY", None)
    if result is None:
        raise ProductChainError("E4 did not produce a result")
    result["cleanup"] = {
        "hybrid_server_port_closed": True,
        "fts_server_port_closed": True,
        "fake_provider_port_closed": inference_server.port_is_closed(),
        "temporary_seekdb_removed": not runtime_directory.exists(),
    }
    cleanup = cast(dict[str, object], result["cleanup"])
    if not all(value is True for value in cleanup.values()):
        raise ProductChainError("E4 cleanup left a listener or temporary seekDB behind")
    _write_json(directory / "e4-report.json", result)
    return result


def _version(command: Sequence[str], *, environment: Mapping[str, str]) -> str:
    completed = _run(command, cwd=PROJECT_ROOT, env=environment, timeout=20)
    return completed.stdout.strip()


def _unavailable(layer: str, gap: str, *, checks: Sequence[str] = ()) -> dict[str, object]:
    return {
        "schema": f"powercontext.topic-memory-r8.{layer.casefold()}.v1",
        "status": "UNAVAILABLE",
        "gap": gap,
        "safe_checks_completed": list(checks),
        "passed": False,
    }


def _real_embedding_config(
    environment: Mapping[str, str],
) -> tuple[_RealEmbeddingConfig | None, dict[str, object] | None]:
    required = (
        "POWERCONTEXT_R8_EMBEDDING_MODEL",
        "POWERCONTEXT_R8_EMBEDDING_PROFILE_ID",
        "POWERCONTEXT_R8_EMBEDDING_DIMENSION",
    )
    missing = [name for name in required if not environment.get(name, "").strip()]
    if missing:
        return None, _unavailable(
            "E2",
            "missing explicit real embedding configuration: " + ", ".join(missing),
            checks=(
                "SQLite vector support is covered hermetically in E0",
                "no fake embedding result was promoted to real-provider PASS",
            ),
        )
    try:
        dimension = int(environment["POWERCONTEXT_R8_EMBEDDING_DIMENSION"])
        timeout_seconds = float(environment.get("POWERCONTEXT_R8_EMBEDDING_TIMEOUT", "30"))
    except ValueError as exc:
        raise ProductChainError("E2 embedding dimension and timeout must be numeric") from exc
    if dimension < 1 or timeout_seconds <= 0:
        raise ProductChainError("E2 embedding dimension and timeout must be positive")
    normalization_value = environment.get("POWERCONTEXT_R8_EMBEDDING_NORMALIZATION", "unit").strip()
    if normalization_value not in {"none", "unit"}:
        raise ProductChainError("E2 embedding normalization must be 'none' or 'unit'")
    base_url_value = environment.get("POWERCONTEXT_R8_EMBEDDING_BASE_URL", "").strip()
    headers_value = environment.get("POWERCONTEXT_R8_EMBEDDING_HEADERS_JSON", "").strip()
    headers: dict[str, SecretStr] = {}
    if headers_value:
        try:
            decoded_headers = json.loads(headers_value)
        except json.JSONDecodeError as exc:
            raise ProductChainError("E2 embedding headers JSON is invalid") from exc
        if not isinstance(decoded_headers, dict) or not all(
            isinstance(name, str) and isinstance(value, str) and name and value
            for name, value in decoded_headers.items()
        ):
            raise ProductChainError("E2 embedding headers must be a non-empty string mapping")
        headers = {str(name): SecretStr(str(value)) for name, value in decoded_headers.items()}
    return (
        _RealEmbeddingConfig(
            model=environment["POWERCONTEXT_R8_EMBEDDING_MODEL"].strip(),
            profile_id=environment["POWERCONTEXT_R8_EMBEDDING_PROFILE_ID"].strip(),
            dimension=dimension,
            base_url=None if not base_url_value else AnyHttpUrl(base_url_value),
            headers=headers,
            normalization=cast(Literal["none", "unit"], normalization_value),
            timeout_seconds=timeout_seconds,
        ),
        None,
    )


def _oceanbase_layer_config(
    environment: Mapping[str, str],
) -> tuple[_OceanBaseLayerConfig | None, dict[str, object] | None]:
    url = environment.get("POWERCONTEXT_R8_OCEANBASE_URL", "").strip()
    if not url:
        return None, _unavailable(
            "E3",
            "missing dedicated disposable OceanBase URL/schema: POWERCONTEXT_R8_OCEANBASE_URL",
            checks=("no shared/default OceanBase schema was touched",),
        )
    database_name = make_url(url).database
    if not database_name:
        raise ProductChainError("E3 OceanBase URL must select a dedicated database schema")
    return (
        _OceanBaseLayerConfig(
            database=OceanBaseConfig(url=SecretStr(url)),
            schema_fingerprint=digest_text(database_name),
        ),
        None,
    )


def _execute_environment_layers(
    *,
    requested: set[str],
    directory: Path,
    e1_status: str,
    generation_timeout: float,
    environment: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    layers: dict[str, dict[str, object]] = {}
    if "e2" not in requested:
        layers["E2"] = _unavailable("E2", "not requested in this invocation")
    elif e1_status == "UNAVAILABLE":
        layers["E2"] = _unavailable(
            "E2",
            "E1 prerequisite is UNAVAILABLE; E2 runner was not executed",
            checks=(
                "E1 is automatically requested with E2",
                "the embedding provider was not contacted without an E1 PASS",
            ),
        )
    elif e1_status != "PASS":
        raise ProductChainError(f"E2 requires E1 PASS; observed {e1_status}")
    else:
        embedding, unavailable = _real_embedding_config(environment)
        layers["E2"] = (
            cast(dict[str, object], unavailable)
            if embedding is None
            else run_e2(
                directory / "e2",
                config=embedding,
                e1_status=e1_status,
                generation_timeout=generation_timeout,
            )
        )

    if "e3" not in requested:
        layers["E3"] = _unavailable("E3", "not requested in this invocation")
    else:
        oceanbase, unavailable = _oceanbase_layer_config(environment)
        layers["E3"] = (
            cast(dict[str, object], unavailable)
            if oceanbase is None
            else run_e3(directory / "e3", config=oceanbase, generation_timeout=generation_timeout)
        )

    if "e4" not in requested:
        layers["E4"] = _unavailable("E4", "not requested in this invocation")
    elif environment.get("POWERCONTEXT_R8_SEEKDB_ENABLED") != "1":
        layers["E4"] = _unavailable(
            "E4",
            "missing explicit opt-in: POWERCONTEXT_R8_SEEKDB_ENABLED=1",
            checks=("no implicit local SeekDB state was touched",),
        )
    elif importlib.util.find_spec("pylibseekdb") is None:
        layers["E4"] = _unavailable(
            "E4",
            "POWERCONTEXT_R8_SEEKDB_ENABLED=1 but the pylibseekdb runtime is unavailable",
            checks=("no fake SeekDB result was promoted to PASS",),
        )
    else:
        layers["E4"] = run_e4(directory / "e4", generation_timeout=generation_timeout)
    return layers


def _requested_layers(raw: str) -> set[str]:
    requested = {value.strip().casefold() for value in raw.split(",") if value.strip()}
    unknown = requested - {"e0", "e1", "e2", "e3", "e4"}
    if unknown:
        raise ProductChainError("unknown R8 acceptance layers: " + ", ".join(sorted(unknown)))
    requested.add("e0")
    if "e2" in requested:
        requested.add("e1")
    return requested


def main() -> int:
    args = _parse_args()
    requested = _requested_layers(args.layers)
    args.output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "schema": "powercontext.topic-memory-r8.acceptance.v1",
        "baseline": "d5d7fa2acaa0e72a5122140b58856e18172d2e8d",
        "head": _version(("git", "rev-parse", "HEAD"), environment=os.environ),
        "requested_layers": sorted(requested),
        "layers": {},
        "limits": {
            "codex_timeout_seconds": args.codex_timeout,
            "generation_timeout_seconds": args.generation_timeout,
            "generation_max_requests_per_inference": 1,
            "polling_is_deadline_bounded": True,
        },
    }
    layers = report["layers"]
    assert isinstance(layers, dict)
    try:
        layers["E0"] = run_e0(args.output / "e0")
        if "e1" in requested:
            layers["E1"] = run_e1(
                args.output / "e1",
                codex_model=args.codex_model,
                generation_model=args.generation_model,
                codex_timeout=args.codex_timeout,
                generation_timeout=args.generation_timeout,
            )
        else:
            layers["E1"] = _unavailable("E1", "not requested in this invocation")
        e1_status = str(layers["E1"].get("status", "FAIL"))
        layers.update(
            _execute_environment_layers(
                requested=requested,
                directory=args.output,
                e1_status=e1_status,
                generation_timeout=args.generation_timeout,
                environment=os.environ,
            )
        )
        layer_statuses = [cast(dict[str, object], layers[name.upper()]).get("status") for name in sorted(requested)]
        if "FAIL" in layer_statuses:
            report["status"] = "FAIL"
        elif "UNAVAILABLE" in layer_statuses:
            report["status"] = "PARTIAL"
        else:
            report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        _write_json(args.output / "r8-report.json", report)
        raise
    _write_json(args.output / "r8-report.json", report)
    print(json.dumps({"status": report["status"], "report": str(args.output / "r8-report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
