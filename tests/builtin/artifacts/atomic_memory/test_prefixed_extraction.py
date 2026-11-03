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

"""Atomic Memory extraction over a gateway turn's cached prefix, with fallback to normal extraction."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.prefixed_extraction import GatewayPrefixedExtractor
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

TRANSCRIPT = json.dumps({"kind": "gateway_turn", "user": "Our team deploys only on Tuesdays.", "assistant": "Noted."})
CANDIDATE = {
    "kind": "constraint",
    "text": "The team deploys only on Tuesdays.",
    "description": "Deployment day",
    "evidence_ids": ["source:1"],
}


class _Extractor:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        self.calls += 1
        return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=()))


class _CreatingReconciler:
    async def generate(
        self, request: AtomicMemoryReconciliationInput, /
    ) -> GenerationResult[AtomicMemoryReconciliationOutput]:
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create",
                compared_ids=tuple(item.item_id for item in request.related),
                content=AtomicMemoryReconciliationContent(
                    kind=request.proposal.kind, text=request.proposal.text, description="Deployment day"
                ),
                evidence_ids=request.proposal.evidence_ids,
                reason="Keep the extracted candidate.",
            )
        )


class _Gateway:
    """The gateway's extraction route: records requests and returns a scripted reply."""

    def __init__(self, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self.payload = payload
        self.requests: list[dict[str, object]] = []
        # Atomic Memory text in the Scope after the flush.
        self.memories: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gateway/v1/extract"
        assert request.headers["authorization"] == "Bearer gateway-key"
        self.requests.append(json.loads(request.content))
        return httpx.Response(self.status, json=self.payload)


def _flush(tmp_path, gateway: _Gateway, sources: list[dict[str, object]]) -> _Extractor:
    extractor = _Extractor()
    client = httpx.AsyncClient(
        base_url="http://gateway.local",
        headers={"Authorization": "Bearer gateway-key"},
        transport=httpx.MockTransport(gateway),
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'prefixed.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=AtomicMemoryGenerationPipeline(
            extractor=extractor,
            reconciler=_CreatingReconciler(),
            estimator=character_token_estimator(),
            prefixed=GatewayPrefixedExtractor(client=client, instructions="Extract memories."),
        ),
    )
    with TestClient(app) as test_client:
        scope_id = test_client.get("/v1/scopes/default").json()["scope_id"]
        for source in sources:
            captured = test_client.post("/v1/sources/content", json={"scope_id": scope_id, **source})
            assert captured.status_code == 202, captured.text
        flushed = test_client.post("/v1/memory/flush", json={"scope_id": scope_id})
        assert flushed.status_code == 200, flushed.text
        listed = test_client.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        assert listed.status_code == 200, listed.text
        gateway.memories = [entry["text"] for entry in listed.json()["entries"]]
    return extractor


def _turn(source_id: str, handle: str | None = "a" * 32) -> dict[str, object]:
    metadata: dict[str, object] = {"kind": "gateway_turn", "target_profile": "codex"}
    if handle is not None:
        metadata["prefix_handle"] = handle
    return {"source_id": source_id, "content": TRANSCRIPT, "metadata": metadata}


def test_a_gateway_turn_is_extracted_over_its_prefix_without_resending_the_transcript(tmp_path, caplog) -> None:
    reply = {"candidates": [CANDIDATE]}
    gateway = _Gateway(200, {"text": json.dumps(reply), "usage": {"input_tokens": 9000, "output_tokens": 40}})
    with caplog.at_level(logging.INFO):
        extractor = _flush(tmp_path, gateway, [_turn("turn-1")])

    assert extractor.calls == 0
    assert gateway.memories == [CANDIDATE["text"]]
    (request,) = gateway.requests
    assert request["handle"] == "a" * 32
    instruction = request["instruction"]
    assert isinstance(instruction, str)
    assert instruction.startswith("Extract memories.")
    assert '"evidence_id": "source:1"' in instruction
    # The conversation comes from the cached prefix: neither the transcript nor the handle is sent again.
    assert "deploys only on Tuesdays" not in instruction
    assert "a" * 32 not in instruction
    assert any(
        getattr(record, "event", None) == "atomic_memory.prefixed_extraction"
        and getattr(record, "outcome", None) == "success"
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("status", "payload", "reason"),
    [
        (404, {"error": "prefix_unavailable"}, "prefix_unavailable"),
        (409, {"error": "target_mismatch"}, "target_mismatch"),
        (409, {"decision": "withhold", "reason_codes": ["encrypted-origin-unverified"]}, "check_rejected"),
        (502, {"decision": "not_sent", "attempts": 2}, "gateway_error"),
        (200, {"text": "These are the memories: none."}, "invalid_output"),
        (200, {"text": json.dumps({"candidates": [{**CANDIDATE, "evidence_ids": ["source:9"]}]})}, "invalid_output"),
    ],
)
def test_a_failed_prefixed_extraction_falls_back_with_its_reason(tmp_path, caplog, status, payload, reason) -> None:
    gateway = _Gateway(status, payload)
    with caplog.at_level(logging.INFO):
        extractor = _flush(tmp_path, gateway, [_turn("turn-1")])

    assert len(gateway.requests) == 1
    assert extractor.calls == 1
    assert gateway.memories == []
    assert [
        getattr(record, "fallback_reason", None)
        for record in caplog.records
        if getattr(record, "event", None) == "atomic_memory.prefixed_extraction"
    ] == [reason]


@pytest.mark.parametrize(
    "sources",
    [
        [_turn("turn-1", handle=None)],
        [_turn("turn-1"), _turn("turn-2", handle="b" * 32)],
    ],
    ids=["no-handle", "several-sources"],
)
def test_windows_without_a_single_prefixed_turn_use_normal_extraction(tmp_path, sources) -> None:
    gateway = _Gateway(500, {})
    extractor = _flush(tmp_path, gateway, sources)

    assert gateway.requests == []
    assert extractor.calls == 1
