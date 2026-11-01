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

"""Real backend projection transactions, native initialization and exact search."""

import asyncio
import json

import pytest

from powercontext.artifacts.search import ArtifactSearchUnavailable
from powercontext.builtin.persistence.artifact_indexes import ArtifactIndexBuilder, ArtifactProjectionPublisher
from powercontext.builtin.persistence.oceanbase import OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.custom_artifact_search import CustomArtifactSearcher, CustomArtifactSearchRequest
from tests.builtin.persistence.test_artifact_indexes import PROFILE, body, declaration, register, service, write


def test_search_projection_backends_require_native_provisioning_and_preserve_revisions(migration_backend):
    config = migration_backend.config

    def open_storage():
        if isinstance(config, SQLiteConfig):
            return SQLiteProfile.open(config, tables=BUILTIN_TABLES, load_vector_extension=True)
        if isinstance(config, SeekDBConfig):
            return SeekDBProfile.open(config, tables=BUILTIN_TABLES)
        return OceanBaseProfile.open(config, tables=BUILTIN_TABLES)

    async def declare():
        async with open_storage() as storage:
            db = storage.database
            await register(db, "reports", declaration())
            publisher = ArtifactProjectionPublisher(db)
            await write(db, publisher, "scope", "reports", "a", body("数据库 timeout", *([[0, 0]] * 8)))
            await write(db, publisher, "scope", "reports", "b", body("数据库 normal", [1, 0]))
            builder = ArtifactIndexBuilder(db)
            await builder.refresh("scope", "reports")
            if isinstance(config, SQLiteConfig):
                assert await builder.build("scope", "reports")
            else:
                with pytest.raises(ArtifactSearchUnavailable, match="database initialization"):
                    await builder.build("scope", "reports")

    asyncio.run(declare())

    async def verify():
        async with open_storage() as storage:
            db = storage.database
            builder = ArtifactIndexBuilder(db)
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(db, "reports")
            vector = {"profile": PROFILE.model_dump(mode="json"), "vector": [0, 0]}
            request = {"mode": "vector", "query_vector": vector, "limit": 2, "include_scores": True}
            found = await search.search("scope", "reports", request)
            assert [item.artifact_id for item in found.artifacts] == ["a", "b"]
            assert found.matches[0].channel_scores["vector"].raw == 0
            hybrid = await search.search("scope", "reports", {**request, "mode": "hybrid", "query": "数据库"})
            assert {item.artifact_id for item in hybrid.artifacts} == {"a", "b"}
            # Pause only to order the concurrent operations; all reads and writes use the real backend.
            reading, published = asyncio.Event(), asyncio.Event()

            class SnapshotReader(CustomArtifactSearcher):
                async def _candidates(self, *args, **kwargs):
                    reading.set()
                    await published.wait()
                    return await super()._candidates(*args, **kwargs)

            reader = asyncio.create_task(
                SnapshotReader(db, "reports").search(
                    "scope",
                    CustomArtifactSearchRequest.model_validate_json(
                        json.dumps({**request, "mode": "hybrid", "query": "数据库"})
                    ),
                )
            )
            try:
                await asyncio.wait_for(reading.wait(), 10)
                await builder.refresh("scope", "reports", rebuild=True)
                with pytest.raises(ArtifactSearchUnavailable):
                    await search.search("scope", "reports", request)
                assert await builder.build("scope", "reports")
            finally:
                published.set()
                snapshot = await reader
            assert {item.artifact_id for item in snapshot.artifacts} == {"a", "b"}
            publisher = ArtifactProjectionPublisher(db)
            await write(db, publisher, "scope", "reports", "a", body("recovered"))
            assert [item.artifact_id for item in (await search.search("scope", "reports", request)).artifacts] == ["b"]
            assert not (await search.search("scope", "reports", {"query": "timeout"})).matches

    asyncio.run(verify())
