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

"""Pinned-harness worker that builds bounded LongMemEval-V2 Reader inputs."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from powercontext_eval.benchmarks.longmemeval_v2.prepare_smoke import (
    PREPARE_FAILURE_SCHEMA,
    PREPARE_SUMMARY_SCHEMA,
    PREPARED_PROMPT_SCHEMA,
)


class HarnessPromptAPI(Protocol):
    def get_system_prompt(self, domain: str) -> str: ...

    def validate_memory_context_items(self, memory_context: Any, *, question_id: str) -> list[dict[str, str]]: ...

    def truncate_memory_context(
        self,
        memory_context: list[dict[str, str]],
        *,
        max_tokens: int,
        question_id: str,
    ) -> tuple[list[dict[str, str]], int, int]: ...

    def build_messages(
        self,
        *,
        system_prompt: str,
        question_text: str,
        image_path: str | None,
        memory_context: list[dict[str, str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare bounded LongMemEval-V2 Reader prompts.")
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--prompts-path", type=Path, required=True)
    parser.add_argument("--failures-path", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--processor-model", required=True)
    parser.add_argument("--processor-revision", required=True)
    parser.add_argument("--memory-context-max-tokens", type=int, required=True)
    args = parser.parse_args()
    if args.memory_context_max_tokens <= 0:
        raise SystemExit("memory_context_max_tokens must be positive")
    harness = _load_harness(args.harness_root, args.processor_model, args.processor_revision)
    _run(
        harness,
        input_path=args.input_path,
        prompts_path=args.prompts_path,
        failures_path=args.failures_path,
        summary_path=args.summary_path,
        processor_model=args.processor_model,
        processor_revision=args.processor_revision,
        memory_context_max_tokens=args.memory_context_max_tokens,
    )


def _load_harness(harness_root: Path, processor_model: str, processor_revision: str) -> HarnessPromptAPI:
    root = harness_root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    harness = importlib.import_module("evaluation.harness")
    transformers = importlib.import_module("transformers")
    processor_class = transformers.AutoProcessor
    processor = processor_class.from_pretrained(processor_model, revision=processor_revision)
    harness.MEMORY_CONTEXT_PROCESSOR_LOCAL.processor = processor
    return cast(HarnessPromptAPI, harness)


def _run(
    harness: HarnessPromptAPI,
    *,
    input_path: Path,
    prompts_path: Path,
    failures_path: Path,
    summary_path: Path,
    processor_model: str,
    processor_revision: str,
    memory_context_max_tokens: int,
) -> None:
    started_ns = time.perf_counter_ns()
    succeeded = 0
    failed = 0
    original_tokens_total = 0
    prepared_tokens_total = 0
    _create_empty(prompts_path)
    _create_empty(failures_path)
    for sequence, result in enumerate(_jsonl(input_path), start=1):
        question_id = _nonblank(result.get("question_id"), "question_id")
        try:
            prepared = _prepare_record(
                harness,
                result,
                sequence=sequence,
                memory_context_max_tokens=memory_context_max_tokens,
            )
        except Exception as error:  # noqa: BLE001 - one malformed result must not hide later preparation failures
            failed += 1
            _append_json(
                failures_path,
                {
                    "schema": PREPARE_FAILURE_SCHEMA,
                    "question_id": question_id,
                    "phase": "prompt_prepare",
                    "error_type": type(error).__name__,
                    "summary": (str(error).strip() or type(error).__name__)[:500],
                },
            )
            continue
        _append_json(prompts_path, prepared)
        succeeded += 1
        original_tokens = prepared["memory_context_original_tokens"]
        prepared_tokens = prepared["memory_context_tokens"]
        if not isinstance(original_tokens, int) or not isinstance(prepared_tokens, int):
            raise TypeError("prepared prompt token counts must be integers")
        original_tokens_total += original_tokens
        prepared_tokens_total += prepared_tokens
    _write_json(
        summary_path,
        {
            "schema": PREPARE_SUMMARY_SCHEMA,
            "classification": "smoke-subset-prepare-only",
            "completed_at": datetime.now(UTC).isoformat(),
            "question_count": succeeded + failed,
            "succeeded": succeeded,
            "failed": failed,
            "memory_context_original_tokens": original_tokens_total,
            "memory_context_tokens": prepared_tokens_total,
            "memory_context_max_tokens": memory_context_max_tokens,
            "processor": {"model": processor_model, "revision": processor_revision},
            "reader": None,
            "judge": None,
            "elapsed_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
        },
    )
    if failed:
        raise SystemExit(1)


def _prepare_record(
    harness: HarnessPromptAPI,
    result: Mapping[str, object],
    *,
    sequence: int,
    memory_context_max_tokens: int,
) -> dict[str, object]:
    started_ns = time.perf_counter_ns()
    question_id = _nonblank(result.get("question_id"), "question_id")
    domain = _domain(result.get("domain"))
    question = result.get("question")
    if not isinstance(question, Mapping):
        raise TypeError("question must be an object")
    question_text = _nonblank(question.get("text"), "question.text")
    image_path = question.get("image")
    if image_path is not None and (not isinstance(image_path, str) or not image_path.strip()):
        raise ValueError("question.image must be null or a non-empty string")
    memory_context = harness.validate_memory_context_items(result.get("memory_context"), question_id=question_id)
    bounded_context, original_tokens, bounded_tokens = harness.truncate_memory_context(
        memory_context,
        max_tokens=memory_context_max_tokens,
        question_id=question_id,
    )
    system_prompt = harness.get_system_prompt(domain)
    messages, prompt_messages = harness.build_messages(
        system_prompt=system_prompt,
        question_text=question_text,
        image_path=image_path,
        memory_context=bounded_context,
    )
    return {
        "schema": PREPARED_PROMPT_SCHEMA,
        "sequence": sequence,
        "question_id": question_id,
        "domain": domain,
        "question": {"text": question_text, "image": image_path},
        "scope_id": result.get("scope_id"),
        "haystack_digest": result.get("haystack_digest"),
        "memory_context": bounded_context,
        "memory_context_original_tokens": original_tokens,
        "memory_context_tokens": bounded_tokens,
        "memory_context_max_tokens": memory_context_max_tokens,
        "memory_context_was_truncated": original_tokens > bounded_tokens,
        "memory_context_bytes": sum(len(item["value"].encode()) for item in bounded_context),
        "system_prompt": system_prompt,
        "messages": messages,
        "prompt_messages": prompt_messages,
        "prompt_sha256": _canonical_digest(messages),
        "prepare_latency_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
    }


def _jsonl(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"retrieval result {line_number} is not valid JSON") from error
            if not isinstance(value, dict):
                raise TypeError(f"retrieval result {line_number} is not an object")
            yield value


def _domain(value: object) -> str:
    if value not in {"web", "enterprise"}:
        raise ValueError("domain must be web or enterprise")
    return cast(str, value)


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _create_empty(path: Path) -> None:
    with path.open("x", encoding="utf-8"):
        pass


def _append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
