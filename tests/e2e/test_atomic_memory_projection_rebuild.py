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
from sqlalchemy import select, text

from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryRelationError
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexError
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.migrations.atomic_memory_v1 import AtomicMemoryMigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from tests.e2e.atomic_memory_migration_fixture import SCOPE, _atomic, _seed_and_migrate
from tests.legacy_memory import add_legacy_citation_columns


@pytest.mark.parametrize("retention", ["complete", "archive_only", "entries_only", "removed", "unreadable"])
def test_rebuild_uses_current_authority_independently_of_legacy_retention(tmp_path: Path, retention: str) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'projection.db'}")

    async def scenario() -> None:
        await _seed_and_migrate(config)
        async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            current = await memory.get(_atomic("alpha", 2).artifact_id)
            historical = await memory.get(_atomic("alpha", 1).artifact_id, revision=1)
        async with SQLiteProfile.open(config, tables=()) as profile:
            async with profile.database.transaction() as connection:
                if retention in {"entries_only", "removed"}:
                    await connection.execute(text("DROP TABLE pc_memory_artifact_archive"))
                if retention in {"archive_only", "removed"}:
                    await connection.execute(text("DROP TABLE pc_memory_entry_heads"))
                    await connection.execute(text("DROP TABLE pc_memory_entry_versions"))
                if retention == "unreadable":
                    await connection.execute(
                        text("UPDATE pc_memory_artifact_archive SET metadata = :bad"), {"bad": b"invalid"}
                    )
                    await connection.execute(
                        text("UPDATE pc_memory_entry_versions SET source_refs = :bad"), {"bad": b"invalid"}
                    )
                await connection.execute(text("DELETE FROM pc_atomic_memory_current"))
            report = await rebuild_atomic_memory_projection(
                profile.database, SQLiteAtomicMemoryIndex(), maintenance_confirmed=True
            )
            assert report.ready, report.errors
            assert report.counts["rebuilt_rows"] == 2
        async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            assert await memory.get(current.ref.artifact_id) == current
            assert await memory.get(historical.ref.artifact_id, revision=1) == historical
            result = await memory.search("Tasks A and C", mode="text")
            assert current.ref in tuple(hit.hit.artifact_ref for hit in result.hits)

    asyncio.run(scenario())


@pytest.mark.parametrize("residual", ["collection", "citations", "foreign_key"])
def test_public_migration_residual_blocks_startup_and_rebuild(tmp_path: Path, residual: str) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'residual.db'}")

    async def scenario() -> None:
        await _seed_and_migrate(config)
        async with SQLiteProfile.open(config, tables=()) as profile:
            async with profile.database.transaction() as connection:
                if residual == "collection":
                    await connection.execute(
                        text(
                            "INSERT INTO pc_artifacts (scope_id, family, artifact_id, revision, content) "
                            "VALUES (:scope, 'memory', 'unmigrated', 1, :content)"
                        ),
                        {"scope": SCOPE, "content": b"{}"},
                    )
                elif residual == "citations":
                    await add_legacy_citation_columns(connection)
                else:
                    await connection.execute(text("DROP TABLE pc_memory_entry_heads"))
                    await connection.execute(
                        text(
                            "CREATE TABLE pc_memory_entry_heads (scope_id TEXT, family TEXT, memory_artifact_id TEXT, "
                            "head_revision INTEGER, FOREIGN KEY (scope_id, family, memory_artifact_id, head_revision) "
                            "REFERENCES pc_artifacts (scope_id, family, artifact_id, revision))"
                        )
                    )
                before = (await connection.execute(select(ARTIFACT_HEADS_TABLE))).all()
            with pytest.raises(AtomicMemoryIndexError, match="migration is not ready"):
                await rebuild_atomic_memory_projection(
                    profile.database, SQLiteAtomicMemoryIndex(), maintenance_confirmed=True
                )
            async with profile.database.transaction() as connection:
                assert (await connection.execute(select(ARTIFACT_HEADS_TABLE))).all() == before
        with pytest.raises(AtomicMemoryMigrationError):
            async with open_builtin_contexts(BuiltinConfig(database=config)):
                pytest.fail("Public migration residuals must prevent startup")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "corruption",
    ["missing_state", "missing_head", "missing_head_and_state", "head_behind", "summary", "content"],
)
def test_rebuild_rejects_damaged_current_authority_without_legacy_history(tmp_path: Path, corruption: str) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'authority.db'}")

    async def scenario() -> None:
        await _seed_and_migrate(config)
        async with SQLiteProfile.open(config, tables=()) as profile:
            async with profile.database.transaction() as connection:
                await connection.execute(text("DROP TABLE pc_memory_artifact_archive"))
                await connection.execute(text("DROP TABLE pc_memory_entry_heads"))
                await connection.execute(text("DROP TABLE pc_memory_entry_versions"))
                identity = {"scope": SCOPE, "id": _atomic("alpha", 2).artifact_id}
                if corruption == "missing_state":
                    statement = "DELETE FROM pc_atomic_memory_states WHERE scope_id = :scope AND artifact_id = :id"
                elif corruption in {"missing_head", "missing_head_and_state"}:
                    if corruption == "missing_head_and_state":
                        await connection.execute(
                            text("DELETE FROM pc_atomic_memory_states WHERE scope_id = :scope AND artifact_id = :id"),
                            identity,
                        )
                    statement = "DELETE FROM pc_artifact_heads WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id"
                elif corruption == "head_behind":
                    statement = "UPDATE pc_artifact_heads SET revision = 1 WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id"
                elif corruption == "summary":
                    statement = "UPDATE pc_artifact_heads SET governance_generation = governance_generation + 1 WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id"
                else:
                    statement = "UPDATE pc_artifacts SET content = :bad WHERE scope_id = :scope AND family = 'atomic-memory' AND artifact_id = :id AND revision = 2"
                    identity["bad"] = b"{}"
                await connection.execute(text(statement), identity)
                before = (await connection.execute(select(ARTIFACTS_TABLE))).all()
            expected = {
                "missing_state": "orphan head or Family state",
                "missing_head": "orphan head or Family state",
                "missing_head_and_state": "history has no head",
                "head_behind": "head does not select its latest revision",
                "summary": "governance summary disagree",
                "content": "invalid stored artifact payload",
            }
            with pytest.raises(
                (AtomicMemoryIndexError, AtomicMemoryRelationError, PersistenceError), match=expected[corruption]
            ):
                await rebuild_atomic_memory_projection(
                    profile.database, SQLiteAtomicMemoryIndex(), maintenance_confirmed=True
                )
            async with profile.database.transaction() as connection:
                assert (await connection.execute(select(ARTIFACTS_TABLE))).all() == before

    asyncio.run(scenario())
