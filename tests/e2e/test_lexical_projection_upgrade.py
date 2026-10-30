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

"""Upgrades preserve public search, exact revisions and vector retrieval."""

import asyncio

import pytest
from pydantic import JsonValue
from sqlalchemy import update

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.artifact_governance import ArtifactLifecycleState
from powercontext.builtin.persistence.atomic_memory_index_schema import atomic_memory_current_table
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    LEXICAL_PROJECTION_VERSIONS_TABLE,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
)
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from tests.e2e.atomic_memory_migration_backend import migration_profile


class _Embedding:
    profile = EmbeddingProfile(profile_id="lexical-upgrade", model="fixture", dimension=3)

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


@pytest.mark.parametrize("missing_helper", (False, True))
def test_restart_reuses_search_text_and_preserves_skill_reactivation(migration_backend, missing_helper):
    if missing_helper and not isinstance(migration_backend.config, SQLiteConfig):
        pytest.skip("Only SQLite has a separately stored FTS helper")
    config = BuiltinConfig(database=migration_backend.config)

    async def scenario():
        async with open_builtin_contexts(config) as contexts:
            scope = (
                await contexts.scopes.create(
                    ScopeDraft(title="Restart", summary="Search lifecycle", idempotency_key="restart")
                )
            ).scope_id
            experience = await contexts.records.create_artifact(
                scope,
                "experience",
                ArtifactWrite(
                    content={
                        "situation": "restartmarker",
                        "action": "Restart",
                        "outcome": "Ready",
                        "lesson": "Reuse text",
                    }
                ),
            )
            skill = await contexts.records.create_artifact(
                scope,
                "skill",
                ArtifactWrite(
                    content={
                        "name": "restart-skill",
                        "description": "restartmarker",
                        "instructions": "Restart",
                        "validation": ["Search finds the current revision"],
                    }
                ),
            )
            await contexts.update_skill_lifecycle(scope, skill.artifact_id, 0, ArtifactLifecycleState.DEPRECATED, None)
            if missing_helper:
                async with contexts.database.transaction() as connection:
                    await connection.exec_driver_sql("DROP TABLE pc_artifact_fts")

        async with open_builtin_contexts(config) as contexts:
            assert [
                hit.artifact_ref.artifact_id for hit in await contexts.search_experience(scope, "restartmarker", 8)
            ] == [experience.artifact_id]
            assert await contexts.search_skills(scope, "restartmarker", 8) == ()
            await contexts.update_skill_lifecycle(scope, skill.artifact_id, 1, ArtifactLifecycleState.ACTIVE, None)
            hits = await contexts.search_skills(scope, "restartmarker", 8)
            assert [(hit.artifact_ref.artifact_id, hit.artifact_ref.revision) for hit in hits] == [
                (skill.artifact_id, skill.revision)
            ]

    asyncio.run(scenario())


def test_upgrade_preserves_search_exact_records_and_vectors(migration_backend):
    config = BuiltinConfig(database=migration_backend.config, runtime=RuntimeConfig(artifact_processing_families=()))
    embedding = _Embedding()
    marker = "部署地址"
    contents: dict[str, dict[str, JsonValue]] = {
        "atomic-memory": {"kind": "fact", "text": marker + " 127.0.0.1:8000"},
        "experience": {"situation": marker, "action": "配置服务", "outcome": "可访问", "lesson": "核对端口"},
        "skill": {"name": "deployment", "description": marker, "instructions": "配置服务", "validation": ["核对端口"]},
        "topic-memory": {"title": marker, "summary": "服务配置", "detail": "地址固定在 127.0.0.1:8000"},
    }

    async def scenario():
        async with open_builtin_runtime(config, embedding_model=embedding) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Upgrade", summary="Lexical acceptance", idempotency_key="upgrade")
                )
            ).scope_id
            application = runtime.artifacts.for_scope(scope)
            originals = {
                family: await runtime.records.for_scope(scope).create_artifact(family, ArtifactWrite(content=content))
                for family, content in contents.items()
            }
            originals = {
                family: await runtime.records.for_scope(scope).get_artifact_revision(
                    family, value.artifact_id, value.revision
                )
                for family, value in originals.items()
            }
            assert runtime.atomic_memory is not None
            before_vectors = await runtime.atomic_memory.for_scope(scope).search(marker, mode="vector")
            assert before_vectors.hits

        async with migration_profile(migration_backend.config) as profile, profile.database.transaction() as connection:
            # Canonical revisions, states and vectors survive an analyzer change.
            await connection.execute(LEXICAL_PROJECTION_VERSIONS_TABLE.delete())
            for table in (
                atomic_memory_current_table(),
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
            ):
                await connection.execute(update(table).values(searchable_text="legacy stale terms"))
            await connection.execute(
                update(ARTIFACT_HEADS_TABLE)
                .where(ARTIFACT_HEADS_TABLE.c.family.in_(("experience", "skill")))
                .values(searchable_text="legacy stale terms")
            )
        async with open_builtin_runtime(config, embedding_model=embedding) as runtime:
            application = runtime.artifacts.for_scope(scope)
            for family, original in originals.items():
                result = await application.search(family, {"query": "请告诉我部署地址是什么", "mode": "text"})
                assert [match.artifact_ref.artifact_id for match in result.matches] == [original.artifact_id]
                assert (
                    await runtime.records.for_scope(scope).get_artifact_revision(
                        family, original.artifact_id, original.revision
                    )
                    == original
                )
            assert runtime.atomic_memory is not None
            after_vectors = await runtime.atomic_memory.for_scope(scope).search(marker, mode="vector")
            assert after_vectors == before_vectors
            fresh = await runtime.records.for_scope(scope).create_artifact(
                "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "freshmarker"})
            )
            assert (await application.search("atomic-memory", {"query": "freshmarker"})).matches[
                0
            ].artifact_ref.artifact_id == fresh.artifact_id
            current_vectors = await runtime.atomic_memory.for_scope(scope).search(marker, mode="vector")

        async with open_builtin_runtime(config, embedding_model=embedding) as runtime:
            for family, original in originals.items():
                result = await runtime.artifacts.for_scope(scope).search(family, {"query": marker, "mode": "text"})
                assert [match.artifact_ref.artifact_id for match in result.matches] == [original.artifact_id]
            assert runtime.atomic_memory is not None
            assert await runtime.atomic_memory.for_scope(scope).search(marker, mode="vector") == current_vectors

    asyncio.run(scenario())
