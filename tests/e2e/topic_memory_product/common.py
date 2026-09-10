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

"""Shared, content-redacted support for the R8 Topic Memory product chain."""

# ruff: noqa: TRY003

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import AnyHttpUrl, SecretStr
from starlette.middleware import Middleware

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    CaptureContentSourceRequest,
    FlushTopicMemoryRequest,
    GetTopicMemoryRequest,
    PrepareContextRequest,
    SearchTopicMemoryRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    BearerAuthConfig,
    McpConfig,
    ServerSettings,
)

E0_CANARY = "ZIRCON-R8-HERMETIC-CANARY"
E0_DETAIL_MARKER = "R8-HERMETIC-DETAIL-MUST-NOT-BE-PREPARED"
E0_SOURCE_ID = "r8-hermetic-source"
_E0_TOKEN = "r8-hermetic-one-time-token"  # noqa: S105 - synthetic loopback-only credential.


class ProductChainError(RuntimeError):
    """An observable R8 acceptance invariant failed."""


def require_no_worker_failures(layer: str, failures: list[dict[str, object]]) -> None:
    """Prevent a recovered background-worker error from being reported as PASS."""

    if failures:
        raise ProductChainError(f"{layer} captured background-worker failures: {failures}")


class _WorkerFailureCapture(logging.Handler):
    """Capture only content-free worker failure classifications."""

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


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    family: str
    artifact_id: str
    revision: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ArtifactIdentity:
        family = value.get("family")
        artifact_id = value.get("artifact_id")
        revision = value.get("revision")
        if not isinstance(family, str) or not isinstance(artifact_id, str) or not isinstance(revision, int):
            raise ProductChainError("invalid exact ArtifactRef in product-chain response")
        return cls(family=family, artifact_id=artifact_id, revision=revision)

    def as_dict(self) -> dict[str, object]:
        return {"family": self.family, "artifact_id": self.artifact_id, "revision": self.revision}

    def display(self) -> str:
        return f"{self.family}:{self.artifact_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class ChainEvidence:
    exact_ref: ArtifactIdentity
    source_ref: str
    flush_status: str
    flush_duration_seconds: float
    returned_before_generation_completed: bool
    search_mode: str
    prepared_context_bytes: int
    mcp_tools: tuple[str, ...]
    access_timeline: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "exact_ref": self.exact_ref.as_dict(),
            "source_ref": self.source_ref,
            "flush": {
                "status": self.flush_status,
                "duration_seconds": self.flush_duration_seconds,
                "returned_before_generation_completed": self.returned_before_generation_completed,
            },
            "search": {"mode": self.search_mode, "exact_ref": self.exact_ref.as_dict()},
            "prepared_context": {
                "content_bytes": self.prepared_context_bytes,
                "exact_ref": self.exact_ref.as_dict(),
                "full_detail_absent": True,
            },
            "mcp": {"tools": list(self.mcp_tools), "exact_ref": self.exact_ref.as_dict()},
            "access_timeline": list(self.access_timeline),
        }


@dataclass(slots=True)
class AccessTimeline:
    """Record only method, route, status, and elapsed time; never bodies or headers."""

    started: float = field(default_factory=time.monotonic)
    entries: list[dict[str, object]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, *, method: str, path: str, status: int, started: float) -> None:
        with self._lock:
            self.entries.append({
                "sequence": len(self.entries) + 1,
                "offset_seconds": round(started - self.started, 3),
                "duration_seconds": round(time.monotonic() - started, 3),
                "method": method,
                "path": path,
                "status": status,
            })

    def snapshot(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(entry) for entry in self.entries)


class AccessTimelineMiddleware:
    """ASGI middleware for a bounded, content-free acceptance timeline."""

    def __init__(self, app: Any, *, timeline: AccessTimeline) -> None:
        self.app = app
        self.timeline = timeline

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500

        async def observe(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            self.timeline.append(
                method=str(scope.get("method", "")),
                path=str(scope.get("path", "")),
                status=status,
                started=started,
            )


@dataclass(slots=True)
class PreparedContextAudit:
    """Retain structural Prepared Context facts without prompts or generated content."""

    entries: list[dict[str, object]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, *, request_body: bytes, response_body: bytes, status: int) -> None:
        observation: dict[str, object] = {"status": status}
        try:
            request_payload = json.loads(request_body)
            response_payload = json.loads(response_body)
            query = request_payload.get("query")
            content = response_payload.get("content")
            if not isinstance(query, str) or not isinstance(content, str):
                observation["structural_parse"] = "FAILED"
                with self._lock:
                    self.entries.append(observation)
                return
            envelope = _prepared_envelope(content)
            topic_refs: list[dict[str, object]] = []
            full_detail_absent = True
            for item in envelope.get("items", []):
                if not isinstance(item, dict) or item.get("kind") != "topic-memory":
                    continue
                citation = item.get("citation")
                if isinstance(citation, dict) and isinstance(citation.get("artifact_ref"), dict):
                    topic_refs.append(ArtifactIdentity.from_mapping(citation["artifact_ref"]).as_dict())
                compact_content = item.get("content")
                if not isinstance(compact_content, str):
                    full_detail_absent = False
                    continue
                try:
                    compact_payload = json.loads(compact_content)
                except json.JSONDecodeError:
                    full_detail_absent = False
                    continue
                if not isinstance(compact_payload, dict) or "detail" in compact_payload:
                    full_detail_absent = False
            observation.update({
                "query_sha256": digest_text(query),
                "content_bytes": response_payload.get("content_bytes"),
                "topic_refs": topic_refs,
                "full_detail_absent": full_detail_absent,
            })
        except (json.JSONDecodeError, ProductChainError, TypeError, ValueError):
            observation["structural_parse"] = "FAILED"
        with self._lock:
            self.entries.append(observation)

    def for_query(self, query: str) -> tuple[dict[str, object], ...]:
        query_sha256 = digest_text(query)
        with self._lock:
            return tuple(dict(entry) for entry in self.entries if entry.get("query_sha256") == query_sha256)


class PreparedContextAuditMiddleware:
    """Observe only redacted structural facts from prepare responses."""

    def __init__(self, app: Any, *, audit: PreparedContextAudit) -> None:
        self.app = app
        self.audit = audit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/v1/context/prepare":
            await self.app(scope, receive, send)
            return
        request_chunks: list[bytes] = []
        response_chunks: list[bytes] = []
        status = 500

        async def observe_receive() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    request_chunks.append(body)
            return message

        async def observe_send(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
            elif message.get("type") == "http.response.body":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    response_chunks.append(body)
            await send(message)

        try:
            await self.app(scope, observe_receive, observe_send)
        finally:
            self.audit.append(
                request_body=b"".join(request_chunks),
                response_body=b"".join(response_chunks),
                status=status,
            )


@dataclass(slots=True)
class RunningServer:
    app: FastAPI
    server: uvicorn.Server
    thread: threading.Thread
    listener: socket.socket
    base_url: str
    port: int

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)
        self.listener.close()
        if self.thread.is_alive():
            raise ProductChainError("loopback server did not stop")

    def port_is_closed(self) -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.2)
        try:
            return probe.connect_ex(("127.0.0.1", self.port)) != 0
        finally:
            probe.close()


@dataclass(slots=True)
class FakeInference:
    """OpenAI-compatible loopback fake with deterministic Topic and embedding output."""

    canary: str = E0_CANARY
    detail_marker: str = E0_DETAIL_MARKER
    generation_delay_seconds: float = 0.2
    calls: list[dict[str, object]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _topic_generation_started: threading.Event = field(default_factory=threading.Event)
    _topic_generation_finished: threading.Event = field(default_factory=threading.Event)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> dict[str, object]:
            payload = await request.json()
            rendered = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            started = time.monotonic()
            stage = "readiness"
            if "Identify up to 20 durable topic probes" in rendered:
                stage = "topic_probe"
                content = json.dumps({
                    "probes": [
                        {
                            "query": self.canary,
                            "keywords": ["zircon", "hermetic", "canary"],
                            "evidence_ids": ["evidence-0001"],
                        }
                    ]
                })
            elif "Evolve the whole Window into at most 20 topics" in rendered:
                stage = "topic_global"
                content = json.dumps({
                    "ambiguous": False,
                    "proposals": [
                        {
                            "content": {
                                "title": "Hermetic R8 release sentinel",
                                "summary": f"The durable release sentinel is {self.canary}.",
                                "detail": (
                                    "# Hermetic R8 release sentinel\n\n"
                                    f"Use {self.canary} when verifying the synthetic release chain.\n\n"
                                    + ("This is bounded synthetic filler for snippet truncation. " * 120)
                                    + "\n\n"
                                    f"{self.detail_marker}"
                                ),
                            },
                            "evidence_ids": ["evidence-0001"],
                        }
                    ],
                })
            else:
                content = "READY"
            if stage == "topic_global":
                self._topic_generation_started.set()
            if stage != "readiness":
                await asyncio.sleep(self.generation_delay_seconds)
            finished = time.monotonic()
            with self._lock:
                self.calls.append({"stage": stage, "started": started, "finished": finished})
            if stage == "topic_global":
                self._topic_generation_finished.set()
            return {
                "id": f"r8-{stage}",
                "object": "chat.completion",
                "created": 0,
                "model": "r8-fake-generation",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        @app.post("/v1/embeddings")
        async def embeddings(request: Request) -> dict[str, object]:
            payload = await request.json()
            values = payload.get("input", [])
            inputs = [values] if isinstance(values, str) else list(values)
            with self._lock:
                self.calls.append({
                    "stage": "embedding",
                    "started": time.monotonic(),
                    "finished": time.monotonic(),
                    "count": len(inputs),
                })
            return {
                "object": "list",
                "model": "r8-fake-embedding",
                "data": [
                    {"object": "embedding", "index": index, "embedding": [1.0, 0.0, 0.0, 0.0]}
                    for index in range(len(inputs))
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            }

        return app

    def topic_generation_completed_after(self, timestamp: float) -> bool:
        with self._lock:
            global_completions = [
                float(finished)
                for call in self.calls
                if call.get("stage") == "topic_global" and isinstance((finished := call.get("finished")), int | float)
            ]
        return bool(global_completions) and min(global_completions) > timestamp

    def wait_for_topic_generation(self, timeout: float) -> bool:
        return self._topic_generation_finished.wait(timeout)

    def wait_for_topic_generation_started(self, timeout: float) -> bool:
        return self._topic_generation_started.wait(timeout)

    def redacted_calls(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(
                {key: value for key, value in call.items() if key not in {"started", "finished"}} for call in self.calls
            )


def start_loopback_server(app: FastAPI, *, startup_timeout: float = 30.0) -> RunningServer:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="on", log_level="critical", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + startup_timeout
    while thread.is_alive() and not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise ProductChainError("loopback server did not become ready")
    return RunningServer(
        app=app,
        server=server,
        thread=thread,
        listener=listener,
        base_url=f"http://127.0.0.1:{port}",
        port=port,
    )


async def default_scope_id(base_url: str, *, token: str | None) -> str:
    async with PowerContextClient(base_url, token=token, timeout=10) as client:
        return (await client.get_default_scope()).scope_id


async def exercise_http_mcp_prepared_chain(  # noqa: C901
    *,
    base_url: str,
    token: str,
    scope_id: str,
    query: str,
    source_id: str | None,
    source_content: str | None,
    expected_detail_marker: str | None,
    timeline: AccessTimeline,
    generation: FakeInference | None = None,
    search_timeout_seconds: float = 60.0,
) -> ChainEvidence:
    """Exercise one exact ref through public HTTP, SDK, MCP, and Prepared Context."""

    authorization = f"Bearer {token}"
    async with PowerContextClient(base_url, token=token, timeout=10) as client:
        if source_id is not None and source_content is not None:
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id=source_id,
                    content=source_content,
                    metadata={"origin": "r8-hermetic", "synthetic": True},
                )
            )
        flush_started = time.monotonic()
        flushed = await client.flush_topic_memory(FlushTopicMemoryRequest(scope_id=scope_id))
        flush_returned = time.monotonic()
        if flushed.status != "accepted":
            raise ProductChainError(f"Topic Memory flush was not accepted: {flushed.status}")

        search = None
        observed_empty_after_flush = False
        deadline = time.monotonic() + search_timeout_seconds
        if generation is not None:
            # Avoid turning deterministic readiness polling into a stream of
            # concurrent embedding reads while SQLite publishes the topic.
            await asyncio.to_thread(generation.wait_for_topic_generation, max(0.0, deadline - time.monotonic()))
            await asyncio.sleep(0.5)
        while time.monotonic() < deadline:
            search = await client.search_topic_memory(SearchTopicMemoryRequest(scope_id=scope_id, query=query, limit=8))
            if search.hits:
                break
            observed_empty_after_flush = True
            await asyncio.sleep(0.2)
        if search is None or not search.hits:
            raise ProductChainError("background Topic Memory generation did not publish a searchable topic")
        exact_ref = ArtifactIdentity.from_mapping(search.hits[0].artifact.model_dump(mode="json"))
        if exact_ref.family != "topic-memory":
            raise ProductChainError("search returned a non-Topic exact ref")
        exact = await client.get_topic_memory(
            GetTopicMemoryRequest(scope_id=scope_id, artifact=search.hits[0].artifact)
        )
        if ArtifactIdentity.from_mapping(exact.artifact.model_dump(mode="json")) != exact_ref:
            raise ProductChainError("exact get changed the search ArtifactRef")
        if not exact.source_refs:
            raise ProductChainError("exact Topic response omitted direct SourceRefs")
        source_ref = f"{exact.source_refs[0].name}:{exact.source_refs[0].source_id}"
        if source_id is not None and exact.source_refs[0].source_id != source_id:
            raise ProductChainError("exact Topic response changed the captured SourceRef")

        prepared = await client.prepare_context(PrepareContextRequest(scope_id=scope_id, query=query, max_bytes=8000))
        if prepared.content is None:
            raise ProductChainError("Prepared Context omitted the generated Topic")
        prepared_envelope = _prepared_envelope(prepared.content)
        prepared_item = next(
            (
                item
                for item in prepared_envelope.get("items", [])
                if isinstance(item, dict) and item.get("kind") == "topic-memory"
            ),
            None,
        )
        if prepared_item is None:
            raise ProductChainError("Prepared Context contained no compact Topic item")
        citation = prepared_item.get("citation")
        if not isinstance(citation, dict) or not isinstance(citation.get("artifact_ref"), dict):
            raise ProductChainError("Prepared Context Topic citation is invalid")
        if ArtifactIdentity.from_mapping(citation["artifact_ref"]) != exact_ref:
            raise ProductChainError("Prepared Context changed the exact Topic ref")
        compact_content = prepared_item.get("content")
        if not isinstance(compact_content, str):
            raise ProductChainError("Prepared Context Topic item omitted compact content")
        try:
            compact_payload = json.loads(compact_content)
        except json.JSONDecodeError as exc:
            raise ProductChainError("Prepared Context Topic content was not compact JSON") from exc
        if not isinstance(compact_payload, dict) or "detail" in compact_payload:
            raise ProductChainError("Prepared Context included the full Topic detail field")
        if expected_detail_marker is not None and expected_detail_marker in prepared.content:
            raise ProductChainError("Prepared Context leaked full Topic detail")

        transport = StreamableHttpTransport(
            f"{base_url}/mcp",
            headers={"Authorization": authorization},
        )
        async with Client(transport) as mcp:
            mcp_search = await mcp.call_tool(
                "search_topic_memory",
                {"scope_id": scope_id, "query": query, "limit": 8},
            )
            search_payload = mcp_search.structured_content or {}
            mcp_hits = search_payload.get("hits")
            if not isinstance(mcp_hits, list) or not mcp_hits or not isinstance(mcp_hits[0], dict):
                raise ProductChainError("MCP Topic search returned no structured hits")
            mcp_ref_value = mcp_hits[0].get("artifact")
            if not isinstance(mcp_ref_value, dict) or ArtifactIdentity.from_mapping(mcp_ref_value) != exact_ref:
                raise ProductChainError("MCP search changed the exact Topic ref")
            mcp_get = await mcp.call_tool(
                "get_topic_memory",
                {"scope_id": scope_id, "artifact": exact_ref.as_dict()},
            )
            get_payload = mcp_get.structured_content or {}
            get_ref_value = get_payload.get("artifact")
            if not isinstance(get_ref_value, dict) or ArtifactIdentity.from_mapping(get_ref_value) != exact_ref:
                raise ProductChainError("MCP exact get changed the search ref")

    returned_before_generation_completed = (
        generation.topic_generation_completed_after(flush_returned)
        if generation is not None
        else observed_empty_after_flush
    )
    if not returned_before_generation_completed:
        raise ProductChainError("flush did not demonstrably return before background generation completed")
    return ChainEvidence(
        exact_ref=exact_ref,
        source_ref=source_ref,
        flush_status=str(flushed.status),
        flush_duration_seconds=round(flush_returned - flush_started, 3),
        returned_before_generation_completed=returned_before_generation_completed,
        search_mode=str(search.mode),
        prepared_context_bytes=prepared.content_bytes,
        mcp_tools=("search_topic_memory", "get_topic_memory"),
        access_timeline=timeline.snapshot(),
    )


def run_e0(directory: Path) -> dict[str, object]:
    """Run the mandatory zero-egress deterministic product chain and write sanitized evidence."""

    directory.mkdir(parents=True, exist_ok=True)
    runtime_directory = Path(tempfile.mkdtemp(prefix=".runtime-", dir=directory))
    fake = FakeInference()
    inference_server = start_loopback_server(fake.app())
    timeline = AccessTimeline()
    powercontext_server: RunningServer | None = None
    failure_capture = _WorkerFailureCapture()
    worker_logger = logging.getLogger("powercontext.builtin.runtime.artifact_processing")
    worker_logger.addHandler(failure_capture)
    result: dict[str, object] | None = None
    try:
        secret_header = {"Authorization": SecretStr("Bearer r8-fake-provider")}
        settings = ServerSettings(
            auth=BearerAuthConfig(enabled=True, token=SecretStr(_E0_TOKEN)),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{runtime_directory / 'runtime.db'}"),
            runtime=RuntimeConfig(
                topic_memory_source_window_limit=1,
                artifact_processing_worker_timeout_seconds=20,
            ),
            inference=InferenceConfig(
                generation_model="openai-chat:r8-fake-generation",
                generation_base_url=AnyHttpUrl(f"{inference_server.base_url}/v1"),
                generation_headers=secret_header,
                generation_timeout_seconds=5,
                generation_max_requests=1,
                generation_model_settings={"max_tokens": 1024},
                embedding_model="openai:r8-fake-embedding",
                embedding_base_url=AnyHttpUrl(f"{inference_server.base_url}/v1"),
                embedding_headers=secret_header,
                embedding_profile_id="r8-fake-embedding-4-unit",
                embedding_dimension=4,
                embedding_timeout_seconds=5,
            ),
            mcp=McpConfig(enabled=True),
        )
        app = create_server_app(
            settings=settings,
            scheduler_path=runtime_directory / "scheduler.db",
            middleware=(Middleware(AccessTimelineMiddleware, timeline=timeline),),
        )
        powercontext_server = start_loopback_server(app, startup_timeout=60)
        scope_id = asyncio.run(default_scope_id(powercontext_server.base_url, token=_E0_TOKEN))
        try:
            evidence = asyncio.run(
                exercise_http_mcp_prepared_chain(
                    base_url=powercontext_server.base_url,
                    token=_E0_TOKEN,
                    scope_id=scope_id,
                    query=E0_CANARY,
                    source_id=E0_SOURCE_ID,
                    source_content=(
                        f"Synthetic durable release decision: use {E0_CANARY} for the hermetic acceptance chain."
                    ),
                    expected_detail_marker=E0_DETAIL_MARKER,
                    timeline=timeline,
                    generation=fake,
                )
            )
        except ProductChainError as exc:
            raise ProductChainError(
                f"{exc}; redacted_worker_failures={failure_capture.failures[-3:]}; "
                f"redacted_fake_calls={fake.redacted_calls()[-20:]}"
            ) from exc
        require_no_worker_failures("E0", failure_capture.failures)
        result = {
            "schema": "powercontext.topic-memory-r8.e0.v1",
            "status": "PASS",
            "environment": {
                "database": "temporary file SQLite",
                "generation": "deterministic OpenAI-compatible loopback fake",
                "embedding": {
                    "provider": "deterministic OpenAI-compatible loopback fake",
                    "profile": "r8-fake-embedding-4-unit",
                    "dimension": 4,
                    "sqlite_vector_extension": "loaded by the production runtime",
                },
                "network": "loopback only; no external provider configured",
                "authentication": "synthetic one-time bearer",
            },
            "chain": evidence.as_dict(),
            "fake_inference_calls": list(fake.redacted_calls()),
            "worker_failures": list(failure_capture.failures),
            "redaction": {
                "prompt_content_recorded": False,
                "model_output_recorded": False,
                "credentials_recorded": False,
                "provider_body_recorded": False,
            },
        }
    finally:
        worker_logger.removeHandler(failure_capture)
        cleanup: dict[str, object] = {}
        if powercontext_server is not None:
            powercontext_server.stop()
            cleanup["powercontext_port_closed"] = powercontext_server.port_is_closed()
        inference_server.stop()
        cleanup["fake_provider_port_closed"] = inference_server.port_is_closed()
        shutil.rmtree(runtime_directory)
        cleanup["temporary_runtime_removed"] = not runtime_directory.exists()
        _write_json(directory / "e0-cleanup.json", cleanup)
        if cleanup and not all(value is True for value in cleanup.values()):
            raise ProductChainError("E0 cleanup left a loopback listener or temporary runtime behind")
    if result is None:
        raise ProductChainError("E0 did not produce a result")
    result["cleanup"] = cleanup
    _write_json(directory / "e0-report.json", result)
    return result


def _prepared_envelope(content: str) -> dict[str, Any]:
    for line in content.splitlines():
        if not line.startswith("{"):
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(decoded, dict)
            and decoded.get("trust") == "untrusted_history"
            and isinstance(decoded.get("items"), list)
        ):
            return decoded
    raise ProductChainError("Prepared Context envelope was not valid JSON")


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(f"{json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)}\n", encoding="utf-8")


__all__ = [
    "AccessTimeline",
    "AccessTimelineMiddleware",
    "ArtifactIdentity",
    "ChainEvidence",
    "PreparedContextAudit",
    "PreparedContextAuditMiddleware",
    "ProductChainError",
    "RunningServer",
    "default_scope_id",
    "digest_text",
    "exercise_http_mcp_prepared_chain",
    "require_no_worker_failures",
    "run_e0",
    "start_loopback_server",
]
