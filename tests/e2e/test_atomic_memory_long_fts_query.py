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

"""Long lexical queries preserve admission on SQLite with a depth limit of 1000."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from sqlalchemy import Engine, event

from powercontext.builtin.artifacts.search import AdmissionFloor
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexFilter, AtomicMemoryRelatedRequest
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from tests.e2e.test_access_control_regressions import _scope, _server
from tests.e2e.test_sqlite_vec import _KeywordEmbeddingModel

QUERY = " ".join(f"q{index:04}" for index in range(1000))
# The 25% match threshold must include terms at the end of the query.
ADMITTED = "Alpha " + " ".join(f"q{index:04}" for index in range(750, 1000))
# Repetition raises BM25 without increasing the distinct matched-term count.
BELOW_THRESHOLD = "Alpha " + " ".join(f"q{index:04}" for index in range(249) for _ in range(3))


@pytest.fixture(autouse=True)
def sqlite_expression_limit():
    # Some Python distributions raise the compiled default to 10000. Exercise
    # the affected deployment setting using SQLite's own per-connection limit.
    def configure(connection, _record):
        if isinstance(connection, sqlite3.Connection):
            connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 1000)
        else:

            async def set_limit(driver):
                await driver._execute(driver._conn.setlimit, sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 1000)

            connection.run_async(set_limit)

    event.listen(Engine, "connect", configure)
    try:
        yield
    finally:
        event.remove(Engine, "connect", configure)


@pytest.mark.parametrize("entrypoint", ["atomic", "generic"])
def test_long_fts_query_http_keeps_all_terms_before_result_limit(tmp_path, entrypoint):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope = await _scope(client)
            for body in (ADMITTED, BELOW_THRESHOLD):
                created = await client.post(
                    "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": body}
                )
                assert created.status_code == 200, created.text
            path = (
                "/v1/atomic-memory/search"
                if entrypoint == "atomic"
                else f"/v1/scopes/{scope}/artifacts/atomic-memory/search"
            )
            for limit in (1, 100):
                request = {"query": QUERY, "mode": "text", "limit": limit}
                if entrypoint == "atomic":
                    request["scope_id"] = scope
                response = await client.post(path, json=request)
                assert response.status_code == 200, response.text
                data = response.json()
                bodies = (
                    [hit["memory"]["text"] for hit in data["hits"]]
                    if entrypoint == "atomic"
                    else [result["content"]["text"] for result in data["results"]]
                )
                assert bodies == [ADMITTED]

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["text", "hybrid"])
def test_long_fts_query_search_preserves_recovery_probe(tmp_path, mode):
    async def scenario():
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'long-query.db'}")),
            embedding_model=_KeywordEmbeddingModel(),
        ) as contexts:
            await contexts.get("project")
            await contexts.records.create_atomic_memories(
                "project", tuple({"kind": "fact", "text": body} for body in (ADMITTED, BELOW_THRESHOLD))
            )
            memory = contexts.atomic_memory.for_scope("project")
            ranked = await memory.search(QUERY, mode=mode, limit=1, admission=AdmissionFloor(lexical_coverage=0.2))
            assert [hit.text for hit in ranked.hits] == [BELOW_THRESHOLD]
            result = await memory.search(
                QUERY, mode=mode, limit=1, recovery_admission=AdmissionFloor(lexical_coverage=0.2)
            )
            assert [hit.text for hit in result.hits] == [ADMITTED]
            assert result.hits[0].matched_by == ("text",)
            assert result.recoverable is True
            relaxed = await memory.search(
                QUERY,
                mode=mode,
                admission=AdmissionFloor(lexical_coverage=0.2),
                recovery_admission=AdmissionFloor(lexical_coverage=0.2),
            )
            assert {hit.text for hit in relaxed.hits} == {ADMITTED, BELOW_THRESHOLD}
            assert relaxed.recoverable is False

    asyncio.run(scenario())


def test_long_fts_query_related_recall_preserves_threshold(tmp_path):
    async def scenario():
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'long-related.db'}"))
        ) as contexts:
            await contexts.get("project")
            await contexts.records.create_atomic_memories(
                "project", tuple({"kind": "fact", "text": body} for body in (ADMITTED, BELOW_THRESHOLD))
            )
            async with contexts.database.transaction() as connection:
                hits = await contexts.atomic_memory.index.enumerate_related(
                    connection, "project", AtomicMemoryRelatedRequest(QUERY, AtomicMemoryIndexFilter())
                )
            assert [hit.text for hit in hits] == [ADMITTED]

    asyncio.run(scenario())
