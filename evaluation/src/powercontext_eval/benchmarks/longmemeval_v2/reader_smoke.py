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

"""Run one configured Anthropic-compatible Reader over prepared LongMemEval-V2 prompts."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from powercontext_eval.benchmarks.longmemeval_v2.costs import (
    ModelPricePolicy,
    UsageAccount,
    cost_policy_record,
    usage_cost_block,
)
from powercontext_eval.errors import PowerContextEvalError

DEFAULT_ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
DEFAULT_ANTHROPIC_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
DEFAULT_READER_MODEL = "deepseek-flash-latest"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_TOKEN_ENV = "DEEPSEEK_API_KEY"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
READER_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-reader-run.v1"
READER_OUTPUT_SCHEMA = "powercontext.longmemeval-v2-reader-output.v1"
READER_FAILURE_SCHEMA = "powercontext.longmemeval-v2-reader-failure.v1"
READER_SUMMARY_SCHEMA = "powercontext.longmemeval-v2-reader-summary.v1"


class ReaderSmokeError(PowerContextEvalError):
    """Prepared prompts cannot be sent safely to the configured Reader."""


class ReaderResponseError(ReaderSmokeError):
    """A completed Reader response could not be turned into usable text; its usage is preserved."""

    def __init__(self, message: str, *, usage: dict[str, object], latency_ms: float | None = None) -> None:
        super().__init__(message)
        self.usage = usage
        self.latency_ms = latency_ms


class ReaderTransport(Protocol):
    """Minimal synchronous Anthropic-compatible Reader transport."""

    def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class ReaderSmokeRun:
    """Inspectable artifacts emitted after Reader calls and before scoring."""

    output_dir: Path
    manifest_path: Path
    outputs_path: Path
    failures_path: Path
    summary_path: Path


class AnthropicCompatibleReader:
    """Use bearer authentication without persisting the token or response headers."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ReaderSmokeError("Reader base URL must be an HTTPS URL without credentials")
        if not token.strip():
            raise ReaderSmokeError("Reader token is empty")
        if not model.strip():
            raise ReaderSmokeError("Reader model is empty")
        if max_tokens <= 0:
            raise ReaderSmokeError("Reader max_tokens must be positive")
        if not 0 <= temperature <= 2:
            raise ReaderSmokeError("Reader temperature must be from 0 through 2")
        if timeout_seconds <= 0:
            raise ReaderSmokeError("Reader timeout_seconds must be positive")
        self._endpoint = f"{base_url.rstrip('/')}/v1/messages"
        self._token = token
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

    def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]:
        request = Request(
            self._endpoint,
            data=json.dumps(
                {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                    "system": system,
                    "messages": [{"role": "user", "content": _anthropic_content(content)}],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            raise ReaderSmokeError(f"Reader request returned HTTP {error.code}") from error
        except (OSError, URLError) as error:
            raise ReaderSmokeError("Reader request failed") from error
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReaderSmokeError("Reader returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ReaderSmokeError("Reader returned a non-object response")
        return value


class DeepSeekOpenAIReader:
    """Use DeepSeek's OpenAI-compatible Chat Completions API without persisting credentials."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ReaderSmokeError("Reader base URL must be an HTTPS URL without credentials")
        if not token.strip():
            raise ReaderSmokeError("Reader token is empty")
        if not model.strip():
            raise ReaderSmokeError("Reader model is empty")
        if max_tokens <= 0:
            raise ReaderSmokeError("Reader max_tokens must be positive")
        if not 0 <= temperature <= 2:
            raise ReaderSmokeError("Reader temperature must be from 0 through 2")
        if timeout_seconds <= 0:
            raise ReaderSmokeError("Reader timeout_seconds must be positive")
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._token = token
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds

    def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]:
        request = Request(
            self._endpoint,
            data=json.dumps(
                {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                    "thinking": {"type": "disabled"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": content},
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            raise ReaderSmokeError(f"Reader request returned HTTP {error.code}") from error
        except (OSError, URLError) as error:
            raise ReaderSmokeError("Reader request failed") from error
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReaderSmokeError("Reader returned invalid JSON") from error
        return _normalize_deepseek_response(value)


def run_reader_smoke(
    *,
    prepared_dir: Path,
    output_dir: Path,
    provider: str = "anthropic-compatible",
    base_url: str | None = None,
    base_url_env: str = DEFAULT_ANTHROPIC_BASE_URL_ENV,
    token_env: str | None = None,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.0,
    timeout_seconds: float = 120.0,
    max_questions: int | None = None,
    price_policy: ModelPricePolicy | None = None,
    transport: ReaderTransport | None = None,
) -> ReaderSmokeRun:
    """Call a Reader sequentially and write answer/usage artifacts without persisting credentials."""

    if output_dir.exists():
        raise ReaderSmokeError(f"Refusing to overwrite Reader artifacts: {output_dir}")
    if provider not in {"anthropic-compatible", "deepseek-openai"}:
        raise ReaderSmokeError("provider must be anthropic-compatible or deepseek-openai")
    normalized_model = _nonblank(
        model or (DEFAULT_DEEPSEEK_MODEL if provider == "deepseek-openai" else DEFAULT_READER_MODEL), "model"
    )
    if max_questions is not None and (isinstance(max_questions, bool) or max_questions <= 0):
        raise ReaderSmokeError("max_questions must be null or positive")
    prepared_manifest = _load_json(prepared_dir / "prepare-manifest.json", "prepare manifest")
    prepared_summary = _load_json(prepared_dir / "prepare-summary.json", "prepare summary")
    prompts_path = prepared_dir / "prepared-prompts.jsonl"
    if not prompts_path.is_file():
        raise ReaderSmokeError(f"Missing prepared prompts: {prompts_path}")
    _validate_prepared_artifacts(prepared_manifest, prepared_summary)

    if transport is None:
        resolved_token_env = token_env or (
            DEFAULT_DEEPSEEK_TOKEN_ENV if provider == "deepseek-openai" else DEFAULT_ANTHROPIC_TOKEN_ENV
        )
        resolved_base_url = _nonblank(
            base_url or (DEFAULT_DEEPSEEK_BASE_URL if provider == "deepseek-openai" else os.getenv(base_url_env)),
            "base_url",
        )
        token = _nonblank(os.getenv(resolved_token_env), resolved_token_env)
        if provider == "deepseek-openai":
            active_transport = DeepSeekOpenAIReader(
                resolved_base_url,
                token=token,
                model=normalized_model,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        else:
            active_transport = AnthropicCompatibleReader(
                resolved_base_url,
                token=token,
                model=normalized_model,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
    else:
        resolved_token_env = token_env or (
            DEFAULT_DEEPSEEK_TOKEN_ENV if provider == "deepseek-openai" else DEFAULT_ANTHROPIC_TOKEN_ENV
        )
        active_transport = transport

    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ReaderSmokeError(f"Refusing to overwrite Reader artifacts: {output_dir}") from error
    except OSError as error:
        raise ReaderSmokeError(f"Cannot create Reader artifact directory: {output_dir}") from error

    manifest_path = output_dir / "reader-manifest.json"
    outputs_path = output_dir / "reader-outputs.jsonl"
    failures_path = output_dir / "reader-failures.jsonl"
    summary_path = output_dir / "reader-summary.json"
    _write_json_exclusive(
        manifest_path,
        {
            "schema": READER_MANIFEST_SCHEMA,
            "classification": "smoke-subset-reader-only",
            "prepared": {
                "directory": str(prepared_dir.resolve()),
                "manifest_sha256": _file_digest(prepared_dir / "prepare-manifest.json"),
                "prompts_sha256": _file_digest(prompts_path),
                "summary_sha256": _file_digest(prepared_dir / "prepare-summary.json"),
            },
            "reader": {
                "provider": provider,
                "base_url_env": base_url_env,
                "base_url": None if base_url is None else "configured-directly",
                "token_env": resolved_token_env,
                "model": normalized_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout_seconds": timeout_seconds,
            },
            "judge": None,
            "telemetry": "disabled-by-reader-runner",
            "cost_policy": cost_policy_record(price_policy),
        },
    )

    started_ns = time.perf_counter_ns()
    succeeded = 0
    failed = 0
    account = UsageAccount()
    _create_empty(outputs_path)
    _create_empty(failures_path)
    for prompt in _jsonl(prompts_path):
        if max_questions is not None and succeeded + failed >= max_questions:
            break
        question_id = _nonblank(prompt.get("question_id"), "question_id")
        try:
            output = _run_one(active_transport, prompt)
        except ReaderResponseError as error:
            # The transport completed and reported usage; keep the failed call priced
            # and record its evidence instead of silently reporting zero cost.
            failed += 1
            account.account(error.usage)
            failure: dict[str, object] = {
                "schema": READER_FAILURE_SCHEMA,
                "question_id": question_id,
                "phase": "reader",
                "error_type": type(error).__name__,
                "summary": (str(error).strip() or type(error).__name__)[:500],
                "usage": error.usage,
            }
            if error.latency_ms is not None:
                failure["reader_latency_ms"] = error.latency_ms
            _append_json(failures_path, failure)
            continue
        except Exception as error:  # noqa: BLE001 - each request needs an independent classified failure
            failed += 1
            _append_json(
                failures_path,
                {
                    "schema": READER_FAILURE_SCHEMA,
                    "question_id": question_id,
                    "phase": "reader",
                    "error_type": type(error).__name__,
                    "summary": (str(error).strip() or type(error).__name__)[:500],
                },
            )
            continue
        _append_json(outputs_path, output)
        succeeded += 1
        account.account(output["usage"])
    _write_json_exclusive(
        summary_path,
        {
            "schema": READER_SUMMARY_SCHEMA,
            "classification": "smoke-subset-reader-only",
            "completed_at": datetime.now(UTC).isoformat(),
            "question_count": succeeded + failed,
            "succeeded": succeeded,
            "failed": failed,
            "usage": _reader_usage_totals(
                input_tokens=account.input_tokens,
                output_tokens=account.output_tokens,
                cache_hit_tokens=account.cache_hit_tokens,
                cache_miss_tokens=account.cache_miss_tokens,
                cache_split_reported=account.cache_split_reported,
            ),
            "cost": usage_cost_block(
                price_policy,
                provider=provider,
                model=normalized_model,
                input_tokens=account.input_tokens,
                cache_hit_tokens=account.cache_hit_tokens if account.cache_split_reported else None,
                cache_miss_tokens=account.cache_miss_tokens if account.cache_split_reported else None,
                output_tokens=account.output_tokens,
            ),
            "judge": None,
            "elapsed_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
        },
    )
    if failed:
        raise ReaderSmokeError(f"Reader failed for {failed} prepared prompt(s)")
    return ReaderSmokeRun(
        output_dir=output_dir,
        manifest_path=manifest_path,
        outputs_path=outputs_path,
        failures_path=failures_path,
        summary_path=summary_path,
    )


def _run_one(transport: ReaderTransport, prompt: Mapping[str, object]) -> dict[str, object]:
    question_id = _nonblank(prompt.get("question_id"), "question_id")
    messages = prompt.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise ReaderSmokeError("prepared prompt must contain exactly system and user messages")
    system = messages[0]
    user = messages[1]
    if not isinstance(system, Mapping) or system.get("role") != "system" or not isinstance(system.get("content"), str):
        raise ReaderSmokeError("prepared system message is invalid")
    if not isinstance(user, Mapping) or user.get("role") != "user" or not isinstance(user.get("content"), list):
        raise ReaderSmokeError("prepared user message is invalid")
    raw_content = user.get("content")
    if not isinstance(raw_content, list):
        raise ReaderSmokeError("prepared user message is invalid")
    content: list[dict[str, object]] = []
    for item in raw_content:
        if not isinstance(item, Mapping):
            raise ReaderSmokeError("prepared user content item must be an object")
        item_type = item.get("type")
        if item_type == "text":
            text = item.get("text")
            if not isinstance(text, str):
                raise ReaderSmokeError("prepared text content is invalid")
            content.append({"type": "text", "text": text})
            continue
        if item_type == "image_url":
            image_url = item.get("image_url")
            image_value = image_url.get("url") if isinstance(image_url, Mapping) else None
            if not isinstance(image_value, str):
                raise ReaderSmokeError("prepared image content is invalid")
            content.append({"type": "image_url", "image_url": {"url": image_value}})
            continue
        raise ReaderSmokeError("prepared user content type is unsupported")
    system_content = system.get("content")
    if not isinstance(system_content, str):
        raise ReaderSmokeError("prepared system message is invalid")
    started_ns = time.perf_counter_ns()
    try:
        response = transport.complete(system=system_content, content=content)
    except ReaderResponseError as error:
        error.latency_ms = round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)
        raise
    latency_ms = round((time.perf_counter_ns() - started_ns) / 1_000_000, 3)
    raw_usage = response.get("usage")
    usage = _reader_usage(raw_usage if isinstance(raw_usage, Mapping) else {})
    try:
        answer = _response_text(response)
    except ReaderSmokeError as error:
        raise ReaderResponseError(str(error), usage=usage, latency_ms=latency_ms) from error
    return {
        "schema": READER_OUTPUT_SCHEMA,
        "question_id": question_id,
        "sequence": prompt.get("sequence"),
        "domain": prompt.get("domain"),
        "scope_id": prompt.get("scope_id"),
        "haystack_digest": prompt.get("haystack_digest"),
        "prompt_sha256": prompt.get("prompt_sha256"),
        "response_text": answer,
        "response_sha256": hashlib.sha256(answer.encode()).hexdigest(),
        "usage": usage,
        "model": response.get("model"),
        "stop_reason": response.get("stop_reason"),
        "reader_latency_ms": latency_ms,
    }


def _anthropic_content(content: list[dict[str, object]]) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for item in content:
        if item.get("type") != "image_url":
            converted.append(item)
            continue
        image_url = item.get("image_url")
        value = image_url.get("url") if isinstance(image_url, Mapping) else None
        if not isinstance(value, str) or not value.startswith("data:") or ";base64," not in value:
            raise ReaderSmokeError("Anthropic-compatible Reader requires base64 data URLs for image content")
        header, encoded = value.split(",", 1)
        media_type = header[5 : -len(";base64")]
        if not media_type or not encoded:
            raise ReaderSmokeError("prepared image data URL is invalid")
        converted.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": encoded},
            }
        )
    return converted


def _reader_usage(usage: Mapping[Any, Any]) -> dict[str, object]:
    """Keep a transport's cache hit/miss split so the recorded cost can price it."""

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


def _reader_usage_totals(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    cache_split_reported: bool,
) -> dict[str, object]:
    """Report summed usage, keeping the cache split only when every response reported it."""

    totals: dict[str, object] = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cache_split_reported:
        totals["input_cache_hit_tokens"] = cache_hit_tokens
        totals["input_cache_miss_tokens"] = cache_miss_tokens
    return totals


def _response_text(response: Mapping[str, object]) -> str:
    content = response.get("content")
    if not isinstance(content, list):
        raise ReaderSmokeError("Reader response content must be an array")
    parts = [item.get("text") for item in content if isinstance(item, Mapping) and item.get("type") == "text"]
    text = "".join(part for part in parts if isinstance(part, str)).strip()
    if not text:
        raise ReaderSmokeError("Reader response contains no text")
    return text


def _normalize_deepseek_response(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReaderSmokeError("Reader returned a non-object response")
    # The HTTP call already completed, so its usage stands even when the response
    # body cannot produce text; extract it before any content validation raises.
    raw_usage = value.get("usage")
    usage = _deepseek_usage(raw_usage if isinstance(raw_usage, Mapping) else {})
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ReaderResponseError("Reader response choices are invalid", usage=usage)
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ReaderResponseError("Reader response message is invalid", usage=usage)
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise ReaderResponseError("Reader response contains no text", usage=usage)
    return {
        "model": value.get("model"),
        "stop_reason": choice.get("finish_reason"),
        "content": [{"type": "text", "text": text}],
        "usage": usage,
    }


def _deepseek_usage(usage: Mapping[Any, Any]) -> dict[str, object]:
    """Keep DeepSeek's native cache split instead of collapsing it into one input total.

    ``prompt_cache_hit_tokens`` and ``prompt_cache_miss_tokens`` are billed at different
    rates, so both are preserved; the total input is their sum, matching the API contract.
    """

    hit = _optional_nonnegative_int(usage.get("prompt_cache_hit_tokens"))
    miss = _optional_nonnegative_int(usage.get("prompt_cache_miss_tokens"))
    total = _nonnegative_int(usage.get("prompt_tokens"))
    if hit is None or miss is None:
        return {"input_tokens": total, "output_tokens": _nonnegative_int(usage.get("completion_tokens"))}
    return {
        "input_tokens": total,
        "input_cache_hit_tokens": hit,
        "input_cache_miss_tokens": miss,
        "output_tokens": _nonnegative_int(usage.get("completion_tokens")),
    }


def _validate_prepared_artifacts(manifest: dict[str, object], summary: dict[str, object]) -> None:
    if manifest.get("classification") != "smoke-subset-prepare-only":
        raise ReaderSmokeError("Prepare manifest is not a prepare-only smoke subset")
    if manifest.get("reader") is not None or manifest.get("judge") is not None:
        raise ReaderSmokeError("Prepared artifacts must not contain Reader or Judge execution")
    if summary.get("classification") != "smoke-subset-prepare-only":
        raise ReaderSmokeError("Prepare summary is not a prepare-only smoke subset")
    if summary.get("question_count") != 10 or summary.get("failed") != 0:
        raise ReaderSmokeError("Prepared artifacts must contain ten successful smoke prompts")


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReaderSmokeError(f"Cannot read {label}: {path}") from error
    if not isinstance(value, dict):
        raise ReaderSmokeError(f"{label} must be a JSON object")
    return value


def _jsonl(path: Path) -> Iterator[dict[str, object]]:
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ReaderSmokeError(f"Prepared prompt {line_number} must be an object")
                yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReaderSmokeError(f"Cannot read prepared prompts: {path}") from error


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    except OSError as error:
        raise ReaderSmokeError(f"Cannot hash Reader input: {path}") from error
    return hasher.hexdigest()


def _write_json_exclusive(path: Path, value: object) -> None:
    _write_text_exclusive(path, json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")


def _write_text_exclusive(path: Path, value: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(value)
    except OSError as error:
        raise ReaderSmokeError(f"Cannot write Reader artifact: {path}") from error


def _create_empty(path: Path) -> None:
    _write_text_exclusive(path, "")


def _append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReaderSmokeError(f"{label} must be a non-empty string")
    return value.strip()


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _optional_nonnegative_int(value: object) -> int | None:
    """Keep an absent cache field distinguishable from a reported zero."""

    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
