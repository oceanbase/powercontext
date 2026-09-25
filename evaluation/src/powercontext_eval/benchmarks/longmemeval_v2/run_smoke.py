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

"""Run every LongMemEval-V2 smoke stage into one fail-closed run directory."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeAlias
from urllib.parse import urlsplit, urlunsplit

from powercontext_eval.benchmarks.longmemeval_v2.adapter import (
    PowerContextMemoryAdapterError,
    PowerContextMemoryModeError,
)
from powercontext_eval.benchmarks.longmemeval_v2.arms import (
    ExperimentArm,
    arm_manifest_record,
    resolve_experiment_arm,
)
from powercontext_eval.benchmarks.longmemeval_v2.catalog import (
    UPSTREAM_HARNESS_COMMIT,
    LongMemEvalV2CatalogError,
    LongMemEvalV2EnvironmentError,
    LongMemEvalV2InputError,
)
from powercontext_eval.benchmarks.longmemeval_v2.costs import (
    ModelPricePolicy,
    cost_policy_record,
)
from powercontext_eval.benchmarks.longmemeval_v2.prepare_smoke import (
    DEFAULT_PROCESSOR_MODEL,
    PreparedPromptRun,
    PrepareSmokeError,
    prepare_reader_inputs_smoke,
)
from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import (
    DEFAULT_ANTHROPIC_BASE_URL_ENV,
    DEFAULT_ANTHROPIC_TOKEN_ENV,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_TOKEN_ENV,
    DEFAULT_READER_MODEL,
    ReaderSmokeError,
    ReaderSmokeRun,
    ReaderTransport,
    run_reader_smoke,
)
from powercontext_eval.benchmarks.longmemeval_v2.replay_score import (
    ReplayScoreError,
    ReplayScoreRun,
    replay_score_smoke,
)
from powercontext_eval.benchmarks.longmemeval_v2.report import build_report
from powercontext_eval.benchmarks.longmemeval_v2.retrieval_smoke import (
    RetrievalCapabilityError,
    RetrievalSmokeError,
    RetrievalSmokeRun,
    RetrievalSmokeRuntime,
    run_retrieval_smoke,
)
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import ScoreSmokeError, ScoreSmokeRun, run_score_smoke
from powercontext_eval.benchmarks.longmemeval_v2.smoke import PreparedSmokeRun, prepare_smoke_run
from powercontext_eval.errors import PowerContextEvalError

RUN_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-smoke-run.v1"
RUN_SUMMARY_SCHEMA = "powercontext.longmemeval-v2-smoke-run-summary.v1"
RUN_FAILURE_SCHEMA = "powercontext.longmemeval-v2-smoke-run-failure.v1"

Phase: TypeAlias = Literal["preflight", "retrieval", "prepare", "reader", "score", "replay"]
ErrorClass: TypeAlias = Literal["configuration", "infrastructure", "retrieval", "generation", "judge", "integrity"]
RunStatus: TypeAlias = Literal["completed", "partial", "failed"]

PHASES: tuple[Phase, ...] = ("preflight", "retrieval", "prepare", "reader", "score", "replay")
PHASE_DIRECTORIES: Mapping[Phase, str] = {
    "preflight": "01-inputs",
    "retrieval": "02-retrieval",
    "prepare": "03-prepare",
    "reader": "04-reader",
    "score": "05-score",
    "replay": "06-replay",
}


class RunSmokeError(PowerContextEvalError):
    """The one-command smoke run cannot preserve its evaluation contract."""


@dataclass(frozen=True)
class SmokeStages:
    """The reusable stage entry points, injectable so tests never reach a model or server."""

    preflight: Callable[..., PreparedSmokeRun] = prepare_smoke_run
    retrieval: Callable[..., RetrievalSmokeRun] = run_retrieval_smoke
    prepare: Callable[..., PreparedPromptRun] = prepare_reader_inputs_smoke
    reader: Callable[..., ReaderSmokeRun] = run_reader_smoke
    score: Callable[..., ScoreSmokeRun] = run_score_smoke
    replay: Callable[..., ReplayScoreRun] = replay_score_smoke


@dataclass(frozen=True)
class SmokeRunResult:
    """The inspectable outcome of one orchestrated smoke run."""

    output_dir: Path
    manifest_path: Path
    summary_path: Path
    failures_path: Path
    status: RunStatus
    completed_phases: tuple[Phase, ...]
    skipped_phases: tuple[Phase, ...]
    failed_phase: Phase | None


def classify_error(error: BaseException) -> ErrorClass:
    """Map one raised error to the failure class recorded for review."""

    if isinstance(error, RunSmokeError):
        return "configuration"
    if isinstance(error, LongMemEvalV2InputError):
        return "configuration"
    if isinstance(error, LongMemEvalV2EnvironmentError):
        return "infrastructure"
    if isinstance(error, LongMemEvalV2CatalogError):
        return "integrity"
    if isinstance(error, PowerContextMemoryModeError):
        return "integrity"
    if isinstance(error, RetrievalCapabilityError):
        return "infrastructure"
    if isinstance(error, PowerContextMemoryAdapterError):
        return "infrastructure"
    if isinstance(error, PrepareSmokeError):
        return "infrastructure"
    if isinstance(error, ReaderSmokeError):
        return "generation"
    if isinstance(error, ScoreSmokeError):
        return "judge"
    if isinstance(error, ReplayScoreError):
        return "integrity"
    if isinstance(error, RetrievalSmokeError):
        return "retrieval"
    return "infrastructure"


def run_smoke(
    *,
    data_root: Path,
    dataset_lock: Path,
    smoke_manifest: Path,
    harness_root: Path,
    harness_python: Path,
    processor_revision: str,
    output_dir: Path,
    powercontext_revision: str,
    integration_revision: str,
    run_id: str | None = None,
    processor_model: str = DEFAULT_PROCESSOR_MODEL,
    memory_context_max_tokens: int = 200_000,
    powercontext_base_url: str = "http://127.0.0.1:8000",
    powercontext_token_env: str = "POWERCONTEXT_TOKEN",
    experiment_arm: str | ExperimentArm | None = None,
    search_limit: int = 10,
    timeout_seconds: float = 30.0,
    reader_provider: str = "deepseek-openai",
    reader_model: str | None = None,
    reader_base_url: str | None = None,
    reader_base_url_env: str = DEFAULT_ANTHROPIC_BASE_URL_ENV,
    reader_token_env: str | None = None,
    reader_max_tokens: int = 512,
    reader_temperature: float = 0.0,
    reader_timeout_seconds: float = 120.0,
    judge_provider: str = "deepseek-openai",
    judge_model: str = DEFAULT_DEEPSEEK_MODEL,
    judge_token_env: str = DEFAULT_DEEPSEEK_TOKEN_ENV,
    judge_base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    judge_max_tokens: int = 256,
    judge_temperature: float = 0.0,
    judge_timeout_seconds: float = 120.0,
    reader_price_policy: ModelPricePolicy | None = None,
    judge_price_policy: ModelPricePolicy | None = None,
    skip_reader: bool = False,
    skip_score: bool = False,
    stages: SmokeStages | None = None,
    runtime: RetrievalSmokeRuntime | None = None,
    reader_transport: ReaderTransport | None = None,
    judge_transport: ReaderTransport | None = None,
) -> SmokeRunResult:
    """Run preflight, retrieval, prepare, Reader, Judge scoring, and replay into one run directory."""

    active = stages or SmokeStages()
    normalized_run_id = _nonblank(run_id or output_dir.name, "run_id")
    powercontext_ref = _nonblank(powercontext_revision, "powercontext_revision")
    integration_ref = _nonblank(integration_revision, "integration_revision")
    processor_ref = _nonblank(processor_revision, "processor_revision")
    processor_name = _nonblank(processor_model, "processor_model")
    arm = resolve_experiment_arm(experiment_arm)
    if skip_reader:
        skip_score = True
    if reader_provider not in {"anthropic-compatible", "deepseek-openai"}:
        raise RunSmokeError("reader_provider must be anthropic-compatible or deepseek-openai")
    if judge_provider != "deepseek-openai":
        raise RunSmokeError("judge_provider must be deepseek-openai")
    if isinstance(search_limit, bool) or not 1 <= search_limit <= 50:
        raise RunSmokeError("search_limit must be from 1 through 50")
    if isinstance(memory_context_max_tokens, bool) or memory_context_max_tokens <= 0:
        raise RunSmokeError("memory_context_max_tokens must be positive")
    if output_dir.exists():
        raise RunSmokeError(f"Refusing to overwrite smoke run artifacts: {output_dir}")

    resolved_reader_token_env = _nonblank(
        reader_token_env
        or (DEFAULT_DEEPSEEK_TOKEN_ENV if reader_provider == "deepseek-openai" else None)
        or DEFAULT_ANTHROPIC_TOKEN_ENV,
        "reader_token_env",
    )
    resolved_reader_model = _nonblank(
        reader_model or (DEFAULT_DEEPSEEK_MODEL if reader_provider == "deepseek-openai" else DEFAULT_READER_MODEL),
        "reader_model",
    )
    resolved_judge_model = _nonblank(judge_model, "judge_model")
    resolved_judge_token_env = _nonblank(judge_token_env, "judge_token_env")
    resolved_powercontext_token_env = _nonblank(powercontext_token_env, "powercontext_token_env")
    base_url = _credential_free_url(powercontext_base_url, "powercontext_base_url")
    # The retrieval stage always resolves the PowerContext token, even in skip-reader mode,
    # so its value must always be collected for redaction; Reader/Judge values are collected
    # only while their stages can still raise errors that quote them.
    secret_env_names = [resolved_powercontext_token_env]
    if not skip_reader:
        secret_env_names.append(resolved_reader_token_env)
    if not skip_score:
        secret_env_names.append(resolved_judge_token_env)
    secrets = _known_secrets(tuple(secret_env_names))

    lock_digest = _file_digest(dataset_lock, "dataset lock")
    manifest_digest = _file_digest(smoke_manifest, "smoke manifest")
    started_at = datetime.now(UTC)
    started_ns = time.perf_counter_ns()
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise RunSmokeError(f"Refusing to overwrite smoke run artifacts: {output_dir}") from error
    except OSError as error:
        raise RunSmokeError(f"Cannot create smoke run directory: {output_dir}") from error

    manifest_path = output_dir / "run-manifest.json"
    summary_path = output_dir / "run-summary.json"
    failures_path = output_dir / "failures.jsonl"
    outputs = {phase: output_dir / PHASE_DIRECTORIES[phase] for phase in PHASES}
    with failures_path.open("x", encoding="utf-8", newline=""):
        pass
    _write_json_exclusive(
        manifest_path,
        {
            "schema": RUN_MANIFEST_SCHEMA,
            "classification": "smoke-subset",
            "run_id": normalized_run_id,
            "started_at": started_at.isoformat(),
            "phases": {phase: PHASE_DIRECTORIES[phase] for phase in PHASES},
            "modes": {"reader": not skip_reader, "score": not skip_score},
            "experiment_arm": arm_manifest_record(arm),
            "inputs": {
                "data_root": str(data_root.resolve()),
                "dataset_lock": {"path": str(dataset_lock.resolve()), "content_sha256": lock_digest},
                "smoke_manifest": {"path": str(smoke_manifest.resolve()), "content_sha256": manifest_digest},
                "harness": {
                    "root": str(harness_root.resolve()),
                    "commit": UPSTREAM_HARNESS_COMMIT,
                    "python": str(harness_python.resolve()),
                },
            },
            "processor": {"model": processor_name, "revision": processor_ref},
            "memory_context_max_tokens": memory_context_max_tokens,
            "powercontext": {
                "base_url": base_url,
                "token_env": resolved_powercontext_token_env,
                "search_mode": arm.search_mode,
                "search_limit": search_limit,
                "timeout_seconds": timeout_seconds,
            },
            "reader": None
            if skip_reader
            else {
                "provider": reader_provider,
                "model": resolved_reader_model,
                "token_env": resolved_reader_token_env,
                "base_url": _reader_base_url_record(reader_provider, reader_base_url, reader_base_url_env),
                "max_tokens": reader_max_tokens,
                "temperature": reader_temperature,
                "timeout_seconds": reader_timeout_seconds,
            },
            "judge": None
            if skip_score
            else {
                "provider": judge_provider,
                "model": resolved_judge_model,
                "token_env": resolved_judge_token_env,
                "base_url": _credential_free_url(judge_base_url, "judge_base_url"),
                "max_tokens": judge_max_tokens,
                "temperature": judge_temperature,
                "timeout_seconds": judge_timeout_seconds,
            },
            "revisions": {"powercontext": powercontext_ref, "integration": integration_ref},
            "cost_policy": {
                "reader": cost_policy_record(reader_price_policy),
                "judge": cost_policy_record(judge_price_policy),
            },
            "privacy": {
                "credentials": "resolved-from-environment-at-runtime-never-recorded",
                "reference_answers": "never-read-by-the-adapter-retrieval-prepare-or-reader-stages",
                "reference_answers_readers": (
                    "the score stage reads the locked local questions to score and to write the local replay artifact; "
                    "the replay stage reads only that local artifact"
                ),
                "reference_answers_artifact": "05-score/scoring-inputs.local.jsonl",
            },
        },
    )

    completed: list[Phase] = []
    failures: list[dict[str, object]] = []
    failed: Phase | None = None

    def persist(status: RunStatus, failed_phase: Phase | None) -> None:
        _write_summary(
            summary_path,
            output_dir=output_dir,
            completed=completed,
            skipped=tuple(phase for phase in PHASES if phase not in completed),
            failures=failures,
            status=status,
            failed_phase=failed_phase,
            elapsed_ms=round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
        )

    def run_phase(phase: Phase, action: Callable[[], object]) -> bool:
        nonlocal failed
        try:
            action()
        except Exception as error:  # noqa: BLE001 - every phase failure needs an independent classification
            failure: dict[str, object] = {
                "schema": RUN_FAILURE_SCHEMA,
                "phase": phase,
                "error_class": classify_error(error),
                "error_type": type(error).__name__,
                "summary": _redact(str(error).strip() or type(error).__name__, secrets)[:500],
            }
            _append_json(failures_path, failure)
            failures.append(failure)
            failed = phase
            persist("failed", phase)
            return False
        completed.append(phase)
        persist(_in_progress_status(completed, skip_reader, skip_score), None)
        return True

    def finish() -> SmokeRunResult:
        skipped = tuple(phase for phase in PHASES if phase not in completed)
        if failed is not None:
            status: RunStatus = "failed"
        elif skipped:
            status = "partial"
        else:
            status = "completed"
        persist(status, failed)
        build_report(run_dir=output_dir)
        return SmokeRunResult(
            output_dir=output_dir,
            manifest_path=manifest_path,
            summary_path=summary_path,
            failures_path=failures_path,
            status=status,
            completed_phases=tuple(completed),
            skipped_phases=skipped,
            failed_phase=failed,
        )

    def preflight() -> None:
        _require_reader_configuration(
            enabled=not skip_reader,
            provider=reader_provider,
            token_env=resolved_reader_token_env,
            base_url=reader_base_url,
            base_url_env=reader_base_url_env,
            transport=reader_transport,
        )
        _require_token(
            enabled=not skip_score, token_env=resolved_judge_token_env, label="Judge", transport=judge_transport
        )
        active.preflight(
            data_root=data_root,
            dataset_lock=dataset_lock,
            harness_root=harness_root,
            smoke_manifest=smoke_manifest,
            output_dir=outputs["preflight"],
        )

    if not run_phase("preflight", preflight):
        return finish()
    if not run_phase(
        "retrieval",
        lambda: active.retrieval(
            data_root=data_root,
            dataset_lock=dataset_lock,
            harness_root=harness_root,
            smoke_manifest=smoke_manifest,
            output_dir=outputs["retrieval"],
            run_id=normalized_run_id,
            powercontext_revision=powercontext_ref,
            integration_revision=integration_ref,
            base_url=powercontext_base_url,
            token_env=resolved_powercontext_token_env,
            experiment_arm=arm,
            search_limit=search_limit,
            timeout_seconds=timeout_seconds,
            runtime=runtime,
        ),
    ):
        return finish()
    if not run_phase(
        "prepare",
        lambda: active.prepare(
            retrieval_dir=outputs["retrieval"],
            harness_root=harness_root,
            harness_python=harness_python,
            output_dir=outputs["prepare"],
            processor_model=processor_name,
            processor_revision=processor_ref,
            memory_context_max_tokens=memory_context_max_tokens,
        ),
    ):
        return finish()
    if skip_reader:
        return finish()
    if not run_phase(
        "reader",
        lambda: active.reader(
            prepared_dir=outputs["prepare"],
            output_dir=outputs["reader"],
            provider=reader_provider,
            model=resolved_reader_model,
            base_url=reader_base_url,
            base_url_env=reader_base_url_env,
            token_env=resolved_reader_token_env,
            max_tokens=reader_max_tokens,
            temperature=reader_temperature,
            timeout_seconds=reader_timeout_seconds,
            price_policy=reader_price_policy,
            transport=reader_transport,
        ),
    ):
        return finish()
    if skip_score:
        return finish()
    if not run_phase(
        "score",
        lambda: active.score(
            reader_dir=outputs["reader"],
            data_root=data_root,
            dataset_lock=dataset_lock,
            smoke_manifest=smoke_manifest,
            harness_root=harness_root,
            output_dir=outputs["score"],
            judge_model=resolved_judge_model,
            judge_token_env=resolved_judge_token_env,
            judge_base_url=judge_base_url,
            judge_max_tokens=judge_max_tokens,
            judge_temperature=judge_temperature,
            judge_timeout_seconds=judge_timeout_seconds,
            judge_price_policy=judge_price_policy,
            judge_transport=judge_transport,
        ),
    ):
        return finish()
    if not run_phase(
        "replay",
        lambda: active.replay(
            score_dir=outputs["score"],
            harness_root=harness_root,
            output_dir=outputs["replay"],
        ),
    ):
        return finish()
    return finish()


def _write_summary(
    summary_path: Path,
    *,
    output_dir: Path,
    completed: list[Phase],
    skipped: tuple[Phase, ...],
    failures: list[dict[str, object]],
    status: RunStatus,
    failed_phase: Phase | None,
    elapsed_ms: float,
) -> None:
    _write_json_replace(
        summary_path,
        {
            "schema": RUN_SUMMARY_SCHEMA,
            "classification": "smoke-subset",
            "status": status,
            "completed_at": datetime.now(UTC).isoformat(),
            "completed_phases": list(completed),
            "skipped_phases": list(skipped),
            "failed_phase": failed_phase,
            "question_count": _question_count(output_dir),
            "accuracy": _accuracy(output_dir),
            "failures": list(failures),
            "artifacts": _artifacts(output_dir),
            "elapsed_ms": elapsed_ms,
        },
    )


def _in_progress_status(completed: list[Phase], skip_reader: bool, skip_score: bool) -> RunStatus:
    """Report an in-progress run without claiming phases that are still pending.

    A run may read as ``completed`` only after every phase ran, so a skip-reader or
    skip-score run stays ``partial`` and an abnormal exit cannot leave a ``completed``
    summary behind while retrieval or prepare has not executed.
    """

    if skip_reader or skip_score or any(phase not in completed for phase in PHASES):
        return "partial"
    return "completed"


def _artifacts(output_dir: Path) -> dict[str, str | None]:
    names = {"run_manifest": "run-manifest.json", "run_summary": "run-summary.json", "failures": "failures.jsonl"}
    names.update({phase: PHASE_DIRECTORIES[phase] for phase in PHASES})
    return {name: value if (output_dir / value).exists() else None for name, value in names.items()}


def _question_count(output_dir: Path) -> int | None:
    for phase in ("score", "replay", "reader", "prepare", "retrieval"):
        summary = _load_optional_json(output_dir / PHASE_DIRECTORIES[phase] / _summary_name(phase))
        if summary is None:
            continue
        count = summary.get("question_count")
        if isinstance(count, int) and not isinstance(count, bool):
            return count
    return None


def _accuracy(output_dir: Path) -> dict[str, object] | None:
    for phase in ("score", "replay"):
        summary = _load_optional_json(output_dir / PHASE_DIRECTORIES[phase] / _summary_name(phase))
        if summary is None or summary.get("failed") != 0:
            continue
        correct = summary.get("correct")
        incorrect = summary.get("incorrect")
        total = summary.get("question_count")
        value = summary.get("accuracy")
        if isinstance(correct, int) and isinstance(incorrect, int) and isinstance(total, int):
            return {
                "correct": correct,
                "incorrect": incorrect,
                "failed": 0,
                "value": value if isinstance(value, float) else None,
            }
    return None


def _summary_name(phase: Phase) -> str:
    return {
        "retrieval": "summary.json",
        "prepare": "prepare-summary.json",
        "reader": "reader-summary.json",
        "score": "score-summary.json",
        "replay": "replay-summary.json",
    }[phase]


def _load_optional_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _require_reader_configuration(
    *,
    enabled: bool,
    provider: str,
    token_env: str,
    base_url: str | None,
    base_url_env: str,
    transport: ReaderTransport | None,
) -> None:
    if not enabled or transport is not None:
        return
    if provider == "anthropic-compatible" and base_url is None:
        _require_token(enabled=True, token_env=base_url_env, label="Reader base URL", transport=None)
    _require_token(enabled=True, token_env=token_env, label="Reader", transport=None)


def _require_token(*, enabled: bool, token_env: str, label: str, transport: ReaderTransport | None) -> None:
    """Fail as a configuration error before a model-backed stage spends any work."""

    if not enabled or transport is not None:
        return
    if not os.getenv(token_env, "").strip():
        raise RunSmokeError(f"{label} requires the {token_env} environment variable")


def _known_secrets(env_names: tuple[str, ...]) -> tuple[str, ...]:
    """Collect the current secret values used only to redact failure summaries, never recorded."""

    values = (os.getenv(name, "") for name in env_names)
    return tuple(value for value in values if value)


def _redact(summary: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        summary = summary.replace(secret, "<redacted>")
    return summary


def _reader_base_url_record(provider: str, base_url: str | None, base_url_env: str) -> str:
    if base_url is not None:
        return _credential_free_url(base_url, "reader_base_url")
    if provider == "deepseek-openai":
        return DEFAULT_DEEPSEEK_BASE_URL
    return f"environment:{base_url_env}"


def _credential_free_url(value: str, label: str) -> str:
    parsed = urlsplit(_nonblank(value, label))
    if not parsed.scheme or not parsed.hostname:
        raise RunSmokeError(f"{label} must be an absolute URL")
    if parsed.username is not None or parsed.password is not None:
        raise RunSmokeError(f"{label} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise RunSmokeError(f"{label} must not contain query or fragment data")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _file_digest(path: Path, label: str) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except OSError as error:
        raise RunSmokeError(f"Cannot read LongMemEval-V2 {label}: {path}") from error
    return hasher.hexdigest()


def _write_json_exclusive(path: Path, value: object) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n")
    except OSError as error:
        raise RunSmokeError(f"Cannot write smoke run artifact: {path}") from error


def _write_json_replace(path: Path, value: object) -> None:
    """Update the run summary in place without ever publishing a partial document."""

    temporary = path.with_name(f".{path.name}.partial")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n")
        os.replace(temporary, path)
    except OSError as error:
        raise RunSmokeError(f"Cannot update smoke run summary: {path}") from error


def _append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunSmokeError(f"{label} must be a non-empty string")
    return value.strip()
