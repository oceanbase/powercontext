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

"""Upgrade real pre-unification Candidate rows through normal profile startup."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.persistence.candidate_schema import CandidateMigrationError, migrate_candidate_schema
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import PROFILE_POLICIES_TABLE
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from tests.e2e.test_catalog_changes import seed

FIXTURES = Path(__file__).parents[1] / "builtin/review/fixtures"


def legacy_schema(path):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for suffix in ("heads", "versions"):
            connection.execute(f"ALTER TABLE pc_candidate_{suffix} RENAME TO pc_artifact_candidate_{suffix}")
        for suffix in ("versions", "heads"):
            name = f"pc_artifact_candidate_{suffix}"
            temporary = name + "_copy"
            connection.execute((FIXTURES / f"legacy_candidate_{suffix}.sql").read_text().replace(name, temporary, 1))
            columns = ", ".join(row[1] for row in connection.execute(f"PRAGMA table_info({temporary})"))
            connection.execute(f"INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {name}")  # noqa: S608 - fixture identifiers
            connection.execute(f"DROP TABLE {name}")
            connection.execute(f"ALTER TABLE {temporary} RENAME TO {name}")
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


async def populate(path):
    from datetime import UTC, datetime

    async with seed(SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")) as (contexts, scope, source, artifact):
        service = contexts.review(scope)
        pending = await service.propose_experience(
            artifact.content, sources=(source,), artifacts=(), target=None, reason="Initial"
        )
        pending = await service.revise(
            pending.candidate_id, 1, artifact.content, sources=(source,), artifacts=(), target=None, reason="Reviewed"
        )
        approved = await service.propose_experience(
            artifact.content, sources=(source,), artifacts=(), target=None, reason="Publish"
        )
        approved = await service.approve(approved.candidate_id, 1)
        rejected = await service.propose_experience(
            artifact.content, sources=(source,), artifacts=(), target=None, reason="Inspect"
        )
        rejected = await service.reject(rejected.candidate_id, 1, "Insufficient improvement")
        async with contexts.database.transaction() as connection:
            await connection.execute(
                insert(PROFILE_POLICIES_TABLE).values(
                    scope_id=scope,
                    generation_enabled=True,
                    activation_mode="review_required",
                    pending_candidate_id=pending.candidate_id,
                    version=1,
                    updated_at=datetime.now(UTC),
                )
            )
    legacy_schema(path)
    return scope, (pending, approved, rejected)


def test_startup_preserves_legacy_versions_decisions_and_profile_reference(tmp_path):
    async def scenario():
        path = tmp_path / "upgrade.db"
        scope, expected = await populate(path)
        for _ in range(2):
            async with open_builtin_contexts(
                BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
            ) as contexts:
                service = contexts.review(scope)
                for candidate in expected:
                    assert await service.get_candidate(candidate.candidate_id) == candidate
                history = await service.history(expected[0].candidate_id)
                assert [(item.version, item.reason) for item in history] == [(1, "Initial"), (2, "Reviewed")]
                async with contexts.database.transaction() as connection:
                    pointer = await connection.scalar(select(PROFILE_POLICIES_TABLE.c.pending_candidate_id))
                    assert pointer == expected[0].candidate_id
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
        ) as contexts:
            result = await contexts.review(scope).approve(expected[0].candidate_id, 2)
            assert result.result_artifact is not None
        with sqlite3.connect(path) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {name for name in names if "candidate" in name} == {"pc_candidate_heads", "pc_candidate_versions"}
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_point", ["RENAME TO pc_candidate_heads", "DROP TABLE pc_candidate_heads"])
def test_interrupted_sqlite_upgrade_rolls_back_and_retries(tmp_path, failure_point):
    async def scenario():
        path = tmp_path / "interrupted.db"
        scope, expected = await populate(path)
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

        def interrupt(_connection, _cursor, statement, _parameters, _context, _many):
            if failure_point in statement:
                raise RuntimeError("injected migration interruption")  # noqa: TRY003 - injected failure

        event.listen(engine.sync_engine, "before_cursor_execute", interrupt)
        try:
            with pytest.raises(RuntimeError, match="injected migration interruption"):
                await migrate_candidate_schema(engine)
            with sqlite3.connect(path) as connection:
                assert connection.execute("SELECT COUNT(*) FROM pc_artifact_candidate_heads").fetchone()[0] == 3
            event.remove(engine.sync_engine, "before_cursor_execute", interrupt)
            await asyncio.gather(migrate_candidate_schema(engine), migrate_candidate_schema(engine))
        finally:
            await engine.dispose()
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
        ) as contexts:
            assert await contexts.review(scope).get_candidate(expected[1].candidate_id) == expected[1]

    asyncio.run(scenario())


def test_ambiguous_schema_blocks_startup_without_deleting_records(tmp_path):
    async def scenario():
        path = tmp_path / "ambiguous.db"
        await populate(path)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE pc_candidate_heads (preserve_me TEXT)")
            connection.execute("INSERT INTO pc_candidate_heads VALUES ('keep')")
        with pytest.raises(CandidateMigrationError, match="ambiguous"):
            async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))):
                pytest.fail("Ambiguous storage must not become ready")
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT preserve_me FROM pc_candidate_heads").fetchone() == ("keep",)
            assert connection.execute("SELECT COUNT(*) FROM pc_artifact_candidate_heads").fetchone()[0] == 3

    asyncio.run(scenario())
