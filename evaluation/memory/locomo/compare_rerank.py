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

"""Validate paired LoCoMo runs before comparing reranking quality and cost."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .metrics import percentile

COMPARISON_BILLING_POLICY = "no-sdk-retries-require-usage.v1"

_SHARED = (
    "run_id",
    "dataset_sha256",
    "categories",
    "candidate_k",
    "answer_k",
    "answer_max_bytes",
    "answer_source_content",
    "answer_instructions",
    "answer_inference_aware",
    "answer_unknown_fallback_inference",
    "answer_fallback_trigger",
    "fallback_answer_instructions",
    "judge_profile",
    "judge_instructions",
    "generation_temperature",
    "configuration",
    "selected_question_count",
    "conversation_limit",
    "question_limit",
    "quality_regression_tolerance",
    "comparison_inference_sha256",
    "comparison_billing_policy",
)


def compare_rerank_runs(  # noqa: C901
    runs: Mapping[str, Path],
    *,
    baseline: str,
    candidate: str,
    input_prices: Mapping[str, float],
    output_prices: Mapping[str, float],
    generation_input_price: float,
    generation_output_price: float,
    retrieval_costs_usd: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Compare named runs, using one baseline for candidate cost and latency.

    The candidate must preserve quality against every other supplied run.
    Run names are caller labels, independent of the recorded reranker mode.
    """

    if baseline == candidate or baseline not in runs or candidate not in runs:
        raise ValueError("select distinct baseline and candidate names from the supplied runs")  # noqa: TRY003
    if any(not name.strip() for name in runs):
        raise ValueError("run names must not be blank")  # noqa: TRY003
    if set(input_prices) != set(runs) or set(output_prices) != set(runs):
        raise ValueError("provide input and output rerank prices for every named run")  # noqa: TRY003
    if any(
        not math.isfinite(price) or price < 0
        for price in (*input_prices.values(), *output_prices.values(), generation_input_price, generation_output_price)
    ):
        raise ValueError("prices must be finite and nonnegative")  # noqa: TRY003
    if retrieval_costs_usd is not None and (
        set(retrieval_costs_usd) != set(runs)
        or any(not math.isfinite(cost) or cost < 0 for cost in retrieval_costs_usd.values())
    ):
        raise ValueError("provide finite nonnegative retrieval costs for every named run")  # noqa: TRY003
    manifests = {mode: _read_json(directory / "run.json") for mode, directory in runs.items()}
    observations = {mode: _read_observations(directory / "observations.jsonl") for mode, directory in runs.items()}
    base = manifests[baseline]
    tolerance = base.get("quality_regression_tolerance")
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(tolerance)
        or not 0 <= tolerance <= 1
    ):
        raise ValueError("runs must pin quality_regression_tolerance before evaluation")  # noqa: TRY003
    if not observations[baseline]:
        raise ValueError("comparison requires observations")  # noqa: TRY003
    for mode, manifest in manifests.items():
        if not isinstance(manifest.get("answer_max_bytes"), int) or manifest["answer_max_bytes"] < 1:
            raise ValueError("comparison requires an explicit shared answer_max_bytes")  # noqa: TRY003
        for field in _SHARED:
            if manifest.get(field) != base.get(field):
                raise ValueError(f"{mode} run changed {field}")  # noqa: TRY003
        if set(observations[mode]) != set(observations[baseline]):
            raise ValueError(f"{mode} run has a different question set")  # noqa: TRY003
        if len(observations[mode]) != manifest.get("selected_question_count"):
            raise ValueError(f"{mode} run has missing selected observations")  # noqa: TRY003
    for question_id in observations[baseline]:
        compared = [values[question_id] for values in observations.values()]
        if any(value.get("question") != compared[0].get("question") for value in compared):
            raise ValueError(f"question changed for {question_id}")  # noqa: TRY003
        if all(value.get("status") == "ok" for value in compared) and (
            len({value.get("candidate_digest") for value in compared}) != 1 or not compared[0].get("candidate_digest")
        ):
            raise ValueError(f"coarse candidate pool changed for {question_id}")  # noqa: TRY003

    scored: dict[str, Any] = {}
    for mode, values in observations.items():
        completed = tuple(value for value in values.values() if value.get("status") == "ok")
        answer_latencies = [latency for value in completed if (latency := _answer_pipeline_latency(value)) is not None]
        latency_complete = bool(completed) and len(answer_latencies) == len(completed)
        denominator = len(values)
        input_tokens = _tokens(completed, ("rerank",), "input_tokens")
        output_tokens = _tokens(completed, ("rerank",), "output_tokens")
        generation_input = _tokens(completed, ("answer", "answer_fallback", "judge"), "input_tokens")
        generation_output = _tokens(completed, ("answer", "answer_fallback", "judge"), "output_tokens")
        answer_input = _tokens(completed, ("answer", "answer_fallback"), "input_tokens")
        answer_output = _tokens(completed, ("answer", "answer_fallback"), "output_tokens")
        retries = sum(sum(value["transient_retries"].values()) for value in completed) + sum(
            value["_checkpoint_attempts"] - 1 for value in values.values()
        )
        embedding_calls = sum(value.get("embedding_calls", 0) for value in completed)
        cost_complete = (
            len(completed) == denominator
            and retries == 0
            and manifests[mode].get("comparison_billing_policy") == COMPARISON_BILLING_POLICY
        )
        rerank_cost = (
            (input_tokens * input_prices[mode] + output_tokens * output_prices[mode]) / 1_000_000
            if cost_complete and input_tokens is not None and output_tokens is not None
            else None
        )
        priced_request_cost = (
            rerank_cost
            + (generation_input * generation_input_price + generation_output * generation_output_price) / 1_000_000
            if rerank_cost is not None and generation_input is not None and generation_output is not None
            else None
        )
        pipeline_cost = (
            priced_request_cost + (retrieval_costs_usd[mode] if retrieval_costs_usd is not None else 0)
            if priced_request_cost is not None and (embedding_calls == 0 or retrieval_costs_usd is not None)
            else None
        )
        answer_pipeline_cost = (
            rerank_cost
            + (answer_input * generation_input_price + answer_output * generation_output_price) / 1_000_000
            + (retrieval_costs_usd[mode] if retrieval_costs_usd is not None else 0)
            if rerank_cost is not None
            and answer_input is not None
            and answer_output is not None
            and (embedding_calls == 0 or retrieval_costs_usd is not None)
            else None
        )
        scored[mode] = {
            "question_count": denominator,
            "error_count": denominator - len(completed),
            "downstream_success": sum(float(value["metrics"]["llm_judge"]) for value in completed) / denominator,
            "evidence_recall": sum(float(value["metrics"]["evidence_recall"]) for value in completed) / denominator,
            "evidence_mrr": sum(float(value["metrics"]["evidence_mrr"]) for value in completed) / denominator,
            "injected_utf8_bytes": sum(int(value["answer_context"]["utf8_bytes"]) for value in completed),
            "rerank_requests": sum(int(value["usage"]["rerank"]["requests"]) for value in completed),
            "rerank_input_tokens": input_tokens,
            "rerank_output_tokens": output_tokens,
            "rerank_cost_usd": rerank_cost,
            "generation_input_tokens": generation_input,
            "generation_output_tokens": generation_output,
            "pipeline_cost_usd": pipeline_cost,
            "answer_pipeline_cost_usd": answer_pipeline_cost,
            "priced_request_cost_usd": priced_request_cost,
            "retrieval_embedding_calls": embedding_calls,
            "cost_complete": pipeline_cost is not None,
            "fallback_count": sum(bool(value["rerank"]["used_fallback"]) for value in completed),
            "total_retries": retries,
            "search_retries": sum(int(value["transient_retries"]["search"]) for value in completed),
            "rerank_latency_ms_p50": percentile([float(value["latency_ms"]["rerank"]) for value in completed], 0.5),
            "rerank_latency_ms_p95": percentile([float(value["latency_ms"]["rerank"]) for value in completed], 0.95),
            "total_latency_ms_p50": percentile([float(value["latency_ms"]["total"]) for value in completed], 0.5),
            "total_latency_ms_p95": percentile([float(value["latency_ms"]["total"]) for value in completed], 0.95),
            "answer_pipeline_latency_ms_p50": (percentile(answer_latencies, 0.5) if latency_complete else None),
            "answer_pipeline_latency_ms_p95": (percentile(answer_latencies, 0.95) if latency_complete else None),
        }
    complete = all(result["error_count"] == 0 for result in scored.values())
    quality_passed = complete and all(
        scored[candidate]["downstream_success"] + tolerance >= result["downstream_success"]
        for name, result in scored.items()
        if name != candidate
    )
    adopt = (
        quality_passed
        and all(result["cost_complete"] for result in scored.values())
        and scored[candidate]["fallback_count"] == 0
        and scored[candidate]["answer_pipeline_cost_usd"] < scored[baseline]["answer_pipeline_cost_usd"]
        and scored[candidate]["answer_pipeline_latency_ms_p95"] is not None
        and scored[baseline]["answer_pipeline_latency_ms_p95"] is not None
        and scored[candidate]["answer_pipeline_latency_ms_p95"] <= scored[baseline]["answer_pipeline_latency_ms_p95"]
    )
    return {
        "schema": "powercontext.benchmark.locomo.rerank-comparison.v3",
        "dataset_sha256": base["dataset_sha256"],
        "run_id": base["run_id"],
        "baseline": baseline,
        "candidate": candidate,
        "rerankers": {
            mode: {
                "mode": manifest.get("rerank_mode"),
                "policy": manifest.get("rerank_instructions"),
                **manifest.get("comparison_reranker", {}),
            }
            for mode, manifest in manifests.items()
        },
        "quality_regression_tolerance": tolerance,
        "comparison_complete": complete,
        "quality_gate_passed": quality_passed,
        "recommendation": "adopt" if adopt else "revise" if quality_passed or not complete else "stop",
        "cost_coverage": (
            "rerank, answer and judge receipts; shared ingestion excluded; "
            "optional measured retrieval costs; total unknown when required receipts are unavailable"
        ),
        "adoption_latency": "search, rerank and answer, including answer fallback; excludes evaluation judge",
        "adoption_cost": "retrieval, rerank and answer, including answer fallback; excludes evaluation judge",
        "retrieval_costs_usd": None if retrieval_costs_usd is None else dict(retrieval_costs_usd),
        "prices_usd_per_million_tokens": {
            mode: {"input": input_prices[mode], "output": output_prices[mode]} for mode in runs
        },
        "generation_prices_usd_per_million_tokens": {
            "input": generation_input_price,
            "output": generation_output_price,
        },
        "results": scored,
    }


def _answer_pipeline_latency(value: Mapping[str, Any]) -> float | None:
    stages = ("search", "rerank", "answer")
    if "answer_fallback" in value.get("usage", {}):
        stages = (*stages, "answer_fallback")
    latencies = [value["latency_ms"].get(stage) for stage in stages]
    if any(type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0 for latency in latencies):
        return None
    return sum(latencies)


def _tokens(records: tuple[dict[str, Any], ...], stages: tuple[str, ...], field: str) -> int | None:
    counts = [
        value["usage"][stage].get(field)
        for value in records
        for stage in stages
        if stage != "answer_fallback" or stage in value["usage"]
    ]
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    return sum(counts)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_observations(path: Path) -> dict[str, dict[str, Any]]:
    observed: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        question_id = record["question_id"]
        record["_checkpoint_attempts"] = observed.get(question_id, {}).get("_checkpoint_attempts", 0) + 1
        observed[question_id] = record
    return observed
