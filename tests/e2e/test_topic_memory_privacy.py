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

"""Spawned Topic workers enforce deployment privacy before I/O and publication."""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    InferenceConfig,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

_FORBIDDEN = "synthetic-topic-forbidden-value"
_MODEL = "topic-privacy-test"


class _Server(ThreadingHTTPServer):
    requests: list[bytes]
    response_id: str


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_POST(self) -> None:
        self.server.requests.append(self.rfile.read(int(self.headers["Content-Length"])))
        payload = json.dumps({
            "id": self.server.response_id,
            "object": "chat.completion",
            "created": 0,
            "model": _MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"probes":[]}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - standard-library signature
        pass


@pytest.mark.parametrize("phase", ["before_send", "before_release"])
def test_topic_worker_enforces_privacy_and_preserves_source_for_retry(tmp_path, monkeypatch, phase):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-topic-key")
    server = _Server(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.response_id = _FORBIDDEN if phase == "before_release" else "topic-response"
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    async def scenario():
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        protection = InferenceProtectionConfig(
            mode="enforcing",
            forbidden_values=(SecretStr(_FORBIDDEN),),
            targets={
                "topic": UpstreamProfile(
                    endpoint=base_url + "/chat/completions",
                    model=_MODEL,
                    protocol="openai-chat",
                    context_window_tokens=125000,
                    max_output_tokens=1024,
                    validated_utf8_token_bound=True,
                )
            },
        )
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic.db'}"),
            inference=InferenceConfig(
                generation_model="openai-chat:" + _MODEL,
                generation_base_url=AnyHttpUrl(base_url),
                generation_model_settings={"max_tokens": 1024},
                protection=protection,
            ),
            runtime=RuntimeConfig(
                artifact_processing_families=("topic-memory",),
                topic_memory_max_workers=1,
                profile_schedule_enabled=False,
                dream_enabled=False,
            ),
        )
        content = _FORBIDDEN if phase == "before_send" else "Safe Topic evidence."
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Topic privacy", summary="Worker boundary", idempotency_key="topic-privacy")
            )
            receipt = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="topic-evidence", content=content, metadata={})
            )
            topic = runtime.topic_memory.for_scope(scope.scope_id)
            await topic.flush()
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            async with asyncio.timeout(60):
                while True:
                    state = supervisor.family_status["topic-memory"]
                    if state["failed"] or state["completed"]:
                        break
                    await asyncio.sleep(0.05)
            assert int(state["failed"]) > 0 and state["completed"] == 0
            assert (not server.requests) if phase == "before_send" else bool(server.requests)
            status = await topic.processing_status()
            assert status.source_cursor == 0 and status.source_head == receipt.sequence
            assert not await topic.browse(limit=10)
            source = await runtime.records.for_scope(scope.scope_id).get_source(
                receipt.source_ref.source_type,
                receipt.source_ref.source_id,
            )
            assert source.content == content

        # An operator-approved policy change can resume the same durable work.
        server.response_id = "topic-response"
        permitted = config.model_copy(
            update={
                "inference": config.inference.model_copy(
                    update={
                        "protection": protection.model_copy(update={"forbidden_values": ()}),
                    }
                )
            }
        )
        async with open_builtin_runtime(permitted) as runtime:
            topic = runtime.topic_memory.for_scope(scope.scope_id)
            async with asyncio.timeout(60):
                while (await topic.processing_status()).source_cursor < receipt.sequence:  # noqa: ASYNC110 - durable child state
                    await asyncio.sleep(0.05)
            assert server.requests
            assert not await topic.browse(limit=10)

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
