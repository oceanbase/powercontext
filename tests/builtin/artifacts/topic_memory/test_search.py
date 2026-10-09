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

"""Public Topic request and configurable fusion behavior."""

import json

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.topic_memory import TopicMemoryChannelHit, TopicMemorySearchChannels


def test_topic_request_has_family_defaults_and_preserves_explicit_fields():
    from powercontext.builtin.artifacts.topic_memory import TopicArtifactSearchRequest

    request = TopicArtifactSearchRequest.model_validate_json(
        '{"query":"  needle  ","admission":{"lexical_coverage":0}}'
    )
    assert request.query == "needle"
    assert request.mode is None
    assert request.limit == 10
    assert request.admission.min_semantic_similarity == 0.3
    assert request.admission.model_fields_set == {"lexical_coverage"}
    assert request.model_fields_set == {"query", "admission"}


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": None},
        {"min_score": None},
        {"fusion": None},
        {"filters": {"tag": "x"}},
        {"mode": "fts"},
        {"mode": "auto"},
        {"limit": 21},
        {"rerank": {}},
        {"admission": {"min_semantic_similarity": True}},
        {"admission": {"lexical_min_matched_terms": 1.0}},
        {"min_score": "0.5"},
    ],
)
def test_topic_request_rejects_unsupported_or_non_strict_fields(fields):
    from powercontext.builtin.artifacts.topic_memory import TopicArtifactSearchRequest

    with pytest.raises(ValidationError):
        TopicArtifactSearchRequest.model_validate_json(json.dumps({"query": "needle", **fields}), strict=True)


def _hit(channel, artifact_id="topic", *, distance=None, raw_score=-2.0, metric="sqlite_bm25"):
    return TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id=artifact_id, revision=1),
        title="needle",
        summary="needle evidence",
        channel=channel,
        chunk_text="needle detail" if channel.startswith("detail") else None,
        chunk_ordinal=0 if channel.startswith("detail") else None,
        chunk_start=0 if channel.startswith("detail") else None,
        distance=distance,
        raw_score=raw_score,
        metric=metric,
    )


def test_topic_fusion_weights_scores_and_metadata_keep_channel_evidence():
    from powercontext.artifacts.fusion import RrfParameters
    from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings

    channels = TopicMemorySearchChannels(
        topic_fts=(_hit("topic_fts"),),
        detail_fts=(_hit("detail_fts", raw_score=-3),),
        topic_vector=(_hit("topic_vector", distance=0.1),),
    )
    result = _fuse_topic_memory_rankings(
        "needle", channels, 10, fusion=RrfParameters(weights={"topic_fts": 0}), include_scores=True
    )
    assert result.hits[0].retrieval_score == pytest.approx(2 / 3)
    assert result.hits[0].score == pytest.approx(200 / 3)
    assert result.hits[0].matched_by == ("topic_fts", "topic_vector", "detail_fts")
    assert result.hits[0].snippet == "needle detail"
    metadata = result.hits[0].channel_scores
    assert metadata is not None
    assert metadata["topic_fts"].raw == -2
    assert metadata["topic_fts"].higher_is_better is False
    assert metadata["topic_vector"].raw == 0.1
    assert metadata["topic_vector"].metric == "l2_distance"
    assert "detail_vector" not in metadata


def test_topic_fusion_threshold_does_not_fill_with_lower_candidates():
    from powercontext.artifacts.fusion import RrfParameters
    from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings

    channels = TopicMemorySearchChannels(topic_fts=(_hit("topic_fts", "first"), _hit("topic_fts", "second")))
    result = _fuse_topic_memory_rankings("needle", channels, 10, mode="fts", fusion=RrfParameters(), min_score=0.5)
    assert [hit.artifact_ref.artifact_id for hit in result.hits] == ["first"]


def test_topic_fusion_keeps_duplicate_rank_gaps_and_score_toggle_order():
    from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings

    channels = TopicMemorySearchChannels(
        topic_fts=(_hit("topic_fts", "first"), _hit("topic_fts", "first"), _hit("topic_fts", "second"))
    )
    plain = _fuse_topic_memory_rankings("needle", channels, 10, mode="fts")
    scored = _fuse_topic_memory_rankings("needle", channels, 10, mode="fts", include_scores=True)
    assert [hit.artifact_ref for hit in plain.hits] == [hit.artifact_ref for hit in scored.hits]
    assert plain.hits[1].retrieval_score == pytest.approx(61 / 126)
    assert plain.hits[0].channel_scores is None


@pytest.mark.parametrize("weights", [{"unknown": 0}, {"topic_vector": 0}, {"topic_fts": 0, "detail_fts": 0}])
def test_topic_fusion_rejects_inactive_unknown_and_all_zero_weights(weights):
    from powercontext.artifacts.fusion import RrfParameters
    from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings

    with pytest.raises(ValidationError):
        _fuse_topic_memory_rankings(
            "needle", TopicMemorySearchChannels(), 10, mode="fts", fusion=RrfParameters(weights=weights)
        )


def test_topic_fusion_custom_rank_constant_and_zero_weight_phantoms():
    from powercontext.artifacts.fusion import RrfParameters
    from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings

    channels = TopicMemorySearchChannels(
        topic_fts=(_hit("topic_fts", "phantom"),),
        detail_fts=(_hit("detail_fts", "first"), _hit("detail_fts", "first"), _hit("detail_fts", "second")),
    )
    result = _fuse_topic_memory_rankings(
        "needle", channels, 10, mode="fts", fusion=RrfParameters(rank_constant=1, weights={"topic_fts": 0})
    )
    assert [hit.artifact_ref.artifact_id for hit in result.hits] == ["first", "second"]
    assert [hit.retrieval_score for hit in result.hits] == [1.0, 0.5]


def test_topic_admission_rejection_removes_only_that_channel_contribution():
    from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings

    lexical = _hit("topic_fts").model_copy(update={"title": "alpha", "summary": "unrelated"})
    semantic = _hit("topic_vector", distance=0.1)
    outcome = _fuse_topic_memory_rankings(
        "alpha beta gamma",
        TopicMemorySearchChannels(topic_fts=(lexical,), topic_vector=(semantic,)),
        10,
        include_scores=True,
    )
    assert outcome.hits[0].matched_by == ("topic_vector",)
    assert outcome.hits[0].retrieval_score == 0.25
    assert outcome.hits[0].channel_scores is not None
    assert set(outcome.hits[0].channel_scores) == {"topic_vector"}
    assert outcome.rejected == 1
