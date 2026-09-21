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

"""Offline acceptance checks for paid-call bounds and benchmark artifacts."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart
from pydantic_ai.models.function import FunctionModel

from benchmark.locomo.dataset import LoCoMoConversation, LoCoMoSession, LoCoMoTurn
from benchmark.locomo_plus import runner
from benchmark.locomo_plus.dataset import SMOKE_CASE_IDS, LoCoMoPlusCase, LoCoMoPlusDataset
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    LLMMemoryCandidatePipeline,
    MemoryCandidateRequest,
    MemoryEntryInput,
    MemoryExtractionInput,
    MemoryExtractionOutput,
)
from powercontext.builtin.inference import EmbeddingResult, InvalidInferenceOutputError
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, open_builtin_runtime
from powercontext.builtin.sources import ContentSource
from powercontext.server.settings import ServerSettings


def _dataset() -> LoCoMoPlusDataset:
    sessions = tuple(
        LoCoMoSession(f"D{index}", index, f"2024-01-{index:02d} 10:00", (LoCoMoTurn(f"D{index}:1", "Alice", text),))
        for index, text in enumerate(("I bought a book.", "Walking helped my mood.", "I watched the sunset."), 1)
    )
    conversation = LoCoMoConversation("host-conversation", "Alice", "Bob", sessions, ())
    cases = tuple(
        LoCoMoPlusCase(
            case_id=case_id,
            sample_id=f"host-conversation:{case_id}",
            category=6,
            relation_type=relation,
            question="Alice: I am having a difficult afternoon.",
            answer="",
            evidence=("D2:1",),
            evidence_text="[D2:1] Alice: Walking helped my mood.",
            conversation=conversation,
            cue_session_id="D2",
            query_time="2024-01-10 10:00",
            metadata={"host_sample_id": "host-conversation"},
        )
        for case_id, relation in zip(SMOKE_CASE_IDS, ("causal", "state", "goal", "value"), strict=True)
    )
    factual = replace(
        cases[0],
        case_id="factual:host:q001",
        sample_id="host-conversation",
        category=4,
        relation_type=None,
        cue_session_id=None,
    )
    return LoCoMoPlusDataset(cases=(factual, *cases), exclusions=(), manifest={"fixture": "original-synthetic-data"})


def _settings() -> ServerSettings:
    return ServerSettings(
        database=SQLiteConfig(),
        inference=InferenceConfig(
            generation_model="test-answer",
            embedding_model="test-embedding",
            embedding_profile_id="locomo-plus-test",
            embedding_dimension=3,
        ),
    )


def _rows(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]


def test_judge_failure_resumes_frozen_answer_and_replay_never_calls_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"answer": 0, "judge": 0}
    valid_judge = False

    async def open_model(name, settings, resources):
        async def respond(messages, info):
            if name == "test-answer":
                calls["answer"] += 1
                output = "You found walking helpful before; perhaps a walk would help today."
            else:
                calls["judge"] += 1
                output = (
                    '{"label":"correct","reason":"Uses the earlier walking experience."}'
                    if valid_judge
                    else "unparseable verdict"
                )
            return ModelResponse(parts=[TextPart(output)])

        return FunctionModel(respond, model_name=name)

    monkeypatch.setattr(runner, "open_model", open_model)
    parameters: dict[str, Any] = {
        "dataset": _dataset(),
        "settings": _settings(),
        "output_directory": tmp_path,
        "run_id": "resume",
        "judge_model": "test-judge",
        "arm": "query-only",
        "limit": 1,
    }
    failed = asyncio.run(runner.run_benchmark(**parameters))
    assert failed["overall"]["completed_count"] == 0
    assert failed["overall"]["failures_by_stage"]["judge"] == 1
    assert calls == {"answer": 1, "judge": 1}
    frozen = _rows(tmp_path)[-1]
    assert frozen["generated_answer"]
    assert frozen["context"]["text"] == ""
    valid_judge = True
    completed = asyncio.run(runner.run_benchmark(**parameters))
    assert completed["overall"]["completed_count"] == 1
    assert calls == {"answer": 1, "judge": 2}
    assert _rows(tmp_path)[-1]["answer_input"] == frozen["answer_input"]

    async def no_models(*args, **kwargs):
        pytest.fail("completed runs and deterministic replay must not open models")

    monkeypatch.setattr(runner, "open_model", no_models)
    assert asyncio.run(runner.run_benchmark(**parameters)) == completed
    assert runner.replay_results(tmp_path) == completed
    with pytest.raises(ValueError, match="run identity changed"):
        asyncio.run(runner.run_benchmark(**parameters, max_tokens=1))


def test_smoke_and_full_preserve_identical_complete_histories_for_selected_cases() -> None:
    dataset = _dataset()
    smoke = runner.dry_run_plan(dataset)
    full = runner.dry_run_plan(dataset, profile="full")
    assert smoke["selected_count"] == 4
    assert smoke["ingestion_session_count"] == 12
    assert smoke["history_truncated"] is False
    assert smoke["max_history_sessions"] is None
    assert smoke["history_policy"] == full["history_policy"] == "all sessions"
    assert all(identity["matches_full_history"] for identity in smoke["histories"].values())
    assert all(identity["sha256"] == identity["full_sha256"] for identity in smoke["histories"].values())
    assert all(identity == full["histories"][sample_id] for sample_id, identity in smoke["histories"].items())
    assert smoke["scope"] == "subset"
    assert full["selected_count"] == 5
    assert full["ingestion_session_count"] == 15
    assert full["history_truncated"] is False
    assert full["scope"] == "full"


class _CandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,), reason="recorded dialogue")
            for source in request.sources
            if isinstance(source, ContentSource)
        )


class _EmbeddingModel:
    profile = EmbeddingProfile(
        profile_id="locomo-plus-test", model="deterministic", dimension=3, distance="l2", normalization="unit"
    )

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


def test_memory_source_arm_captures_flushes_searches_and_expands_real_sqlite_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def runtime_factory(config: BuiltinConfig):
        offline = config.model_copy(update={"inference": InferenceConfig()})
        async with open_builtin_runtime(
            offline, candidate_pipeline=_CandidatePipeline(), embedding_model=_EmbeddingModel()
        ) as runtime:
            yield runtime

    async def open_model(name, settings, resources):
        async def respond(messages, info):
            output = (
                "A walk could help; it helped your mood before."
                if name == "test-answer"
                else '{"label":"correct","reason":"Mentions the prior walk."}'
            )
            return ModelResponse(parts=[TextPart(output)])

        return FunctionModel(respond, model_name=name)

    monkeypatch.setattr(runner, "open_builtin_runtime", runtime_factory)
    monkeypatch.setattr(runner, "open_model", open_model)
    summary = asyncio.run(
        runner.run_benchmark(
            _dataset(),
            settings=_settings(),
            output_directory=tmp_path,
            run_id="sqlite",
            judge_model="test-judge",
            arm="memory-source",
            limit=1,
        )
    )
    assert summary["overall"]["completed_count"] == 1, _rows(tmp_path)
    row = _rows(tmp_path)[-1]
    assert row["citation"]["target_evidence_available"] is True
    assert row["retrieval"]["evidence_hit"] == 1
    assert "[D2:1] Alice: Walking helped my mood." in row["context"]["text"]
    assert "causal" not in row["context"]["text"]
    inputs = [json.loads(line) for line in (tmp_path / "inputs.jsonl").read_text().splitlines()]
    assert [session["source_id"] for session in inputs[0]["sessions"]] == ["D1", "D2", "D3"]
    assert summary["ingestion"]["scopes"] == 1
    assert (tmp_path / "state.sqlite3").exists()


def test_generation_errors_are_redacted_and_not_judged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def open_model(name, settings, resources):
        async def respond(messages, info):
            if name == "test-judge":
                pytest.fail("an unsuccessful answer must not be judged")
            raise RuntimeError("private-provider-error") from ValueError("private-provider-error")

        return FunctionModel(respond, model_name=name)

    monkeypatch.setattr(runner, "open_model", open_model)
    summary = asyncio.run(
        runner.run_benchmark(
            _dataset(),
            settings=_settings(),
            output_directory=tmp_path,
            run_id="generation-failure",
            judge_model="test-judge",
            arm="query-only",
            limit=1,
        )
    )
    assert summary["overall"]["failures_by_stage"]["generation"] == 1
    assert summary["overall"]["quality_rate_on_completed"] is None
    assert summary["overall"]["end_to_end_success_rate"] == 0
    assert "private-provider-error" not in (tmp_path / "observations.jsonl").read_text()
    assert [item["type"] for item in _rows(tmp_path)[-1]["error"]["chain"]] == ["RuntimeError", "ValueError"]


@pytest.mark.parametrize("profile", ["smoke", "full"])
@pytest.mark.parametrize("request_limit", [1, 2])
def test_extraction_corrects_invalid_json_within_the_configured_request_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str, request_limit: int
) -> None:
    requests = 0
    malformed = '{"candidates":[{"intent":"add","kind":"kind":"preference"}]}'

    async def extract(messages, info):
        nonlocal requests
        requests += 1
        correcting = any(isinstance(part, RetryPromptPart) for message in messages for part in message.parts)
        output = (
            '{"candidates":[{"intent":"add","kind":"fact","text":"Walking helped Alice.","evidence_ids":["source:0"]}]}'
            if correcting
            else malformed
        )
        return ModelResponse(parts=[TextPart(output)])

    @asynccontextmanager
    async def runtime_factory(config: BuiltinConfig):
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(extract),
            instructions="Extract memories with source citations.",
            input_type=MemoryExtractionInput,
            output_type=MemoryExtractionOutput,
            limits=InferenceLimits(max_requests=config.inference.generation_max_requests),
        )
        async with open_builtin_runtime(
            config.model_copy(update={"inference": InferenceConfig()}),
            candidate_pipeline=LLMMemoryCandidatePipeline(generator),
            embedding_model=_EmbeddingModel(),
        ) as runtime:
            yield runtime

    async def open_model(name, settings, resources):
        async def respond(messages, info):
            output = (
                "Walking helped Alice before."
                if name == "test-answer"
                else '{"label":"correct","reason":"Uses the walking memory."}'
            )
            return ModelResponse(parts=[TextPart(output)])

        return FunctionModel(respond, model_name=name)

    monkeypatch.setattr(runner, "open_builtin_runtime", runtime_factory)
    monkeypatch.setattr(runner, "open_model", open_model)
    settings = _settings()
    settings.inference.generation_max_requests = request_limit
    dataset = _dataset()
    summary = asyncio.run(
        runner.run_benchmark(
            replace(dataset, cases=tuple(case for case in dataset.cases if case.category == 6)),
            settings=settings,
            output_directory=tmp_path,
            run_id="bounded-extraction",
            judge_model="test-judge",
            profile=profile,
            limit=1,
        )
    )
    assert summary["configuration"]["generation_max_requests"] == request_limit
    ingestion = next(iter(json.loads((tmp_path / "ingestion.json").read_text()).values()))
    if request_limit == 2:
        assert summary["overall"]["completed_count"] == 1, _rows(tmp_path)
        assert ingestion["processed_session_count"] == ingestion["planned_session_count"] == 3
        assert requests == 6  # Three full sessions, each needing one correction within the configured budget.
    else:
        assert requests == 1
        assert summary["overall"]["failures_by_stage"]["infrastructure"] == 1
        failure = ingestion["failures"][0]
        assert failure["session_position"] == 1
        assert failure["source_id"] == "D1"
        assert failure["error"]["operation"] == "generate"
        validation = next(item for item in failure["error"]["chain"] if "validation_errors" in item)
        assert validation["validation_errors"] == [{"type": "json_invalid", "location": []}]
        assert malformed in (tmp_path / failure["messages_file"]).read_text().replace('\\"', '"')


def test_failed_extraction_usage_remains_unknown_after_successful_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_extraction = True

    class Pipeline(_CandidatePipeline):
        async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
            if fail_extraction:
                raise InvalidInferenceOutputError("memory-extract", "candidate cites evidence outside the request")
            return await super().extract(request)

    @asynccontextmanager
    async def runtime_factory(config: BuiltinConfig):
        offline = config.model_copy(update={"inference": InferenceConfig()})
        async with open_builtin_runtime(
            offline, candidate_pipeline=Pipeline(), embedding_model=_EmbeddingModel()
        ) as runtime:
            yield runtime

    async def open_model(name, settings, resources):
        async def respond(messages, info):
            output = (
                "A walk helped your mood before."
                if name == "test-answer"
                else '{"label":"correct","reason":"Uses the recorded experience."}'
            )
            return ModelResponse(parts=[TextPart(output)])

        return FunctionModel(respond, model_name=name)

    monkeypatch.setattr(runner, "open_builtin_runtime", runtime_factory)
    monkeypatch.setattr(runner, "open_model", open_model)
    parameters: dict[str, Any] = {
        "dataset": _dataset(),
        "settings": _settings(),
        "output_directory": tmp_path,
        "run_id": "extraction-resume",
        "judge_model": "test-judge",
        "arm": "memory-source",
        "limit": 1,
    }
    failed = asyncio.run(runner.run_benchmark(**parameters))
    assert failed["overall"]["failures_by_stage"]["infrastructure"] == 1
    assert failed["overall"]["generated_answer_count"] == 0
    for field in ("requests", "input_tokens", "output_tokens", "cost_usd"):
        assert failed["ingestion"]["usage"][field] is None
        assert failed["usage"]["ingestion"][field] is None

    fail_extraction = False
    completed = asyncio.run(runner.run_benchmark(**parameters))
    assert completed["overall"]["completed_count"] == 1, _rows(tmp_path)
    ingestion = next(iter(json.loads((tmp_path / "ingestion.json").read_text()).values()))
    assert "error" not in ingestion
    assert "error_type" not in ingestion
    assert ingestion["failures"][0]["error"]["detail"] == "candidate cites evidence outside the request"
    for field in ("requests", "input_tokens", "output_tokens", "cost_usd"):
        assert completed["ingestion"]["usage"][field] is None
        assert completed["usage"]["ingestion"][field] is None


def test_resume_setup_failure_replaces_the_previous_judge_failure_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_setup = False

    async def open_model(name, settings, resources):
        if fail_setup:
            raise RuntimeError("model-setup-failed")

        async def respond(messages, info):
            output = "A walk may help." if name == "test-answer" else "unparseable verdict"
            return ModelResponse(parts=[TextPart(output)])

        return FunctionModel(respond, model_name=name)

    monkeypatch.setattr(runner, "open_model", open_model)
    parameters: dict[str, Any] = {
        "dataset": _dataset(),
        "settings": _settings(),
        "output_directory": tmp_path,
        "run_id": "setup-failure",
        "judge_model": "test-judge",
        "arm": "query-only",
        "limit": 1,
    }
    first = asyncio.run(runner.run_benchmark(**parameters))
    assert first["overall"]["failures_by_stage"]["judge"] == 1
    frozen_answer = _rows(tmp_path)[-1]["generated_answer"]
    fail_setup = True
    second = asyncio.run(runner.run_benchmark(**parameters))
    assert second["overall"]["failures_by_stage"]["infrastructure"] == 1
    assert second["overall"]["failures_by_stage"]["judge"] == 0
    assert _rows(tmp_path)[-1]["generated_answer"] == frozen_answer
