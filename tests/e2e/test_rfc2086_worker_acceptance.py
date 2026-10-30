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

"""Real spawned-worker privacy rejection retains Source work across restart.

The database, supervisor and child workers are real. Only the model HTTP
responses are controlled; this does not measure a real provider's behavior.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    InferenceConfig,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.scope import ScopeDraft

_FORBIDDEN = "synthetic-worker-forbidden-value"
_MEMORY = "The user prefers tests exercising spawned worker recovery."
_MODEL = "rfc2086-worker-model"


class WorkerServer(ThreadingHTTPServer):
    requests: list[dict[str, Any]]


class WorkerHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cast(WorkerServer, self.server).requests.append(payload)
        prompt = next(item["content"] for item in payload["messages"] if item["role"] == "user")
        task = json.loads(prompt)
        if "proposal" in task:
            output = {
                "action": "create",
                "compared_ids": [item["item_id"] for item in task["related"]],
                "content": {
                    "kind": task["proposal"]["kind"],
                    "text": task["proposal"]["text"],
                    "description": task["proposal"]["description"],
                },
                "evidence_ids": task["proposal"]["evidence_ids"],
                "reason": "Retain the synthetic preference with Source evidence.",
            }
        else:
            output = {
                "candidates": [
                    {
                        "kind": "preference",
                        "text": _MEMORY,
                        "description": "Synthetic user preference.",
                        "evidence_ids": [item["evidence_id"] for item in task["evidence"]],
                    }
                ]
            }
        encoded = json.dumps({
            "id": "rfc2086-worker",
            "object": "chat.completion",
            "created": 0,
            "model": _MODEL,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": json.dumps(output)}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 13, "total_tokens": 24},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - standard-library signature
        pass


def test_privacy_rejection_keeps_source_pending_and_recovers_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-worker-key")
    server = WorkerServer(("127.0.0.1", 0), WorkerHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        policy = InferenceProtectionConfig(
            mode="enforcing",
            forbidden_values=(SecretStr(_FORBIDDEN),),
            targets={
                "generation": UpstreamProfile(
                    endpoint=base_url + "/chat/completions",
                    model=_MODEL,
                    protocol="openai-chat",
                    context_window_tokens=32768,
                    max_output_tokens=1024,
                    validated_utf8_token_bound=True,
                )
            },
        )
        config = BuiltinConfig(
            database=database,
            inference=InferenceConfig(
                generation_model="openai-chat:" + _MODEL,
                generation_base_url=AnyHttpUrl(base_url),
                generation_model_settings={"max_tokens": 1024},
                protection=policy,
            ),
            runtime=RuntimeConfig(
                artifact_processing_families=("memory",),
                memory_schedule_seconds=0.02,
                memory_worker_timeout_seconds=45,
                atomic_memory_related_mode="fts",
                profile_schedule_enabled=False,
                dream_enabled=False,
            ),
        )
        source_content = _MEMORY + " Synthetic sensitive marker: " + _FORBIDDEN
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Privacy recovery",
                    summary="Isolated worker acceptance",
                    idempotency_key="rfc2086-worker",
                )
            )
            receipt = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="private-source",
                    content=source_content,
                    metadata={"kind": "preference", "project_rules": "Preserve all exact Source evidence. " * 100},
                )
            )
            async with asyncio.timeout(60):
                while supervisor.family_status["memory"]["failed"] == 0:
                    assert supervisor.family_status["memory"]["completed"] == 0
                    await asyncio.sleep(0.05)
            await supervisor.close()
            assert not server.requests  # The real child's blocked input never reached the socket.
            assert not (await runtime.atomic_memory.for_scope(scope.scope_id).list()).items

        async with (
            SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as conn,
        ):
            binding = FAMILY_BINDINGS["memory"]
            cursor = await SourceCursorRepository().load(conn, scope.scope_id, binding)
            intent = await ArtifactProcessingIntentRepository().load(conn, scope.scope_id, binding)
            assert cursor is None or cursor.cursor.sequence < receipt.sequence
            assert intent is not None
            assert intent.handled_generation < intent.requested_generation
            assert intent.clean_generation < intent.dirty_generation

        # Reopen the same deployment and stop admission before inspecting preserved evidence.
        async with open_builtin_runtime(config) as reopened:
            assert reopened.artifact_processing_supervisor is not None
            await reopened.artifact_processing_supervisor.close()
            source = await reopened.records.for_scope(scope.scope_id).get_source(
                receipt.source_ref.source_type,
                receipt.source_ref.source_id,
            )
            assert source.content == source_content and source.position == receipt.sequence
            assert reopened.atomic_memory is not None
            assert not (await reopened.atomic_memory.for_scope(scope.scope_id).list()).items
        assert not server.requests

        recovery = config.model_copy(
            update={
                "inference": config.inference.model_copy(
                    update={
                        "protection": policy.model_copy(update={"forbidden_values": ()}),
                    }
                )
            }
        )
        async with open_builtin_runtime(recovery) as reopened:
            supervisor = reopened.artifact_processing_supervisor
            assert supervisor is not None
            async with asyncio.timeout(60):
                while supervisor.family_status["memory"]["completed"] == 0:
                    assert supervisor.family_status["memory"]["failed"] == 0
                    await asyncio.sleep(0.05)
            await supervisor.close()
            assert reopened.atomic_memory is not None
            records = (await reopened.atomic_memory.for_scope(scope.scope_id).list()).items
            assert len(records) == 1
            assert records[0].artifact.content.text == _MEMORY
            assert records[0].artifact.lineage.sources == (receipt.source_ref,)
        assert server.requests
        async with (
            SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as conn,
        ):
            cursor = await SourceCursorRepository().load(conn, scope.scope_id, binding)
            intent = await ArtifactProcessingIntentRepository().load(conn, scope.scope_id, binding)
            assert cursor is not None and cursor.cursor.sequence == receipt.sequence
            assert intent is not None and intent.handled_generation == intent.requested_generation
            assert intent.clean_generation == intent.dirty_generation > 0

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
