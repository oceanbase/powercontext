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

import pytest
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent
from pydantic import ValidationError

from .. import _native
from .._adapter import chunk_topic_memory_detail


@pytest.mark.parametrize("ranges", [[(-1, 1)], [(0, 5)], [(1, 1)], [(True, 2)], [(1, 3), (0, 4)], [(0, 4), (1, 3)]])
def test_invalid_native_ranges_fail_before_model_acceptance(
    monkeypatch: pytest.MonkeyPatch, ranges: list[tuple[int, int]]
) -> None:
    monkeypatch.setattr(_native, "chunk_ranges", lambda _: ranges)
    with pytest.raises(ValueError, match="invalid or decreasing"):
        chunk_topic_memory_detail("abcd")


def test_equal_ranges_and_overlap_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_native, "chunk_ranges", lambda _: [(0, 2), (0, 2), (1, 3)])
    chunks = chunk_topic_memory_detail("abcd")
    assert [(chunk.start_offset, chunk.end_offset, chunk.text) for chunk in chunks] == [
        (0, 2, "ab"),
        (0, 2, "ab"),
        (1, 3, "bc"),
    ]


def test_blank_forced_window_still_raises_original_model_error() -> None:
    with pytest.raises(ValidationError, match="Topic Memory chunks must not be blank"):
        chunk_topic_memory_detail("x" + " " * 3598 + "y")


def test_surrogate_positions_survive_until_original_model_validation() -> None:
    assert _native.chunk_ranges(str.encode("a\ud800b", "utf-32-le", "surrogatepass")) == [(0, 3)]
    with pytest.raises(ValidationError, match="string_unicode"):
        chunk_topic_memory_detail("a\ud800b")


def test_production_model_normalizes_str_subclass() -> None:
    class Text(str):
        pass

    content = TopicMemoryContent(title="Title", summary="Summary", detail=Text("abc"))
    assert type(content.detail) is str
    assert chunk_topic_memory_detail(content.detail)[0].text == "abc"


@pytest.mark.parametrize("payload", [b"x", b"\x00\x00\x11\x00"])
def test_malformed_utf32_is_a_handled_error(payload: bytes) -> None:
    with pytest.raises(ValueError):
        _native.chunk_ranges(payload)
