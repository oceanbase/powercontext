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

"""Real backend maintenance of reconstructed v1 projections, not an old-package upgrade."""

import asyncio

import pytest
from pydantic import JsonValue
from sqlalchemy import select, update

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.atomic_memory_index_schema import atomic_memory_current_table
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    LEXICAL_PROJECTION_VERSIONS_TABLE,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
)
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings
from tests.e2e.atomic_memory_migration_backend import migration_profile


class _Embedding:
    profile = EmbeddingProfile(profile_id="lexical-maintenance", model="fixture", dimension=3)

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


async def _stored_vectors(backend):
    statements = {
        "atomic": "SELECT scope_id,artifact_id,revision,embedding,embedding_input_hash,profile_fingerprint "
        "FROM pc_atomic_memory_current ORDER BY scope_id,artifact_id",
        "topics": "SELECT * FROM pc_topic_memory_vector_topics ORDER BY vector_id",
        "chunks": "SELECT * FROM pc_topic_memory_vector_chunks ORDER BY vector_id",
    }
    if isinstance(backend.config, SQLiteConfig):
        statements["atomic_helper"] = "SELECT identity,embedding FROM pc_atomic_memory_current_vec ORDER BY identity"
        for table in ("pc_topic_memory_topic_vec", "pc_topic_memory_chunk_vec"):
            statements[table] = f"SELECT rowid,embedding FROM {table} ORDER BY rowid"  # noqa: S608 -- Fixed fixture table names.
    async with (
        migration_profile(backend.config, load_vector_extension=True) as profile,
        profile.database.transaction() as connection,
    ):
        return {
            name: tuple(tuple(row) for row in (await connection.exec_driver_sql(statement)).all())
            for name, statement in statements.items()
        }


async def _restore_v1_projections(backend):
    async with (
        migration_profile(backend.config, load_vector_extension=True) as profile,
        profile.database.transaction() as connection,
    ):
        # These short fixture words use reversible v2 tokens. Reconstruct the v1
        # passthrough rule, retaining encoded short words such as SKILL.md's "md".
        # Only derived tokens and markers change; no old binary is executed.
        for table in (
            atomic_memory_current_table(),
            TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
            TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
            ARTIFACT_HEADS_TABLE,
        ):
            keys = tuple(table.primary_key.columns)
            rows = (await connection.execute(select(*keys, table.c.searchable_text))).mappings().all()
            for row in rows:
                if row["searchable_text"] is None:
                    continue
                legacy_terms = []
                for term in row["searchable_text"].split():
                    word = bytes.fromhex(term[3:]).decode()
                    passthrough = (
                        word.isascii()
                        and word.isalnum()
                        and 3 <= len(word) <= 80
                        and not word.startswith(("pcu", "pch"))
                    )
                    legacy_terms.append(word if passthrough else term)
                await connection.execute(
                    update(table)
                    .where(*(key == row[key.name] for key in keys))
                    .values(searchable_text=" ".join(legacy_terms))
                )
        await connection.execute(
            update(LEXICAL_PROJECTION_VERSIONS_TABLE).values(analyzer_version="jieba-0.42.1-words-v1")
        )
        await connection.execute(
            update(LEXICAL_PROJECTION_VERSIONS_TABLE)
            .where(LEXICAL_PROJECTION_VERSIONS_TABLE.c.projection == ARTIFACT_HEADS_TABLE.name)
            .values(analyzer_version="jieba-0.42.1-words-v1:approved-v1")
        )


def test_managed_lexical_maintenance_preserves_search_records_and_vectors(migration_backend):
    embedding = _Embedding()
    config = BuiltinConfig(database=migration_backend.config, runtime=RuntimeConfig(artifact_processing_families=()))
    settings = ServerSettings(database=config.database, runtime=config.runtime)
    # Every backend belongs exclusively to this disposable fixture; backup recovery
    # is covered separately by managed maintenance acceptance tests.
    full = deployment_runner(
        settings,
        evidence_dir=migration_backend.directory / "evidence",
        lock_coordination="single-host",
        embedding_profile=embedding.profile,
    )
    initial = full.plan(backup_policy="skip")
    full.apply(
        plan_id=initial.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="skip",
        accept_no_backup=True,
    )

    async def seed():
        async with open_builtin_runtime(
            config, embedding_model=embedding, scheduler_path=migration_backend.directory / "scheduler"
        ) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Maintenance", summary="Preserve lexical evidence", idempotency_key="maintenance")
                )
            ).scope_id
            contents: dict[str, dict[str, JsonValue]] = {
                "atomic-memory": {"kind": "fact", "text": "the www deploymentmarker"},
                "topic-memory": {
                    "title": "deploymentmarker",
                    "summary": "the www",
                    "detail": "the www durable evidence",
                },
                "experience": {
                    "situation": "the www",
                    "action": "Keep evidence",
                    "outcome": "Available",
                    "lesson": "Retain words",
                },
                "skill": {
                    "name": "lexical-evidence",
                    "description": "the www",
                    "instructions": "Read evidence",
                    "validation": ["Retrieve evidence"],
                },
            }
            originals = {}
            for family, content in contents.items():
                created = await runtime.records.for_scope(scope).create_artifact(family, ArtifactWrite(content=content))
                originals[family] = await runtime.records.for_scope(scope).get_artifact_revision(
                    family, created.artifact_id, created.revision
                )
            vector_matches = {}
            for family in ("atomic-memory", "topic-memory"):
                result = await runtime.artifacts.for_scope(scope).search(
                    family, {"query": "deploymentmarker", "mode": "vector", "include_scores": True}
                )
                assert result.matches
                vector_matches[family] = tuple(result.matches)
            return scope, originals, vector_matches

    scope, originals, before_matches = asyncio.run(seed())
    before_vectors = asyncio.run(_stored_vectors(migration_backend))

    asyncio.run(_restore_v1_projections(migration_backend))
    with pytest.raises(MigrationError, match="projection_migration_required"):
        full.verify()

    maintenance = deployment_runner(
        settings,
        bundle="lexical-projection",
        evidence_dir=migration_backend.directory / "evidence",
        lock_coordination="single-host",
        embedding_profile=embedding.profile,
    )
    plan = maintenance.plan(backup_policy="skip")
    assert plan.state == "migration_required" and plan.revisions == ()
    result = maintenance.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="skip",
        accept_no_backup=True,
    )
    assert result.changed and result.revision == initial.target_revision
    assert maintenance.verify().state == full.verify().state == "ready"
    assert not maintenance.apply(backup_policy="skip").changed
    assert asyncio.run(_stored_vectors(migration_backend)) == before_vectors

    async def verify_public_reads():
        async with open_builtin_runtime(
            config, embedding_model=embedding, scheduler_path=migration_backend.directory / "scheduler"
        ) as runtime:
            application = runtime.artifacts.for_scope(scope)
            for family, original in originals.items():
                for query in ('"the"', '"www"', "www"):
                    result = await application.search(family, {"query": query, "mode": "text"})
                    assert [
                        (match.artifact_ref.artifact_id, match.artifact_ref.revision) for match in result.matches
                    ] == [(original.artifact_id, original.revision)], (family, query)
                assert (
                    await runtime.records.for_scope(scope).get_artifact_revision(
                        family, original.artifact_id, original.revision
                    )
                    == original
                )
            for family, before in before_matches.items():
                result = await application.search(
                    family, {"query": "deploymentmarker", "mode": "vector", "include_scores": True}
                )
                assert tuple(result.matches) == before

    asyncio.run(verify_public_reads())
