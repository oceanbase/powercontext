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

"""OceanBase related pagination binds every rank and body parameter."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryRelatedRequest,
)
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex

PROFILE = EmbeddingProfile(
    profile_id="related-pagination-sql", model="fixture", dimension=3, normalization="unit"
)


class _Result:
    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self.rows = rows

    def mappings(self) -> tuple[dict[str, Any], ...]:
        return self.rows


class _Connection:
    def __init__(self, *, scalar: int = 0, batches: tuple[tuple[dict[str, Any], ...], ...] = ()) -> None:
        self.dialect = SimpleNamespace(name="mysql")
        self.scalar_value = scalar
        self.batches = list(batches)
        self.executed: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, parameters=None):
        self.executed.append((str(statement), dict(parameters or {})))
        rows = self.batches.pop(0) if self.batches else ()
        return _Result(rows)

    async def scalar(self, statement, parameters=None):
        self.executed.append((str(statement), dict(parameters or {})))
        return self.scalar_value


def _placeholder_names(statement: str) -> set[str]:
    return set(re.findall(r":([a-z_][a-z0-9_]*)", statement))


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
        {"artifact_id": identity, "revision": 1, "state_version": 0, "kind": "fact", "distance": 0.5}
        for identity in identities
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


@pytest.mark.parametrize("mode", ["fts", "vector", "hybrid"])
def test_oceanbase_related_page_binds_every_placeholder(mode: Literal["fts", "vector", "hybrid"]) -> None:
    async def scenario() -> None:
        batches = {
            "fts": (_fts_rows("memory-1"), _body_rows("memory-1")),
            "vector": (_vector_rows("memory-1"), _body_rows("memory-1")),
            "hybrid": (_fts_rows("memory-1"), _vector_rows("memory-1"), _body_rows("memory-1")),
        }[mode]
        connection = _Connection(batches=batches)
        page = await OceanBaseAtomicMemoryIndex(PROFILE).related_page(
            cast(Any, connection), "scope", _request(mode)
        )
        assert page is not None and tuple(hit.artifact_ref.artifact_id for hit in page.hits) == ("memory-1",)
        for statement, parameters in connection.executed:
            assert _placeholder_names(statement) <= set(parameters)

    asyncio.run(scenario())


def test_oceanbase_rank_queries_keep_bodies_out_of_rank_rows() -> None:
    async def scenario() -> None:
        connection = _Connection(batches=(_fts_rows("memory-1"), _body_rows("memory-1")))
        await OceanBaseAtomicMemoryIndex(PROFILE).related_page(cast(Any, connection), "scope", _request("fts"))
        rank_sql, body_sql = (statement for statement, _ in connection.executed)
        assert "authority_content" not in rank_sql
        assert " AS projected_text" not in rank_sql
        assert "artifact_id IN (__[POSTCOMPILE_body_ids])" in body_sql
        assert "current.text AS projected_text" in body_sql

    asyncio.run(scenario())


def test_oceanbase_hybrid_rank_queries_share_the_rank_identity_columns() -> None:
    async def scenario() -> None:
        connection = _Connection(
            batches=(_fts_rows("memory-1"), _vector_rows("memory-1"), _body_rows("memory-1"))
        )
        await OceanBaseAtomicMemoryIndex(PROFILE).related_page(
            cast(Any, connection), "scope", _request("hybrid")
        )
        rank_statements = tuple(
            statement
            for statement, _ in connection.executed
            if " AS projected_text" not in statement and "SELECT COUNT(*)" not in statement
        )
        assert len(rank_statements) == 2
        assert all("artifact_id, revision, state_version, kind" in statement for statement in rank_statements)
        assert all("authority_content" not in statement for statement in rank_statements)

    asyncio.run(scenario())


def test_oceanbase_related_page_rejects_incomplete_vector_rows_beyond_the_first_page() -> None:
    async def scenario() -> None:
        connection = _Connection(scalar=1)
        with pytest.raises(AtomicMemoryIndexError) as raised:
            await OceanBaseAtomicMemoryIndex(PROFILE).related_page(
                cast(Any, connection), "scope", _request("vector")
            )
        assert raised.value.code == "incomplete-vector"

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
