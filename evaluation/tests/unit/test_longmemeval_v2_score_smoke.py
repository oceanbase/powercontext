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

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from powercontext_eval.benchmarks.longmemeval_v2 import score_smoke
from powercontext_eval.benchmarks.longmemeval_v2.catalog import SmokeSelection
from powercontext_eval.benchmarks.longmemeval_v2.costs import ModelPricePolicy
from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import ReaderResponseError
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import ScoreSmokeError, run_score_smoke


class FakeMetrics:
    def eval_name(self, spec: str) -> str:
        return spec.split("|", 1)[0]

    def extract_boxed_answer(self, text: str) -> str:
        return text

    def eval_from_spec(self, spec: str, prediction: str, answer: str) -> bool:
        return prediction == answer and not spec.startswith("llm_")

    def score_to_bool(self, value: Any) -> bool:
        return bool(value)

    def _build_abstention_judge_messages(self, **kwargs: Any) -> list[dict[str, str]]:
        return [{"role": "system", "content": "abstention"}, {"role": "user", "content": kwargs["reference_answer"]}]

    def _build_gotchas_judge_messages(self, **kwargs: Any) -> list[dict[str, str]]:
        return [{"role": "system", "content": "gotchas"}, {"role": "user", "content": kwargs["reference_answer"]}]

    def _parse_llm_binary_judgement(self, text: str) -> tuple[int, str]:
        assert text == '{"label":1}'
        return 1, "accepted"


class FakeJudge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]:
        self.calls.append((system, content))
        return {
            "content": [{"type": "text", "text": '{"label":1}'}],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }


def score_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    reader = tmp_path / "reader"
    data = tmp_path / "data"
    reader.mkdir()
    data.mkdir()
    reader_manifest = {"classification": "smoke-subset-reader-only", "judge": None}
    reader_summary = {"classification": "smoke-subset-reader-only", "question_count": 10, "failed": 0}
    (reader / "reader-manifest.json").write_text(json.dumps(reader_manifest), encoding="utf-8")
    (reader / "reader-summary.json").write_text(json.dumps(reader_summary), encoding="utf-8")
    cases = []
    with (
        (reader / "reader-outputs.jsonl").open("w", encoding="utf-8") as outputs,
        (data / "questions.jsonl").open("w", encoding="utf-8") as questions,
    ):
        for index in range(10):
            question_id = f"q-{index}"
            evaluator = "llm_abstention_checker" if index < 2 else "llm_gotchas_checker" if index < 4 else "exact"
            answer = f"answer-{index}"
            cases.append({"question_id": question_id, "ability": "static_state"})
            outputs.write(json.dumps({"question_id": question_id, "response_text": answer}) + "\n")
            questions.write(
                json.dumps(
                    {
                        "id": question_id,
                        "domain": "web",
                        "question": f"question {index}",
                        "answer": answer,
                        "eval_function": evaluator,
                    }
                )
                + "\n"
            )
    manifest = tmp_path / "smoke.json"
    manifest.write_text(
        json.dumps({"schema": "powercontext.longmemeval-v2-smoke.v1", "tier": "small", "cases": cases}),
        encoding="utf-8",
    )
    return reader, data, manifest


def allow_validated_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(score_smoke, "load_dataset_lock", lambda path: SimpleNamespace(tier="small", file_digests={}))
    monkeypatch.setattr(
        score_smoke,
        "LongMemEvalV2Catalog",
        SimpleNamespace(
            load=lambda *args, **kwargs: SimpleNamespace(
                select_smoke=lambda cases: SmokeSelection("small", tuple(cases))
            )
        ),
    )


def test_score_smoke_keeps_gold_only_in_local_scoring_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reader, data, manifest = score_fixture(tmp_path)
    output = tmp_path / "score"
    judge = FakeJudge()
    monkeypatch.setattr(score_smoke, "validate_harness_checkout", lambda root: None)
    monkeypatch.setattr(score_smoke, "_load_metrics", lambda root: FakeMetrics())
    allow_validated_catalog(monkeypatch)

    result = run_score_smoke(
        reader_dir=reader,
        data_root=data,
        dataset_lock=tmp_path / "dataset-lock.json",
        smoke_manifest=manifest,
        harness_root=tmp_path / "harness",
        output_dir=output,
        judge_transport=judge,
    )

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["correct"] == 10
    assert summary["accuracy"] == 1.0
    assert summary["judge_calls"] == 4
    assert summary["judge_usage"] == {"input_tokens": 40, "output_tokens": 8}
    assert len(judge.calls) == 4
    results = [json.loads(line) for line in result.results_path.read_text(encoding="utf-8").splitlines()]
    assert {row["score_mode"] for row in results} == {"deterministic", "llm_judge"}
    assert all("reference_answer" not in row for row in results)
    scoring_inputs = result.inputs_path.read_text(encoding="utf-8")
    assert "answer-0" in scoring_inputs
    assert '"reference_answer"' not in result.results_path.read_text(encoding="utf-8")


def test_score_smoke_refuses_to_overwrite_before_reading_inputs(tmp_path: Path) -> None:
    output = tmp_path / "score"
    output.mkdir()

    with pytest.raises(ScoreSmokeError, match="Refusing to overwrite"):
        run_score_smoke(
            reader_dir=tmp_path / "missing",
            data_root=tmp_path / "missing-data",
            dataset_lock=tmp_path / "missing-lock",
            smoke_manifest=tmp_path / "missing-smoke",
            harness_root=tmp_path / "missing-harness",
            output_dir=output,
        )


class UnparseableJudgementMetrics(FakeMetrics):
    """The pinned parser rejects the Judge text; the completed call's usage must survive."""

    def _parse_llm_binary_judgement(self, text: str) -> tuple[int, str]:
        raise ValueError("judgement is malformed")


class CacheSplitJudge:
    """A Judge stub whose responses carry usage with the cache split an explicit policy prices."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]:
        self.calls.append((system, content))
        return {
            "content": [{"type": "text", "text": "truncated judgement"}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "input_cache_hit_tokens": 4,
                "input_cache_miss_tokens": 6,
            },
        }


def test_score_smoke_preserves_judge_usage_when_the_judgement_cannot_be_parsed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reader, data, manifest = score_fixture(tmp_path)
    output = tmp_path / "score"
    judge = CacheSplitJudge()
    monkeypatch.setattr(score_smoke, "validate_harness_checkout", lambda root: None)
    monkeypatch.setattr(score_smoke, "_load_metrics", lambda root: UnparseableJudgementMetrics())
    allow_validated_catalog(monkeypatch)
    policy = ModelPricePolicy(
        provider="deepseek-openai",
        model="test-judge",
        currency="USD",
        input_cache_hit_price_per_million=1.0,
        input_cache_miss_price_per_million=2.0,
        output_price_per_million=3.0,
        price_policy_revision="test-prices",
    )

    with pytest.raises(ScoreSmokeError, match="Scoring failed for 4"):
        run_score_smoke(
            reader_dir=reader,
            data_root=data,
            dataset_lock=tmp_path / "dataset-lock.json",
            smoke_manifest=manifest,
            harness_root=tmp_path / "harness",
            output_dir=output,
            judge_model="test-judge",
            judge_price_policy=policy,
            judge_transport=judge,
        )

    assert len(judge.calls) == 4
    summary = json.loads((output / "score-summary.json").read_text(encoding="utf-8"))
    assert summary["judge_calls"] == 4
    assert summary["judge_usage"] == {
        "input_tokens": 40,
        "output_tokens": 8,
        "input_cache_hit_tokens": 16,
        "input_cache_miss_tokens": 24,
    }
    assert summary["judge_cost"]["cost_usd"] == pytest.approx(88 / 1_000_000)
    failures = [json.loads(line) for line in (output / "score-failures.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(failures) == 4
    assert all(failure["error_type"] == "JudgeJudgementError" for failure in failures)
    assert all(
        failure["judge_usage"]
        == {"input_tokens": 10, "output_tokens": 2, "input_cache_hit_tokens": 4, "input_cache_miss_tokens": 6}
        for failure in failures
    )
    assert all(isinstance(failure["judge_latency_ms"], (int, float)) for failure in failures)
    assert not (output / "judge-outputs.jsonl").read_text(encoding="utf-8")


def test_score_smoke_preserves_judge_usage_when_the_transport_returns_no_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reader, data, manifest = score_fixture(tmp_path)
    output = tmp_path / "score"
    monkeypatch.setattr(score_smoke, "validate_harness_checkout", lambda root: None)
    monkeypatch.setattr(score_smoke, "_load_metrics", lambda root: FakeMetrics())
    allow_validated_catalog(monkeypatch)
    policy = ModelPricePolicy(
        provider="deepseek-openai",
        model="test-judge",
        currency="USD",
        input_cache_hit_price_per_million=1.0,
        input_cache_miss_price_per_million=2.0,
        output_price_per_million=3.0,
        price_policy_revision="test-prices",
    )

    class NoTextJudge:
        def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]:
            raise ReaderResponseError(
                "Reader response contains no text",
                usage={
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "input_cache_hit_tokens": 4,
                    "input_cache_miss_tokens": 6,
                },
            )

    with pytest.raises(ScoreSmokeError, match="Scoring failed for 4"):
        run_score_smoke(
            reader_dir=reader,
            data_root=data,
            dataset_lock=tmp_path / "dataset-lock.json",
            smoke_manifest=manifest,
            harness_root=tmp_path / "harness",
            output_dir=output,
            judge_model="test-judge",
            judge_price_policy=policy,
            judge_transport=NoTextJudge(),
        )

    summary = json.loads((output / "score-summary.json").read_text(encoding="utf-8"))
    assert summary["judge_calls"] == 4
    assert summary["judge_usage"] == {
        "input_tokens": 40,
        "output_tokens": 8,
        "input_cache_hit_tokens": 16,
        "input_cache_miss_tokens": 24,
    }
    assert summary["judge_cost"]["cost_usd"] == pytest.approx(88 / 1_000_000)
    failures = [json.loads(line) for line in (output / "score-failures.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(failures) == 4
    assert all(failure["error_type"] == "JudgeJudgementError" for failure in failures)
    assert all(
        failure["judge_usage"]
        == {"input_tokens": 10, "output_tokens": 2, "input_cache_hit_tokens": 4, "input_cache_miss_tokens": 6}
        for failure in failures
    )
    assert all(isinstance(failure["judge_latency_ms"], (int, float)) for failure in failures)
