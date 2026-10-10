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
from powercontext.artifacts.fusion import RrfParameters
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryChannelHit,
    TopicMemoryMatchedBy,
    TopicMemorySearchChannels,
    fuse_topic_memory_rankings,
)
from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings


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


def test_topic_vector_relevance_survives_fusion_with_fts() -> None:
    fts = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="alpha beta",
        summary="alpha beta",
        channel="topic_fts",
    )
    vector = fts.model_copy(update={"channel": "topic_vector", "distance": 1.0})
    hit = fuse_topic_memory_rankings(
        "alpha beta", TopicMemorySearchChannels(topic_fts=(fts,), topic_vector=(vector,)), 1
    )[0]
    assert hit.relevance == pytest.approx(0.5)


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


@pytest.mark.parametrize(
    ("fts_weight", "vector_weight", "expected_order"),
    [
        (3.0, 1.0, ("lexical", "semantic")),
        (1.0, 3.0, ("semantic", "lexical")),
    ],
)
def test_hybrid_weights_prefer_the_higher_weight_channel_without_saturating_scores(
    fts_weight: float,
    vector_weight: float,
    expected_order: tuple[str, str],
) -> None:
    lexical = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="lexical", revision=1),
        title="Lexical topic",
        summary="needle",
        channel="topic_fts",
    )
    semantic = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="semantic", revision=1),
        title="Semantic topic",
        summary="needle",
        channel="topic_vector",
        distance=0.1,
    )

    result = _fuse_topic_memory_rankings(
        "needle",
        TopicMemorySearchChannels(topic_fts=(lexical,), topic_vector=(semantic,)),
        2,
        fusion=RrfParameters(
            weights={
                "topic_fts": fts_weight,
                "detail_fts": fts_weight,
                "topic_vector": vector_weight,
                "detail_vector": vector_weight,
            }
        ),
    ).hits

    assert tuple(hit.artifact_ref.artifact_id for hit in result) == expected_order
    assert result[0].score == pytest.approx(37.5)
    assert result[1].score == pytest.approx(12.5)
    assert all(0.0 < hit.score < 100.0 for hit in result)


def test_zero_weight_drops_topic_matches_from_only_that_channel() -> None:
    lexical = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="lexical", revision=1),
        title="Lexical topic",
        summary="needle",
        channel="topic_fts",
    )
    semantic = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="semantic", revision=1),
        title="Semantic topic",
        summary="needle",
        channel="topic_vector",
        distance=0.1,
    )

    result = _fuse_topic_memory_rankings(
        "needle",
        TopicMemorySearchChannels(topic_fts=(lexical,), topic_vector=(semantic,)),
        2,
        fusion=RrfParameters(
            weights={
                "topic_fts": 0.0,
                "detail_fts": 0.0,
                "topic_vector": 1.0,
                "detail_vector": 1.0,
            }
        ),
    ).hits

    assert tuple(hit.artifact_ref.artifact_id for hit in result) == ("semantic",)
    assert result[0].matched_by == ("topic_vector",)


def test_fusion_outcome_counts_retrieved_and_admitted_over_enabled_channels() -> None:
    def hit(
        channel: TopicMemoryMatchedBy,
        *,
        text: str,
        distance: float | None = None,
    ) -> TopicMemoryChannelHit:
        return TopicMemoryChannelHit(
            artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
            title="Needle topic",
            summary="Summary carrying the needle.",
            channel=channel,
            chunk_ordinal=0,
            chunk_start=0,
            chunk_text=text,
            distance=distance,
        )

    channels = TopicMemorySearchChannels(
        topic_fts=(hit("topic_fts", text="needle"), hit("topic_fts", text="unrelated nothing here")),
        topic_vector=(hit("topic_vector", text="needle", distance=0.1),),
        detail_fts=(hit("detail_fts", text="needle"),),
        detail_vector=(hit("detail_vector", text="needle", distance=0.1),),
    )
    outcome = _fuse_topic_memory_rankings("needle", channels, 4)
    assert outcome.hits
    assert outcome.admitted <= outcome.retrieved
    # The default admission floor keeps the unrelated lexical candidate out.
    assert outcome.admitted < outcome.retrieved

    fts_only = _fuse_topic_memory_rankings("needle", channels, 4, mode="fts")
    assert fts_only.retrieved == len(channels.topic_fts) + len(channels.detail_fts)
    assert fts_only.admitted <= fts_only.retrieved


def test_public_fusion_helper_preserves_the_historical_tuple_contract() -> None:
    hit = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic-1", revision=1),
        title="Needle topic",
        summary="needle",
        channel="topic_fts",
    )

    result = fuse_topic_memory_rankings("needle", TopicMemorySearchChannels(topic_fts=(hit,)), 1)

    assert isinstance(result, tuple)
    assert result[0].artifact_ref == hit.artifact_ref
