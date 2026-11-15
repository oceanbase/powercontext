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

"""Lexical admission keeps distinct whole terms and same-row eligibility on every backend."""

import asyncio
import os
from collections.abc import Iterator
from dataclasses import replace

import pytest
from sqlalchemy import Engine, event, update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.search import AdmissionFloor
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryProjection,
    AtomicMemoryProjectionPublisher,
    AtomicMemoryRelatedRequest,
    AtomicMemorySearchRequest,
)
from powercontext.builtin.persistence.migrations.pinned import finish, pinned
from powercontext.builtin.tags import TagFilter
from tests.e2e.atomic_memory_migration_backend import MigrationBackend, migration_index, migration_profile
from tests.e2e.test_atomic_memory_vector_index import Embedding, _initialize_related_authority


@pytest.fixture(autouse=True)
def require_oceanbase_fts_path(request: pytest.FixtureRequest) -> Iterator[None]:
    """CI must exercise successful business queries, not just the capability probe."""
    expected = os.environ.get("POWERCONTEXT_TEST_ATOMIC_MEMORY_FTS_PATH")
    if expected is None:
        yield
        return
    expected = getattr(request, "param", expected)
    assert expected in {"native", "legacy"}, f"Unknown OceanBase FTS path: {expected}"
    observed: set[str] = set()

    def record_path(connection, cursor, statement, parameters, context, executemany):
        if "pc_atomic_memory_current" not in statement or not any(
            "fts_query" in bound for bound in getattr(context, "compiled_parameters", ()) or ()
        ):
            return
        if "MATCH(searchable_text^1" in statement:
            observed.add("native")
        elif "LOCATE(BINARY" in statement:
            observed.add("legacy")

    event.listen(Engine, "after_cursor_execute", record_path)
    try:
        yield
    finally:
        event.remove(Engine, "after_cursor_execute", record_path)
    assert observed == {expected}, f"Expected successful {expected} FTS queries, observed {sorted(observed)}"


def test_fts_coverage_keeps_distinct_terms_and_filters_before_limits(migration_backend: MigrationBackend):
    async def scenario():
        index = migration_index(migration_backend.config)
        preparer = AtomicMemoryProjectionPublisher(index)
        terms = tuple(f"q{number:04}" for number in range(16))
        # Repeating a query term changes neither its weight in admission nor the denominator.
        query = " ".join((*terms, *(terms[0] for _ in range(8))))
        admitted = " ".join(terms[-4:])
        recovery = " ".join(terms[:2] * 20)
        substring = " ".join((terms[0],) * 20 + tuple(f"{term}suffix" for term in terms[1:]))
        all_terms = " ".join(terms * 20)
        rows = (
            ("scope", "admitted-a", "fact", ("keep",), admitted),
            ("scope", "admitted-b", "fact", ("keep",), admitted),
            ("scope", "recoverable", "fact", ("keep",), recovery),
            ("scope", "substrings", "fact", ("keep",), substring),
            ("scope", "other-kind", "preference", ("keep",), all_terms),
            ("scope", "other-tag", "fact", ("other",), all_terms),
            ("foreign", "foreign", "fact", ("keep",), all_terms),
        )
        async with migration_profile(migration_backend.config) as profile:
            async with profile.database.transaction() as connection:
                if connection.dialect.name == "mysql":
                    # Historical native migration retains its synchronous connection and maintenance lock.
                    await connection.run_sync(lambda sync: finish(index.initialize(pinned(sync))))
                else:
                    await index.initialize(connection)
                await _initialize_related_authority(connection)
                for scope, identity, kind, tags, body in rows:
                    prepared = await preparer.prepare(AtomicMemoryContent(kind=kind, text=body))
                    await index.replace_many(
                        connection,
                        scope,
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=identity, revision=1),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=tags,
                            ),
                        ),
                    )
            filters = AtomicMemoryIndexFilter(kind="fact", tag_filter=TagFilter(tags=("keep",), match="all"))
            request = AtomicMemorySearchRequest(query, filters, limit=1)
            related = AtomicMemoryRelatedRequest(query, filters)
            lower = AdmissionFloor(lexical_coverage=0.125)
            async with profile.database.transaction() as connection:
                bounded = (await index.search(connection, "scope", request)).fts
                assert [hit.artifact_ref.artifact_id for hit in bounded] == ["admitted-a"]
                complete, _ = await index.enumerate_related_with_heads(connection, "scope", related)
                assert [hit.artifact_ref.artifact_id for hit in complete] == ["admitted-a", "admitted-b"]
                assert bounded[0].channel_scores == complete[0].channel_scores
                assert await index.probe_recoverable(connection, "scope", request, lower)
                relaxed, _ = await index.enumerate_related_with_heads(
                    connection, "scope", replace(related, admission=lower)
                )
                assert {hit.artifact_ref.artifact_id for hit in relaxed} == {
                    "admitted-a",
                    "admitted-b",
                    "recoverable",
                }
                # A one-term query still admits a single match, including after deduplication.
                short = replace(request, query="q0000 q0000", limit=20)
                assert {
                    hit.artifact_ref.artifact_id for hit in (await index.search(connection, "scope", short)).fts
                } == {
                    "recoverable",
                    "substrings",
                }
                await index.delete(connection, "scope", "recoverable")
                assert not await index.probe_recoverable(connection, "scope", request, lower)

    asyncio.run(scenario())


def test_hybrid_coverage_keeps_vectors_and_validates_rows_outside_lexical_matches(
    migration_backend: MigrationBackend,
):
    async def scenario():
        model = Embedding()
        index = migration_index(migration_backend.config, model.profile)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        async with migration_profile(migration_backend.config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                for identity, kind, body in (
                    ("both", "fact", "alpha beta gamma"),
                    ("vector-only", "preference", "unrelated archive"),
                ):
                    prepared = await preparer.prepare(AtomicMemoryContent(kind=kind, text=body))
                    await index.replace_many(
                        connection,
                        "scope",
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=identity, revision=1),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=(),
                            ),
                        ),
                    )
            request = AtomicMemorySearchRequest(
                query="alpha beta gamma",
                filters=AtomicMemoryIndexFilter(),
                mode="hybrid",
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=model.profile,
            )
            related = AtomicMemoryRelatedRequest(
                query=request.query,
                filters=request.filters,
                mode=request.mode,
                query_vector=request.query_vector,
                embedding_profile=request.embedding_profile,
                max_distance=0.1,
            )
            unreachable = AdmissionFloor(lexical_min_matched_terms=5)
            async with profile.database.transaction() as connection:
                complete, _ = await index.enumerate_related_with_heads(connection, "scope", related)
                assert len(complete) == 2
                assert {hit.artifact_ref.artifact_id: set(hit.channel_scores or {}) for hit in complete} == {
                    "both": {"text", "vector"},
                    "vector-only": {"vector"},
                }
                for mode in ("fts", "hybrid"):
                    strict = replace(request, mode=mode, admission=unreachable)
                    channels = await index.search(connection, "scope", strict)
                    assert not channels.fts
                    strict_complete, _ = await index.enumerate_related_with_heads(
                        connection, "scope", replace(related, mode=mode, admission=unreachable)
                    )
                    expected = {"both", "vector-only"} if mode == "hybrid" else set()
                    assert {hit.artifact_ref.artifact_id for hit in channels.vector} == expected
                    assert {hit.artifact_ref.artifact_id for hit in strict_complete} == expected
                    assert all(set(hit.channel_scores or {}) == {"vector"} for hit in strict_complete)
                    assert not await index.probe_recoverable(
                        connection, "scope", strict, AdmissionFloor(lexical_min_matched_terms=4)
                    )
                    assert await index.probe_recoverable(
                        connection, "scope", strict, AdmissionFloor(lexical_min_matched_terms=3)
                    )

                await connection.execute(
                    update(index.table)
                    .where(index.table.c.artifact_id == "vector-only")
                    .values(embedding=None, profile_fingerprint=None, embedding_input_hash=None)
                )
                bounded = await index.search(connection, "scope", request)
                assert [hit.artifact_ref.artifact_id for hit in bounded.fts] == ["both"]
                assert [hit.artifact_ref.artifact_id for hit in bounded.vector] == ["both"]
                with pytest.raises(AtomicMemoryIndexError, match="incomplete"):
                    await index.enumerate_related_with_heads(connection, "scope", related)
                with pytest.raises(AtomicMemoryIndexError, match="incomplete"):
                    await index.probe_recoverable(connection, "scope", request, AdmissionFloor())
                # Readiness covers eligible rows, so excluding the corrupt row makes the request valid.
                filtered = AtomicMemoryIndexFilter(kind="fact")
                complete, _ = await index.enumerate_related_with_heads(
                    connection, "scope", replace(related, filters=filtered)
                )
                assert [hit.artifact_ref.artifact_id for hit in complete] == ["both"]
                assert not await index.probe_recoverable(
                    connection, "scope", replace(request, filters=filtered), AdmissionFloor()
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("require_oceanbase_fts_path", ["legacy"], indirect=True)
@pytest.mark.parametrize("word", ["com", "www", "the"])
def test_fts_coverage_counts_analyzer_terms_omitted_by_space_parser(migration_backend: MigrationBackend, word: str):
    async def scenario():
        index = migration_index(migration_backend.config)
        preparer = AtomicMemoryProjectionPublisher(index)
        async with migration_profile(migration_backend.config) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                for identity, body in (("admitted", f"{word} alpha"), ("insufficient", "alpha")):
                    prepared = await preparer.prepare(AtomicMemoryContent(kind="fact", text=body))
                    await index.replace_many(
                        connection,
                        "scope",
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=identity, revision=1),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=(),
                            ),
                        ),
                    )
            # Quoted identifiers remain Analyzer terms even if they are ordinary stopwords.
            query = f'"{word}" alpha beta'
            request = AtomicMemorySearchRequest(query, AtomicMemoryIndexFilter(), limit=1)
            related = AtomicMemoryRelatedRequest(query, AtomicMemoryIndexFilter())
            strict = replace(request, admission=AdmissionFloor(lexical_min_matched_terms=3))
            async with profile.database.transaction() as connection:
                hits = (await index.search(connection, "scope", request)).fts
                assert [hit.artifact_ref.artifact_id for hit in hits] == ["admitted"]
                complete, _ = await index.enumerate_related_with_heads(connection, "scope", related)
                assert [hit.artifact_ref.artifact_id for hit in complete] == ["admitted"]
                assert not (await index.search(connection, "scope", strict)).fts
                assert await index.probe_recoverable(connection, "scope", strict, AdmissionFloor())
                await index.delete(connection, "scope", "admitted")
                assert not await index.probe_recoverable(connection, "scope", strict, AdmissionFloor())

    asyncio.run(scenario())
