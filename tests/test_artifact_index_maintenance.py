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

"""Idle index maintenance must not require SQLite's writer lock or delay startup."""

import asyncio

import pytest
from sqlalchemy import insert, select

from powercontext.artifacts.indexes import ArtifactSearchDeclaration, TextIndex, VectorIndex
from powercontext.artifacts.search import ArtifactSearchUnavailable, ArtifactSearchUnsupported
from powercontext.builtin.inference.models import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_SEARCH_STATES_TABLE, SCOPES_TABLE
from powercontext.server.artifact_plugins import ArtifactPluginManager
from tests.builtin.persistence.test_artifact_indexes import PROFILE, register, service, store, write


@pytest.mark.parametrize("after_rebuild", [False, True])
def test_unchanged_maintenance_does_not_wait_for_an_unrelated_writer(tmp_path, after_rebuild):
    async def run():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'store.db'}", busy_timeout_ms=50)
        async with store(config) as (db, builder, publisher):
            declaration = ArtifactSearchDeclaration(
                version="1",
                indexes=(TextIndex(name="text", fields=("text",), projection_version="1"),),
                modes=("text",),
                default_mode="text",
            )
            await register(db, "reports", declaration)
            await write(db, publisher, "scope", "reports", "a", {"text": "数据库故障"})
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            if after_rebuild:
                await builder.refresh("scope", "reports", rebuild=True)
                assert await builder.build("scope", "reports")
            # A separate connection holds SQLite's writer lock. Idle maintenance
            # must still complete using WAL reads, without a busy timeout failure.
            async with db.engine.connect() as blocker:
                await blocker.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    assert await builder.refresh("scope", "reports")
                    assert await builder.build("scope", "reports")
                finally:
                    await blocker.rollback()
            assert (await service(db, "reports").search("scope", "reports", {"query": "故障"})).matches

    asyncio.run(run())


def test_manager_startup_does_not_wait_for_existing_data_encoding():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class Encoder:
            profile = PROFILE

            async def embed(self, texts):
                started.set()
                await release.wait()
                return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

        async with store() as (db, _, publisher):
            await register(
                db,
                "startup_reports",
                ArtifactSearchDeclaration(
                    version="1",
                    indexes=(
                        VectorIndex(
                            name="semantic", fields=("text",), projection_version="1", source="text", profile=PROFILE
                        ),
                    ),
                    modes=("vector",),
                    default_mode="vector",
                ),
            )
            async with db.transaction() as connection:
                await connection.execute(
                    insert(SCOPES_TABLE).values(
                        scope_id="scope",
                        title="",
                        summary="",
                        scope_id_search="scope",
                        title_search="",
                        summary_search="",
                        version=1,
                    )
                )
            await write(db, publisher, "scope", "startup_reports", "a", {"text": "数据库故障"})
            manager = ArtifactPluginManager(
                database=db,
                config=SQLiteConfig(),
                supervisor=None,
                security_builder=lambda: None,
                on_supervisor=lambda _: None,
                embedding_model=Encoder(),
            )
            try:
                async with asyncio.timeout(5):
                    await manager.__aenter__()
                    await started.wait()
                release.set()
                async with asyncio.timeout(5):
                    while True:
                        try:
                            result = await service(db, "startup_reports").search(
                                "scope",
                                "startup_reports",
                                {"query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1.0, 0.0]}},
                            )
                            assert result.matches
                            break
                        except ArtifactSearchUnavailable:
                            await asyncio.sleep(0.01)
            finally:
                release.set()
                await manager.__aexit__()

    asyncio.run(run())


def test_manager_discovers_first_write_without_creating_empty_scope_states():
    async def run():
        async with store() as (db, _, publisher):
            await register(
                db,
                "reports",
                ArtifactSearchDeclaration(
                    version="1",
                    indexes=(TextIndex(name="text", fields=("text",), projection_version="1"),),
                    modes=("text",),
                    default_mode="text",
                ),
            )
            async with db.transaction() as connection:
                for scope in ("active", "empty"):
                    await connection.execute(
                        insert(SCOPES_TABLE).values(
                            scope_id=scope,
                            title="",
                            summary="",
                            scope_id_search=scope,
                            title_search="",
                            summary_search="",
                            version=1,
                        )
                    )
            async with ArtifactPluginManager(
                database=db,
                config=SQLiteConfig(),
                supervisor=None,
                security_builder=lambda: None,
                on_supervisor=lambda _: None,
            ):
                # Allow startup discovery to finish before the first content arrives.
                await asyncio.sleep(0.1)
                assert not (await service(db, "reports").search("empty", "reports", {"query": "故障"})).matches
                with pytest.raises(ArtifactSearchUnsupported):
                    await service(db, "reports").search(
                        "empty", "reports", {"query": "故障", "indexes": {"text": ["unknown"]}}
                    )
                await write(db, publisher, "active", "reports", "a", {"text": "数据库故障"})
                async with asyncio.timeout(6):
                    while True:
                        try:
                            result = await service(db, "reports").search("active", "reports", {"query": "故障"})
                            assert result.matches
                            break
                        except ArtifactSearchUnavailable:
                            await asyncio.sleep(0.05)
                async with db.transaction() as connection:
                    scopes = (await connection.execute(select(ARTIFACT_SEARCH_STATES_TABLE.c.scope_id))).scalars().all()
                    assert scopes == ["active"]

    asyncio.run(run())


def test_manager_recovers_failed_build_and_cleans_before_rebuilding():
    async def run():
        from powercontext.builtin.persistence.artifact_indexes import COVERAGE, STATES

        failed = asyncio.Event()

        class Encoder:
            profile = PROFILE
            available = False

            async def embed(self, texts):
                if not self.available:
                    failed.set()
                    raise RuntimeError("encoder unavailable")  # noqa: TRY003
                return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

        encoder = Encoder()
        async with store(model=encoder) as (db, builder, publisher):
            await register(
                db,
                "reports",
                ArtifactSearchDeclaration(
                    version="1",
                    indexes=(
                        VectorIndex(
                            name="semantic", fields=("text",), projection_version="1", source="text", profile=PROFILE
                        ),
                    ),
                    modes=("vector",),
                    default_mode="vector",
                ),
            )
            await write(db, publisher, "scope", "reports", "a", {"text": "数据库故障"})

            def manager():
                return ArtifactPluginManager(
                    database=db,
                    config=SQLiteConfig(),
                    supervisor=None,
                    security_builder=lambda: None,
                    on_supervisor=lambda _: None,
                    embedding_model=encoder,
                )

            async with manager():
                async with asyncio.timeout(5):
                    await failed.wait()
            encoder.available = True
            async with manager():
                async with asyncio.timeout(6):
                    while True:
                        try:
                            result = await service(db, "reports").search(
                                "scope",
                                "reports",
                                {"query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]}},
                            )
                            assert result.matches
                            break
                        except ArtifactSearchUnavailable:
                            await asyncio.sleep(0.05)
                async with db.transaction() as connection:
                    old = await connection.scalar(select(STATES.c.build_id).where(STATES.c.state == "ready"))
                await builder.refresh("scope", "reports", rebuild=True)
                async with asyncio.timeout(6):
                    while True:
                        async with db.transaction() as connection:
                            state = await connection.scalar(select(STATES.c.state).where(STATES.c.build_id != old))
                            remains = await connection.scalar(
                                select(COVERAGE.c.artifact_id).where(COVERAGE.c.build_id == old)
                            )
                        if state == "ready" and remains is None:
                            break
                        await asyncio.sleep(0.05)

    asyncio.run(run())
