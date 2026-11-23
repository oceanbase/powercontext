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

"""Observe actual candidate text without an extra search or rank request."""

import asyncio
import json

import pytest

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceTimeoutError, InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, PrepareContextRequest, PreparedContext, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft


class SelectLast:
    policy_id = "test.select-last.v1"
    supports_atomic_memory = True

    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def rerank(self, query, candidates, limit, /):
        self.calls.append([hit.text for hit in candidates])
        if self.fail:
            raise InferenceTimeoutError("rerank", 1)
        return MemoryRerankDecision(selected_ranks=(len(candidates),), usage=InferenceUsage(requests=1))

    def receipt_configuration(self):
        return {
            "policy_id": self.policy_id,
            "model": None,
            "effective_settings": {"algorithm": "select-last", "algorithm_version": "1"},
            "timeout_seconds": 1.0,
            "max_requests": 1,
        }


def test_stage_text_is_same_execution_input_and_output(tmp_path):
    async def scenario():
        reranker = SelectLast()
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/text.db"))
        async with open_builtin_runtime(config, memory_reranker=reranker) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Text evidence", summary="Same execution", idempotency_key="text")
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="fact", text="Budget candidate_alpha is reviewed Thursday."),
                AtomicMemoryContent(kind="fact", text="Budget candidate_beta is reviewed Friday."),
            ))
            captured = []

            async def sink(record):
                captured.append(record)

            request = PrepareContextRequest(query="budget")
            ordinary = await runtime.context.for_scope(scope.scope_id).prepare(request)
            observed = await runtime.context.for_scope(scope.scope_id).prepare(request, diagnostic_sink=sink)
            assert isinstance(ordinary, PreparedContext) and isinstance(observed, PreparedContext)
            assert ordinary == observed and observed.receipt is None
            assert observed.content is not None
            assert len(reranker.calls) == 2 and len(captured) == 1
            record = captured[0]
            snapshots = record["diagnostics_payload"]["text_snapshots"]
            memory = {item["stage"]: item for item in snapshots if item["family"] == "atomic-memory"}
            original = reranker.calls[-1]
            assert [item["text"] for item in memory["search"]["items"]] == original
            assert [item["text"] for item in memory["rerank-input"]["items"]] == original
            assert [item["text"] for item in memory["rerank-output"]["items"]] == [original[-1]]
            assert [item["text"] for item in memory["assembly"]["items"]] == [original[-1]]
            assert record["prepared_content"] == observed.content and original[-1] in observed.content
            receipt = record["receipt_payload"]
            assert (
                receipt is not None
                and "candidate_alpha" not in json.dumps(receipt)
                and "candidate_beta" not in json.dumps(receipt)
            )
            assert record["diagnostics_payload"]["text_capture_complete"]

    asyncio.run(scenario())


def test_failed_rank_keeps_input_and_unknown_output(tmp_path):
    async def scenario():
        reranker = SelectLast(fail=True)
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/failed.db"))
        async with open_builtin_runtime(config, memory_reranker=reranker) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Failed rank", summary="Input survives", idempotency_key="failure")
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="fact", text="Budget candidate survives a model timeout."),
            ))
            records = []

            async def sink(record):
                records.append(record)

            with pytest.raises(InferenceTimeoutError):
                await runtime.context.for_scope(scope.scope_id).prepare(
                    PrepareContextRequest(query="budget"), diagnostic_sink=sink
                )
            assert len(reranker.calls) == 1 and len(records) == 1
            record = records[0]
            snapshots = record["diagnostics_payload"]["text_snapshots"]
            before = next(row for row in snapshots if row["stage"] == "rerank-input")
            after = next(row for row in snapshots if row["stage"] == "rerank-output")
            assert "candidate survives" in before["items"][0]["text"]
            assert after["items"] is None and after["outcome"] == "failed"
            assert record["prepared_content"] is None and record["status"] == "failed"

    asyncio.run(scenario())


def test_text_extraction_failure_does_not_change_prepared_context(tmp_path, monkeypatch):
    from powercontext.builtin.runtime import application, atomic_memory

    def unavailable(hit):
        raise RuntimeError

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/capture-failure.db"))
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Capture failure", summary="Business isolation", idempotency_key="capture")
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="fact", text="Budget decision remains readable."),
            ))
            request = PrepareContextRequest(query="budget")
            ordinary = await runtime.context.for_scope(scope.scope_id).prepare(request)
            monkeypatch.setattr(application, "memory_text", unavailable)
            monkeypatch.setattr(atomic_memory, "memory_text", unavailable)
            records = []

            async def sink(record):
                records.append(record)

            observed = await runtime.context.for_scope(scope.scope_id).prepare(request, diagnostic_sink=sink)
            assert observed == ordinary
            assert len(records) == 1 and records[0]["status"] == "ready"
            assert records[0]["diagnostics_payload"]["text_capture_complete"] is False

    asyncio.run(scenario())
