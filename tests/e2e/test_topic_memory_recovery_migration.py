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

"""Recovery evidence survives the real SQLite upgrade, backup and reopen paths."""

# SQL identifiers below are frozen fixture columns; every value is bound.
# ruff: noqa: S608

import asyncio
import shutil
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import Engine, event

from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.migrations import MigrationError
from powercontext.builtin.persistence.migrations.topic_memory_recovery import TopicMemoryRecoveryMigrationRunner
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.persistence.topic_memory_recovery_schema import recovery_statements
from powercontext.builtin.runtime.topic_memory_recovery import TopicMemoryRecoveryService
from tests.builtin.runtime.test_topic_memory_processing import _repositories
from tests.builtin.runtime.test_topic_memory_scope import BINDING, _capture
from tests.builtin.runtime.test_topic_memory_terminal_frontier import _budget, _seed
from tests.e2e.test_tag_schema_upgrade import _isolated_database


def _legacy(path):
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")

    async def seed():
        manager, profile, sources, _ = await _repositories(config)
        try:
            for number in (1, 2, 3):
                await _capture(profile, sources, number)
            await _seed(profile.database, source_through=2)
            return await _budget(profile.database)
        finally:
            await manager.__aexit__(None, None, None)

    budget = asyncio.run(seed())
    core = {
        name: budget[name]
        for name in (
            "scope_id",
            "binding_name",
            "source_after",
            "source_through",
            "attempt_id",
            "attempts",
            "requests",
            "tokens",
            "failure_code",
        )
    }
    common = {
        "scope_id": "scope-a",
        "binding_name": BINDING,
        "source_after": 0,
        "source_through": 2,
        "target_source_through": 3,
        "attempts": 3,
        "requests": 9,
        "tokens": 1000,
        "blocked_code": "window_attempt_limit",
        "last_error_code": "processing_failed",
        "blocked_at": "2026-10-01 12:00:00.000000",
        "finished_at": "2026-10-01 11:59:59.000000",
    }
    rows = [
        dict(
            common,
            block_id="legacy-accepted",
            attempt_id="old-attempt",
            retried_at="2026-10-01 12:01:00.000000",
            retry_request_generation=7,
        ),
        dict(
            common,
            block_id="legacy-current",
            attempt_id=core["attempt_id"],
            source_through=1,
            last_error_code="worker_timeout",
        ),
        dict(common, block_id="successful-attempt", attempt_id="successful", blocked_code=None, blocked_at=None),
    ]
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE pc_topic_memory_work_blocks")
        connection.execute("DROP TABLE pc_topic_memory_work_budgets")
        for shape in ("budget_v0", "attempts_v0"):
            for statement in recovery_statements(shape, "sqlite"):
                connection.execute(statement)
        connection.execute(
            f"INSERT INTO pc_topic_memory_work_budgets ({','.join(core)}) VALUES ({','.join(':' + name for name in core)})",
            core,
        )
        for row in rows:
            connection.execute(
                f"INSERT INTO pc_topic_memory_work_attempts ({','.join(row)}) VALUES ({','.join(':' + name for name in row)})",
                row,
            )
    return config, core


def _apply(runner):
    return runner.apply(plan_id=runner.plan().plan_id, accepted=True, maintenance_confirmed=True)


def test_attempts_upgrade_preserves_receipts_allowance_unknown_ranges_and_backup_restore(tmp_path):
    path = tmp_path / "legacy.db"
    config, core = _legacy(path)
    original = path.read_bytes()
    with pytest.raises(MigrationError, match="migration_required"):
        asyncio.run(_repositories(config))
    assert path.read_bytes() == original
    runner = TopicMemoryRecoveryMigrationRunner(config)
    result = _apply(runner)
    assert result.changed and result.backup_state == "completed"
    assert runner.verify().state == "ready"
    after = path.read_bytes()
    assert not runner.apply().changed and path.read_bytes() == after

    async def inspect_and_retry(config, *, grant_current):
        manager, profile, _, _ = await _repositories(config)
        try:
            service = TopicMemoryRecoveryService(profile.database)
            assert {name: (await _budget(profile.database))[name] for name in core} == core
            accepted = await service.get_block("scope-a", "legacy-accepted")
            assert accepted.source_through is None and accepted.budget_source_through is None
            assert accepted.legacy_source_through == 2 and accepted.last_error_code == "processing_failed"
            assert accepted.retry_request_generation == 7 and accepted.retried_at is not None
            assert (await service.retry("scope-a", accepted.block_id)).block == accepted
            assert {name: (await _budget(profile.database))[name] for name in core} == core
            current = (await service.status("scope-a")).block
            assert current is not None
            assert current.block_id == "legacy-current" and current.attempt_id == core["attempt_id"]
            assert current.source_through is None and current.budget_source_through == 2
            assert current.legacy_source_through == 1 and current.last_error_code == "worker_timeout"
            if grant_current:
                receipt = await service.retry("scope-a", current.block_id)
                assert receipt.block.budget_source_through == 2
                assert await service.retry("scope-a", current.block_id) == receipt
                assert (await service.retry("scope-a", accepted.block_id)).block == accepted
            return accepted
        finally:
            await manager.__aexit__(None, None, None)

    accepted = asyncio.run(inspect_and_retry(config, grant_current=True))

    async def replay_readonly():
        async with SQLiteProfile.open_readonly(config) as profile:
            assert (
                await TopicMemoryRecoveryService(profile.database).retry("scope-a", accepted.block_id)
            ).block == accepted

    asyncio.run(replay_readonly())
    restored = tmp_path / "restored.db"
    shutil.copyfile(result.backup_ref, restored)
    restored_config = SQLiteConfig(url=f"sqlite+aiosqlite:///{restored}")
    _apply(TopicMemoryRecoveryMigrationRunner(restored_config))
    assert asyncio.run(inspect_and_retry(restored_config, grant_current=False)) == accepted
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("SELECT count(*) FROM pc_topic_memory_work_blocks").fetchone() == (2,)
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='pc_topic_memory_work_attempts'").fetchall()
            == []
        )


def test_interrupted_sqlite_conversion_rolls_back_and_resumes_with_original_recovery_point(tmp_path):
    path = tmp_path / "interrupted.db"
    config, core = _legacy(path)
    runner = TopicMemoryRecoveryMigrationRunner(config)

    def stop_before_removing_evidence(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement == "DROP TABLE pc_topic_memory_work_attempts":
            raise RuntimeError("injected migration interruption")  # noqa: TRY003 - inject a mid-conversion failure

    event.listen(Engine, "before_cursor_execute", stop_before_removing_evidence)
    try:
        with pytest.raises(RuntimeError, match="injected migration interruption"):
            _apply(runner)
    finally:
        event.remove(Engine, "before_cursor_execute", stop_before_removing_evidence)
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("SELECT attempts,requests,tokens FROM pc_topic_memory_work_budgets").fetchone() == (
            core["attempts"],
            core["requests"],
            core["tokens"],
        )
        assert connection.execute("SELECT count(*) FROM pc_topic_memory_work_attempts").fetchone() == (3,)
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='pc_topic_memory_work_blocks'").fetchall()
            == []
        )
    assert runner.plan().state == "recovery_required"
    result = _apply(runner)
    assert result.backup_state == "completed"
    assert len(list(path.parent.glob("*.pc-migration-backups/*.sqlite3"))) == 1
    assert runner.verify().state == "ready"


@pytest.mark.parametrize("invalid", ["shape", "acceptance", "collation", "expression_index"])
def test_unknown_schema_or_invalid_receipt_is_rejected_before_writes(tmp_path, invalid):
    path = tmp_path / "unsupported.db"
    config, _ = _legacy(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        if invalid == "shape":
            connection.execute("ALTER TABLE pc_topic_memory_work_attempts ADD COLUMN unexpected TEXT")
        elif invalid == "acceptance":
            connection.execute(
                "UPDATE pc_topic_memory_work_attempts SET retry_request_generation=NULL WHERE block_id='legacy-accepted'"
            )
        elif invalid == "collation":
            connection.execute("ALTER TABLE pc_topic_memory_work_budgets RENAME TO fixture_budget")
            connection.execute(
                recovery_statements("budget_v0", "sqlite")[0].replace(
                    "scope_id VARCHAR(256)", "scope_id VARCHAR(256) COLLATE NOCASE"
                )
            )
            connection.execute("INSERT INTO pc_topic_memory_work_budgets SELECT * FROM fixture_budget")
            connection.execute("DROP TABLE fixture_budget")
        else:
            connection.execute(
                "CREATE UNIQUE INDEX unexpected_identity ON pc_topic_memory_work_budgets(lower(scope_id))"
            )
    original = path.read_bytes()
    runner = TopicMemoryRecoveryMigrationRunner(config)
    with pytest.raises(MigrationError, match="incompatible"):
        runner.plan()
    assert path.read_bytes() == original
    assert not runner.directory.exists()


def test_consumed_allowance_change_invalidates_the_reviewed_migration_plan(tmp_path):
    path = tmp_path / "changed.db"
    config, _ = _legacy(path)
    runner = TopicMemoryRecoveryMigrationRunner(config)
    plan = runner.plan()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE pc_topic_memory_work_budgets SET tokens=tokens+1")
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="plan_changed"):
        runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert path.read_bytes() == before
    assert not runner.directory.exists()


@pytest.mark.parametrize("table", ["pc_topic_memory_work_budgets", "pc_topic_memory_work_blocks"])
def test_interrupted_empty_sqlite_initialization_rolls_back_before_retry(tmp_path, table):
    path = tmp_path / "empty.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")

    def interrupt(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().startswith("CREATE TABLE " + table):
            raise RuntimeError("injected initialization interruption")  # noqa: TRY003 - interrupt real transactional DDL

    event.listen(Engine, "before_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="injected initialization interruption"):
            asyncio.run(_repositories(config))
    finally:
        event.remove(Engine, "before_cursor_execute", interrupt)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == []

    async def reopen():
        manager, _, _, _ = await _repositories(config)
        await manager.__aexit__(None, None, None)

    asyncio.run(reopen())


@pytest.mark.parametrize("with_attempts", [False, True])
def test_upgrade_revokes_an_unfinished_old_worker_without_waiting_for_a_new_supervisor(tmp_path, with_attempts):
    path = tmp_path / "stopped-worker.db"
    config, _ = _legacy(path)

    async def old_term():
        async with SQLiteProfile.open(config, tables=()) as profile, profile.database.transaction() as connection:
            return (
                await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "old-holder")
            ).fence("single-process")

    fence = asyncio.run(old_term())
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE pc_topic_memory_work_attempts SET blocked_at=NULL,blocked_code=NULL,last_error_code=NULL,"
            "started_at='2026-10-01 11:59:00',finished_at=NULL,supervisor_group=?,supervisor_generation=?,holder_id=? "
            "WHERE block_id='legacy-current'",
            (fence.supervisor_group, fence.supervisor_generation, fence.holder_id),
        )
        if not with_attempts:
            connection.execute("DROP TABLE pc_topic_memory_work_attempts")
    _apply(TopicMemoryRecoveryMigrationRunner(config))

    async def inspect_without_supervisor():
        manager, profile, _, _ = await _repositories(config)
        try:
            async with profile.database.transaction() as connection:
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await ArtifactProcessingLeaseRepository().require_fence(connection, fence)
            status = await TopicMemoryRecoveryService(profile.database).status("scope-a")
            assert status.state == "blocked" and status.block is not None
            assert status.block.last_error_code is None and status.block.source_through is None
            assert status.block.budget_source_through == 2
            budget = await _budget(profile.database)
            assert budget["holder_id"] == ("old-holder" if with_attempts else None)
            assert budget["finished_at"] is None
            assert (budget["attempts"], budget["requests"], budget["tokens"]) == (3, 9, 1000)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(inspect_without_supervisor())


@pytest.mark.parametrize("state", ["pristine", "receipt", "business"])
def test_only_pristine_block_first_initialization_can_resume_without_migration_evidence(tmp_path, state):
    path = tmp_path / "prefix.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(recovery_statements("blocks_v1", "sqlite")[0])
        if state == "receipt":
            connection.execute(
                "INSERT INTO pc_topic_memory_work_blocks "
                "(block_id,attempt_id,scope_id,binding_name,source_after,source_through,budget_source_through,legacy_source_through,"
                "target_source_through,attempts,requests,tokens,error_code,last_error_code,blocked_at,retried_at,retry_request_generation) "
                "VALUES ('accepted','old','scope-a','topic-memory-source-window',0,NULL,NULL,1,3,3,9,1000,"
                "'window_attempt_limit',NULL,'2026-10-01 12:00:00','2026-10-01 12:01:00',7)"
            )
        elif state == "business":
            connection.execute(
                "CREATE TABLE pc_source_journal_heads(scope_id TEXT PRIMARY KEY, position INTEGER NOT NULL)"
            )

    async def reopen():
        manager, _, _, _ = await _repositories(config)
        await manager.__aexit__(None, None, None)

    if state == "pristine":
        asyncio.run(reopen())
    else:
        original = path.read_bytes()
        with pytest.raises(MigrationError, match="unknown_baseline"):
            asyncio.run(reopen())
        assert path.read_bytes() == original


@pytest.mark.parametrize("block_prefix", [False, True])
def test_initialization_preserves_an_independent_code_database(tmp_path, block_prefix):
    path = tmp_path / "code-first.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE pc_code_generations(id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO pc_code_generations VALUES ('existing-generation')")
        if block_prefix:
            connection.execute(recovery_statements("blocks_v1", "sqlite")[0])

    async def reopen():
        manager, _, _, _ = await _repositories(config)
        await manager.__aexit__(None, None, None)

    asyncio.run(reopen())
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT id FROM pc_code_generations").fetchall() == [("existing-generation",)]
        assert connection.execute("SELECT COUNT(*) FROM pc_topic_memory_work_budgets").fetchone() == (0,)


@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_real_mysql_recovery_upgrade_preserves_receipt_and_resumes_registered_ddl_prefix(
    backend, interrupted, short_tmp_path, tmp_path
):
    async def scenario():
        async with _isolated_database(backend, short_tmp_path) as (config, open_profile):
            async with open_profile(config, tables=()) as profile, profile.database.transaction() as connection:
                for shape in ("budget_v0", "attempts_v0", "leases_v0"):
                    for statement in recovery_statements(shape, "mysql"):
                        await connection.exec_driver_sql(statement)
                old_fence = (
                    await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "old-holder")
                ).fence("single-process")
                await connection.exec_driver_sql(
                    "INSERT INTO pc_topic_memory_work_budgets VALUES ('scope-a','topic-memory-source-window',0,2,'current',3,9,1000,'')"
                )
                await connection.exec_driver_sql(
                    "INSERT INTO pc_topic_memory_work_attempts "
                    "(block_id,attempt_id,scope_id,binding_name,source_after,source_through,target_source_through,attempts,requests,tokens,"
                    "blocked_code,last_error_code,blocked_at,retried_at,retry_request_generation) "
                    "VALUES ('accepted','old','scope-a','topic-memory-source-window',0,2,3,3,9,1000,"
                    "'window_attempt_limit','processing_failed','2026-10-01 12:00:00','2026-10-01 12:01:00',7)"
                )
            runner = TopicMemoryRecoveryMigrationRunner(
                config, evidence_directory=tmp_path / "evidence", lock_coordination="single-host"
            )

            def apply_skipping_backup():
                plan = runner.plan(backup_policy="skip")
                return runner.apply(
                    plan_id=plan.plan_id,
                    accepted=True,
                    maintenance_confirmed=True,
                    backup_policy="skip",
                    accept_no_backup=True,
                )

            def stop_during_ddl(_connection, _cursor, statement, _parameters, _context, _executemany):
                if statement.startswith("ALTER TABLE pc_topic_memory_work_budgets ADD COLUMN finished_at"):
                    raise RuntimeError("injected migration interruption")  # noqa: TRY003 - exercise committed DDL recovery

            if interrupted:
                event.listen(Engine, "before_cursor_execute", stop_during_ddl)
                try:
                    with pytest.raises(RuntimeError, match="injected migration interruption"):
                        await asyncio.to_thread(apply_skipping_backup)
                finally:
                    event.remove(Engine, "before_cursor_execute", stop_during_ddl)
                plan = await asyncio.to_thread(runner.plan, backup_policy="skip")
                assert plan.state == "recovery_required"
                token = (plan.coordination or {}).get("pending_run_id")
                runner = TopicMemoryRecoveryMigrationRunner(
                    config,
                    acknowledge_previous_run=token,
                    evidence_directory=tmp_path / "evidence",
                    lock_coordination="single-host",
                )
            result = await asyncio.to_thread(apply_skipping_backup)
            assert result.changed and result.backup_state == "skipped"
            assert (await asyncio.to_thread(runner.verify)).state == "ready"
            async with open_profile(config, tables=BUILTIN_TABLES) as profile:
                async with profile.database.transaction() as connection:
                    with pytest.raises(ArtifactProcessingLeadershipLostError):
                        await ArtifactProcessingLeaseRepository().require_fence(connection, old_fence)
                service = TopicMemoryRecoveryService(profile.database)
                block = await service.get_block("scope-a", "accepted")
                assert block.source_through is None and block.budget_source_through is None
                assert block.legacy_source_through == 2 and block.retry_request_generation == 7
                assert (await service.retry("scope-a", "accepted")).block == block
                async with profile.database.transaction() as connection:
                    assert (
                        await connection.exec_driver_sql(
                            "SELECT attempts,requests,tokens FROM pc_topic_memory_work_budgets"
                        )
                    ).one() == (3, 9, 1000)

    asyncio.run(scenario())
