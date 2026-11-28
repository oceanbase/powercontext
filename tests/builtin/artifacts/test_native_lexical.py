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


"""The normal search entry point executes the installed native analyzer."""

import importlib.machinery
import inspect

import pytest

from powercontext import _lexical
from powercontext.builtin.artifacts.search import analyze_text, analyze_text_with_spans


def test_default_analyzer_is_native():
    assert inspect.isbuiltin(analyze_text)
    assert analyze_text is _lexical.analyze_text
    assert any(_lexical.__file__.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ABC ab ABC",
        "pcu pch _",
        "don't can’t",  # noqa: RUF001 - intentional quote boundary input.
        "ǰ j\u030c İ ı ß ẞ Σ ς",  # noqa: RUF001 - distinct casefold inputs.
        "中文文本 数据库查询 南京市长江大桥",
        "\U00020000\U0002fa1f",
        "a\ud800b",
        "中文\udfff文本",
        "\ud800\udc00",
        "a\ud800\u0301b",
        "a\U0001e4ec\u0300",
        "a\U0001e5ee\u0300",
        "\u1c89\u1c8a",
        "word\u200cword\u200dword a\u203fb ² Ⅳ",
        "x\x00y\r\nz",
        "é" * 20,
        "é" * 21,
        "a" * 80,
        "a" * 81,
    ],
)
def test_native_projection_matches_python_span_contract(text):
    expected = " ".join(term for term, _start, _end in analyze_text_with_spans(text))
    assert analyze_text(text) == expected


@pytest.mark.parametrize("value", [None, True, 1, 1.5, [], {}, b"x"])
def test_native_analyzer_rejects_non_strings(value):
    with pytest.raises(TypeError):
        analyze_text(value)


def test_native_analyzer_accepts_str_subclasses():
    class Text(str):
        pass

    assert analyze_text(Text("ABC ab ABC")) == "abc pcu6162 abc"


@pytest.mark.parametrize("boundary", ["㐀", "\u4dbf", "\u9fd6", "\u9fff", "\uf900", "\U00020000", "\U0002fa1f"])
def test_native_preserves_jieba_scoring_boundaries(boundary):
    text = f"丁丁丁{boundary}丁丁丁"
    expected = " ".join(term for term, _start, _end in analyze_text_with_spans(text))
    assert analyze_text(text) == expected
