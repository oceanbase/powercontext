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

"""Released judge scales, replay, and failure-aware reporting behavior."""

import hashlib
import json

import pytest

from benchmark.locomo_plus.metrics import render_summary, summarize_observations
from benchmark.locomo_plus.prompts import (
    ANSWER_INSTRUCTIONS,
    build_answer_input,
    build_claim_input,
    build_judge_input,
    parse_judge_response,
    replay_claims,
    replay_judgment,
)


def test_generation_contains_only_evidence_and_request_without_task_disclosure() -> None:
    rendered = build_answer_input(question="What would you suggest?", context="A: I am tired.\nB: Rest well.")

    assert json.loads(rendered) == {
        "conversation_evidence": "A: I am tired.\nB: Rest well.",
        "user_request": "What would you suggest?",
    }
    for hidden in ("cognitive", "gold", "category", "judge", "memory-aware", "causal", "state", "goal", "value"):
        assert hidden not in ANSWER_INSTRUCTIONS.lower()


@pytest.mark.parametrize("category", [1, 3, 4])
def test_partial_credit_is_available_only_for_released_ternary_categories(category: int) -> None:
    result = parse_judge_response('{"label":"partial","reason":"One required detail is missing."}', category)

    assert result["score"] == 0.5


@pytest.mark.parametrize("category", [2, 5, 6])
def test_binary_categories_reject_partial_as_judge_failure(category: int) -> None:
    with pytest.raises(ValueError, match="Invalid judge label"):
        parse_judge_response('{"label":"partial","reason":"Some overlap."}', category)


def test_invalid_judge_text_is_never_converted_to_a_wrong_or_correct_answer() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_judge_response("The answer is incorrect, but I cannot return JSON.", 6)


@pytest.mark.parametrize("support", ["you had an ankle fracture", "you had\nan ankle fracture"])
def test_judge_support_cannot_bridge_individual_claims(support: str) -> None:
    frozen = build_judge_input(
        category=6,
        evidence="You had an ankle fracture.",
        prediction="you had a cold, while your brother had an ankle fracture",
        question="I walked to work today",
        memory_claims=["you had", "an ankle fracture"],
    )
    verdict = {
        "label": "correct",
        "reason": "Recalls the injury.",
        "prediction_support": support,
        "historical_support": "You had an ankle fracture.",
    }
    with pytest.raises(ValueError, match="support must quote"):
        replay_judgment(frozen, json.dumps(verdict))
    verdict["prediction_support"] = "an ankle fracture"
    assert replay_judgment(frozen, json.dumps(verdict))["score"] == 1
    with pytest.raises(ValueError, match="Invalid judge label"):
        parse_judge_response('{"label":"unknown","reason":"API failed."}', 6)
    result = parse_judge_response('```json\n{"label":"wrong","reason":"No connection."}\n```', 6)
    assert result["score"] == 0


def test_saved_judge_input_roundtrips_and_replays_without_gold_for_cognitive() -> None:
    saved = build_judge_input(
        category=6,
        evidence="小明 avoids peanuts.",
        prediction="Choose a nut-free meal.",
        gold="unused",
        question="I booked a restaurant.",
        memory_claims=["nut-free meal"],
    )
    persisted = json.loads(json.dumps(saved, ensure_ascii=False))
    raw = (
        '{"label":"correct","reason":"Reflects the dietary constraint.",'
        '"prediction_support":"nut-free meal","historical_support":"avoids peanuts"}'
    )

    assert persisted == build_judge_input(
        category="Cognitive",
        evidence="小明 avoids peanuts.",
        prediction="Choose a nut-free meal.",
        gold="different",
        question="I booked a restaurant.",
        memory_claims=["nut-free meal"],
    )
    assert json.loads(saved["input"]) == {
        "historical_evidence": "小明 avoids peanuts.",
        "candidate_claims": ["nut-free meal"],
    }
    assert replay_judgment(persisted, raw)["score"] == 1
    persisted["input"] += " changed"
    with pytest.raises(ValueError, match="digest does not match"):
        replay_judgment(persisted, raw)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_frozen_judgments_remain_replayable(version: int) -> None:
    saved = {
        "version": f"powercontext.benchmark.locomo_plus.judge.release-semantics.v{version}",
        "category": "Cognitive",
        "instructions": "Historical saved instructions.",
        "input": '{"evidence":"avoids peanuts","prediction":"nut-free meal"}',
    }
    saved["sha256"] = hashlib.sha256(
        json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert replay_judgment(saved, '{"label":"correct","reason":"Respects the restriction."}')["score"] == 1


def test_judge_cannot_credit_an_invented_supporting_quote() -> None:
    saved = build_judge_input(
        category=6,
        evidence="I have a dog allergy.",
        question="I booked a hotel.",
        prediction="Have a good trip.",
        memory_claims=["Have a good trip."],
    )
    raw = json.dumps({
        "label": "correct",
        "reason": "Avoids allergens.",
        "prediction_support": "Choose a pet-free hotel",
        "historical_support": "dog allergy",
    })
    with pytest.raises(ValueError, match="quote"):
        replay_judgment(saved, raw)


def test_projection_is_evidence_blind_and_rejects_invented_claims() -> None:
    frozen = build_claim_input(question="I booked a hotel.", prediction="Choose a pet-free room for your allergy.")
    assert json.loads(frozen["input"]) == {
        "current_request": "I booked a hotel.",
        "response": "Choose a pet-free room for your allergy.",
    }
    assert replay_claims(frozen, '{"claims":["pet-free room for your allergy"]}') == ["pet-free room for your allergy"]
    with pytest.raises(ValueError, match="quote"):
        replay_claims(frozen, '{"claims":["You had a broken wrist"]}')
    assert replay_claims(frozen, '{"claims":[]}') == []


def test_negative_judgments_do_not_require_support_for_a_claim_they_reject() -> None:
    saved = build_judge_input(
        category=6,
        evidence="I have a dog allergy.",
        prediction="Have a good trip.",
        memory_claims=["Have a good trip."],
    )
    raw = '{"label":"wrong","reason":"No historical connection.","prediction_support":"empty","historical_support":"empty"}'
    verdict = replay_judgment(saved, raw)
    assert verdict["score"] == 0
    assert verdict["prediction_support"] == "empty"


def test_report_separates_execution_failures_from_scored_wrong_answers() -> None:
    observations = [
        {"category": 6, "relation_type": "causal", "status": "ok", "judge": {"score": 1}, "generated_answer": "Rest."},
        {"category": 6, "relation_type": "value", "status": "ok", "judge": {"score": 0}, "generated_answer": "Unknown"},
        {"category": 4, "status": "ok", "judge": {"score": 0.5}, "generated_answer": "Paris"},
        *[
            {"category": 6, "status": "error", "error": {"stage": stage}}
            for stage in ("infrastructure", "retrieval", "generation", "judge")
        ],
    ]

    report = summarize_observations(observations, planned_count=8)

    assert "SUBSET" in report["result_label"]
    assert report["overall"]["quality_rate_on_completed"] == 0.5
    assert report["overall"]["end_to_end_success_rate"] == 1.5 / 8
    assert report["overall"]["wrong_count"] == 1
    assert report["overall"]["failure_count"] == 4
    assert report["overall"]["unobserved_count"] == 1
    assert report["overall"]["failures_by_stage"] == {
        "dataset": 0,
        "infrastructure": 1,
        "retrieval": 1,
        "generation": 1,
        "judge": 1,
    }
    assert report["overall"]["abstention_rate"] == 1 / 3
    assert report["cognitive"]["quality_rate_on_completed"] == 0.5
    assert report["factual"]["quality_rate_on_completed"] == 0.5
    assert set(report["by_cognitive_constraint"]) == {"causal", "value"}
    assert report["cognitive_constraint_labels"]["unlabeled_count"] == 4


def test_missing_cognitive_labels_and_invalid_scores_are_not_invented() -> None:
    report = summarize_observations([
        {"category": 6, "status": "ok", "judge": {"score": 0.5}, "generated_answer": "No."},
        {"category": 6, "status": "error", "failure_stage": "dataset"},
    ])

    assert "by_cognitive_constraint" not in report
    assert report["overall"]["quality_rate_on_completed"] is None
    assert report["overall"]["end_to_end_success_rate"] == 0
    assert report["overall"]["wrong_count"] == 0
    assert report["overall"]["failures_by_stage"]["judge"] == 1
    assert report["overall"]["failures_by_stage"]["dataset"] == 1


def test_resource_accounting_preserves_unknown_prices_and_counts_ingestion_once() -> None:
    observations = [
        {
            "category": 6,
            "status": "ok",
            "judge": {"score": 1},
            "latency_ms": {"query": 10, "generation": 20},
            "context": {"bytes": 12, "tokens": 3, "tokenizer": "character-estimate-ceil-div4-v1"},
            "usage": {"generation": {"requests": 1, "input_tokens": 20, "output_tokens": 4, "cost_usd": None}},
            "citation": {"available": True, "exact_source_content": False, "target_evidence_available": None},
        },
        {
            "category": 6,
            "status": "error",
            "failure_stage": "judge",
            "generated_answer": "Unknown",
            "latency_ms": {"query": 30, "generation": 40},
            "context": {"bytes": 8, "tokens": 2, "tokenizer": "character-estimate-ceil-div4-v1"},
            "usage": {"generation": {"requests": 1, "input_tokens": 10, "output_tokens": 2, "cost_usd": 0.001}},
            "citation": {"available": False, "exact_source_content": True, "target_evidence_available": False},
        },
    ]
    report = summarize_observations(
        observations,
        scope="full",
        prepare_latency_ms=250,
        ingestion_usage={"requests": 1, "input_tokens": 100, "output_tokens": 10, "cost_usd": None},
    )

    assert "FULL" in report["result_label"]
    assert report["prepare_latency_ms"] == 250
    assert report["overall"]["latency_ms"]["query"]["p50"] == 20
    assert report["overall"]["context"]["bytes"]["sum"] == 20
    assert report["overall"]["context"]["estimated_token_observations"] == 2
    assert report["overall"]["citation"]["source_provenance_available"]["rate"] == 0.5
    assert report["overall"]["citation"]["exact_source_content_available"]["rate"] == 0
    assert report["overall"]["citation"]["target_evidence_available"]["applicable_count"] == 1
    assert report["overall"]["abstention_rate"] == 1
    assert report["usage"]["generation"]["input_tokens"] == 30
    assert report["usage"]["generation"]["cost_usd"] is None
    assert report["usage"]["generation"]["known_cost_usd"] == 0.001
    assert report["usage"]["ingestion"]["input_tokens"] == 100


def test_markdown_report_discloses_full_profile_subset_and_unknown_cost() -> None:
    report = summarize_observations([
        {"category": 6, "status": "ok", "judge": {"score": 1}, "usage": {"judge": {"requests": 1}}}
    ])
    report.update({
        "run_id": "reviewable-run",
        "selection": {"requested_profile": "full", "excluded_count": 44, "max_history_sessions": 1},
        "configuration": {"generation_model": "generator-id", "judge_model": "judge-id"},
    })

    rendered = render_summary(report)

    assert "LoCoMo-Plus SUBSET result" in rendered
    assert "Requested profile: full" in rendered
    assert "Excluded dataset cases: 44" in rendered
    assert "History sessions per case: 1" in rendered
    assert "reviewable-run" in rendered
    assert "generator-id" in rendered
    assert "judge-id" in rendered
    assert "| judge | 1 | unknown | unknown | unknown | unknown | unknown |" in rendered
