"""Focused tests for the deterministic LoCoMo benchmark boundary."""

import json
from pathlib import Path

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
from benchmark.locomo.runner import normalize_run_id, prepare_run, scope_id
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
    assert first["generation_temperature"] == 0.0
    assert first["judge_profile"] == "strict"
    assert "secret" not in json.dumps(first)
    assert normalize_run_id("  a/b c  ") == "a-b-c"
    assert scope_id("smoke / test", "conv-26") == "benchmark:locomo:smoke-test:conv-26"
