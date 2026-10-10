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

"""Source capture produces discoverable Atomic Memory through the spawned Supervisor worker."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from pydantic import AnyHttpUrl

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime import CaptureSource
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.scope import ScopeDraft

_MEMORY_TEXT = "The user prefers acceptance tests that exercise spawned workers."


class _GenerationServer(ThreadingHTTPServer):
    requests: list[dict[str, Any]]


class _GenerationHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server = cast(_GenerationServer, self.server)
        server.requests.append(payload)
        request = json.loads(next(message["content"] for message in payload["messages"] if message["role"] == "user"))
        if "proposal" in request:
            output = {
                "action": "create",
                "compared_ids": [item["item_id"] for item in request["related"]],
                "content": {"kind": request["proposal"]["kind"], "text": request["proposal"]["text"]},
                "evidence_ids": request["proposal"]["evidence_ids"],
                "reason": "Keep the preference with the supplied Source evidence.",
            }
        else:
            output = {
                "candidates": [
                    {
                        "kind": "preference",
                        "text": _MEMORY_TEXT,
                        "evidence_ids": [item["evidence_id"] for item in request["evidence"]],
                    }
                ]
            }
        body = {
            "id": "supervisor-generation-test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(output)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 13, "total_tokens": 24},
        }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib parameter
        pass


def test_source_capture_is_generated_and_acknowledged_by_the_spawned_supervisor_worker(tmp_path, monkeypatch) -> None:
    # Only the model's HTTP responses are controlled. The production composition
    # owns automatic admission, assignments, leases, child processes and publication.
    monkeypatch.setenv("OPENAI_API_KEY", "hermetic-supervisor-key")
    server = _GenerationServer(("127.0.0.1", 0), _GenerationHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'supervisor-generation.db'}"),
            inference=InferenceConfig(
                generation_model="openai-chat:gpt-4o-mini",
                generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{server.server_port}/v1"),
            ),
            runtime=RuntimeConfig(
                artifact_processing_families=("memory",),
                memory_schedule_seconds=0.02,
                memory_worker_timeout_seconds=45,
                atomic_memory_related_mode="fts",
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Supervisor", summary="Spawned generation acceptance", idempotency_key="supervisor")
            )
            receipt = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="worker-preference", content=_MEMORY_TEXT, metadata={"kind": "preference"})
            )
            async with asyncio.timeout(60):
                while supervisor.family_status["memory"]["completed"] == 0:
                    status = supervisor.family_status["memory"]
                    assert status["failed"] == 0 and status["timeouts"] == 0, status
                    await asyncio.sleep(0.05)
            # Finish background work before reading its durable acknowledgements.
            await supervisor.close()
            status = supervisor.family_status["memory"]
            assert cast(int, status["completed"]) >= 1
            assert status["used_workers"] == 0 and status["failed"] == 0 and status["timeouts"] == 0

            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            page = await memory.list()
            assert len(page.items) == 1 and page.next_cursor is None
            record = page.items[0]
            assert record.artifact.family == "atomic-memory"
            assert record.artifact.content.kind == "preference"
            assert record.artifact.content.text == _MEMORY_TEXT
            assert record.artifact.lineage.sources == (receipt.source_ref,)
            assert record.state.state == "active" and record.state.state_version == 0
            assert await memory.get(record.ref.artifact_id, revision=record.ref.revision) == record
            found = await memory.search("spawned workers", mode="text")
            assert [item.hit.artifact_ref for item in found.hits] == [record.ref]

        assert server.requests
        # Reopen after both controller and worker have closed. Public reads must
        # retain the exact published revision and the evidence behind it.
        async with open_builtin_runtime(config) as reopened:
            assert reopened.artifact_processing_supervisor is not None
            await reopened.artifact_processing_supervisor.close()
            assert reopened.atomic_memory is not None
            memory = reopened.atomic_memory.for_scope(scope.scope_id)
            assert (await memory.list()).items == (record,)
            assert await memory.get(record.ref.artifact_id, revision=record.ref.revision) == record
            found = await memory.search("spawned workers", mode="text")
            assert [item.hit.artifact_ref for item in found.hits] == [record.ref]
            source = await reopened.records.for_scope(scope.scope_id).get_source(
                receipt.source_ref.source_type, receipt.source_ref.source_id
            )
            assert source.scope_id == scope.scope_id
            assert source.source_type == receipt.source_ref.source_type
            assert source.source_id == receipt.source_ref.source_id
            assert source.position == receipt.sequence
            assert source.content == _MEMORY_TEXT

        assert isinstance(config.database, SQLiteConfig)
        async with (
            SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            binding = FAMILY_BINDINGS["memory"]
            cursor = await SourceCursorRepository().load(connection, scope.scope_id, binding)
            intent = await ArtifactProcessingIntentRepository().load(connection, scope.scope_id, binding)
            assert cursor is not None and cursor.cursor.sequence == receipt.sequence
            assert intent is not None and intent.requested_generation > 0
            assert intent.handled_generation == intent.requested_generation
            assert intent.clean_generation == intent.dirty_generation > 0

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
