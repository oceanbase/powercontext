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

"""Score LongMemEval-V2 Reader outputs with pinned rules and an optional DeepSeek Judge."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from powercontext_eval.benchmarks.longmemeval_v2.catalog import (
    LongMemEvalV2Catalog,
    SmokeSelection,
    load_dataset_lock,
    load_smoke_manifest,
    validate_harness_checkout,
)
from powercontext_eval.benchmarks.longmemeval_v2.costs import (
    ModelPricePolicy,
    UsageAccount,
    cost_policy_record,
    usage_cost_block,
)
from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_TOKEN_ENV,
    DeepSeekOpenAIReader,
    ReaderResponseError,
    ReaderTransport,
)
from powercontext_eval.errors import PowerContextEvalError

SCORE_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-score-run.v1"
SCORE_INPUT_SCHEMA = "powercontext.longmemeval-v2-score-input.v1"
PER_QUESTION_SCHEMA = "powercontext.longmemeval-v2-score-result.v1"
JUDGE_OUTPUT_SCHEMA = "powercontext.longmemeval-v2-judge-output.v1"
SCORE_FAILURE_SCHEMA = "powercontext.longmemeval-v2-score-failure.v1"
SCORE_SUMMARY_SCHEMA = "powercontext.longmemeval-v2-score-summary.v1"
LLM_JUDGE_NAMES = {"llm_abstention_checker", "llm_gotchas_checker"}


class ScoreSmokeError(PowerContextEvalError):
    """Reader outputs cannot be scored under the pinned LongMemEval-V2 contract."""


class JudgeJudgementError(ScoreSmokeError):
    """A completed Judge call could not be parsed into a judgement; its usage evidence is preserved."""

    def __init__(self, message: str, *, usage: dict[str, object], judge_latency_ms: float) -> None:
        super().__init__(message)
        self.usage = usage
        self.judge_latency_ms = judge_latency_ms


class MetricsAPI(Protocol):
    def eval_name(self, eval_spec: str) -> str: ...

    def extract_boxed_answer(self, text: str) -> str: ...

    def eval_from_spec(self, spec: str, *args: Any, **kwargs: Any) -> Any: ...

    def score_to_bool(self, value: Any) -> bool: ...

    def _build_abstention_judge_messages(self, **kwargs: Any) -> list[dict[str, str]]: ...

    def _build_gotchas_judge_messages(self, **kwargs: Any) -> list[dict[str, str]]: ...

    def _parse_llm_binary_judgement(self, text: str) -> tuple[int, str]: ...


@dataclass(frozen=True)
class ScoreSmokeRun:
    output_dir: Path
    manifest_path: Path
    inputs_path: Path
    results_path: Path
    judge_outputs_path: Path
    failures_path: Path
    summary_path: Path


def run_score_smoke(
    *,
    reader_dir: Path,
    data_root: Path,
    dataset_lock: Path,
    smoke_manifest: Path,
    harness_root: Path,
    output_dir: Path,
    judge_model: str = DEFAULT_DEEPSEEK_MODEL,
    judge_token_env: str = DEFAULT_DEEPSEEK_TOKEN_ENV,
    judge_base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    judge_max_tokens: int = 256,
    judge_temperature: float = 0.0,
    judge_timeout_seconds: float = 120.0,
    judge_price_policy: ModelPricePolicy | None = None,
    judge_transport: ReaderTransport | None = None,
) -> ScoreSmokeRun:
    """Score ten Reader answers and call an LLM Judge only for upstream LLM metric types."""

    if output_dir.exists():
        raise ScoreSmokeError(f"Refusing to overwrite score artifacts: {output_dir}")
    validate_harness_checkout(harness_root)
    lock = load_dataset_lock(dataset_lock)
    declared_selection = load_smoke_manifest(smoke_manifest)
    if declared_selection.tier != lock.tier:
        raise ScoreSmokeError("Smoke manifest tier does not match the dataset lock")
    catalog = LongMemEvalV2Catalog.load(
        data_root,
        tier=declared_selection.tier,
        expected_digests=lock.file_digests,
    )
    selection = catalog.select_smoke(declared_selection.cases)
    reader_manifest = _load_json(reader_dir / "reader-manifest.json", "reader manifest")
    reader_summary = _load_json(reader_dir / "reader-summary.json", "reader summary")
    outputs_path = reader_dir / "reader-outputs.jsonl"
    if not outputs_path.is_file():
        raise ScoreSmokeError(f"Missing Reader outputs: {outputs_path}")
    _validate_reader_artifacts(reader_manifest, reader_summary)
    outputs = _reader_outputs(outputs_path, selection)

    metrics = _load_metrics(harness_root)
    if judge_transport is None:
        token = _nonblank(os.getenv(judge_token_env), judge_token_env)
        transport: ReaderTransport = DeepSeekOpenAIReader(
            judge_base_url,
            token=token,
            model=_nonblank(judge_model, "judge_model"),
            max_tokens=judge_max_tokens,
            temperature=judge_temperature,
            timeout_seconds=judge_timeout_seconds,
        )
    else:
        transport = judge_transport

    questions = _selected_questions(data_root / "questions.jsonl", selection)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ScoreSmokeError(f"Refusing to overwrite score artifacts: {output_dir}") from error
    except OSError as error:
        raise ScoreSmokeError(f"Cannot create score artifact directory: {output_dir}") from error

    manifest_path = output_dir / "score-manifest.json"
    inputs_path = output_dir / "scoring-inputs.local.jsonl"
    results_path = output_dir / "per-question.jsonl"
    judge_outputs_path = output_dir / "judge-outputs.jsonl"
    failures_path = output_dir / "score-failures.jsonl"
    summary_path = output_dir / "score-summary.json"
    _write_json_exclusive(
        manifest_path,
        {
            "schema": SCORE_MANIFEST_SCHEMA,
            "classification": "smoke-subset-score-only",
            "reader": {
                "directory": str(reader_dir.resolve()),
                "manifest_sha256": _file_digest(reader_dir / "reader-manifest.json"),
                "outputs_sha256": _file_digest(outputs_path),
                "summary_sha256": _file_digest(reader_dir / "reader-summary.json"),
            },
            "judge": {
                "provider": "deepseek-openai",
                "model": judge_model,
                "token_env": judge_token_env,
                "base_url": "configured-directly",
                "max_tokens": judge_max_tokens,
                "temperature": judge_temperature,
            },
            "cost_policy": cost_policy_record(judge_price_policy),
        },
    )
    _create_empty(inputs_path)
    _create_empty(results_path)
    _create_empty(judge_outputs_path)
    _create_empty(failures_path)

    started_ns = time.perf_counter_ns()
    correct = 0
    failed = 0
    judge_account = UsageAccount()
    for sequence, question in enumerate(questions, start=1):
        question_id = _nonblank(question.get("id"), "question.id")
        reader = outputs[question_id]
        try:
            score_input, result, judge_output = _score_one(
                metrics, question, reader, sequence=sequence, transport=transport
            )
        except JudgeJudgementError as error:
            # The Judge transport already completed and returned usage; count the call
            # and keep the evidence with the failure instead of losing the accounting.
            failed += 1
            judge_account.account(error.usage)
            _append_json(
                failures_path,
                {
                    "schema": SCORE_FAILURE_SCHEMA,
                    "question_id": question["id"],
                    "phase": "scoring",
                    "error_type": type(error).__name__,
                    "summary": (str(error).strip() or type(error).__name__)[:500],
                    "judge_usage": error.usage,
                    "judge_latency_ms": error.judge_latency_ms,
                },
            )
            continue
        except Exception as error:  # noqa: BLE001 - one scoring failure must not hide subsequent outcomes
            failed += 1
            _append_json(
                failures_path,
                {
                    "schema": SCORE_FAILURE_SCHEMA,
                    "question_id": question["id"],
                    "phase": "scoring",
                    "error_type": type(error).__name__,
                    "summary": (str(error).strip() or type(error).__name__)[:500],
                },
            )
            continue
        _append_json(inputs_path, score_input)
        _append_json(results_path, result)
        if judge_output is not None:
            _append_json(judge_outputs_path, judge_output)
            judge_account.account(judge_output["usage"])
        result_correct = result["correct"]
        if not isinstance(result_correct, bool):
            raise TypeError("score result correct field must be a boolean")
        correct += int(result_correct)
    total = len(questions)
    _write_json_exclusive(
        summary_path,
        {
            "schema": SCORE_SUMMARY_SCHEMA,
            "classification": "smoke-subset-score-only",
            "completed_at": datetime.now(UTC).isoformat(),
            "question_count": total,
            "correct": correct,
            "incorrect": total - correct - failed,
            "failed": failed,
            "accuracy": None if failed else correct / total,
            "judge_calls": judge_account.calls,
            "judge_usage": _judge_usage_totals(
                input_tokens=judge_account.input_tokens,
                output_tokens=judge_account.output_tokens,
                cache_hit_tokens=judge_account.cache_hit_tokens,
                cache_miss_tokens=judge_account.cache_miss_tokens,
                cache_split_reported=judge_account.cache_split_reported,
            ),
            "judge_cost": usage_cost_block(
                judge_price_policy,
                provider="deepseek-openai",
                model=_nonblank(judge_model, "judge_model"),
                input_tokens=judge_account.input_tokens,
                cache_hit_tokens=judge_account.cache_hit_tokens if judge_account.cache_split_reported else None,
                cache_miss_tokens=judge_account.cache_miss_tokens if judge_account.cache_split_reported else None,
                output_tokens=judge_account.output_tokens,
            ),
            "elapsed_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
        },
    )
    if failed:
        raise ScoreSmokeError(f"Scoring failed for {failed} question(s)")
    return ScoreSmokeRun(
        output_dir, manifest_path, inputs_path, results_path, judge_outputs_path, failures_path, summary_path
    )


def _judge_usage(usage: object) -> dict[str, object]:
    """Keep the Judge's cache hit/miss split so its cost is priced per token class."""

    if not isinstance(usage, Mapping):
        return {"input_tokens": 0, "output_tokens": 0}
    record: dict[str, object] = {
        "input_tokens": _nonnegative_int(usage.get("input_tokens")),
        "output_tokens": _nonnegative_int(usage.get("output_tokens")),
    }
    hit = _optional_nonnegative_int(usage.get("input_cache_hit_tokens"))
    miss = _optional_nonnegative_int(usage.get("input_cache_miss_tokens"))
    if hit is not None and miss is not None:
        record["input_cache_hit_tokens"] = hit
        record["input_cache_miss_tokens"] = miss
    return record


def _judge_usage_totals(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    cache_split_reported: bool,
) -> dict[str, object]:
    """Report summed Judge usage, keeping the cache split only when every call reported it."""

    totals: dict[str, object] = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cache_split_reported:
        totals["input_cache_hit_tokens"] = cache_hit_tokens
        totals["input_cache_miss_tokens"] = cache_miss_tokens
    return totals


def _score_one(
    metrics: MetricsAPI,
    question: dict[str, object],
    reader: Mapping[str, object],
    *,
    sequence: int,
    transport: ReaderTransport,
) -> tuple[dict[str, object], dict[str, object], dict[str, object] | None]:
    question_id = _nonblank(question.get("id"), "question id")
    response = _nonblank(reader.get("response_text"), "Reader response")
    answer = _nonblank(question.get("answer"), "reference answer")
    eval_spec = _nonblank(question.get("eval_function"), "eval function")
    parsed = metrics.extract_boxed_answer(response)
    evaluator = metrics.eval_name(eval_spec)
    score_input = {
        "schema": SCORE_INPUT_SCHEMA,
        "question_id": question_id,
        "eval_function": eval_spec,
        "reference_answer": answer,
        "reader_response": response,
        "parsed_prediction": parsed,
        "reader_response_sha256": hashlib.sha256(response.encode()).hexdigest(),
    }
    judge_output: dict[str, object] | None = None
    if evaluator in LLM_JUDGE_NAMES:
        messages = _judge_messages(metrics, evaluator, question, answer, response, parsed)
        started_ns = time.perf_counter_ns()
        try:
            judge_response = transport.complete(
                system=messages[0]["content"],
                content=[{"type": "text", "text": messages[1]["content"]}],
            )
        except ReaderResponseError as error:
            # The Judge transport completed and reported usage; route it through the
            # usage-preserving failure path instead of losing the accounting.
            raise JudgeJudgementError(
                f"Judge response for question {question_id} could not be read: {error}",
                usage=error.usage,
                judge_latency_ms=round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
            ) from error
        judge_latency_ms = round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)
        usage = _judge_usage(judge_response.get("usage"))
        try:
            judge_text = _response_text(judge_response)
            label, reason = metrics._parse_llm_binary_judgement(judge_text)
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            raise JudgeJudgementError(
                f"Judge judgement for question {question_id} could not be parsed: {detail[:200]}",
                usage=usage,
                judge_latency_ms=judge_latency_ms,
            ) from error
        judge_output = {
            "schema": JUDGE_OUTPUT_SCHEMA,
            "question_id": question_id,
            "evaluator": evaluator,
            "label": label,
            "reason": reason,
            "response_sha256": hashlib.sha256(judge_text.encode()).hexdigest(),
            "usage": usage,
            "judge_latency_ms": judge_latency_ms,
        }
        correct = label == 1
        mode = "llm_judge"
    else:
        correct = metrics.score_to_bool(metrics.eval_from_spec(eval_spec, parsed, answer))
        mode = "deterministic"
    result = {
        "schema": PER_QUESTION_SCHEMA,
        "sequence": sequence,
        "question_id": question_id,
        "domain": question.get("domain"),
        "eval_function": eval_spec,
        "score_mode": mode,
        "correct": correct,
        "parsed_prediction": parsed,
        "reader_response_sha256": hashlib.sha256(response.encode()).hexdigest(),
        "judge_output_ref": None if judge_output is None else question_id,
    }
    return score_input, result, judge_output


def _judge_messages(
    metrics: MetricsAPI,
    evaluator: str,
    question: dict[str, object],
    answer: str,
    response: str,
    parsed: str,
) -> list[dict[str, str]]:
    arguments = {
        "question_text": _question_text(question),
        "reference_answer": answer,
        "model_full_response": response,
        "model_final_answer": parsed,
    }
    if evaluator == "llm_abstention_checker":
        return metrics._build_abstention_judge_messages(**arguments)
    return metrics._build_gotchas_judge_messages(**arguments)


def _load_metrics(harness_root: Path) -> MetricsAPI:
    root = str(harness_root.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    return cast(MetricsAPI, importlib.import_module("evaluation.qa_eval_metrics"))


def _selected_questions(path: Path, selection: object) -> tuple[dict[str, object], ...]:
    cases = getattr(selection, "cases", ())
    ids = [case.question_id for case in cases]
    found: dict[str, dict[str, object]] = {}
    for row in _jsonl(path, "question"):
        question_id = row.get("id")
        if isinstance(question_id, str) and question_id in ids:
            found[question_id] = row
    missing = [question_id for question_id in ids if question_id not in found]
    if missing:
        raise ScoreSmokeError(f"Missing smoke questions for scoring: {missing}")
    return tuple(found[question_id] for question_id in ids)


def _reader_outputs(path: Path, selection: SmokeSelection) -> dict[str, dict[str, object]]:
    expected = {case.question_id for case in selection.cases}
    outputs: dict[str, dict[str, object]] = {}
    for row in _jsonl(path, "reader output"):
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ScoreSmokeError("Reader output has an invalid question_id")
        if question_id in outputs:
            raise ScoreSmokeError(f"Reader outputs contain a duplicate question_id: {question_id}")
        if question_id not in expected:
            raise ScoreSmokeError(f"Reader output is not part of the fixed smoke subset: {question_id}")
        outputs[question_id] = row
    if set(outputs) != expected:
        raise ScoreSmokeError("Reader outputs do not match the fixed smoke question ids")
    return outputs


def _validate_reader_artifacts(manifest: dict[str, object], summary: dict[str, object]) -> None:
    if manifest.get("classification") != "smoke-subset-reader-only":
        raise ScoreSmokeError("Reader manifest is not a reader-only smoke subset")
    if manifest.get("judge") is not None:
        raise ScoreSmokeError("Reader artifacts must not contain Judge execution")
    if summary.get("classification") != "smoke-subset-reader-only":
        raise ScoreSmokeError("Reader summary is not a reader-only smoke subset")
    if summary.get("question_count") != 10 or summary.get("failed") != 0:
        raise ScoreSmokeError("Reader artifacts must contain ten successful smoke answers")


def _question_text(question: Mapping[str, object]) -> str:
    value = question.get("question")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _nonblank(value.get("text"), "question.text")
    raise ScoreSmokeError("Question text is invalid")


def _response_text(response: Mapping[str, object]) -> str:
    content = response.get("content")
    if not isinstance(content, list):
        raise ScoreSmokeError("Judge response content is invalid")
    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        text_part = item.get("text")
        if isinstance(text_part, str):
            text_parts.append(text_part)
    text = "".join(text_parts).strip()
    if not text:
        raise ScoreSmokeError("Judge response contains no text")
    return text


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScoreSmokeError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ScoreSmokeError(f"{label} must be a JSON object")
    return value


def _jsonl(path: Path, label: str) -> Iterator[dict[str, object]]:
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ScoreSmokeError(f"{label} row {line_number} must be an object")
                yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScoreSmokeError(f"Cannot read {label} input: {path}") from error


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except OSError as error:
        raise ScoreSmokeError(f"Cannot hash score input: {path}") from error
    return hasher.hexdigest()


def _write_json_exclusive(path: Path, value: object) -> None:
    _write_text_exclusive(path, json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")


def _write_text_exclusive(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(value)
    except OSError as error:
        raise ScoreSmokeError(f"Cannot write score artifact: {path}") from error


def _create_empty(path: Path) -> None:
    _write_text_exclusive(path, "")


def _append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoreSmokeError(f"{label} must be a non-empty string")
    return value.strip()


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _optional_nonnegative_int(value: object) -> int | None:
    """Keep an absent cache field distinguishable from a reported zero."""

    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
