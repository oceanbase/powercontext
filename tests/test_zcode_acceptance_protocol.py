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

"""The acceptance fault withholds an actual app receipt, independently of client timer scheduling."""

import asyncio
import json

import httpx
import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryComparisonItem,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
    validate_reconciliation_output,
)
from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator
from powercontext.sources import SourceRef

from .e2e.zcode_acceptance.protocol import (
    CaptureResponseGate,
    HostModelFixture,
    ObserveServer,
    WireEvidence,
    decode_response,
)


@pytest.mark.parametrize("streamed", [False, True], ids=["json", "sse"])
def test_wire_decoder_preserves_json_rpc_errors(streamed):
    response = {"jsonrpc": "2.0", "id": 7, "error": {"code": -32603, "message": "Synthetic tool failure"}}
    encoded = json.dumps(response)
    raw = ("event: message\ndata: " + encoded + "\n\n") if streamed else encoded

    assert decode_response(raw.encode()) == response


@pytest.mark.parametrize(
    "response",
    [
        {"result": {"isError": False}},
        {"result": {"isError": True}},
        {"error": {"code": -32603, "message": "Synthetic tool failure"}},
    ],
)
def test_wire_tool_calls_exclude_redirects_and_preserve_matching_rpc_errors(response):
    wire = WireEvidence()
    request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "replace_artifact"}}
    redirect = {"path": "/mcp", "status": 307, "request": request, "response": {}}
    unrelated = {
        "path": "/mcp/",
        "status": 200,
        "request": request,
        "response": {"jsonrpc": "2.0", "id": 8, **response},
    }
    executed = {
        "path": "/mcp/",
        "status": 200,
        "request": request,
        "response": {"jsonrpc": "2.0", "id": 7, **response},
    }
    wire.records.extend([redirect, unrelated, executed])

    assert wire.calls("replace_artifact") == [executed]


@pytest.mark.parametrize("tool", ["pc_work_contract", "Bash", "mcp__powercontext__create_work_contract"])
def test_controlled_model_drives_native_catalog_names(tool: str) -> None:
    async def check() -> None:
        model = HostModelFixture()
        operation = "create_work_contract" if tool.startswith("mcp__") else tool
        model.plan([(operation, {"source_id": "synthetic-contract"})])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=model.app), base_url="http://fixture") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "fixture",
                    "messages": [],
                    "tools": [{"type": "function", "function": {"name": tool}}],
                },
            )
        response.raise_for_status()
        call = response.json()["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == tool
        assert json.loads(call["function"]["arguments"]) == {"source_id": "synthetic-contract"}

    asyncio.run(check())


def capture_app(writes: list[str], *, reject: bool = False) -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] == "/health/ready":
            status, body = 200, {"ready": True}
        else:
            request = json.loads((await receive())["body"])
            if reject:
                status, body = 422, {"error": "invalid_request"}
            else:
                writes.append(request["content"])
                status, body = (
                    202,
                    {
                        "status": "accepted",
                        "source": {"name": "content", "source_id": "source-" + str(len(writes))},
                        "position": len(writes),
                    },
                )
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": json.dumps(body).encode()})

    return app


async def exchange(
    observer: ObserveServer, content: str, sent: list[Message], *, path: str = "/v1/sources/content"
) -> None:
    async def receive() -> Message:
        return {"type": "http.request", "body": json.dumps({"content": content}).encode()}

    async def send(message: Message) -> None:
        sent.append(message)

    await observer({"type": "http", "path": path}, receive, send)


def test_capture_gate_holds_only_target_accepted_response_until_release():
    async def check():
        writes = []
        wire = WireEvidence()
        observer = ObserveServer(capture_app(writes), wire)
        gate = CaptureResponseGate("target capture")
        observer.capture_response_gate = gate
        target_sent = []
        target = asyncio.create_task(exchange(observer, gate.content, target_sent))
        try:
            assert await asyncio.to_thread(gate.accepted.wait, 2)
            assert writes == [gate.content]
            assert wire.records[0]["status"] == 202
            assert target_sent == []
            assert not target.done()

            health_sent, other_sent = [], []
            await asyncio.wait_for(exchange(observer, "", health_sent, path="/health/ready"), 2)
            await asyncio.wait_for(exchange(observer, "other capture", other_sent), 2)
            assert health_sent[0]["status"] == 200
            assert other_sent[0]["status"] == 202
            assert writes == [gate.content, "other capture"]
            assert target_sent == []
            assert gate.evidence()["accepted_receipt"] == {
                "source": {"name": "content", "source_id": "source-1"},
                "position": 1,
            }
        finally:
            gate.release()
            await asyncio.wait_for(target, 2)
        assert target_sent[0]["status"] == 202
        assert json.loads(target_sent[1]["body"])["source"]["source_id"] == "source-1"
        assert not gate.release_timed_out

    asyncio.run(check())


def test_capture_gate_does_not_hold_a_rejected_target_request():
    gate = CaptureResponseGate("target capture")
    observer = ObserveServer(capture_app([], reject=True), WireEvidence())
    observer.capture_response_gate = gate
    sent = []
    asyncio.run(asyncio.wait_for(exchange(observer, gate.content, sent), 2))
    assert sent[0]["status"] == 422
    assert not gate.accepted.is_set()


def test_capture_gate_has_bounded_failure_without_release():
    gate = CaptureResponseGate("target capture", release_timeout=0.01)
    observer = ObserveServer(capture_app([]), WireEvidence())
    observer.capture_response_gate = gate
    sent = []
    with pytest.raises(AssertionError, match="capture_response_gate_release_timeout"):
        asyncio.run(exchange(observer, gate.content, sent))
    assert gate.accepted.is_set()
    assert gate.release_timed_out
    assert sent == []


@pytest.mark.parametrize("duplicate", [False, True])
def test_generation_fixture_round_trips_atomic_source_extraction_and_reconciliation(duplicate):
    async def scenario():
        fixture = HostModelFixture()
        evidence = AtomicMemoryEvidence(
            evidence_id="captured-source-evidence",
            source_ref=SourceRef(source_type="content", source_id="captured-color-policy"),
            journal_position=1,
            content="In the synthetic Orion project, the verification color is copper-7319.",
            source_metadata={},
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app)) as client:
            model = OpenAIChatModel(
                "memory-fixture",
                provider=OpenAIProvider(base_url="http://fixture/v1", api_key="synthetic-only", http_client=client),
            )
            extractor = PydanticAIStructuredGenerator(
                model=model,
                instructions="Extract only the supplied Source facts and cite their evidence IDs.",
                input_type=AtomicMemoryExtractionInput,
                output_type=AtomicMemoryExtractionOutput,
            )
            extraction = await extractor.generate(AtomicMemoryExtractionInput(evidence=(evidence,)))
            (candidate,) = extraction.output.candidates
            assert candidate.text == evidence.content
            assert candidate.evidence_ids == (evidence.evidence_id,)
            proposal = AtomicMemoryComparisonItem(
                item_id="new-candidate",
                kind=candidate.kind,
                text=candidate.text,
                original_refs=(),
                evidence_ids=candidate.evidence_ids,
            )
            related: tuple[AtomicMemoryComparisonItem, ...] = tuple(
                [proposal.model_copy(update={"item_id": "existing-working-item"})] if duplicate else []
            )
            request = AtomicMemoryReconciliationInput(proposal=proposal, related=related, evidence=(evidence,))
            reconciler = PydanticAIStructuredGenerator(
                model=model,
                instructions="Reconcile against every supplied related item; preserve supplied evidence identities.",
                input_type=AtomicMemoryReconciliationInput,
                output_type=AtomicMemoryReconciliationOutput,
            )
            output = (await reconciler.generate(request)).output
            validate_reconciliation_output(request, output)
            if duplicate:
                assert output.action == "noop"
                assert output.target_ids == (related[0].item_id,)
            else:
                assert output.action == "create"
                assert output.content is not None and output.content.text == evidence.content
                assert output.evidence_ids == candidate.evidence_ids

    asyncio.run(scenario())
