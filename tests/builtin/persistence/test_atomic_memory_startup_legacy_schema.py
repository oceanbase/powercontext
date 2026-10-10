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

"""Artifact head schema compatibility does not import legacy Memory data."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
import rfc8785
from sqlalchemy import text

from powercontext.builtin.persistence.migrations.atomic_memory_v1 import AtomicMemoryMigrationError
from powercontext.builtin.persistence.processing_migration import bootstrap_processing_schema
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.server.authz import ArtifactOwnerRelation, MemoryEntrySelector, PrincipalRef, ResourceRef
from powercontext.server.authz.repository import ACCESS_TABLES, RelationalAccessRepository
from tests.legacy_memory import add_legacy_citation_columns


def test_sqlite_startup_rejects_unmigrated_memory_with_legacy_artifact_head_columns(tmp_path: Path) -> None:
    database = tmp_path / "legacy-memory.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE pc_artifact_heads (scope_id TEXT NOT NULL, family TEXT NOT NULL, "
            "artifact_id TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY (scope_id, family, artifact_id))"
        )
    value = {"kind": "fact", "text": "Preserve the legacy fact.", "source_refs": [], "artifact_refs": []}
    content_hash = sha256(b"powercontext:entry-content:v1\0" + rfc8785.dumps(value)).hexdigest()
    content = json.dumps({
        "schema": "powercontext.memory.v1",
        "manifest": {
            "format": "flat-v1",
            "entries": [
                {
                    "entry_id": "entry",
                    "entry_version_id": "entry-v1",
                    "entry_content_hash": content_hash,
                    "state": "active",
                }
            ],
        },
        "changes": [
            {
                "op": "add",
                "entry_id": "entry",
                "from_entry_version_id": None,
                "to_entry_version_id": "entry-v1",
                "reason": None,
            }
        ],
    }).encode()

    async def scenario() -> None:
        database_config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        config = BuiltinConfig(database=database_config)
        async with (
            SQLiteProfile.open(database_config, tables=BUILTIN_TABLES + ACCESS_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await bootstrap_processing_schema(connection, canonical_processing_manifest(config))
            await add_legacy_citation_columns(connection)
            # Frozen legacy payloads retain the old collection and entry identities.
            await connection.execute(
                text("INSERT INTO pc_artifacts VALUES ('project', 'memory', 'memory', 1, :content, NULL)"),
                {"content": content},
            )
            await connection.execute(text("INSERT INTO pc_artifact_heads VALUES ('project', 'memory', 'memory', 1)"))
            await connection.execute(
                text(
                    "INSERT INTO pc_memory_entry_versions VALUES "
                    "('project', 'memory', 'memory', 'entry', 'entry-v1', 1, NULL, 'fact', :text, "
                    ":source_refs, :artifact_refs, :content_hash, 1)"
                ),
                {"text": value["text"], "source_refs": b"[]", "artifact_refs": b"[]", "content_hash": content_hash},
            )
            await RelationalAccessRepository(profile.database, connection=connection).establish_artifact_owner(
                ArtifactOwnerRelation(
                    resource=ResourceRef.artifact(
                        "project", family="memory", artifact_id="memory", selector=MemoryEntrySelector(entry_id="entry")
                    ),
                    owner=PrincipalRef(type="user", id="owner"),
                    established_at=datetime(2026, 1, 1, tzinfo=UTC),
                    policy_revision="pending",
                    idempotency_key="legacy-entry-owner",
                )
            )

        with sqlite3.connect(database) as connection:
            versions = connection.execute("SELECT * FROM pc_memory_entry_versions").fetchall()
            owners = connection.execute("SELECT * FROM pc_access_owners").fetchall()

        for _ in range(2):
            with pytest.raises(
                AtomicMemoryMigrationError, match="legacy Memory collections remain in public Artifact tables"
            ):
                async with open_builtin_contexts(config):
                    pytest.fail("Legacy Memory must complete offline conversion before normal startup")

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT scope_id, family, artifact_id, revision FROM pc_artifact_heads"
            ).fetchall() == [("project", "memory", "memory", 1)]
            assert connection.execute("SELECT content FROM pc_artifacts WHERE family = 'memory'").fetchall() == [
                (content,)
            ]
            assert connection.execute("SELECT * FROM pc_memory_entry_versions").fetchall() == versions
            assert connection.execute("SELECT * FROM pc_access_owners").fetchall() == owners
            assert (
                connection.execute("SELECT COUNT(*) FROM pc_artifacts WHERE family = 'atomic-memory'").fetchone()[0]
                == 0
            )
            assert (
                connection.execute("SELECT name FROM sqlite_master WHERE name = 'pc_atomic_memory_states'").fetchone()
                is None
            )
            assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_current").fetchone()[0] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("entrypoint", ["startup", "apply", "authority"])
def test_unreleased_atomic_state_table_is_rejected_without_data_loss(tmp_path: Path, entrypoint: str) -> None:
    database = tmp_path / "unreleased.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE pc_atomic_memory_states(artifact_id TEXT, state TEXT)")
        connection.execute("INSERT INTO pc_atomic_memory_states VALUES ('retained', 'merged')")
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")

    async def scenario() -> None:
        with pytest.raises(AtomicMemoryMigrationError, match=r"unsupported unreleased.*pc_atomic_memory_states"):
            if entrypoint == "startup":
                async with open_builtin_contexts(BuiltinConfig(database=config)):
                    pytest.fail("Unreleased Atomic Memory authority must be rejected")
            else:
                from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
                    apply_atomic_memory_migration,
                    assert_atomic_memory_migration_ready,
                    verify_atomic_memory_migration_authority,
                )
                from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

                async with SQLiteProfile.open(config, tables=()) as profile:
                    if entrypoint == "apply":
                        await apply_atomic_memory_migration(
                            profile.database, SQLiteAtomicMemoryIndex(), maintenance_confirmed=True
                        )
                    else:
                        async with profile.database.transaction() as connection:
                            report = await verify_atomic_memory_migration_authority(connection)
                            assert not report.ready
                            assert any("unsupported unreleased" in error for error in report.errors)
                            await assert_atomic_memory_migration_ready(connection)

    asyncio.run(scenario())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM pc_atomic_memory_states").fetchall() == [("retained", "merged")]
        current = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'pc_atomic_memory_current'"
        ).fetchone()
        if current is not None:
            assert connection.execute("SELECT COUNT(*) FROM pc_atomic_memory_current").fetchone()[0] == 0
