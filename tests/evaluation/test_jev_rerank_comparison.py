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

"""Controlled comparisons reject unequal evidence and unknown billing claims."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.memory.locomo.compare_rerank import compare_rerank_runs
from evaluation.memory.locomo.dataset import load_locomo
from evaluation.memory.locomo.runner import (
    COMPARISON_BILLING_POLICY,
    RetrievedMemory,
    _bounded_answer_input,
    prepare_run,
)
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryRerankMode
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.settings import ServerSettings


def _runs(tmp_path):
    runs = {mode: tmp_path / mode for mode in ("none", "llm", "jev")}
    for mode, path in runs.items():
        path.mkdir()
        (path / "run.json").write_text(
            json.dumps({
                "run_id": "shared",
                "dataset_sha256": "dataset",
                "rerank_mode": mode,
                "answer_max_bytes": 2048,
                "selected_question_count": 1,
                "quality_regression_tolerance": 0.0,
                "comparison_billing_policy": COMPARISON_BILLING_POLICY,
            })
        )
        (path / "observations.jsonl").write_text(
            json.dumps({
                "question_id": "q1",
                "question": "Which evidence?",
                "candidate_digest": "shared",
                "status": "ok",
                "metrics": {"llm_judge": 1, "evidence_recall": 1, "evidence_mrr": 1},
                "answer_context": {"utf8_bytes": 120},
                "rerank": {"used_fallback": False},
                "usage": {
                    stage: {"requests": 1, "input_tokens": 100, "output_tokens": 0}
                    for stage in ("rerank", "answer", "judge")
                },
                "transient_retries": {"search": 0, "answer": 0, "judge": 0},
                "latency_ms": {"rerank": 10, "total": 40},
            })
            + "\n"
        )
    return runs


def _compare(runs):
    return compare_rerank_runs(
        runs,
        input_prices={"none": 0, "llm": 0.2, "jev": 0.04},
        output_prices=dict.fromkeys(runs, 0),
        generation_input_price=1,
        generation_output_price=1,
    )


def _change_observation(path, mutate):
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value) + "\n")


def test_comparison_requires_the_same_exact_candidate_pool(tmp_path):
    runs = _runs(tmp_path)
    _change_observation(runs["jev"] / "observations.jsonl", lambda value: value.update(candidate_digest="changed"))
    with pytest.raises(ValueError, match="coarse candidate pool changed"):
        _compare(runs)


@pytest.mark.parametrize(
    ("model", "endpoint_variable", "key_variable"),
    [
        ("openai:test-chat", "OPENAI_BASE_URL", "OPENAI_API_KEY"),
        ("anthropic:test-chat", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"),
    ],
)
def test_resuming_comparison_rejects_environment_endpoint_drift(
    tmp_path, monkeypatch, model, endpoint_variable, key_variable
):
    monkeypatch.setenv(key_variable, "test-key")
    monkeypatch.setenv(endpoint_variable, "https://provider-a.example/v1")
    dataset = load_locomo(Path(__file__).parents[2] / "evaluation/memory/locomo/dataset/jev_rerank_cases.json")
    settings = ServerSettings(inference=InferenceConfig(generation_model=model))
    prepare = partial(
        prepare_run,
        dataset=dataset,
        settings=settings,
        run_id="endpoint-drift",
        output_directory=tmp_path,
        top_k=3,
        answer_max_bytes=2048,
        categories=(1,),
        conversation_limit=1,
        question_limit=1,
        operation_retries=1,
    )
    asyncio.run(prepare())
    asyncio.run(prepare())

    monkeypatch.setenv(endpoint_variable, "https://provider-b.example/v1")
    with pytest.raises(ValueError, match="run manifest does not match"):
        asyncio.run(prepare())


def test_comparison_can_resume_with_the_same_resolved_provider_default(tmp_path, monkeypatch):
    from pydantic_ai.models import infer_model

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    dataset = load_locomo(Path(__file__).parents[2] / "evaluation/memory/locomo/dataset/jev_rerank_cases.json")
    generation_model = "openai:test-chat"
    settings = ServerSettings(inference=InferenceConfig(generation_model=generation_model))

    async def scenario():
        async with infer_model(generation_model) as model:
            default_endpoint = model.base_url
        prepare = partial(
            prepare_run,
            dataset=dataset,
            settings=settings,
            run_id="provider-default",
            output_directory=tmp_path,
            top_k=3,
            answer_max_bytes=2048,
            categories=(1,),
            conversation_limit=1,
            question_limit=1,
            operation_retries=1,
        )
        first = await prepare()
        monkeypatch.setenv("OPENAI_BASE_URL", default_endpoint)
        assert await prepare() == first

    asyncio.run(scenario())


def test_resuming_comparison_rejects_independent_reranker_endpoint_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://provider-a.example")
    dataset = load_locomo(Path(__file__).parents[2] / "evaluation/memory/locomo/dataset/jev_rerank_cases.json")
    settings = ServerSettings(
        inference=InferenceConfig(generation_model="openai:test-chat", rerank_model="anthropic:test-rerank")
    )
    prepare = partial(
        prepare_run,
        dataset=dataset,
        settings=settings,
        run_id="reranker-endpoint-drift",
        output_directory=tmp_path,
        top_k=3,
        answer_max_bytes=2048,
        rerank_mode=MemoryRerankMode.LLM,
        categories=(1,),
        conversation_limit=1,
        question_limit=1,
        operation_retries=1,
    )
    asyncio.run(prepare())
    asyncio.run(prepare())
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://provider-b.example")
    with pytest.raises(ValueError, match="run manifest does not match"):
        asyncio.run(prepare())


def test_comparison_rejects_different_environment_downstream_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    monkeypatch.setenv("JEV_MODEL", "jev-1.13.0")
    dataset = load_locomo(Path(__file__).parents[2] / "evaluation/memory/locomo/dataset/jev_rerank_cases.json")
    settings = ServerSettings(inference=InferenceConfig(generation_model="openai:test-chat"))
    runs = _runs(tmp_path)
    for mode, directory in runs.items():
        monkeypatch.setenv(
            "OPENAI_BASE_URL",
            "https://provider-b.example/v1" if mode == "jev" else "https://provider-a.example/v1",
        )
        (directory / "run.json").unlink()
        asyncio.run(
            prepare_run(
                dataset=dataset,
                settings=settings,
                run_id="endpoint-drift",
                output_directory=directory,
                top_k=3,
                answer_max_bytes=2048,
                rerank_mode=MemoryRerankMode(mode),
                categories=(1,),
                conversation_limit=1,
                question_limit=1,
                operation_retries=1,
            )
        )

    with pytest.raises(ValueError, match="changed comparison_inference_sha256"):
        _compare(runs)


@pytest.mark.parametrize("missing_usage", [False, True])
def test_fallback_or_unknown_usage_cannot_establish_an_adoption_cost(tmp_path, missing_usage):
    runs = _runs(tmp_path)

    def change(value):
        value["rerank"]["used_fallback"] = True
        if missing_usage:
            value["usage"]["rerank"]["input_tokens"] = None

    _change_observation(runs["jev"] / "observations.jsonl", change)
    result = _compare(runs)
    assert result["quality_gate_passed"] is True
    assert result["recommendation"] == "revise"
    assert result["results"]["jev"]["fallback_count"] == 1
    if missing_usage:
        assert result["results"]["jev"]["pipeline_cost_usd"] is None
        assert result["results"]["jev"]["cost_complete"] is False
    else:
        assert result["results"]["jev"]["rerank_cost_usd"] == pytest.approx(0.000004)


def test_comparison_includes_errors_in_quality_and_leaves_their_cost_unknown(tmp_path):
    runs = _runs(tmp_path)
    _change_observation(runs["jev"] / "observations.jsonl", lambda value: value.update(status="error"))
    result = _compare(runs)
    assert result["results"]["jev"]["downstream_success"] == 0
    assert result["results"]["jev"]["pipeline_cost_usd"] is None
    assert result["comparison_complete"] is False
    assert result["recommendation"] == "revise"


def test_comparison_cannot_redeclare_tolerance_after_running(tmp_path):
    runs = _runs(tmp_path)
    path = runs["jev"] / "run.json"
    value = json.loads(path.read_text())
    value["quality_regression_tolerance"] = 0.1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="changed quality_regression_tolerance"):
        _compare(runs)


def test_resuming_after_an_error_does_not_erase_unknown_prior_cost(tmp_path):
    runs = _runs(tmp_path)
    path = runs["jev"] / "observations.jsonl"
    succeeded = path.read_text()
    failed = json.loads(succeeded)
    failed["status"] = "error"
    path.write_text(json.dumps(failed) + "\n" + succeeded)
    result = _compare(runs)
    assert result["results"]["jev"]["error_count"] == 0
    assert result["results"]["jev"]["total_retries"] == 1
    assert result["results"]["jev"]["pipeline_cost_usd"] is None
    assert result["recommendation"] == "revise"


def test_total_cost_requires_retrieval_receipts_when_embeddings_are_used(tmp_path):
    runs = _runs(tmp_path)
    for path in runs.values():
        _change_observation(path / "observations.jsonl", lambda value: value.update(embedding_calls=1))
    assert _compare(runs)["results"]["jev"]["pipeline_cost_usd"] is None
    result = compare_rerank_runs(
        runs,
        input_prices={"none": 0, "llm": 0.2, "jev": 0.04},
        output_prices=dict.fromkeys(runs, 0),
        generation_input_price=1,
        generation_output_price=1,
        retrieval_costs_usd=dict.fromkeys(runs, 0.001),
    )
    jev = result["results"]["jev"]
    assert jev["cost_complete"] is True
    assert jev["pipeline_cost_usd"] == pytest.approx(jev["priced_request_cost_usd"] + 0.001)


def test_legacy_runs_without_observable_provider_billing_cannot_recommend_adoption(tmp_path):
    runs = _runs(tmp_path)
    for directory in runs.values():
        path = directory / "run.json"
        manifest = json.loads(path.read_text())
        del manifest["comparison_billing_policy"]
        path.write_text(json.dumps(manifest))
    result = _compare(runs)
    assert all(not value["cost_complete"] for value in result["results"].values())
    assert result["recommendation"] != "adopt"


def test_utf8_budget_records_only_evidence_delivered_to_the_answer_model():
    dataset = Path(__file__).parents[2] / "evaluation/memory/locomo/dataset/jev_rerank_cases.json"
    conversation = load_locomo(dataset).conversations[1]
    memories = tuple(
        RetrievedMemory(
            rank=rank,
            retrieval_rank=rank,
            artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=f"item-{rank}", revision=1),
            state_version=0,
            text="必要的项目证据。" * 100,
            score=1 / rank,
            distance=None,
            matched_by=("text",),
            source_ids=(),
            source_dates=(),
        )
        for rank in (1, 2)
    )
    one = _bounded_answer_input(
        conversation=conversation,
        question=conversation.questions[0].question,
        memories=memories[:1],
        source_content=False,
        max_bytes=None,
    )
    limit = len(one.model_dump_json().encode("utf-8"))
    bounded = _bounded_answer_input(
        conversation=conversation,
        question=conversation.questions[0].question,
        memories=memories,
        source_content=False,
        max_bytes=limit,
    )
    assert bounded.memories == memories[:1]
    assert len(bounded.model_dump_json().encode("utf-8")) <= limit
    assert conversation.questions[0].answer not in bounded.model_dump_json()


@pytest.mark.parametrize(
    ("mode", "provider", "provider_failure"),
    [
        ("jev", "function", None),
        *(
            (mode, provider, failure)
            for mode in ("jev", "llm")
            for provider in ("openai-chat", "anthropic")
            for failure in ("receipt", "timeout", "missing-usage", "partial-usage")
        ),
    ],
)
def test_comparison_billing_through_persisted_candidates(  # noqa: C901
    tmp_path, monkeypatch, mode, provider, provider_failure
):
    from contextlib import asynccontextmanager

    import httpx
    from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from evaluation.memory.locomo import runner
    from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
    from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryRerankMode
    from powercontext.builtin.inference import EmbeddingResult
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, open_builtin_runtime
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.server.settings import ServerSettings

    class Embedding:
        profile = EmbeddingProfile(profile_id="jev-comparison-fixture", model="fixture", dimension=3)

        async def embed(self, texts, /):
            return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))

    @asynccontextmanager
    async def runtime_with_embedding(config, **kwargs):
        async with open_builtin_runtime(config, embedding_model=Embedding(), **kwargs) as runtime:
            yield runtime

    original_client = httpx.AsyncClient

    def respond_model(messages, _info):
        value = next(
            json.loads(part.content)
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        output = {"label": "CORRECT"} if "gold_answer" in value else {"answer": "OceanBase"}
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    model_requests = []

    def respond_native(request):
        model_requests.append(request)
        if provider_failure == "timeout" and len(model_requests) == 1:
            raise httpx.ReadTimeout("response lost", request=request)  # noqa: TRY003
        body = json.loads(request.content)
        prompt = json.dumps(body["messages"][-1]["content"])
        is_judge = "gold_answer" in prompt
        is_rerank = "max_results" in prompt
        output = {"label": "CORRECT"} if is_judge else {"selected_ranks": [1]} if is_rerank else {"answer": "OceanBase"}
        if provider == "anthropic":
            return httpx.Response(
                200,
                json={
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5",
                    "content": [{"type": "text", "text": json.dumps(output)}],
                    "stop_reason": "end_turn",
                    **(
                        {
                            "usage": {
                                "input_tokens": 100,
                                **({"output_tokens": 10} if provider_failure != "partial-usage" else {}),
                            }
                        }
                        if provider_failure != "missing-usage"
                        else {}
                    ),
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "created": 0,
                "model": "gpt-4o",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output),
                        },
                        "finish_reason": "stop",
                    }
                ],
                **(
                    {
                        "usage": {
                            "prompt_tokens": 100,
                            **(
                                {"completion_tokens": 10, "total_tokens": 110}
                                if provider_failure != "partial-usage"
                                else {}
                            ),
                        }
                    }
                    if provider_failure != "missing-usage"
                    else {}
                ),
            },
        )

    if provider == "function":
        monkeypatch.setattr(runner, "infer_model", lambda *args, **kwargs: FunctionModel(respond_model))
    else:
        monkeypatch.setenv("OPENAI_API_KEY" if provider == "openai-chat" else "ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            f"pydantic_ai.providers.{'openai' if provider == 'openai-chat' else 'anthropic'}.create_async_http_client",
            lambda: original_client(transport=httpx.MockTransport(respond_native)),
        )
    monkeypatch.setattr(runner, "open_builtin_runtime", runtime_with_embedding)
    monkeypatch.setattr(
        runner,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda: original_client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        json={
                            "model": "jev-1.13.0",
                            "answers": {"useful": {"type": "noul", "noul": 0.9}},
                            "usage": {"input_tokens": 10, "output_tokens": 1},
                        },
                    )
                )
            )
        ),
    )
    monkeypatch.setenv("JEV_MODEL", "jev-1.13.0")
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    path = Path(__file__).parents[2] / "evaluation/memory/locomo/dataset/jev_rerank_cases.json"
    dataset = load_locomo(path)
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'evaluation.db'}"),
        inference=InferenceConfig(
            generation_model=(
                "test"
                if provider == "function"
                else "openai-chat:gpt-4o"
                if provider == "openai-chat"
                else "anthropic:claude-haiku-4-5"
            )
        ),
        runtime=RuntimeConfig(artifact_processing_families=()),
    )

    async def scenario():
        async with runtime_with_embedding(
            BuiltinConfig(database=settings.database, runtime=settings.runtime)
        ) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Jev evaluation", summary="Fixed pool", idempotency_key="jev-evaluation")
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(
                    kind="fact",
                    text="The project selects OceanBase for multi-node persistent storage.",
                ),
            ))
        sample = dataset.conversations[0].sample_id
        (tmp_path / "ingestion.json").write_text(
            json.dumps({
                "run_id": "jev-evaluation",
                "conversations": {
                    sample: {"namespace": runner.scope_id("jev-evaluation", sample), "scope_id": scope.scope_id},
                },
            })
        )
        await runner.prepare_run(
            dataset=dataset,
            settings=settings,
            run_id="jev-evaluation",
            output_directory=tmp_path,
            top_k=3,
            answer_k=1,
            answer_max_bytes=2048,
            rerank_mode=MemoryRerankMode(mode),
            categories=(1,),
            conversation_limit=1,
            question_limit=1,
            operation_retries=1,
        )
        await runner.evaluate_dataset(
            dataset,
            settings=settings,
            run_id="jev-evaluation",
            output_directory=tmp_path,
            top_k=3,
            answer_k=1,
            answer_max_bytes=2048,
            rerank_mode=MemoryRerankMode(mode),
            conversation_limit=1,
            question_limit=1,
            operation_retries=1,
            progress=lambda _: None,
        )
        observation = json.loads((tmp_path / "observations.jsonl").read_text())
        if provider_failure in {"timeout", "missing-usage", "partial-usage"}:
            assert observation["status"] == "error", observation
            assert observation["error_stage"] == ("search" if mode == "llm" else "answer")
            assert len(model_requests) == 1
            runs = _runs(tmp_path)
            for directory in runs.values():
                (directory / "observations.jsonl").write_text(json.dumps(observation) + "\n")
            report = _compare(runs)
            assert report["results"]["jev"]["cost_complete"] is False
            assert report["results"]["jev"]["pipeline_cost_usd"] is None
            assert report["recommendation"] != "adopt"
            return
        assert observation["status"] == "ok", observation
        assert observation["rerank"]["mode"] == mode and not observation["rerank"]["used_fallback"]
        assert observation["usage"]["rerank"]["requests"] == 1
        assert observation["candidate_digest"]
        assert observation["answer_context"]["utf8_bytes"] <= 2048
        assert observation["hits"][0]["artifact_ref"] == observation["candidate_hits"][0]["artifact_ref"]
        if provider != "function":
            assert observation["usage"]["answer"]["input_tokens"] == 100
            assert observation["usage"]["judge"]["output_tokens"] == 10

    asyncio.run(scenario())
