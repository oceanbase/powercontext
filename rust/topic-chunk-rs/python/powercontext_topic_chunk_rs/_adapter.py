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

"""Lossless transport and original model construction; no chunking policy."""

from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryChunk

from . import _native


def chunk_topic_memory_detail(detail: str, /) -> tuple[TopicMemoryChunk, ...]:
    # Preserve the public reference's len() error for its explicitly tested invalid inputs.
    size = len(detail)
    if type(detail) is not str:
        message = "The experiment supports builtin str inputs"
        raise TypeError(message)
    ranges = _native.chunk_ranges(str.encode(detail, "utf-32-le", "surrogatepass"))
    previous_start = previous_end = -1
    for start, end in ranges:
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= size
            or start < previous_start
            or end < previous_end
        ):
            message = "Native returned invalid or decreasing codepoint ranges"
            raise ValueError(message)
        previous_start, previous_end = start, end
    return tuple(
        TopicMemoryChunk(ordinal=ordinal, start_offset=start, end_offset=end, text=detail[start:end])
        for ordinal, (start, end) in enumerate(ranges)
    )
