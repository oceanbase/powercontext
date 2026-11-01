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

"""Live-fixture cleanup preserves other Scopes and their real vector projections."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import inspect

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.artifact_indexes import ArtifactIndexBuilder, ArtifactProjectionPublisher
from powercontext.builtin.persistence.artifact_vector_space import prepare_family_spaces
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings
from tests.builtin.persistence.test_artifact_indexes import PROFILE, body, declaration, register, service, write
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_prompt_management_live import _cleanup


class _Embedding:
    profile = EmbeddingProfile(profile_id="cleanup-fixture", model="fixture", dimension=3, normalization="unit")

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))


@pytest.mark.parametrize("migration_backend", ("sqlite", "oceanbase"), indirect=True)
def test_cleanup_removes_scoped_native_vectors_and_preserves_other_scope(migration_backend: MigrationBackend) -> None:
    embedding = _Embedding()
    settings = ServerSettings(
        database=migration_backend.config,
        inference=InferenceConfig(
            embedding_model=embedding.profile.model,
            embedding_profile_id=embedding.profile.profile_id,
            embedding_dimension=embedding.profile.dimension,
        ),
    )
    prepare_server_database(
        settings, evidence_dir=migration_backend.directory / "migration", embedding_profile=embedding.profile
    )
    config = BuiltinConfig(database=settings.database, inference=settings.inference, runtime=settings.runtime)

    async def scenario() -> None:
        async with open_builtin_contexts(config, embedding_model=embedding) as contexts:
            scopes = tuple([
                await contexts.scopes.create(ScopeDraft(title=key, summary="Cleanup evidence", idempotency_key=key))
                for key in ("cleanup-owned", "other-project")
            ])
            memories = []
            family = "cleanup-report"
            database = contexts.database
            await register(database, family, declaration())
            publisher = ArtifactProjectionPublisher(database)
            builder = ArtifactIndexBuilder(database)
            for scope in scopes:
                (created,) = await contexts.records.create_atomic_memories(
                    scope.scope_id,
                    ({"kind": "fact", "text": "A retry retains one committed write.", "description": "Retry policy"},),
                )
                memories.append(await contexts.atomic_memory.for_scope(scope.scope_id).get(created.artifact_id))
                await write(database, publisher, scope.scope_id, family, "report", body("Retry report", [1, 0]))
                await builder.refresh(scope.scope_id, family)
            await prepare_family_spaces(database, family)
            for scope in scopes:
                assert await builder.build(scope.scope_id, family)
            async with database.transaction() as connection:
                names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            if isinstance(settings.database, OceanBaseConfig):
                assert "pc_artifact_vectors" not in names
                assert any(name.startswith("pc_av_") for name in names)

            search = service(database, family)
            query = {
                "mode": "vector",
                "query_vector": {"profile": PROFILE.model_dump(mode="json"), "vector": [1, 0]},
                "include_scores": True,
            }
            for scope in scopes:
                found = await search.search(scope.scope_id, family, query)
                assert tuple(item.artifact_id for item in found.artifacts) == ("report",)
            keep = scopes[1].scope_id
            before = await contexts.records.get_artifact_revision(keep, family, "report", 1)
            before_vector = await search.search(keep, family, query)

            await _cleanup(contexts, [scopes[0].scope_id])
            await _cleanup(contexts, [scopes[0].scope_id])

            assert await contexts.scopes.list(scope_ids=[scope.scope_id for scope in scopes]) == (scopes[1],)
            assert await contexts.records.get_artifact_revision(keep, family, "report", 1) == before
            after_vector = await search.search(keep, family, query)
            assert after_vector.artifacts == before_vector.artifacts
            assert (
                after_vector.matches[0].channel_scores["vector"].raw
                == before_vector.matches[0].channel_scores["vector"].raw
            )
            memory = contexts.atomic_memory.for_scope(keep)
            assert await memory.get(memories[1].ref.artifact_id) == memories[1]
            result = await memory.search("Retried write", mode="vector")
            assert tuple(hit.hit.artifact_ref for hit in result.hits) == (memories[1].ref,)
            result = await memory.search("retry committed", mode="text")
            assert tuple(hit.hit.artifact_ref for hit in result.hits) == (memories[1].ref,)

    asyncio.run(scenario())
