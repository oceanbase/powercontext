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

import json
from typing import Any

import pytest
from pydantic import JsonValue, TypeAdapter

from powercontext.builtin.inference.input_encoding import (
    INPUT_ENCODING_SCHEMA,
    INPUT_ENCODING_SCHEMA_V2,
    InputEncodingError,
    decode_input,
    encode_input,
)
from powercontext.builtin.inference.tokens import TokenEstimator, TokenEstimatorProfile

_DETAIL = "Preserve each original event, evidence identity, date, condition, and repeated occurrence. " * 8
_JSON = TypeAdapter(JsonValue)


def _original() -> str:
    return _JSON.dump_json({
        "sources": [
            {
                "source_id": f"source-{index}",
                "revision": index + 1,
                "recorded_at": f"2026-10-{index + 1:02d}",
                "content": _DETAIL,
                "metadata": {"same_text": _DETAIL, "author": "user"},
            }
            for index in range(12)
        ],
        "instruction": "Explain the observed experience with references.",
    }).decode()


def _envelope() -> dict[str, Any]:
    result = encode_input(_original(), stage="experience.generate", mode="active")
    assert result.report.reason == "applied"
    return json.loads(result.prompt)


@pytest.mark.parametrize("stage", ["experience.generate", "skill.generate", "custom.operation"])
def test_plain_json_input_preserves_order_identity_revision_and_every_occurrence(stage: str) -> None:
    original = _original()
    result = encode_input(original, stage=stage, mode="active")

    assert result.report.reason == "applied"
    assert result.report.stage == stage
    assert json.loads(result.prompt)["schema"] == INPUT_ENCODING_SCHEMA
    assert result.report.selected_bytes < result.report.original_bytes
    assert decode_input(result.prompt, stage=stage) == original
    restored = json.loads(decode_input(result.prompt))
    assert [item["source_id"] for item in restored["sources"]] == [f"source-{i}" for i in range(12)]
    assert [item["revision"] for item in restored["sources"]] == list(range(1, 13))
    assert all(item["content"] == _DETAIL for item in restored["sources"])


def test_explicit_schema_and_stage_cannot_be_confused_between_operations() -> None:
    result = encode_input(_original(), stage="experience.generate", schema="custom.input.v1", mode="active")

    assert decode_input(result.prompt, schema="custom.input.v1", stage="experience.generate") == _original()
    for options in ({}, {"schema": "custom.input.v1", "stage": "skill.generate"}):
        with pytest.raises(InputEncodingError, match="unsupported_envelope"):
            decode_input(result.prompt, **options)


def test_default_off_and_shadow_never_change_the_input() -> None:
    original = _original()
    off = encode_input(original, stage="experience.generate")
    shadow = encode_input(original, stage="experience.generate", mode="shadow")
    active = encode_input(original, stage="experience.generate", mode="active")

    assert off.prompt == shadow.prompt == original
    assert off.report.reason == "disabled"
    assert shadow.report.reason == "shadow_candidate"
    assert shadow.report.candidate_bytes == active.report.selected_bytes
    assert shadow.report.selected_estimated_tokens == shadow.report.original_estimated_tokens


def test_one_estimator_counts_full_envelope_including_all_decoding_instructions() -> None:
    estimator = TokenEstimator(TokenEstimatorProfile(estimator_id="test:utf8", version="1"), lambda s: len(s.encode()))
    result = encode_input(_original(), stage="experience.generate", mode="active", estimator=estimator)

    assert result.report.reason == "applied"
    assert result.report.original_estimated_tokens == len(_original().encode())
    assert result.report.selected_estimated_tokens == len(result.prompt.encode())
    assert json.loads(result.prompt)["encoding"]


@pytest.mark.parametrize("candidate_tokens", [970, 960, 950])
def test_absolute_and_percentage_thresholds_remain_required(candidate_tokens: int) -> None:
    estimator = TokenEstimator(
        TokenEstimatorProfile(estimator_id="test:threshold", version="1"),
        lambda s: candidate_tokens if s.startswith('{"schema":') else 1000,
    )
    result = encode_input(_original(), stage="experience.generate", mode="active", estimator=estimator)

    assert (result.report.reason == "applied") is (candidate_tokens == 950)
    assert result.report.selected_estimated_tokens == (950 if candidate_tokens == 950 else 1000)


def test_scalar_types_order_and_literal_reference_shaped_data_round_trip_exactly() -> None:
    original = _JSON.dump_json({
        "z": [_DETAIL] * 8,
        "a": [True, False, 1, 1.0, -0.0, 1e-7, 1e20, None, "", "中文\n\r\t\u2028\u0000"],
        "literal_nodes": [["r", 0], ["o", [["name", ["r", 1]]]], ["a", []]],
        "shared": {"0": "literal"},
    }).decode()
    result = encode_input(original, stage="experience.generate", mode="active")

    assert result.report.reason == "applied"
    assert decode_input(result.prompt) == original
    assert list(json.loads(decode_input(result.prompt))) == ["z", "a", "literal_nodes", "shared"]


@pytest.mark.parametrize("schema", [INPUT_ENCODING_SCHEMA, INPUT_ENCODING_SCHEMA_V2])
def test_literal_placeholders_keep_record_attribution_and_string_types(schema: str) -> None:
    """Verify codec preservation only; this does not exercise a model's interpretation."""
    payload = {
        "owner": "<PERSON_1>",
        "credential": "${API_KEY}",
        "literal_reference": ["r", 0],
        "note": _DETAIL,
    }
    serialized = _JSON.dump_json(payload).decode()
    records = [
        {
            "source_id": f"source:{tenant}",
            "entity_id": tenant,
            "path": f"/workspace/{tenant}/config.json",
            "ip": ip,
            "intent": intent,
            "object": payload,
            "json_string": serialized,
        }
        for tenant, ip, intent in (
            ("tenant-a", "192.0.2.10", "Read the configuration without modifying it."),
            ("tenant-b", "192.0.2.20", "Explain deletion risks without executing a command."),
        )
    ]
    original = _JSON.dump_json({"records": records}).decode()
    encoded = encode_input(original, stage="experience.generate", schema=schema, mode="active")

    assert encoded.report.reason == "applied"
    restored = decode_input(encoded.prompt, schema=schema, stage="experience.generate")
    assert restored == original
    for actual, expected in zip(json.loads(restored)["records"], records, strict=True):
        assert {key: actual[key] for key in ("source_id", "entity_id", "path", "ip", "intent")} == {
            key: expected[key] for key in ("source_id", "entity_id", "path", "ip", "intent")
        }
        assert actual["object"] == payload
        assert isinstance(actual["json_string"], str)
        assert actual["json_string"] == serialized


def test_repeated_array_root_does_not_require_an_artifact_dto_shape() -> None:
    original = _JSON.dump_json([[_DETAIL, ["r", 0]]] * 10).decode()
    result = encode_input(original, stage="custom.array", mode="active")

    assert result.report.reason == "applied"
    assert decode_input(result.prompt) == original
    assert len(json.loads(decode_input(result.prompt))) == 10


@pytest.mark.parametrize("change", ["whitespace", "number", "escaped_unicode"])
def test_noncanonical_json_falls_back_instead_of_rewriting_source_bytes(change: str) -> None:
    original = _original()
    if change == "whitespace":
        original = " \n" + original
    elif change == "number":
        original = original.replace('"revision":1,', '"revision":1e0,', 1)
    else:
        original = original.replace("source-0", "\\u0073ource-0", 1)
    result = encode_input(original, stage="experience.generate", mode="active")

    assert result.prompt == original
    assert result.report.reason == "roundtrip_failed"


@pytest.mark.parametrize(
    ("original", "reason"),
    [
        ("", "invalid_json"),
        ("[", "invalid_json"),
        ('{"x":1,"x":2}', "duplicate_key"),
        ('{"x":NaN}', "invalid_number"),
        ('{"x":Infinity}', "invalid_number"),
        ('{"x":1e999}', "invalid_number"),
        ('"\\ud800"', "invalid_json"),
        ('"\ud800"', "invalid_json"),
    ],
)
def test_invalid_json_falls_back_without_exposing_content_in_reason(original: str, reason: str) -> None:
    result = encode_input(original, stage="experience.generate", mode="active")

    assert result.prompt == original
    assert result.report.reason == reason
    assert result.report.candidate_bytes is None


@pytest.mark.parametrize("case", ["bytes", "depth", "nodes"])
def test_source_bounds_apply_to_generic_input(case: str) -> None:
    if case == "bytes":
        original = json.dumps("x" * 270_000)
    elif case == "depth":
        original = "[" * 30 + "0" + "]" * 30
    else:
        original = json.dumps([None] * 20_001)
    result = encode_input(original, stage="experience.generate", mode="active")

    assert result.prompt == original
    assert result.report.reason == {"bytes": "input_limit", "depth": "depth_limit", "nodes": "node_limit"}[case]
    assert result.report.candidate_bytes is None


@pytest.mark.parametrize("original", ['{"question":"A short unique question"}', "[]", "null", "17"])
def test_unique_and_tiny_inputs_never_pay_for_a_sharing_envelope(original: str) -> None:
    result = encode_input(original, stage="experience.generate", mode="active")

    assert result.prompt == original
    assert result.report.reason == "no_repetition"


@pytest.mark.parametrize("case", ["bytes", "depth", "nodes", "dictionary"])
def test_expanded_output_is_bounded_independently_of_compact_wire_input(case: str) -> None:
    envelope = _envelope()
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
        InputEncodingError,
        match={"bytes": "input_limit", "depth": "depth_limit", "nodes": "node_limit", "dictionary": "dictionary_limit"}[
            case
        ],
    ):
        decode_input(json.dumps(envelope))


@pytest.mark.parametrize("node", [["r", True], ["r", -1], ["r", 999], ["r", 0.0], ["o", [["x", 1], ["x", 2]]]])
def test_invalid_references_and_duplicate_reconstructed_keys_are_rejected(node: Any) -> None:
    envelope = _envelope()
    envelope["value"] = node

    with pytest.raises(InputEncodingError):
        decode_input(json.dumps(envelope))
