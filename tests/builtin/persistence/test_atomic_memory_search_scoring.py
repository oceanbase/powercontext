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

"""Atomic raw observations, public fusion and unchanged legacy enumeration scores."""

import math
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, ChannelScore
from powercontext.artifacts.fusion import RrfParameters
from powercontext.builtin.artifacts.search import InvalidSearchScore, UnsupportedScoreMetric
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryIndexHit,
    AtomicMemorySearchChannels,
    AtomicMemorySearchRequest,
    atomic_memory_channel_hits,
    combine_atomic_memory_channels,
)
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex


def _hit(key, score=1.0, *, revision=1, state_version=0, channel="text"):
    return AtomicMemoryIndexHit(
        ArtifactRef(family="atomic-memory", artifact_id=key, revision=revision),
        state_version,
        "fact",
        key,
        score,
        channel_scores={
            channel: ChannelScore(-1.0, "sqlite_bm25", False)
            if channel == "text"
            else ChannelScore(0.2, "l2_distance", False)
        },
    )


@pytest.mark.parametrize(
    ("raw", "metric", "score"), [(-0.5, "sqlite_bm25", 0.5), (0.5, "oceanbase_match", 0.5), (0.0, "sqlite_bm25", 0.0)]
)
def test_atomic_text_decoder_keeps_true_raw_direction(raw, metric, score) -> None:
    (hit,) = atomic_memory_channel_hits([
        {
            "artifact_id": "row",
            "revision": 1,
            "state_version": 0,
            "kind": "fact",
            "text": "needle",
            "score": score,
            "raw_score": raw,
            "score_metric": metric,
        }
    ])
    assert hit.score == score
    assert hit.channel_scores is not None
    assert hit.channel_scores["text"] == ChannelScore(raw, metric, metric == "oceanbase_match")


@pytest.mark.parametrize(
    ("raw", "metric", "error"),
    [
        (0.5, "sqlite_bm25", InvalidSearchScore),
        (-0.5, "oceanbase_match", InvalidSearchScore),
        (1.0, "unknown", UnsupportedScoreMetric),
        (math.inf, "oceanbase_match", InvalidSearchScore),
    ],
)
def test_atomic_decoder_rejects_illegal_known_raw_or_unknown_metric(raw, metric, error) -> None:
    with pytest.raises(error):
        atomic_memory_channel_hits([
            {
                "artifact_id": "row",
                "revision": 1,
                "state_version": 0,
                "kind": "fact",
                "text": "needle",
                "score": 1.0,
                "raw_score": raw,
                "score_metric": metric,
            }
        ])


def test_atomic_vector_decoder_keeps_actual_distance() -> None:
    (hit,) = atomic_memory_channel_hits(
        [
            {
                "artifact_id": "row",
                "revision": 1,
                "state_version": 0,
                "kind": "fact",
                "text": "needle",
                "distance": 0.25,
                "invalid_vectors": 0,
            }
        ],
        vector=True,
    )
    assert hit.score == -0.25
    assert hit.distance == 0.25
    assert hit.channel_scores is not None
    assert hit.channel_scores["vector"] == ChannelScore(0.25, "l2_distance", False)


def test_atomic_public_rrf_retains_enabled_empty_channel_and_observed_zero_weight() -> None:
    text = _hit("shared")
    channels = AtomicMemorySearchChannels(fts=(text,), vector=())
    (hybrid,) = combine_atomic_memory_channels(channels, fusion=RrfParameters(), mode="hybrid")
    assert hybrid.retrieval_score == 0.5
    assert hybrid.channel_scores == text.channel_scores
    channels = AtomicMemorySearchChannels(fts=(text,), vector=(_hit("shared", channel="vector"),))
    (selected,) = combine_atomic_memory_channels(channels, fusion=RrfParameters(weights={"text": 0}), mode="hybrid")
    assert selected.retrieval_score == 1
    assert selected.channel_scores is not None
    assert set(selected.channel_scores) == {"text", "vector"}


def test_atomic_rrf_legacy_score_and_ties_are_unchanged() -> None:
    channels = AtomicMemorySearchChannels(
        fts=(_hit("z"), _hit("a")), vector=(_hit("a", channel="vector"), _hit("z", channel="vector"))
    )
    legacy = combine_atomic_memory_channels(channels)
    assert [hit.artifact_ref.artifact_id for hit in legacy] == ["a", "z"]
    assert [hit.score for hit in legacy] == [1 / 61 + 1 / 62, 1 / 61 + 1 / 62]
    public = combine_atomic_memory_channels(
        channels, fusion=RrfParameters(rank_constant=10, weights={"text": 3}), mode="hybrid"
    )
    assert [hit.artifact_ref.artifact_id for hit in public] == ["z", "a"]
    for hit in public:
        assert hit.retrieval_score is not None
        assert 0 <= hit.retrieval_score <= 1


@pytest.mark.parametrize("public", [False, True])
def test_atomic_cross_channel_revision_or_state_mismatch_fails_closed(public) -> None:
    for changed in (_hit("row", revision=2, channel="vector"), _hit("row", state_version=1, channel="vector")):
        with pytest.raises(AtomicMemoryIndexError, match="changed between"):
            combine_atomic_memory_channels(
                AtomicMemorySearchChannels(fts=(_hit("row"),), vector=(changed,)),
                **({"fusion": RrfParameters(), "mode": "hybrid"} if public else {}),
            )


@pytest.mark.parametrize("distance", [-0.01, math.inf, math.nan])
def test_atomic_vector_raw_distance_must_be_finite_and_nonnegative(distance) -> None:
    with pytest.raises(InvalidSearchScore):
        atomic_memory_channel_hits(
            [
                {
                    "artifact_id": "row",
                    "revision": 1,
                    "state_version": 0,
                    "kind": "fact",
                    "text": "needle",
                    "distance": distance,
                    "invalid_vectors": 0,
                }
            ],
            vector=True,
        )


def test_atomic_public_fusion_requires_backend_raw_even_without_requested_metadata() -> None:
    hit = AtomicMemoryIndexHit(
        ArtifactRef(family="atomic-memory", artifact_id="row", revision=1), 0, "fact", "needle", 1.0
    )
    channels = AtomicMemorySearchChannels(fts=(hit,))
    assert combine_atomic_memory_channels(channels)[0].score == 1 / 61
    with pytest.raises(InvalidSearchScore):
        combine_atomic_memory_channels(channels, fusion=RrfParameters(), mode="text")


@pytest.mark.parametrize(
    ("dialect", "raw", "metric"), [("sqlite", -0.5, "sqlite_bm25"), ("mysql", 0.5, "oceanbase_match")]
)
def test_atomic_backend_sql_returns_raw_separately_without_changing_legacy_order(dialect, raw, metric) -> None:
    import asyncio

    class Connection:
        def __init__(self):
            self.dialect = SimpleNamespace(name=dialect)
            self.sql = ""
            self.parameters = {}

        async def execute(self, statement, parameters):
            self.sql, self.parameters = str(statement), parameters
            return self

        def mappings(self):
            return [
                {
                    "artifact_id": "row",
                    "revision": 1,
                    "state_version": 0,
                    "kind": "fact",
                    "text": "needle",
                    "score": 0.5,
                    "raw_score": raw,
                    "score_metric": metric,
                }
            ]

    async def scenario():
        connection = Connection()
        index = SQLiteAtomicMemoryIndex() if dialect == "sqlite" else OceanBaseAtomicMemoryIndex()
        result = await index.search(
            cast(AsyncConnection, connection),
            "project",
            AtomicMemorySearchRequest("needle", AtomicMemoryIndexFilter(scope_read=True), limit=7),
        )
        assert "AS raw_score" in connection.sql and f"'{metric}' AS score_metric" in connection.sql
        assert "ORDER BY score DESC, artifact_id LIMIT :result_limit" in connection.sql
        assert connection.parameters["result_limit"] == 7
        assert result.fts[0].score == 0.5 and result.fts[0].channel_scores is not None
        assert result.fts[0].channel_scores["text"].raw == raw

    asyncio.run(scenario())
