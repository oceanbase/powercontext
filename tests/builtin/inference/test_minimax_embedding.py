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

"""Tests for the MiniMax embedding adapter (native /v1/embeddings contract)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx2
import pytest

from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.inference.budget import EmbeddingBudget
from powercontext.builtin.inference.errors import (
    InferenceBudgetExceededError,
    InferenceConfigurationError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.minimax import (
    MiniMaxEmbeddingModel,
    _embedding_model_name,
    is_minimax_embedding,
)
from powercontext.builtin.processing_diagnostics import (
    ProcessingFailureReason,
    ProcessingStage,
    classify_processing_error,
)

TEST_PROFILE = EmbeddingProfile(
    profile_id="mm-v1",
    model="openai:embo-01",
    dimension=3,
    distance="l2",
    normalization="none",
)


def _model(
    profile: EmbeddingProfile = TEST_PROFILE,
    batch_size: int = 10,
    headers=None,
    http_client=None,
    timeout_seconds: float = 30.0,
    budget: EmbeddingBudget | None = None,
) -> MiniMaxEmbeddingModel:
    return MiniMaxEmbeddingModel(
        base_url="https://api.minimaxi.com/v1",
        model="embo-01",
        headers=headers,
        profile=profile,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        http_client=http_client,
        budget=budget,
    )


def _ok_response(vectors: list[list[float]], status_code: int = 0) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "vectors": vectors,
            "total_tokens": len(vectors),
            "base_resp": {"status_code": status_code, "status_msg": "success"},
        },
    )


def test_embed_returns_validated_vectors_and_sends_native_shape() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return _ok_response([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            result = await model.embed(("alpha", "beta"))
            assert result.vectors == ((0.1, 0.2, 0.3), (0.4, 0.5, 0.6))
            assert result.usage.requests == 1
            assert result.usage.input_tokens == 2

    asyncio.run(scenario())
    assert captured[0] == {"model": "embo-01", "texts": ["alpha", "beta"], "type": "db"}


def test_embed_query_sends_query_type() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return _ok_response([[0.1, 0.2, 0.3]])

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            result = await model.embed_query(("alpha",))
            assert result.vectors == ((0.1, 0.2, 0.3),)

    asyncio.run(scenario())
    assert captured[0] == {"model": "embo-01", "texts": ["alpha"], "type": "query"}


def test_embed_batches_requests_without_split_size_mismatch() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        calls.append(body)
        return _ok_response([[0.1, 0.2, 0.3] for _ in body["texts"]])

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(batch_size=2, http_client=client)
            result = await model.embed(("a", "b", "c", "d", "e"))
            assert result.usage.requests == 3

    asyncio.run(scenario())
    assert [len(call["texts"]) for call in calls] == [2, 2, 1]


def test_embed_raises_unavailable_on_business_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        # MiniMax answers HTTP 200 with a non-zero base_resp.status_code.
        return httpx2.Response(
            200, json={"vectors": None, "base_resp": {"status_code": 2013, "status_msg": "missing texts"}}
        )

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InferenceUnavailableError) as error:
                await model.embed(("alpha",))
            assert "MiniMax status_code 2013" in str(error.value)
            assert "missing texts" in str(error.value)

    asyncio.run(scenario())


def test_embed_raises_unavailable_on_http_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"base_resp": {"status_code": 1001}})

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InferenceUnavailableError):
                await model.embed(("alpha",))

    asyncio.run(scenario())


def test_embed_classifies_rate_limited_http_status() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"base_resp": {"status_code": 1002}})

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InferenceUnavailableError) as error:
                await model.embed(("alpha",))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.EMBEDDING
        assert diagnostics.reason is ProcessingFailureReason.RATE_LIMIT
        assert diagnostics.cause_exception_type == "HTTPStatusError"

    asyncio.run(scenario())


def test_embed_maps_http_timeout_to_inference_timeout() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("provider did not respond", request=request)  # noqa: TRY003

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InferenceTimeoutError):
                await model.embed(("alpha",))

    asyncio.run(scenario())


def test_default_http_client_uses_configured_timeout() -> None:
    async def scenario() -> None:
        model = _model(timeout_seconds=7.0)
        try:
            assert model._client.timeout.read == 7.0
        finally:
            await model.aclose()

    asyncio.run(scenario())


def test_embed_raises_invalid_output_on_wrong_vector_count() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _ok_response([[0.1, 0.2, 0.3]])

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InvalidInferenceOutputError):
                await model.embed(("alpha", "beta"))

    asyncio.run(scenario())


def test_embed_raises_invalid_output_on_malformed_json() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not-json")

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InvalidInferenceOutputError):
                await model.embed(("alpha",))

    asyncio.run(scenario())


def test_embed_does_not_classify_unexpected_adapter_errors_as_unavailable() -> None:
    class AdapterBugError(RuntimeError):
        pass

    class BrokenClient:
        async def post(self, *_args, **_kwargs):
            raise AdapterBugError

    async def scenario() -> None:
        model = _model(http_client=BrokenClient())
        with pytest.raises(AdapterBugError):
            await model.embed(("alpha",))

    asyncio.run(scenario())


def test_embed_raises_invalid_output_on_dimension_mismatch() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _ok_response([[0.1, 0.2, 0.3, 0.4]])

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InvalidInferenceOutputError):
                await model.embed(("alpha",))

    asyncio.run(scenario())


def test_embed_empty_returns_no_vectors() -> None:
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return _ok_response([])

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            result = await model.embed(())
            assert result.vectors == ()

    asyncio.run(scenario())
    assert captured == []


def test_embed_sends_configured_headers() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(dict(request.headers))
        return _ok_response([[0.1, 0.2, 0.3]])

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(headers={"Authorization": "Bearer secret-key"}, http_client=client)
            await model.embed(("alpha",))

    asyncio.run(scenario())
    assert captured[0]["authorization"] == "Bearer secret-key"


def test_init_rejects_non_positive_dimension() -> None:
    profile = EmbeddingProfile(profile_id="p", model="m", dimension=0, distance="l2", normalization="none")
    with pytest.raises(InferenceConfigurationError):
        MiniMaxEmbeddingModel(base_url="https://api.minimaxi.com/v1", model="embo-01", profile=profile)


def test_is_minimax_embedding_detects_host_and_model() -> None:
    assert is_minimax_embedding("https://api.minimaxi.com/v1", "openai:embo-01") is True
    assert is_minimax_embedding("https://api.minimax.io/v1", None) is True
    assert is_minimax_embedding(None, "minimax:embo-01") is True
    assert is_minimax_embedding("https://proxy.example/v1?upstream=api.minimaxi.com", "openai:embo-01") is False
    assert is_minimax_embedding("https://api.openai.com/v1/minimax.io", "openai:embo-01") is False
    assert is_minimax_embedding("https://api.openai.com/v1", "openai:my-minimax-proxy") is False
    assert is_minimax_embedding("https://api.openai.com/v1", "openai:text-embedding-3-small") is False
    assert is_minimax_embedding(None, "openai:embo-01") is False


def test_embedding_model_name_strips_provider_prefix() -> None:
    assert _embedding_model_name("openai:embo-01") == "embo-01"
    assert _embedding_model_name("minimax:embo-01") == "embo-01"
    assert _embedding_model_name("embo-01") == "embo-01"
    assert _embedding_model_name(None) == ""


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (401, ProcessingFailureReason.CONFIGURATION),
        (403, ProcessingFailureReason.CONFIGURATION),
        (408, ProcessingFailureReason.TIMEOUT),
        (504, ProcessingFailureReason.TIMEOUT),
        (409, ProcessingFailureReason.PROVIDER_UNAVAILABLE),
        (425, ProcessingFailureReason.PROVIDER_UNAVAILABLE),
        (500, ProcessingFailureReason.PROVIDER_UNAVAILABLE),
        (400, None),
    ],
)
def test_embed_classifies_http_status_reason(status_code, reason) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, json={"base_resp": {"status_code": 1000}})

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client)
            with pytest.raises(InferenceUnavailableError) as error:
                await model.embed(("alpha",))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.EMBEDDING
        assert diagnostics.reason is reason
        assert diagnostics.cause_exception_type == "HTTPStatusError"

    asyncio.run(scenario())


def _recording_client(handler_responses: list[tuple[str, ...]], calls: list[dict[str, Any]]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        calls.append(body)
        return _ok_response([[0.1, 0.2, 0.3] for _ in body["texts"]])

    return handler


def test_embedding_budget_rejects_one_oversized_text_before_dispatch() -> None:
    calls: list[dict[str, Any]] = []

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(_recording_client([], calls))) as client:
            model = _model(http_client=client, budget=EmbeddingBudget(max_input_tokens_per_text=4))
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await model.embed(("ok", "x" * 40))
            decision = rejected.value.decision
            assert decision.dimension == "embedding_item_tokens"
            assert decision.estimated_value == 10
            assert decision.limit_value == 4

    asyncio.run(scenario())
    assert calls == []


def test_embedding_budget_keeps_batch_limit_separate_from_item_limit() -> None:
    calls: list[dict[str, Any]] = []

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(_recording_client([], calls))) as client:
            model = _model(
                http_client=client,
                budget=EmbeddingBudget(max_input_tokens_per_text=4, max_input_tokens_per_batch=6),
            )
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await model.embed(("abcdefgh", "abcdefgh", "abcdefgh", "abcdefgh"))
            assert rejected.value.decision.dimension == "embedding_batch_tokens"

    asyncio.run(scenario())
    assert calls == []


def test_embedding_budget_limits_the_item_count_independently() -> None:
    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(_recording_client([], []))) as client:
            model = _model(http_client=client, batch_size=10, budget=EmbeddingBudget(max_batch_items=1))
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await model.embed(("alpha", "beta"))
            assert rejected.value.decision.dimension == "embedding_batch_items"
            assert rejected.value.decision.unit == "items"

    asyncio.run(scenario())


def test_embedding_budget_without_limits_keeps_the_baseline_dispatch() -> None:
    calls: list[dict[str, Any]] = []

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(_recording_client([], calls))) as client:
            model = _model(http_client=client, budget=EmbeddingBudget())
            result = await model.embed(("alpha", "beta"))
            assert len(result.vectors) == 2

    asyncio.run(scenario())
    assert len(calls) == 1


def test_embedding_budget_counts_multibyte_text_per_character() -> None:
    calls: list[dict[str, Any]] = []

    async def scenario() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(_recording_client([], calls))) as client:
            ascii_model = _model(http_client=client, budget=EmbeddingBudget(max_input_tokens_per_text=3))
            admitted = await ascii_model.embed(("abcd",))
            assert len(admitted.vectors) == 1

            cjk_model = _model(http_client=client, budget=EmbeddingBudget(max_input_tokens_per_text=3))
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await cjk_model.embed(("中文中文",))

            decision = rejected.value.decision
            assert decision.dimension == "embedding_item_tokens"
            assert decision.estimated_value == 4
            assert decision.limit_value == 3

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize("query", [False, True])
def test_later_oversized_text_is_rejected_before_the_first_batch(query: bool) -> None:
    sent = []

    async def handler(request):
        sent.append(request)
        return _ok_response([[1.0, 0.0, 0.0]])

    async def scenario():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client, batch_size=1, budget=EmbeddingBudget(max_input_tokens_per_text=2))
            with pytest.raises(InferenceBudgetExceededError):
                await (model.embed_query(("ok", "X" * 100)) if query else model.embed(("ok", "X" * 100)))
        assert sent == []

    asyncio.run(scenario())


def test_embedding_error_preserves_preceding_batch_usage() -> None:
    async def scenario():
        from powercontext.builtin.inference.budget import hard_limit_decision

        class FailingModel(MiniMaxEmbeddingModel):
            calls = 0

            async def _embed_one(self, batch, *, embedding_type):
                self.calls += 1
                if self.calls == 2:
                    raise InferenceBudgetExceededError(
                        hard_limit_decision(
                            operation="embed",
                            dimension="embedding_item_tokens",
                            estimated_value=3,
                            limit_value=2,
                            unit="tokens",
                        )
                    )
                return [[1.0, 0.0, 0.0]], 3

        model = FailingModel(base_url="https://example.test", model="test", profile=TEST_PROFILE, batch_size=1)
        try:
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await model.embed(("first", "second"))
            assert rejected.value.usage is not None
            assert rejected.value.usage.requests == 1
            assert rejected.value.usage.input_tokens == 3
        finally:
            await model.aclose()

    asyncio.run(scenario())


def test_embedding_http_failure_preserves_completed_batch_usage() -> None:
    async def scenario():
        calls = []

        async def handler(request):
            calls.append(request)
            if len(calls) == 2:
                return httpx2.Response(503, json={"error": "unavailable"})
            return _ok_response([[1.0, 0.0, 0.0]])

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            model = _model(http_client=client, batch_size=1)
            with pytest.raises(InferenceUnavailableError) as rejected:
                await model.embed(("first", "second"))
            assert rejected.value.usage is not None
            assert rejected.value.usage.requests == 1
            assert rejected.value.usage.input_tokens == 1

    asyncio.run(scenario())
