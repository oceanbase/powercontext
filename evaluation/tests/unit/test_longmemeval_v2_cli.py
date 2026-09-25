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
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powercontext_eval.benchmarks.longmemeval_v2.costs import ModelPricePolicy
from powercontext_eval.benchmarks.longmemeval_v2.prepare_smoke import PreparedPromptRun
from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import ReaderSmokeRun
from powercontext_eval.benchmarks.longmemeval_v2.replay_score import ReplayScoreRun
from powercontext_eval.benchmarks.longmemeval_v2.report import ReportError, ReportRun
from powercontext_eval.benchmarks.longmemeval_v2.retrieval_smoke import (
    RetrievalCapabilityError,
    RetrievalSmokeRun,
)
from powercontext_eval.benchmarks.longmemeval_v2.run_smoke import RunSmokeError, SmokeRunResult
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import ScoreSmokeRun
from powercontext_eval.cli import app


def test_longmemeval_v2_retrieval_smoke_runs_without_reader_or_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> RetrievalSmokeRun:
        calls.append(kwargs)
        output = tmp_path / "output"
        return RetrievalSmokeRun(
            output_dir=output,
            manifest_path=output / "retrieval-manifest.json",
            results_path=output / "retrieval-results.jsonl",
            failures_path=output / "failures.jsonl",
            summary_path=output / "summary.json",
            audit_path=output / "adapter-audit.jsonl",
        )

    monkeypatch.setattr("powercontext_eval.cli.run_retrieval_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "retrieval-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--harness-root",
            "/harness",
            "--smoke-manifest",
            "/smoke.json",
            "--output-dir",
            "/output",
            "--run-id",
            "retrieval-1",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "adapter-sha",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"classification": "smoke-subset-retrieval-only"' in result.output
    assert calls == [
        {
            "data_root": Path("/data"),
            "dataset_lock": Path("/dataset-lock.json"),
            "harness_root": Path("/harness"),
            "smoke_manifest": Path("/smoke.json"),
            "output_dir": Path("/output"),
            "run_id": "retrieval-1",
            "powercontext_revision": "pc-sha",
            "integration_revision": "adapter-sha",
            "base_url": "http://127.0.0.1:8000",
            "token_env": "POWERCONTEXT_TOKEN",
            "experiment_arm": "current-memory-fts-v1",
            "search_limit": 10,
            "timeout_seconds": 30.0,
        }
    ]


def test_longmemeval_v2_prepare_smoke_runs_without_reader_or_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def prepare(**kwargs: object) -> PreparedPromptRun:
        calls.append(kwargs)
        output = tmp_path / "output"
        return PreparedPromptRun(
            output_dir=output,
            manifest_path=output / "prepare-manifest.json",
            prompts_path=output / "prepared-prompts.jsonl",
            failures_path=output / "prepare-failures.jsonl",
            summary_path=output / "prepare-summary.json",
        )

    monkeypatch.setattr("powercontext_eval.cli.prepare_reader_inputs_smoke", prepare)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "prepare-smoke",
            "--retrieval-dir",
            "/retrieval",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--output-dir",
            "/output",
            "--processor-revision",
            "processor-sha",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"classification": "smoke-subset-prepare-only"' in result.output
    assert calls == [
        {
            "retrieval_dir": Path("/retrieval"),
            "harness_root": Path("/harness"),
            "harness_python": Path("/harness/python"),
            "output_dir": Path("/output"),
            "processor_model": "Qwen/Qwen3.5-9B",
            "processor_revision": "processor-sha",
            "memory_context_max_tokens": 200_000,
        }
    ]


def test_longmemeval_v2_reader_smoke_uses_environment_reference_without_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> ReaderSmokeRun:
        calls.append(kwargs)
        output = tmp_path / "output"
        return ReaderSmokeRun(
            output_dir=output,
            manifest_path=output / "reader-manifest.json",
            outputs_path=output / "reader-outputs.jsonl",
            failures_path=output / "reader-failures.jsonl",
            summary_path=output / "reader-summary.json",
        )

    monkeypatch.setattr("powercontext_eval.cli.run_reader_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "reader-smoke",
            "--prepared-dir",
            "/prepared",
            "--output-dir",
            "/output",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"classification": "smoke-subset-reader-only"' in result.output
    assert calls == [
        {
            "prepared_dir": Path("/prepared"),
            "output_dir": Path("/output"),
            "provider": "anthropic-compatible",
            "model": None,
            "base_url": None,
            "base_url_env": "ANTHROPIC_BASE_URL",
            "token_env": None,
            "max_tokens": 512,
            "temperature": 0.0,
            "timeout_seconds": 120.0,
            "max_questions": None,
            "price_policy": None,
        }
    ]


def test_longmemeval_v2_score_smoke_uses_local_reader_artifacts_and_judge_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> ScoreSmokeRun:
        calls.append(kwargs)
        output = tmp_path / "output"
        return ScoreSmokeRun(
            output_dir=output,
            manifest_path=output / "score-manifest.json",
            inputs_path=output / "scoring-inputs.local.jsonl",
            results_path=output / "per-question.jsonl",
            judge_outputs_path=output / "judge-outputs.jsonl",
            failures_path=output / "score-failures.jsonl",
            summary_path=output / "score-summary.json",
        )

    monkeypatch.setattr("powercontext_eval.cli.run_score_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "score-smoke",
            "--reader-dir",
            "/reader",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--output-dir",
            "/output",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"classification": "smoke-subset-score-only"' in result.output
    assert calls == [
        {
            "reader_dir": Path("/reader"),
            "data_root": Path("/data"),
            "dataset_lock": Path("/dataset-lock.json"),
            "smoke_manifest": Path("/smoke.json"),
            "harness_root": Path("/harness"),
            "output_dir": Path("/output"),
            "judge_model": "deepseek-flash",
            "judge_token_env": "DEEPSEEK_API_KEY",
            "judge_base_url": "https://api.deepseek.com",
            "judge_max_tokens": 256,
            "judge_temperature": 0.0,
            "judge_timeout_seconds": 120.0,
            "judge_price_policy": None,
        }
    ]


def test_longmemeval_v2_replay_score_uses_only_local_score_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def replay(**kwargs: object) -> ReplayScoreRun:
        calls.append(kwargs)
        output = tmp_path / "output"
        return ReplayScoreRun(
            output_dir=output,
            manifest_path=output / "replay-manifest.json",
            results_path=output / "replay-per-question.jsonl",
            failures_path=output / "replay-failures.jsonl",
            summary_path=output / "replay-summary.json",
        )

    monkeypatch.setattr("powercontext_eval.cli.replay_score_smoke", replay)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "replay-score",
            "--score-dir",
            "/score",
            "--harness-root",
            "/harness",
            "--output-dir",
            "/output",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"classification": "smoke-subset-score-replay"' in result.output
    assert calls == [{"score_dir": Path("/score"), "harness_root": Path("/harness"), "output_dir": Path("/output")}]


def test_longmemeval_v2_run_smoke_forwards_every_stage_option(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> SmokeRunResult:
        calls.append(kwargs)
        output = tmp_path / "output"
        return SmokeRunResult(
            output_dir=output,
            manifest_path=output / "run-manifest.json",
            summary_path=output / "run-summary.json",
            failures_path=output / "failures.jsonl",
            status="completed",
            completed_phases=("preflight", "retrieval", "prepare", "reader", "score", "replay"),
            skipped_phases=(),
            failed_phase=None,
        )

    monkeypatch.setattr("powercontext_eval.cli.run_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "completed"' in result.output
    assert calls == [
        {
            "data_root": Path("/data"),
            "dataset_lock": Path("/dataset-lock.json"),
            "smoke_manifest": Path("/smoke.json"),
            "harness_root": Path("/harness"),
            "harness_python": Path("/harness/python"),
            "processor_revision": "processor-sha",
            "output_dir": Path("/output"),
            "powercontext_revision": "pc-sha",
            "integration_revision": "integration-sha",
            "run_id": None,
            "processor_model": "Qwen/Qwen3.5-9B",
            "memory_context_max_tokens": 200_000,
            "powercontext_base_url": "http://127.0.0.1:8000",
            "powercontext_token_env": "POWERCONTEXT_TOKEN",
            "experiment_arm": "current-memory-fts-v1",
            "search_limit": 10,
            "timeout_seconds": 30.0,
            "reader_provider": "deepseek-openai",
            "reader_model": None,
            "reader_base_url": None,
            "reader_base_url_env": "ANTHROPIC_BASE_URL",
            "reader_token_env": None,
            "reader_max_tokens": 512,
            "reader_temperature": 0.0,
            "reader_timeout_seconds": 120.0,
            "judge_provider": "deepseek-openai",
            "judge_model": "deepseek-flash",
            "judge_token_env": "DEEPSEEK_API_KEY",
            "judge_base_url": "https://api.deepseek.com",
            "judge_max_tokens": 256,
            "judge_temperature": 0.0,
            "judge_timeout_seconds": 120.0,
            "reader_price_policy": None,
            "judge_price_policy": None,
            "skip_reader": False,
            "skip_score": False,
        }
    ]


def test_longmemeval_v2_run_smoke_exits_nonzero_without_a_complete_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(**kwargs: object) -> SmokeRunResult:
        output = tmp_path / "output"
        return SmokeRunResult(
            output_dir=output,
            manifest_path=output / "run-manifest.json",
            summary_path=output / "run-summary.json",
            failures_path=output / "failures.jsonl",
            status="failed",
            completed_phases=("preflight", "retrieval"),
            skipped_phases=("prepare", "reader", "score", "replay"),
            failed_phase="prepare",
        )

    monkeypatch.setattr("powercontext_eval.cli.run_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
            "--skip-reader",
        ],
    )

    assert result.exit_code == 1
    assert '"failed_phase": "prepare"' in result.output
    assert '"status": "failed"' in result.output


def test_longmemeval_v2_run_smoke_reports_a_refused_output_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(**kwargs: object) -> SmokeRunResult:
        raise RunSmokeError("Refusing to overwrite smoke run artifacts: /output")

    monkeypatch.setattr("powercontext_eval.cli.run_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
        ],
    )

    assert result.exit_code == 1
    assert "Refusing to overwrite smoke run artifacts" in result.output


def test_longmemeval_v2_run_smoke_forwards_a_selected_experiment_arm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> SmokeRunResult:
        calls.append(kwargs)
        output = tmp_path / "output"
        return SmokeRunResult(
            output_dir=output,
            manifest_path=output / "run-manifest.json",
            summary_path=output / "run-summary.json",
            failures_path=output / "failures.jsonl",
            status="partial",
            completed_phases=("preflight", "retrieval", "prepare"),
            skipped_phases=("reader", "score", "replay"),
            failed_phase=None,
        )

    monkeypatch.setattr("powercontext_eval.cli.run_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
            "--skip-reader",
            "--experiment-arm",
            "current-memory-hybrid-v1",
        ],
    )

    assert result.exit_code == 1, result.output  # a partial model-free run exits non-zero by design
    assert calls[0]["experiment_arm"] == "current-memory-hybrid-v1"


def test_longmemeval_v2_run_smoke_rejects_an_unknown_experiment_arm() -> None:
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
            "--experiment-arm",
            "l0-persistent-v1",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "unknown experiment arm" in result.output
    assert "current-memory-fts-v1" in result.output
    assert "current-memory-hybrid-v1" in result.output


def test_longmemeval_v2_retrieval_smoke_rejects_an_unknown_experiment_arm() -> None:
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "retrieval-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--harness-root",
            "/harness",
            "--smoke-manifest",
            "/smoke.json",
            "--output-dir",
            "/output",
            "--run-id",
            "retrieval-1",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "adapter-sha",
            "--experiment-arm",
            "temporal-recency-v1",
        ],
    )

    assert result.exit_code == 2, result.output  # an unknown arm is a parameter error
    assert "unknown experiment arm" in result.output


def test_longmemeval_v2_retrieval_smoke_reports_a_capability_failure_as_a_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(**kwargs: object) -> RetrievalSmokeRun:
        raise RetrievalCapabilityError(
            "PowerContext Server does not support hybrid Memory search "
            "required by experiment arm current-memory-hybrid-v1"
        )

    monkeypatch.setattr("powercontext_eval.cli.run_retrieval_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "retrieval-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--harness-root",
            "/harness",
            "--smoke-manifest",
            "/smoke.json",
            "--output-dir",
            "/output",
            "--run-id",
            "retrieval-1",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "adapter-sha",
            "--experiment-arm",
            "current-memory-hybrid-v1",
        ],
    )

    assert result.exit_code == 1, result.output  # the environment, not the argument, is wrong
    assert "does not support hybrid Memory search" in result.output
    assert "Invalid value" not in result.output


def test_longmemeval_v2_report_reads_only_saved_run_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def report(**kwargs: object) -> ReportRun:
        calls.append(kwargs)
        return ReportRun(report_path=tmp_path / "report.json", markdown_path=tmp_path / "report.md")

    monkeypatch.setattr("powercontext_eval.cli.build_report", report)
    result = CliRunner().invoke(app, ["longmemeval-v2", "report", "--run-dir", "/run"])

    assert result.exit_code == 0, result.output
    assert '"classification": "smoke-subset"' in result.output
    assert calls == [{"run_dir": Path("/run"), "output_dir": None}]


def test_longmemeval_v2_report_exits_nonzero_for_a_refused_target(monkeypatch: pytest.MonkeyPatch) -> None:
    def report(**kwargs: object) -> ReportRun:
        raise ReportError("Refusing to overwrite report artifacts: /report")

    monkeypatch.setattr("powercontext_eval.cli.build_report", report)
    result = CliRunner().invoke(app, ["longmemeval-v2", "report", "--run-dir", "/run", "--output-dir", "/report"])

    assert result.exit_code == 1
    assert "Refusing to overwrite report artifacts" in result.output


PRICE_POLICY_JSON = json.dumps(
    {
        "provider": "deepseek-openai",
        "model": "deepseek-flash",
        "currency": "USD",
        "input_cache_hit_price_per_million": 0.006,
        "input_cache_miss_price_per_million": 0.3,
        "output_price_per_million": 1.2,
        "price_policy_revision": "deepseek-public-list-2026-09",
    }
)


def test_longmemeval_v2_reader_smoke_forwards_an_explicit_price_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> ReaderSmokeRun:
        calls.append(kwargs)
        output = tmp_path / "output"
        return ReaderSmokeRun(
            output_dir=output,
            manifest_path=output / "reader-manifest.json",
            outputs_path=output / "reader-outputs.jsonl",
            failures_path=output / "reader-failures.jsonl",
            summary_path=output / "reader-summary.json",
        )

    monkeypatch.setattr("powercontext_eval.cli.run_reader_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "reader-smoke",
            "--prepared-dir",
            "/prepared",
            "--output-dir",
            "/output",
            "--price-policy",
            PRICE_POLICY_JSON,
        ],
    )

    assert result.exit_code == 0, result.output
    [call] = calls
    assert call["price_policy"] == ModelPricePolicy(
        provider="deepseek-openai",
        model="deepseek-flash",
        currency="USD",
        input_cache_hit_price_per_million=0.006,
        input_cache_miss_price_per_million=0.3,
        output_price_per_million=1.2,
        price_policy_revision="deepseek-public-list-2026-09",
    )


def test_longmemeval_v2_score_smoke_forwards_an_explicit_judge_price_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> ScoreSmokeRun:
        calls.append(kwargs)
        output = tmp_path / "output"
        return ScoreSmokeRun(
            output_dir=output,
            manifest_path=output / "score-manifest.json",
            inputs_path=output / "scoring-inputs.local.jsonl",
            results_path=output / "per-question.jsonl",
            judge_outputs_path=output / "judge-outputs.jsonl",
            failures_path=output / "score-failures.jsonl",
            summary_path=output / "score-summary.json",
        )

    monkeypatch.setattr("powercontext_eval.cli.run_score_smoke", run)
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "score-smoke",
            "--reader-dir",
            "/reader",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--output-dir",
            "/output",
            "--judge-price-policy",
            PRICE_POLICY_JSON,
        ],
    )

    assert result.exit_code == 0, result.output
    [call] = calls
    assert call["judge_price_policy"] == ModelPricePolicy(
        provider="deepseek-openai",
        model="deepseek-flash",
        currency="USD",
        input_cache_hit_price_per_million=0.006,
        input_cache_miss_price_per_million=0.3,
        output_price_per_million=1.2,
        price_policy_revision="deepseek-public-list-2026-09",
    )


def test_longmemeval_v2_run_smoke_forwards_separate_reader_and_judge_price_policies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def run(**kwargs: object) -> SmokeRunResult:
        calls.append(kwargs)
        output = tmp_path / "output"
        return SmokeRunResult(
            output_dir=output,
            manifest_path=output / "run-manifest.json",
            summary_path=output / "run-summary.json",
            failures_path=output / "failures.jsonl",
            status="completed",
            completed_phases=("preflight", "retrieval", "prepare", "reader", "score", "replay"),
            skipped_phases=(),
            failed_phase=None,
        )

    monkeypatch.setattr("powercontext_eval.cli.run_smoke", run)
    judge_policy_json = json.dumps({**json.loads(PRICE_POLICY_JSON), "model": "deepseek-v4-pro"})
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
            "--price-policy",
            PRICE_POLICY_JSON,
            "--judge-price-policy",
            judge_policy_json,
        ],
    )

    assert result.exit_code == 0, result.output
    [call] = calls
    reader_policy = call["reader_price_policy"]
    judge_policy = call["judge_price_policy"]
    assert isinstance(reader_policy, ModelPricePolicy)
    assert isinstance(judge_policy, ModelPricePolicy)
    assert reader_policy.model == "deepseek-flash"
    assert judge_policy.model == "deepseek-v4-pro"


def test_longmemeval_v2_run_smoke_rejects_a_malformed_price_policy() -> None:
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
            "--price-policy",
            '{"provider": "deepseek-openai"}',
        ],
    )

    assert result.exit_code != 0
    assert "must contain exactly" in result.output


def test_longmemeval_v2_run_smoke_rejects_a_non_usd_price_policy() -> None:
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
            "--price-policy",
            json.dumps({**json.loads(PRICE_POLICY_JSON), "currency": "CNY"}),
        ],
    )

    assert result.exit_code != 0
    assert "currency must be USD" in result.output


def test_longmemeval_v2_run_smoke_rejects_a_non_json_price_policy() -> None:
    result = CliRunner().invoke(
        app,
        [
            "longmemeval-v2",
            "run-smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--smoke-manifest",
            "/smoke.json",
            "--harness-root",
            "/harness",
            "--harness-python",
            "/harness/python",
            "--processor-revision",
            "processor-sha",
            "--output-dir",
            "/output",
            "--powercontext-revision",
            "pc-sha",
            "--integration-revision",
            "integration-sha",
            "--judge-price-policy",
            "0.3 USD per million",
        ],
    )

    assert result.exit_code != 0
    assert "--judge-price-policy must be a JSON object" in result.output
