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

"""HTTP and SDK acceptance for the optional, content-free prepare receipt."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.memory.canonical import normalize_query
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.runtime import PrepareContextRequest as RuntimePrepareContextRequest
from powercontext.builtin.runtime.receipt_builder import ReceiptEvidence
from powercontext.builtin.scope import ScopeDraft
from powercontext.http import (
    AtomicMemoryLifecycleRequest,
    CreateScopeRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    ReplaceArtifactRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.test_context_text_assembly import _server

_FOUR = {"schema", "status", "content", "content_bytes"}


@pytest.mark.parametrize("backend", ["sqlite", "oceanbase"])
def test_http_and_sdk_receipt_matches_injected_memory_and_empty_query(tmp_path, backend):
    if backend == "oceanbase":
        url = os.environ.get("POWERCONTEXT_TEST_RECEIPT_OCEANBASE_URL")
        if url is None:
            pytest.skip("real OceanBase receipt acceptance not configured")
        database = OceanBaseConfig(url=SecretStr(url))
    else:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'assembly.db'}")

    async def scenario():
        async with _server(tmp_path, database=database) as (_, transport, client):
            scope = await client.create_scope(
                CreateScopeRequest(title="Receipt", summary="Exact selection", idempotency_key="receipt")
            )
            scope_id = scope.scope_id
            remembered = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind="fact", text="The budget review is on Thursday.")
            )
            query = "budget review"
            base = {"scope_id": scope_id, "query": query}
            ordinary = await transport.post("/v1/context/prepare", json=base)
            declined = await transport.post("/v1/context/prepare", json={**base, "include_receipt": False})
            null = await transport.post("/v1/context/prepare", json={**base, "include_receipt": None})
            opted = await transport.post("/v1/context/prepare", json={**base, "include_receipt": True})
            assert all(item.status_code == 200 for item in (ordinary, declined, null, opted))
            assert all(set(item.json()) == _FOUR for item in (ordinary, declined, null))
            assert set(opted.json()) == _FOUR | {"receipt"}
            assert {key: value for key, value in opted.json().items() if key != "receipt"} == ordinary.json()
            receipt = opted.json()["receipt"]
            assert receipt["used_bytes"] == opted.json()["content_bytes"]
            assert (
                receipt["content_digest"]
                == "sha256:" + hashlib.sha256(opted.json()["content"].encode("utf-8")).hexdigest()
            )
            assert receipt["selected"][0]["artifact_address"] == {
                "scope_id": scope_id,
                "artifact": remembered.records[0].artifact.model_dump(mode="json"),
            }
            assert receipt["selected"][0]["kind"] == "atomic-memory"
            assert "code_evidence" not in receipt["selected"][0]
            assert receipt["retrieval"]["rounds"] == [{"round": 0, "outcome": "completed", "retained": True}]
            assert any(
                group["family"] == "atomic-memory" and group["mode"] == "fts" and group["outcome"] == "completed"
                for group in receipt["retrieval"]["searches"]
            )
            experience_only = await transport.post(
                "/v1/context/prepare",
                json={
                    **base,
                    "assembly": {"sections": [{"family": "experience", "limit": 1}]},
                    "include_receipt": True,
                },
            )
            assert experience_only.status_code == 200
            experience_searches = experience_only.json()["receipt"]["retrieval"]["searches"]
            assert all(group["family"] != "atomic-memory" for group in experience_searches)
            assert any(group["family"] == "experience" for group in experience_searches)
            assert len(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 8192
            parsed = await client.prepare_context(
                PrepareContextRequest(scope_id=scope_id, query=query, include_receipt=True)
            )
            assert parsed.receipt is not None
            assert parsed.receipt.selected[0].artifact_address.scope_id == scope_id

            large_query = "界" * 2731
            empty = await client.prepare_context(
                PrepareContextRequest(scope_id=scope_id, query=large_query, include_receipt=True)
            )
            assert empty.status.value == "empty"
            assert empty.receipt is not None
            assert empty.receipt.content_digest is None
            assert (
                empty.receipt.query_digest
                == "sha256:" + hashlib.sha256(normalize_query(large_query).encode("utf-8")).hexdigest()
            )
            assert any(
                item.family.value == "atomic-memory" and item.reason.value == "not_retrieved"
                for item in empty.receipt.omitted
            )
            ref = receipt["selected"][0]["artifact_address"]["artifact"]
            original = await client.get_artifact_revision(scope_id, ref["family"], ref["artifact_id"], ref["revision"])
            assert original.content["text"] == remembered.records[0].text
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{ref['artifact_id']}"
            head = await transport.get(path)
            revised = await client.replace_artifact(
                scope_id,
                ref["family"],
                ref["artifact_id"],
                ReplaceArtifactRequest.model_validate({
                    "content": {"kind": "fact", "text": "The budget review moved to Friday."}
                }),
                expected_etag=head.headers["ETag"],
            )
            assert revised.revision > ref["revision"]
            state = await client.get_atomic_memory_state(scope_id, ref["artifact_id"])
            await client.change_atomic_memory_lifecycle(
                AtomicMemoryLifecycleRequest.model_validate({
                    "scope_id": scope_id,
                    "target": {
                        "artifact": state.artifact.model_dump(mode="json"),
                        "state_version": state.state_version,
                    },
                    "state": "forgotten",
                })
            )
            historical = await client.get_artifact_revision(
                scope_id, ref["family"], ref["artifact_id"], ref["revision"]
            )
            assert historical.content == original.content

    asyncio.run(scenario())


@pytest.mark.parametrize("declared", [False, True, "unsafe", "incomplete", "timeout", "unknown-fallback"])
def test_injected_reranker_needs_actual_configuration_evidence(tmp_path, declared):
    class Reranker:
        policy_id = "test.select-first.v1"
        supports_atomic_memory = True

        async def rerank(self, query, candidates, limit, /):
            return MemoryRerankDecision(
                selected_ranks=(1,),
                usage=InferenceUsage(requests=1),
                used_fallback=declared in {"timeout", "unknown-fallback"},
                fallback_reason="inference_timeout" if declared == "timeout" else None,
            )

        if declared:

            def receipt_configuration(self):
                return {
                    "policy_id": self.policy_id,
                    "model": None,
                    "effective_settings": (
                        {}
                        if declared == "incomplete"
                        else {
                            "algorithm": "select-first",
                            "algorithm_version": "1",
                            **({"tool_choice": "private-token"} if declared == "unsafe" else {}),
                        }
                    ),
                    "timeout_seconds": 5.0,
                    "max_requests": 1,
                }

    async def scenario():
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rerank.db'}")
        async with open_builtin_runtime(BuiltinConfig(database=database), memory_reranker=Reranker()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Rerank", summary="Receipt evidence", idempotency_key="rerank")
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="fact", text="Budget review is on Thursday."),
                AtomicMemoryContent(kind="fact", text="Budget review has two stages."),
            ))
            plain = await runtime.context.for_scope(scope.scope_id).prepare(
                RuntimePrepareContextRequest(query="budget review")
            )
            prepared = await runtime.context.for_scope(scope.scope_id).prepare(
                RuntimePrepareContextRequest(query="budget review", include_receipt=True)
            )
            assert plain.content == prepared.content
            if declared is True or declared == "timeout":
                assert prepared.receipt is not None
                (config,) = prepared.receipt.retrieval.rerank_configs
                assert config.policy_id == "test.select-first.v1"
                assert config.model is None and config.prompt is None
                assert config.config_id == config.config_digest
                assert prepared.receipt.non_deterministic is False
                assert config.effective_settings["algorithm"] == "select-first"
                assert prepared.receipt.retrieval.reranks[0].outcome == (
                    "fallback" if declared == "timeout" else "selected"
                )
                if declared == "timeout":
                    assert prepared.receipt.retrieval.reranks[0].fallback_reason == "inference_timeout"
                else:
                    assert any(
                        item.family == "atomic-memory" and item.reason == "rerank_not_selected" and item.count == 1
                        for item in prepared.receipt.omitted
                    )
            else:
                assert prepared.receipt is None

    asyncio.run(scenario())


def test_receipt_failure_and_byte_overflow_leave_prepare_unchanged(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (_, transport, client):
            scope = await client.create_scope(
                CreateScopeRequest(title="Fallback", summary="Receipt failure", idempotency_key="fallback")
            )
            scope_id = scope.scope_id
            await client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind="fact", text="The budget review is on Thursday.")
            )
            base = {"scope_id": scope_id, "query": "budget review"}
            expected = (await transport.post("/v1/context/prepare", json=base)).json()
            original_build = ReceiptEvidence.build
            monkeypatch.setattr(ReceiptEvidence, "build", lambda *_: (_ for _ in ()).throw(ValueError("failed")))
            failed = await transport.post("/v1/context/prepare", json={**base, "include_receipt": True})
            assert failed.status_code == 200
            assert failed.json() == expected

            def oversized(self, request, context, build, scope_id):
                self.stage("x" * 9000, 1.0)
                return original_build(self, request, context, build, scope_id)

            monkeypatch.setattr(ReceiptEvidence, "build", oversized)
            overflow = await transport.post("/v1/context/prepare", json={**base, "include_receipt": True})
            assert overflow.status_code == 200
            assert overflow.json() == expected

    asyncio.run(scenario())


def test_builtin_model_rerank_captures_effective_settings_and_bound_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pydantic_ai.models.infer_model",
        lambda model, **_kwargs: (
            model
            if isinstance(model, Model)
            else TestModel(
                custom_output_text='{"selected_ranks":[1]}',
                settings={"temperature": 0.4},
            )
        ),
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'model.db'}"),
            runtime=RuntimeConfig(memory_rerank_enabled=True),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        ),
    )
    with TestClient(app) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": scope_id, "kind": "fact", "text": "Budget review is on Thursday."},
        )
        assert remembered.status_code == 200
        response = client.post(
            "/v1/context/prepare", json={"scope_id": scope_id, "query": "budget review", "include_receipt": True}
        )
    assert response.status_code == 200, response.text
    receipt = response.json().get("receipt")
    assert receipt is not None
    (config,) = receipt["retrieval"]["rerank_configs"]
    assert config["model"] is not None
    assert config["effective_settings"]["temperature"] == 0.0
    assert config["prompt"]["scope_id"] == scope_id
    assert config["prompt"]["key"] == "memory.rerank"
    assert config["non_deterministic"] and receipt["non_deterministic"]
    assert receipt["retrieval"]["reranks"][0]["config_id"] == config["config_digest"]


def test_empty_rerank_fallback_accounts_for_candidates_dropped_before_assembly(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pydantic_ai.models.infer_model",
        lambda _model, **_kwargs: TestModel(custom_output_text='{"selected_ranks":[]}'),
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rerank-fallback.db'}"),
            runtime=RuntimeConfig(memory_rerank_enabled=True),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        ),
    )
    with TestClient(app) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        for index in range(30):
            remembered = client.post(
                "/v1/memory/remember",
                json={
                    "scope_id": scope_id,
                    "kind": "fact",
                    "text": f"Budget review evidence {index:02d}.",
                },
            )
            assert remembered.status_code == 200, remembered.text

        response = client.post(
            "/v1/context/prepare",
            json={
                "scope_id": scope_id,
                "query": "budget review",
                "max_bytes": 32768,
                "assembly": {"sections": [{"family": "memory", "limit": 8}]},
                "include_receipt": True,
            },
        )

    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    (rerank,) = receipt["retrieval"]["reranks"]
    assert rerank["outcome"] == "fallback"
    assert rerank["fallback_reason"] == "empty_selection"
    assert len(receipt["selected"]) == 8
    omitted = {(item["family"], item["reason"]): item["count"] for item in receipt["omitted"]}
    assert omitted[("atomic-memory", "rerank_not_selected")] == 14
    assert omitted[("atomic-memory", "family_limit")] == 8
