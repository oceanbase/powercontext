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

import asyncio
import json

import httpx
import pytest

pytest.importorskip("anthropic")

from anthropic import AsyncAnthropic

from examples.anthropic_sdk.chat import SYSTEM_INSTRUCTIONS, answer
from powercontext.client import PowerContextClient

POWERCONTEXT_TOKEN = "powercontext-secret"  # noqa: S105
ANTHROPIC_KEY = "anthropic-secret"
QUESTION = "Should the assistant check refund eligibility before offering a refund?"
READY_CONTENT = "## Memory\n\n- Validate refund eligibility before offering a refund action."


def _prepared(content: str | None) -> dict[str, object]:
    if content is None:
        return {"schema": "powercontext.prepared-context.v1", "status": "empty", "content": None, "content_bytes": 0}
    return {
        "schema": "powercontext.prepared-context.v1",
        "status": "ready",
        "content": content,
        "content_bytes": len(content.encode("utf-8")),
    }


def _message(text: str) -> dict[str, object]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _run(prepared: dict[str, object]) -> tuple[list[httpx.Request], list[httpx.Request], str, str]:
    powercontext_requests: list[httpx.Request] = []
    model_requests: list[httpx.Request] = []

    def powercontext_handler(request: httpx.Request) -> httpx.Response:
        powercontext_requests.append(request)
        return httpx.Response(200, json=prepared)

    def model_handler(request: httpx.Request) -> httpx.Response:
        model_requests.append(request)
        return httpx.Response(200, json=_message("Check eligibility first."))

    async def scenario() -> tuple[str, str]:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(powercontext_handler)) as powercontext_http,
            httpx.AsyncClient(transport=httpx.MockTransport(model_handler)) as model_http,
        ):
            powercontext = PowerContextClient(
                "http://127.0.0.1:8000", token=POWERCONTEXT_TOKEN, http_client=powercontext_http
            )
            anthropic = AsyncAnthropic(api_key=ANTHROPIC_KEY, base_url="https://model.test", http_client=model_http)
            result, reply = await answer(
                QUESTION, powercontext=powercontext, anthropic=anthropic, scope_id="scope-1", model="test-model"
            )
            return str(result.status), reply

    status, reply = asyncio.run(scenario())
    return powercontext_requests, model_requests, status, reply


def _body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


def test_ready_context_is_sent_as_untrusted_history() -> None:
    powercontext_requests, model_requests, status, reply = _run(_prepared(READY_CONTENT))

    assert status == "ready"
    assert reply == "Check eligibility first."
    assert [request.url.path for request in powercontext_requests] == ["/v1/context/prepare"]
    prepare_body = _body(powercontext_requests[0])
    assert (prepare_body["scope_id"], prepare_body["query"], prepare_body["max_bytes"]) == ("scope-1", QUESTION, 4000)
    assert len(model_requests) == 1
    assert model_requests[0].url.path == "/v1/messages"
    body = _body(model_requests[0])
    assert body["system"] == f"{SYSTEM_INSTRUCTIONS}\n\n{READY_CONTENT}"
    assert body["messages"] == [{"role": "user", "content": QUESTION}]


def test_empty_context_continues_without_history() -> None:
    _, model_requests, status, reply = _run(_prepared(None))

    assert status == "empty"
    assert reply == "Check eligibility first."
    assert len(model_requests) == 1
    body = _body(model_requests[0])
    assert body["system"] == SYSTEM_INSTRUCTIONS
    assert body["messages"] == [{"role": "user", "content": QUESTION}]


def test_credentials_stay_in_transport_headers() -> None:
    powercontext_requests, model_requests, _, _ = _run(_prepared(READY_CONTENT))

    assert powercontext_requests[0].headers["Authorization"] == f"Bearer {POWERCONTEXT_TOKEN}"
    assert model_requests[0].headers["x-api-key"] == ANTHROPIC_KEY
    for request in (*powercontext_requests, *model_requests):
        body = request.content.decode("utf-8")
        assert POWERCONTEXT_TOKEN not in body
        assert ANTHROPIC_KEY not in body
