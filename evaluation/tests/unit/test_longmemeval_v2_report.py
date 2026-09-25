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

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from powercontext_eval.benchmarks.longmemeval_v2.report import REPORT_BANNER, ReportError, build_report

GOLD_SENTINEL = "the gold reference answer that must never reach a report"
SECRET_PATTERNS = (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), re.compile(r"eyJ[A-Za-z0-9_-]{10,}"))
FORBIDDEN_KEYS = frozenset(
    {"reference_answer", "gold", "gold_answer", "api_key", "auth_token", "token", "token_env", "password", "secret"}
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def _successful_run(root: Path) -> Path:
    run = root / "run"
    _write_json(
        run / "run-manifest.json",
        {
            "schema": "powercontext.longmemeval-v2-smoke-run.v1",
            "classification": "smoke-subset",
            "run_id": "smoke-v1",
            "experiment_arm": {
                "id": "current-memory-hybrid-v1",
                "search_mode": "hybrid",
                "memory_projection": "deterministic-compact-v1",
                "query_projection": "question-text-v1",
                "temporal_filter": None,
                "task_lens": None,
            },
        },
    )
    _write_json(
        run / "run-summary.json",
        {
            "schema": "powercontext.longmemeval-v2-smoke-run-summary.v1",
            "classification": "smoke-subset",
            "run_id": "smoke-v1",
            "status": "completed",
            "question_count": 10,
            "accuracy": {"correct": 4, "incorrect": 6, "failed": 0, "value": 0.4},
            "artifacts": {"retrieval": "02-retrieval"},
        },
    )
    _write_jsonl(
        run / "02-retrieval" / "retrieval-results.jsonl",
        [
            {
                "question_id": f"q{index}",
                "status": "succeeded",
                "memory_context": [{"type": "text", "value": "x"}] * 3,
                "timings_ms": {"search": 1.0, "format": 0.5, "total": 2.0},
            }
            for index in range(10)
        ],
    )
    _write_jsonl(
        run / "02-retrieval" / "adapter-audit.jsonl",
        [{"operation": "ingest", "timings_ms": {"total": 4.0}}, {"operation": "query", "timings_ms": {"total": 9.0}}],
    )
    _write_json(
        run / "02-retrieval" / "summary.json",
        {
            "question_count": 10,
            "failed": 0,
            "context_bytes": 900,
            "citation_count": 12,
            "elapsed_ms": 100.0,
        },
    )
    _write_json(
        run / "03-prepare" / "prepare-summary.json",
        {"question_count": 10, "failed": 0, "memory_context_tokens": 1234, "elapsed_ms": 7.0},
    )
    _write_jsonl(run / "04-reader" / "reader-outputs.jsonl", [{"question_id": "q0", "reader_latency_ms": 5.0}] * 10)
    _write_json(
        run / "04-reader" / "reader-summary.json",
        {
            "question_count": 10,
            "failed": 0,
            "usage": {"input_tokens": 1000, "output_tokens": 200},
            "elapsed_ms": 50.0,
        },
    )
    _write_jsonl(
        run / "05-score" / "judge-outputs.jsonl",
        [
            {
                "question_id": "q0",
                "evaluator": "llm_abstention_checker",
                "label": 1,
                "judge_latency_ms": 3.0,
            },
            {
                "question_id": "q1",
                "evaluator": "llm_abstention_checker",
                "label": 0,
                "judge_latency_ms": 4.0,
            },
            {"question_id": "q2", "evaluator": "llm_gotchas_checker", "label": 1, "judge_latency_ms": 5.0},
        ],
    )
    _write_json(
        run / "05-score" / "score-summary.json",
        {
            "question_count": 10,
            "correct": 4,
            "incorrect": 6,
            "failed": 0,
            "accuracy": 0.4,
            "judge_usage": {"input_tokens": 300, "output_tokens": 60},
            "elapsed_ms": 40.0,
        },
    )
    _write_json(
        run / "06-replay" / "replay-summary.json",
        {"question_count": 10, "correct": 4, "incorrect": 6, "failed": 0, "accuracy": 0.4},
    )
    # The score stage keeps local reference answers for replay. A report must never read or copy them.
    _write_jsonl(
        run / "05-score" / "scoring-inputs.local.jsonl",
        [{"question_id": "q0", "reference_answer": GOLD_SENTINEL, "api_key": "sk-abcdefghijklmnop"}],
    )
    return run


def test_report_summarizes_a_successful_run_from_saved_artifacts(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "powercontext.longmemeval-v2-smoke-report.v1"
    assert report["classification"] == "smoke-subset"
    assert report["status"] == "completed"
    assert report["experiment_arm"] == {
        "id": "current-memory-hybrid-v1",
        "search_mode": "hybrid",
        "memory_projection": "deterministic-compact-v1",
        "query_projection": "question-text-v1",
        "temporal_filter": None,
        "task_lens": None,
    }
    assert report["question_count"] == 10
    assert report["accuracy"] == {"correct": 4, "incorrect": 6, "failed": 0, "value": 0.4}
    assert report["latency_ms"] == {
        "ingest": 4.0,
        "retrieval": 20.0,
        "prepare": 7.0,
        "reader": 50.0,
        "judge": 12.0,
    }
    assert report["context"] == {"items": 30, "bytes": 900, "tokens": 1234, "citations_available": 12}
    assert report["usage"] == {
        "ingestion_tokens": 0,
        "ingestion_tokens_note": (
            "the PowerContext Memory adapter ingests without a model, so no provider reports ingestion usage"
        ),
        "ingestion_cost": {
            "stage": "ingestion",
            "input_tokens": 0,
            "input_cache_hit_tokens": 0,
            "input_cache_miss_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "reason": (
                "the PowerContext Memory adapter ingests without a model, so no provider reports "
                "ingestion usage and ingestion cost is zero"
            ),
        },
        "reader_input_tokens": 1000,
        "reader_output_tokens": 200,
        "judge_input_tokens": 300,
        "judge_output_tokens": 60,
        "reader_cost": None,
        "judge_cost": None,
        "estimated_cost_usd": None,
        "estimated_cost_note": (
            "the Reader stage ran but its summary recorded no cost block; "
            "the Judge made 1 model call(s) but recorded no priced cost"
        ),
    }
    assert report["abstention"] == {"count": 2, "correct": 1, "incorrect": 1, "unavailable": False}
    assert report["failures"] == {
        "configuration": 0,
        "infrastructure": 0,
        "retrieval": 0,
        "generation": 0,
        "judge": 0,
        "integrity": 0,
    }
    assert report["artifacts"]["retrieval"] == "02-retrieval"
    assert report["artifacts"]["score"] == "05-score"

    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert markdown.splitlines()[0] == REPORT_BANNER
    assert "Accuracy: 4/10 (0.4)" in markdown
    assert "Arm: current-memory-hybrid-v1" in markdown
    assert "not a complete benchmark result" in markdown
    assert "evaluation/docs/longmemeval-v2-full-run.md" in markdown


def test_report_uses_relative_latency_from_saved_artifacts_only(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    result = build_report(run_dir=run)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert report["latency_ms"]["ingest"] == 4.0  # only the ingest audit row, never the query row
    assert report["latency_ms"]["retrieval"] == 20.0  # ten saved queries at 2 ms each


def test_report_counts_failures_by_class_from_run_and_stage_artifacts(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    _write_jsonl(
        run / "failures.jsonl",
        [
            {"phase": "reader", "error_class": "configuration", "summary": "missing key"},
            {"phase": "reader", "error_class": "generation", "summary": "http 500"},
        ],
    )
    _write_jsonl(run / "04-reader" / "reader-failures.jsonl", [{"question_id": "q1", "phase": "reader"}])
    _write_jsonl(
        run / "02-retrieval" / "failures.jsonl",
        [
            {"question_id": "q2", "phase": "query", "category": "integration"},
            {"question_id": "q3", "phase": "query", "category": "infrastructure"},
        ],
    )
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["failures"] == {
        "configuration": 1,
        "infrastructure": 1,
        "retrieval": 1,
        "generation": 2,
        "judge": 0,
        "integrity": 0,
    }


PRICE_POLICY_RECORD = {
    "provider": "deepseek-openai",
    "model": "deepseek-flash",
    "currency": "USD",
    "input_cache_hit_price_per_million": 0.006,
    "input_cache_miss_price_per_million": 0.3,
    "output_price_per_million": 1.2,
    "price_policy_revision": "deepseek-public-list-2026-09",
}


def _stage_cost_record(*, cost_usd: float | None, model: str = "deepseek-flash") -> dict[str, object]:
    priced = cost_usd is not None
    return {
        "provider": "deepseek-openai",
        "model": model,
        "input_tokens": 1300,
        "input_cache_hit_tokens": 1000,
        "input_cache_miss_tokens": 300,
        "output_tokens": 260,
        "cost_usd": cost_usd,
        "currency": "USD" if priced else None,
        "input_cache_hit_price_per_million": 0.006 if priced else None,
        "input_cache_miss_price_per_million": 0.3 if priced else None,
        "output_price_per_million": 1.2 if priced else None,
        "price_policy_revision": "deepseek-public-list-2026-09" if priced else None,
        "unavailable_reason": None if priced else "no price policy was configured for this run",
    }


def _costed_run(tmp_path: Path, *, reader_cost: float | None, judge_cost: float | None) -> Path:
    """Build a run whose manifest says both model stages ran and record stage costs."""

    run = _successful_run(tmp_path)
    manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
    manifest["modes"] = {"reader": True, "score": True}
    manifest["reader"] = {"provider": "deepseek-openai", "model": "deepseek-flash"}
    manifest["judge"] = {"provider": "deepseek-openai", "model": "deepseek-flash"}
    manifest["cost_policy"] = {"reader": PRICE_POLICY_RECORD, "judge": PRICE_POLICY_RECORD}
    _write_json(run / "run-manifest.json", manifest)
    reader_summary = json.loads((run / "04-reader" / "reader-summary.json").read_text(encoding="utf-8"))
    reader_summary["cost"] = _stage_cost_record(cost_usd=reader_cost)
    _write_json(run / "04-reader" / "reader-summary.json", reader_summary)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_calls"] = 3
    score_summary["judge_cost"] = _stage_cost_record(cost_usd=judge_cost)
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    return run


def test_report_sums_stage_costs_recorded_under_one_price_policy(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["reader_cost"]["cost_usd"] == 0.00049
    assert report["usage"]["judge_cost"]["cost_usd"] == 0.000147
    assert report["usage"]["estimated_cost_usd"] == 0.000637
    assert report["usage"]["estimated_cost_note"] == (
        "sum of the Reader and Judge usage costs recorded under the run's explicit price policy"
    )
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Cost: " in markdown
    assert "total_usd=0.000637" in markdown


def test_report_keeps_the_total_null_when_an_executed_judge_cost_block_is_missing(tmp_path: Path) -> None:
    """A Judge that ran without a recorded cost block must not be silently excluded."""

    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    del score_summary["judge_cost"]
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["reader_cost"]["cost_usd"] == 0.00049
    assert report["usage"]["estimated_cost_usd"] is None
    assert "Judge made 3 model call(s) but recorded no priced cost" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_an_executed_reader_cost_block_is_missing(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    reader_summary = json.loads((run / "04-reader" / "reader-summary.json").read_text(encoding="utf-8"))
    del reader_summary["cost"]
    _write_json(run / "04-reader" / "reader-summary.json", reader_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "Reader stage ran but its summary recorded no cost block" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_a_configured_reader_summary_is_absent(tmp_path: Path) -> None:
    """A manifest that configured the Reader but kept no summary must not be totalled."""

    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    shutil.rmtree(run / "04-reader")
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert (
        "configured to run the Reader but no Reader summary recorded a cost" in report["usage"]["estimated_cost_note"]
    )


def test_report_keeps_the_total_null_when_an_executed_stage_was_unpriced(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=None, judge_cost=0.000147)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert report["usage"]["reader_cost"]["cost_usd"] is None
    assert report["usage"]["reader_cost"]["unavailable_reason"] == "no price policy was configured for this run"
    assert "the Reader recorded non-zero usage without a priced cost" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_stage_costs_use_different_policy_revisions(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_cost"]["price_policy_revision"] = "deepseek-public-list-2026-10"
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "configured price policy field price_policy_revision" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_the_recorded_model_is_not_the_configured_model(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_cost"]["model"] = "deepseek-v4-pro"
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "not the configured 'deepseek-flash'" in report["usage"]["estimated_cost_note"]


def test_report_totals_a_judge_that_made_no_model_call_as_zero_judge_cost(tmp_path: Path) -> None:
    """A score stage with no LLM-judge questions owes no Judge cost and must not block the total."""

    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=None)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_calls"] = 0
    score_summary["judge_usage"] = {"input_tokens": 0, "output_tokens": 0}
    score_summary["judge_cost"] = {
        **_stage_cost_record(cost_usd=None),
        "input_tokens": 0,
        "input_cache_hit_tokens": 0,
        "input_cache_miss_tokens": 0,
        "output_tokens": 0,
        "unavailable_reason": "no price policy was configured for this run",
    }
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] == 0.00049
    assert report["usage"]["estimated_cost_note"] == (
        "sum of the Reader and Judge usage costs recorded under the run's explicit price policy"
    )


def test_report_ignores_a_zero_call_zero_usage_judge_cost_when_its_policy_differs(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.0)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_calls"] = 0
    score_summary["judge_usage"] = {"input_tokens": 0, "output_tokens": 0}
    score_summary["judge_cost"].update(
        {
            "input_tokens": 0,
            "input_cache_hit_tokens": 0,
            "input_cache_miss_tokens": 0,
            "output_tokens": 0,
            "price_policy_revision": "unused-judge-policy",
        }
    )
    _write_json(run / "05-score" / "score-summary.json", score_summary)

    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] == 0.00049


def test_report_rejects_a_zero_call_judge_with_a_priced_nonzero_usage(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_calls"] = 0
    _write_json(run / "05-score" / "score-summary.json", score_summary)

    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "despite zero recorded model calls" in report["usage"]["estimated_cost_note"]


def test_report_rejects_a_priced_cost_that_does_not_match_its_configured_provider(tmp_path: Path) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    reader_summary = json.loads((run / "04-reader" / "reader-summary.json").read_text(encoding="utf-8"))
    reader_summary["cost"]["provider"] = "anthropic-compatible"
    _write_json(run / "04-reader" / "reader-summary.json", reader_summary)

    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "not the configured 'deepseek-openai'" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_a_judge_without_calls_still_recorded_usage(tmp_path: Path) -> None:
    """A block claiming no Judge call but non-zero usage is self-contradictory, so it blocks the total."""

    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=None)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_calls"] = 0
    score_summary["judge_cost"] = _stage_cost_record(cost_usd=None)
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "despite zero recorded model calls" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_without_a_price_policy(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    # The fixture ran both model stages, so both missing priced costs must be named.
    note = report["usage"]["estimated_cost_note"]
    assert "the Reader stage ran but its summary recorded no cost block" in note
    assert "the Judge made 1 model call(s) but recorded no priced cost" in note


def test_report_covers_a_run_that_stopped_after_a_failed_phase(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    for name in ("04-reader", "05-score", "06-replay"):
        shutil.rmtree(run / name)
    _write_json(
        run / "run-summary.json",
        {
            "classification": "smoke-subset",
            "status": "failed",
            "question_count": 10,
            "accuracy": None,
            "completed_phases": ["preflight", "retrieval", "prepare"],
            "failed_phase": "prepare",
        },
    )
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["accuracy"] is None
    assert report["usage"]["reader_input_tokens"] is None
    assert report["latency_ms"]["reader"] is None
    assert report["abstention"] == {"count": 0, "correct": None, "incorrect": None, "unavailable": True}
    assert report["artifacts"]["score"] is None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Accuracy: unavailable" in markdown
    assert markdown.splitlines()[0] == REPORT_BANNER


def test_report_covers_a_model_free_run_without_inventing_accuracy(tmp_path: Path) -> None:
    run = tmp_path / "model-free"
    _write_json(run / "run-manifest.json", {"classification": "smoke-subset", "run_id": "model-free"})
    _write_json(
        run / "run-summary.json",
        {"classification": "smoke-subset", "status": "partial", "question_count": 10, "accuracy": None},
    )
    _write_json(run / "02-retrieval" / "summary.json", {"question_count": 10, "failed": 0, "context_bytes": 10})
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    assert report["accuracy"] is None
    assert report["question_count"] == 10
    assert report["usage"]["estimated_cost_usd"] is None
    assert report["latency_ms"]["judge"] is None


def test_report_never_copies_reference_answers_or_credentials(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    result = build_report(run_dir=run)

    for path in (result.report_path, result.markdown_path):
        text = path.read_text(encoding="utf-8")
        assert GOLD_SENTINEL not in text
        assert "sk-abcdefghijklmnop" not in text
        for pattern in SECRET_PATTERNS:
            assert pattern.search(text) is None
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    for key, value in _walk(report):
        assert key.rsplit(".", 1)[-1].lower() not in FORBIDDEN_KEYS, key
        assert GOLD_SENTINEL not in str(value)


def test_report_refuses_to_overwrite_an_existing_report(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    build_report(run_dir=run)

    with pytest.raises(ReportError, match="Cannot write report artifact"):
        build_report(run_dir=run)


def test_report_can_be_written_to_a_new_separate_directory(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    target = tmp_path / "report"
    result = build_report(run_dir=run, output_dir=target)

    assert result.report_path == target / "report.json"
    assert (target / "report.md").is_file()
    assert not (run / "report.json").exists()

    with pytest.raises(ReportError, match="Refusing to overwrite report artifacts"):
        build_report(run_dir=run, output_dir=target)


def test_report_rejects_a_missing_run_directory(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="does not exist"):
        build_report(run_dir=tmp_path / "absent")


def test_report_of_a_run_without_an_arm_records_no_arm(tmp_path: Path) -> None:
    run = tmp_path / "legacy-run"
    _write_json(run / "run-manifest.json", {"classification": "smoke-subset", "run_id": "legacy"})
    _write_json(
        run / "run-summary.json",
        {"classification": "smoke-subset", "status": "partial", "question_count": 10, "accuracy": None},
    )
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["experiment_arm"] is None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Arm: unavailable" in markdown


def test_report_counts_hybrid_mode_mismatch_as_integrity_failures(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    _write_jsonl(
        run / "02-retrieval" / "failures.jsonl",
        [
            {"question_id": "q0", "phase": "query", "category": "integrity"},
            {"question_id": "q1", "phase": "query", "category": "integration"},
            {"question_id": "q2", "phase": "ingest", "category": "infrastructure"},
        ],
    )
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["failures"]["integrity"] == 1
    assert report["failures"]["retrieval"] == 1
    assert report["failures"]["infrastructure"] == 1


def _walk(value: Any, prefix: str = "") -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_walk(item, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk(item, prefix))
    else:
        found.append((prefix, value))
    return found
