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


"""Evaluator-only full-row oracle and final-answer grounding; never mounted in a worker."""

# ruff: noqa: TRY003
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from powercontext_datus.capture import reconcile, table_from_json
from powercontext_datus.oracle import ComparisonPolicy, compare_tables
from powercontext_datus.report import CaseVerdict


def model_metrics(records: list[dict[str, Any]], rates: dict[str, str] | None = None) -> dict[str, Any]:
    starts = [r for r in records if r["kind"] == "model_started"]
    ends = [r for r in records if r["kind"] == "model_finished"]
    usage = [r["usage"] for r in ends]
    complete = bool(starts) and len(starts) == len(ends) and all(usage)
    totals: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "cached_tokens"):
        known = complete and all(type(u.get(key)) is int and u[key] >= 0 for u in usage)
        totals[key] = sum(u[key] for u in usage) if known else None
    cost = None
    if rates and all(value is not None for value in totals.values()):
        values = {k: Decimal(v) for k, v in rates.items()}
        if set(values) != {"input_per_million", "output_per_million", "cached_per_million"}:
            raise ValueError("all three frozen token rates required")
        if any(not v.is_finite() or v < 0 for v in values.values()):
            raise ValueError("invalid token rate")
        cost = str(
            (
                (totals["input_tokens"] - totals["cached_tokens"]) * values["input_per_million"]
                + totals["cached_tokens"] * values["cached_per_million"]
                + totals["output_tokens"] * values["output_per_million"]
            )
            / Decimal(1_000_000)
        )
    questions = [r for r in records if r["kind"] == "question_injected"]
    answers = [r for r in records if r["kind"] == "answer_submitted"]
    return {
        "calls_started": len(starts),
        "calls_finished": len(ends),
        "usage_complete": complete,
        **totals,
        "cost": cost,
        "cost_basis": "frozen_supplied_rates" if cost is not None else None,
        "http_attempts": sum(r["kind"] == "http_started" and r["online"] for r in records),
        "http_failures": sum(
            r["kind"] == "http_failure" or (r["kind"] == "http_finished" and r["status_code"] >= 400) for r in records
        ),
        "latency_seconds": (
            (answers[0]["monotonic_ns"] - questions[0]["monotonic_ns"]) / 1e9
            if len(answers) == len(questions) == 1
            else None
        ),
        "capture_latency_seconds": (
            (records[-1]["monotonic_ns"] - records[0]["monotonic_ns"]) / 1e9 if records else None
        ),
    }


def evaluate_case(
    task_id: str, run: dict[str, Any], oracle: dict[str, Any], *, state_valid: bool, data_version_verified: bool
) -> tuple[CaseVerdict, dict[str, Any]]:
    records = run["records"]
    trace = reconcile(records)
    policy = ComparisonPolicy(
        ordered=oracle.get("ordered", False),
        absolute_tolerance=Decimal(oracle.get("absolute_tolerance", "0")),
        relative_tolerance=Decimal(oracle.get("relative_tolerance", "0")),
    )
    expected = table_from_json(oracle["expected"])
    declared = table_from_json(oracle["declared_answer"])
    consistent = compare_tables(expected, declared, policy) and data_version_verified
    answers = [r for r in records if r["kind"] == "answer_submitted"]
    actual = None
    grounded = False
    answer_error = None
    if len(answers) == 1:
        submitted = answers[0]
        final_sql = submitted["output"]["sql_query_final"].strip()
        matching = [r for r in trace["sql_results"] if r["sql"].strip() == final_sql]
        if matching:
            try:
                actual = table_from_json(matching[-1])
                answer = json.loads(submitted["answer"])
                # Grounding is exact, independent of the oracle's permitted
                # numerical tolerance. It cannot borrow the expected answer.
                grounded = set(answer) == {"columns", "rows"} and compare_tables(table_from_json(answer), actual)
            except (ValueError, TypeError, KeyError):
                answer_error = "unsupported_or_inaccurate_final_answer"
    correct = actual is not None and compare_tables(actual, expected, policy)
    failures: set[str] = set()
    if run.get("control_failure"):
        failures.add(run["control_failure"])
    if run["timeout"]:
        failures.add("timeout/cancel")
    if run["returncode"] not in (0, None):
        failures.add("environment/auth")
    if any(op["failed"] for op in trace["operations"]):
        failures.add(
            "sql_error" if any(op["failed"] and op["name"] == "sql" for op in trace["operations"]) else "tool_error"
        )
    if run["malformed_output"]:
        failures.add("trace_missing")
    complete = trace["trace_complete"] and not run["malformed_output"] and run["returncode"] == 0
    verdict = CaseVerdict(
        task_id=task_id,
        correct=correct,
        steps=trace["steps"] if complete else None,
        native_execution=bool(trace["sql_results"]),
        answer_grounded=grounded,
        trace_complete=complete,
        oracle_consistent=consistent,
        state_valid=state_valid,
        failures=tuple(sorted(failures)),
    )
    return verdict, {**trace, "answer_error": answer_error, "metrics": model_metrics(records)}
