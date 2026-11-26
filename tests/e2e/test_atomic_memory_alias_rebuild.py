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

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import (
    rebuild_atomic_memory_projection,
    verify_atomic_memory_aliases,
)


class _Embedding:
    profile = EmbeddingProfile(profile_id="alias-rebuild", model="fixture", dimension=3, normalization="unit")

    def __init__(self):
        self.calls = 0

    async def embed(self, texts, /):
        self.calls += len(texts)
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


@pytest.mark.parametrize("vectors", [False, True])
def test_empty_rebuild_restores_exact_merged_aliases_and_is_repeatable(tmp_path: Path, vectors: bool):
    async def scenario():
        model = _Embedding() if vectors else None
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'aliases.db'}"))
        async with open_builtin_contexts(config, embedding_model=model) as contexts:
            await contexts.get("scope")
            memory = contexts.atomic_memory.for_scope("scope")
            originals = []
            for body in ("Historical deploy wording.", "Historical pause wording."):
                artifact = await contexts.records.create_artifact(
                    "scope", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": body})
                )
                originals.append(await memory.get(artifact.artifact_id))
            merged = (
                await memory.merge(
                    tuple(item.as_read() for item in originals),
                    AtomicMemoryContent(kind="fact", text="Current rollout policy."),
                )
            ).primary
            async with contexts.database.transaction() as connection:
                before = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            text("SELECT * FROM pc_atomic_memory_alias ORDER BY alias_artifact_id")
                        )
                    ).mappings()
                ]
                assert len(before) == 2
                await connection.execute(text("DELETE FROM pc_atomic_memory_alias"))
                await connection.execute(text("DELETE FROM pc_atomic_memory_current"))
            index = contexts.atomic_memory_index
            report = await rebuild_atomic_memory_projection(
                contexts.database, index, maintenance_confirmed=True, embedding_model=model, batch_size=1
            )
            assert report.ready, report.errors
            assert report.counts["alias_rows"] == 2
            async with contexts.database.transaction() as connection:
                after = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            text("SELECT * FROM pc_atomic_memory_alias ORDER BY alias_artifact_id")
                        )
                    ).mappings()
                ]
            assert after == before
            calls = 0 if model is None else model.calls
            report = await rebuild_atomic_memory_projection(
                contexts.database, index, maintenance_confirmed=True, embedding_model=model, batch_size=1
            )
            assert report.ready, report.errors
            assert report.counts["embedding_calls"] == 0
            if model is not None:
                assert model.calls == calls
            assert (await memory.get(merged.ref.artifact_id)) == merged
            assert {item["alias_artifact_id"] for item in after} == {item.ref.artifact_id for item in originals}

    asyncio.run(scenario())


def test_rebuild_preserves_compatible_raw_alias_vectors_and_cleans_obsolete_ids(tmp_path: Path):
    async def scenario():
        model = _Embedding()
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'raw.db'}"))
        async with open_builtin_contexts(config, embedding_model=model) as contexts:
            await contexts.get("scope")
            memory = contexts.atomic_memory.for_scope("scope")
            originals = []
            for body in ("Historical canary.", "Historical pause."):
                artifact = await contexts.records.create_artifact(
                    "scope", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": body})
                )
                originals.append(await memory.get(artifact.artifact_id))
            await memory.merge(
                tuple(item.as_read() for item in originals),
                AtomicMemoryContent(kind="fact", text="Current policy."),
            )
            # These finite float32 bytes need not be reproducible by an embedding service.
            import struct

            raw = struct.pack("<3f", 0.6, 0.8, 0.0)
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    text(
                        "UPDATE pc_atomic_memory_alias SET embedding = :raw, merged_state_version = 999, target_artifact_id = 'stale'"
                    ),
                    {"raw": raw},
                )
                await connection.execute(
                    text(
                        "INSERT INTO pc_atomic_memory_alias "
                        "SELECT scope_id, 'orphan', target_artifact_id, alias_revision, merged_state_version, "
                        "projection_format, kind, text, searchable_text, embedding, profile_fingerprint, embedding_input_hash "
                        "FROM pc_atomic_memory_alias LIMIT 1"
                    )
                )
            async with contexts.database.transaction() as connection:
                invalid = await verify_atomic_memory_aliases(connection, contexts.atomic_memory_index, batch_size=1)
                assert not invalid.ready
                assert invalid.counts["obsolete_alias_rows"] == 1
                assert any("merged_state_version" in error for error in invalid.errors)
            calls = model.calls
            report = await rebuild_atomic_memory_projection(
                contexts.database, contexts.atomic_memory_index, maintenance_confirmed=True, embedding_model=model
            )
            assert report.ready, report.errors
            assert report.counts["alias_rows"] == 2
            assert report.counts["obsolete_alias_rows"] == 0
            assert model.calls == calls
            async with contexts.database.transaction() as connection:
                assert list(
                    (await connection.execute(text("SELECT embedding FROM pc_atomic_memory_alias"))).scalars()
                ) == [raw, raw]

    asyncio.run(scenario())


def test_portable_rebuild_includes_merged_historical_wording(tmp_path: Path):
    from tests.e2e.test_atomic_memory_cascade_restoration import SCOPE, _seed_chain

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'portable.db'}"))
        async with open_builtin_contexts(config) as contexts:
            await _seed_chain(contexts)
            async with contexts.database.transaction() as connection:
                before = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            text("SELECT * FROM pc_atomic_memory_alias ORDER BY alias_artifact_id")
                        )
                    ).mappings()
                ]
                assert len(before) == 4
                await connection.execute(text("DELETE FROM pc_atomic_memory_alias"))
            await contexts.rebuild_portable_projections((SCOPE,))
            async with contexts.database.transaction() as connection:
                after = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            text("SELECT * FROM pc_atomic_memory_alias ORDER BY alias_artifact_id")
                        )
                    ).mappings()
                ]
            assert after == before

    asyncio.run(scenario())


def test_rebuild_regenerates_alias_vectors_after_embedding_profile_change(tmp_path: Path):
    from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile.db'}"))
        async with open_builtin_contexts(config, embedding_model=_Embedding()) as contexts:
            await contexts.get("scope")
            memory = contexts.atomic_memory.for_scope("scope")
            originals = []
            for body in ("Historical deploy.", "Historical pause."):
                created = await contexts.records.create_artifact(
                    "scope", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": body})
                )
                originals.append(await memory.get(created.artifact_id))
            await memory.merge(
                tuple(item.as_read() for item in originals), AtomicMemoryContent(kind="fact", text="Current policy.")
            )
            target = _Embedding()
            target.profile = target.profile.model_copy(update={"profile_id": "changed-profile"})
            index = SQLiteAtomicMemoryIndex(target.profile)
            report = await rebuild_atomic_memory_projection(
                contexts.database, index, maintenance_confirmed=True, embedding_model=target, batch_size=1
            )
            assert report.ready, report.errors
            assert target.calls == 3
            assert report.counts["embedding_calls"] == 3
            async with contexts.database.transaction() as connection:
                fingerprints = set(
                    (await connection.execute(text("SELECT profile_fingerprint FROM pc_atomic_memory_alias"))).scalars()
                )
            assert fingerprints == {report.profile_fingerprint}

    asyncio.run(scenario())
