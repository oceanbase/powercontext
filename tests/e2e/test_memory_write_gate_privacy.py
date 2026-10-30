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

"""A real model HTTP refusal must prevent Memory publication and Source consumption."""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import cast

import pytest
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.decision_observations import DecisionPolicyMode, DecisionPrivacyBoundary
from powercontext.builtin.inference import InferencePrivacyError
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

_FORBIDDEN = "synthetic-gate-forbidden-rationale"
_MEMORY = "The user prefers SQLite restart regression tests."
_MODEL = "memory-gate-test-model"


class _ModelServer(ThreadingHTTPServer):
    refuse_gate = True
    gate_responses = 0


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server = cast(_ModelServer, self.server)
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        task = json.loads(next(item["content"] for item in request["messages"] if item["role"] == "user"))
        if "decision_kind" in task:
            server.gate_responses += 1
            output = {
                "answer": "no",
                "confidence": 1.0,
                "rationale": _FORBIDDEN if server.refuse_gate else "Supported.",
            }
        elif "proposal" in task:
            output = {
                "action": "create",
                "compared_ids": [item["item_id"] for item in task["related"]],
                "content": {
                    "kind": task["proposal"]["kind"],
                    "text": task["proposal"]["text"],
                    "description": task["proposal"]["description"],
                },
                "evidence_ids": task["proposal"]["evidence_ids"],
                "reason": "Retain the supported preference.",
            }
        else:
            output = {
                "candidates": [
                    {
                        "kind": "preference",
                        "text": _MEMORY,
                        "description": "Synthetic user preference.",
                        "evidence_ids": [task["evidence"][0]["evidence_id"]],
                    }
                ]
            }
        encoded = json.dumps({
            "id": "gate-response",
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

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - standard-library signature
        pass


def test_privacy_refusal_keeps_memory_unpublished_and_source_retryable_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-gate-key")
    server = _ModelServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    async def scenario():
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}"),
            inference=InferenceConfig(
                generation_model="openai-chat:" + _MODEL,
                generation_base_url=AnyHttpUrl(base_url),
                generation_model_settings={"max_tokens": 1024},
                protection=InferenceProtectionConfig(
                    mode="enforcing",
                    forbidden_values=(SecretStr(_FORBIDDEN),),
                    targets={
                        "model": UpstreamProfile(
                            endpoint=base_url + "/chat/completions",
                            model=_MODEL,
                            protocol="openai-chat",
                            context_window_tokens=32768,
                            max_output_tokens=1024,
                            validated_utf8_token_bound=True,
                        )
                    },
                ),
            ),
            runtime=RuntimeConfig(
                memory_write_gate_enabled=True,
                memory_write_gate_mode=DecisionPolicyMode.ENFORCING,
                memory_write_gate_privacy_boundary=DecisionPrivacyBoundary.LOCAL_ONLY,
                atomic_memory_related_mode="fts",
                profile_schedule_enabled=False,
                dream_enabled=False,
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Protected gate", summary="Privacy refusal recovery", idempotency_key="gate")
            )
            receipt = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="preference", content=_MEMORY, metadata={})
            )
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            with pytest.raises(InferencePrivacyError) as caught:
                await memory.flush()
            assert caught.value.phase == "before_release"
            assert caught.value.reason_codes == ("forbidden_content",)
            assert not (await memory.list()).items
            assert server.gate_responses > 0

        async with open_builtin_runtime(config) as reopened:
            assert reopened.atomic_memory is not None
            memory = reopened.atomic_memory.for_scope(scope.scope_id)
            assert not (await memory.list()).items
            source = await reopened.records.for_scope(scope.scope_id).get_source(
                receipt.source_ref.source_type, receipt.source_ref.source_id
            )
            assert source.content == _MEMORY and source.position == receipt.sequence
            with pytest.raises(InferencePrivacyError):
                await memory.flush()
            assert not (await memory.list()).items
            server.refuse_gate = False
            flushed = await memory.flush()
            assert flushed.processed and flushed.source_count == 1
            records = (await memory.list()).items
            assert len(records) == 1 and records[0].artifact.content.text == _MEMORY
            assert records[0].artifact.lineage.sources == (receipt.source_ref,)
            assert not (await memory.flush()).processed

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
