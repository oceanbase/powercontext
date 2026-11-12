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
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from powercontext_eval_longmemeval_v2.report import (
    REPORT_BANNER,
    ReportError,
    build_report,
)

GOLD_SENTINEL = "the gold reference answer that must never reach a report"
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),
)
FORBIDDEN_KEYS = frozenset(
    {
        "reference_answer",
        "gold",
        "gold_answer",
        "api_key",
        "auth_token",
        "token",
        "token_env",
        "password",
        "secret",
    }
)
AUDIT_V1 = "powercontext.longmemeval-v2-memory-audit.v1"
AUDIT_V2 = "powercontext.longmemeval-v2-memory-audit.v2"
DIAGNOSTICS_SCHEMA = "powercontext.longmemeval-v2-prepared-context-diagnostics.v1"


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
        [
            {"schema": AUDIT_V1, "operation": "ingest", "status": "succeeded", "timings_ms": {"total": 4.0}},
            {"schema": AUDIT_V1, "operation": "query", "status": "succeeded", "timings_ms": {"total": 9.0}},
        ],
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
        {
            "question_count": 10,
            "failed": 0,
            "memory_context_tokens": 1234,
            "elapsed_ms": 7.0,
        },
    )
    _write_jsonl(
        run / "04-reader" / "reader-outputs.jsonl",
        [{"question_id": "q0", "reader_latency_ms": 5.0}] * 10,
    )
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
            {
                "question_id": "q2",
                "evaluator": "llm_gotchas_checker",
                "label": 1,
                "judge_latency_ms": 5.0,
            },
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
        {
            "question_count": 10,
            "correct": 4,
            "incorrect": 6,
            "failed": 0,
            "accuracy": 0.4,
        },
    )
    # The score stage keeps local reference answers for replay. A report must never read or copy them.
    _write_jsonl(
        run / "05-score" / "scoring-inputs.local.jsonl",
        [
            {
                "question_id": "q0",
                "reference_answer": GOLD_SENTINEL,
                "api_key": "sk-abcdefghijklmnop",
            }
        ],
    )
    return run


def test_report_summarizes_a_successful_run_from_saved_artifacts(
    tmp_path: Path,
) -> None:
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
    assert report["accuracy"] == {
        "correct": 4,
        "incorrect": 6,
        "failed": 0,
        "value": 0.4,
    }
    assert report["latency_ms"] == {
        "ingest": 4.0,
        "retrieval": 20.0,
        "prepare": 7.0,
        "reader": 50.0,
        "judge": 12.0,
    }
    assert report["context"] == {
        "items": 30,
        "bytes": 900,
        "tokens": 1234,
        "citations_available": 12,
    }
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
    assert report["abstention"] == {
        "count": 2,
        "correct": 1,
        "incorrect": 1,
        "unavailable": False,
    }
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
    assert "evaluation/memory/longmemeval_v2/docs/longmemeval-v2-full-run.md" in markdown


def test_report_uses_relative_latency_from_saved_artifacts_only(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    result = build_report(run_dir=run)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert report["latency_ms"]["ingest"] == 4.0  # only the ingest audit row, never the query row
    assert report["latency_ms"]["retrieval"] == 20.0  # ten saved queries at 2 ms each


def test_report_counts_failures_by_class_from_run_and_stage_artifacts(
    tmp_path: Path,
) -> None:
    run = _successful_run(tmp_path)
    _write_jsonl(
        run / "failures.jsonl",
        [
            {
                "phase": "reader",
                "error_class": "configuration",
                "summary": "missing key",
            },
            {"phase": "reader", "error_class": "generation", "summary": "http 500"},
        ],
    )
    _write_jsonl(
        run / "04-reader" / "reader-failures.jsonl",
        [{"question_id": "q1", "phase": "reader"}],
    )
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
    manifest["cost_policy"] = {
        "reader": PRICE_POLICY_RECORD,
        "judge": PRICE_POLICY_RECORD,
    }
    _write_json(run / "run-manifest.json", manifest)
    reader_summary = json.loads((run / "04-reader" / "reader-summary.json").read_text(encoding="utf-8"))
    reader_summary["cost"] = _stage_cost_record(cost_usd=reader_cost)
    _write_json(run / "04-reader" / "reader-summary.json", reader_summary)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_calls"] = 3
    score_summary["judge_cost"] = _stage_cost_record(cost_usd=judge_cost)
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    return run


def test_report_sums_stage_costs_recorded_under_one_price_policy(
    tmp_path: Path,
) -> None:
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


def test_report_keeps_the_total_null_when_an_executed_judge_cost_block_is_missing(
    tmp_path: Path,
) -> None:
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


def test_report_keeps_the_total_null_when_an_executed_reader_cost_block_is_missing(
    tmp_path: Path,
) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    reader_summary = json.loads((run / "04-reader" / "reader-summary.json").read_text(encoding="utf-8"))
    del reader_summary["cost"]
    _write_json(run / "04-reader" / "reader-summary.json", reader_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "Reader stage ran but its summary recorded no cost block" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_a_configured_reader_summary_is_absent(
    tmp_path: Path,
) -> None:
    """A manifest that configured the Reader but kept no summary must not be totalled."""

    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    shutil.rmtree(run / "04-reader")
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert (
        "configured to run the Reader but no Reader summary recorded a cost" in report["usage"]["estimated_cost_note"]
    )


def test_report_keeps_the_total_null_when_an_executed_stage_was_unpriced(
    tmp_path: Path,
) -> None:
    run = _costed_run(tmp_path, reader_cost=None, judge_cost=0.000147)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert report["usage"]["reader_cost"]["cost_usd"] is None
    assert report["usage"]["reader_cost"]["unavailable_reason"] == "no price policy was configured for this run"
    assert "the Reader recorded non-zero usage without a priced cost" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_stage_costs_use_different_policy_revisions(
    tmp_path: Path,
) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_cost"]["price_policy_revision"] = "deepseek-public-list-2026-10"
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "configured price policy field price_policy_revision" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_the_recorded_model_is_not_the_configured_model(
    tmp_path: Path,
) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_cost"]["model"] = "deepseek-v4-pro"
    _write_json(run / "05-score" / "score-summary.json", score_summary)
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "not the configured 'deepseek-flash'" in report["usage"]["estimated_cost_note"]


def test_report_totals_a_judge_that_made_no_model_call_as_zero_judge_cost(
    tmp_path: Path,
) -> None:
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


def test_report_ignores_a_zero_call_zero_usage_judge_cost_when_its_policy_differs(
    tmp_path: Path,
) -> None:
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


def test_report_rejects_a_zero_call_judge_with_a_priced_nonzero_usage(
    tmp_path: Path,
) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    score_summary = json.loads((run / "05-score" / "score-summary.json").read_text(encoding="utf-8"))
    score_summary["judge_calls"] = 0
    _write_json(run / "05-score" / "score-summary.json", score_summary)

    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "despite zero recorded model calls" in report["usage"]["estimated_cost_note"]


def test_report_rejects_a_priced_cost_that_does_not_match_its_configured_provider(
    tmp_path: Path,
) -> None:
    run = _costed_run(tmp_path, reader_cost=0.00049, judge_cost=0.000147)
    reader_summary = json.loads((run / "04-reader" / "reader-summary.json").read_text(encoding="utf-8"))
    reader_summary["cost"]["provider"] = "anthropic-compatible"
    _write_json(run / "04-reader" / "reader-summary.json", reader_summary)

    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["usage"]["estimated_cost_usd"] is None
    assert "not the configured 'deepseek-openai'" in report["usage"]["estimated_cost_note"]


def test_report_keeps_the_total_null_when_a_judge_without_calls_still_recorded_usage(
    tmp_path: Path,
) -> None:
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
    assert report["abstention"] == {
        "count": 0,
        "correct": None,
        "incorrect": None,
        "unavailable": True,
    }
    assert report["artifacts"]["score"] is None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Accuracy: unavailable" in markdown
    assert markdown.splitlines()[0] == REPORT_BANNER


def test_report_covers_a_model_free_run_without_inventing_accuracy(
    tmp_path: Path,
) -> None:
    run = tmp_path / "model-free"
    _write_json(
        run / "run-manifest.json",
        {"classification": "smoke-subset", "run_id": "model-free"},
    )
    _write_json(
        run / "run-summary.json",
        {
            "classification": "smoke-subset",
            "status": "partial",
            "question_count": 10,
            "accuracy": None,
        },
    )
    _write_json(
        run / "02-retrieval" / "summary.json",
        {"question_count": 10, "failed": 0, "context_bytes": 10},
    )
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
    _write_json(
        run / "run-manifest.json",
        {"classification": "smoke-subset", "run_id": "legacy"},
    )
    _write_json(
        run / "run-summary.json",
        {
            "classification": "smoke-subset",
            "status": "partial",
            "question_count": 10,
            "accuracy": None,
        },
    )
    result = build_report(run_dir=run)

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["experiment_arm"] is None
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Arm: unavailable" in markdown


def test_report_counts_hybrid_mode_mismatch_as_integrity_failures(
    tmp_path: Path,
) -> None:
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


def _available_diagnostics() -> dict[str, Any]:
    return {
        "schema": DIAGNOSTICS_SCHEMA,
        "receipt_status": "available",
        "validation_failure": None,
        "completeness": "complete",
        "candidate_count": None,
        "candidate_count_basis": "not_exposed",
        "receipt_schema": "powercontext.prepared-context-receipt.v1",
        "receipt_id": "private-receipt-id",
        "policy_id": "powercontext.prepared-context-receipt.v1",
        "content_digest": "private-content-digest",
        "used_bytes": 150,
        "requested_max_bytes": 512,
        "truncated": True,
        "non_deterministic": True,
        "selected": [
            {
                "kind": "atomic-memory",
                "artifact_address": {
                    "scope_id": "private-scope-id",
                    "artifact": {"family": "atomic-memory", "artifact_id": "private-artifact-id", "revision": "1"},
                },
                "rendered_bytes": 100,
                "truncated": True,
            },
            {
                "kind": "code",
                "code_evidence": {
                    "scope_id": "private-code-scope-id",
                    "fingerprint": "private-fingerprint",
                    "file_sha256": "private-file-digest",
                    "snippet_sha256": "private-snippet-digest",
                    "start_line": 1,
                    "end_line": 4,
                },
                "rendered_bytes": 49,
                "truncated": False,
            },
        ],
        "omitted": [
            {"family": "atomic-memory", "reason": "duplicate", "count": 2},
            {"family": "atomic-memory", "reason": "below_min_bytes", "count": 3},
            {"family": "topic-memory", "reason": "not_retrieved", "count": 1},
        ],
        "retrieval": {
            "rounds": [
                {"round": 0, "outcome": "completed", "retained": True},
                {"round": 1, "outcome": "completed", "retained": False},
                {"round": 2, "outcome": "expansion_failed", "retained": False},
            ],
            "searches": [
                {
                    "round": 0,
                    "family": "atomic-memory",
                    "mode": "hybrid",
                    "outcome": "completed",
                    "fallback_reason": "none",
                    "count": 2,
                },
                {
                    "round": 0,
                    "family": "atomic-memory",
                    "mode": "fts",
                    "outcome": "completed",
                    "fallback_reason": "inference_timeout",
                    "count": 1,
                },
                {
                    "round": 1,
                    "family": "atomic-memory",
                    "mode": "fts",
                    "outcome": "completed",
                    "fallback_reason": "reused_fts_fallback",
                    "count": 1,
                },
                {
                    "round": 0,
                    "family": "experience",
                    "mode": "none",
                    "outcome": "not_run",
                    "fallback_reason": "none",
                    "count": 1,
                },
            ],
            "reranks": [
                {
                    "round": 0,
                    "config_id": "private-config-a",
                    "outcome": "selected",
                    "fallback_reason": "none",
                    "count": 2,
                },
                {
                    "round": 0,
                    "config_id": "private-config-b",
                    "outcome": "selected",
                    "fallback_reason": "none",
                    "count": 1,
                },
                {
                    "round": 1,
                    "config_id": "private-config-a",
                    "outcome": "fallback",
                    "fallback_reason": "inference_timeout",
                    "count": 1,
                },
            ],
            "rerank_configs": [
                {
                    "config_id": "private-config-a",
                    "config_digest": "private-config-digest-a",
                    "non_deterministic": True,
                },
                {
                    "config_id": "private-config-b",
                    "config_digest": "private-config-digest-b",
                    "prompt_digest": "private-prompt-digest",
                    "non_deterministic": False,
                },
            ],
        },
        "stages": [
            {"stage": "memory.search", "duration_ms": 4.25, "count": 4},
            {"stage": "memory.rerank", "duration_ms": 2.5, "count": 4},
            {"stage": "context.build", "duration_ms": 1.5, "count": 1},
        ],
    }


def _query_audit(diagnostics: object, *, status: str = "succeeded") -> dict[str, object]:
    return {
        "schema": AUDIT_V2,
        "operation": "query",
        "status": status,
        "prepared_context_diagnostics": diagnostics,
    }


def _report_with_audit(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[dict[str, Any], str]:
    run = _successful_run(tmp_path)
    _write_jsonl(run / "02-retrieval" / "adapter-audit.jsonl", rows)
    result = build_report(run_dir=run)
    return json.loads(result.report_path.read_text(encoding="utf-8")), result.markdown_path.read_text(encoding="utf-8")


def test_report_aggregates_receipts_by_public_fields_with_separate_units(tmp_path: Path) -> None:
    diagnostic = _available_diagnostics()
    second = deepcopy(diagnostic)
    second["selected"] = second["selected"][:1]
    report, markdown = _report_with_audit(
        tmp_path,
        [
            {"schema": AUDIT_V2, "operation": "ingest", "timings_ms": {"total": 4.0}},
            _query_audit(diagnostic),
            _query_audit(second),
        ],
    )

    summary = report["prepared_context_diagnostics"]
    assert summary["status"] == "collected"
    assert summary["completeness"] == "complete"
    assert summary["query_count"] == summary["available_count"] == 2
    assert summary["unavailable_count"] == summary["invalid_count"] == summary["failed_request_count"] == 0
    assert summary["candidate_count"] is None
    assert summary["candidate_count_basis"] == "not_exposed"
    assert summary["selected_count"] == 3
    assert summary["truncated_item_count"] == 2
    assert summary["truncated_receipt_count"] == 2
    assert summary["non_deterministic_count"] == 2
    assert summary["used_bytes"] == 300
    assert summary["requested_max_bytes"] == 1024
    assert summary["selected"] == [
        {"kind": "atomic-memory", "count": 2, "truncated_count": 2, "rendered_bytes": 200, "unit": "items"},
        {"kind": "code", "count": 1, "truncated_count": 0, "rendered_bytes": 49, "unit": "items"},
    ]
    assert summary["omitted"] == [
        {"family": "atomic-memory", "reason": "duplicate", "count": 4, "unit": "items"},
        {"family": "atomic-memory", "reason": "below_min_bytes", "count": 6, "unit": "items"},
        {"family": "topic-memory", "reason": "not_retrieved", "count": 2, "unit": "items"},
    ]
    assert summary["searches"] == [
        dict(row, count=row["count"] * 2, unit="executions") for row in diagnostic["retrieval"]["searches"]
    ]
    assert summary["reranks"] == [
        {"round": 0, "outcome": "selected", "fallback_reason": "none", "count": 6, "unit": "executions"},
        {"round": 1, "outcome": "fallback", "fallback_reason": "inference_timeout", "count": 2, "unit": "executions"},
    ]
    assert summary["rounds"] == [dict(row, count=2, unit="rounds") for row in diagnostic["retrieval"]["rounds"]]
    assert summary["expansion_failure_count"] == 2
    assert summary["stages"] == [
        dict(row, count=row["count"] * 2, duration_ms=row["duration_ms"] * 2, unit="executions")
        for row in diagnostic["stages"]
    ]
    assert report["latency_ms"]["ingest"] == 4.0
    assert report["context"]["items"] == 30
    assert "Prepared context diagnostics:" in markdown
    assert "unit=items" in markdown
    assert "unit=executions" in markdown
    assert "duration_ms=8.5" in markdown


def test_report_marks_v1_diagnostics_as_not_collected_with_null_counts(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    result = build_report(run_dir=run)
    summary = json.loads(result.report_path.read_text(encoding="utf-8"))["prepared_context_diagnostics"]

    assert summary["status"] == summary["completeness"] == "not_collected"
    assert all(value is None for key, value in summary.items() if key.endswith(("count", "bytes")))
    for key in ("selected", "omitted", "searches", "reranks", "rounds", "stages"):
        assert summary[key] is None
    assert "status=not_collected" in result.markdown_path.read_text(encoding="utf-8")


def test_report_preserves_zero_selection_from_a_valid_empty_receipt(tmp_path: Path) -> None:
    diagnostic = _available_diagnostics()
    diagnostic.update(selected=[], used_bytes=0, content_digest=None, truncated=False, non_deterministic=False)
    diagnostic["retrieval"] = {"rounds": [], "searches": [], "reranks": [], "rerank_configs": []}
    diagnostic["omitted"] = [{"family": "atomic-memory", "reason": "not_retrieved", "count": 1}]
    diagnostic["stages"] = []
    report, _ = _report_with_audit(tmp_path, [_query_audit(diagnostic)])

    summary = report["prepared_context_diagnostics"]
    assert summary["completeness"] == "complete"
    assert summary["available_count"] == 1
    assert summary["selected_count"] == summary["truncated_item_count"] == summary["truncated_receipt_count"] == 0
    assert summary["used_bytes"] == 0
    assert summary["selected"] == summary["searches"] == summary["reranks"] == summary["stages"] == []
    assert summary["candidate_count"] is None


@pytest.mark.parametrize(
    "malformation",
    [
        "schema",
        "selected",
        "search_count",
        "family",
        "duration",
        "failure",
        "incomplete",
        "truncated",
        "oversize",
    ],
)
def test_report_marks_malformed_available_diagnostics_as_invalid(tmp_path: Path, malformation: str) -> None:
    diagnostic = _available_diagnostics()
    if malformation == "schema":
        diagnostic["schema"] = "powercontext.longmemeval-v2-prepared-context-diagnostics.v2"
    elif malformation == "selected":
        del diagnostic["selected"]
    elif malformation == "search_count":
        diagnostic["retrieval"]["searches"][0]["count"] = True
    elif malformation == "family":
        diagnostic["omitted"][0]["family"] = "private-unrecognized-family"
    elif malformation == "duration":
        diagnostic["stages"][0]["duration_ms"] = -1
    elif malformation == "failure":
        diagnostic["validation_failure"] = "receipt_schema"
    elif malformation == "incomplete":
        diagnostic["completeness"] = "incomplete"
    elif malformation == "truncated":
        diagnostic["truncated"] = False
    else:
        diagnostic["receipt_id"] = "汉" * 3000
        assert len(json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":"))) < 8192
    report, markdown = _report_with_audit(tmp_path, [_query_audit(diagnostic)])

    summary = report["prepared_context_diagnostics"]
    assert summary["available_count"] == 0
    assert summary["invalid_count"] == summary["incomplete_count"] == 1
    assert summary["completeness"] == "incomplete"
    assert summary["selected_count"] is None
    assert summary["searches"] is None
    assert "private-" not in json.dumps(report) + markdown


def test_report_separates_missing_invalid_unavailable_and_failed_requests(tmp_path: Path) -> None:
    unavailable = {
        "schema": DIAGNOSTICS_SCHEMA,
        "receipt_status": "unavailable",
        "validation_failure": None,
        "completeness": "incomplete",
        "candidate_count": None,
        "candidate_count_basis": "not_exposed",
    }
    invalid = dict(unavailable, receipt_status="invalid", validation_failure="receipt_schema")
    report, _ = _report_with_audit(
        tmp_path,
        [
            _query_audit(unavailable),
            _query_audit(invalid),
            {"schema": AUDIT_V2, "operation": "query", "status": "succeeded"},
            _query_audit(None),
            _query_audit(None, status="failed"),
        ],
    )
    summary = report["prepared_context_diagnostics"]
    assert summary["query_count"] == 5
    assert summary["available_count"] == 0
    assert summary["unavailable_count"] == 1
    assert summary["invalid_count"] == 3
    assert summary["incomplete_count"] == 4
    assert summary["failed_request_count"] == 1
    assert summary["completeness"] == "incomplete"
    for key in ("selected_count", "truncated_item_count", "used_bytes", "omitted", "searches", "reranks", "stages"):
        assert summary[key] is None


@pytest.mark.parametrize("status", ["failed", "unavailable", "invalid"])
def test_report_never_uses_attribution_from_failed_or_untrusted_diagnostics(tmp_path: Path, status: str) -> None:
    diagnostic = _available_diagnostics()
    diagnostic["receipt_status"] = status if status != "failed" else "available"
    report, markdown = _report_with_audit(
        tmp_path, [_query_audit(diagnostic, status="failed" if status == "failed" else "succeeded")]
    )

    summary = report["prepared_context_diagnostics"]
    assert summary["available_count"] == 0
    assert summary["failed_request_count"] == int(status == "failed")
    assert summary["selected_count"] is None
    assert summary["omitted"] is None
    assert summary["stages"] is None
    assert "private-" not in json.dumps(report) + markdown


def test_report_keeps_attribution_partial_when_some_v2_diagnostics_are_missing(tmp_path: Path) -> None:
    report, _ = _report_with_audit(tmp_path, [_query_audit(_available_diagnostics()), _query_audit(None)])
    summary = report["prepared_context_diagnostics"]
    assert summary["completeness"] == "incomplete"
    assert summary["attribution_basis"] == "available_receipts"
    assert summary["available_count"] == summary["invalid_count"] == 1
    assert summary["selected_count"] == 2


@pytest.mark.parametrize("version", [None, "powercontext.longmemeval-v2-memory-audit.v3"])
def test_report_rejects_unknown_audit_schemas_before_writing(tmp_path: Path, version: object) -> None:
    run = _successful_run(tmp_path)
    _write_jsonl(run / "02-retrieval" / "adapter-audit.jsonl", [{"schema": version, "operation": "ingest"}])
    with pytest.raises(ReportError, match="Unsupported memory audit schema"):
        build_report(run_dir=run)
    assert not (run / "report.json").exists()


def test_report_rejects_mixed_audit_versions_even_on_non_query_rows(tmp_path: Path) -> None:
    run = _successful_run(tmp_path)
    _write_jsonl(
        run / "02-retrieval" / "adapter-audit.jsonl",
        [
            {"schema": AUDIT_V1, "operation": "ingest"},
            _query_audit(_available_diagnostics()),
        ],
    )
    with pytest.raises(ReportError, match="Mixed memory audit schemas"):
        build_report(run_dir=run)
    assert not (run / "report.json").exists()


@pytest.mark.parametrize("damaged", [b"{bad json\n", b"[]\n", b"\xff\n"])
def test_report_rejects_malformed_audit_records_instead_of_skipping_them(tmp_path: Path, damaged: bytes) -> None:
    run = _successful_run(tmp_path)
    audit = run / "02-retrieval" / "adapter-audit.jsonl"
    audit.write_bytes(json.dumps(_query_audit(_available_diagnostics())).encode("utf-8") + b"\n" + damaged)
    with pytest.raises(ReportError, match="memory audit|Memory audit"):
        build_report(run_dir=run)
    assert not (run / "report.json").exists()


def test_report_diagnostics_exclude_identities_content_and_open_stage_names(tmp_path: Path) -> None:
    diagnostic = _available_diagnostics()
    diagnostic["stages"].append({"stage": "private-path-or-query", "duration_ms": 0.5, "count": 1})
    diagnostic["content"] = GOLD_SENTINEL
    diagnostic["query"] = "private-query-text"
    diagnostic["query_digest"] = "private-query-digest"
    diagnostic["selected"][1]["code_evidence"]["path"] = "private-repository-path"
    report, markdown = _report_with_audit(tmp_path, [_query_audit(diagnostic)])

    summary = report["prepared_context_diagnostics"]
    assert summary["stages"][-1] == {"stage": "other", "duration_ms": 0.5, "count": 1, "unit": "executions"}
    text = json.dumps(report) + markdown
    assert "private-" not in text
    assert GOLD_SENTINEL not in text
    for key, _ in _walk(summary):
        leaf = key.rsplit(".", 1)[-1]
        assert not leaf.endswith(("_id", "_digest", "_sha256")), key
        assert leaf not in {"artifact_address", "code_evidence", "content", "query", "path"}, key


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
