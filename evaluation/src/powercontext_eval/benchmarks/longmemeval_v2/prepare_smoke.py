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

"""Prepare pinned LongMemEval-V2 Reader prompts without calling a Reader."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from powercontext_eval.benchmarks.longmemeval_v2.catalog import UPSTREAM_HARNESS_COMMIT, validate_harness_checkout
from powercontext_eval.errors import PowerContextEvalError

DEFAULT_PROCESSOR_MODEL = "Qwen/Qwen3.5-9B"
PREPARE_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-prepare-run.v1"
PREPARED_PROMPT_SCHEMA = "powercontext.longmemeval-v2-prepared-prompt.v1"
PREPARE_FAILURE_SCHEMA = "powercontext.longmemeval-v2-prepare-failure.v1"
PREPARE_SUMMARY_SCHEMA = "powercontext.longmemeval-v2-prepare-summary.v1"


class PrepareSmokeError(PowerContextEvalError):
    """The retrieval artifacts or pinned harness cannot produce Reader inputs."""


@dataclass(frozen=True)
class PreparedPromptRun:
    """Inspectable artifacts emitted before a Reader call."""

    output_dir: Path
    manifest_path: Path
    prompts_path: Path
    failures_path: Path
    summary_path: Path


def prepare_reader_inputs_smoke(
    *,
    retrieval_dir: Path,
    harness_root: Path,
    harness_python: Path,
    output_dir: Path,
    processor_revision: str,
    processor_model: str = DEFAULT_PROCESSOR_MODEL,
    memory_context_max_tokens: int = 200_000,
) -> PreparedPromptRun:
    """Use the pinned harness to truncate context and build deterministic Reader messages."""

    caller_cwd = Path.cwd()
    retrieval_dir = _resolve_path(retrieval_dir, caller_cwd)
    harness_root = _resolve_path(harness_root, caller_cwd)
    harness_python = _resolve_path(harness_python, caller_cwd)
    output_dir = _resolve_path(output_dir, caller_cwd)
    if output_dir.exists():
        raise PrepareSmokeError(f"Refusing to overwrite prepared prompt artifacts: {output_dir}")
    revision = _nonblank(processor_revision, "processor_revision")
    model = _nonblank(processor_model, "processor_model")
    if isinstance(memory_context_max_tokens, bool) or memory_context_max_tokens <= 0:
        raise PrepareSmokeError("memory_context_max_tokens must be positive")
    if not harness_python.is_file():
        raise PrepareSmokeError(f"LongMemEval-V2 harness Python does not exist: {harness_python}")
    validate_harness_checkout(harness_root)

    retrieval_manifest = _load_json(retrieval_dir / "retrieval-manifest.json", "retrieval manifest")
    retrieval_summary = _load_json(retrieval_dir / "summary.json", "retrieval summary")
    retrieval_results = retrieval_dir / "retrieval-results.jsonl"
    if not retrieval_results.is_file():
        raise PrepareSmokeError(f"Missing retrieval results: {retrieval_results}")
    _validate_retrieval_artifacts(retrieval_manifest, retrieval_summary)

    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise PrepareSmokeError(f"Refusing to overwrite prepared prompt artifacts: {output_dir}") from error
    except OSError as error:
        raise PrepareSmokeError(f"Cannot create prepared prompt artifact directory: {output_dir}") from error

    manifest_path = output_dir / "prepare-manifest.json"
    prompts_path = output_dir / "prepared-prompts.jsonl"
    failures_path = output_dir / "prepare-failures.jsonl"
    summary_path = output_dir / "prepare-summary.json"
    _write_json_exclusive(
        manifest_path,
        {
            "schema": PREPARE_MANIFEST_SCHEMA,
            "classification": "smoke-subset-prepare-only",
            "retrieval": {
                "directory": str(retrieval_dir.resolve()),
                "manifest_sha256": _file_digest(retrieval_dir / "retrieval-manifest.json"),
                "results_sha256": _file_digest(retrieval_results),
                "summary_sha256": _file_digest(retrieval_dir / "summary.json"),
            },
            "harness": {
                "commit": UPSTREAM_HARNESS_COMMIT,
                "root": str(harness_root.resolve()),
                "python": str(harness_python),
            },
            "processor": {"model": model, "revision": revision},
            "memory_context_max_tokens": memory_context_max_tokens,
            "reader": None,
            "judge": None,
        },
    )

    command = [
        str(harness_python),
        "-m",
        "powercontext_eval.benchmarks.longmemeval_v2.prepare_worker",
        "--harness-root",
        str(harness_root),
        "--input-path",
        str(retrieval_results),
        "--prompts-path",
        str(prompts_path),
        "--failures-path",
        str(failures_path),
        "--summary-path",
        str(summary_path),
        "--processor-model",
        model,
        "--processor-revision",
        revision,
        "--memory-context-max-tokens",
        str(memory_context_max_tokens),
    ]
    environment = dict(os.environ)
    source_root = Path(__file__).parents[3]
    environment["PYTHONPATH"] = _prepend_path(source_root, environment.get("PYTHONPATH"))
    environment["PYTHONNOUSERSITE"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=harness_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PrepareSmokeError("Cannot start pinned LongMemEval-V2 prompt preparation worker") from error
    _write_text_exclusive(output_dir / "prepare-worker.stdout.log", completed.stdout)
    _write_text_exclusive(output_dir / "prepare-worker.stderr.log", completed.stderr)
    if completed.returncode != 0:
        raise PrepareSmokeError(
            f"Pinned LongMemEval-V2 prompt preparation worker failed with exit {completed.returncode}"
        )
    for path in (prompts_path, failures_path, summary_path):
        if not path.is_file():
            raise PrepareSmokeError(f"Prompt preparation worker did not produce {path.name}")
    return PreparedPromptRun(
        output_dir=output_dir,
        manifest_path=manifest_path,
        prompts_path=prompts_path,
        failures_path=failures_path,
        summary_path=summary_path,
    )


def _validate_retrieval_artifacts(manifest: dict[str, object], summary: dict[str, object]) -> None:
    if manifest.get("classification") != "smoke-subset-retrieval-only":
        raise PrepareSmokeError("Retrieval manifest is not a retrieval-only smoke subset")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("reader") is not None or runtime.get("judge") is not None:
        raise PrepareSmokeError("Retrieval artifacts must not contain Reader or Judge execution")
    if summary.get("classification") != "smoke-subset-retrieval-only":
        raise PrepareSmokeError("Retrieval summary is not a retrieval-only smoke subset")
    question_count = summary.get("question_count")
    failed = summary.get("failed")
    if question_count != 10 or failed != 0:
        raise PrepareSmokeError("Retrieval artifacts must contain ten successful smoke questions")


def _resolve_path(path: Path, caller_cwd: Path) -> Path:
    """Absolutize against the caller's cwd without dereferencing symlinks.

    ``Path.resolve()`` would replace a virtualenv's interpreter with the base Python
    it links to, losing the venv's installed dependencies at worker launch.
    """
    return Path(os.path.abspath(caller_cwd / path))


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PrepareSmokeError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise PrepareSmokeError(f"{label} must be a JSON object")
    return value


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except OSError as error:
        raise PrepareSmokeError(f"Cannot hash prompt preparation input: {path}") from error
    return hasher.hexdigest()


def _prepend_path(path: Path, current: str | None) -> str:
    return str(path) if not current else f"{path}{os.pathsep}{current}"


def _write_json_exclusive(path: Path, value: object) -> None:
    _write_text_exclusive(path, json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")


def _write_text_exclusive(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(value)
    except OSError as error:
        raise PrepareSmokeError(f"Cannot write prepared prompt artifact: {path}") from error


def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PrepareSmokeError(f"{label} must be a non-empty string")
    return value.strip()
