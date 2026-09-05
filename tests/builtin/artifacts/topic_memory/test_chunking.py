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

from itertools import pairwise

from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_CHUNK_MAX_CHARACTERS,
    TOPIC_MEMORY_CHUNK_MAX_COUNT,
    TOPIC_MEMORY_CHUNK_OVERLAP_CHARACTERS,
    TopicMemoryContent,
    chunk_topic_memory_detail,
    prepare_topic_memory_projection,
)


def test_markdown_chunks_preserve_exact_detail_offsets_and_policy() -> None:
    detail = "\n\n".join((
        "# Lease recovery\n" + "The leader renews before its deadline. " * 24,
        "## Fencing\n" + "- stale workers cannot publish\n" * 24,
        "## Shutdown\n" + "A child is killed and reaped before close returns. " * 24,
    ))

    chunks = chunk_topic_memory_detail(detail)

    assert len(chunks) >= 2
    assert tuple(chunk.ordinal for chunk in chunks) == tuple(range(len(chunks)))
    assert all(chunk.policy_version == "markdown-v1" for chunk in chunks)
    assert all(chunk.text == detail[chunk.start_offset : chunk.end_offset] for chunk in chunks)
    assert all(len(chunk.text) <= TOPIC_MEMORY_CHUNK_MAX_CHARACTERS for chunk in chunks)
    assert all(previous.end_offset <= current.start_offset for previous, current in pairwise(chunks))
    assert "# Lease recovery" in chunks[0].text
    assert "## Shutdown" in chunks[-1].text


def test_overlap_is_used_only_to_split_one_oversized_semantic_atom() -> None:
    detail = "x" * (TOPIC_MEMORY_CHUNK_MAX_CHARACTERS * 2)

    chunks = chunk_topic_memory_detail(detail)

    assert len(chunks) == 3
    assert all(len(chunk.text) <= TOPIC_MEMORY_CHUNK_MAX_CHARACTERS for chunk in chunks)
    for previous, current in pairwise(chunks):
        assert previous.end_offset - current.start_offset == TOPIC_MEMORY_CHUNK_OVERLAP_CHARACTERS


def test_projection_indexes_title_summary_and_every_detail_chunk() -> None:
    content = TopicMemoryContent(
        title="Lease Recovery",
        summary="Stale workers are fenced.",
        detail="# Recovery\n\nThe next leader resumes durable work.",
    )

    projection = prepare_topic_memory_projection(content)

    assert projection.content == content
    assert projection.topic_searchable_text == "lease recovery stale workers are fenced"
    assert "".join(chunk.text for chunk in projection.chunks) == content.detail


def test_short_tail_is_merged_into_the_preceding_chunk() -> None:
    detail = f"{'a' * 1_300}\n\n{'tail ' * 20}"

    chunks = chunk_topic_memory_detail(detail)

    assert len(chunks) == 1
    assert chunks[0].text == detail.rstrip()


def test_adversarial_markdown_falls_back_to_the_hard_chunk_count_bound() -> None:
    detail = (("x\n\n" + "y" * 1_801 + "\n\n") * 69).rstrip()

    chunks = chunk_topic_memory_detail(detail)

    assert len(detail) == 124_612
    assert len(chunks) <= TOPIC_MEMORY_CHUNK_MAX_COUNT
    assert all(chunk.text == detail[chunk.start_offset : chunk.end_offset] for chunk in chunks)
    assert all(len(chunk.text) <= TOPIC_MEMORY_CHUNK_MAX_CHARACTERS for chunk in chunks)
    assert chunks[0].start_offset == 0
    assert chunks[-1].end_offset == len(detail)
