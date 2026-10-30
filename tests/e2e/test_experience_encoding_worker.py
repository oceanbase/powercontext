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

"""Experience input encoding survives a real worker boundary and privacy recovery.

The database, supervisor and child workers are real. Only the model HTTP
responses are controlled; this does not measure a real provider's behavior.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal, cast

import pytest
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.inference.input_encoding import decode_input
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.review.models import Candidate
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    InferenceConfig,
    ListCandidatesRequest,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.scope import ScopeDraft

_FORBIDDEN = "synthetic-worker-forbidden-value"
_MEMORY = "Keep the observed migration failure and require a verified recovery before recording success."
_MODEL = "rfc2086-worker-model"


class WorkerServer(ThreadingHTTPServer):
    requests: list[dict[str, Any]]


class WorkerHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cast(WorkerServer, self.server).requests.append(payload)
        prompt = next(item["content"] for item in payload["messages"] if item["role"] == "user")
        task = json.loads(prompt)
        if task.get("schema", "").startswith("powercontext.input."):
            task = json.loads(decode_input(prompt, schema=task["schema"], stage="experience.incubate"))
        output = {
            "candidates": [
                {
                    "proposal": {
                        "situation": "A migration has failed and recovery is pending.",
                        "action": "Preserve the failed check and source evidence.",
                        "outcome": "The migration remains failed; recovery has not been verified.",
                        "lesson": _MEMORY,
                    },
                    "evidence_ids": [item["evidence_id"] for item in task["evidence"]],
                }
            ],
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


@pytest.mark.parametrize("encoding", ["off", "shadow", "active"])
def test_experience_encoding_worker_keeps_blocked_work_and_recovers_after_restart(
    tmp_path, monkeypatch, encoding: Literal["off", "shadow", "active"]
) -> None:
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
                input_encoding={"experience.incubate": encoding},
            ),
            runtime=RuntimeConfig(
                artifact_processing_families=("experience",),
                memory_schedule_seconds=None,
                experience_schedule_seconds=0.02,
                experience_worker_timeout_seconds=45,
                atomic_memory_related_mode="fts",
                profile_schedule_enabled=False,
                dream_enabled=False,
            ),
        )
        source_content = (_MEMORY + "\n") * 60 + " Synthetic sensitive marker: " + _FORBIDDEN
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.experience is not None
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Privacy recovery",
                    summary="Isolated worker acceptance",
                    idempotency_key="rfc2086-worker",
                )
            )
            receipts = [
                await runtime.sources.for_scope(scope.scope_id).capture(
                    CaptureSource(
                        source_id=f"private-source-{index}", content=source_content, metadata={"kind": "task-outcome"}
                    )
                )
                for index in range(3)
            ]
            receipt = receipts[-1]
            async with asyncio.timeout(60):
                while supervisor.family_status["experience"]["failed"] == 0:
                    assert supervisor.family_status["experience"]["completed"] == 0
                    await asyncio.sleep(0.05)
            await supervisor.close()
            assert not server.requests  # The real child's blocked input never reached the socket.
            assert not (await runtime.review.for_scope(scope.scope_id).list(ListCandidatesRequest())).candidates

        async with (
            SQLiteProfile.open(database, tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as conn,
        ):
            binding = FAMILY_BINDINGS["experience"]
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
            assert reopened.experience is not None
            assert not (await reopened.review.for_scope(scope.scope_id).list(ListCandidatesRequest())).candidates
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
                while supervisor.family_status["experience"]["completed"] == 0:
                    assert supervisor.family_status["experience"]["failed"] == 0
                    await asyncio.sleep(0.05)
            await supervisor.close()
            assert reopened.experience is not None
            records = (await reopened.review.for_scope(scope.scope_id).list(ListCandidatesRequest())).candidates
            assert len(records) == 1
            assert isinstance(records[0], Candidate) and isinstance(records[0].proposal, ExperienceContent)
            assert records[0].proposal.lesson == _MEMORY
            assert records[0].sources == tuple(item.source_ref for item in receipts)
            assert records[0].result_artifact is None
        assert server.requests
        sent = [
            json.loads(item["content"])
            for request in server.requests
            for item in request["messages"]
            if item["role"] == "user"
        ]
        assert any(item.get("schema", "").startswith("powercontext.input.") for item in sent) == (encoding == "active")
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
