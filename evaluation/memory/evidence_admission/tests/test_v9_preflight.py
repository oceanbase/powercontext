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
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import Support, digest
from powercontext_eval_evidence_admission.v9.codec import INSTRUCTIONS, prepare
from powercontext_eval_evidence_admission.v9_preflight.adapter import (
    Config,
    Journal,
    QualityFailure,
    normalized_schema,
    open_adapter,
    summary,
)
from powercontext_eval_evidence_admission.v9_preflight.workflow import mock_response

VALID = {
    "candidates": [
        {
            "kind": "fact",
            "evidence_ids": ["e0"],
            "parts": [{"type": "copy", "quote_id": "q0", "byte_start": 0, "byte_end": 4}],
        }
    ]
}


def supplied(text="text"):
    evidence = AtomicMemoryEvidence(
        evidence_id="source:full-id",
        source_ref=SourceRef(source_type="content", source_id="source-a"),
        journal_position=1,
        content=text,
        source_metadata={},
    )
    support = Support(
        evidence_id=evidence.evidence_id,
        source_hash=digest("source-a"),
        materialization_hash=digest("material"),
        projection_hash=digest(evidence.model_dump(mode="json")),
        field_hash=digest("/content"),
        byte_start=0,
        byte_end=len(text.encode()),
        char_start=0,
        char_end=len(text),
    )
    return prepare(AtomicMemoryExtractionInput(evidence=(evidence,)), (support,))


def body(content=None, finish="stop", usage=True):
    result = mock_response()
    result["choices"][0]["message"]["content"] = json.dumps(VALID) if content is None else content
    result["choices"][0]["finish_reason"] = finish
    if not usage:
        result.pop("usage")
    return result


async def execute(directory, handler, *, value=None, review=None, request_hash=None, protocol="v9", instructions=None):
    value = supplied() if value is None else value
    journal = Journal(directory)
    error = output = None
    async with open_adapter(
        Config("deepseek-v4-flash", "https://api.deepseek.com"),
        journal,
        httpx.MockTransport(handler),
        v5_instructions="Return JSON.",
        api_key="offline-secret-do-not-store",
    ) as adapter:
        try:
            output = await adapter.generate(
                value if protocol == "v9" else value.value,
                protocol=protocol,
                sample_id="sample",
                call_index=0,
                instructions=instructions or (INSTRUCTIONS if protocol == "v9" else "Return JSON."),
                request_hash=value.binding_hash if request_hash is None else request_hash,
                review=review,
            )
        except Exception as caught:  # noqa: BLE001 - return any injected SDK failure for audit assertions.
            error = caught
    return output, error, summary(directory)


def test_actual_sdk_sends_compact_payload_documented_limit_and_restores_citations(tmp_path):
    seen = []
    value = supplied()

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=body())

    result, error, audit = asyncio.run(execute(tmp_path / "private", handler, value=value))
    assert error is None and result.output.candidates[0].text == "text"
    assert result.output.candidates[0].evidence_ids == ("source:full-id",)
    assert len(seen) == 1 and seen[0]["max_tokens"] == 2048 and "max_completion_tokens" not in seen[0]
    assert json.loads(seen[0]["messages"][-1]["content"]) == value.payload
    assert audit["attempts"] == audit["terminal_records"] == audit["http_requests"] == 1
    assert audit["attempt_coverage_complete"] and audit["response_capture_complete"]
    assert audit["input_tokens_total"] == 17 and audit["output_tokens_total"] == 11
    terminal = json.loads((tmp_path / "private/attempts.jsonl").read_text().splitlines()[-1])
    assert terminal["quality_review"] == "not_run" and terminal["protocol_output_validated"]
    for p in (tmp_path / "private").iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        assert b"offline-secret-do-not-store" not in p.read_bytes()
    assert stat.S_IMODE((tmp_path / "private").stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("mode", "category"),
    [
        ("http", "provider_http_error"),
        ("connection", "transport_error"),
        ("timeout", "timeout"),
        ("envelope", "json_parse_error"),
        ("json", "json_parse_error"),
        ("schema", "schema_error"),
        ("alias", "invalid_evidence_reference"),
        ("quote", "copy_without_candidate_support"),
        ("bounds", "copy_span_out_of_bounds_or_too_large"),
        ("utf8", "copy_splits_utf8"),
        ("unicode", "copy_splits_protected_sequence"),
        ("length", "response_incomplete"),
        ("missing-finish", "response_completion_unknown"),
        ("aborted", "provider_aborted"),
        ("resource", "provider_resource_interrupted"),
        ("filter", "provider_content_filter"),
        ("empty", "empty_content"),
        ("refusal", "provider_refusal"),
        ("tools", "unexpected_tool_calls"),
        ("capture", "response_capture_incomplete"),
        ("usage-cap", "output_usage_limit_exceeded"),
        ("fenced-alias", "invalid_evidence_reference"),
    ],
)
def test_failures_keep_one_terminal_exact_category_and_no_retry(tmp_path, mode, category):
    calls = []
    content = json.loads(json.dumps(VALID))
    finish = "stop"
    value = supplied()
    if mode in ("alias", "fenced-alias"):
        content["candidates"][0]["evidence_ids"] = ["missing"]
    if mode == "quote":
        content["candidates"][0]["parts"][0]["quote_id"] = "q99"
    if mode == "bounds":
        content["candidates"][0]["parts"][0]["byte_end"] = 9999
    if mode in ("utf8", "unicode"):
        value = supplied("中文" if mode == "utf8" else "e\u0301")
        content["candidates"][0]["parts"][0].update(
            byte_start=1 if mode == "utf8" else 0, byte_end=3 if mode == "utf8" else 1
        )
    if mode == "schema":
        content["candidates"][0]["kind"] = "unknown"
    if mode == "length":
        finish = "length"
    if mode == "missing-finish":
        finish = None
    if mode == "aborted":
        finish = "aborted"
    if mode == "resource":
        finish = "insufficient_system_resource"
    if mode == "filter":
        finish = "content_filter"
    response = body(json.dumps(content), finish)
    if mode == "json":
        response["choices"][0]["message"]["content"] = '{"candidates":'
    if mode == "empty":
        response["choices"][0]["message"]["content"] = ""
    if mode == "refusal":
        response["choices"][0]["message"].update(content=None, refusal="not returning content")
    if mode == "tools":
        response["choices"][0]["message"].update(
            content=None,
            tool_calls=[{"id": "x", "type": "function", "function": {"name": "unexpected", "arguments": "{}"}}],
        )
    if mode == "capture":
        response["choices"][0]["message"]["content"] = json.dumps(content) + " " * 70000
    if mode == "usage-cap":
        response["usage"]["completion_tokens"] = 2049
    if mode == "fenced-alias":
        response["choices"][0]["message"]["content"] = "```json\n" + json.dumps(content) + "\n```"

    def handler(request):
        calls.append(1)
        if mode == "connection":
            raise httpx.ConnectError("sensitive transport text", request=request)
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive timeout text", request=request)
        if mode == "http":
            return httpx.Response(503, json={"error": {"message": "sensitive provider text", "type": "bad"}})
        if mode == "envelope":
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(200, json=response)

    _, error, audit = asyncio.run(execute(tmp_path / "private", handler, value=value))
    assert error is not None
    assert len(calls) == 1 and audit["attempt_coverage_complete"]
    assert audit["outcomes"][0]["error_category"] == category
    assert audit["outcomes"][0]["outcome"] == "failed"
    assert "sensitive transport text" not in (tmp_path / "private/attempts.jsonl").read_text()
    if mode in ("http", "timeout", "connection", "envelope"):
        assert audit["input_tokens_total"] is None and audit["unknown_usage_attempts"] == 1
    else:
        assert audit["input_tokens_total"] == 17


def test_missing_usage_is_unknown_even_after_success(tmp_path):
    result, error, audit = asyncio.run(
        execute(tmp_path / "private", lambda _: httpx.Response(200, json=body(usage=False)))
    )
    assert error is None
    assert result.usage.input_tokens is result.usage.output_tokens is None
    assert audit["input_tokens_total"] is audit["output_tokens_total"] is None
    assert not audit["usage_complete"] and audit["unknown_usage_attempts"] == 1


@pytest.mark.parametrize("category", ["semantic_quality_error", "atomicity_error"])
def test_explicit_review_failures_are_distinct_from_protocol_validity(tmp_path, category):
    def review(_):
        raise QualityFailure(category)

    _, error, audit = asyncio.run(
        execute(tmp_path / "private", lambda _: httpx.Response(200, json=body()), review=review)
    )
    assert isinstance(error, QualityFailure)
    assert audit["outcomes"][0]["failure_stage"] == "quality"
    assert audit["outcomes"][0]["error_category"] == category
    assert audit["usage_complete"]


def test_stale_binding_fails_before_http_without_losing_terminal(tmp_path):
    seen = []
    _, error, audit = asyncio.run(execute(tmp_path / "private", lambda r: seen.append(r), request_hash="wrong"))
    assert error is not None and seen == [] and audit["http_requests"] == 0
    assert audit["outcomes"][0]["error_category"] == "v9_request_binding_mismatch"
    assert audit["attempt_coverage_complete"] and audit["input_tokens_total"] is None


@pytest.mark.parametrize("fail_terminal_write", [False, True])
def test_cancellation_propagates_and_failed_audit_write_never_claims_complete(
    tmp_path, monkeypatch, fail_terminal_write
):
    async def scenario():
        ready = asyncio.Event()

        async def handler(_):
            ready.set()
            await asyncio.Event().wait()

        if fail_terminal_write:
            original = Journal.append

            def append(self, row):
                if row["kind"] == "terminal":
                    raise OSError("audit disk failure")
                original(self, row)

            monkeypatch.setattr(Journal, "append", append)
        task = asyncio.create_task(execute(tmp_path / "private", handler))
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    audit = summary(tmp_path / "private")
    assert audit["attempt_coverage_complete"] == (not fail_terminal_write)
    assert audit["input_tokens_total"] is None
    if not fail_terminal_write:
        assert audit["outcomes"][0]["outcome"] == "cancelled"


def test_request_capture_tampering_is_detected(tmp_path):
    _, error, _ = asyncio.run(execute(tmp_path / "private", lambda _: httpx.Response(200, json=body())))
    assert error is None
    next((tmp_path / "private").glob("*.request.json")).write_text("{}")
    with pytest.raises(EvaluationError, match="audit_request_integrity_mismatch"):
        summary(tmp_path / "private")


def test_v5_baseline_keeps_its_original_schema_and_citation_contract(tmp_path):
    seen = []
    output = {"candidates": [{"kind": "fact", "text": "text", "evidence_ids": ["source:full-id"]}]}

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=body(json.dumps(output)))

    result, error, _ = asyncio.run(execute(tmp_path / "private", handler, protocol="v5"))
    assert error is None and result.output.candidates[0].evidence_ids == ("source:full-id",)
    assert seen[0]["messages"][0]["content"] == "Return JSON."
    assert "TemplateCandidate" not in seen[0]["messages"][1]["content"]
    assert seen[0]["max_tokens"] == 2048


def test_schema_annotation_removal_does_not_delete_property_names():
    schema = {
        "title": "annotation",
        "properties": {"title": {"type": "string", "minLength": 1}, "description": {"type": "integer"}},
    }
    result = normalized_schema(schema)
    assert set(result["properties"]) == {"title", "description"}
    assert result["properties"]["title"]["minLength"] == 1
