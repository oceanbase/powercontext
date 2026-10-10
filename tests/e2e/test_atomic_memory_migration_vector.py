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

"""Deterministic embeddings exercise actual vector SQL during clean migration and rebuild."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.migrations.atomic_memory_v1 import apply_atomic_memory_migration
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from tests.e2e.atomic_memory_migration_backend import MigrationBackend, migration_index, migration_profile
from tests.e2e.atomic_memory_migration_fixture import SCOPE, _atomic, _seed


class _Embedding:
    profile = EmbeddingProfile(profile_id="clean-migration", model="fixture", dimension=3)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple((0.0, 1.0, 0.0) if "Unrelated" in value else (1.0, 0.0, 0.0) for value in texts)
        )


def test_clean_migration_and_rebuild_publish_searchable_vectors(migration_backend: MigrationBackend) -> None:
    config = migration_backend.config
    embedding = _Embedding()

    async def scenario() -> None:
        await _seed(config, embedding_model=embedding)
        async with migration_profile(config, load_vector_extension=True) as profile:
            report = await apply_atomic_memory_migration(
                profile.database,
                migration_index(config, embedding.profile),
                maintenance_confirmed=True,
                embedding_model=embedding,
            )
            assert report.ready, report.errors
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=embedding) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            before = await memory.search("Tasks A and C", mode="vector")
            assert before.hits[0].hit.artifact_ref == _atomic("alpha", 2)
            assert before.hits[0].text == "Tasks A and C were completed."
            historical = await memory.get(_atomic("alpha", 1).artifact_id, revision=1)
        async with migration_profile(config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                for table in ("pc_memory_artifact_archive", "pc_memory_entry_heads", "pc_memory_entry_versions"):
                    await connection.execute(text(f"DROP TABLE {table}"))
                await connection.execute(text("DELETE FROM pc_atomic_memory_current"))
            rebuilt = await rebuild_atomic_memory_projection(
                profile.database,
                migration_index(config, embedding.profile),
                maintenance_confirmed=True,
                embedding_model=embedding,
            )
            assert rebuilt.ready, rebuilt.errors
            assert rebuilt.counts["rebuilt_rows"] == 2
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=embedding) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            after = await memory.search("Tasks A and C", mode="vector")
            assert [(hit.hit.artifact_ref, hit.text) for hit in after.hits] == [
                (hit.hit.artifact_ref, hit.text) for hit in before.hits
            ]
            assert await memory.get(historical.ref.artifact_id, revision=1) == historical

    asyncio.run(scenario())
