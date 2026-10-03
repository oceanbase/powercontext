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

"""Real HTTP, SQLite and spawned Topic Worker; the model endpoint is a local stub."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import httpx
from pydantic import AnyHttpUrl
from sqlalchemy import insert, select, update

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING as BINDING
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BUDGETS_TABLE as BUDGETS
from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http import CaptureContentSourceRequest, FlushTopicMemoryRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class _Provider(ThreadingHTTPServer):
    calls = 0


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        cast(_Provider, self.server).calls += 1
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        message = {"role": "assistant", "content": '{"probes":[]}'}
        finish_reason = "stop"
        if request.get("tools"):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "probe-result",
                        "type": "function",
                        "function": {"name": request["tools"][0]["function"]["name"], "arguments": '{"probes":[]}'},
                    }
                ],
            }
            finish_reason = "tool_calls"
        body = json.dumps({
            "id": "probe-result",
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


async def _wait(predicate):
    async with asyncio.timeout(60):
        while not predicate():  # noqa: ASYNC110 - bounded observation of real Worker exits
            await asyncio.sleep(0.01)


async def _snapshot(database, scope):
    async with database.transaction() as connection:
        budget = (await connection.execute(select(BUDGETS))).mappings().one_or_none()
        cursor = await SourceCursorRepository().load(connection, scope, BINDING)
        pending = await ArtifactProcessingPendingRepository().load(connection, scope, BINDING)
        intent = await ArtifactProcessingIntentRepository().load(connection, scope, BINDING)
    return None if budget is None else dict(budget), 0 if cursor is None else cursor.cursor.sequence, pending, intent


def test_http_flush_restart_and_model_change_preserve_terminal_budget_until_operator_repair(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.WARNING, logger="powercontext.builtin.runtime.artifact_processing")
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-provider")
    launches = []
    original_start = SpawnArtifactProcessingWorkerLauncher.start

    async def observe_start(self, assignment):
        launches.append(assignment)
        return await original_start(self, assignment)

    monkeypatch.setattr(SpawnArtifactProcessingWorkerLauncher, "start", observe_start)
    provider = _Provider(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        scope = None
        retained = None
        for model in ("before-restart", "after-restart"):
            app = create_server_app(
                settings=ServerSettings(
                    database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'terminal.db'}"),
                    inference=InferenceConfig(
                        generation_model=f"openai-chat:{model}",
                        generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{provider.server_port}/v1"),
                    ),
                    runtime=RuntimeConfig(artifact_processing_families=("topic-memory",)),
                    mcp=McpConfig(enabled=False),
                ),
                scheduler_path=tmp_path / "scheduler.db",
            )
            warnings_before = len([
                r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.blocked"
            ])
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
            ):
                client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
                database = app.state.application._provider.database
                supervisor = app.state.application.artifact_processing_supervisor
                if scope is None:
                    scope = (await client.get_default_scope()).scope_id
                    for number in range(2):
                        await client.capture_content_source(
                            CaptureContentSourceRequest(
                                scope_id=scope,
                                source_id=f"source-{number}",
                                content="private source sentinel",
                            )
                        )
                    async with database.transaction() as connection:
                        await connection.execute(
                            insert(BUDGETS).values(
                                scope_id=scope,
                                binding_name=BINDING,
                                source_after=0,
                                source_through=1,
                                attempt_id="retained-attempt",
                                attempts=3,
                                requests=9,
                                tokens=1000,
                                failure_code="",
                            )
                        )
                    retained = (await _snapshot(database, scope))[0]
                # Startup readiness may probe the endpoint independently of processing.
                calls_before_flush = provider.calls
                await client.flush_topic_memory(FlushTopicMemoryRequest(scope_id=scope))
                await _wait(
                    lambda warnings_before=warnings_before: (
                        len([r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.blocked"])
                        > warnings_before
                    )
                )
                for _ in range(3):
                    await client.flush_topic_memory(FlushTopicMemoryRequest(scope_id=scope))
                    await asyncio.sleep(0.03)
                budget, cursor, pending, intent = await _snapshot(database, scope)
                assert budget == retained and cursor == 0 and pending.source_through == 2
                assert intent.handled_generation == 0 and intent.requested_generation > 0
                assert not launches and provider.calls == calls_before_flush
                assert supervisor.family_status["topic-memory"]["failed"] == 0
                assert (
                    len([r for r in caplog.records if getattr(r, "event", None) == "artifact_processing.blocked"])
                    == warnings_before + 1
                )
                if model == "after-restart":
                    async with database.transaction() as connection:
                        await connection.execute(update(BUDGETS).values(attempts=1))
                    await client.flush_topic_memory(FlushTopicMemoryRequest(scope_id=scope))
                    await _wait(
                        lambda supervisor=supervisor: supervisor.family_status["topic-memory"]["completed"] == 1
                    )
                    budget, cursor, pending, intent = await _snapshot(database, scope)
                    assert budget is None and cursor == 2 and pending is None
                    assert intent.requested_generation == intent.handled_generation
                    assert len(launches) == 1 and provider.calls == calls_before_flush + 1
                    assert supervisor.family_status["topic-memory"]["failed"] == 0
        assert "private source sentinel" not in caplog.text

    try:
        asyncio.run(scenario())
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
