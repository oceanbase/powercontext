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

"""Regression checks for the lossless official-tool boundary."""

import json

import pytest

from .. import _harness, _tool_protocol


@pytest.mark.parametrize("text", ["", "abc", "中文😀", "\ud800", "\udfff", "\ud83d\ude42"])
def test_utf32_requests_preserve_python_codepoints(text):
    request = _tool_protocol.canonical({
        "schema": 1,
        "id": "roundtrip",
        "value": {"type": "str32", "value": text.encode("utf-32-le", "surrogatepass").hex()},
    })
    assert request.isascii()
    identity, case = _tool_protocol.decode_request(request)
    assert identity == "roundtrip"
    assert type(case["invalid"]) is str
    assert list(map(ord, case["invalid"])) == list(map(ord, text))


@pytest.mark.parametrize("value", [None, False, 0])
def test_invalid_public_arguments_retain_their_types(value):
    request = _tool_protocol.canonical({"schema": 1, "id": "invalid", "value": _harness.encode(value)})
    _, case = _tool_protocol.decode_request(request)
    assert type(case["invalid"]) is type(value)
    assert case["invalid"] == value


def test_missing_argument_is_distinct_from_none():
    _, missing = _tool_protocol.decode_request('{"schema":1,"id":"missing","missing":true}')
    _, null = _tool_protocol.decode_request('{"schema":1,"id":"null","value":{"type":"NoneType","value":null}}')
    assert missing == {"missing": True}
    assert null == {"invalid": None}


def test_malformed_protocol_is_rejected_before_business_invocation():
    with pytest.raises(ValueError) as error:
        _tool_protocol.decode_request("{}")
    assert type(error.value) is ValueError
    assert str(error.value) == "Invalid tool request"


def test_capture_preserves_errors_as_exact_return_data(monkeypatch):
    monkeypatch.setattr(_tool_protocol, "context", lambda: ({}, {"outcome": _harness.outcome}))
    request = '{"schema":1,"id":"error","value":{"type":"str","value":"x"}}'

    def value_error(detail):
        message = "original error"
        raise ValueError(message)

    def type_error(detail):
        message = "original error"
        raise TypeError(message)

    def wrong_message(detail):
        message = "different error"
        raise ValueError(message)

    original = _tool_protocol.capture(value_error, request)
    assert original != _tool_protocol.capture(type_error, request)
    assert original != _tool_protocol.capture(wrong_message, request)
    decoded = json.loads(original)
    assert decoded["exception"]["class"] == "builtins.ValueError"
    assert decoded["exception"]["args"]["type"] == "tuple"


def test_codec_failure_is_not_encoded_as_business_error(monkeypatch):
    def broken_outcome(function, case):
        message = "codec failed"
        raise RuntimeError(message)

    monkeypatch.setattr(_tool_protocol, "context", lambda: ({}, {"outcome": broken_outcome}))
    with pytest.raises(RuntimeError, match="codec failed"):
        _tool_protocol.capture(str, '{"schema":1,"id":"x","missing":true}')
