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

"""Private evaluation attempt ledger, before SDK parsing and domain validation."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.inference import InvalidInferenceOutputError
from pydantic import BaseModel, ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.wrapper import WrapperModel
from typing_extensions import override

from ..manifests import PACKAGE_ROOT, EvaluationError
from ..models import canonical, digest

MAX_RESPONSE_BYTES = 65_536


def _write_new(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass
class Attempt:
    journal: AttemptJournal
    attempt_id: str
    facts: dict[str, Any] = field(default_factory=dict)
    closed: bool = False
    output_type: type[BaseModel] = AtomicMemoryExtractionOutput

    def event(self, kind: str, **values) -> None:
        self.journal.append({"attempt_id": self.attempt_id, "kind": kind, **values})

    def finish(self, outcome: str, category: str | None) -> None:
        if self.closed:
            raise EvaluationError("duplicate_attempt_terminal")
        self.closed = True
        self.event("terminal", outcome=outcome, error_category=category, **self.facts)


_CURRENT: contextvars.ContextVar[Attempt | None] = contextvars.ContextVar("v6_attempt", default=None)


class AttemptJournal:
    """Audit failure aborts the evaluation; this is not best-effort operational logging."""

    def __init__(self, directory: Path):
        repository = next((parent for parent in PACKAGE_ROOT.parents if (parent / ".git").exists()), None)
        if repository is not None and directory.resolve().is_relative_to(repository):
            raise EvaluationError("private_audit_directory_must_be_outside_repository")
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        directory.chmod(0o700)
        self.directory = directory
        self.events = directory / "attempts.jsonl"
        _write_new(self.events, b"")
        self.sequence = 0

    def append(self, value: dict[str, Any]) -> None:
        with self.events.open("ab") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def begin(self, sample_id: str, input_hash: str) -> Attempt:
        self.sequence += 1
        identity = digest({"sample_id": sample_id, "input_hash": input_hash, "sequence": self.sequence})
        attempt = Attempt(self, identity)
        attempt.event("start", sample_id=sample_id, input_hash=input_hash, sequence=self.sequence)
        return attempt

    def response(self, attempt: Attempt, body: bytes, status: int) -> None:
        path = self.directory / f"{attempt.attempt_id}.response.bin"
        _write_new(path, body[:MAX_RESPONSE_BYTES])
        facts = {
            "http_status": status,
            "response_hash": hashlib.sha256(body).hexdigest(),
            "captured_response_hash": hashlib.sha256(body[:MAX_RESPONSE_BYTES]).hexdigest(),
            "response_bytes": len(body),
            "response_capture_complete": len(body) <= MAX_RESPONSE_BYTES,
            "usage_known": False,
            "input_tokens": None,
            "output_tokens": None,
        }
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            facts["envelope_json_valid"] = False
        else:
            facts["envelope_json_valid"] = isinstance(payload, dict)
            if isinstance(payload, dict):
                usage = payload.get("usage")
                if isinstance(usage, dict):
                    before, after = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if type(before) is int and type(after) is int and before >= 0 and after >= 0:
                        facts.update(usage_known=True, input_tokens=before, output_tokens=after)
                choices = payload.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    choice = choices[0]
                    finish = choice.get("finish_reason")
                    facts["finish_reason"] = (
                        finish if finish in {"stop", "length", "tool_calls", "content_filter"} else "unknown"
                    )
                    message = choice.get("message")
                    if isinstance(message, dict) and isinstance(message.get("content"), str):
                        try:
                            content = json.loads(message["content"])
                        except ValueError:
                            facts["content_json_valid"] = False
                        else:
                            facts["content_json_valid"] = True
                            try:
                                attempt.output_type.model_validate(content)
                            except ValidationError:
                                facts["schema_valid"] = False
                            else:
                                facts["schema_valid"] = True
                model = payload.get("model")
                facts["response_model"] = model[:128] if isinstance(model, str) else None
        attempt.facts.update(facts)
        attempt.event("http_response", **facts)


class AuditTransport(httpx.AsyncBaseTransport):
    """Capture non-streaming response bytes, returning the same response to the SDK."""

    def __init__(self, wrapped: httpx.AsyncBaseTransport):
        self.wrapped = wrapped

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        attempt = _CURRENT.get()
        if attempt is None:
            raise EvaluationError("transport_without_audit_attempt")
        response = await self.wrapped.handle_async_request(request)
        body = await response.aread()
        attempt.journal.response(attempt, body, response.status_code)
        return response

    @override
    async def aclose(self) -> None:
        await self.wrapped.aclose()


class ResponseCaptureModel(WrapperModel):
    @override
    async def request(self, messages, model_settings, model_request_parameters):
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        attempt = _CURRENT.get()
        if attempt is None:
            raise EvaluationError("model_without_audit_attempt")
        attempt.facts["sdk_response_state"] = response.state
        attempt.event("sdk_response", state=response.state)
        if response.state != "complete" or attempt.facts.get("finish_reason") not in {"stop", "tool_calls"}:
            raise InvalidInferenceOutputError("generate", "incomplete provider response")
        return response


def classify(error: BaseException, facts: dict[str, Any]) -> tuple[str, str]:
    if isinstance(error, asyncio.CancelledError):
        return "cancelled", "cancelled"
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "transport", "timeout"
    if isinstance(error, httpx.RequestError):
        return "transport", "transport_error"
    if facts.get("http_status", 0) >= 400 or isinstance(error, ModelHTTPError):
        return "provider", "provider_http_error"
    if facts.get("finish_reason") == "length" or facts.get("sdk_response_state") in {"suspended", "interrupted"}:
        return "response", "response_incomplete"
    if facts.get("finish_reason") == "unknown":
        return "response", "response_completion_unknown"
    if facts.get("finish_reason") == "content_filter":
        return "response", "provider_content_filter"
    if facts.get("structured_output_validated"):
        if (
            isinstance(error, InvalidInferenceOutputError)
            and error.detail == "candidate cites unavailable Source evidence"
        ):
            return "domain", "invalid_evidence_reference"
        if isinstance(error, ValidationError):
            return "domain", "domain_content_error"
    if facts.get("envelope_json_valid") is False or facts.get("content_json_valid") is False:
        return "parse", "json_parse_error"
    if isinstance(error, InvalidInferenceOutputError) and error.detail == "candidate cites unavailable Source evidence":
        return "domain", "invalid_evidence_reference"
    if isinstance(error, ValidationError):
        return "domain", "domain_content_error"
    if facts.get("schema_valid") is False:
        return "schema", "schema_error"
    cause = error
    while cause is not None:
        if isinstance(cause, ValidationError):
            return "schema", "schema_error"
        if isinstance(cause, (TimeoutError, httpx.TimeoutException)):
            return "transport", "timeout"
        cause = cause.__cause__
    # Exhausted Agent parsing/schema retries are normalized by the production adapter.
    return "unknown", "unknown"


class AuditedExtractor:
    def __init__(
        self,
        generator,
        journal: AttemptJournal,
        sample_id: str,
        output_type: type[BaseModel] = AtomicMemoryExtractionOutput,
    ):
        self.generator, self.journal, self.sample_id = generator, journal, sample_id
        self.output_type = output_type

    async def generate(self, value, /):
        attempt = self.journal.begin(self.sample_id, digest(value.model_dump(mode="json")))
        attempt.output_type = self.output_type
        token = _CURRENT.set(attempt)
        try:
            result = await self.generator.generate(value)
            attempt.facts["structured_output_validated"] = True
            # Keep this check at the evaluation domain boundary, after recording SDK output evidence.
            if isinstance(result.output, AtomicMemoryExtractionOutput):
                supplied = {e.evidence_id for e in value.evidence}
                for candidate in result.output.candidates:
                    AtomicMemoryContent(kind=candidate.kind, text=candidate.text)
                    if not set(candidate.evidence_ids) <= supplied:
                        raise InvalidInferenceOutputError(
                            "atomic-memory-extract", "candidate cites unavailable Source evidence"
                        )
        except asyncio.CancelledError as error:
            attempt.facts["failure_stage"], category = classify(error, attempt.facts)
            attempt.finish("cancelled", category)
            raise
        except Exception as error:
            attempt.facts["failure_stage"], category = classify(error, attempt.facts)
            attempt.finish("failed", category)
            raise
        else:
            attempt.finish("success", None)
            return result
        finally:
            _CURRENT.reset(token)


def audit_summary(directory: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in (directory / "attempts.jsonl").read_bytes().splitlines()]
    for row in rows:
        if row["kind"] == "http_response":
            raw = (directory / f"{row['attempt_id']}.response.bin").read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["captured_response_hash"] or len(raw) != min(
                MAX_RESPONSE_BYTES, row["response_bytes"]
            ):
                raise EvaluationError("audit_response_integrity_mismatch")
    starts = {r["attempt_id"] for r in rows if r["kind"] == "start"}
    terminals = [r for r in rows if r["kind"] == "terminal"]
    closed = {r["attempt_id"] for r in terminals}
    if len(closed) != len(terminals) or closed - starts:
        raise EvaluationError("invalid_attempt_terminal_identity")
    return {
        "attempts": len(starts),
        "terminal_records": len(terminals),
        "attempt_coverage_complete": starts == closed,
        "usage_complete": bool(terminals) and all(r.get("usage_known", False) for r in terminals),
        "response_capture_complete": all(r.get("response_capture_complete", False) for r in terminals),
        "known_input_tokens": sum(r.get("input_tokens") or 0 for r in terminals),
        "known_output_tokens": sum(r.get("output_tokens") or 0 for r in terminals),
        "unknown_usage_attempts": sum(not r.get("usage_known", False) for r in terminals),
        "outcomes": [
            {k: r.get(k) for k in ("attempt_id", "outcome", "error_category", "failure_stage", "usage_known")}
            for r in terminals
        ],
    }
