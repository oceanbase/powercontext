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

"""Capability negotiation preserves old tenants without hiding database failures."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.search import AdmissionFloor
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryRelatedRequest,
    AtomicMemorySearchRequest,
)
from powercontext.builtin.persistence.oceanbase import atomic_memory_index as oceanbase_index
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex


class _Pool:
    pass


class _Result:
    def __init__(self, score: float = 0.75):
        self.score = score

    def close(self):
        pass

    def mappings(self):
        return [
            {
                "artifact_id": "memory",
                "revision": 2,
                "state_version": 3,
                "kind": "fact",
                "text": "A searchable memory.",
                "score": self.score,
                "raw_score": self.score,
                "score_metric": "oceanbase_match",
            }
        ]


def _database_error(code: int) -> DBAPIError:
    return DBAPIError("synthetic statement", None, Exception(code, "Synthetic SCORE capability error"))


class _Connection:
    """Model the server's accepted dialect, not full-text matching or SQL execution."""

    def __init__(
        self,
        *,
        capability_error: BaseException | None = None,
        version: str = "5.7.25-OceanBase_CE-v4.6.0.1",
    ):
        self.dialect = SimpleNamespace(name="mysql")
        self.sync_engine = SimpleNamespace(pool=_Pool())
        self.capability_error = capability_error
        self.version = version
        self.native_query_error: BaseException | None = None
        self.parameters: list[dict[str, object]] = []

    async def exec_driver_sql(self, statement):
        if statement == "SELECT VERSION()":
            return SimpleNamespace(scalar_one=lambda: self.version)
        if "SCORE()" in statement and self.capability_error is not None:
            raise self.capability_error
        return _Result()

    async def scalar(self, _statement, _parameters=None):
        return 1

    async def scalars(self, _statement, _parameters=None):
        return ("description",)

    async def execute(self, statement, parameters):
        native = "SCORE()" in str(statement)
        if native and self.capability_error is not None:
            raise self.capability_error
        if native and self.native_query_error is not None:
            raise self.native_query_error
        self.parameters.append(dict(parameters))
        # Distinct observations make routing visible without asserting a SQL template.
        return _Result(0.75 if native else 0.5)


async def _search(index, connection, query="alpha beta"):
    result = await index.search(
        cast(AsyncConnection, connection),
        "project",
        AtomicMemorySearchRequest(query, AtomicMemoryIndexFilter(), limit=7),
    )
    (hit,) = result.fts
    assert (hit.artifact_ref.artifact_id, hit.artifact_ref.revision, hit.state_version) == ("memory", 2, 3)
    assert hit.channel_scores is not None
    assert hit.channel_scores["text"].raw == hit.score
    return hit


@pytest.mark.parametrize("code", [1064, 1235, 1305])
def test_search_uses_legacy_coverage_when_server_lacks_native_match(code):
    async def scenario():
        connection = _Connection(capability_error=_database_error(code))
        hit = await _search(OceanBaseAtomicMemoryIndex(), connection)
        assert hit.score == 0.5

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("version", "expected_score"),
    [
        ("5.7.25-OceanBase_CE-v4.4.2.1", 0.5),
        ("5.7.25-OceanBase_CE-v4.5.0.0", 0.5),
        ("5.7.25-OceanBase_CE-v4.6.0.1", 0.75),
        ("5.7.25-OceanBase_CE-v5.0.1.0", 0.75),
        ("5.7.25-OceanBase seekdb-v1.4.0.0", 0.5),
        ("5.7.25", 0.5),
    ],
)
def test_long_queries_preserve_coverage_on_engines_with_limited_native_matching(version, expected_score):
    async def scenario():
        connection = _Connection(version=version)
        index = OceanBaseAtomicMemoryIndex()
        long_hit = await _search(index, connection, " ".join(f"q{i:04}" for i in range(257)))
        assert long_hit.score == expected_score
        short_hit = await _search(index, connection, " ".join(f"q{i:04}" for i in range(256)))
        assert short_hit.score == 0.75

    asyncio.run(scenario())


@pytest.mark.parametrize("code", [1064, 4012])
def test_initialize_preserves_legacy_support_but_surfaces_probe_failure(code, monkeypatch):
    # Table creation is independent of capability negotiation and needs a real database.
    monkeypatch.setattr(oceanbase_index, "create_tables", AsyncMock())

    async def scenario():
        error = _database_error(code)
        connection = _Connection(capability_error=error)
        index = OceanBaseAtomicMemoryIndex()
        if code == 1064:
            await index.initialize(cast(AsyncConnection, connection))
            assert (await _search(index, connection)).score == 0.5
        else:
            with pytest.raises(AtomicMemoryIndexError) as caught:
                await index.initialize(cast(AsyncConnection, connection))
            assert caught.value.__cause__ is error

    asyncio.run(scenario())


@pytest.mark.parametrize("code", [4012, 1142, 2013])
def test_native_capability_failure_does_not_hide_timeout_permission_or_disconnect(code):
    async def scenario():
        error = _database_error(code)
        connection = _Connection(capability_error=error)
        with pytest.raises(DBAPIError) as caught:
            await _search(OceanBaseAtomicMemoryIndex(), connection)
        assert caught.value is error

    asyncio.run(scenario())


@pytest.mark.parametrize("code", [1064, 4012])
def test_native_search_failure_is_not_retried_as_legacy_coverage(code):
    async def scenario():
        connection = _Connection()
        index = OceanBaseAtomicMemoryIndex()
        assert (await _search(index, connection)).score == 0.75
        error = _database_error(code)
        connection.native_query_error = error
        with pytest.raises(DBAPIError) as caught:
            await _search(index, connection)
        assert caught.value is error

    asyncio.run(scenario())


@pytest.mark.parametrize("replace_pool", [False, True], ids=["different-engine", "disposed-engine"])
@pytest.mark.parametrize("first_native", [False, True], ids=["upgrade", "downgrade"])
def test_native_capability_does_not_leak_between_connection_pools(replace_pool, first_native):
    async def scenario():
        index = OceanBaseAtomicMemoryIndex()
        old = _Connection(capability_error=None if first_native else _database_error(1064))
        assert (await _search(index, old)).score == (0.75 if first_native else 0.5)
        next_error = _database_error(1064) if first_native else None
        if replace_pool:
            old.sync_engine.pool = _Pool()
            old.capability_error = next_error
            new = old
        else:
            new = _Connection(capability_error=next_error)
        assert (await _search(index, new)).score == (0.5 if first_native else 0.75)

    asyncio.run(scenario())


def test_native_search_binding_count_is_independent_of_query_length():
    async def scenario():
        index = OceanBaseAtomicMemoryIndex()
        connection = _Connection()
        short = await _search(index, connection, "q0000 q0001")
        long = await _search(index, connection, " ".join(f"q{number:04}" for number in range(1000)))
        assert short.score == long.score == 0.75
        assert len(connection.parameters[0]) == len(connection.parameters[1])

    asyncio.run(scenario())


def test_native_lexical_admission_above_query_term_count_returns_empty():
    async def scenario():
        index = OceanBaseAtomicMemoryIndex()
        connection = _Connection()
        assert (await _search(index, connection)).score == 0.75
        floor = AdmissionFloor(lexical_min_matched_terms=5)
        channels = await index.search(
            cast(AsyncConnection, connection),
            "project",
            AtomicMemorySearchRequest("alpha beta gamma", AtomicMemoryIndexFilter(), admission=floor),
        )
        assert not channels.fts
        hits, heads = await index.enumerate_related_with_heads(
            cast(AsyncConnection, connection),
            "project",
            AtomicMemoryRelatedRequest("alpha beta gamma", AtomicMemoryIndexFilter(), admission=floor),
        )
        assert not hits and not heads

    asyncio.run(scenario())
