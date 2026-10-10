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

import asyncio
import math
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from powercontext import ArtifactRef
from powercontext.artifacts.fusion import RrfParameters
from powercontext.builtin.artifacts.memory import MemoryChannelHit, MemoryMatchedBy
from powercontext.builtin.artifacts.memory.fusion import fuse_rankings
from powercontext.builtin.artifacts.search import RecallChannelWeights
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryChannelHit,
    TopicMemoryMatchedBy,
    TopicMemorySearchChannels,
)
from powercontext.builtin.artifacts.topic_memory.fusion import _fuse_topic_memory_rankings
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, composition
from powercontext.server.settings import ServerSettings


def test_recall_channel_weights_default_to_the_historical_equal_ratio() -> None:
    config = RuntimeConfig()
    empty_config = RuntimeConfig.model_validate({})

    assert config.recall_fts_weight == 1.0
    assert config.recall_vector_weight == 1.0
    assert config.recall_channel_weights.fts == 1.0
    assert config.recall_channel_weights.vector == 1.0
    assert empty_config.recall_channel_weights == config.recall_channel_weights


def test_recall_channel_weights_are_normalized_as_a_relative_ratio() -> None:
    weights = RuntimeConfig(recall_fts_weight=3.0, recall_vector_weight=1.0).recall_channel_weights
    scaled = RuntimeConfig(recall_fts_weight=300.0, recall_vector_weight=100.0).recall_channel_weights

    assert weights.fts == pytest.approx(1.5)
    assert weights.vector == pytest.approx(0.5)
    assert scaled == weights


def test_recall_channel_weights_support_the_reverse_ratio() -> None:
    weights = RuntimeConfig(recall_fts_weight=1.0, recall_vector_weight=3.0).recall_channel_weights

    assert weights.fts == pytest.approx(0.5)
    assert weights.vector == pytest.approx(1.5)


@pytest.mark.parametrize("configured_weight", [1e-310, 1e-320])
def test_recall_channel_weights_normalize_equal_finite_subnormal_values(configured_weight: float) -> None:
    weights = RuntimeConfig(
        recall_fts_weight=configured_weight,
        recall_vector_weight=configured_weight,
    ).recall_channel_weights

    assert math.isfinite(weights.fts)
    assert math.isfinite(weights.vector)
    assert weights.fts == pytest.approx(1.0)
    assert weights.vector == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("fts", "vector", "expected_fts", "expected_vector"),
    [
        (0.0, 1.0, 0.0, 2.0),
        (1.0, 0.0, 2.0, 0.0),
        (1.0, 1.0, 1.0, 1.0),
        (1e-300, 1.0, 2e-300, 2.0),
    ],
)
def test_recall_channel_weights_preserve_valid_ratios(
    fts: float,
    vector: float,
    expected_fts: float,
    expected_vector: float,
) -> None:
    weights = RuntimeConfig(recall_fts_weight=fts, recall_vector_weight=vector).recall_channel_weights

    assert weights.fts == pytest.approx(expected_fts, abs=0.0)
    assert weights.vector == pytest.approx(expected_vector, abs=0.0)


def test_recall_channel_weights_accept_the_smallest_normalized_normal_weight() -> None:
    weights = RuntimeConfig(
        recall_fts_weight=sys.float_info.min,
        recall_vector_weight=2.0,
    ).recall_channel_weights

    assert weights.fts == sys.float_info.min
    assert weights.vector == 2.0


@pytest.mark.parametrize(
    ("fts", "vector"),
    [
        (1e-20, 1e303),
        (1e303, 1e-20),
        (1e-320, 1e303),
        (1e303, 1e-320),
        (sys.float_info.min / 2.0, 2.0),
        (2.0, sys.float_info.min / 2.0),
    ],
)
def test_extreme_nonzero_recall_weight_ratios_are_rejected_at_configuration_time(
    fts: float,
    vector: float,
) -> None:
    with pytest.raises(ValidationError, match="must be at least the smallest normal IEEE 754 binary64 value"):
        RuntimeConfig(recall_fts_weight=fts, recall_vector_weight=vector)


@pytest.mark.parametrize(
    ("fts", "vector"),
    [
        (1e-20, 1e303),
        (1e303, 1e-20),
        (1e-320, 1e303),
        (1e303, 1e-320),
    ],
)
def test_extreme_nonzero_recall_weight_ratios_are_rejected_by_the_domain_type(
    fts: float,
    vector: float,
) -> None:
    with pytest.raises(ValueError, match="must be at least the smallest normal IEEE 754 binary64 value"):
        RecallChannelWeights(fts=fts, vector=vector)


@pytest.mark.parametrize(
    ("fts", "vector", "memory_channel", "topic_channel"),
    [
        (sys.float_info.min, 2.0, "fts", "topic_fts"),
        (2.0, sys.float_info.min, "vector", "topic_vector"),
    ],
)
def test_smallest_accepted_channel_weight_remains_usable_in_memory_and_topic_fusion(
    fts: float,
    vector: float,
    memory_channel: MemoryMatchedBy,
    topic_channel: TopicMemoryMatchedBy,
) -> None:
    weights = RuntimeConfig(recall_fts_weight=fts, recall_vector_weight=vector).recall_channel_weights
    memory_candidate = MemoryChannelHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id="entry",
        entry_version_id="version",
        text="needle",
        distance=0.1 if memory_channel == "vector" else None,
    )
    memory_hits = fuse_rankings(
        fts=(memory_candidate,) if memory_channel == "fts" else (),
        vector=(memory_candidate,) if memory_channel == "vector" else (),
        limit=1,
        channel_weights=weights,
    )
    topic_candidate = TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id="topic", revision=1),
        title="Needle topic",
        summary="needle",
        channel=topic_channel,
        distance=0.1 if topic_channel == "topic_vector" else None,
    )
    topic_hits = _fuse_topic_memory_rankings(
        "needle",
        TopicMemorySearchChannels(
            topic_fts=(topic_candidate,) if topic_channel == "topic_fts" else (),
            topic_vector=(topic_candidate,) if topic_channel == "topic_vector" else (),
        ),
        1,
        fusion=RrfParameters(
            weights={
                "topic_fts": weights.fts,
                "detail_fts": weights.fts,
                "topic_vector": weights.vector,
                "detail_vector": weights.vector,
            }
        ),
    ).hits

    assert len(memory_hits) == 1
    assert memory_hits[0].matched_by == (memory_channel,)
    assert memory_hits[0].score > 0.0
    assert memory_hits[0].score_upper_bound is not None
    assert memory_hits[0].score_upper_bound > 0.0
    assert len(topic_hits) == 1
    assert topic_hits[0].matched_by == (topic_channel,)
    assert topic_hits[0].score > 0.0


def test_recall_channel_weight_total_overflow_reports_the_actual_cause() -> None:
    with pytest.raises(ValueError, match="weight total must be finite"):
        RecallChannelWeights(fts=1e308, vector=1e308)

    with pytest.raises(ValidationError, match="weight total must be finite"):
        RuntimeConfig(recall_fts_weight=1e308, recall_vector_weight=1e308)


def test_composition_passes_recall_weights_to_regular_and_topic_worker_contexts(
    tmp_path: Path,
) -> None:
    runtime_config = RuntimeConfig(recall_fts_weight=1.0, recall_vector_weight=3.0)
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recall-weights.db'}"),
        runtime=runtime_config,
    )
    expected = runtime_config.recall_channel_weights

    async def scenario() -> None:
        async with composition.open_builtin_contexts(config) as contexts:
            assert contexts.repositories.topic_memories.recall_channel_weights == expected
        async with composition.open_builtin_contexts(config, _topic_memory_worker=True) as worker_contexts:
            assert worker_contexts.repositories.topic_memories.recall_channel_weights == expected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("fts", "vector"),
    [
        (-1.0, 1.0),
        (1.0, -1.0),
        (math.nan, 1.0),
        (math.inf, 1.0),
        (-math.inf, 1.0),
        (1.0, math.nan),
        (1.0, math.inf),
        (1.0, -math.inf),
        (0.0, 0.0),
        (1e308, 1e308),
        (True, 1.0),
        (1.0, False),
    ],
)
def test_invalid_recall_channel_weights_are_rejected_at_configuration_time(fts: object, vector: object) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"recall_fts_weight": fts, "recall_vector_weight": vector})


@pytest.mark.parametrize("field", ["recall_fts_weight", "recall_vector_weight"])
def test_none_recall_channel_weight_is_rejected_by_its_field(field: str) -> None:
    with pytest.raises(ValidationError) as error:
        RuntimeConfig.model_validate({field: None})

    assert error.value.errors()[0]["loc"] == (field,)


def test_recall_channel_weights_load_from_the_server_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_RECALL_FTS_WEIGHT", "3")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_RECALL_VECTOR_WEIGHT", "1")

    runtime = ServerSettings().runtime

    assert runtime.recall_fts_weight == 3.0
    assert runtime.recall_vector_weight == 1.0
    assert runtime.recall_channel_weights.fts == pytest.approx(1.5)
    assert runtime.recall_channel_weights.vector == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("fts_weight", "vector_weight"),
    [("1e-20", "1e303"), ("1e303", "1e-20")],
)
def test_extreme_recall_channel_weight_ratio_from_server_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    fts_weight: str,
    vector_weight: str,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_RECALL_FTS_WEIGHT", fts_weight)
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_RECALL_VECTOR_WEIGHT", vector_weight)

    with pytest.raises(ValidationError, match="must be at least the smallest normal IEEE 754 binary64 value"):
        ServerSettings()
