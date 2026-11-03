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

"""OceanBase bounded related recall keeps per-channel limits and whole-Scope readiness."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryRelatedRequest,
)
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex

PROFILE = EmbeddingProfile(profile_id="candidate-cap", model="fixture", dimension=3, normalization="unit")


class _Connection:
    def __init__(self, rows: tuple[dict[str, Any], ...] = ()) -> None:
        self.dialect = SimpleNamespace(name="mysql")
        self.sql = ""
        self.parameters: dict[str, object] = {}
        self.rows = rows

    async def execute(self, statement, parameters):
        self.sql, self.parameters = str(statement), dict(parameters)
        return self

    def mappings(self):
        return self.rows


def _request(mode: Literal["fts", "vector", "hybrid"], cap: int | None) -> AtomicMemoryRelatedRequest:
    return AtomicMemoryRelatedRequest(
        query="needle",
        filters=AtomicMemoryIndexFilter(),
        mode=mode,
        query_vector=(1.0, 0.0, 0.0) if mode != "fts" else None,
        embedding_profile=PROFILE if mode != "fts" else None,
        max_distance=1.0 if mode != "fts" else None,
        max_candidates=cap,
    )


def _union_arms(statement: str) -> tuple[str, ...]:
    """Return the parenthesized UNION arms in statement order."""
    start = statement.index("(SELECT 'fts'")
    arms: list[str] = []
    depth = 0
    arm_start = start + 1
    for index in range(start, len(statement)):
        character = statement[index]
        if character == "(":
            if depth == 0:
                arm_start = index + 1
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                arms.append(statement[arm_start:index])
    return tuple(arms)


def _select_width(arm: str) -> int:
    """Count top-level SELECT-list expressions up to the first top-level FROM."""
    body = arm[len("SELECT ") :]
    depth = 0
    columns = 1
    for index, character in enumerate(body):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            columns += 1
        elif depth == 0 and body.startswith("FROM", index) and body[index - 1] == " ":
            break
    return columns


def _row(*, channel: str, artifact_id: str | None, invalid_vectors: int, score: float | None = None) -> dict[str, Any]:
    return {
        "channel": channel,
        "artifact_id": artifact_id,
        "revision": 1 if artifact_id is not None else None,
        "state_version": 0 if artifact_id is not None else None,
        "kind": "fact" if artifact_id is not None else None,
        "text": "needle" if artifact_id is not None else None,
        "score": score,
        "raw_score": score,
        "score_metric": "oceanbase_match" if score is not None else None,
        "coverage": 100 if score is not None else 0,
        "distance": None,
        "invalid_vectors": invalid_vectors,
        "authority_artifact_id": None,
    }


def test_oceanbase_bounded_related_recall_limits_both_channels_and_audits_readiness() -> None:
    async def scenario() -> None:
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        connection = _Connection()
        hits, heads = await index.enumerate_related_with_heads(
            cast(AsyncConnection, connection), "scope", _request("hybrid", 3)
        )
        assert connection.sql.count("LIMIT :result_limit") == 2
        assert "WITH readiness AS (SELECT COUNT(*)" in connection.sql
        assert "FROM readiness WHERE invalid_vectors > 0" in connection.sql
        assert "(SELECT 'fts' AS channel" in connection.sql
        assert "(SELECT 'vector' AS channel, artifact_id" in connection.sql
        assert "(SELECT 'vector' AS channel, NULL AS artifact_id" in connection.sql
        assert "LIMIT :result_limit) UNION ALL" in connection.sql
        assert connection.parameters["result_limit"] == 3 + 60
        assert hits == () and heads == {}

    asyncio.run(scenario())


def test_oceanbase_unbounded_related_recall_keeps_the_complete_enumeration_sql() -> None:
    async def scenario() -> None:
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        connection = _Connection()
        await index.enumerate_related_with_heads(cast(AsyncConnection, connection), "scope", _request("hybrid", None))
        assert "result_limit" not in connection.parameters
        assert "LIMIT :result_limit" not in connection.sql
        assert "WITH readiness AS" not in connection.sql

    asyncio.run(scenario())


def test_oceanbase_bounded_fts_recall_skips_readiness_audit() -> None:
    async def scenario() -> None:
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        connection = _Connection()
        await index.enumerate_related_with_heads(cast(AsyncConnection, connection), "scope", _request("fts", 4))
        assert connection.sql.count("LIMIT :result_limit") == 1
        assert "readiness AS" not in connection.sql
        assert connection.parameters["result_limit"] == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [0, -1, 1001, cast(int, True), 3.0, "3"])
def test_oceanbase_bounded_related_recall_validates_the_limit(invalid: object) -> None:
    async def scenario() -> None:
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        connection = _Connection()
        request = replace(_request("hybrid", 3), max_candidates=cast(int, invalid))
        with pytest.raises(AtomicMemoryIndexError, match="positive") as raised:
            await index.enumerate_related_with_heads(cast(AsyncConnection, connection), "scope", request)
        assert raised.value.code == "limit"

    asyncio.run(scenario())


def test_oceanbase_bounded_related_recall_binds_every_named_parameter() -> None:
    async def scenario() -> None:
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        connection = _Connection()
        await index.enumerate_related_with_heads(cast(AsyncConnection, connection), "scope", _request("hybrid", 3))
        named = set(re.findall(r":([a-z_][a-z0-9_]*)", connection.sql))
        assert named <= set(connection.parameters)
        assert {"scope_id", "query_vector", "result_limit"} <= set(connection.parameters)

    asyncio.run(scenario())


def test_oceanbase_union_arms_declare_matching_column_widths() -> None:
    async def scenario() -> None:
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        connection = _Connection()
        await index.enumerate_related_with_heads(cast(AsyncConnection, connection), "scope", _request("hybrid", 3))
        arms = _union_arms(connection.sql)
        assert tuple(_select_width(arm) for arm in arms) == (12, 12, 12)

    asyncio.run(scenario())


def test_oceanbase_bounded_related_recall_rejects_incomplete_vector_rows() -> None:
    async def scenario() -> None:
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        connection = _Connection(
            rows=(
                _row(channel="fts", artifact_id="a", invalid_vectors=0, score=1.0),
                _row(channel="vector", artifact_id=None, invalid_vectors=1),
            )
        )
        with pytest.raises(AtomicMemoryIndexError, match="incomplete") as raised:
            await index.enumerate_related_with_heads(cast(AsyncConnection, connection), "scope", _request("hybrid", 1))
        assert raised.value.code == "incomplete-vector"

    asyncio.run(scenario())
