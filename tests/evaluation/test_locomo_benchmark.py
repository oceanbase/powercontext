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

"""Focused tests for the deterministic LoCoMo benchmark boundary."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from benchmark.locomo import runner
from benchmark.locomo.dataset import (
    LoCoMoConversation,
    LoCoMoDataset,
    LoCoMoQuestion,
    LoCoMoSession,
    LoCoMoTurn,
    load_locomo,
    render_session,
)
from benchmark.locomo.metrics import (
    bleu1,
    diagnose_observations,
    exact_match,
    retrieval_metrics,
    set_token_f1,
    summarize_observations,
    token_f1,
)
from benchmark.locomo.runner import (
    normalize_run_id,
    prepare_rejudge,
    prepare_run,
    scope_id,
)
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    InferenceConfig,
    MemoryExtractionProfile,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeNotFoundError
from powercontext.builtin.sources import ContentSource
from powercontext.server.settings import ServerSettings

DATASET = Path(__file__).parents[2] / "benchmark" / "locomo" / "dataset" / "locomo10.json"


def test_canonical_locomo_dataset_has_expected_shape_and_scored_selection() -> None:
    dataset = load_locomo(DATASET)

    assert dataset.sha256 == "4448275ea2c5cd0af5774d80aea7b05b5a16e1b996caf8554ca3d762a301ae84"
    assert len(dataset.conversations) == 10
    assert len(dataset.sessions) == 272
    assert sum(len(session.turns) for session in dataset.sessions) == 5_882
    assert len(dataset.questions) == 1_986
    assert len(dataset.selected_questions()) == 1_540
    composite = next(
        question for question in dataset.questions if question.question == "What did Melanie paint recently?"
    )
    assert composite.evidence_raw == ("D8:6; D9:17",)
    assert composite.evidence == ("D8:6", "D9:17")


def test_session_source_contains_dialogue_and_date_without_qa_annotations() -> None:
    dataset = load_locomo(DATASET)
    conversation = dataset.conversations[0]

    content = render_session(conversation, conversation.sessions[0])

    assert "Date and time: 1:56 pm on 8 May, 2023" in content
    assert "[D1:3] Caroline: I went to a LGBTQ support group yesterday" in content
    assert "When did Caroline go to the LGBTQ support group?" not in content
    assert "Gold answer" not in content
    assert "evidence" not in content.lower()


def test_answer_metrics_and_session_provenance_are_deterministic() -> None:
    assert exact_match("The shell necklace.", "shell necklace") == 1.0
    assert token_f1("a shell necklace from Hawaii", "shell necklace") == 2 / 3
    assert set_token_f1("red red dress", "red dress") == 1.0
    assert bleu1("shell necklace", "a shell necklace") > 0.5
    assert retrieval_metrics(
        evidence_sessions=("D1", "D3"),
        hit_source_ids=(("D8",), ("D3",), ("D1",)),
    ) == {"evidence_hit": 1.0, "evidence_recall": 1.0, "evidence_mrr": 0.5}


def test_summary_keeps_errors_in_accuracy_denominator() -> None:
    observations = (
        {
            "category": 1,
            "status": "ok",
            "metrics": {
                "exact_match": 1,
                "token_f1": 1,
                "reference_set_f1": 1,
                "bleu1": 1,
                "llm_judge": 1,
                "evidence_hit": 1,
                "evidence_recall": 1,
                "evidence_mrr": 1,
                "candidate_evidence_hit": 1,
                "candidate_evidence_recall": 1,
                "candidate_evidence_mrr": 1,
            },
            "latency_ms": {"search": 10, "rerank": 1, "answer": 20, "judge": 30, "total": 60},
        },
        {"category": 1, "status": "error", "latency_ms": {"total": 40}},
    )

    summary = summarize_observations(observations)["overall"]

    assert summary["question_count"] == 2
    assert summary["error_count"] == 1
    assert summary["llm_judge"] == 0.5
    assert summary["total_latency_ms_p50"] == 60
    diagnostics = diagnose_observations(observations)
    assert diagnostics["retrieval_conditioned"]["hit"]["llm_judge_accuracy"] == 1.0
    assert diagnostics["wrong_answer_count"] == 0


def test_diagnostics_report_unknown_fallback_quality_and_cost() -> None:
    observations = (
        {
            "status": "ok",
            "generated_answer": "supported conclusion",
            "answer_fallback": {"triggered": True, "initial_answer": "Unknown"},
            "metrics": {"evidence_hit": 1, "evidence_mrr": 1, "llm_judge": 1},
            "usage": {"answer_fallback": {"requests": 1, "input_tokens": 20, "output_tokens": 2}},
            "transient_retries": {"answer_fallback": 1},
        },
        {
            "status": "ok",
            "generated_answer": "Unknownish",
            "answer_fallback": {"triggered": False, "initial_answer": "Unknownish"},
            "metrics": {"evidence_hit": 0, "evidence_mrr": 0, "llm_judge": 0},
        },
    )

    diagnostics = diagnose_observations(observations)

    assert diagnostics["answer_fallback"] == {
        "trigger": "normalized-answer-equals-unknown",
        "triggered_count": 1,
        "triggered_rate": 0.5,
        "resolved_count": 1,
        "resolved_rate": 1.0,
        "llm_judge_accuracy": 1.0,
    }
    assert diagnostics["model_usage"]["answer_fallback"] == {
        "requests": 1,
        "input_tokens": 20,
        "output_tokens": 2,
    }
    assert diagnostics["transient_retries"]["answer_fallback"] == 1


def test_run_manifest_is_stable_and_excludes_database_credentials(tmp_path: Path) -> None:
    dataset = load_locomo(DATASET)
    settings = ServerSettings(
        runtime=RuntimeConfig(memory_extraction_profile=MemoryExtractionProfile.CONVERSATION),
        database=SQLiteConfig(url="sqlite+aiosqlite:////secret/location.db"),
        inference=InferenceConfig(
            generation_model="openai:test-chat",
            embedding_model="openai:test-embedding",
            embedding_profile_id="test-3-unit",
            embedding_dimension=3,
        ),
    )

    first = prepare_run(
        dataset=dataset,
        settings=settings,
        run_id="smoke / test",
        output_directory=tmp_path,
        top_k=30,
        categories=(1, 2, 3, 4),
        conversation_limit=1,
        question_limit=5,
        operation_retries=3,
    )
    second = prepare_run(
        dataset=dataset,
        settings=settings,
        run_id="smoke / test",
        output_directory=tmp_path,
        top_k=30,
        categories=(1, 2, 3, 4),
        conversation_limit=1,
        question_limit=5,
        operation_retries=3,
    )

    assert first == second
    assert first["run_id"] == "smoke-test"
    assert first["configuration"]["memory_extraction_profile"] == "conversation"
    assert first["configuration"]["memory_extraction_instructions"] == "powercontext.memory.extract.conversation.v1"
    assert first["candidate_k"] == 30
    assert first["answer_k"] == 30
    assert first["rerank_mode"] == "none"
    assert first["answer_source_content"] is False
    assert "answer_inference_aware" not in first
    assert "answer_unknown_fallback_inference" not in first
    assert first["schema"] == "powercontext.benchmark.locomo.run.v5"
    assert first["generation_temperature"] == 0.0
    assert first["judge_profile"] == "strict"
    assert "secret" not in json.dumps(first)
    assert normalize_run_id("  a/b c  ") == "a-b-c"
    assert scope_id("smoke / test", "conv-26") == "benchmark:locomo:smoke-test:conv-26"


def test_run_manifest_records_inference_aware_answer_policy(tmp_path: Path) -> None:
    dataset = load_locomo(DATASET)
    settings = ServerSettings(
        runtime=RuntimeConfig(memory_extraction_profile=MemoryExtractionProfile.CONVERSATION),
        database=SQLiteConfig(url="sqlite+aiosqlite:////secret/location.db"),
        inference=InferenceConfig(
            generation_model="openai:test-chat",
            embedding_model="openai:test-embedding",
            embedding_profile_id="test-3-unit",
            embedding_dimension=3,
        ),
    )

    manifest = prepare_run(
        dataset=dataset,
        settings=settings,
        run_id="inference-aware",
        output_directory=tmp_path / "treatment",
        top_k=30,
        answer_k=10,
        answer_source_content=True,
        answer_inference_aware=True,
        categories=(3,),
        conversation_limit=None,
        question_limit=None,
        operation_retries=3,
    )

    assert manifest["schema"] == "powercontext.benchmark.locomo.run.v6"
    assert manifest["answer_inference_aware"] is True
    assert manifest["answer_instructions"] == "powercontext.benchmark.locomo.answer.source.inference.v1"
    with pytest.raises(ValueError, match="requires Source expansion"):
        prepare_run(
            dataset=dataset,
            settings=settings,
            run_id="invalid-inference-aware",
            output_directory=tmp_path / "invalid",
            top_k=30,
            answer_inference_aware=True,
            categories=(3,),
            conversation_limit=None,
            question_limit=None,
            operation_retries=3,
        )


def test_run_manifest_records_unknown_fallback_inference_policy(tmp_path: Path) -> None:
    dataset = load_locomo(DATASET)
    settings = ServerSettings(
        runtime=RuntimeConfig(memory_extraction_profile=MemoryExtractionProfile.CONVERSATION),
        database=SQLiteConfig(url="sqlite+aiosqlite:////secret/location.db"),
        inference=InferenceConfig(
            generation_model="openai:test-chat",
            embedding_model="openai:test-embedding",
            embedding_profile_id="test-3-unit",
            embedding_dimension=3,
        ),
    )

    manifest = prepare_run(
        dataset=dataset,
        settings=settings,
        run_id="unknown-fallback-inference",
        output_directory=tmp_path / "fallback",
        top_k=30,
        answer_k=10,
        answer_source_content=True,
        answer_unknown_fallback_inference=True,
        categories=(1, 2, 3, 4),
        conversation_limit=None,
        question_limit=None,
        operation_retries=3,
    )

    assert manifest["schema"] == "powercontext.benchmark.locomo.run.v7"
    assert manifest["answer_unknown_fallback_inference"] is True
    assert manifest["answer_instructions"] == "powercontext.benchmark.locomo.answer.source.unknown_fallback.v1"
    assert manifest["answer_fallback_trigger"] == "normalized-answer-equals-unknown"
    assert manifest["direct_answer_instructions"] == "powercontext.benchmark.locomo.answer.source.v1"
    assert manifest["fallback_answer_instructions"] == "powercontext.benchmark.locomo.answer.source.inference.v1"
    with pytest.raises(ValueError, match="requires Source expansion"):
        prepare_run(
            dataset=dataset,
            settings=settings,
            run_id="invalid-unknown-fallback",
            output_directory=tmp_path / "invalid-fallback",
            top_k=30,
            answer_unknown_fallback_inference=True,
            categories=(3,),
            conversation_limit=None,
            question_limit=None,
            operation_retries=3,
        )


def test_rejudge_manifest_freezes_answers_and_records_independent_judge(tmp_path: Path) -> None:
    dataset = load_locomo(DATASET)
    question = dataset.selected_questions(question_limit=1)[0]
    source_directory = tmp_path / "source"
    output_directory = tmp_path / "rejudge"
    source_directory.mkdir()
    source_manifest = {
        "schema": "powercontext.benchmark.locomo.run.v5",
        "run_id": "source-run",
        "dataset_sha256": dataset.sha256,
        "selected_question_count": 1,
        "categories": [1, 2, 3, 4],
        "conversation_limit": None,
        "question_limit": 1,
        "top_k": 30,
        "candidate_k": 30,
        "answer_k": 10,
        "rerank_mode": "none",
        "rerank_instructions": None,
        "answer_source_content": True,
        "answer_instructions": "powercontext.benchmark.locomo.answer.source.v1",
        "judge_profile": "strict",
        "judge_instructions": "old-judge",
        "configuration": {
            "generation_model": "openai:answer-model",
            "memory_extraction_profile": "conversation",
        },
    }
    (source_directory / "run.json").write_text(json.dumps(source_manifest), encoding="utf-8")
    (source_directory / "observations.jsonl").write_text(
        json.dumps({
            "schema": "powercontext.benchmark.locomo.observation.v2",
            "question_id": question.question_id,
            "sample_id": question.sample_id,
            "category": question.category,
            "question": question.question,
            "gold_answer": question.answer,
            "generated_answer": "frozen answer",
            "status": "ok",
            "metrics": {"llm_judge": 0.0},
        })
        + "\n",
        encoding="utf-8",
    )

    manifest = prepare_rejudge(
        dataset=dataset,
        source_directory=source_directory,
        output_directory=output_directory,
        run_id="qwen topical judge",
        judge_model="openai:qwen3.7-plus",
    )

    assert manifest["run_id"] == "qwen-topical-judge"
    assert manifest["source"]["answer_model"] == "openai:answer-model"
    assert manifest["source"]["answer_contract"]["answer_k"] == 10
    assert manifest["source"]["answer_contract"]["answer_source_content"] is True
    assert "answer_inference_aware" not in manifest["source"]["answer_contract"]
    assert "answer_unknown_fallback_inference" not in manifest["source"]["answer_contract"]
    assert manifest["judge_model"] == "openai:qwen3.7-plus"
    assert manifest["judge_profile"] == "topical"
    assert manifest["judge_instructions"] == "powercontext.benchmark.locomo.judge.topical.v1"
    assert len(manifest["source"]["observations_sha256"]) == 64
    with pytest.raises(ValueError, match="rejudge manifest does not match"):
        prepare_rejudge(
            dataset=dataset,
            source_directory=source_directory,
            output_directory=output_directory,
            run_id="qwen topical judge",
            judge_model="openai:different-judge",
        )


class _CandidatePipeline:
    """Deterministic in-process stand-in for the configured extraction pipeline."""

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,), reason="recorded dialogue")
            for source in request.sources
            if isinstance(source, ContentSource)
        )


class _EmbeddingModel:
    """Deterministic in-process stand-in for the configured embedding profile."""

    profile = EmbeddingProfile(
        profile_id="locomo-test", model="deterministic", dimension=3, distance="l2", normalization="unit"
    )

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


def _use_offline_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Open the runtime with extraction and embeddings that never leave this process."""

    @asynccontextmanager
    async def runtime_factory(config: BuiltinConfig):
        offline = config.model_copy(update={"inference": InferenceConfig()})
        async with open_builtin_runtime(
            offline,
            scheduler_path=tmp_path / "scheduler.db",
            candidate_pipeline=_CandidatePipeline(),
            embedding_model=_EmbeddingModel(),
        ) as runtime:
            yield runtime

    monkeypatch.setattr(runner, "open_builtin_runtime", runtime_factory)


def _synthetic_conversation(sample_id: str) -> LoCoMoConversation:
    return LoCoMoConversation(
        sample_id,
        "Alice",
        "Bob",
        (LoCoMoSession("D1", 1, "1:56 pm on 8 May, 2023", (LoCoMoTurn("D1:1", "Alice", "I bought a book."),)),),
        (),
    )


def _scored_conversation(sample_id: str) -> LoCoMoConversation:
    """One conversation that both ingestion and scoring accept."""

    conversation = _synthetic_conversation(sample_id)
    question = LoCoMoQuestion(
        question_id=f"{sample_id}:q001",
        sample_id=sample_id,
        question="What did Alice buy?",
        answer="A book.",
        category=1,
        evidence_raw=("D1:1",),
        evidence=("D1:1",),
    )
    return LoCoMoConversation(
        conversation.sample_id,
        conversation.speaker_a,
        conversation.speaker_b,
        conversation.sessions,
        (question,),
    )


def test_ingestion_registers_the_scopes_a_later_phase_must_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for #1778: the runner captures into Scopes nothing ever registered.

    The Scope registry assigns Scope ids, so a name the runner derives by itself resolves
    nowhere and the first `capture` fails with ScopeNotFoundError. Ingestion has to register
    each conversation's Scope, and a later phase has to resolve that same Scope rather than
    mint a second one.
    """

    _use_offline_runtime(monkeypatch, tmp_path)
    dataset = LoCoMoDataset(
        path=DATASET,
        sha256="0" * 64,
        conversations=(_synthetic_conversation("conv-26"), _synthetic_conversation("conv-27")),
    )
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'locomo.db'}"),
        inference=InferenceConfig(),
    )

    def ingest() -> dict[str, Any]:
        return asyncio.run(
            runner.ingest_dataset(
                dataset,
                settings=settings,
                run_id="scope-registration",
                output_directory=tmp_path,
                progress=lambda _message: None,
            )
        )

    first = ingest()
    scopes = {sample_id: entry["scope_id"] for sample_id, entry in first["conversations"].items()}
    assert set(scopes) == {"conv-26", "conv-27"}
    assert all(scope.startswith("scp_") for scope in scopes.values())
    assert runner.scope_id("scope-registration", "conv-26") not in scopes.values()
    assert first["resumed_session_count"] == 0
    assert first["newly_processed_session_count"] == 2

    # The same run resolves to the same Scopes, so a later evaluation resumes the ingested
    # sessions instead of capturing them again under freshly minted Scopes.
    second = ingest()
    assert {entry["scope_id"] for entry in second["conversations"].values()} == set(scopes.values())
    assert second["resumed_session_count"] == 2
    assert second["newly_processed_session_count"] == 0


def test_evaluation_requires_the_scope_ingestion_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the #1894 review: evaluation must not register a replacement Scope.

    Evaluating against a database that never ingested this run used to register an empty Scope,
    score empty retrieval as a valid observation, and spend answer and judge requests on it. Those
    observations were then skipped on resume, so a configuration mistake silently produced
    benchmark results. Evaluation has to resolve the Scope ingestion recorded, and reject a
    database that has none before any inference is set up.
    """

    _use_offline_runtime(monkeypatch, tmp_path)
    dataset = LoCoMoDataset(
        path=DATASET,
        sha256="0" * 64,
        conversations=(_scored_conversation("conv-26"),),
    )
    asyncio.run(
        runner.ingest_dataset(
            dataset,
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'ingested.db'}"),
                inference=InferenceConfig(),
            ),
            run_id="scope-required",
            output_directory=tmp_path,
            progress=lambda _message: None,
        )
    )
    recorded = {
        entry["scope_id"] for entry in json.loads((tmp_path / "ingestion.json").read_text())["conversations"].values()
    }
    assert len(recorded) == 1

    def no_inference(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("evaluation must reject a database that never ingested the run before opening models")

    monkeypatch.setattr(runner, "infer_model", no_inference)
    with pytest.raises(ScopeNotFoundError):
        asyncio.run(
            runner.evaluate_dataset(
                dataset,
                settings=ServerSettings(
                    database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}"),
                    inference=InferenceConfig(generation_model="openai:answer-model"),
                ),
                run_id="scope-required",
                output_directory=tmp_path,
                progress=lambda _message: None,
            )
        )

    # No observation may be written for a question that was never ingested.
    assert not (tmp_path / "observations.jsonl").exists()


def test_evaluation_rejects_a_run_without_a_recorded_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--skip-ingestion` in a directory that never ingested may not score anything."""

    _use_offline_runtime(monkeypatch, tmp_path)
    dataset = LoCoMoDataset(
        path=DATASET,
        sha256="0" * 64,
        conversations=(_scored_conversation("conv-26"),),
    )

    def no_inference(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("evaluation must reject a run that never ingested before opening models")

    monkeypatch.setattr(runner, "infer_model", no_inference)
    with pytest.raises(FileNotFoundError, match="ingestion report is missing"):
        asyncio.run(
            runner.evaluate_dataset(
                dataset,
                settings=ServerSettings(
                    database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}"),
                    inference=InferenceConfig(generation_model="openai:answer-model"),
                ),
                run_id="never-ingested",
                output_directory=tmp_path,
                progress=lambda _message: None,
            )
        )
