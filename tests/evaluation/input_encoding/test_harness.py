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

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from evaluation.performance.input_encoding.dataset import projection, sources
from evaluation.performance.input_encoding.report import build_report, paired_confidence, summarize, throughput_report
from evaluation.performance.input_encoding.runner import _attempt, plan_jobs, usage_totals
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, GetExperienceRequest, open_builtin_runtime


def _instance():
    return SimpleNamespace(
        instance_id="public-task",
        repo="example/repository",
        problem_statement="Repair the expired-token check without rejecting valid tokens.",
        patch="+++ b/auth.py\n" + "exact diff line\n" * 1000,
        test_patch="+++ b/test_auth.py\n+def test_expired_token(): pass\n",
    )


def test_public_projection_preserves_all_parts_without_fabricating_success():
    instance = _instance()
    records = sources(instance)
    for key, original in (
        ("problem", instance.problem_statement),
        ("reference-patch", instance.patch),
        ("reference-tests", instance.test_patch),
    ):
        assert "".join(row["content"] for row in records if row["metadata"]["kind"] == key) == original
    value = projection(instance)
    assert len(value.evidence) == len(records)
    assert all(not evidence.truncated for evidence in value.evidence)
    assert all(
        json.loads(evidence.content)["metadata"]["test_execution"] == "not_executed_by_this_evaluation"
        for evidence in value.evidence
    )


def test_usage_unknown_failure_does_not_become_zero_or_drop_known_partial_cost():
    assert usage_totals([])["requests"] is None
    assert usage_totals([])["total_tokens"] is None
    result = usage_totals([
        {"requests": 1, "input_tokens": 100, "output_tokens": 20},
        {"requests": 1, "input_tokens": None, "output_tokens": 5, "incomplete": True},
    ])
    assert result["requests"] == 2
    assert result["input_tokens"] is None
    assert result["input_tokens_known_subtotal"] == 100
    assert result["output_tokens"] == 25
    assert result["total_tokens_known_subtotal"] == 125
    assert result["cache_read_tokens"] is None
    assert result["cost"] is None


def test_report_retains_failed_missing_and_no_proposal_attempts():
    rows: list[dict[str, Any]] = [
        {
            "status": "succeeded",
            "usage": {"requests": 2, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            "reopen_equal": True,
        },
        {"status": "failed", "usage": usage_totals([])},
        {"status": "no_proposal", "usage": {"requests": 1, "input_tokens": 8, "output_tokens": 2, "total_tokens": 10}},
    ]
    for row in rows:
        row["usage_by_operation"] = {"generation": row["usage"]}
    result = summarize(rows, expected=4)
    assert result["success_rate"] == 0.25
    assert result["missing_attempts"] == 1
    assert result["usage"]["total_tokens"] == {"known_subtotal": 25, "unknown_attempts": 2, "complete": False}
    assert result["total_tokens_per_success_known_subtotal"] == 25
    assert result["additional_generation_requests_known_subtotal"] == 1


def test_report_keeps_partial_usage_without_certifying_bill_or_paired_savings(tmp_path):
    rows = []
    for mode, status, usage in (
        ("off", "succeeded", {"requests": 1, "input_tokens": 100, "output_tokens": 20, "total_tokens": 120}),
        (
            "shadow",
            "succeeded",
            {
                "requests": 2,
                "input_tokens": None,
                "input_tokens_known_subtotal": 50,
                "output_tokens": 5,
                "total_tokens": None,
                "total_tokens_known_subtotal": 55,
                "incomplete": True,
            },
        ),
        (
            "active",
            "failed",
            # A timeout on a later request can leave only the earlier response's
            # usage, without an incomplete flag from the interrupted provider.
            {"requests": 1, "input_tokens": 80, "output_tokens": 5, "total_tokens": 85, "incomplete": False},
        ),
    ):
        rows.append({
            "attempt_id": mode,
            "instance_id": "public-task",
            "repeat": 0,
            "mode": mode,
            "status": status,
            "usage": usage,
            "usage_by_operation": {"generation": usage},
        })
    (tmp_path / "attempts").mkdir()
    for row in rows:
        (tmp_path / "attempts" / f"{row['mode']}.json").write_text(json.dumps(row))
    (tmp_path / "manifest.json").write_text(json.dumps({"jobs": rows, "repeats": 1}))

    report = build_report(tmp_path)
    assert report["arms"]["off"]["generation_usage"]["total_tokens"]["complete"] is True
    assert report["experiment_known_token_subtotals"]["combined"]["total_tokens"] == 260
    assert report["experiment_known_token_subtotals"]["complete"] is False
    for mode in ("shadow", "active"):
        usage = report["arms"][mode]["generation_usage"]
        assert usage["input_tokens"]["known_subtotal"] == (50 if mode == "shadow" else 80)
        assert usage["total_tokens"]["known_subtotal"] == (55 if mode == "shadow" else 85)
        for field in ("requests", "input_tokens", "output_tokens", "total_tokens"):
            assert usage[field]["unknown_attempts"] == 1
            assert usage[field]["complete"] is False
        assert report["paired_deltas"][mode][0]["output_tokens_delta"] is None
        interval = report["paired_case_bootstrap"][mode]["output_tokens"]
        assert interval["unrepresented_cases"] == 1
        assert interval["mean_delta"] is None
    assert report["arms"]["active"]["failed_cost_completeness"]["status"] == "not_certified"


def test_order_is_balanced_reproducible_and_interleaved():
    jobs = plan_jobs(("a", "b"), 2, 2086)
    assert jobs == plan_jobs(("a", "b"), 2, 2086)
    assert len(jobs) == 12
    for start in range(0, 12, 3):
        group = jobs[start : start + 3]
        assert len({row["instance_id"] for row in group}) == 1
        assert {row["mode"] for row in group} == {"off", "shadow", "active"}


def test_approval_embedding_is_not_a_generation_retry():
    rows: list[dict[str, Any]] = [
        {
            "status": "succeeded",
            "usage": {"requests": 2, "input_tokens": 110, "output_tokens": None},
            "usage_by_operation": {
                "generation": {"requests": 1, "input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                "embedding": {"requests": 1, "input_tokens": 10, "output_tokens": None, "total_tokens": None},
            },
        }
    ]
    report = summarize(rows, 1)
    assert report["additional_generation_requests_known_subtotal"] == 0
    assert report["generation_usage"]["total_tokens"]["known_subtotal"] == 110
    assert report["embedding_usage"]["input_tokens"]["known_subtotal"] == 10
    assert report["usage"]["total_tokens"]["complete"] is False


def test_resume_segment_throughput_excludes_preexisting_attempts(tmp_path):
    (tmp_path / "segments").mkdir()
    (tmp_path / "segments/new.json").write_text(
        json.dumps({
            "segment_id": "new",
            "pending_attempt_ids": ["b"],
            "execution_wall_seconds": 2,
        })
    )
    result = throughput_report(
        tmp_path,
        [
            {"attempt_id": "a", "status": "succeeded", "segment_id": "old"},
            {"attempt_id": "b", "status": "failed", "segment_id": "new"},
        ],
    )[0]
    assert result["new_attempts_completed"] == 1
    assert result["completed_per_second"] == 0.5
    assert result["successes_per_second"] == 0


def test_case_bootstrap_keeps_missing_repeats_out_of_complete_pairs():
    pairs = []
    for case, repeat, value in (("a", 0, 10), ("a", 1, 30), ("b", 0, -100)):
        pairs.append({
            "instance_id": case,
            "repeat": repeat,
            **{
                field + "_delta": value
                for field in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens")
            },
        })
    result = paired_confidence(pairs, planned_cases=2, planned_repeats=2)
    assert result["total_tokens"]["mean_delta"] == 20
    assert result["total_tokens"]["unrepresented_cases"] == 1
    assert result["total_tokens"]["mean_delta_95pct_interval"] == [20, 20]


def test_attempt_saves_public_approval_and_readable_artifact_without_model_usage(tmp_path):
    class Generator:
        async def generate(self, value):
            return ExperienceContent(
                situation="Expired tokens were accepted.",
                action="Check expiration before authentication.",
                outcome="The reference patch includes an expiration check; tests were not executed here.",
                lesson="Check expiry separately while preserving valid-token behavior.",
            )

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'local.db'}"))
        async with open_builtin_runtime(
            config, experience_generator=Generator(), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            await _attempt(
                runtime,
                _instance(),
                {"instance_id": "public-task", "repeat": 0, "mode": "off"},
                tmp_path,
                "local-only",
                30,
            )
        paths = list((tmp_path / "attempts").glob("*.json"))
        assert len(paths) == 1
        record = json.loads(paths[0].read_text())
        assert record["status"] == "succeeded"
        assert record["lineage_equal"] is True
        assert record["usage"]["requests"] is None
        async with open_builtin_runtime(
            config, experience_generator=Generator(), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            saved = await runtime.experience.for_scope(record["scope_id"]).get(
                GetExperienceRequest.model_validate({"artifact": record["artifact_ref"]})
            )
            assert saved.content.model_dump(mode="json") == record["content"]
            # A durable attempt is not submitted to the provider again on resume.
            await _attempt(
                None,
                _instance(),
                {"instance_id": "public-task", "repeat": 0, "mode": "off"},
                tmp_path,
                "local-only",
                30,
            )
        assert json.loads(paths[0].read_text()) == record

    asyncio.run(scenario())
