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

"""Model-free unit tests for explicit price policies and usage-based cost records."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from powercontext_eval.benchmarks.longmemeval_v2.catalog import SmokeSelection
from powercontext_eval.benchmarks.longmemeval_v2.costs import (
    CostPolicyError,
    ModelPricePolicy,
    cost_policy_record,
    model_free_cost_block,
    parse_cost_policy,
    usage_cost_block,
    usage_cost_usd,
)

# DeepSeek's cache-hit input rate is far below its cache-miss rate, so a single blended
# input price would misprice a long, highly repeated prompt.
PRICE_POLICY = {
    "provider": "deepseek-openai",
    "model": "deepseek-flash",
    "currency": "USD",
    "input_cache_hit_price_per_million": 0.006,
    "input_cache_miss_price_per_million": 0.3,
    "output_price_per_million": 1.2,
    "price_policy_revision": "deepseek-public-list-2026-09",
}


def test_parse_cost_policy_keeps_an_unconfigured_policy_null() -> None:
    assert parse_cost_policy(None) is None


def test_parse_cost_policy_reads_explicit_prices_model_and_revision() -> None:
    policy = parse_cost_policy(PRICE_POLICY)

    assert policy == ModelPricePolicy(
        provider="deepseek-openai",
        model="deepseek-flash",
        currency="USD",
        input_cache_hit_price_per_million=0.006,
        input_cache_miss_price_per_million=0.3,
        output_price_per_million=1.2,
        price_policy_revision="deepseek-public-list-2026-09",
    )
    assert cost_policy_record(policy) == PRICE_POLICY


@pytest.mark.parametrize(
    "value",
    [
        {"provider": "deepseek-openai"},
        {**PRICE_POLICY, "unexpected": 1},
        {**PRICE_POLICY, "provider": "  "},
        {**PRICE_POLICY, "model": ""},
        {**PRICE_POLICY, "currency": ""},
        {**PRICE_POLICY, "price_policy_revision": ""},
        {**PRICE_POLICY, "input_cache_hit_price_per_million": -0.1},
        {**PRICE_POLICY, "input_cache_miss_price_per_million": -1},
        {**PRICE_POLICY, "output_price_per_million": -1},
        {**PRICE_POLICY, "input_cache_hit_price_per_million": True},
        {**PRICE_POLICY, "input_cache_miss_price_per_million": "free"},
        {**PRICE_POLICY, "output_price_per_million": float("inf")},
        {**PRICE_POLICY, "input_cache_hit_price_per_million": float("nan")},
    ],
)
def test_parse_cost_policy_rejects_a_malformed_explicit_policy(value: object) -> None:
    with pytest.raises(CostPolicyError):
        parse_cost_policy(value)


def test_parse_cost_policy_rejects_a_non_object_policy() -> None:
    with pytest.raises(CostPolicyError, match="JSON object"):
        parse_cost_policy("0.3 USD per million")


@pytest.mark.parametrize("currency", ["CNY", "EUR", "usd-cents", "RMB"])
def test_parse_cost_policy_rejects_a_non_usd_currency(currency: str) -> None:
    """The amount fields are named ``*_usd``, so a non-USD policy must be refused."""

    with pytest.raises(CostPolicyError, match="currency must be USD"):
        parse_cost_policy({**PRICE_POLICY, "currency": currency})


def test_parse_cost_policy_accepts_a_lowercase_usd_currency() -> None:
    policy = parse_cost_policy({**PRICE_POLICY, "currency": "usd"})

    assert policy is not None
    assert policy.currency == "USD"


def test_usage_cost_usd_prices_cache_hit_and_miss_input_separately() -> None:
    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    # 1M cached input at 0.006/M, 1M uncached input at 0.3/M, 1M output at 1.2/M.
    priced = usage_cost_usd(policy, cache_hit_tokens=1_000_000, cache_miss_tokens=1_000_000, output_tokens=1_000_000)
    assert priced == 1.506
    # Pricing every input token at the cache-miss rate would overcharge this by 0.294 USD.
    blended = usage_cost_usd(policy, cache_hit_tokens=0, cache_miss_tokens=2_000_000, output_tokens=1_000_000)
    assert blended == 1.8


def test_usage_cost_usd_keeps_zero_usage_at_zero_cost() -> None:
    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    assert usage_cost_usd(policy, cache_hit_tokens=0, cache_miss_tokens=0, output_tokens=0) == 0.0


def test_usage_cost_usd_rejects_a_negative_or_non_integer_token_count() -> None:
    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    with pytest.raises(CostPolicyError, match="non-negative integer"):
        usage_cost_usd(policy, cache_hit_tokens=-1, cache_miss_tokens=0, output_tokens=0)
    with pytest.raises(CostPolicyError, match="non-negative integer"):
        usage_cost_usd(policy, cache_hit_tokens=True, cache_miss_tokens=0, output_tokens=0)  # type: ignore[arg-type]


def test_usage_cost_block_reports_null_cost_and_the_reason_without_a_policy() -> None:
    block = usage_cost_block(
        None,
        provider="deepseek-openai",
        model="deepseek-flash",
        input_tokens=123,
        cache_hit_tokens=100,
        cache_miss_tokens=23,
        output_tokens=7,
    )

    assert block["cost_usd"] is None
    assert block["currency"] is None
    assert block["input_cache_hit_tokens"] == 100
    assert block["input_cache_miss_tokens"] == 23
    assert block["unavailable_reason"] == "no price policy was configured for this run"


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("anthropic-compatible", "deepseek-flash"),
        ("deepseek-openai", "deepseek-v4-pro"),
    ],
)
def test_usage_cost_block_requires_the_policy_to_match_provider_and_model(provider: str, model: str) -> None:
    """The same provider may serve several models at different prices, so both must match."""

    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    block = usage_cost_block(
        policy,
        provider=provider,
        model=model,
        input_tokens=10,
        cache_hit_tokens=5,
        cache_miss_tokens=5,
        output_tokens=5,
    )

    assert block["cost_usd"] is None
    assert block["input_cache_hit_price_per_million"] is None
    assert f"model {model!r}" in str(block["unavailable_reason"])


def test_usage_cost_block_refuses_to_price_usage_without_a_cache_split() -> None:
    """A single blended input total cannot be priced when cache-hit and miss rates differ."""

    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    block = usage_cost_block(
        policy,
        provider="deepseek-openai",
        model="deepseek-flash",
        input_tokens=1_000_000,
        cache_hit_tokens=None,
        cache_miss_tokens=None,
        output_tokens=0,
    )

    assert block["cost_usd"] is None
    assert "cache hit/miss split" in str(block["unavailable_reason"])


@pytest.mark.parametrize(
    ("cache_hit_tokens", "cache_miss_tokens"),
    [(100, None), (None, 100)],
)
def test_usage_cost_block_refuses_a_partial_cache_split(
    cache_hit_tokens: int | None, cache_miss_tokens: int | None
) -> None:
    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    block = usage_cost_block(
        policy,
        provider="deepseek-openai",
        model="deepseek-flash",
        input_tokens=100,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        output_tokens=0,
    )

    assert block["cost_usd"] is None
    assert "only one side" in str(block["unavailable_reason"])


def test_usage_cost_block_refuses_a_cache_split_that_does_not_match_input_total() -> None:
    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    block = usage_cost_block(
        policy,
        provider="deepseek-openai",
        model="deepseek-flash",
        input_tokens=1_000,
        cache_hit_tokens=100,
        cache_miss_tokens=200,
        output_tokens=0,
    )

    assert block["cost_usd"] is None
    assert "do not equal" in str(block["unavailable_reason"])


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 100, "completion_tokens": 1, "prompt_cache_hit_tokens": 100},
        {"prompt_tokens": 100, "completion_tokens": 1, "prompt_cache_miss_tokens": 100},
    ],
)
def test_deepseek_normalization_drops_a_partial_cache_split(usage: dict[str, int]) -> None:
    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import _normalize_deepseek_response

    response = _normalize_deepseek_response(
        {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
            "usage": usage,
        }
    )

    normalized = response["usage"]
    assert isinstance(normalized, dict)
    assert "input_cache_hit_tokens" not in normalized
    assert "input_cache_miss_tokens" not in normalized


def test_usage_cost_block_records_cache_split_cost_and_policy_identity() -> None:
    policy = parse_cost_policy(PRICE_POLICY)
    assert policy is not None

    block = usage_cost_block(
        policy,
        provider="deepseek-openai",
        model="deepseek-flash",
        input_tokens=2_000_000,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    assert block["cost_usd"] == 1.506
    assert block["currency"] == "USD"
    assert block["model"] == "deepseek-flash"
    assert block["input_cache_hit_price_per_million"] == 0.006
    assert block["input_cache_miss_price_per_million"] == 0.3
    assert block["output_price_per_million"] == 1.2
    assert block["price_policy_revision"] == "deepseek-public-list-2026-09"
    assert block["unavailable_reason"] is None


def test_model_free_cost_block_records_zero_tokens_zero_cost_and_the_reason() -> None:
    block = model_free_cost_block(stage="ingestion", reason="the adapter ingests without a model")

    assert block == {
        "stage": "ingestion",
        "input_tokens": 0,
        "input_cache_hit_tokens": 0,
        "input_cache_miss_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "reason": "the adapter ingests without a model",
    }


def test_cost_policy_record_is_null_without_a_policy() -> None:
    assert cost_policy_record(None) is None


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _prepared_artifacts(root: Path) -> Path:
    prepared = root / "prepared"
    prepared.mkdir()
    _write_json(
        prepared / "prepare-manifest.json",
        {"classification": "smoke-subset-prepare-only", "reader": None, "judge": None},
    )
    _write_json(
        prepared / "prepare-summary.json",
        {"classification": "smoke-subset-prepare-only", "question_count": 10, "failed": 0},
    )
    with (prepared / "prepared-prompts.jsonl").open("w", encoding="utf-8") as stream:
        for index in range(10):
            stream.write(
                json.dumps(
                    {
                        "question_id": f"q-{index}",
                        "sequence": index + 1,
                        "messages": [
                            {"role": "system", "content": "system"},
                            {"role": "user", "content": [{"type": "text", "text": "question"}]},
                        ],
                    }
                )
                + "\n"
            )
    return prepared


class _CountingReader:
    """Return normalized DeepSeek-shaped usage, including the cache split, without any network call."""

    def __init__(self, *, with_cache_split: bool = True) -> None:
        self.calls = 0
        self._with_cache_split = with_cache_split

    def complete(self, *, system: str, content: list[dict[str, object]]) -> dict[str, object]:
        self.calls += 1
        usage: dict[str, object] = {
            "input_tokens": 2_000_000,
            "output_tokens": 1_000_000,
        }
        if self._with_cache_split:
            usage["input_cache_hit_tokens"] = 1_000_000
            usage["input_cache_miss_tokens"] = 1_000_000
        return {
            "model": "deepseek-flash",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "\\boxed{answer}"}],
            "usage": usage,
        }


def test_deepseek_response_normalization_keeps_the_native_cache_split() -> None:
    """DeepSeek's ``prompt_cache_*_tokens`` fields must survive into normalized usage."""

    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import _normalize_deepseek_response

    normalized = _normalize_deepseek_response(
        {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": "\\boxed{answer}"}}],
            "usage": {
                "prompt_tokens": 2_000_000,
                "completion_tokens": 1_000_000,
                "prompt_cache_hit_tokens": 1_500_000,
                "prompt_cache_miss_tokens": 500_000,
            },
        }
    )

    assert normalized["usage"] == {
        "input_tokens": 2_000_000,
        "input_cache_hit_tokens": 1_500_000,
        "input_cache_miss_tokens": 500_000,
        "output_tokens": 1_000_000,
    }


def test_deepseek_response_normalization_omits_an_absent_cache_split() -> None:
    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import _normalize_deepseek_response

    normalized = _normalize_deepseek_response(
        {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": "\\boxed{answer}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
    )

    assert normalized["usage"] == {"input_tokens": 10, "output_tokens": 2}


def test_reader_keeps_the_deepseek_cache_split_in_its_outputs_and_summary(tmp_path: Path) -> None:
    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import run_reader_smoke

    result = run_reader_smoke(
        prepared_dir=_prepared_artifacts(tmp_path),
        output_dir=tmp_path / "reader",
        provider="deepseek-openai",
        model="deepseek-flash",
        max_questions=1,
        transport=_CountingReader(),
    )

    [record] = [json.loads(line) for line in result.outputs_path.read_text(encoding="utf-8").splitlines()]
    assert record["usage"] == {
        "input_tokens": 2_000_000,
        "input_cache_hit_tokens": 1_000_000,
        "input_cache_miss_tokens": 1_000_000,
        "output_tokens": 1_000_000,
    }
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["usage"] == {
        "input_tokens": 2_000_000,
        "input_cache_hit_tokens": 1_000_000,
        "input_cache_miss_tokens": 1_000_000,
        "output_tokens": 1_000_000,
    }


def test_reader_summary_prices_cache_hit_and_miss_usage_separately(tmp_path: Path) -> None:
    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import run_reader_smoke

    policy = parse_cost_policy(PRICE_POLICY)
    transport = _CountingReader()
    result = run_reader_smoke(
        prepared_dir=_prepared_artifacts(tmp_path),
        output_dir=tmp_path / "reader",
        provider="deepseek-openai",
        model="deepseek-flash",
        max_questions=2,
        price_policy=policy,
        transport=transport,
    )

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert transport.calls == 2
    # 2M cached at 0.006/M plus 2M uncached at 0.3/M plus 2M out at 1.2/M.
    assert summary["cost"]["cost_usd"] == 3.012
    assert summary["cost"]["currency"] == "USD"
    assert summary["cost"]["model"] == "deepseek-flash"
    assert summary["cost"]["price_policy_revision"] == "deepseek-public-list-2026-09"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["cost_policy"] == PRICE_POLICY


def test_reader_summary_keeps_cost_null_when_a_response_omits_the_cache_split(tmp_path: Path) -> None:
    """One response without the split makes the summed split incomplete, so cost stays null."""

    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import run_reader_smoke

    policy = parse_cost_policy(PRICE_POLICY)
    result = run_reader_smoke(
        prepared_dir=_prepared_artifacts(tmp_path),
        output_dir=tmp_path / "reader",
        provider="deepseek-openai",
        model="deepseek-flash",
        max_questions=1,
        price_policy=policy,
        transport=_CountingReader(with_cache_split=False),
    )

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["cost"]["cost_usd"] is None
    assert "cache hit/miss split" in str(summary["cost"]["unavailable_reason"])
    assert "input_cache_hit_tokens" not in summary["usage"]


def test_reader_summary_keeps_cost_null_when_the_policy_prices_another_model(tmp_path: Path) -> None:
    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import run_reader_smoke

    policy = parse_cost_policy({**PRICE_POLICY, "model": "deepseek-v4-pro"})
    result = run_reader_smoke(
        prepared_dir=_prepared_artifacts(tmp_path),
        output_dir=tmp_path / "reader",
        provider="deepseek-openai",
        model="deepseek-flash",
        max_questions=1,
        price_policy=policy,
        transport=_CountingReader(),
    )

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["cost"]["cost_usd"] is None
    assert "model 'deepseek-v4-pro'" in str(summary["cost"]["unavailable_reason"])


def test_reader_summary_keeps_cost_null_without_a_policy(tmp_path: Path) -> None:
    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import run_reader_smoke

    result = run_reader_smoke(
        prepared_dir=_prepared_artifacts(tmp_path),
        output_dir=tmp_path / "reader",
        provider="deepseek-openai",
        model="deepseek-flash",
        max_questions=1,
        transport=_CountingReader(),
    )

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["cost"]["cost_usd"] is None
    assert summary["cost"]["unavailable_reason"] == "no price policy was configured for this run"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["cost_policy"] is None


def test_reader_summary_never_writes_zero_for_an_unconfigured_cost(tmp_path: Path) -> None:
    from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import run_reader_smoke

    result = run_reader_smoke(
        prepared_dir=_prepared_artifacts(tmp_path),
        output_dir=tmp_path / "reader",
        provider="deepseek-openai",
        model="deepseek-flash",
        max_questions=1,
        transport=_CountingReader(),
    )

    recorded = result.summary_path.read_text(encoding="utf-8")
    assert '"cost_usd": 0' not in recorded
    assert '"cost_usd": null' in recorded


def _score_fixture(root: Path) -> tuple[Path, Path, Path]:
    reader = root / "reader"
    data = root / "data"
    reader.mkdir()
    data.mkdir()
    _write_json(reader / "reader-manifest.json", {"classification": "smoke-subset-reader-only", "judge": None})
    _write_json(
        reader / "reader-summary.json",
        {"classification": "smoke-subset-reader-only", "question_count": 10, "failed": 0},
    )
    cases = []
    with (
        (reader / "reader-outputs.jsonl").open("w", encoding="utf-8") as outputs,
        (data / "questions.jsonl").open("w", encoding="utf-8") as questions,
    ):
        for index in range(10):
            question_id = f"q-{index}"
            evaluator = "llm_abstention_checker" if index < 2 else "exact"
            outputs.write(json.dumps({"question_id": question_id, "response_text": f"answer-{index}"}) + "\n")
            questions.write(
                json.dumps(
                    {
                        "id": question_id,
                        "domain": "web",
                        "question": f"question {index}",
                        "answer": f"answer-{index}",
                        "eval_function": evaluator,
                    }
                )
                + "\n"
            )
            cases.append({"question_id": question_id, "ability": "static_state"})
    manifest = root / "smoke.json"
    _write_json(manifest, {"schema": "powercontext.longmemeval-v2-smoke.v1", "tier": "small", "cases": cases})
    return reader, data, manifest


class _FakeJudge:
    """Report usage without a cache split, standing in for a provider that omits it."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, content: list[dict[str, object]]) -> dict[str, object]:
        self.calls += 1
        return {
            "content": [{"type": "text", "text": '{"label":1}'}],
            "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        }


class _CacheAwareJudge:
    def complete(self, *, system: str, content: list[dict[str, object]]) -> dict[str, object]:
        return {
            "content": [{"type": "text", "text": '{"label":1}'}],
            "usage": {
                "input_tokens": 1_000_000,
                "input_cache_hit_tokens": 1_000_000,
                "input_cache_miss_tokens": 0,
                "output_tokens": 1_000_000,
            },
        }


class _FakeMetrics:
    def eval_name(self, spec: str) -> str:
        return spec

    def extract_boxed_answer(self, text: str) -> str:
        return text

    def eval_from_spec(self, spec: str, prediction: str, answer: str) -> bool:
        return prediction == answer

    def score_to_bool(self, value: Any) -> bool:
        return bool(value)

    def _build_abstention_judge_messages(self, **kwargs: Any) -> list[dict[str, str]]:
        return [{"role": "system", "content": "abstention"}, {"role": "user", "content": "question"}]

    def _build_gotchas_judge_messages(self, **kwargs: Any) -> list[dict[str, str]]:
        return [{"role": "system", "content": "gotchas"}, {"role": "user", "content": "question"}]

    def _parse_llm_binary_judgement(self, text: str) -> tuple[int, str]:
        return 1, "accepted"


def _run_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, policy: Any, judge: Any) -> Any:
    from powercontext_eval.benchmarks.longmemeval_v2 import score_smoke
    from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import run_score_smoke

    reader, data, manifest = _score_fixture(tmp_path)
    monkeypatch.setattr(score_smoke, "validate_harness_checkout", lambda root: None)
    monkeypatch.setattr(score_smoke, "_load_metrics", lambda root: _FakeMetrics())
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
    result = run_score_smoke(
        reader_dir=reader,
        data_root=data,
        dataset_lock=tmp_path / "dataset-lock.json",
        smoke_manifest=manifest,
        harness_root=tmp_path / "harness",
        output_dir=tmp_path / "score",
        judge_price_policy=policy,
        judge_transport=judge,
    )
    return json.loads(result.summary_path.read_text(encoding="utf-8"))


def test_score_summary_keeps_judge_cost_null_when_the_judge_omits_the_cache_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blended Judge input total must not be priced at one rate."""

    policy = parse_cost_policy(PRICE_POLICY)
    summary = _run_score(tmp_path, monkeypatch, policy=policy, judge=_FakeJudge())

    assert summary["judge_cost"]["cost_usd"] is None
    assert "cache hit/miss split" in str(summary["judge_cost"]["unavailable_reason"])


def test_score_summary_prices_a_judge_that_reports_its_cache_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = parse_cost_policy(PRICE_POLICY)
    summary = _run_score(tmp_path, monkeypatch, policy=policy, judge=_CacheAwareJudge())

    # 2M cached input at 0.006/M plus 2M output at 1.2/M.
    assert summary["judge_cost"]["cost_usd"] == 2.412
    assert summary["judge_cost"]["price_policy_revision"] == "deepseek-public-list-2026-09"


def test_score_summary_keeps_judge_cost_null_without_a_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _run_score(tmp_path, monkeypatch, policy=None, judge=_FakeJudge())

    assert summary["judge_cost"]["cost_usd"] is None
    assert summary["judge_cost"]["unavailable_reason"] == "no price policy was configured for this run"
