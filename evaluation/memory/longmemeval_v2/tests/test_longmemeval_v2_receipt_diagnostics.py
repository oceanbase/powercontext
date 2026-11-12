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

"""Same-response diagnostics through the evaluation adapter's public interface."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from powercontext_eval_longmemeval_v2.adapter import (
    AUDIT_SCHEMA,
    RECEIPT_AUDIT_SCHEMA,
    PowerContextMemory,
    PowerContextMemoryAdapterError,
)
from powercontext_eval_longmemeval_v2.receipt_diagnostics import DIAGNOSTICS_SCHEMA, RECEIPT_SCHEMA


def response(content: str | None = "资料 evidence", maximum_bytes: int = 8_000) -> dict[str, Any]:
    digest = "sha256:" + "a" * 64
    return {
        "schema": "powercontext.prepared-context.v1",
        "status": "empty" if content is None else "ready",
        "content": content,
        "content_bytes": 0 if content is None else len(content.encode()),
        "receipt": {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": "00000000-0000-4000-8000-000000000001",
            "policy_id": RECEIPT_SCHEMA,
            "query_digest": digest,
            "content_digest": None if content is None else "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            "requested_max_bytes": maximum_bytes,
            "used_bytes": 0 if content is None else len(content.encode()),
            "truncated": content is not None,
            "non_deterministic": False,
            "selected": []
            if content is None
            else [
                {
                    "kind": "atomic-memory",
                    "artifact_address": {
                        "scope_id": "synthetic-scope",
                        "artifact": {"family": "atomic-memory", "artifact_id": "evidence-1", "revision": 3},
                    },
                    "rendered_bytes": len(content.encode()),
                    "truncated": True,
                }
            ],
            "omitted": [
                {"family": "atomic-memory", "reason": "below_min_bytes", "count": 2},
                {"family": "atomic-memory", "reason": "rerank_not_selected", "count": 3},
                {"family": "experience", "reason": "not_retrieved", "count": 1},
            ],
            "retrieval": {
                "rounds": [
                    {"round": 0, "outcome": "completed", "retained": True},
                    {"round": 1, "outcome": "expansion_failed", "retained": False},
                ],
                "searches": [
                    {
                        "round": 0,
                        "family": "atomic-memory",
                        "mode": "fts",
                        "outcome": "completed",
                        "fallback_reason": "reused_fts_fallback",
                        "count": 2,
                    }
                ],
                "reranks": [
                    {
                        "round": 0,
                        "config_id": digest,
                        "outcome": "fallback",
                        "fallback_reason": "inference_timeout",
                        "count": 1,
                    }
                ],
                "rerank_configs": [
                    {
                        "config_id": digest,
                        "config_digest": digest,
                        "policy_id": "synthetic.rerank.v1",
                        "model": None,
                        "non_deterministic": False,
                        "effective_settings": {"secret": "PRIVATE-SETTING"},
                        "prompt": None,
                        "max_requests": 1,
                        "timeout_seconds": 1.0,
                    }
                ],
            },
            "stages": [{"stage": "context.build", "duration_ms": 1.2, "count": 1}],
        },
    }


class Runtime:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.response = payload
        self.calls: list[dict[str, object]] = []

    def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.calls.append(dict(payload))
        return deepcopy(self.response)

    def capture_content_source(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        raise AssertionError("unexpected source call")

    def remember_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        raise AssertionError("unexpected remember call")

    def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        raise AssertionError("unexpected search call")


def memory(tmp_path: Path, runtime: Runtime | None = None, **params: object) -> PowerContextMemory:
    result = PowerContextMemory(
        {
            "scope_id": "synthetic-scope",
            "audit_path": str(tmp_path / "audit.jsonl"),
            "query_strategy": "prepared-context",
            "receipt_diagnostics": True,
            "receipt_server_revision": "a" * 40,
            **params,
        }
    )
    if runtime is not None:
        result.configure_runtime(runtime=runtime)
    return result


def events(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


def test_same_prepare_response_preserves_items_and_records_only_allowlisted_evidence(tmp_path: Path) -> None:
    payload = response()
    payload["receipt"]["future_secret"] = "PRIVATE-FUTURE"
    runtime = Runtime(payload)
    adapter = memory(tmp_path, runtime)
    adapter.set_query_context(query_invocation_id="query-1")

    items = adapter.query("PRIVATE-QUESTION")
    [event] = events(tmp_path)
    diagnostics = event["prepared_context_diagnostics"]

    assert items == [{"type": "text", "value": payload["content"]}]
    assert runtime.calls == [
        {
            "scope_id": "synthetic-scope",
            "query": "PRIVATE-QUESTION",
            "max_bytes": 8_000,
            "include_receipt": True,
        }
    ]
    assert event["schema"] == RECEIPT_AUDIT_SCHEMA
    assert event["query_invocation_id"] == "query-1"
    assert diagnostics["receipt_status"] == "available"
    assert diagnostics["schema"] == DIAGNOSTICS_SCHEMA
    assert diagnostics["candidate_count"] is None
    assert diagnostics["candidate_count_basis"] == "not_exposed"
    assert diagnostics["selected"] == payload["receipt"]["selected"]
    assert diagnostics["omitted"] == payload["receipt"]["omitted"]
    assert diagnostics["retrieval"]["searches"] == payload["receipt"]["retrieval"]["searches"]
    assert diagnostics["retrieval"]["rounds"][1]["outcome"] == "expansion_failed"
    assert diagnostics["stages"] == payload["receipt"]["stages"]
    serialized = json.dumps(event, ensure_ascii=False)
    assert all(secret not in serialized for secret in ("PRIVATE-QUESTION", "PRIVATE-SETTING", "PRIVATE-FUTURE", "资料"))
    assert "effective_settings" not in serialized
    metadata = adapter.post_query_hook(query="PRIVATE-QUESTION", query_image=None, memory_context=items)
    assert metadata is not None
    assert "prepared_context_diagnostics" not in metadata


def test_unknown_stage_and_future_fields_cannot_leak_content(tmp_path: Path) -> None:
    payload = response()
    payload["receipt"]["stages"][0]["stage"] = "PRIVATE-STAGE-PATH"
    payload["receipt"]["stages"][0]["future"] = "PRIVATE-FUTURE"
    adapter = memory(tmp_path, Runtime(payload))
    adapter.query("question")
    diagnostics = events(tmp_path)[0]["prepared_context_diagnostics"]
    assert diagnostics["receipt_status"] == "available"
    assert diagnostics["stages"] == [{"stage": "other", "duration_ms": 1.2, "count": 1}]
    assert "PRIVATE" not in json.dumps(diagnostics)


def test_selected_addresses_preserve_public_unicode_identities_in_order(tmp_path: Path) -> None:
    payload = response()
    first = payload["receipt"]["selected"][0]
    first["artifact_address"]["scope_id"] = "合成 scope"
    first["artifact_address"]["artifact"]["artifact_id"] = "材料 一"
    second = deepcopy(first)
    second["artifact_address"]["artifact"]["artifact_id"] = "材料 二"
    payload["receipt"]["selected"].append(second)
    adapter = memory(tmp_path, Runtime(payload))

    assert adapter.query("question") == [{"type": "text", "value": payload["content"]}]
    diagnostics = events(tmp_path)[0]["prepared_context_diagnostics"]
    assert diagnostics["receipt_status"] == "available"
    assert diagnostics["selected"] == payload["receipt"]["selected"]


@pytest.mark.parametrize("field", ["scope_id", "artifact_id"])
def test_whitespace_padded_artifact_identity_is_invalid_without_changing_context(tmp_path: Path, field: str) -> None:
    payload = response()
    address = payload["receipt"]["selected"][0]["artifact_address"]
    target = address if field == "scope_id" else address["artifact"]
    target[field] = " padded "
    adapter = memory(tmp_path, Runtime(payload))

    assert adapter.query("question") == [{"type": "text", "value": payload["content"]}]
    diagnostics = events(tmp_path)[0]["prepared_context_diagnostics"]
    assert diagnostics["receipt_status"] == "invalid"
    assert diagnostics["validation_failure"] == "invalid_receipt"


def test_default_has_original_request_schema_and_items(tmp_path: Path) -> None:
    runtime = Runtime(response())
    adapter = memory(tmp_path, runtime, receipt_diagnostics=False, receipt_server_revision=None)
    assert adapter.query("question") == [{"type": "text", "value": "资料 evidence"}]
    assert runtime.calls == [{"scope_id": "synthetic-scope", "query": "question", "max_bytes": 8_000}]
    [event] = events(tmp_path)
    assert event["schema"] == AUDIT_SCHEMA
    assert "prepared_context_diagnostics" not in event


@pytest.mark.parametrize(
    "params",
    [
        {"receipt_diagnostics": "false"},
        {"receipt_diagnostics": 1},
        {"query_strategy": "memory-search"},
        {"receipt_server_revision": None},
        {"receipt_server_revision": "A" * 40},
        {"receipt_server_revision": "abc"},
    ],
)
def test_invalid_profile_is_rejected_before_writing(tmp_path: Path, params: dict[str, object]) -> None:
    with pytest.raises(PowerContextMemoryAdapterError):
        memory(tmp_path, **params)
    assert not (tmp_path / "audit.jsonl").exists()


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("schema", "future", "unsupported_schema"),
        ("content_digest", "sha256:" + "b" * 64, "content_digest_mismatch"),
        ("used_bytes", 1, "used_bytes_mismatch"),
        ("used_bytes", True, "invalid_receipt"),
        ("requested_max_bytes", 512, "budget_mismatch"),
        ("truncated", False, "truncation_mismatch"),
        ("selected", [{}], "invalid_receipt"),
        ("selected", [{}] * 33, "invalid_receipt"),
        ("stages", [{"stage": "context.build", "duration_ms": float("inf"), "count": 1}], "invalid_receipt"),
    ],
)
def test_invalid_receipt_keeps_usable_context_without_trusted_attribution(
    tmp_path: Path,
    field: str,
    value: object,
    failure: str,
) -> None:
    payload = response()
    payload["receipt"][field] = value
    adapter = memory(tmp_path, Runtime(payload))
    assert adapter.query("question") == [{"type": "text", "value": payload["content"]}]
    [event] = events(tmp_path)
    assert event["status"] == "succeeded"
    diagnostics = event["prepared_context_diagnostics"]
    assert diagnostics["receipt_status"] == "invalid"
    assert diagnostics["validation_failure"] == failure
    assert diagnostics["completeness"] == "incomplete"
    assert "selected" not in diagnostics


def test_missing_receipt_and_empty_selected_preserve_unknown_candidate_count(tmp_path: Path) -> None:
    payload = response(None)
    runtime = Runtime(payload)
    adapter = memory(tmp_path, runtime)
    assert adapter.query("question") == []
    runtime.response.pop("receipt")
    assert adapter.query("question") == []
    available, missing = [event["prepared_context_diagnostics"] for event in events(tmp_path)]
    assert available["receipt_status"] == "available"
    assert available["selected"] == []
    assert available["content_digest"] is None
    assert available["omitted"][2]["reason"] == "not_retrieved"
    assert missing["receipt_status"] == "unavailable"
    assert missing["validation_failure"] is None
    assert available["candidate_count"] is missing["candidate_count"] is None


def test_code_paths_and_prompt_payload_are_excluded(tmp_path: Path) -> None:
    payload = response()
    digest = "sha256:" + "a" * 64
    payload["receipt"]["selected"] = [
        {
            "kind": "code",
            "rendered_bytes": 12,
            "truncated": True,
            "code_evidence": {
                "scope_id": "synthetic-scope",
                "fingerprint": "a" * 64,
                "path": "PRIVATE-PATH/secret.py",
                "file_sha256": "a" * 64,
                "start_line": 1,
                "end_line": 3,
                "snippet_sha256": "a" * 64,
            },
        }
    ]
    config = payload["receipt"]["retrieval"]["rerank_configs"][0]
    payload["receipt"]["non_deterministic"] = True
    config.update(
        model="PRIVATE-MODEL",
        non_deterministic=True,
        prompt={
            "compiled_digest": digest,
            "payload": "PRIVATE-PROMPT",
            "scope_id": "synthetic-scope",
            "key": "private",
            "definition_version": "1",
            "builtin_version": "1",
            "selection": "built_in",
            "selected_version": "1",
            "artifact_address": None,
        },
    )
    adapter = memory(tmp_path, Runtime(payload))
    adapter.query("question")
    diagnostics = events(tmp_path)[0]["prepared_context_diagnostics"]
    assert diagnostics["receipt_status"] == "available"
    assert "path" not in diagnostics["selected"][0]["code_evidence"]
    assert diagnostics["retrieval"]["rerank_configs"][0]["prompt_digest"] == digest
    assert "PRIVATE" not in json.dumps(diagnostics)


def test_local_projection_limit_uses_utf8_bytes_and_drops_whole_diagnostic(tmp_path: Path) -> None:
    payload = response()
    item = payload["receipt"]["selected"][0]
    item["artifact_address"]["scope_id"] = "界" * 256
    payload["receipt"]["selected"] = [item] * 16
    adapter = memory(tmp_path, Runtime(payload))
    adapter.query("question")
    diagnostics = events(tmp_path)[0]["prepared_context_diagnostics"]
    assert diagnostics["receipt_status"] == "invalid"
    assert diagnostics["validation_failure"] == "local_size_limit"
    assert "selected" not in diagnostics


def test_concurrent_queries_keep_context_receipt_and_invocation_together(tmp_path: Path) -> None:
    barrier = Barrier(2)

    class ConcurrentRuntime(Runtime):
        def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            barrier.wait(timeout=5)
            content = str(payload["query"])
            result = response(content)
            result["receipt"]["selected"][0]["artifact_address"]["artifact"]["artifact_id"] = content
            return result

    adapter = memory(tmp_path, ConcurrentRuntime(response()))

    def query(identifier: str) -> list[dict[str, str]]:
        adapter.set_query_context(query_invocation_id=identifier)
        try:
            return adapter.query(identifier)
        finally:
            adapter.clear_query_context()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(query, ("first", "second"))) == [
            [{"type": "text", "value": "first"}],
            [{"type": "text", "value": "second"}],
        ]
    for event in events(tmp_path):
        identifier = event["query_invocation_id"]
        diagnostics = event["prepared_context_diagnostics"]
        assert diagnostics["selected"][0]["artifact_address"]["artifact"]["artifact_id"] == identifier
        assert diagnostics["content_digest"] == "sha256:" + hashlib.sha256(identifier.encode()).hexdigest()
        assert event["timings_ms"]["total"] >= 0


@pytest.mark.parametrize("existing", [AUDIT_SCHEMA, "unknown", None])
def test_existing_audit_cannot_mix_schemas_or_be_rewritten(tmp_path: Path, existing: str | None) -> None:
    path = tmp_path / "audit.jsonl"
    original = json.dumps({"schema": existing, "operation": "query"}) + "\n"
    path.write_text(original)
    with pytest.raises(PowerContextMemoryAdapterError, match="new run directory"):
        memory(tmp_path)
    assert path.read_text() == original


def test_schema_is_instance_local_and_reserves_empty_run_before_writes(tmp_path: Path) -> None:
    runtime = Runtime(response())
    v2 = memory(tmp_path / "v2", runtime)
    v1 = memory(tmp_path / "v1", runtime, receipt_diagnostics=False)
    with pytest.raises(PowerContextMemoryAdapterError, match="new run directory"):
        memory(tmp_path / "v2", receipt_diagnostics=False)
    v2.query("question")
    v1.query("question")
    assert events(tmp_path / "v2")[0]["schema"] == RECEIPT_AUDIT_SCHEMA
    assert events(tmp_path / "v1")[0]["schema"] == AUDIT_SCHEMA


def test_old_server_422_stays_request_failure_without_retry_or_missing_receipt(tmp_path: Path) -> None:
    adapter = memory(tmp_path)
    with patch("powercontext_eval_longmemeval_v2.adapter.urlopen") as send:
        send.side_effect = HTTPError("http://127.0.0.1/v1/context/prepare", 422, "unsupported", {}, None)
        with pytest.raises(PowerContextMemoryAdapterError, match="HTTP 422"):
            adapter.query("question")
        assert send.call_count == 1
    [event] = events(tmp_path)
    assert event["status"] == "failed"
    assert event["failure_type"] == "PowerContextMemoryAdapterError"
    assert "prepared_context_diagnostics" not in event


def test_receipt_does_not_weaken_prepared_content_validation(tmp_path: Path) -> None:
    payload = response()
    payload["content_bytes"] = 1
    adapter = memory(tmp_path, Runtime(payload))
    with pytest.raises(PowerContextMemoryAdapterError, match="UTF-8"):
        adapter.query("question")
    [event] = events(tmp_path)
    assert event["status"] == "failed"
    assert "prepared_context_diagnostics" not in event
