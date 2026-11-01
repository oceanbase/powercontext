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

"""Actual SQLite search, exact evidence and atomic projection publication."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import insert, select, update

from powercontext.artifacts.indexes import ArtifactSearchDeclaration, FragmentProjection, TextIndex, VectorIndex
from powercontext.artifacts.search import ArtifactSearchUnavailable, ArtifactSearchUnsupported
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.inference.models import EmbeddingResult
from powercontext.builtin.persistence.artifact_governance import ArtifactGovernanceRepository, ArtifactLifecycleState
from powercontext.builtin.persistence.artifact_indexes import (
    SCHEMAS,
    STATES,
    ArtifactIndexBuilder,
    ArtifactProjectionPublisher,
)
from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.persistence.custom_artifacts import StoredArtifactContent, custom_artifact_repository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.artifact_search import ArtifactSearchService
from powercontext.builtin.runtime.custom_artifact_search import CustomArtifactSearcher

PROFILE = EmbeddingProfile(profile_id="features-v1", model="fixture", dimension=2, normalization="none")


def declaration(*, version="1", vector_field="vector", profile=PROFILE):
    return ArtifactSearchDeclaration(
        version=version,
        indexes=(
            TextIndex(name="lexical", fields=("text",), projection_version="1"),
            VectorIndex(
                name="visual",
                projection_version=version,
                source="content",
                profile=profile,
                vector_field=vector_field,
                profile_field="profile",
                fragments=FragmentProjection(array_field="fragments", id_field="id"),
            ),
        ),
        modes=("text", "vector", "hybrid"),
        default_mode="text",
    )


@asynccontextmanager
async def store(config=None, model=None):
    async with SQLiteProfile.open(config or SQLiteConfig(), tables=BUILTIN_TABLES) as storage:
        yield (
            storage.database,
            ArtifactIndexBuilder(storage.database, model),
            ArtifactProjectionPublisher(storage.database, model),
        )


async def register(db, family, search):
    durable = {"family": family, "schema_version": "1", "search_fields": [], "search": search.model_dump(mode="json")}
    async with db.transaction() as connection:
        existing = await connection.scalar(select(SCHEMAS.c.family).where(SCHEMAS.c.family == family))
        if existing is None:
            await connection.execute(insert(SCHEMAS).values(family=family, schema_version="1", declaration=durable))
        else:
            await connection.execute(update(SCHEMAS).where(SCHEMAS.c.family == family).values(declaration=durable))


async def write(db, publisher, scope, family, identity, content):
    prepared = await publisher.prepare(scope, family, "1", content)
    repository = custom_artifact_repository(family, prepared={identity: prepared})
    async with db.transaction() as connection:
        draft = RepositoryArtifactDraft(
            family=family, content=StoredArtifactContent(schema_version="1", content=content)
        )
        try:
            previous = await repository.latest(connection, scope, family, identity)
        except RepositoryNotFoundError:
            return await repository.create(connection, scope, identity, draft)
        return await repository.revise(connection, scope, previous, draft)


def body(text, *vectors, profile=PROFILE):
    return {
        "text": text,
        "fragments": [
            {"id": f"f{i}", "vector": vector, "profile": profile.model_dump(mode="json")}
            for i, vector in enumerate(vectors)
        ],
    }


def service(db, family, model=None):
    result = ArtifactSearchService(known_families=(family,))
    result.register(CustomArtifactSearcher(db, family, model))
    return result


def test_missing_vector_extension_keeps_text_search_and_content_writes_available(monkeypatch, tmp_path):
    import sqlite_vec

    async def run():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'vectors.db'}")
        async with store(config) as (db, builder, publisher):
            await register(db, "reports", declaration())
            await write(db, publisher, "scope", "reports", "a", body("数据库", [1, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")

        monkeypatch.setattr(sqlite_vec, "loadable_path", lambda: str(tmp_path / "missing-sqlite-vec"))
        async with store(config) as (db, _, publisher):
            search = service(db, "reports")
            with pytest.raises(ArtifactSearchUnavailable):
                await search.search(
                    "scope",
                    "reports",
                    {
                        "mode": "vector",
                        "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]},
                    },
                )
            result = await search.search("scope", "reports", {"mode": "text", "query": "数据库"})
            assert [artifact.artifact_id for artifact in result.artifacts] == ["a"]
            revised = await write(db, publisher, "scope", "reports", "a", body("数据库 restored", [0, 1]))
            assert revised.revision == 2

    asyncio.run(run())


def test_exact_vectors_collapse_fragments_before_limit_and_preserve_scope_history():
    async def run():
        async with store() as (db, builder, publisher):
            for family in ("reports", "other"):
                await register(db, family, declaration())
            await write(db, publisher, "scope", "reports", "a", body("数据库 timeout", *([[0, 0]] * 8)))
            await write(db, publisher, "scope", "reports", "b", body("数据库 normal", [1, 0]))
            await write(db, publisher, "foreign", "reports", "a", body("数据库 hidden", [0, 0]))
            await write(db, publisher, "scope", "other", "a", body("数据库 hidden", [0, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(db, "reports")
            request = {
                "mode": "vector",
                "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [0, 0]},
                "limit": 2,
                "include_scores": True,
            }
            result = await search.search("scope", "reports", request)
            assert [artifact.artifact_id for artifact in result.artifacts] == ["a", "b"]
            assert result.matches[0].channel_scores["vector"].raw == 0
            old = result.artifacts[0].as_ref()
            hybrid = await search.search("scope", "reports", {**request, "mode": "hybrid", "query": "数据库"})
            assert {artifact.artifact_id for artifact in hybrid.artifacts} == {"a", "b"}
            assert all(0 <= hit.retrieval_score <= 1 for hit in hybrid.matches)
            await write(db, publisher, "scope", "reports", "a", body("recovered"))
            current = await search.search("scope", "reports", request)
            assert [artifact.artifact_id for artifact in current.artifacts] == ["b"]
            async with db.transaction() as connection:
                historical = await custom_artifact_repository("reports").get(connection, "scope", old)
                assert len(historical.content.content["fragments"]) == 8
            assert not (await search.search("scope", "reports", {"query": "timeout"})).matches

    asyncio.run(run())


def test_initial_build_catches_insert_before_scan_cursor_and_publishes_atomically():
    async def run():
        async with store() as (db, builder, publisher):

            class ConcurrentEncoder:
                profile = PROFILE
                inserted = False

                async def embed(self, texts):
                    if not self.inserted:
                        self.inserted = True
                        # The text generation has finished its scan. This new identity is
                        # before its cursor and must be picked up by invalidation replay.
                        await write(db, publisher, "scope", "reports", "a", {"text": "original", "title": "new-index"})
                    return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

            builder = ArtifactIndexBuilder(db, ConcurrentEncoder())
            initial = ArtifactSearchDeclaration(
                version="1",
                indexes=(
                    TextIndex(name="lexical", fields=("title",), projection_version="1"),
                    VectorIndex(
                        name="semantic", projection_version="1", source="text", profile=PROFILE, fields=("title",)
                    ),
                ),
                modes=("text",),
                default_mode="text",
            )
            await register(db, "reports", initial)
            await write(db, publisher, "scope", "reports", "z", {"text": "original", "title": "new-index"})
            await builder.refresh("scope", "reports")
            search = service(db, "reports")
            with pytest.raises(ArtifactSearchUnavailable):
                await search.search("scope", "reports", {"query": "original"})
            async with db.transaction() as connection:
                await connection.exec_driver_sql(
                    "CREATE TRIGGER fail_release BEFORE UPDATE OF state ON pc_artifact_search_states WHEN new.state='ready' BEGIN SELECT RAISE(ABORT, 'release failure'); END"
                )
            with pytest.raises(Exception, match="release failure"):
                await builder.build("scope", "reports")
            async with db.transaction() as connection:
                assert (
                    await connection.scalar(
                        select(STATES.c.state).where(STATES.c.scope_id == "scope", STATES.c.family == "reports")
                    )
                    == "building"
                )
                await connection.exec_driver_sql("DROP TRIGGER fail_release")
            with pytest.raises(ArtifactSearchUnavailable):
                await search.search("scope", "reports", {"query": "original"})
            assert await builder.build("scope", "reports")
            assert not (await search.search("scope", "reports", {"query": "original"})).matches
            assert len((await search.search("scope", "reports", {"query": "new-index"})).matches) == 2

    asyncio.run(run())


def test_text_vectors_use_declared_encoder_and_unready_profile_never_falls_back():
    class Encoder:
        profile = PROFILE

        async def embed(self, texts):
            return EmbeddingResult(vectors=tuple((float(len(text)), 1.0) for text in texts))

    async def run():
        model = Encoder()
        async with store(model=model) as (db, builder, publisher):
            search_config = ArtifactSearchDeclaration(
                version="1",
                indexes=(
                    VectorIndex(
                        name="semantic", projection_version="1", source="text", profile=PROFILE, fields=("text",)
                    ),
                ),
                modes=("vector",),
                default_mode="vector",
            )
            await register(db, "reports", search_config)
            await write(db, publisher, "scope", "reports", "a", {"text": "数据库"})
            search = service(db, "reports", model)
            with pytest.raises(ArtifactSearchUnavailable):
                await search.search("scope", "reports", {"query": "数据库"})
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            assert (await search.search("scope", "reports", {"query": "数据库"})).artifacts[0].artifact_id == "a"
            with pytest.raises(ArtifactSearchUnavailable):
                await service(db, "reports").search("scope", "reports", {"query": "数据库"})
            assert (
                await service(db, "reports").search(
                    "scope", "reports", {"query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [3, 1]}}
                )
            ).matches

    asyncio.run(run())


def test_query_errors_are_unsupported_and_declared_indexes_are_unavailable_during_repair():
    async def run():
        async with store() as (db, builder, publisher):
            await register(db, "reports", declaration())
            await write(db, publisher, "scope", "reports", "a", body("数据库", [0, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(db, "reports")
            vector = {"profile": PROFILE.model_dump(mode="json"), "vector": [0, 0]}
            invalid = (
                {"query": "数据库", "mode": "text", "indexes": {"vector": ["visual"]}},
                {"query": "数据库", "indexes": {"text": []}},
                {
                    "query": "数据库",
                    "mode": "hybrid",
                    "query_vector": vector,
                    "fusion": {"method": "rrf", "params": {"weights": {"text": 0, "vector": 0}}},
                },
                {"mode": "vector", "query_vector": {**vector, "vector": [0]}},
                {"mode": "vector", "query": "数据库", "query_vector": vector},
            )
            for request in invalid:
                with pytest.raises(ArtifactSearchUnsupported):
                    await search.search("scope", "reports", request)
            await builder.refresh("scope", "reports", rebuild=True)
            with pytest.raises(ArtifactSearchUnavailable):
                await search.search("scope", "reports", {"query": "数据库", "indexes": {"text": ["lexical"]}})
            with pytest.raises(ArtifactSearchUnavailable):
                await search.search("scope", "reports", {"query": "数据库"})

    asyncio.run(run())


def test_governance_removal_restore_and_rebuild_preserve_exact_history():
    async def run():
        async with store() as (db, builder, publisher):
            await register(db, "reports", declaration())
            original = await write(db, publisher, "scope", "reports", "a", body("数据库", [0, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(db, "reports")
            async with db.transaction() as connection:
                state = await ArtifactGovernanceRepository().transition(
                    connection, "scope", "reports", "a", 0, ArtifactLifecycleState.DEPRECATED, None
                )
            assert not (await search.search("scope", "reports", {"query": "数据库"})).matches
            # Restore requires all serving projections to be prepared outside its transaction.
            with pytest.raises(ValueError, match="prepared before"):
                async with db.transaction() as connection:
                    await ArtifactGovernanceRepository().transition(
                        connection,
                        "scope",
                        "reports",
                        "a",
                        state.governance_generation,
                        ArtifactLifecycleState.ACTIVE,
                        None,
                    )
            prepared = await publisher.prepare("scope", "reports", "1", original.content.content)
            async with db.transaction() as connection:
                governance = ArtifactGovernanceRepository(prepared_indexes={("scope", "reports", "a"): prepared})
                state = await governance.transition(
                    connection,
                    "scope",
                    "reports",
                    "a",
                    state.governance_generation,
                    ArtifactLifecycleState.ACTIVE,
                    None,
                )
            assert (await search.search("scope", "reports", {"query": "数据库"})).artifacts[0].revision == 1
            await builder.refresh("scope", "reports", rebuild=True)
            async with db.transaction() as connection:
                await ArtifactGovernanceRepository().transition(
                    connection,
                    "scope",
                    "reports",
                    "a",
                    state.governance_generation,
                    ArtifactLifecycleState.RETIRED,
                    None,
                )
            assert await builder.build("scope", "reports")
            assert not (await search.search("scope", "reports", {"query": "数据库"})).matches
            async with db.transaction() as connection:
                assert (
                    await custom_artifact_repository("reports").get(connection, "scope", original.as_ref())
                ).content == original.content

    asyncio.run(run())


def test_incompatible_content_vector_write_preserves_search_and_history():
    async def run():
        async with store() as (db, builder, publisher):
            await register(db, "reports", declaration())
            first = await write(db, publisher, "scope", "reports", "a", body("数据库", [1, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            next_profile = PROFILE.model_copy(update={"profile_id": "features-v2", "model": "second"})
            with pytest.raises(ValueError, match="profile differs"):
                await write(db, publisher, "scope", "reports", "a", body("数据库", [0, 1], profile=next_profile))
            search = service(db, "reports")
            request = {
                "mode": "vector",
                "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]},
            }
            assert (await search.search("scope", "reports", request)).artifacts[0].revision == 1
            async with db.transaction() as connection:
                assert (
                    await custom_artifact_repository("reports").get(connection, "scope", first.as_ref())
                ).content == first.content

    asyncio.run(run())


def test_initial_content_vector_validation_rejects_bad_record_without_requiring_index_resources():
    async def run():
        async with store() as (db, _, publisher):
            base = declaration()
            search_config = ArtifactSearchDeclaration(
                version="1",
                indexes=(
                    *base.indexes,
                    VectorIndex(
                        name="semantic", fields=("text",), source="text", profile=PROFILE, projection_version="1"
                    ),
                ),
                modes=base.modes,
                default_mode="text",
                defaults={
                    "text": {"text": ("lexical",)},
                    "vector": {"vector": ("visual",)},
                    "hybrid": {"text": ("lexical",), "vector": ("visual",)},
                },
            )
            await register(db, "reports", search_config)
            wrong = PROFILE.model_copy(update={"profile_id": "features-v2", "model": "second"})
            with pytest.raises(ValueError, match="profile differs"):
                await write(db, publisher, "scope", "reports", "a", body("invalid", [1, 0], profile=wrong))
            async with db.transaction() as connection:
                with pytest.raises(RepositoryNotFoundError):
                    await custom_artifact_repository("reports").latest(connection, "scope", "reports", "a")

            # The family is still unready: neither the text encoder nor physical vector spaces are needed for this write.
            stored = await write(db, publisher, "scope", "reports", "a", body("valid", [1, 0]))
            assert stored.revision == 1

    asyncio.run(run())


def test_repair_content_vector_validation_rejects_bad_revision_across_ready_transition():
    async def run():
        async with store() as (db, builder, publisher):
            await register(db, "reports", declaration())
            original = await write(db, publisher, "scope", "reports", "a", body("original", [1, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            await builder.refresh("scope", "reports", rebuild=True)

            with pytest.raises(ValueError, match="dimension"):
                await write(db, publisher, "scope", "reports", "a", body("invalid dimension", [1]))
            async with db.transaction() as connection:
                latest = await custom_artifact_repository("reports").latest(connection, "scope", "reports", "a")
                assert latest.revision == original.revision
                assert latest.content == original.content

            duplicate = body("duplicate fragments", [1, 0], [0, 1])
            duplicate["fragments"][1]["id"] = duplicate["fragments"][0]["id"]
            with pytest.raises(ValueError, match="identities must be unique"):
                await write(db, publisher, "scope", "reports", "a", duplicate)

            changed = body("changed while repairing", [0, 1])
            prepared = await publisher.prepare("scope", "reports", "1", changed)
            assert await builder.build("scope", "reports")
            repository = custom_artifact_repository("reports", prepared={"a": prepared})
            draft = RepositoryArtifactDraft(
                family="reports", content=StoredArtifactContent(schema_version="1", content=changed)
            )
            with pytest.raises(ValueError):
                async with db.transaction() as connection:
                    await repository.revise(connection, "scope", original, draft)
            async with db.transaction() as connection:
                latest = await repository.latest(connection, "scope", "reports", "a")
                assert latest.revision == original.revision
                assert latest.content == original.content

    asyncio.run(run())


def test_vector_profile_is_validated_against_requested_index():
    async def run():
        async with store() as (db, builder, _):
            alternate = PROFILE.model_copy(update={"profile_id": "alternate"})
            initial = declaration()
            declared = ArtifactSearchDeclaration(
                version="1",
                indexes=(
                    *initial.indexes,
                    initial.indexes[1].model_copy(update={"name": "alternate", "profile": alternate}),
                ),
                modes=initial.modes,
                default_mode="text",
                defaults={"vector": {"vector": ("visual",)}, "hybrid": {"text": ("lexical",), "vector": ("visual",)}},
            )
            await register(db, "reports", declared)
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(db, "reports")
            for selected, profile in (("visual", PROFILE), ("alternate", alternate)):
                request = {
                    "mode": "vector",
                    "indexes": {"vector": [selected]},
                    "query_vector": {"profile": profile.model_dump(mode="json"), "vector": [0, 0]},
                }
                assert not (await search.search("scope", "reports", request)).matches
                other_profile = alternate if selected == "visual" else PROFILE
                request["query_vector"]["profile"] = other_profile.model_dump(mode="json")
                with pytest.raises(ArtifactSearchUnsupported):
                    await search.search("scope", "reports", request)

    asyncio.run(run())


def test_named_indexes_share_build_and_space_without_cross_matching():
    async def run():
        async with store() as (db, builder, publisher):
            search_config = ArtifactSearchDeclaration(
                version="1",
                indexes=(
                    TextIndex(name="title", fields=("title",), projection_version="1"),
                    TextIndex(name="body", fields=("text",), projection_version="1"),
                    VectorIndex(
                        name="near",
                        source="content",
                        vector_field="v1",
                        profile_field="profile",
                        profile=PROFILE,
                        projection_version="1",
                    ),
                    VectorIndex(
                        name="far",
                        source="content",
                        vector_field="v2",
                        profile_field="profile",
                        profile=PROFILE,
                        projection_version="1",
                    ),
                ),
                modes=("text", "vector"),
                default_mode="text",
                defaults={"text": {"text": ("title",)}, "vector": {"vector": ("near",)}},
            )
            await register(db, "reports", search_config)
            await write(
                db,
                publisher,
                "scope",
                "reports",
                "a",
                {
                    "title": "标题",
                    "text": "正文",
                    "v1": [0, 0],
                    "v2": [10, 0],
                    "profile": PROFILE.model_dump(mode="json"),
                },
            )
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(db, "reports")
            for name, query, expected in (("title", "正文", False), ("body", "正文", True)):
                assert (
                    bool(
                        (await search.search("scope", "reports", {"query": query, "indexes": {"text": [name]}})).matches
                    )
                    is expected
                )
            for name, distance in (("near", 0), ("far", 10)):
                found = await search.search(
                    "scope",
                    "reports",
                    {
                        "mode": "vector",
                        "indexes": {"vector": [name]},
                        "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [0, 0]},
                        "include_scores": True,
                    },
                )
                assert found.matches[0].channel_scores["vector"].raw == distance

    asyncio.run(run())


def test_inflight_content_preparation_obeys_current_build_state():
    async def run():
        async with store() as (db, builder, publisher):
            await register(db, "reports", declaration())
            original = await write(db, publisher, "scope", "reports", "a", body("before", [0, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            content = body("recovered", [1, 0])
            prepared = await publisher.prepare("scope", "reports", "1", content)
            await builder.refresh("scope", "reports", rebuild=True)
            # Preparation started when ready; commit now uses only the change log.
            repository = custom_artifact_repository("reports", prepared={"a": prepared})
            draft = RepositoryArtifactDraft(
                family="reports", content=StoredArtifactContent(schema_version="1", content=content)
            )
            async with db.transaction() as connection:
                revised = await repository.revise(
                    connection, "scope", await repository.get(connection, "scope", original.as_ref()), draft
                )
            pending = await publisher.prepare("scope", "reports", "1", body("too-late", [2, 0]))
            assert await builder.build("scope", "reports")
            repository = custom_artifact_repository("reports", prepared={"a": pending})
            with pytest.raises(ValueError, match="prepare again"):
                async with db.transaction() as connection:
                    await repository.revise(
                        connection,
                        "scope",
                        revised,
                        RepositoryArtifactDraft(
                            family="reports",
                            content=StoredArtifactContent(schema_version="1", content=body("too-late", [2, 0])),
                        ),
                    )
            assert (await service(db, "reports").search("scope", "reports", {"query": "recovered"})).artifacts[
                0
            ].revision == 2

    asyncio.run(run())


def test_superseded_build_cannot_write_clean_or_publish_and_cleanup_resumes():
    async def run():
        from powercontext.builtin.persistence.artifact_indexes import COVERAGE, TEXTS, entries

        async with store() as (db, builder, publisher):
            await register(db, "reports", declaration())
            for scope, family in (("scope", "reports"), ("other-scope", "reports"), ("scope", "other-family")):
                if family != "reports":
                    await register(db, family, declaration())
                await write(db, publisher, scope, family, "a", body("保留", [1, 0]))
                await builder.refresh(scope, family)
                assert await builder.build(scope, family)
            await builder.refresh("scope", "reports", rebuild=True)
            async with db.transaction() as connection:
                old = (
                    (
                        await connection.execute(
                            select(STATES).where(STATES.c.scope_id == "scope", STATES.c.family == "reports")
                        )
                    )
                    .mappings()
                    .one()
                )
            assert not await builder.clean("scope", "reports", old["build_id"], limit=1)
            # Restart repair with only part of the old rows removed.
            await builder.refresh("scope", "reports", rebuild=True)
            assert not await builder.clean("scope", "reports", old["build_id"])
            definition = entries(old["manifest"])[0][1]["index_def_id"]
            assert not await builder._project_current("scope", "reports", old["build_id"], definition, "a")
            assert not await builder._validate_and_publish("scope", "reports", old["build_id"])
            restarted = ArtifactIndexBuilder(db)
            assert await restarted.build("scope", "reports")
            for scope, family in (("scope", "reports"), ("other-scope", "reports"), ("scope", "other-family")):
                assert (await service(db, family).search(scope, family, {"query": "保留"})).matches
            async with db.transaction() as connection:
                current = (
                    (
                        await connection.execute(
                            select(STATES).where(STATES.c.scope_id == "scope", STATES.c.family == "reports")
                        )
                    )
                    .mappings()
                    .one()
                )
                assert current["cleanup_targets"] == []
                for table in (TEXTS, COVERAGE):
                    assert not (
                        await connection.execute(
                            select(table).where(
                                table.c.scope_id == "scope",
                                table.c.family == "reports",
                                table.c.build_id != current["build_id"],
                            )
                        )
                    ).all()
                # A crashed final validator must not leave the build permanently occupied.
            await restarted.refresh("scope", "reports", rebuild=True)
            async with db.transaction() as connection:
                await connection.execute(
                    update(STATES)
                    .where(STATES.c.scope_id == "scope", STATES.c.family == "reports")
                    .values(validation_token="interrupted")  # noqa: S106 - Persisted build fencing token, not a credential.
                )
            assert await ArtifactIndexBuilder(db).build("scope", "reports")

    asyncio.run(run())


def test_repair_restart_rejects_an_inflight_encoder_and_keeps_new_build_unavailable():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Encoder:
            profile = PROFILE

            async def embed(self, texts):
                entered.set()
                await release.wait()
                return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

        async with store(model=Encoder()) as (db, builder, publisher):
            config = ArtifactSearchDeclaration(
                version="1",
                indexes=(
                    VectorIndex(
                        name="semantic", fields=("text",), source="text", profile=PROFILE, projection_version="1"
                    ),
                ),
                modes=("vector",),
                default_mode="vector",
            )
            await register(db, "reports", config)
            await write(db, publisher, "scope", "reports", "a", {"text": "数据库"})
            await builder.refresh("scope", "reports")
            old_task = asyncio.create_task(builder.build("scope", "reports"))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await builder.refresh("scope", "reports", rebuild=True)
                release.set()
                assert not await old_task
                request = {"query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]}}
                with pytest.raises(ArtifactSearchUnavailable):
                    await service(db, "reports").search("scope", "reports", request)
                assert await builder.build("scope", "reports")
                assert (await service(db, "reports").search("scope", "reports", request)).matches
            finally:
                release.set()
                await old_task

    asyncio.run(run())


@pytest.mark.parametrize("during_build", [False, True])
def test_query_policy_update_preserves_build_and_does_not_reencode(during_build):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Encoder:
            profile = PROFILE
            encoded = False

            async def embed(self, texts):
                assert not self.encoded, "query policy must not re-encode stored content"
                self.encoded = True
                entered.set()
                await release.wait()
                return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

        async with store(model=Encoder()) as (db, builder, publisher):
            initial = ArtifactSearchDeclaration(
                version="1",
                indexes=(
                    TextIndex(name="title", fields=("title",), projection_version="1"),
                    TextIndex(name="body", fields=("text",), projection_version="1"),
                    VectorIndex(
                        name="semantic", fields=("text",), source="text", profile=PROFILE, projection_version="1"
                    ),
                ),
                modes=("text", "vector"),
                default_mode="text",
                defaults={"text": {"text": ("title",)}, "vector": {"vector": ("semantic",)}},
            )
            await register(db, "reports", initial)
            await write(db, publisher, "scope", "reports", "a", {"title": "标题", "text": "正文"})
            await builder.refresh("scope", "reports")
            build = asyncio.create_task(builder.build("scope", "reports"))
            try:
                async with asyncio.timeout(5):
                    await entered.wait()
                if not during_build:
                    release.set()
                    assert await build
                async with db.transaction() as connection:
                    before = dict((await connection.execute(select(STATES))).mappings().one())
                changed = initial.model_copy(
                    update={
                        "default_mode": "vector",
                        "defaults": {"text": {"text": ("body",)}, "vector": {"vector": ("semantic",)}},
                        "candidate_limit": 17,
                        "rrf_k": 11,
                        "text_weight": 2,
                        "vector_weight": 3,
                    }
                )
                await register(db, "reports", changed)
                await builder.refresh("scope", "reports")
                async with db.transaction() as connection:
                    after = dict((await connection.execute(select(STATES))).mappings().one())
                assert after == before  # The build identity, progress and readiness must survive policy changes.
                release.set()
                assert await build
                assert await builder.build("scope", "reports")
                search = service(db, "reports")
                result = await search.search(
                    "scope",
                    "reports",
                    {
                        "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]},
                    },
                )
                assert [item.artifact_id for item in result.artifacts] == ["a"]
                assert (await search.search("scope", "reports", {"mode": "text", "query": "正文"})).artifacts
                assert not (await search.search("scope", "reports", {"mode": "text", "query": "标题"})).artifacts
            finally:
                release.set()
                await build

    asyncio.run(run())


def test_inflight_query_keeps_policy_while_next_query_uses_update():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class QueryEncoder:
            profile = PROFILE

            async def embed(self, texts):
                entered.set()
                await release.wait()
                return EmbeddingResult(vectors=((1.0, 0.0),))

        async with store() as (db, builder, publisher):
            initial = declaration().model_copy(update={"default_mode": "vector"})
            await register(db, "reports", initial)
            await write(db, publisher, "scope", "reports", "a", body("数据库", [1, 0]))
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(db, "reports", QueryEncoder())
            request = {"query": "no lexical match"}
            pending = asyncio.create_task(search.search("scope", "reports", request))
            try:
                async with asyncio.timeout(5):
                    await entered.wait()
                await register(db, "reports", initial.model_copy(update={"modes": ("text",), "default_mode": "text"}))
                release.set()
                assert [item.artifact_id for item in (await pending).artifacts] == ["a"]
                assert not (await search.search("scope", "reports", request)).artifacts
            finally:
                release.set()
                await pending

    asyncio.run(run())
