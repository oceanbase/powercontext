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

"""SQLite related page bodies stay within the bind-variable limit."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryRelatedRank
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

PAGE_SIZE = 1000
CHUNK_SIZE = 900


class _Result:
    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self.rows = rows

    def mappings(self) -> tuple[dict[str, Any], ...]:
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def execute(self, statement, parameters: dict[str, Any] | None = None):
        identities = list((parameters or {})["body_ids"])
        self.batches.append(identities)
        return _Result(tuple(_body_row(identity) for identity in identities))


def _body_row(identity: str) -> dict[str, Any]:
    return {
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


def test_sqlite_related_page_batches_body_reads_within_the_bind_limit() -> None:
    async def scenario() -> None:
        connection = _Connection()
        ranks = tuple(
            AtomicMemoryRelatedRank(
                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=f"memory-{number:04}", revision=1),
                state_version=0,
                kind="fact",
                score=1.0,
            )
            for number in range(PAGE_SIZE)
        )
        page = await SQLiteAtomicMemoryIndex()._related_page_from_ranks(cast(Any, connection), "scope", ranks, None)
        assert page is not None
        assert [len(batch) for batch in connection.batches] == [CHUNK_SIZE, PAGE_SIZE - CHUNK_SIZE]
        assert all(len(batch) + 1 <= 999 for batch in connection.batches)
        assert tuple(hit.artifact_ref.artifact_id for hit in page.hits) == tuple(
            rank.artifact_ref.artifact_id for rank in ranks
        )

    asyncio.run(scenario())
