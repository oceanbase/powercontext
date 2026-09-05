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

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryChannelHit,
    TopicMemorySearchChannels,
    fuse_topic_memory_rankings,
)


@pytest.mark.parametrize("match_offset", [20, 720, 1_500])
def test_fts_snippet_centers_its_window_on_analyzer_match(match_offset: int) -> None:
    detail = f"{'x' * match_offset} late-needle {'y' * (1_800 - match_offset)}"
    hit = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="Late evidence",
        summary="The relevant evidence can occur anywhere in a chunk.",
        channel="detail_fts",
        chunk_ordinal=0,
        chunk_start=0,
        chunk_text=detail,
    )

    result = fuse_topic_memory_rankings(
        "late-needle",
        TopicMemorySearchChannels(detail_fts=(hit,)),
        1,
    )

    assert result[0].snippet is not None
    assert "late-needle" in result[0].snippet
    assert len(result[0].snippet) <= 480


def test_vector_snippet_uses_a_stable_chunk_local_window() -> None:
    detail = f"{'prefix ' * 150}stable-center{' suffix' * 150}"
    hit = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="Semantic evidence",
        summary="Vector matches have no lexical position.",
        channel="detail_vector",
        chunk_ordinal=0,
        chunk_start=0,
        chunk_text=detail,
        distance=0.1,
    )

    result = fuse_topic_memory_rankings(
        "!!!",
        TopicMemorySearchChannels(detail_vector=(hit,)),
        1,
    )

    assert result[0].snippet is not None
    assert "stable-center" in result[0].snippet
    assert len(result[0].snippet) <= 480


def test_fts_snippet_prefers_the_window_with_the_most_query_terms() -> None:
    detail = f"common {'filler ' * 180}common unique"
    hit = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="Clustered evidence",
        summary="The common term also occurs away from the best match.",
        channel="detail_fts",
        chunk_ordinal=0,
        chunk_start=0,
        chunk_text=detail,
    )

    result = fuse_topic_memory_rankings(
        "common unique",
        TopicMemorySearchChannels(detail_fts=(hit,)),
        1,
    )

    assert result[0].snippet is not None
    assert "common unique" in result[0].snippet


def test_fts_snippet_maps_casefold_expansion_back_to_source_offsets() -> None:
    detail = f"{'ß' * 1_000} needle {'y' * 1_000}"
    hit = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="Unicode evidence",
        summary="Analyzer coordinates can expand relative to the source text.",
        channel="detail_fts",
        chunk_ordinal=0,
        chunk_start=0,
        chunk_text=detail,
    )

    result = fuse_topic_memory_rankings(
        "needle",
        TopicMemorySearchChannels(detail_fts=(hit,)),
        1,
    )

    assert result[0].snippet is not None
    assert "needle" in result[0].snippet
    assert len(result[0].snippet) <= 480


def test_fts_snippet_uses_analyzer_token_boundaries() -> None:
    detail = f"party {'filler ' * 160}needle"
    hit = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="Boundary evidence",
        summary="Substring matches must not outrank Analyzer tokens.",
        channel="detail_fts",
        chunk_ordinal=0,
        chunk_start=0,
        chunk_text=detail,
    )

    result = fuse_topic_memory_rankings(
        "art needle",
        TopicMemorySearchChannels(detail_fts=(hit,)),
        1,
    )

    assert result[0].snippet is not None
    assert "needle" in result[0].snippet
    assert "party" not in result[0].snippet


def test_rrf_scores_are_normalized_against_the_enabled_first_place_channels() -> None:
    base = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="Normalized score",
        summary="needle",
        channel="topic_fts",
    )
    detail = base.model_copy(
        update={
            "channel": "detail_fts",
            "chunk_ordinal": 0,
            "chunk_start": 0,
            "chunk_text": "needle",
        }
    )

    single = fuse_topic_memory_rankings(
        "needle",
        TopicMemorySearchChannels(topic_fts=(base,)),
        1,
    )
    fts_only = fuse_topic_memory_rankings(
        "needle",
        TopicMemorySearchChannels(topic_fts=(base,), detail_fts=(detail,)),
        1,
        mode="fts",
    )
    all_channels = fuse_topic_memory_rankings(
        "needle",
        TopicMemorySearchChannels(
            topic_fts=(base,),
            topic_vector=(base.model_copy(update={"channel": "topic_vector", "distance": 0.1}),),
            detail_fts=(detail,),
            detail_vector=(detail.model_copy(update={"channel": "detail_vector", "distance": 0.1}),),
        ),
        1,
    )

    assert single[0].score == pytest.approx(25.0)
    assert fts_only[0].score == pytest.approx(100.0)
    assert all_channels[0].score == pytest.approx(100.0)
