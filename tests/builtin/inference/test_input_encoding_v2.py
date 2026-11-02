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

"""Versioned string expansion preserves nested evidence byte for byte."""

from __future__ import annotations

import json
from typing import Any, Literal

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

_JSON = TypeAdapter(JsonValue)
_LINE = "    assert database.read_exact_revision(expected_revision) == retained_evidence\n"


def _dump(value: Any) -> str:
    return _JSON.dump_json(value).decode()


def _projected() -> str:
    return _dump({
        "evidence": [
            {
                "evidence_id": f"source:content/task-{index}",
                "kind": "source",
                "content": _dump({
                    "name": f"task-{index}",
                    "content": _dump({"patch": f"diff --git a/task{index}.py b/task{index}.py\n" + _LINE * 50}),
                    "metadata": {"attempt": index, "status": "failed"},
                }),
                "truncated": False,
            }
            for index in range(3)
        ],
        "target_evidence_id": None,
    })


def _active(original: str):
    return encode_input(original, stage="experience.generate", schema=INPUT_ENCODING_SCHEMA_V2, mode="active")


def _envelope() -> dict[str, Any]:
    result = _active(_projected())
    assert result.report.reason == "applied"
    return json.loads(result.prompt)


def test_nested_source_projection_shares_lines_without_merging_identities_or_occurrences() -> None:
    original = _projected()
    old = encode_input(original, stage="experience.generate", mode="active")
    result = _active(original)

    assert old.prompt == original and old.report.reason == "no_repetition"
    assert result.report.reason == "applied"
    assert result.report.codec == "shared-json-v2"
    assert decode_input(result.prompt, schema=INPUT_ENCODING_SCHEMA_V2, stage="experience.generate") == original
    restored = json.loads(decode_input(result.prompt, schema=INPUT_ENCODING_SCHEMA_V2))
    assert [item["evidence_id"] for item in restored["evidence"]] == [f"source:content/task-{i}" for i in range(3)]
    assert [json.loads(item["content"])["metadata"]["attempt"] for item in restored["evidence"]] == [0, 1, 2]
    for index, item in enumerate(restored["evidence"]):
        patch = json.loads(json.loads(item["content"])["content"])["patch"]
        assert patch == f"diff --git a/task{index}.py b/task{index}.py\n" + _LINE * 50


def test_v2_is_explicit_and_cannot_be_decoded_as_v1_or_another_operation() -> None:
    result = _active(_projected())
    with pytest.raises(InputEncodingError, match="unsupported_envelope"):
        decode_input(result.prompt)
    with pytest.raises(InputEncodingError, match="unsupported_envelope"):
        decode_input(result.prompt, schema=INPUT_ENCODING_SCHEMA_V2, stage="skill.generate")
    old = encode_input(_dump([_LINE * 30] * 3), stage="experience.generate", mode="active")
    assert json.loads(old.prompt)["schema"] == INPUT_ENCODING_SCHEMA
    assert old.report.codec == "shared-json-v1"
    with pytest.raises(InputEncodingError, match="unsupported_envelope"):
        decode_input(old.prompt, schema=INPUT_ENCODING_SCHEMA_V2)


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_v2_disabled_and_shadow_preserve_original_without_changing_the_candidate(
    mode: Literal["off", "shadow"],
) -> None:
    original = _projected()
    result = encode_input(original, stage="experience.generate", schema=INPUT_ENCODING_SCHEMA_V2, mode=mode)
    assert result.prompt == original
    assert result.report.reason == ("disabled" if mode == "off" else "shadow_candidate")
    if mode == "shadow":
        active = _active(original)
        assert result.report.candidate_bytes == active.report.selected_bytes
        assert result.report.candidate_estimated_tokens == active.report.selected_estimated_tokens


def test_v2_full_envelope_instructions_and_fragment_tags_count_toward_savings() -> None:
    original = _projected()
    estimator = TokenEstimator(TokenEstimatorProfile(estimator_id="utf8", version="1"), lambda text: len(text.encode()))
    result = encode_input(
        original, stage="experience.generate", schema=INPUT_ENCODING_SCHEMA_V2, mode="active", estimator=estimator
    )
    assert result.report.reason == "applied"
    assert result.report.selected_estimated_tokens == len(result.prompt.encode())
    assert result.report.original_estimated_tokens == len(original.encode())


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r", "\u2028"])
def test_string_fragments_preserve_unicode_whitespace_newline_kind_and_last_partial_line(ending: str) -> None:
    line = '    校验失败, 保留 exact revision、引号 " 和反斜杠 \\。' + ending
    text = line * 70 + "  trailing partial line  "
    original = _dump({"content": _dump({"text": text}), "other": "独立证据"})
    result = _active(original)
    assert result.report.reason == "applied"
    assert decode_input(result.prompt, schema=INPUT_ENCODING_SCHEMA_V2) == original


@pytest.mark.parametrize(
    "literal",
    [
        '{ "value": 1 }',
        '{"value":1e0}',
        '{"value":"\\u4e2d"}',
        '{"a":1,"a":2}',
        '{"value":NaN}',
        '{"value":"\\ud800"}',
        '{\n"first":1,\n"second":2\n}',
    ],
)
def test_noncanonical_or_ambiguous_nested_json_remains_exact_literal(literal: str) -> None:
    original = _dump({"content": _dump({"patch": _LINE * 70, "literal": literal})})
    result = _active(original)
    assert result.report.reason == "applied"
    assert decode_input(result.prompt, schema=INPUT_ENCODING_SCHEMA_V2) == original


def test_literal_node_shapes_and_numeric_types_are_not_reinterpreted() -> None:
    values = [True, False, 1, 1.0, -0.0, 1e-7, 1e20, None, ["j", ["r", 0]], ["s", ["r", 0]]]
    original = _dump({"payload": _dump({"values": values, "patch": _LINE * 80})})
    result = _active(original)
    assert result.report.reason == "applied"
    assert decode_input(result.prompt, schema=INPUT_ENCODING_SCHEMA_V2) == original


@pytest.mark.parametrize("kind", ["nodes", "depth"])
def test_nested_parse_work_uses_one_cumulative_budget(kind: str) -> None:
    if kind == "nodes":
        original = _dump([_dump([None] * 10)] * 1900)
    else:
        nested: Any = {"patch": _LINE * 20}
        for _ in range(15):
            nested = {"nested": _dump(nested)}
        original = _dump(nested)
    result = _active(original)
    assert result.prompt == original
    assert result.report.reason in ({"node_limit"} if kind == "nodes" else {"depth_limit", "input_limit"})


@pytest.mark.parametrize(
    "node",
    [
        ["s", 1],
        ["s", [1]],
        ["s", [["j", 1]]],
        ["s", [["r", True]]],
        ["s", [["r", -1]]],
        ["s", [["r", 999]]],
        ["j", ["o", [["a", 1], ["a", 2]]]],
    ],
)
def test_invalid_string_parts_and_reconstructed_keys_are_rejected(node: Any) -> None:
    envelope = _envelope()
    envelope["value"] = node
    with pytest.raises(InputEncodingError):
        decode_input(json.dumps(envelope), schema=INPUT_ENCODING_SCHEMA_V2)


def test_v2_dictionary_never_interprets_object_entries_as_string_references() -> None:
    envelope = _envelope()
    envelope["shared"] = [["r", 0]]
    envelope["value"] = ["r", 0]
    with pytest.raises(InputEncodingError, match="invalid_shared_string"):
        decode_input(json.dumps(envelope), schema=INPUT_ENCODING_SCHEMA_V2)


@pytest.mark.parametrize("kind", ["joined_bytes", "nested_json", "nodes"])
def test_compact_v2_nodes_cannot_expand_beyond_decoded_limits(kind: str) -> None:
    envelope = _envelope()
    if kind == "joined_bytes":
        envelope["shared"] = ["x" * 2000]
        envelope["value"] = ["s", [["r", 0]] * 200]
    elif kind == "nested_json":
        envelope["shared"] = ['"' * 100]
        envelope["value"] = ["r", 0]
        for _ in range(22):
            envelope["value"] = ["j", envelope["value"]]
    else:
        envelope["shared"] = [""]
        envelope["value"] = ["s", [["r", 0]] * 20_001]
    with pytest.raises(InputEncodingError, match=r"input_limit|depth_limit|node_limit"):
        decode_input(json.dumps(envelope), schema=INPUT_ENCODING_SCHEMA_V2)


def test_custom_prompt_can_disable_v2_even_for_a_profitable_projection() -> None:
    original = _projected()
    result = encode_input(
        original, stage="experience.generate", schema=INPUT_ENCODING_SCHEMA_V2, mode="active", allowed=False
    )
    assert result.prompt == original
    assert result.report.reason == "unsupported_prompt"
