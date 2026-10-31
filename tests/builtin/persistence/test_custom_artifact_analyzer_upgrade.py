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

"""Upgrade persisted custom text encodings without rewriting evidence or vectors."""

import asyncio

import pytest
from sqlalchemy import select

from powercontext.artifacts.indexes import ArtifactSearchDeclaration, TextIndex, VectorIndex
from powercontext.artifacts.search import ArtifactSearchUnavailable
from powercontext.builtin.inference.models import EmbeddingResult
from powercontext.builtin.persistence import artifact_indexes as indexes
from powercontext.builtin.persistence.custom_artifacts import custom_artifact_repository
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from tests.builtin.persistence.test_artifact_indexes import PROFILE, body, declaration, register, service, store, write


class Encoder:
    profile = PROFILE

    async def embed(self, texts):
        return EmbeddingResult(vectors=((1.0, 0.0),) * len(texts))


def text_and_generated_vector():
    return ArtifactSearchDeclaration(
        version="1",
        indexes=(
            TextIndex(name="lexical", fields=("text",), projection_version="1"),
            VectorIndex(name="semantic", fields=("text",), source="text", profile=PROFILE, projection_version="1"),
        ),
        modes=("text", "vector", "hybrid"),
        default_mode="text",
    )


async def rows(connection, table):
    return [
        dict(row) for row in (await connection.execute(select(table).order_by(*table.primary_key.columns))).mappings()
    ]


def legacy_encoding(patch, version="jieba-0.42.1-words-v1"):
    patch.setattr(indexes, "TEXT_ANALYZER_VERSION", version)
    # These ASCII fixtures use the previous analyzer's literal word encoding.
    patch.setattr(indexes, "analyze_text", str.casefold)


def test_maintenance_upgrades_custom_text_preserving_vectors_and_exact_evidence(monkeypatch):
    async def run():
        async with store(model=Encoder()) as (db, builder, publisher):
            with monkeypatch.context() as previous:
                legacy_encoding(previous)
                await register(db, "reports", text_and_generated_vector())
                first = await write(db, publisher, "scope", "reports", "a", {"text": "previous localhost"})
                current = await write(db, publisher, "scope", "reports", "a", {"text": "not localhost"})
                await builder.refresh("scope", "reports")
                assert await builder.build("scope", "reports")
            vector = {"profile": PROFILE.model_dump(mode="json"), "vector": [1.0, 0.0]}
            search = service(db, "reports")
            for request in ({"query": "localhost"}, {"mode": "hybrid", "query": "localhost", "query_vector": vector}):
                with pytest.raises(ArtifactSearchUnavailable, match="analyzer upgrade"):
                    await search.search("scope", "reports", request)
            result = await search.search("scope", "reports", {"mode": "vector", "query_vector": vector})
            assert result.matches[0].artifact_ref == current.as_ref()
            with pytest.raises(ArtifactSearchUnavailable, match="analyzer upgrade"):
                await write(db, indexes.ArtifactProjectionPublisher(db), "scope", "reports", "a", {"text": "changed"})
            with pytest.raises(ArtifactSearchUnavailable, match="analyzer upgrade"):
                await builder.refresh("scope", "reports")
            async with db.transaction() as connection:
                evidence = await rows(connection, ARTIFACTS_TABLE)
                vectors = await rows(connection, indexes.VECTORS)
                definitions = await rows(connection, indexes.DEFINITIONS)
                before = (await rows(connection, indexes.STATES))[0]
                vector_ids = {row["index_def_id"] for row in vectors}
                coverage = [
                    row for row in await rows(connection, indexes.COVERAGE) if row["index_def_id"] in vector_ids
                ]
                assert not await indexes.custom_artifact_text_analyzers_current(connection)
                await indexes.upgrade_custom_artifact_text_analyzers(connection)
                assert await indexes.custom_artifact_text_analyzers_current(connection)
                assert await rows(connection, ARTIFACTS_TABLE) == evidence
                assert await rows(connection, indexes.VECTORS) == vectors
                assert [
                    row for row in await rows(connection, indexes.COVERAGE) if row["index_def_id"] in vector_ids
                ] == coverage
                after = (await rows(connection, indexes.STATES))[0]
                assert after["state"] == "ready" and after["build_id"] == before["build_id"]
                upgraded_definitions = await rows(connection, indexes.DEFINITIONS)
                assert all(row in upgraded_definitions for row in definitions)
                assert len(upgraded_definitions) == len(definitions) + 1
                assert after["manifest"] != before["manifest"]
            for query in ("localhost", "not"):
                result = await search.search("scope", "reports", {"query": query})
                assert result.matches[0].artifact_ref == current.as_ref()
            result = await search.search("scope", "reports", {"mode": "vector", "query_vector": vector})
            assert result.matches[0].artifact_ref == current.as_ref()
            async with db.transaction() as connection:
                assert (
                    await custom_artifact_repository("reports").get(connection, "scope", first.as_ref())
                ).content.content == {"text": "previous localhost"}
                snapshot = {
                    table.name: await rows(connection, table)
                    for table in (indexes.STATES, indexes.TEXTS, indexes.COVERAGE, indexes.DEFINITIONS)
                }
                await indexes.upgrade_custom_artifact_text_analyzers(connection)
                assert {
                    table.name: await rows(connection, table)
                    for table in (indexes.STATES, indexes.TEXTS, indexes.COVERAGE, indexes.DEFINITIONS)
                } == snapshot

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["pending", "building", "cleaning"])
def test_maintenance_preserves_unfinished_custom_build_and_resumes_it(monkeypatch, phase):
    async def run():
        async with store() as (db, builder, publisher):
            with monkeypatch.context() as previous:
                legacy_encoding(previous)
                await register(db, "reports", text_and_generated_vector())
                await write(db, publisher, "scope", "reports", "a", {"text": "localhost"})
                if phase != "pending":
                    await builder.refresh("scope", "reports")
                    if phase == "building":
                        with pytest.raises(ArtifactSearchUnavailable, match="embedding resource"):
                            await builder.build("scope", "reports")
                    else:
                        builder = indexes.ArtifactIndexBuilder(db, Encoder())
                        assert await builder.build("scope", "reports")
                        await builder.refresh("scope", "reports", rebuild=True)
            async with db.transaction() as connection:
                before = (await rows(connection, indexes.STATES))[0]
                assert before["state"] == phase
                await indexes.upgrade_custom_artifact_text_analyzers(connection)
                after = (await rows(connection, indexes.STATES))[0]
                assert after["state"] == phase
                assert after["build_id"] == before["build_id"]
                assert after["cleanup_targets"] == before["cleanup_targets"]
                for (old_schema, old), (new_schema, new) in zip(
                    indexes.entries(before["manifest"]), indexes.entries(after["manifest"]), strict=True
                ):
                    assert old_schema == new_schema
                    assert (old["scan_cursor"], old["scan_complete"]) == (new["scan_cursor"], new["scan_complete"])
            with pytest.raises(ArtifactSearchUnavailable):
                await service(db, "reports").search("scope", "reports", {"query": "localhost"})
            builder = indexes.ArtifactIndexBuilder(db, Encoder())
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            assert (await service(db, "reports").search("scope", "reports", {"query": "localhost"})).matches

    asyncio.run(run())


def test_explicit_custom_rebuild_registers_new_text_definition(monkeypatch):
    async def run():
        async with store() as (db, builder, publisher):
            with monkeypatch.context() as previous:
                legacy_encoding(previous)
                await register(db, "reports", declaration())
                artifact = await write(db, publisher, "scope", "reports", "a", body("not localhost", [1.0, 0.0]))
                await builder.refresh("scope", "reports")
                assert await builder.build("scope", "reports")
            await builder.refresh("scope", "reports", rebuild=True)
            with pytest.raises(ArtifactSearchUnavailable):
                await service(db, "reports").search("scope", "reports", {"query": "localhost"})
            assert await builder.build("scope", "reports")
            result = await service(db, "reports").search("scope", "reports", {"query": "not"})
            assert result.matches[0].artifact_ref == artifact.as_ref()
            async with db.transaction() as connection:
                assert await indexes.custom_artifact_text_analyzers_current(connection)
                versions = {
                    row["projection_spec"].get("analyzer_version")
                    for row in await rows(connection, indexes.DEFINITIONS)
                    if row["projection_spec"]["kind"] == "text"
                }
                assert versions == {"jieba-0.42.1-words-v1", indexes.TEXT_ANALYZER_VERSION}

    asyncio.run(run())


def test_unknown_custom_analyzer_rejects_search_and_upgrade(monkeypatch):
    async def run():
        async with store() as (db, builder, publisher):
            with monkeypatch.context() as future:
                legacy_encoding(future, "future-encoding")
                await register(db, "reports", declaration())
                await write(db, publisher, "scope", "reports", "a", body("localhost", [1.0, 0.0]))
                await builder.refresh("scope", "reports")
                assert await builder.build("scope", "reports")
            with pytest.raises(ArtifactSearchUnavailable, match="analyzer version is unsupported"):
                await service(db, "reports").search("scope", "reports", {"query": "localhost"})
            async with db.transaction() as connection:
                before = {
                    table.name: await rows(connection, table)
                    for table in (indexes.STATES, indexes.TEXTS, indexes.DEFINITIONS)
                }
                with pytest.raises(ValueError, match="analyzer version is unsupported"):
                    await indexes.custom_artifact_text_analyzers_current(connection)
            with pytest.raises(ValueError, match="analyzer version is unsupported"):
                async with db.transaction() as connection:
                    await indexes.upgrade_custom_artifact_text_analyzers(connection)
            async with db.transaction() as connection:
                assert {
                    table.name: await rows(connection, table)
                    for table in (indexes.STATES, indexes.TEXTS, indexes.DEFINITIONS)
                } == before

    asyncio.run(run())
