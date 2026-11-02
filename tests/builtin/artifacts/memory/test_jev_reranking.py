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

"""Jev rank identity, bounded requests, usage, and visible provider failures."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from powercontext.builtin.artifacts.memory import JevMemoryReranker, JevRerankConfig
from powercontext.builtin.inference.errors import (
    InferenceConfigurationError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)


@dataclass(frozen=True)
class _Text:
    text: str


def _config(**overrides) -> JevRerankConfig:
    return JevRerankConfig.model_validate({
        "endpoint": "https://api.typesafe.ai/v1/systemone",
        "model": "jev-1.13.0",
        "api_key": SecretStr("dedicated-key"),
        **overrides,
    })


def _response(score=0.8, **overrides) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "jev-1.13.0",
            "answers": {"useful": {"type": "noul", "noul": score}},
            "usage": {"input_tokens": 20, "output_tokens": 1},
            **overrides,
        },
    )


def test_ranks_keep_original_identity_and_stable_ties_with_reported_usage():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == "Bearer dedicated-key"
        return _response({"first": 0.2, "second": 0.9, "third": 0.9}[body["state"]["candidate"]])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), auth=("other", "credential")) as client:
            result = await JevMemoryReranker(_config(), client).rerank(
                "query", tuple(_Text(text) for text in ("first", "second", "third")), 2
            )
        assert result.selected_ranks == (2, 3)
        assert result.used_fallback is False
        assert result.usage.requests == 3
        assert result.usage.input_tokens == 60
        assert result.usage.output_tokens == 3
        assert all(body["model"] == "jev-1.13.0" for body in requests)
        assert all(set(body["state"]) == {"query", "candidate"} for body in requests)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), InferenceUnavailableError),
        (_response(1.1), InvalidInferenceOutputError),
        (_response(model="jev-1.14.0"), InvalidInferenceOutputError),
        (_response(usage={}), InvalidInferenceOutputError),
    ],
)
def test_unavailable_or_unusable_results_expose_provider_failure(response, error):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises(error):
                await JevMemoryReranker(_config(), client).rerank("query", (_Text("one"), _Text("two")), 2)

    asyncio.run(scenario())


def test_partial_failure_does_not_return_a_successful_decision():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    _response() if json.loads(request.content)["state"]["candidate"] == "one" else httpx.Response(429)
                )
            )
        ) as client:
            with pytest.raises(InferenceUnavailableError):
                await JevMemoryReranker(_config(max_concurrency=1), client).rerank(
                    "query", (_Text("one"), _Text("two")), 2
                )

    asyncio.run(scenario())


def test_total_deadline_includes_waiting_candidates_and_cancels_requests():
    async def scenario():
        active = 0
        maximum = 0

        async def respond(_request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1
            return _response()

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            reranker = JevMemoryReranker(_config(timeout_seconds=0.03, max_concurrency=1), client)
            results = await asyncio.gather(
                *(reranker.rerank("query", (_Text("one"), _Text("two")), 1) for _ in range(2)),
                return_exceptions=True,
            )
        assert maximum == 1 and active == 0
        assert all(isinstance(result, InferenceTimeoutError) for result in results)

    asyncio.run(scenario())


def test_caller_cancellation_propagates_and_releases_pending_calls():
    async def scenario():
        started = asyncio.Event()
        finished = asyncio.Event()

        async def respond(_request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
            return _response()

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            task = asyncio.create_task(JevMemoryReranker(_config(), client).rerank("query", (_Text("one"),), 1))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()

    asyncio.run(scenario())


def test_oversized_utf8_request_fails_without_sending_content():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
        ) as client:
            with pytest.raises(InferenceConfigurationError, match="exceeds max_request_bytes"):
                await JevMemoryReranker(_config(max_request_bytes=1), client).rerank("query", (_Text("evidence"),), 1)

    asyncio.run(scenario())


def test_redirect_does_not_send_credentials_or_evidence_to_another_host():
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://other.example/systemone"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
            with pytest.raises(InferenceConfigurationError):
                await JevMemoryReranker(_config(), client).rerank("query", (_Text("one"),), 1)
        assert requests == ["https://api.typesafe.ai/v1/systemone"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "jev-latest"},
        {"api_key": SecretStr("")},
        {"endpoint": "https://user:password@api.typesafe.ai/v1/systemone"},
        {"endpoint": "https://api.typesafe.ai/v1/systemone?api_key=secret"},
        {"timeout_seconds": float("inf")},
        {"max_concurrency": True},
    ],
)
def test_configuration_requires_pinned_model_and_separate_credentials(changes):
    with pytest.raises(ValidationError):
        _config(**changes)
