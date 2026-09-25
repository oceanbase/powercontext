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

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from powercontext_eval.benchmarks.longmemeval_v2.adapter import (
    AUDIT_SCHEMA,
    PowerContextHTTPRuntime,
    PowerContextMemory,
    PowerContextMemoryAdapterError,
    PowerContextMemoryModeError,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.captures: list[dict[str, object]] = []
        self.memories: list[dict[str, object]] = []
        self.searches: list[dict[str, object]] = []

    def capture_content_source(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        request = dict(payload)
        self.captures.append(request)
        return {
            "status": "accepted",
            "source": {"name": "content", "source_id": request["source_id"]},
            "position": len(self.captures),
        }

    def remember_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        request = dict(payload)
        self.memories.append(request)
        index = len(self.memories)
        return {
            "memory": {"family": "memory", "artifact_id": "memory", "revision": index},
            "entry": {
                "citation": {
                    "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": index},
                    "entry_id": f"entry-{index}",
                    "entry_version_id": f"entry-{index}-v1",
                }
            },
        }

    def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.searches.append(dict(payload))
        return {
            "mode": payload.get("mode"),
            "hits": [
                {
                    "text": "Use the Network assignment group.",
                    "score": 0.9,
                    "matched_by": ["fts"],
                    "citation": {
                        "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 2},
                        "entry_id": "entry-2",
                        "entry_version_id": "entry-2-v1",
                    },
                }
            ],
        }

    def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        content = "Prepared compact context."
        return {
            "schema": "powercontext.prepared-context.v1",
            "status": "ready",
            "content": content,
            "content_bytes": len(content.encode()),
        }


class GuardedTrajectory(dict[str, object]):
    def get(self, key: object, default: object = None) -> object:
        if key in {"question_type", "answer", "gold_answer", "judge", "scorer"}:
            raise AssertionError(f"adapter accessed forbidden field {key}")
        return super().get(key, default)


def adapter(tmp_path: Path, runtime: FakeRuntime, **params: object) -> PowerContextMemory:
    memory = PowerContextMemory(
        {
            "scope_id": "benchmark-run",
            "audit_path": str(tmp_path / "adapter-audit.jsonl"),
            **params,
        }
    )
    memory.configure_runtime(runtime=runtime)
    return memory


def trajectory() -> GuardedTrajectory:
    return GuardedTrajectory(
        {
            "id": "trajectory-1",
            "domain": "enterprise",
            "environment": "workarena",
            "goal": "Assign an incident.",
            "outcome": "success",
            "start_url": "https://example.test/start",
            "states": [
                {
                    "state_index": 0,
                    "step": 0,
                    "url": "https://example.test/start",
                    "action": None,
                    "thought": "Find the incident.",
                    "accessibility_tree": "Incident list " + "记忆" * 500,
                    "screenshot": "screenshots/trajectory-1/0.png",
                }
            ],
            "question_type": "must not be read",
            "gold_answer": "must not be read",
            "judge": {"must": "not be read"},
        }
    )


def audit_events(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "adapter-audit.jsonl").read_text(encoding="utf-8").splitlines()]


def test_insert_uses_public_source_and_memory_operations_with_utf8_safe_chunks(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(tmp_path, runtime, source_chunk_bytes=512)

    memory.insert(trajectory())

    assert len(runtime.captures) > 1
    assert len(runtime.memories) == 1
    assert all(request["scope_id"] == "benchmark-run" for request in runtime.captures)
    assert all(len(str(request["content"]).encode()) <= 512 for request in runtime.captures)
    restored = "".join(str(request["content"]) for request in runtime.captures)
    projected = json.loads(restored)
    assert set(projected) == {"id", "domain", "environment", "goal", "outcome", "start_url", "states"}
    assert "question_type" not in restored
    assert "gold_answer" not in restored
    assert len(str(runtime.memories[0]["text"]).encode()) <= 8192
    assert "Assign an incident" in str(runtime.memories[0]["text"])

    [event] = audit_events(tmp_path)
    assert event["schema"] == AUDIT_SCHEMA
    assert event["operation"] == "ingest"
    assert event["status"] == "succeeded"
    assert event["scope_id"] == "benchmark-run"
    assert isinstance(event["observed_at"], str)
    assert event["source_chunk_count"] == len(runtime.captures)
    assert event["memory_entry_count"] == 1
    assert len(event["source_refs"]) == len(runtime.captures)
    assert len(event["memory_citations"]) == 1
    assert set(event["timings_ms"]) == {"source_capture", "memory_remember", "total"}


def test_l0_l1_projection_writes_two_bounded_memory_entries_without_schema_changes(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(tmp_path, runtime, memory_projection="deterministic-l0-l1-v1")

    memory.insert(trajectory())

    assert len(runtime.memories) == 2
    assert str(runtime.memories[0]["text"]).startswith("LongMemEval-V2 deterministic L0 trajectory index")
    assert str(runtime.memories[1]["text"]).startswith("LongMemEval-V2 deterministic L1 trajectory summary")
    assert all(len(str(entry["text"]).encode()) <= 8_192 for entry in runtime.memories)
    [audit] = audit_events(tmp_path)
    assert audit["memory_projection"] == "deterministic-l0-l1-v1"
    assert audit["memory_entry_count"] == 2


def test_l0_l1_projection_keeps_the_byte_limit_when_a_trajectory_reaches_it(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(tmp_path, runtime, memory_projection="deterministic-l0-l1-v1")
    long_trajectory = trajectory()
    long_trajectory["states"] = [
        {
            "state_index": index,
            "step": index,
            "url": "https://example.test/state",
            "action": "relocate " + "x" * 590,
            "thought": "inspect " + "y" * 590,
            "accessibility_tree": "tree " + "z" * 590,
            "screenshot": "unused.png",
        }
        for index in range(10)
    ]

    memory.insert(long_trajectory)

    assert len(runtime.memories) == 2
    l0_text, l1_text = (str(entry["text"]) for entry in runtime.memories)
    assert len(l0_text.encode()) <= 1_024
    assert len(l1_text.encode()) == 8_192
    assert l1_text.startswith("LongMemEval-V2 deterministic L1 trajectory summary")


def test_query_returns_upstream_text_items_and_records_citations(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(tmp_path, runtime, search_mode="fts", search_limit=4)
    memory.set_query_context(query_invocation_id="query-7")

    result = memory.query("Which assignment group should I use?", "question.png")
    metadata = memory.post_query_hook(
        query="Which assignment group should I use?",
        query_image="question.png",
        memory_context=result,
    )

    assert result == [{"type": "text", "value": "Use the Network assignment group."}]
    assert runtime.searches == [
        {
            "scope_id": "benchmark-run",
            "query": "Which assignment group should I use?",
            "limit": 4,
            "mode": "fts",
        }
    ]
    [event] = audit_events(tmp_path)
    assert event["query_invocation_id"] == "query-7"
    assert event["query_image_present"] is True
    assert event["requested_mode"] == "fts"
    assert event["actual_mode"] == "fts"
    assert event["result_count"] == 1
    assert event["citations"][0]["entry_id"] == "entry-2"
    assert "question.png" not in json.dumps(event)
    assert set(event["timings_ms"]) == {"search", "format", "total"}
    assert metadata == {
        "query_invocation_id": "query-7",
        "result_count": 1,
        "retrieval_strategy": "memory-search",
        "task_lens": None,
        "requested_mode": "fts",
        "actual_mode": "fts",
        "citations": event["citations"],
        "timings_ms": event["timings_ms"],
    }


def test_failed_query_is_audited_without_question_or_image_content(tmp_path: Path) -> None:
    class FailingRuntime(FakeRuntime):
        def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            raise PowerContextMemoryAdapterError("unavailable")

    memory = adapter(tmp_path, FailingRuntime())

    with pytest.raises(PowerContextMemoryAdapterError, match="unavailable"):
        memory.query("secret-looking benchmark question", "private/image.png")

    [event] = audit_events(tmp_path)
    assert event["status"] == "failed"
    assert event["failure_type"] == "PowerContextMemoryAdapterError"
    serialized = json.dumps(event)
    assert "secret-looking" not in serialized
    assert "private/image.png" not in serialized


def test_rejects_a_search_mode_outside_the_public_contract(tmp_path: Path) -> None:
    with pytest.raises(PowerContextMemoryAdapterError, match="search_mode must be one of"):
        adapter(tmp_path, FakeRuntime(), search_mode="semantic")

    with pytest.raises(PowerContextMemoryAdapterError, match="search_mode must be one of"):
        adapter(tmp_path, FakeRuntime(), search_mode="hybrid-fts")


def test_hybrid_query_requests_and_records_the_executed_mode(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(tmp_path, runtime, search_mode="hybrid")

    result = memory.query("Which assignment group should I use?")
    metadata = memory.post_query_hook(
        query="Which assignment group should I use?",
        query_image=None,
        memory_context=result,
    )

    assert runtime.searches[0]["mode"] == "hybrid"
    [event] = audit_events(tmp_path)
    assert event["status"] == "succeeded"
    assert event["requested_mode"] == "hybrid"
    assert event["actual_mode"] == "hybrid"
    assert metadata is not None
    assert metadata["requested_mode"] == "hybrid"
    assert metadata["actual_mode"] == "hybrid"


def test_query_time_compact_uses_public_prepared_context_and_records_strategy(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(
        tmp_path,
        runtime,
        query_strategy="prepared-context",
        prepared_context_max_bytes=4_096,
    )

    result = memory.query("Which assignment group should I use?")
    metadata = memory.post_query_hook(
        query="Which assignment group should I use?",
        query_image=None,
        memory_context=result,
    )

    assert result == [{"type": "text", "value": "Prepared compact context."}]
    assert runtime.searches == []
    assert metadata is not None
    assert metadata["retrieval_strategy"] == "prepared-context"
    assert metadata["requested_mode"] is None
    assert metadata["actual_mode"] is None
    [event] = audit_events(tmp_path)
    assert event["retrieval_strategy"] == "prepared-context"


def test_query_time_compact_rejects_a_response_over_its_byte_budget(tmp_path: Path) -> None:
    class OversizedPreparedContextRuntime(FakeRuntime):
        def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            return {
                "schema": "powercontext.prepared-context.v1",
                "status": "ready",
                "content": "x" * 513,
                "content_bytes": 513,
            }

    memory = adapter(
        tmp_path,
        OversizedPreparedContextRuntime(),
        query_strategy="prepared-context",
        prepared_context_max_bytes=512,
    )

    with pytest.raises(PowerContextMemoryAdapterError, match="exceeded the requested byte budget"):
        memory.query("Which assignment group should I use?")


def test_task_lens_projects_only_question_keywords_into_the_public_search(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(tmp_path, runtime, search_mode="fts", task_lens="question-keywords-v1")

    memory.query("Which assignment group should I use for the Network incident?")

    assert runtime.searches[0]["query"] == "assignment group use network incident"
    [event] = audit_events(tmp_path)
    assert event["task_lens"] == "question-keywords-v1"
    assert event["query_sha256"] != event["retrieval_query_sha256"]


def test_fts_query_fails_closed_when_the_server_executed_a_different_mode(tmp_path: Path) -> None:
    class MisreportingHybridRuntime(FakeRuntime):
        def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            response = dict(super().search_memory(payload))
            response["mode"] = "hybrid"
            return response

    memory = adapter(tmp_path, MisreportingHybridRuntime(), search_mode="fts")

    with pytest.raises(PowerContextMemoryModeError, match="reported mode 'hybrid'"):
        memory.query("Which assignment group should I use?")

    [event] = audit_events(tmp_path)
    assert event["status"] == "failed"
    assert event["failure_type"] == "PowerContextMemoryModeError"
    assert event["requested_mode"] == "fts"
    assert event["actual_mode"] == "hybrid"


def test_hybrid_query_fails_closed_when_the_server_executed_a_different_mode(tmp_path: Path) -> None:
    class SilentFtsFallbackRuntime(FakeRuntime):
        def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            response = dict(super().search_memory(payload))
            response["mode"] = "fts"
            return response

    memory = adapter(tmp_path, SilentFtsFallbackRuntime(), search_mode="hybrid")

    with pytest.raises(PowerContextMemoryModeError, match="reported mode 'fts'"):
        memory.query("Which assignment group should I use?")

    [event] = audit_events(tmp_path)
    assert event["status"] == "failed"
    assert event["failure_type"] == "PowerContextMemoryModeError"
    assert event["requested_mode"] == "hybrid"
    assert event["actual_mode"] == "fts"


def test_an_explicit_mode_fails_closed_when_the_server_reports_no_mode(tmp_path: Path) -> None:
    class UnreportedModeRuntime(FakeRuntime):
        def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            response = dict(super().search_memory(payload))
            del response["mode"]
            return response

    memory = adapter(tmp_path, UnreportedModeRuntime(), search_mode="hybrid")

    with pytest.raises(PowerContextMemoryModeError, match="reported mode None"):
        memory.query("Which assignment group should I use?")

    [event] = audit_events(tmp_path)
    assert event["status"] == "failed"
    assert event["requested_mode"] == "hybrid"
    assert event["actual_mode"] is None


def test_auto_query_accepts_the_server_chosen_mode(tmp_path: Path) -> None:
    class VectorAutoRuntime(FakeRuntime):
        def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            response = dict(super().search_memory(payload))
            response["mode"] = "vector"
            return response

    memory = adapter(tmp_path, VectorAutoRuntime())  # the default mode is auto

    result = memory.query("Which assignment group should I use?")

    assert result == [{"type": "text", "value": "Use the Network assignment group."}]
    [event] = audit_events(tmp_path)
    assert event["status"] == "succeeded"
    assert event["requested_mode"] == "auto"
    assert event["actual_mode"] == "vector"


def test_duplicate_insert_fails_closed_and_records_the_attempt(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    memory = adapter(tmp_path, runtime)
    memory.insert(trajectory())

    with pytest.raises(PowerContextMemoryAdapterError, match="duplicate trajectory"):
        memory.insert(trajectory())

    events = audit_events(tmp_path)
    assert [event["status"] for event in events] == ["succeeded", "failed"]
    assert len(runtime.captures) == 1


def test_partial_ingest_failure_preserves_completed_chunk_citations(tmp_path: Path) -> None:
    class PartiallyFailingRuntime(FakeRuntime):
        def remember_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            raise PowerContextMemoryAdapterError("memory unavailable")

    runtime = PartiallyFailingRuntime()
    memory = adapter(tmp_path, runtime, source_chunk_bytes=512)

    with pytest.raises(PowerContextMemoryAdapterError, match="memory unavailable"):
        memory.insert(trajectory())

    [event] = audit_events(tmp_path)
    assert event["status"] == "failed"
    assert len(event["source_refs"]) == len(runtime.captures) > 1
    assert event["memory_citations"] == []


def test_http_runtime_rejects_credentials_and_plaintext_remote_hosts() -> None:
    with pytest.raises(PowerContextMemoryAdapterError, match="credentials"):
        PowerContextHTTPRuntime("https://token@example.test", token=None, timeout_seconds=1)
    with pytest.raises(PowerContextMemoryAdapterError, match="unencrypted non-loopback"):
        PowerContextHTTPRuntime("http://example.test", token=None, timeout_seconds=1)
    with pytest.raises(PowerContextMemoryAdapterError, match="query or fragment"):
        PowerContextHTTPRuntime("https://example.test/?token=secret", token=None, timeout_seconds=1)

    PowerContextHTTPRuntime("http://127.0.0.1:8765", token=None, timeout_seconds=1)
