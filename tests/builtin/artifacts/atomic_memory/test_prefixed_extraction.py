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

import asyncio
import json
import logging
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.prefixed_extraction import (
    GatewayPrefixedConversation,
    GatewayPrefixedExtractor,
    PrefixedExtractionUnavailable,
    PrefixStart,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError, character_token_estimator
from powercontext.builtin.inference.models import ConversationTurn
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.test_atomic_memory_one_pass import BUDGET_100, BUDGET_200, SCOPE, _Conversation, _facts, _write

# A turn whose tool output makes resending the transcript cost more than reading the cached prefix.
TRANSCRIPT = json.dumps({
    "kind": "gateway_turn",
    "user": "Our team deploys only on Tuesdays. Check the release script.",
    "steps": [
        {"type": "tool_call", "name": "exec", "arguments": '{"cmd": "cat release.sh"}'},
        {"type": "tool_output", "output": "echo release\n" * 2000},
    ],
    "assistant": "Noted.",
})
CANDIDATE = {
    "kind": "constraint",
    "text": "The team deploys only on Tuesdays.",
    "description": "Deployment day",
    "evidence_ids": ["source:1"],
}


def test_scope_routing_metadata_is_not_part_of_the_extraction_contract() -> None:
    value = AtomicMemoryExtractionInput(evidence=(), scope_id="project")

    assert value.scope_id == "project"
    assert value.model_dump(mode="json") == {"evidence": []}
    assert set(AtomicMemoryExtractionInput.model_json_schema()["properties"]) == {"evidence"}
    assert set(AtomicMemoryExtractionInput.model_json_schema(mode="serialization")["properties"]) == {"evidence"}


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

    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self.payload = payload
        self.requests: list[dict[str, object]] = []
        self.request_scopes: list[str | None] = []
        self.source_scope: str | None = None
        # Atomic Memory text in the Scope after the flush.
        self.memories: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gateway/v1/extract"
        assert request.headers["authorization"] == "Bearer gateway-key"
        self.request_scopes.append(request.headers.get("x-powercontext-scope-id"))
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
            prefixed=GatewayPrefixedExtractor(
                client=client, instructions="Extract memories.", estimator=character_token_estimator()
            ),
        ),
    )
    with TestClient(app) as test_client:
        scope_id = gateway.source_scope = _create_source_scope(test_client)
        for source in sources:
            captured = test_client.post("/v1/sources/content", json={"scope_id": scope_id, **source})
            assert captured.status_code == 202, captured.text
        flushed = test_client.post("/v1/memory/flush", json={"scope_id": scope_id})
        assert flushed.status_code == 200, flushed.text
        listed = test_client.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        assert listed.status_code == 200, listed.text
        gateway.memories = [entry["text"] for entry in listed.json()["entries"]]
    return extractor


def _create_source_scope(client: TestClient) -> str:
    response = client.post(
        "/v1/scopes", json={"title": "Project", "summary": "Memory for this project", "idempotency_key": "project"}
    )
    assert response.status_code == 201, response.text
    return cast("str", response.json()["scope_id"])


def _turn(
    source_id: str, handle: str | None = "a" * 32, prefix_tokens: tuple[int, int] | None = (30_000, 50)
) -> dict[str, object]:
    metadata: dict[str, object] = {"kind": "gateway_turn", "target_profile": "codex"}
    if handle is not None:
        metadata["prefix_handle"] = handle
    if prefix_tokens is not None:
        metadata["prefix_input_tokens"], metadata["prefix_output_tokens"] = prefix_tokens
    return {"source_id": source_id, "content": TRANSCRIPT, "metadata": metadata}


def test_a_gateway_turn_is_extracted_over_its_prefix_without_resending_the_transcript(tmp_path, caplog) -> None:
    reply = {"candidates": [CANDIDATE]}
    gateway = _Gateway(200, {"text": json.dumps(reply), "usage": {"input_tokens": 9000, "output_tokens": 40}})
    with caplog.at_level(logging.INFO):
        extractor = _flush(tmp_path, gateway, [_turn("turn-1")])

    assert extractor.calls == 0
    assert gateway.memories == [CANDIDATE["text"]]
    assert gateway.request_scopes == [gateway.source_scope]
    (request,) = gateway.requests
    assert request["handle"] == "a" * 32
    instruction = request["instruction"]
    assert isinstance(instruction, str)
    assert instruction.startswith("Extract memories.")
    assert '"evidence_id": "source:1"' in instruction
    # The conversation comes from the cached prefix: neither the transcript nor gateway metadata is sent again.
    assert "deploys only on Tuesdays" not in instruction
    assert "a" * 32 not in instruction
    assert "prefix_input_tokens" not in instruction
    # Memory injected into the conversation is marked; the instruction excludes it as evidence.
    assert "BEGIN_POWERCONTEXT_<NAME>" in instruction
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
        (400, {"error": "output_limit_unsupported"}, "output_limit_unsupported"),
        (502, {"error": "subscription_auth_failed"}, "credentials_unavailable"),
        (409, {"decision": "withhold", "reason_codes": ["encrypted-origin-unverified"]}, "check_rejected"),
        (502, {"decision": "not_sent", "attempts": 2}, "gateway_error"),
        (
            429,
            {"error": {"message": "Rate limit reached", "type": "rate_limit_error", "code": "rate_limit_exceeded"}},
            "provider_error",
        ),
        (500, ["not", "an", "object"], "gateway_error"),
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
    ("prefix_tokens", "reason"),
    [((10_000_000, 50), "not_cheaper"), ((30_000, 40_000), "not_cheaper"), (None, "prefix_size_unknown")],
    ids=["large-cached-prefix", "large-uncached-output", "unknown-size"],
)
def test_a_prefix_that_would_cost_more_is_not_used(tmp_path, caplog, prefix_tokens, reason) -> None:
    gateway = _Gateway(500, {})
    with caplog.at_level(logging.INFO):
        extractor = _flush(tmp_path, gateway, [_turn("turn-1", prefix_tokens=prefix_tokens)])

    assert gateway.requests == []
    assert extractor.calls == 1
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


def _part(index: int, count: int, *, handle: bool) -> dict[str, object]:
    source = _turn(f"turn-1-{index}of{count}", handle="a" * 32 if handle else None)
    # Each part is a slice of one long turn; together they fit the extraction budget.
    source["content"] = json.dumps({
        "kind": "gateway_turn",
        "part": index,
        "parts": count,
        "steps": [{"type": "tool_output", "output": "echo release\n" * 700}],
    })
    metadata = cast("dict[str, object]", source["metadata"])
    metadata["turn"] = {"source_id": "turn-1", "part": index, "parts": count}
    if not handle:
        del metadata["prefix_input_tokens"], metadata["prefix_output_tokens"]
    return source


def test_a_turn_captured_in_parts_is_extracted_over_the_prefix_of_its_last_part(tmp_path) -> None:
    candidate = {**CANDIDATE, "evidence_ids": ["source:1", "source:3"]}
    gateway = _Gateway(200, {"text": json.dumps({"candidates": [candidate]}), "usage": {}})
    extractor = _flush(
        tmp_path, gateway, [_part(1, 3, handle=False), _part(2, 3, handle=False), _part(3, 3, handle=True)]
    )

    assert extractor.calls == 0
    assert gateway.memories == [CANDIDATE["text"]]
    (request,) = gateway.requests
    assert request["handle"] == "a" * 32
    instruction = request["instruction"]
    assert isinstance(instruction, str)
    for evidence_id in ("source:1", "source:2", "source:3"):
        assert f'"evidence_id": "{evidence_id}"' in instruction


def test_a_window_with_only_some_parts_of_a_turn_uses_normal_extraction(tmp_path) -> None:
    gateway = _Gateway(500, {})
    extractor = _flush(tmp_path, gateway, [_part(2, 3, handle=False), _part(3, 3, handle=True)])

    assert gateway.requests == []
    assert extractor.calls == 1


ENTRIES = {"items": [{"memory": "Deployment day", "details": ["deploys only on Tuesdays"], "query": "deployment day"}]}
OPERATIONS = {
    "operations": [{"action": "create", "target_ids": [], "reason": "No shown memory states it.", **CANDIDATE}]
}


class _ScriptedGateway(_Gateway):
    """The gateway's extraction route answering successive requests from a script of ``(status, payload)``."""

    def __init__(self, *replies: tuple[int, object]) -> None:
        super().__init__(200, None)
        self.replies = list(replies)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.status, self.payload = self.replies.pop(0)
        return super().__call__(request)


def _text(reply: object) -> tuple[int, object]:
    return 200, {"text": json.dumps(reply), "usage": {"input_tokens": 9000, "output_tokens": 40}}


def _flush_one_pass(tmp_path, gateway: _Gateway, source: dict[str, object], *fallback_replies) -> _Conversation:
    """Process one gateway turn in one-pass mode; the returned conversation is the one Core runs without the prefix."""

    conversation = _Conversation(*fallback_replies)
    client = httpx.AsyncClient(
        base_url="http://gateway.local",
        headers={"Authorization": "Bearer gateway-key"},
        transport=httpx.MockTransport(gateway),
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'prefixed.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=(), atomic_memory_processing_mode="one_pass"),
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=AtomicMemoryGenerationPipeline(
            extractor=_Extractor(),
            reconciler=_CreatingReconciler(),
            estimator=character_token_estimator(),
            conversation=conversation,
            prefixed_conversation=GatewayPrefixedConversation(client=client, estimator=character_token_estimator()),
        ),
    )
    with TestClient(app) as test_client:
        scope_id = gateway.source_scope = _create_source_scope(test_client)
        captured = test_client.post("/v1/sources/content", json={"scope_id": scope_id, **source})
        assert captured.status_code == 202, captured.text
        flushed = test_client.post("/v1/memory/flush", json={"scope_id": scope_id})
        assert flushed.status_code == 200, flushed.text
        listed = test_client.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        assert listed.status_code == 200, listed.text
        gateway.memories = [entry["text"] for entry in listed.json()["entries"]]
    return conversation


def _prefixed_one_pass_events(caplog) -> list[tuple[object, object]]:
    return [
        (getattr(record, "outcome", None), getattr(record, "fallback_reason", None))
        for record in caplog.records
        if getattr(record, "event", None) == "atomic_memory.prefixed_extraction"
        and getattr(record, "operation", None) == "atomic_memory.one_pass"
    ]


def test_a_one_pass_window_continues_the_prefix_through_every_step(tmp_path, caplog) -> None:
    gateway = _ScriptedGateway(_text(ENTRIES), _text(OPERATIONS))
    with caplog.at_level(logging.INFO):
        conversation = _flush_one_pass(tmp_path, gateway, _turn("turn-1", prefix_tokens=(10_000, 50)))

    assert conversation.turns == []
    assert gateway.memories == [CANDIDATE["text"]]
    assert gateway.request_scopes == [gateway.source_scope, gateway.source_scope]
    first, second = gateway.requests
    instruction = first["instruction"]
    assert isinstance(instruction, str)
    assert first["handle"] == second["handle"] == "a" * 32
    assert "continuation" not in first
    # The evidence is the prefix: the instruction lists the Source but does not resend its transcript.
    assert '"evidence_id": "source:1"' in instruction
    assert "deploys only on Tuesdays" not in instruction
    assert "prefix_input_tokens" not in instruction
    assert "Your reply to this message is step 1." in instruction
    # Step 2 resends the same instruction, then the step 1 reply and the recalled memories.
    assert second["instruction"] == instruction
    reply, memories = cast("list[dict[str, str]]", second["continuation"])
    assert reply == {"role": "assistant", "text": json.dumps(ENTRIES)}
    assert memories["role"] == "user"
    assert _prefixed_one_pass_events(caplog) == [("success", None)]


def test_a_rejected_one_pass_reply_is_corrected_after_the_same_prefix(tmp_path) -> None:
    unknown_label = {"operations": [{**OPERATIONS["operations"][0], "action": "revise", "target_ids": ["M9"]}]}
    gateway = _ScriptedGateway(_text(ENTRIES), _text(unknown_label), _text(OPERATIONS))
    _flush_one_pass(tmp_path, gateway, _turn("turn-1", prefix_tokens=(10_000, 50)))

    assert gateway.memories == [CANDIDATE["text"]]
    continuation = cast("list[dict[str, str]]", gateway.requests[2]["continuation"])
    assert [message["role"] for message in continuation] == ["assistant", "user", "assistant", "user"]
    assert continuation[2]["text"] == json.dumps(unknown_label)
    assert "Fix the errors and try again." in continuation[3]["text"]


@pytest.mark.parametrize(
    ("replies", "reason"),
    [
        ([(404, {"error": "prefix_unavailable"})], "prefix_unavailable"),
        # The prefix expires between the steps: nothing of the prefixed conversation is kept.
        ([_text(ENTRIES), (404, {"error": "prefix_unavailable"})], "prefix_unavailable"),
        ([_text(ENTRIES), (422, "Failed to deserialize the JSON body")], "gateway_error"),
        ([_text({"candidates": []})] * 3, "invalid_output"),
        # An empty reply cannot be sent back for correction, so the window starts again at once.
        ([(200, {"text": " ", "usage": {"input_tokens": 9000, "output_tokens": 0}})], "invalid_output"),
    ],
)
def test_a_failed_prefixed_one_pass_window_starts_again_without_the_prefix(tmp_path, caplog, replies, reason) -> None:
    gateway = _ScriptedGateway(*replies)
    with caplog.at_level(logging.INFO):
        conversation = _flush_one_pass(
            tmp_path, gateway, _turn("turn-1", prefix_tokens=(10_000, 50)), ENTRIES, OPERATIONS
        )

    assert gateway.replies == []
    assert len(conversation.turns) == 2
    # Core sends the evidence itself this time.
    assert "deploys only on Tuesdays" in conversation.turns[0].message
    assert gateway.memories == [CANDIDATE["text"]]
    assert _prefixed_one_pass_events(caplog) == [("fallback", reason)]


@pytest.mark.parametrize(("prefix_tokens", "reason"), [((400_000, 50), "not_cheaper"), (None, "prefix_size_unknown")])
def test_a_one_pass_prefix_that_would_cost_more_is_not_used(tmp_path, caplog, prefix_tokens, reason) -> None:
    gateway = _ScriptedGateway()
    with caplog.at_level(logging.INFO):
        conversation = _flush_one_pass(
            tmp_path, gateway, _turn("turn-1", prefix_tokens=prefix_tokens), ENTRIES, OPERATIONS
        )

    assert gateway.requests == []
    assert len(conversation.turns) == 2
    assert gateway.memories == [CANDIDATE["text"]]
    assert _prefixed_one_pass_events(caplog) == [("fallback", reason)]


@pytest.mark.parametrize("failure", [(404, {"error": "prefix_unavailable"}), httpx.ReadTimeout("timed out")])
def test_a_failed_correction_keeps_the_usage_already_received(failure) -> None:
    replies = [_text(ENTRIES), failure]

    def gateway(request: httpx.Request) -> httpx.Response:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(reply[0], json=reply[1])

    def reject(_: str) -> None:
        raise InvalidInferenceOutputError("atomic-memory-one-pass", "the reply breaks a rule")

    async def run() -> None:
        async with httpx.AsyncClient(base_url="http://gateway.local", transport=httpx.MockTransport(gateway)) as client:
            conversation = GatewayPrefixedConversation(client=client, estimator=character_token_estimator())
            start = PrefixStart("a" * 32, cached_tokens=10_000, uncached_tokens=50, normal_input_tokens=100_000)
            await conversation.generate(
                ConversationTurn(instructions="rules", message="records", check=reject, history=(start,))
            )

    with pytest.raises(PrefixedExtractionUnavailable) as raised:
        asyncio.run(run())

    assert replies == []
    usage = raised.value.usage
    assert usage is not None
    assert (usage.requests, usage.input_tokens, usage.output_tokens) == (1, 9000, 40)


def _run_one_pass(tmp_path, gateway: _Gateway, scenario, *, input_tokens_limit: int = 24_000) -> _Conversation:
    """Run ``scenario`` in one-pass mode over a Scope whose gateway turns carry a short prefix."""

    conversation = _Conversation()
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'prefixed.db'}"),
        runtime=RuntimeConfig(
            atomic_memory_related_mode="fts",
            atomic_memory_processing_mode="one_pass",
            atomic_memory_input_tokens_limit=input_tokens_limit,
        ),
    )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="http://gateway.local",
            headers={"Authorization": "Bearer gateway-key"},
            transport=httpx.MockTransport(gateway),
        ) as client:
            pipeline = AtomicMemoryGenerationPipeline(
                extractor=_Extractor(),
                reconciler=_CreatingReconciler(),
                estimator=character_token_estimator(),
                conversation=conversation,
                prefixed_conversation=GatewayPrefixedConversation(client=client, estimator=character_token_estimator()),
            )
            async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
                await contexts.get(SCOPE)
                await scenario(contexts)

    asyncio.run(run())
    return conversation


async def _capture_turn(contexts) -> None:
    turn = _turn("turn-1", prefix_tokens=(100, 50))
    content = json.dumps({"kind": "gateway_turn", "user": " ".join(["Raise the rollout budget."] * 160)})
    await contexts.records.capture_source(SCOPE, "content", turn["source_id"], content, turn["metadata"])


def _request_estimate(request: dict[str, object]) -> int:
    continuation = cast("list[dict[str, str]]", request.get("continuation", []))
    texts = (cast("str", request["instruction"]), *(message["text"] for message in continuation))
    return character_token_estimator().estimate("\n".join(texts))


def test_a_prefixed_window_over_the_step_two_budget_is_prepared_per_candidate(tmp_path, caplog) -> None:
    gateway = _ScriptedGateway(_text(_facts("rollout budget payments")))
    detail = " ".join(f"condition{index}" for index in range(300))

    async def scenario(contexts) -> None:
        await contexts.records.create_atomic_memories(
            SCOPE, tuple({"kind": "fact", "text": f"{BUDGET_100} Case {index}: {detail}."} for index in range(5))
        )
        await contexts.process_memory(SCOPE, 5)
        await _capture_turn(contexts)
        with caplog.at_level(logging.INFO):
            assert (await contexts.process_memory(SCOPE, 1)).current_cursor == 6

    # The turn fits this budget sent either way; step 1 plus the five memories it recalled does not.
    conversation = _run_one_pass(tmp_path, gateway, scenario, input_tokens_limit=5500)

    # Step 1 ran after the prefix and step 2 was never sent, with or without the prefix.
    assert len(gateway.requests) == 1
    assert conversation.turns == []
    assert _prefixed_one_pass_events(caplog) == []
    assert [
        getattr(record, "fallback_reason", None)
        for record in caplog.records
        if getattr(record, "event", None) == "atomic_memory.one_pass"
    ] == ["operations_input_over_budget"]


def test_a_prefixed_recheck_over_its_budget_does_not_restart_the_window(tmp_path, caplog) -> None:
    # The long step 2 reply is resent by the recheck, which makes it the one request over the budget.
    operation = {**_write("create", BUDGET_200), "reason": " ".join(["Keep the new rollout budget."] * 500)}

    def gateway() -> _ScriptedGateway:
        recheck = {"operations": [{**_write("create", BUDGET_200), "id": "C1"}]}
        return _ScriptedGateway(_text(_facts("unrelated words")), _text({"operations": [operation]}), _text(recheck))

    async def prepare(contexts) -> None:
        await contexts.records.create_atomic_memories(SCOPE, ({"kind": "fact", "text": BUDGET_100},))
        await contexts.process_memory(SCOPE, 1)
        await _capture_turn(contexts)

    measured = gateway()

    async def measure(contexts) -> None:
        await prepare(contexts)
        await contexts.process_memory(SCOPE, 1)

    (tmp_path / "measure").mkdir()
    _run_one_pass(tmp_path / "measure", measured, measure)
    step_two, recheck = (_request_estimate(request) for request in measured.requests[1:])
    limited = gateway()

    async def scenario(contexts) -> None:
        await prepare(contexts)
        with caplog.at_level(logging.INFO), pytest.raises(InvalidInferenceOutputError) as captured:
            await contexts.process_memory(SCOPE, 1)
        assert captured.value.operation == "atomic-memory-input-budget"

    conversation = _run_one_pass(tmp_path, limited, scenario, input_tokens_limit=(step_two + recheck) // 2)

    # The recheck was not sent, and the window did not start again with a larger input.
    assert len(limited.requests) == 2
    assert conversation.turns == []
    assert _prefixed_one_pass_events(caplog) == []
