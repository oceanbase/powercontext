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

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import AnyHttpUrl

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.inference.pydantic_ai import StreamingAggregateModel
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingWorkerCompletion
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model, open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment as ScopeAssignment
from powercontext.builtin.runtime.topic_memory_processing import TopicMemoryWorkerSpec, _open_topic_memory_processor
from powercontext.builtin.sources import ContentCapture

_EVENT = "inference.generation.streaming_not_applied"


class _RecordingStreamServer(ThreadingHTTPServer):
    requests: list[dict[str, Any]]

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _StreamHandler)
        self.requests = []


class _StreamHandler(BaseHTTPRequestHandler):
    server: _RecordingStreamServer

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(content_length))
        self.server.requests.append(body)
        chunks = [
            {"delta": {"role": "assistant"}, "finish_reason": None},
            {"delta": {"content": '{"probes": []}'}, "finish_reason": None},
            {"delta": {}, "finish_reason": "stop"},
        ]
        payload = (
            b"".join(
                b"data: "
                + json.dumps({
                    "id": "chatcmpl-worker-stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body["model"],
                    "choices": [{"index": 0, **chunk}],
                }).encode()
                + b"\n\n"
                for chunk in chunks
            )
            + b"data: [DONE]\n\n"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def test_topic_memory_worker_sends_streamed_requests_when_auto_is_configured(tmp_path) -> None:
    server = _RecordingStreamServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic-worker-streaming.db'}"),
            inference=InferenceConfig(
                generation_model="openai-chat:qwen",
                generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{server.server_address[1]}/v1"),
                generation_streaming="auto",
            ),
        )

        async def scenario() -> None:
            # The Runtime initializes storage; the worker then reopens the same
            # database file, exactly like the spawned Topic Memory worker does.
            async with open_builtin_contexts(config) as contexts:
                scope = await contexts.get("scope-a")
                await scope.sources.capture(ContentCapture(source_id="worker-stream", content="streaming evidence"))
                async with contexts.database.transaction() as connection:
                    term = await contexts.repositories.processing_leases.start_single_process_term(connection, "holder")
                    await ArtifactProcessingIntentRepository().request(
                        connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                    )
                assignment = ScopeAssignment(
                    binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                    scope_id="scope-a",
                    artifact_family="topic-memory",
                    claimed_request_generation=1,
                    fence=term.fence("single-process"),
                    worker_id="worker-a",
                )
                async with _open_topic_memory_processor(TopicMemoryWorkerSpec(config=config), "scope-a") as processor:
                    # The scripted probe response yields zero probes, which ends the
                    # window successfully after one real request through the
                    # reconstructed worker model.
                    completion = await processor.process(assignment)
                    assert completion == ArtifactProcessingWorkerCompletion()

        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert len(server.requests) == 1
    assert server.requests[0]["stream"] is True


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == _EVENT]


def test_auto_streaming_warns_when_the_provider_prefix_is_not_supported(caplog) -> None:
    async def scenario() -> object:
        async with AsyncExitStack() as resources:
            _provider, model = await _open_pydantic_ai_model(
                "test",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
                streaming="auto",
            )
            return model

    with caplog.at_level(logging.WARNING, logger="powercontext.builtin.runtime.composition"):
        model = asyncio.run(scenario())

    assert not isinstance(model, StreamingAggregateModel)
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert getattr(warnings[0], "operation", None) == "generate"
    assert getattr(warnings[0], "unit", None) == "inference"


def test_disabled_streaming_does_not_warn_for_an_unsupported_prefix(caplog) -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            await _open_pydantic_ai_model(
                "test",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
                streaming="disabled",
            )

    with caplog.at_level(logging.WARNING, logger="powercontext.builtin.runtime.composition"):
        asyncio.run(scenario())

    assert _warnings(caplog) == []
