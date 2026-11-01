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
from dataclasses import asdict
from typing import Any

import pytest
from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.builtin.artifacts.atomic_memory.input_encoding import (
    AtomicMemoryInputEncodingError,
    collect_atomic_memory_input_encodings,
    decode_atomic_memory_input,
    encode_atomic_memory_input,
    record_atomic_memory_input_encoding,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryComparisonItem,
    AtomicMemoryReconciliationInput,
)
from powercontext.builtin.inference.tokens import TokenEstimator, TokenEstimatorProfile, character_token_estimator
from powercontext.sources import SourceRef

_DETAIL = "Keep independent source identities, exact dates, conditions, and repeated occurrences. " * 8


def _input(*, metadata: Any = None, count: int = 8) -> AtomicMemoryExtractionInput:
    if metadata is None:
        metadata = {"session": "session-42", "conditions": _DETAIL}
    return AtomicMemoryExtractionInput(
        evidence=tuple(
            AtomicMemoryEvidence(
                evidence_id=f"source:{index}",
                source_ref=SourceRef(source_type="content", source_id=f"turn-{index}"),
                journal_position=index,
                content={"source_id": f"turn-{index}", "content": f"Independent fact {index}", "metadata": metadata},
                source_metadata={"name": f"turn-{index}", "metadata": metadata},
                via_artifact=(
                    ArtifactRef(family="atomic-memory", artifact_id="memory-1", revision=3) if index == 2 else None
                ),
            )
            for index in range(1, count + 1)
        )
    )


def _encoded_envelope() -> dict[str, Any]:
    result = encode_atomic_memory_input(_input(), mode="active")
    assert result.report.reason == "applied"
    return json.loads(result.prompt)


def test_active_saves_complete_prompt_and_restores_all_ordered_evidence() -> None:
    value = _input()
    original = value.model_dump_json()
    result = encode_atomic_memory_input(value, mode="active")

    assert result.report.reason == "applied"
    assert result.report.replacements >= 2
    assert result.report.shared_values >= 1
    assert result.report.selected_bytes < result.report.original_bytes
    assert result.report.selected_estimated_tokens < result.report.original_estimated_tokens
    assert decode_atomic_memory_input(result.prompt) == original
    assert value.model_dump_json() == original
    restored = AtomicMemoryExtractionInput.model_validate_json(decode_atomic_memory_input(result.prompt))
    assert [item.evidence_id for item in restored.evidence] == [f"source:{index}" for index in range(1, 9)]
    assert restored.evidence[1].via_artifact == value.evidence[1].via_artifact


def test_off_is_default_and_shadow_only_reports_the_candidate() -> None:
    value = _input()
    off = encode_atomic_memory_input(value)
    shadow = encode_atomic_memory_input(value, mode="shadow")
    active = encode_atomic_memory_input(value, mode="active")

    assert off.prompt == shadow.prompt == value.model_dump_json()
    assert off.report.reason == "disabled"
    assert off.report.candidate_bytes is None
    assert shadow.report.reason == "shadow_candidate"
    assert shadow.report.candidate_bytes == active.report.selected_bytes
    assert shadow.report.candidate_estimated_tokens == active.report.selected_estimated_tokens
    assert shadow.report.selected_estimated_tokens == shadow.report.original_estimated_tokens


def test_same_estimator_counts_instructions_dictionary_and_reference_overhead() -> None:
    value = _input()
    estimator = TokenEstimator(
        TokenEstimatorProfile(estimator_id="test:utf8", version="1"), lambda text: len(text.encode())
    )
    result = encode_atomic_memory_input(value, mode="active", estimator=estimator)

    assert result.report.reason == "applied"
    assert result.report.original_estimated_tokens == len(value.model_dump_json().encode())
    assert result.report.selected_estimated_tokens == len(result.prompt.encode())
    assert result.report.estimator_id == "test:utf8"
    assert result.report.estimator_version == "1"
    assert json.loads(result.prompt)["encoding"]


def test_candidate_with_no_token_saving_returns_original_even_when_bytes_shrink() -> None:
    value = _input()
    estimator = TokenEstimator(TokenEstimatorProfile(estimator_id="test:flat", version="1"), lambda _text: 100)
    result = encode_atomic_memory_input(value, mode="active", estimator=estimator)

    assert result.prompt == value.model_dump_json()
    assert result.report.reason == "insufficient_savings"
    assert result.report.candidate_bytes is not None
    assert result.report.candidate_bytes < result.report.original_bytes
    assert result.report.selected_estimated_tokens == 100


@pytest.mark.parametrize(("candidate_tokens", "applied"), [(970, False), (960, False), (950, True)])
def test_absolute_and_relative_savings_thresholds_are_both_required(candidate_tokens: int, applied: bool) -> None:
    estimator = TokenEstimator(
        TokenEstimatorProfile(estimator_id="test:threshold", version="1"),
        lambda text: candidate_tokens if text.startswith('{"schema":"powercontext.atomic-memory-input.v1",') else 1000,
    )
    result = encode_atomic_memory_input(_input(), mode="active", estimator=estimator)

    assert (result.report.reason == "applied") is applied
    assert result.report.selected_estimated_tokens == (candidate_tokens if applied else 1000)


def test_small_or_unique_input_never_pays_for_an_unprofitable_envelope() -> None:
    for value in (AtomicMemoryExtractionInput(evidence=()), _input(metadata={"event": "tool"}, count=1)):
        result = encode_atomic_memory_input(value, mode="active")
        assert result.prompt == value.model_dump_json()
        assert result.report.reason in {"no_repetition", "insufficient_savings"}


def test_repeated_payload_with_excessive_encoding_overhead_stays_original() -> None:
    value = _input(metadata="x" * 96, count=1)
    result = encode_atomic_memory_input(value, mode="active")

    assert result.report.reason == "insufficient_savings"
    assert result.prompt == value.model_dump_json()
    assert result.report.candidate_bytes is not None
    assert result.report.candidate_bytes > result.report.original_bytes


def test_scalars_key_order_and_fake_references_are_exactly_preserved() -> None:
    metadata = {
        "z": _DETAIL,
        "a": [True, False, 1, 1.0, -0.0, 1e-7, 1e20, None, "", "中文\n\r\t\u2028\u0000"],
        "$ref": {"r": 0, "shared": ["r", 1], "o": [["key", ["a", []]]]},
        "backslashes": r'keep\\slashes / and "quotes"',
        "brackets": "[[[[[[{{{{{{" * 30,
    }
    value = _input(metadata=metadata)
    result = encode_atomic_memory_input(value, mode="active")

    assert result.report.reason == "applied"
    assert decode_atomic_memory_input(result.prompt) == value.model_dump_json()
    restored = json.loads(decode_atomic_memory_input(result.prompt))
    scalars = restored["evidence"][0]["content"]["metadata"]["a"]
    assert type(scalars[0]) is bool
    assert type(scalars[2]) is int
    assert type(scalars[3]) is float


def test_equal_text_in_distinct_sources_keeps_each_occurrence_and_time() -> None:
    base = _input()
    evidence = tuple(
        item.model_copy(
            update={
                "content": {"content": _DETAIL, "effective_date": f"2026-10-{index:02}", "metadata": {"note": _DETAIL}}
            }
        )
        for index, item in enumerate(base.evidence, start=1)
    )
    value = AtomicMemoryExtractionInput(evidence=evidence)
    result = encode_atomic_memory_input(value, mode="active")

    assert result.report.reason == "applied"
    assert decode_atomic_memory_input(result.prompt) == value.model_dump_json()
    restored = json.loads(decode_atomic_memory_input(result.prompt))
    assert len(restored["evidence"]) == len(evidence)
    assert [item["content"]["effective_date"] for item in restored["evidence"]] == [
        f"2026-10-{index:02}" for index in range(1, 9)
    ]


def test_reconciliation_retains_proposal_related_order_and_evidence_bindings() -> None:
    evidence = _input().evidence
    proposal = AtomicMemoryComparisonItem(
        item_id="proposal",
        kind="constraint",
        text=_DETAIL,
        description="保留独立约束与原始引用。",
        original_refs=(),
        evidence_ids=("source:1", "source:2"),
    )
    related = (
        AtomicMemoryComparisonItem(
            item_id="related-2",
            kind="fact",
            text=_DETAIL,
            description="Historical fact with its own source.",
            original_refs=(),
            evidence_ids=("source:5",),
        ),
        AtomicMemoryComparisonItem(
            item_id="related-1", kind="fact", text="A different fact", original_refs=(), evidence_ids=("source:3",)
        ),
    )
    value = AtomicMemoryReconciliationInput(proposal=proposal, related=related, evidence=evidence)
    result = encode_atomic_memory_input(value, mode="active")

    assert result.report.stage == "atomic_memory.reconcile"
    assert result.report.reason == "applied"
    assert decode_atomic_memory_input(result.prompt) == value.model_dump_json()


def test_unsupported_prompt_or_schema_returns_original_without_a_candidate() -> None:
    class OtherInput(BaseModel):
        content: str

    value: Any = OtherInput(content=_DETAIL)
    unsupported = encode_atomic_memory_input(value, mode="active")
    incompatible_prompt = encode_atomic_memory_input(_input(), mode="active", allowed=False)

    assert unsupported.prompt == value.model_dump_json()
    assert unsupported.report.reason == "unsupported_schema"
    assert incompatible_prompt.report.reason == "unsupported_prompt"
    assert unsupported.report.candidate_bytes is incompatible_prompt.report.candidate_bytes is None


@pytest.mark.parametrize("mode", ["shadow", "active"])
def test_atomic_adapter_rejects_invalid_typed_input_even_when_json_is_reversible(mode: Any) -> None:
    original = _input()
    value = original.model_copy(
        update={
            "evidence": (original.evidence[0].model_copy(update={"journal_position": 0}), *original.evidence[1:]),
        }
    )
    result = encode_atomic_memory_input(value, mode=mode)

    assert result.prompt == value.model_dump_json()
    assert result.report.reason == "roundtrip_failed"
    assert result.report.candidate_bytes is not None
    assert result.report.selected_estimated_tokens == result.report.original_estimated_tokens


@pytest.mark.parametrize("case", ["bytes", "depth", "nodes"])
def test_input_bounds_return_original_without_encoding(case: str) -> None:
    metadata: Any
    if case == "bytes":
        metadata = "x" * 270_000
    elif case == "nodes":
        metadata = [None] * 20_001
    else:
        metadata = "leaf"
        for _ in range(30):
            metadata = {"nested": metadata}
    value = _input(metadata=metadata, count=1)
    result = encode_atomic_memory_input(value, mode="active")

    assert result.prompt == value.model_dump_json()
    assert result.report.candidate_bytes is None
    assert result.report.reason == {"bytes": "input_limit", "depth": "depth_limit", "nodes": "node_limit"}[case]


@pytest.mark.parametrize(
    "node",
    [
        ["r", True],
        ["r", -1],
        ["r", 999],
        ["r", 0.0],
        ["r"],
        ["r", 0, "extra"],
        ["o", [["x", None], ["x", None]]],
        ["o", [[1, None]]],
        ["o", [None]],
        ["a", None],
        [[], []],
        {"$ref": 0},
    ],
)
def test_decoder_rejects_malformed_nodes_and_references(node: Any) -> None:
    envelope = _encoded_envelope()
    envelope["value"] = node
    with pytest.raises(AtomicMemoryInputEncodingError):
        decode_atomic_memory_input(json.dumps(envelope))


@pytest.mark.parametrize("field", ["schema", "stage", "encoding"])
def test_decoder_rejects_unknown_envelope_contract(field: str) -> None:
    envelope = _encoded_envelope()
    envelope[field] = []
    with pytest.raises(AtomicMemoryInputEncodingError, match="unsupported_envelope"):
        decode_atomic_memory_input(json.dumps(envelope))


def test_decoder_rejects_duplicate_json_keys_and_nonfinite_numbers() -> None:
    encoded = encode_atomic_memory_input(_input(), mode="active").prompt
    with pytest.raises(AtomicMemoryInputEncodingError, match="duplicate_key"):
        decode_atomic_memory_input('{"schema":"duplicate",' + encoded[1:])
    for number in ("NaN", "Infinity", "1e999"):
        envelope = _encoded_envelope()
        envelope["shared"] = ["numeric-placeholder"]
        raw = json.dumps(envelope).replace('"numeric-placeholder"', number)
        with pytest.raises(AtomicMemoryInputEncodingError, match="invalid_number"):
            decode_atomic_memory_input(raw)


@pytest.mark.parametrize("case", ["bytes", "nodes", "depth", "dictionary"])
def test_decoder_bounds_expanded_data_not_just_compact_input(case: str) -> None:
    envelope = _encoded_envelope()
    if case == "bytes":
        envelope["shared"] = ["x" * 2000]
        envelope["value"] = ["a", [["r", 0]] * 200]
    elif case == "nodes":
        envelope["shared"] = [[None] * 200]
        envelope["value"] = ["a", [["r", 0]] * 110]
    elif case == "depth":
        envelope["value"] = ["r", 0]
        for _ in range(30):
            envelope["value"] = ["a", [envelope["value"]]]
    else:
        envelope["shared"] = ["x"] * 129
    with pytest.raises(
        AtomicMemoryInputEncodingError,
        match={"bytes": "input_limit", "nodes": "node_limit", "depth": "depth_limit", "dictionary": "dictionary_limit"}[
            case
        ],
    ):
        decode_atomic_memory_input(json.dumps(envelope))


def test_decoder_requires_a_complete_supported_stage_input() -> None:
    envelope = _encoded_envelope()
    envelope["value"] = ["o", []]
    with pytest.raises(AtomicMemoryInputEncodingError, match="invalid_stage_input"):
        decode_atomic_memory_input(json.dumps(envelope))


@pytest.mark.parametrize("text", ['"\\ud800"', '"\ud800"'])
def test_decoder_rejects_invalid_unicode_without_content_in_error(text: str) -> None:
    raw = encode_atomic_memory_input(_input(), mode="active").prompt
    raw = raw.replace('"shared":[', f'"shared":[{text},', 1)
    with pytest.raises(AtomicMemoryInputEncodingError) as raised:
        decode_atomic_memory_input(raw)
    assert str(raised.value) == "invalid_json"
    assert raised.value.__cause__ is None


def test_reports_are_explicit_bounded_and_contain_no_evidence() -> None:
    value = _input()
    with collect_atomic_memory_input_encodings() as outer:
        result = encode_atomic_memory_input(value, mode="shadow")
        assert outer == []
        record_atomic_memory_input_encoding(result.report)
        with collect_atomic_memory_input_encodings() as inner:
            for _ in range(150):
                record_atomic_memory_input_encoding(result.report)
        assert len(inner) == 128
        assert len(outer) == 1
        record_atomic_memory_input_encoding(result.report)
    record_atomic_memory_input_encoding(result.report)

    assert len(outer) == 2
    serialized = json.dumps(asdict(result.report))
    assert "session-42" not in serialized
    assert "turn-1" not in serialized
    assert "Independent fact" not in serialized
    assert _DETAIL not in serialized
    assert result.report.estimator_id == character_token_estimator().profile.estimator_id


def test_collectors_isolate_concurrent_operations_and_reset_after_failure() -> None:
    report = encode_atomic_memory_input(_input(), mode="shadow").report

    async def collect(count: int) -> int:
        with collect_atomic_memory_input_encodings() as reports:
            for _ in range(count):
                await asyncio.sleep(0)
                record_atomic_memory_input_encoding(report)
            return len(reports)

    async def scenario() -> None:
        assert await asyncio.gather(collect(2), collect(5)) == [2, 5]

    with collect_atomic_memory_input_encodings() as outer:
        with pytest.raises(RuntimeError), collect_atomic_memory_input_encodings() as failed:
            record_atomic_memory_input_encoding(report)
            raise RuntimeError
        asyncio.run(scenario())
        record_atomic_memory_input_encoding(report)
    assert len(failed) == len(outer) == 1
