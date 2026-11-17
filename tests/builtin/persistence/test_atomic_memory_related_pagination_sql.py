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

"""Related pages preserve channel observations and report unusable inputs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from sqlalchemy.exc import DBAPIError

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryRelatedRequest,
)
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex

PROFILE = EmbeddingProfile(profile_id="related-pagination-sql", model="fixture", dimension=3, normalization="unit")


class _Result:
    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self.rows = rows

    def mappings(self) -> tuple[dict[str, Any], ...]:
        return self.rows


class _Pool:
    """Weak-referenceable stand-in for a SQLAlchemy pool."""


class _Connection:
    def __init__(
        self,
        *,
        batches: tuple[tuple[dict[str, Any], ...], ...] = (),
    ) -> None:
        self.dialect = SimpleNamespace(name="mysql")
        self.batches = list(batches)
        self.pool = _Pool()

    async def execute(self, statement, parameters=None):
        rows = self.batches.pop(0) if self.batches else ()
        return _Result(rows)

    async def run_sync(self, fn):
        return fn(SimpleNamespace(engine=SimpleNamespace(pool=self.pool)))

    async def exec_driver_sql(self, statement):
        raise DBAPIError(statement, {}, Exception(1064))


def _fts_rows(*identities: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "artifact_id": identity,
            "revision": 1,
            "state_version": 0,
            "kind": "fact",
            "score": 1.0,
            "raw_score": 1.0,
            "score_metric": "oceanbase_match",
            "coverage": 2,
            "distance": None,
        }
        for identity in identities
    )


def _vector_rows(*identities: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "artifact_id": identity,
            "revision": 1,
            "state_version": 0,
            "kind": "fact",
            "distance": 0.5,
            "invalid_vectors": 0,
        }
        for identity in identities
    )


def _hybrid_rows(*identities: str) -> tuple[dict[str, Any], ...]:
    return tuple({**row, "channel": "text"} for row in _fts_rows(*identities)) + tuple(
        {**row, "channel": "vector"} for row in _vector_rows(*identities)
    )


def _body_rows(*identities: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "artifact_id": identity,
            "projected_text": f"Fixture memory {identity}",
            "authority_content": {"kind": "fact", "text": f"Fixture memory {identity}"},
            "authority_scope_id": "scope",
            "authority_family": "atomic-memory",
            "authority_artifact_id": identity,
            "authority_revision": 1,
            "authority_lifecycle_state": "active",
            "authority_replacement_artifact_id": None,
            "authority_merged_into_id": None,
            "authority_governance_generation": 0,
        }
        for identity in identities
    )


def _request(mode: Literal["fts", "vector", "hybrid"], *, page_size: int = 10) -> AtomicMemoryRelatedRequest:
    return AtomicMemoryRelatedRequest(
        query="needle",
        filters=AtomicMemoryIndexFilter(),
        mode=mode,
        query_vector=(1.0, 0.0, 0.0) if mode != "fts" else None,
        embedding_profile=PROFILE if mode != "fts" else None,
        max_distance=1.0 if mode != "fts" else None,
        page_size=page_size,
    )


def test_oceanbase_hybrid_page_preserves_both_channel_observations() -> None:
    async def scenario() -> None:
        connection = _Connection(batches=(_hybrid_rows("memory-1"), _body_rows("memory-1")))
        page = await OceanBaseAtomicMemoryIndex(PROFILE).related_page(
            cast(Any, connection), "scope", _request("hybrid")
        )
        assert page is not None
        (hit,) = page.hits
        assert hit.artifact_ref.artifact_id == "memory-1"
        assert hit.score == pytest.approx(2 / 61)
        assert hit.distance == 0.5
        assert hit.channel_scores is not None and set(hit.channel_scores) == {"text", "vector"}

    asyncio.run(scenario())


def test_oceanbase_related_page_rejects_incomplete_vectors() -> None:
    async def scenario() -> None:
        connection = _Connection(batches=(({"invalid_vectors": 1},),))
        with pytest.raises(AtomicMemoryIndexError) as raised:
            await OceanBaseAtomicMemoryIndex(PROFILE).related_page(cast(Any, connection), "scope", _request("vector"))
        assert raised.value.code == "incomplete-vector"

    asyncio.run(scenario())


def test_oceanbase_related_page_reports_ranked_memories_without_bodies_as_stale_recall() -> None:
    async def scenario() -> None:
        connection = _Connection(batches=(_fts_rows("memory-1"), ()))
        with pytest.raises(AtomicMemoryIndexError) as raised:
            await OceanBaseAtomicMemoryIndex(PROFILE).related_page(cast(Any, connection), "scope", _request("fts"))
        assert raised.value.code == "stale-recall"

    asyncio.run(scenario())


@pytest.mark.parametrize("page_size", [0, -1, 1001, True, 3.0, "3"])
def test_oceanbase_related_page_rejects_invalid_page_sizes(page_size: object) -> None:
    async def scenario() -> None:
        with pytest.raises(AtomicMemoryIndexError) as raised:
            await OceanBaseAtomicMemoryIndex(PROFILE).related_page(
                cast(Any, _Connection()), "scope", replace(_request("fts"), page_size=cast(int, page_size))
            )
        assert raised.value.code == "limit"

    asyncio.run(scenario())
