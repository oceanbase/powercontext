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

"""Real HTTP replaces race a spawned Topic Worker; the model endpoint is a local stub."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Callable, Coroutine
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
from pydantic import AnyHttpUrl

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.topic_memory_budget import MAX_TOPIC_MEMORY_WORK_ATTEMPTS
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    CaptureContentSourceRequest,
    FlushTopicMemoryRequest,
    TopicMemoryProcessingStatusRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

_REPLACES = MAX_TOPIC_MEMORY_WORK_ATTEMPTS + 1
_WORKER_CONTENT = {"title": "zircon worker", "summary": "zircon worker summary", "detail": "# zircon\nWorker update."}


def _manual_content(number: int) -> dict[str, str]:
    return {"title": f"zircon manual {number}", "summary": "zircon manual", "detail": f"# zircon\nManual {number}."}


class _Provider(ThreadingHTTPServer):
    """Answers the probe and global stages; a manual replace lands during the first global answers."""

    loop: asyncio.AbstractEventLoop
    replace: Callable[[int], Coroutine[Any, Any, None]]
    global_calls = 0


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        provider = self.server
        assert isinstance(provider, _Provider)
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = json.dumps(request["messages"])
        # Lineage-only system Sources from the manual writes share the journal, so cite what was supplied.
        evidence = sorted(set(re.findall(r"evidence-\d{4}", prompt)))
        if "Evolve the whole Window" in prompt:
            provider.global_calls += 1
            if provider.global_calls <= _REPLACES:
                # The Worker already read the head; this replace makes its publication stale.
                asyncio.run_coroutine_threadsafe(provider.replace(provider.global_calls), provider.loop).result(30)
            output = {
                "ambiguous": False,
                "proposals": [{"candidate_id": "candidate-0001", "content": _WORKER_CONTENT, "evidence_ids": evidence}],
            }
        else:
            output = {"probes": [{"query": "zircon", "evidence_ids": evidence}]}
        arguments = json.dumps(output)
        message = {"role": "assistant", "content": arguments}
        finish_reason = "stop"
        if request.get("tools"):
            name = request["tools"][0]["function"]["name"]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "result", "type": "function", "function": {"name": name, "arguments": arguments}}
                ],
            }
            finish_reason = "tool_calls"
        body = json.dumps({
            "id": "result",
            "object": "chat.completion",
            "created": 0,
            "model": request["model"],
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override
        pass


def test_http_replaces_during_generation_never_block_the_spawned_topic_worker(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="powercontext.builtin.runtime.artifact_processing")
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-provider")
    provider = _Provider(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'head-conflict.db'}"),
                inference=InferenceConfig(
                    generation_model="openai-chat:head-conflict",
                    generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{provider.server_port}/v1"),
                ),
                runtime=RuntimeConfig(artifact_processing_families=("topic-memory",)),
                mcp=McpConfig(enabled=False),
            ),
            scheduler_path=tmp_path / "scheduler.db",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
        ):
            client = PowerContextClient("http://testserver", http_client=http, trust_transport_security=True)
            scope = (await client.get_default_scope()).scope_id
            created = await http.post(
                f"/v1/scopes/{scope}/artifacts", json={"family": "topic-memory", "content": _manual_content(0)}
            )
            assert created.status_code == 201, created.text
            path = created.headers["Location"]

            async def replace(number):
                current = await http.get(path)
                replaced = await http.put(
                    path, headers={"If-Match": current.headers["ETag"]}, json={"content": _manual_content(number)}
                )
                assert replaced.status_code == 200, replaced.text

            provider.loop = asyncio.get_running_loop()
            provider.replace = replace
            await client.capture_content_source(
                CaptureContentSourceRequest(scope_id=scope, source_id="update", content="zircon changed")
            )
            await client.flush_topic_memory(FlushTopicMemoryRequest(scope_id=scope))
            async with asyncio.timeout(120):
                while (await http.get(path)).json()["content"] != _WORKER_CONTENT:
                    status = await client.get_topic_memory_processing_status(
                        TopicMemoryProcessingStatusRequest(scope_id=scope)
                    )
                    assert status.block is None
                    await asyncio.sleep(0.05)

            # The Worker revised the head written by the last manual replace.
            revisions = (await http.get(path + "/revisions")).json()["items"]
            assert len(revisions) == _REPLACES + 2
            assert provider.global_calls == _REPLACES + 1
            status = await client.get_topic_memory_processing_status(TopicMemoryProcessingStatusRequest(scope_id=scope))
            assert status.block is None
            # The captured Source is consumed; later Sources are the manual writes' lineage-only records.
            assert status.source_cursor >= 2

        conflicts = [
            record
            for record in caplog.records
            if getattr(record, "event", None) == "artifact_processing.attempt.completed"
            and getattr(record, "outcome", None) == "conflict"
        ]
        assert [getattr(record, "error_code", None) for record in conflicts] == ["head_conflict"] * _REPLACES
        assert not [r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.blocked"]

    try:
        asyncio.run(scenario())
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
