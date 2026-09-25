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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from powercontext_eval.benchmarks.longmemeval_v2.adapter import (
    PowerContextMemoryAdapterError,
    PowerContextMemoryModeError,
)
from powercontext_eval.benchmarks.longmemeval_v2.arms import CURRENT_MEMORY_HYBRID, ExperimentArmError
from powercontext_eval.benchmarks.longmemeval_v2.costs import parse_cost_policy
from powercontext_eval.benchmarks.longmemeval_v2.prepare_smoke import PreparedPromptRun, PrepareSmokeError
from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import ReaderSmokeError, ReaderSmokeRun
from powercontext_eval.benchmarks.longmemeval_v2.replay_score import ReplayScoreRun
from powercontext_eval.benchmarks.longmemeval_v2.retrieval_smoke import (
    RetrievalCapabilityError,
    RetrievalSmokeError,
    RetrievalSmokeRun,
)
from powercontext_eval.benchmarks.longmemeval_v2.run_smoke import RunSmokeError, SmokeStages, classify_error, run_smoke
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import ScoreSmokeRun
from powercontext_eval.benchmarks.longmemeval_v2.smoke import PreparedSmokeRun

SECRET = "sk-smoke-test-secret-value"
READER_PRICE_POLICY = {
    "provider": "deepseek-openai",
    "model": "deepseek-flash",
    "currency": "USD",
    "input_cache_hit_price_per_million": 0.006,
    "input_cache_miss_price_per_million": 0.3,
    "output_price_per_million": 1.2,
    "price_policy_revision": "deepseek-public-list-2026-09",
}


class StubTransport:
    """Stand in for a configured Reader or Judge transport without any network call."""

    def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]:
        raise AssertionError("the orchestration tests must never call a model transport")


class _Harness:
    """Record stage calls and emit the summary artifacts the run summary reads."""

    def __init__(self, *, fail_on: str | None = None, correct: int = 4) -> None:
        self.calls: list[str] = []
        self.retrieval_arguments: dict[str, Any] | None = None
        self.reader_arguments: dict[str, Any] | None = None
        self.score_arguments: dict[str, Any] | None = None
        self._fail_on = fail_on
        self._correct = correct

    def _record(self, name: str, arguments: dict[str, Any]) -> Path:
        self.calls.append(name)
        if name == "retrieval":
            self.retrieval_arguments = dict(arguments)
        if name == "reader":
            self.reader_arguments = dict(arguments)
        if name == "score":
            self.score_arguments = dict(arguments)
        if self._fail_on == name:
            raise _stage_error(name)
        output = Path(arguments["output_dir"])
        output.mkdir(parents=True, exist_ok=False)
        return output

    def preflight(self, **arguments: Any) -> PreparedSmokeRun:
        output = self._record("preflight", arguments)
        manifest = output / "manifest.json"
        manifest.write_text("{}\n", encoding="utf-8")
        return PreparedSmokeRun(output_dir=output, manifest_path=manifest, subset_path=output / "subset.json")

    def retrieval(self, **arguments: Any) -> RetrievalSmokeRun:
        output = self._record("retrieval", arguments)
        _write(
            output / "retrieval-results.jsonl",
            [
                {
                    "question_id": f"q{index}",
                    "status": "succeeded",
                    "memory_context": [{"type": "text", "value": "a"}, {"type": "text", "value": "b"}],
                    "timings_ms": {"search": 1.0, "format": 0.5, "total": 2.0},
                }
                for index in range(10)
            ],
        )
        _write(
            output / "adapter-audit.jsonl",
            [{"operation": "ingest", "timings_ms": {"source_capture": 1.0, "memory_remember": 1.0, "total": 3.0}}] * 4,
        )
        _write_json(
            output / "summary.json",
            {
                "question_count": 10,
                "succeeded": 10,
                "failed": 0,
                "context_bytes": 900,
                "citation_count": 12,
                "elapsed_ms": 100.0,
            },
        )
        return RetrievalSmokeRun(
            output_dir=output,
            manifest_path=output / "retrieval-manifest.json",
            results_path=output / "retrieval-results.jsonl",
            failures_path=output / "failures.jsonl",
            summary_path=output / "summary.json",
            audit_path=output / "adapter-audit.jsonl",
        )

    def prepare(self, **arguments: Any) -> PreparedPromptRun:
        output = self._record("prepare", arguments)
        _write_json(
            output / "prepare-summary.json",
            {
                "question_count": 10,
                "succeeded": 10,
                "failed": 0,
                "memory_context_tokens": 1234,
                "elapsed_ms": 7.0,
            },
        )
        return PreparedPromptRun(
            output_dir=output,
            manifest_path=output / "prepare-manifest.json",
            prompts_path=output / "prepared-prompts.jsonl",
            failures_path=output / "prepare-failures.jsonl",
            summary_path=output / "prepare-summary.json",
        )

    def reader(self, **arguments: Any) -> ReaderSmokeRun:
        output = self._record("reader", arguments)
        _write(output / "reader-outputs.jsonl", [{"question_id": "q0", "reader_latency_ms": 5.0}] * 10)
        _write_json(
            output / "reader-summary.json",
            {
                "question_count": 10,
                "succeeded": 10,
                "failed": 0,
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "elapsed_ms": 50.0,
            },
        )
        return ReaderSmokeRun(
            output_dir=output,
            manifest_path=output / "reader-manifest.json",
            outputs_path=output / "reader-outputs.jsonl",
            failures_path=output / "reader-failures.jsonl",
            summary_path=output / "reader-summary.json",
        )

    def score(self, **arguments: Any) -> ScoreSmokeRun:
        output = self._record("score", arguments)
        incorrect = 10 - self._correct
        _write_json(
            output / "score-summary.json",
            {
                "question_count": 10,
                "correct": self._correct,
                "incorrect": incorrect,
                "failed": 0,
                "accuracy": self._correct / 10,
                "judge_usage": {"input_tokens": 30, "output_tokens": 10},
                "elapsed_ms": 40.0,
            },
        )
        return ScoreSmokeRun(
            output_dir=output,
            manifest_path=output / "score-manifest.json",
            inputs_path=output / "scoring-inputs.local.jsonl",
            results_path=output / "per-question.jsonl",
            judge_outputs_path=output / "judge-outputs.jsonl",
            failures_path=output / "score-failures.jsonl",
            summary_path=output / "score-summary.json",
        )

    def replay(self, **arguments: Any) -> ReplayScoreRun:
        output = self._record("replay", arguments)
        incorrect = 10 - self._correct
        _write_json(
            output / "replay-summary.json",
            {
                "question_count": 10,
                "correct": self._correct,
                "incorrect": incorrect,
                "failed": 0,
                "accuracy": self._correct / 10,
            },
        )
        return ReplayScoreRun(
            output_dir=output,
            manifest_path=output / "replay-manifest.json",
            results_path=output / "replay-per-question.jsonl",
            failures_path=output / "replay-failures.jsonl",
            summary_path=output / "replay-summary.json",
        )

    def stages(self) -> SmokeStages:
        return SmokeStages(
            preflight=self.preflight,
            retrieval=self.retrieval,
            prepare=self.prepare,
            reader=self.reader,
            score=self.score,
            replay=self.replay,
        )


def _stage_error(name: str) -> Exception:
    return {
        "preflight": PrepareSmokeError("preflight rejected the fixed inputs"),
        "retrieval": ReaderSmokeError("retrieval could not reach the runtime"),
        "prepare": PrepareSmokeError("the pinned harness worker failed"),
        "reader": ReaderSmokeError("Reader request returned HTTP 401"),
        "score": ReaderSmokeError("scoring failed"),
        "replay": ReaderSmokeError("replay failed"),
    }[name]


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(tmp_path: Path, harness: _Harness, **overrides: Any) -> Any:
    lock = tmp_path / "dataset-lock.json"
    manifest = tmp_path / "smoke.json"
    lock.write_text('{"schema": "lock"}\n', encoding="utf-8")
    manifest.write_text('{"schema": "smoke"}\n', encoding="utf-8")
    arguments: dict[str, Any] = {
        "data_root": tmp_path / "data",
        "dataset_lock": lock,
        "smoke_manifest": manifest,
        "harness_root": tmp_path / "harness",
        "harness_python": tmp_path / "harness-python",
        "processor_revision": "processor-sha",
        "output_dir": tmp_path / "run",
        "powercontext_revision": "pc-sha",
        "integration_revision": "integration-sha",
        "stages": harness.stages(),
        "reader_transport": StubTransport(),
        "judge_transport": StubTransport(),
    }
    arguments.update(overrides)
    return run_smoke(**arguments)


def test_run_smoke_runs_every_phase_in_order_and_writes_a_report(tmp_path: Path) -> None:
    harness = _Harness()
    result = _run(tmp_path, harness)

    assert result.status == "completed"
    assert harness.calls == ["preflight", "retrieval", "prepare", "reader", "score", "replay"]
    assert result.completed_phases == ("preflight", "retrieval", "prepare", "reader", "score", "replay")
    assert result.skipped_phases == ()
    assert result.failed_phase is None
    for name in ("01-inputs", "02-retrieval", "03-prepare", "04-reader", "05-score", "06-replay"):
        assert (tmp_path / "run" / name).is_dir()

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["classification"] == "smoke-subset"
    assert summary["status"] == "completed"
    assert summary["accuracy"] == {"correct": 4, "incorrect": 6, "failed": 0, "value": 0.4}
    assert summary["question_count"] == 10
    assert summary["artifacts"]["retrieval"] == "02-retrieval"
    assert (tmp_path / "run" / "report.json").is_file()
    assert (tmp_path / "run" / "report.md").is_file()


def test_run_smoke_records_the_manifest_before_any_stage_runs(tmp_path: Path) -> None:
    harness = _Harness()
    _run(tmp_path, harness)

    manifest = json.loads((tmp_path / "run" / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["classification"] == "smoke-subset"
    assert manifest["run_id"] == "run"
    assert manifest["modes"] == {"reader": True, "score": True}
    assert manifest["experiment_arm"] == {
        "id": "current-memory-fts-v1",
        "retrieval_strategy": "memory-search",
        "search_mode": "fts",
        "memory_projection": "deterministic-compact-v1",
        "query_projection": "question-text-v1",
        "prepared_context_max_bytes": None,
        "temporal_filter": None,
        "task_lens": None,
    }
    assert manifest["powercontext"]["search_mode"] == "fts"
    assert manifest["reader"]["model"] == "deepseek-flash"
    assert manifest["reader"]["token_env"] == "DEEPSEEK_API_KEY"
    assert manifest["judge"]["token_env"] == "DEEPSEEK_API_KEY"
    assert manifest["revisions"] == {"powercontext": "pc-sha", "integration": "integration-sha"}
    assert manifest["phases"]["replay"] == "06-replay"
    assert manifest["privacy"]["credentials"] == "resolved-from-environment-at-runtime-never-recorded"
    assert manifest["privacy"]["reference_answers"] == "never-read-by-the-adapter-retrieval-prepare-or-reader-stages"
    assert "score stage reads the locked local questions" in manifest["privacy"]["reference_answers_readers"]
    assert manifest["privacy"]["reference_answers_artifact"] == "05-score/scoring-inputs.local.jsonl"
    assert harness.retrieval_arguments is not None
    assert harness.retrieval_arguments["experiment_arm"].arm_id == "current-memory-fts-v1"


def test_run_smoke_refuses_an_existing_output_directory_before_any_stage(tmp_path: Path) -> None:
    harness = _Harness()
    (tmp_path / "run").mkdir()

    with pytest.raises(RunSmokeError, match="Refusing to overwrite smoke run artifacts"):
        _run(tmp_path, harness)

    assert harness.calls == []


def test_run_smoke_rejects_a_base_url_that_carries_credentials(tmp_path: Path) -> None:
    harness = _Harness()

    with pytest.raises(RunSmokeError, match="must not contain credentials"):
        _run(tmp_path, harness, powercontext_base_url="http://user:secret@127.0.0.1:8000")

    assert harness.calls == []
    assert not (tmp_path / "run").exists()


def test_run_smoke_stops_after_a_failed_phase_and_keeps_prior_artifacts(tmp_path: Path) -> None:
    harness = _Harness(fail_on="prepare")
    result = _run(tmp_path, harness)

    assert result.status == "failed"
    assert result.failed_phase == "prepare"
    assert harness.calls == ["preflight", "retrieval", "prepare"]
    assert result.completed_phases == ("preflight", "retrieval")
    assert (tmp_path / "run" / "01-inputs").is_dir()
    assert (tmp_path / "run" / "02-retrieval").is_dir()
    assert not (tmp_path / "run" / "04-reader").exists()

    failures = [json.loads(line) for line in result.failures_path.read_text(encoding="utf-8").splitlines()]
    assert failures == [
        {
            "schema": "powercontext.longmemeval-v2-smoke-run-failure.v1",
            "phase": "prepare",
            "error_class": "infrastructure",
            "error_type": "PrepareSmokeError",
            "summary": "the pinned harness worker failed",
        }
    ]
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert summary["accuracy"] is None
    assert summary["failed_phase"] == "prepare"


def test_run_smoke_classifies_a_reader_failure_as_generation(tmp_path: Path) -> None:
    harness = _Harness(fail_on="reader")
    result = _run(tmp_path, harness)

    failures = [json.loads(line) for line in result.failures_path.read_text(encoding="utf-8").splitlines()]
    assert failures[0]["error_class"] == "generation"
    assert result.failed_phase == "reader"
    assert result.completed_phases == ("preflight", "retrieval", "prepare")


def test_run_smoke_skips_model_phases_without_inventing_accuracy(tmp_path: Path) -> None:
    harness = _Harness()
    result = _run(tmp_path, harness, skip_reader=True)

    assert result.status == "partial"
    assert harness.calls == ["preflight", "retrieval", "prepare"]
    assert result.skipped_phases == ("reader", "score", "replay")
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["accuracy"] is None
    assert summary["completed_phases"] == ["preflight", "retrieval", "prepare"]
    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["classification"] == "smoke-subset"
    assert report["accuracy"] is None
    assert report["usage"]["reader_input_tokens"] is None


def test_run_smoke_requires_a_reader_token_before_any_model_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    harness = _Harness()
    result = _run(tmp_path, harness, reader_transport=None, judge_transport=None)

    assert result.status == "failed"
    assert result.failed_phase == "preflight"
    assert harness.calls == []
    assert (tmp_path / "run" / "run-manifest.json").is_file()
    failures = [json.loads(line) for line in result.failures_path.read_text(encoding="utf-8").splitlines()]
    assert failures[0]["error_class"] == "configuration"
    assert "DEEPSEEK_API_KEY" in failures[0]["summary"]


def test_run_smoke_redacts_a_configured_secret_from_recorded_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    harness = _Harness()
    monkeypatch.setattr(harness, "reader", _leaky_reader(harness))
    result = _run(tmp_path, harness, reader_transport=None, judge_transport=None)

    assert result.failed_phase == "reader"
    recorded = result.failures_path.read_text(encoding="utf-8")
    assert SECRET not in recorded
    assert "<redacted>" in recorded


def test_run_smoke_redacts_a_short_configured_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    short_secret = "abc"
    monkeypatch.setenv("DEEPSEEK_API_KEY", short_secret)
    harness = _Harness()

    def leaky_reader(**arguments: Any) -> Any:
        raise ReaderSmokeError(f"Reader rejected bearer {short_secret}")

    monkeypatch.setattr(harness, "reader", leaky_reader)
    result = _run(tmp_path, harness, reader_transport=None, judge_transport=None)

    assert result.failed_phase == "reader"
    recorded = result.failures_path.read_text(encoding="utf-8")
    assert short_secret not in recorded
    assert "<redacted>" in recorded


def test_run_smoke_redacts_the_powercontext_token_in_model_free_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retrieval stage still resolves POWERCONTEXT_TOKEN when the Reader is skipped."""

    monkeypatch.setenv("POWERCONTEXT_TOKEN", SECRET)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    harness = _Harness()
    monkeypatch.setattr(harness, "retrieval", _leaky_retrieval(harness))
    result = _run(tmp_path, harness, skip_reader=True)

    assert result.failed_phase == "retrieval"
    recorded = result.failures_path.read_text(encoding="utf-8")
    assert SECRET not in recorded
    assert "<redacted>" in recorded


def test_run_smoke_does_not_publish_completed_while_retrieval_is_still_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skip-reader run must stay partial after preflight, not claim completion early."""

    harness = _Harness()
    observed: list[str] = []
    monkeypatch.setattr(harness, "retrieval", _observing_stage(harness.retrieval, observed, tmp_path / "run"))
    result = _run(tmp_path, harness, skip_reader=True)

    assert observed == ["partial"]
    assert result.status == "partial"


def test_run_smoke_does_not_publish_completed_while_model_phases_are_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full run stays partial until every phase, including replay, has completed."""

    harness = _Harness()
    observed: list[str] = []
    monkeypatch.setattr(harness, "reader", _observing_stage(harness.reader, observed, tmp_path / "run"))
    result = _run(tmp_path, harness)

    assert observed == ["partial"]
    assert result.status == "completed"


def _leaky_reader(harness: _Harness) -> Any:
    def reader(**arguments: Any) -> Any:
        harness._record("reader", arguments)
        raise ReaderSmokeError(f"Reader rejected bearer {SECRET}")

    return reader


def _leaky_retrieval(harness: _Harness) -> Any:
    def retrieval(**arguments: Any) -> Any:
        harness._record("retrieval", arguments)
        raise RetrievalSmokeError(f"Runtime rejected bearer {SECRET}")

    return retrieval


def _observing_stage(stage: Callable[..., Any], observed: list[str], run_dir: Path) -> Callable[..., Any]:
    def wrapper(**arguments: Any) -> Any:
        summary = json.loads((run_dir / "run-summary.json").read_text(encoding="utf-8"))
        observed.append(summary["status"])
        return stage(**arguments)

    return wrapper


def test_run_smoke_forwards_the_selected_experiment_arm_to_retrieval(tmp_path: Path) -> None:
    harness = _Harness()
    result = _run(tmp_path, harness, skip_reader=True, experiment_arm="current-memory-hybrid-v1")

    assert result.status == "partial"
    assert harness.retrieval_arguments is not None
    assert harness.retrieval_arguments["experiment_arm"] is CURRENT_MEMORY_HYBRID
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["experiment_arm"]["id"] == "current-memory-hybrid-v1"
    assert manifest["powercontext"]["search_mode"] == "hybrid"
    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    assert report["experiment_arm"]["id"] == "current-memory-hybrid-v1"


def test_run_smoke_rejects_an_unregistered_experiment_arm(tmp_path: Path) -> None:
    harness = _Harness()

    with pytest.raises(ExperimentArmError, match="unknown experiment arm"):
        _run(tmp_path, harness, experiment_arm="l0-persistent-v1")

    assert harness.calls == []
    assert not (tmp_path / "run").exists()


def test_run_smoke_classifies_mode_and_capability_errors(tmp_path: Path) -> None:
    assert classify_error(PowerContextMemoryModeError("hybrid was not executed")) == "integrity"
    assert classify_error(RetrievalCapabilityError("Server does not support hybrid")) == "infrastructure"
    assert classify_error(RetrievalSmokeError("retrieval failed")) == "retrieval"
    assert classify_error(PowerContextMemoryAdapterError("transport failed")) == "infrastructure"


def test_run_smoke_records_and_forwards_separate_reader_and_judge_price_policies(tmp_path: Path) -> None:
    """Reader and Judge may run different models, so each stage carries its own policy."""

    harness = _Harness()
    reader_policy = parse_cost_policy(READER_PRICE_POLICY)
    judge_policy = parse_cost_policy({**READER_PRICE_POLICY, "model": "deepseek-v4-pro"})
    assert reader_policy is not None and judge_policy is not None
    result = _run(tmp_path, harness, reader_price_policy=reader_policy, judge_price_policy=judge_policy)

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["cost_policy"] == {
        "reader": READER_PRICE_POLICY,
        "judge": {**READER_PRICE_POLICY, "model": "deepseek-v4-pro"},
    }
    assert harness.reader_arguments is not None
    assert harness.reader_arguments["price_policy"] is reader_policy
    assert harness.score_arguments is not None
    assert harness.score_arguments["judge_price_policy"] is judge_policy


def test_run_smoke_keeps_both_cost_policies_null_when_no_prices_are_configured(tmp_path: Path) -> None:
    harness = _Harness()
    result = _run(tmp_path, harness)

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["cost_policy"] == {"reader": None, "judge": None}
    assert harness.reader_arguments is not None
    assert harness.reader_arguments["price_policy"] is None
    assert harness.score_arguments is not None
    assert harness.score_arguments["judge_price_policy"] is None
