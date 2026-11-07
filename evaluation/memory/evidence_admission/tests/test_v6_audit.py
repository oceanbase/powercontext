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

from __future__ import annotations

import asyncio
import json
import stat

import httpx
import pytest
from openai import AsyncOpenAI
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference import InferenceError
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from pydantic import ValidationError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.v6.audit import (
    AttemptJournal,
    AuditedExtractor,
    AuditTransport,
    ResponseCaptureModel,
    audit_summary,
)


def response(content, finish="stop", usage=True):
    body = {
        "id": "offline-response",
        "object": "chat.completion",
        "created": 0,
        "model": "actual-offline-snapshot",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 17, "completion_tokens": 11, "total_tokens": 28}
    return body


async def execute(directory, handler):
    journal = AttemptJournal(directory)
    async with (
        httpx.AsyncClient(transport=AuditTransport(httpx.MockTransport(handler))) as client,
        AsyncOpenAI(
            api_key="offline-secret-never-log", base_url="https://example.invalid/v1", http_client=client, max_retries=0
        ) as sdk,
    ):
        model = ResponseCaptureModel(OpenAIChatModel("offline-model", provider=OpenAIProvider(openai_client=sdk)))
        generator = PydanticAIStructuredGenerator(
            model=model,
            instructions="Return JSON AtomicMemoryExtractionOutput.",
            input_type=AtomicMemoryExtractionInput,
            output_type=AtomicMemoryExtractionOutput,
            limits=InferenceLimits(
                max_requests=1, max_output_tokens_per_request=2048, timeout_seconds=3, allow_continuations=False
            ),
        )
        extractor = AuditedExtractor(generator, journal, "sample-hash")
        error = None
        try:
            await extractor.generate(AtomicMemoryExtractionInput(evidence=()))
        except (InferenceError, ValidationError) as caught:
            error = caught
    return error, audit_summary(directory)


@pytest.mark.parametrize(
    ("content", "finish", "category"),
    [
        ('{"candidates":', "stop", "json_parse_error"),
        ('{"candidates":[{"kind":"bad","text":"x","evidence_ids":["x"]}]}', "stop", "schema_error"),
        (
            '{"candidates":[{"kind":"fact","text":"x","description":"x","evidence_ids":["missing"]}]}',
            "stop",
            "invalid_evidence_reference",
        ),
        (
            '{"candidates":[{"kind":"fact","text":" ","description":"x","evidence_ids":["missing"]}]}',
            "stop",
            "domain_content_error",
        ),
        ('{"candidates":[]}', "length", "response_incomplete"),
        ('{"candidates":[]}', None, "response_completion_unknown"),
        (
            '```json\n{"candidates":[{"kind":"fact","text":"x","description":"x","evidence_ids":["missing"]}]}\n```',
            "stop",
            "invalid_evidence_reference",
        ),
        (
            '```json\n{"candidates":[{"kind":"fact","text":" ","description":"x","evidence_ids":["missing"]}]}\n```',
            "stop",
            "domain_content_error",
        ),
    ],
)
def test_real_sdk_path_records_rejected_response_usage_before_validation(tmp_path, content, finish, category):
    directory = tmp_path / "private"
    body = response(content, finish)
    error, summary = asyncio.run(execute(directory, lambda _: httpx.Response(200, json=body)))
    assert error is not None
    assert summary["attempts"] == summary["terminal_records"] == 1
    assert summary["attempt_coverage_complete"] and summary["usage_complete"]
    assert summary["known_input_tokens"] == 17 and summary["known_output_tokens"] == 11
    assert summary["outcomes"][0]["error_category"] == category
    raw = next(directory.glob("*.response.bin"))
    assert json.loads(raw.read_bytes()) == body
    assert stat.S_IMODE(raw.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(b"offline-secret-never-log" not in p.read_bytes() for p in directory.iterdir())


def test_valid_response_without_usage_is_success_with_unknown_cost(tmp_path):
    error, summary = asyncio.run(
        execute(tmp_path / "private", lambda _: httpx.Response(200, json=response('{"candidates":[]}', usage=False)))
    )
    assert error is None and summary["outcomes"][0]["outcome"] == "success"
    assert not summary["usage_complete"] and summary["unknown_usage_attempts"] == 1


@pytest.mark.parametrize("failure", ["http", "timeout", "invalid-envelope"])
def test_http_transport_and_envelope_failures_are_auditable_without_messages(tmp_path, failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive exception text", request=request)
        if failure == "http":
            return httpx.Response(400, json={"error": {"message": "provider rejected", "type": "bad_request"}})
        return httpx.Response(200, content=b"not JSON")

    error, summary = asyncio.run(execute(tmp_path / "private", handler))
    assert error is not None and summary["attempt_coverage_complete"]
    expected = {"http": "provider_http_error", "timeout": "timeout", "invalid-envelope": "json_parse_error"}
    assert summary["outcomes"][0]["error_category"] == expected[failure]
    assert not summary["usage_complete"]
    assert "sensitive exception text" not in (tmp_path / "private/attempts.jsonl").read_text()


def test_cancellation_propagates_and_closes_attempt_with_unknown_usage(tmp_path):
    async def scenario():
        ready = asyncio.Event()

        async def handler(_):
            ready.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(execute(tmp_path / "private", handler))
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    summary = audit_summary(tmp_path / "private")
    assert summary["outcomes"][0]["outcome"] == "cancelled"
    assert summary["attempt_coverage_complete"] and not summary["usage_complete"]


def test_interrupted_attempt_is_not_complete_and_duplicate_terminal_is_rejected(tmp_path):
    journal = AttemptJournal(tmp_path / "private")
    attempt = journal.begin("sample", "input")
    assert not audit_summary(journal.directory)["attempt_coverage_complete"]
    attempt.finish("failed", "unknown")
    with pytest.raises(EvaluationError, match="duplicate_attempt_terminal"):
        attempt.finish("success", None)


def test_private_response_tampering_cannot_keep_an_audit_complete(tmp_path):
    directory = tmp_path / "private"
    error, summary = asyncio.run(execute(directory, lambda _: httpx.Response(200, json=response('{"candidates":[]}'))))
    assert error is None and summary["response_capture_complete"]
    next(directory.glob("*.response.bin")).write_bytes(b"changed")
    with pytest.raises(EvaluationError, match="audit_response_integrity_mismatch"):
        audit_summary(directory)


def test_response_capture_limit_preserves_incomplete_flag_without_hiding_usage(tmp_path):
    body = response('{"candidates":[]}' + " " * 70000)
    error, summary = asyncio.run(execute(tmp_path / "private", lambda _: httpx.Response(200, json=body)))
    assert error is None and summary["usage_complete"]
    assert not summary["response_capture_complete"]
    assert next((tmp_path / "private").glob("*.response.bin")).stat().st_size == 65536
