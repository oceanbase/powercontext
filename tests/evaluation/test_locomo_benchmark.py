"""Focused tests for the deterministic LoCoMo benchmark boundary."""

import json
from pathlib import Path

import pytest

from benchmark.locomo.dataset import load_locomo, render_session
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
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, MemoryExtractionProfile, RuntimeConfig
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
