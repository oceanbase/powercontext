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

"""Replay deterministic LongMemEval-V2 scoring from saved Reader and Judge evidence."""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from powercontext_eval.benchmarks.longmemeval_v2.catalog import validate_harness_checkout
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import LLM_JUDGE_NAMES, PER_QUESTION_SCHEMA
from powercontext_eval.errors import PowerContextEvalError

REPLAY_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-score-replay.v1"
REPLAY_FAILURE_SCHEMA = "powercontext.longmemeval-v2-score-replay-failure.v1"
REPLAY_SUMMARY_SCHEMA = "powercontext.longmemeval-v2-score-replay-summary.v1"


class ReplayScoreError(PowerContextEvalError):
    """Saved scoring evidence cannot reproduce one deterministic score result."""


class MetricsAPI(Protocol):
    def eval_name(self, eval_spec: str) -> str: ...

    def eval_from_spec(self, spec: str, *args: Any, **kwargs: Any) -> Any: ...

    def score_to_bool(self, value: Any) -> bool: ...


@dataclass(frozen=True)
class ReplayScoreRun:
    output_dir: Path
    manifest_path: Path
    results_path: Path
    failures_path: Path
    summary_path: Path


def replay_score_smoke(*, score_dir: Path, harness_root: Path, output_dir: Path) -> ReplayScoreRun:
    """Replay all score decisions from local artifacts without a Reader or Judge call."""

    if output_dir.exists():
        raise ReplayScoreError(f"Refusing to overwrite score replay artifacts: {output_dir}")
    validate_harness_checkout(harness_root)
    manifest = _load_json(score_dir / "score-manifest.json", "score manifest")
    summary = _load_json(score_dir / "score-summary.json", "score summary")
    inputs_path = score_dir / "scoring-inputs.local.jsonl"
    judge_path = score_dir / "judge-outputs.jsonl"
    if not inputs_path.is_file() or not judge_path.is_file():
        raise ReplayScoreError("Score replay requires local scoring inputs and saved Judge outputs")
    if manifest.get("classification") != "smoke-subset-score-only" or summary.get("failed") != 0:
        raise ReplayScoreError("Score artifacts are not a successful smoke score run")
    metrics = _load_metrics(harness_root)
    judges = {row["question_id"]: row for row in _jsonl(judge_path, "judge output")}
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ReplayScoreError(f"Refusing to overwrite score replay artifacts: {output_dir}") from error
    except OSError as error:
        raise ReplayScoreError(f"Cannot create score replay artifact directory: {output_dir}") from error

    manifest_path = output_dir / "replay-manifest.json"
    results_path = output_dir / "replay-per-question.jsonl"
    failures_path = output_dir / "replay-failures.jsonl"
    summary_path = output_dir / "replay-summary.json"
    _write_json_exclusive(
        manifest_path,
        {
            "schema": REPLAY_MANIFEST_SCHEMA,
            "classification": "smoke-subset-score-replay",
            "source_score": {
                "directory": str(score_dir.resolve()),
                "manifest_sha256": _file_digest(score_dir / "score-manifest.json"),
                "inputs_sha256": _file_digest(inputs_path),
                "judge_outputs_sha256": _file_digest(judge_path),
                "summary_sha256": _file_digest(score_dir / "score-summary.json"),
            },
        },
    )
    _create_empty(results_path)
    _create_empty(failures_path)
    started_ns = time.perf_counter_ns()
    correct = 0
    failed = 0
    total = 0
    for sequence, score_input in enumerate(_jsonl(inputs_path, "scoring input"), start=1):
        total += 1
        question_id = _nonblank(score_input.get("question_id"), "question_id")
        try:
            result = _replay_one(metrics, score_input, judges.get(question_id), sequence)
        except Exception as error:  # noqa: BLE001 - preserve independent replay failure evidence
            failed += 1
            _append_json(
                failures_path,
                {
                    "schema": REPLAY_FAILURE_SCHEMA,
                    "question_id": question_id,
                    "phase": "replay_score",
                    "error_type": type(error).__name__,
                    "summary": (str(error).strip() or type(error).__name__)[:500],
                },
            )
            continue
        _append_json(results_path, result)
        result_correct = result["correct"]
        if not isinstance(result_correct, bool):
            raise TypeError("Replay result correct field must be boolean")
        correct += int(result_correct)
    _write_json_exclusive(
        summary_path,
        {
            "schema": REPLAY_SUMMARY_SCHEMA,
            "classification": "smoke-subset-score-replay",
            "completed_at": datetime.now(UTC).isoformat(),
            "question_count": total,
            "correct": correct,
            "incorrect": total - correct - failed,
            "failed": failed,
            "accuracy": None if failed else correct / total,
            "reader_calls": 0,
            "judge_calls": 0,
            "elapsed_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
        },
    )
    if failed:
        raise ReplayScoreError(f"Score replay failed for {failed} question(s)")
    return ReplayScoreRun(output_dir, manifest_path, results_path, failures_path, summary_path)


def _replay_one(
    metrics: MetricsAPI,
    score_input: Mapping[str, object],
    judge: Mapping[str, object] | None,
    sequence: int,
) -> dict[str, object]:
    question_id = _nonblank(score_input.get("question_id"), "question_id")
    eval_spec = _nonblank(score_input.get("eval_function"), "eval_function")
    parsed = _nonblank(score_input.get("parsed_prediction"), "parsed_prediction")
    response_hash = score_input.get("reader_response_sha256")
    evaluator = metrics.eval_name(eval_spec)
    if evaluator in LLM_JUDGE_NAMES:
        if not isinstance(judge, Mapping) or judge.get("label") not in {0, 1}:
            raise ReplayScoreError(f"Missing saved Judge label for {question_id}")
        correct = judge["label"] == 1
        mode = "llm_judge_replay"
    else:
        answer = _nonblank(score_input.get("reference_answer"), "reference_answer")
        correct = metrics.score_to_bool(metrics.eval_from_spec(eval_spec, parsed, answer))
        mode = "deterministic_replay"
    return {
        "schema": PER_QUESTION_SCHEMA,
        "sequence": sequence,
        "question_id": question_id,
        "eval_function": eval_spec,
        "score_mode": mode,
        "correct": correct,
        "parsed_prediction": parsed,
        "reader_response_sha256": response_hash,
        "judge_output_ref": None if evaluator not in LLM_JUDGE_NAMES else question_id,
    }


def _load_metrics(harness_root: Path) -> MetricsAPI:
    root = str(harness_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    return cast(MetricsAPI, importlib.import_module("evaluation.qa_eval_metrics"))


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReplayScoreError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ReplayScoreError(f"{label} must be a JSON object")
    return value


def _jsonl(path: Path, label: str) -> Iterator[dict[str, object]]:
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ReplayScoreError(f"{label} row {line_number} must be an object")
                yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReplayScoreError(f"Cannot read {label} input: {path}") from error


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except OSError as error:
        raise ReplayScoreError(f"Cannot hash replay input: {path}") from error
    return hasher.hexdigest()


def _write_json_exclusive(path: Path, value: object) -> None:
    _write_text_exclusive(path, json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")


def _write_text_exclusive(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(value)
    except OSError as error:
        raise ReplayScoreError(f"Cannot write replay artifact: {path}") from error


def _create_empty(path: Path) -> None:
    _write_text_exclusive(path, "")


def _append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplayScoreError(f"{label} must be a non-empty string")
    return value.strip()
