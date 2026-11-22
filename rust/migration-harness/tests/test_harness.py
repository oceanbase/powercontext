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

# ruff: noqa: S101  (pytest assertions are the point of these tests)

"""Harness self-tests: lossless codec, LF-only framing, and the comparator's failure modes.

These tests do not need the native candidate. They are the isolated self-check of the harness itself;
product replay against a built native component is a separate stage.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT))

import codec  # noqa: E402
import negative_controls  # noqa: E402
import recorder  # noqa: E402
import replay  # noqa: E402

CORPUS = HARNESS_ROOT / "fixtures" / "corpus.json"

VALUES = [
    "plain ascii",
    "A\ud83d\ude00B",
    "\U0001f600",
    "explicit \ud83d pair",
    "a\u0085b\u2028c\u2029d",
    "\u01f0 and j\u030c",
    "\u0130stanbul Stra\u00dfe",
    "",
    True,
    False,
    0,
    -1,
    2**200,
    -(2**130),
    0.0,
    -0.0,
    1.5,
    -2.25e-300,
    math.inf,
    -math.inf,
    b"",
    b"\x00\xff\x10",
    (),
    [],
    (1, "two", 3.0),
    [None, True, b"x", (1, 2)],
    None,
]


@pytest.mark.parametrize("value", VALUES, ids=lambda value: type(value).__name__)
def test_codec_round_trip_is_lossless(value: object) -> None:
    encoded = codec.encode_value(value)
    decoded = codec.decode_value(encoded)
    assert decoded == value
    assert type(decoded) is type(value)
    if isinstance(value, float):
        assert math.copysign(1.0, decoded) == math.copysign(1.0, value)
        assert math.isnan(decoded) == math.isnan(value)


def test_codec_preserves_nan_and_signed_zero() -> None:
    assert math.isnan(codec.decode_value(codec.encode_value(math.nan)))
    assert math.copysign(1.0, codec.decode_value(codec.encode_value(-0.0))) < 0


def test_codec_distinguishes_surrogate_pair_from_real_scalar() -> None:
    pair = "A\ud83d\ude00B"
    scalar = "A\U0001f600B"
    assert codec.encode_value(pair) != codec.encode_value(scalar)
    assert codec.decode_value(codec.encode_value(pair)) != codec.decode_value(codec.encode_value(scalar))
    assert codec.decode_value(codec.encode_value(pair)).encode("utf-8", "surrogatepass") != (
        codec.decode_value(codec.encode_value(scalar)).encode("utf-8")
    )


def test_codec_distinguishes_tuple_from_list_and_bool_from_int() -> None:
    assert codec.encode_value((1,))["$t"] == "tuple"
    assert codec.encode_value([1])["$t"] == "list"
    assert codec.encode_value(True) != codec.encode_value(1)


@pytest.mark.parametrize("value", [{}, {"a": 1}, object(), complex(1, 2), {1, 2}], ids=str)
def test_codec_rejects_unsupported_types(value: object) -> None:
    with pytest.raises(codec.UnsupportedType):
        codec.encode_value(value)


@pytest.mark.parametrize(
    "document",
    [
        {"$t": "unknown"},
        {"$v": 1},
        {"$t": "int", "$v": 1},
        {"$t": "str", "$v": ["a"]},
        {"$t": "bytes", "$v": "zz"},
        {"$t": "tuple", "$v": 1},
        {"$t": "none", "$extra": 1},
    ],
)
def test_codec_rejects_malformed_documents(document: dict[str, object]) -> None:
    with pytest.raises(codec.MalformedRecord):
        codec.decode_value(document)


def test_records_split_on_lf_only(tmp_path: Path) -> None:
    records = [
        {"schema": codec.SCHEMA_ID, "id": "one", "text": codec.encode_value("a\u0085b\u2028c\u2029d")},
        {"schema": codec.SCHEMA_ID, "id": "two", "text": codec.encode_value("plain")},
    ]
    path = tmp_path / "records.ndjson"
    codec.write_records(path, records)
    raw = path.read_text(encoding="utf-8")
    assert len(raw.split("\n")) == 3  # two records plus the trailing empty segment
    loaded = codec.read_records(path)
    assert [record["id"] for record in loaded] == ["one", "two"]
    assert loaded[0]["text"]["$v"] == [97, 133, 98, 8232, 99, 8233, 100]
    # The separators live inside a codepoint array, so no reader can split on them; assert the raw
    # bytes contain no literal U+2028/U+0085 that a `splitlines()`-style reader would cut on.
    assert "\u2028" not in raw and "\u0085" not in raw
    assert len(json.loads(raw.split("\n")[0])["text"]["$v"]) == 7


def test_record_requires_schema_and_has_no_newlines(tmp_path: Path) -> None:
    with pytest.raises(codec.MalformedRecord):
        codec.dump_record({"schema": "other", "id": "x"})
    line = codec.dump_record({"schema": codec.SCHEMA_ID, "id": "x", "text": codec.encode_value("a\u2028b")})
    assert "\n" not in line
    assert codec.parse_record(line)["id"] == "x"


def test_corpus_is_fixed_and_non_empty() -> None:
    cases = recorder.load_corpus(CORPUS)
    assert len(cases) == 20
    identifiers = [case["id"] for case in cases]
    assert len(set(identifiers)) == len(identifiers)
    assert all(isinstance(case["input"], str) for case in cases)
    # Two cases deliberately share input text but not id: case identity is not input identity.
    assert cases[0]["input"] == cases[19]["input"]
    assert cases[0]["id"] != cases[19]["id"]


def _record_corpus(tmp_path: Path, selectors: list[str]) -> tuple[Path, Path]:
    # The default selector set is registered from the imported reference package; module-level
    # registration is not done at import time because recording must happen explicitly.
    recorder._register_default_selectors()
    manifest = recorder.record(CORPUS, selectors)
    records = manifest.pop("_records")
    for row in records:
        row["reference"] = {"source_sha256": manifest["source"]["source_sha256"]}
    records_path = tmp_path / "records.ndjson"
    codec.write_records(records_path, records)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return records_path, manifest_path


def test_replay_matches_the_reference_and_reports_identity(tmp_path: Path) -> None:
    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text"])
    result = replay.replay(records_path, manifest_path, ["analyze_text"])
    assert result["status"] == "PASS"
    assert result["summary"]["mismatched"] == 0
    assert result["summary"]["compared"] == 20
    assert result["candidate"]["module"] == "powercontext.builtin.artifacts.search"
    assert result["candidate"]["native"] is False
    assert result["candidate"]["suffix"] == ".py"


def test_replay_refuses_empty_selection(tmp_path: Path) -> None:
    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text"])
    with pytest.raises(ValueError):
        replay.replay(records_path, manifest_path, [])


def test_replay_refuses_unknown_selector(tmp_path: Path) -> None:
    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text"])
    with pytest.raises(ValueError):
        replay.replay(records_path, manifest_path, ["analyze_text_native_missing"])


def test_replay_refuses_a_limit_below_the_selection(tmp_path: Path) -> None:
    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text", "analyze_text_with_spans"])
    with pytest.raises(ValueError):
        replay.replay(records_path, manifest_path, ["analyze_text", "analyze_text_with_spans"], limit=1)


def test_negative_controls_fail_for_the_right_reason(tmp_path: Path) -> None:
    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text"])
    records = codec.read_records(records_path)

    directory = tmp_path / "controls"
    directory.mkdir()
    plan = negative_controls.control_wrong_value(records, directory)
    wrong_value = negative_controls.run_replay(plan["records"], manifest_path, ["analyze_text"])
    assert wrong_value["status"] == "FAIL"
    assert any(path.startswith(plan["expected_path"]) for path in wrong_value["summary"]["mismatch_paths"])

    plan = negative_controls.control_wrong_order(records, directory)
    wrong_order = negative_controls.run_replay(plan["records"], manifest_path, ["analyze_text"])
    assert wrong_order["status"] == "FAIL"
    assert any(path.startswith("cases[0].observations") for path in wrong_order["summary"]["mismatch_paths"])

    plan = negative_controls.control_wrong_type(records, directory)
    wrong_type = negative_controls.run_replay(plan["records"], manifest_path, ["analyze_text"])
    assert wrong_type["status"] == "FAIL"
    assert any(path.startswith(plan["expected_path"]) for path in wrong_type["summary"]["mismatch_paths"])

    missing = negative_controls.run_replay(records_path, manifest_path, ["analyze_text_native_missing"])
    assert missing["status"] == "ERROR"
    assert "unknown selectors" in missing["error"]


def test_control_wrong_order_detects_mispaired_records(tmp_path: Path) -> None:
    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text"])
    records = codec.read_records(records_path)
    directory = tmp_path / "order"
    directory.mkdir()
    plan = negative_controls.control_wrong_order(records, directory)
    result = negative_controls.run_replay(plan["records"], manifest_path, ["analyze_text"])
    assert result["status"] == "FAIL"
    assert any(path.startswith("cases[0].observations") for path in result["summary"]["mismatch_paths"])


def test_replay_is_order_insensitive_when_records_are_self_describing(tmp_path: Path) -> None:
    """Reordering whole records must not change the verdict: pairing is by record content, not index."""

    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text"])
    records = codec.read_records(records_path)
    shuffled = list(reversed(records))
    shuffled_path = tmp_path / "shuffled.ndjson"
    codec.write_records(shuffled_path, shuffled)
    assert replay.replay(shuffled_path, manifest_path, ["analyze_text"])["status"] == "PASS"


def test_controls_do_not_modify_delivered_files(tmp_path: Path) -> None:
    records_path, manifest_path = _record_corpus(tmp_path, ["analyze_text"])
    before = negative_controls.copy_identity([records_path, manifest_path])
    destination = tmp_path / "negative.json"
    argv = [
        "negative_controls.py",
        "--records",
        str(records_path),
        "--manifest",
        str(manifest_path),
        "--output",
        str(destination),
        "--temp-root",
        str(tmp_path / "tmp"),
    ]
    previous = sys.argv
    sys.argv = argv
    try:
        negative_controls.main()
    finally:
        sys.argv = previous
    after = negative_controls.copy_identity([records_path, manifest_path])
    assert before == after
    result = json.loads(destination.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert result["delivered_unchanged"] is True
    assert result["controls"]["missing_native"]["fail_closed"] is True
