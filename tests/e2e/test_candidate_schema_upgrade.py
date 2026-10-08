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

"""Dream transformation and read-only startup gates; not full framework acceptance."""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert, select

from powercontext.builtin.persistence.dream_schema import DreamSchemaNotReadyError
from powercontext.builtin.persistence.migration_resources.dream_expansion.upgrade import (
    DreamUpgradeError,
    upgrade_dream_storage,
)
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
        connection.execute("DROP INDEX ix_pc_dream_runs_proposal_fingerprint")
        connection.execute("ALTER TABLE pc_dream_runs DROP COLUMN proposal_fingerprint")
        connection.execute("ALTER TABLE pc_artifact_processing_intents DROP COLUMN consecutive_dream_attempts")
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


def snapshot(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.iterdump())


def transform(path, interrupt=None):
    # The unified runner owns this transaction in production. This harness
    # exercises only the domain contribution, without stamping or claiming ready.
    engine = create_engine(f"sqlite:///{path}")
    if interrupt is not None:
        event.listen(engine, "before_cursor_execute", interrupt)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                upgrade_dream_storage(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
    finally:
        engine.dispose()


def test_legacy_startup_is_read_only_and_preserves_candidates(tmp_path):
    async def scenario():
        path = tmp_path / "upgrade.db"
        await populate(path)
        before = snapshot(path)
        for _ in range(2):
            with pytest.raises(DreamSchemaNotReadyError, match="migration_required"):
                async with open_builtin_contexts(
                    BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
                ):
                    pytest.fail("Existing storage must require explicit maintenance")
            assert snapshot(path) == before

    asyncio.run(scenario())


def test_domain_transformation_preserves_review_history_and_profile_pointer(tmp_path):
    async def scenario():
        path = tmp_path / "upgrade.db"
        scope, expected = await populate(path)
        transform(path)
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
                    assert (
                        await connection.scalar(select(PROFILE_POLICIES_TABLE.c.pending_candidate_id))
                        == expected[0].candidate_id
                    )
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
        ) as contexts:
            result = await contexts.review(scope).approve(expected[0].candidate_id, 2)
            assert result.result_artifact is not None
        with sqlite3.connect(path) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {name for name in names if "candidate" in name} == {
                "pc_candidate_heads",
                "pc_candidate_versions",
                "pc_retained_dream_candidate_heads",
                "pc_retained_dream_candidate_versions",
            }
            assert connection.execute(
                "SELECT status FROM pc_retained_dream_candidate_heads WHERE candidate_id=?", (expected[0].candidate_id,)
            ).fetchone() == ("pending",)
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_point", ["RENAME TO pc_candidate_heads", "DROP TABLE pc_candidate_heads"])
def test_failed_transformation_leaves_framework_transaction_recoverable(tmp_path, failure_point):
    async def scenario():
        path = tmp_path / "interrupted.db"
        await populate(path)
        before = snapshot(path)

        def interrupt(_connection, _cursor, statement, _parameters, _context, _many):
            if failure_point in statement:
                raise RuntimeError("injected migration interruption")  # noqa: TRY003

        with pytest.raises(RuntimeError, match="injected migration interruption"):
            transform(path, interrupt)
        assert snapshot(path) == before
        transform(path)
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM pc_candidate_heads").fetchone() == (3,)

    asyncio.run(scenario())


def test_ambiguous_schema_blocks_startup_without_deleting_records(tmp_path):
    async def scenario():
        path = tmp_path / "ambiguous.db"
        await populate(path)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE pc_candidate_heads (preserve_me TEXT)")
            connection.execute("INSERT INTO pc_candidate_heads VALUES ('keep')")
        before = snapshot(path)
        with pytest.raises(DreamSchemaNotReadyError, match="recovery_required"):
            async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))):
                pytest.fail("Ambiguous storage must not become ready")
        assert snapshot(path) == before

    asyncio.run(scenario())


def seed_run(path, scope, candidate, *, operation="refine_experience", status="queued"):
    from datetime import UTC, datetime

    payload = {
        "principal_id": "owner",
        "generation": 0,
        "request_generation": 1,
        "run": {
            "scope_id": scope,
            "run_id": "legacy-run",
            "operation": operation,
            "status": status,
            "accepted_at": datetime.now(UTC).isoformat(),
            "candidate": {"candidate_id": candidate.candidate_id, "version": candidate.version}
            if status == "succeeded"
            else None,
        },
        "request": {
            "operation": operation,
            "idempotency_key": "legacy-key",
            "artifacts": [candidate.result_artifact.model_dump(mode="json")],
        },
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO pc_dream_runs (scope_id, run_id, principal_key, idempotency_key, request_digest, operation, status, accepted_at, generation, request_generation, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope,
                "legacy-run",
                hashlib.sha256(b"owner").hexdigest(),
                "legacy-key",
                "sha256:" + "0" * 64,
                operation,
                status,
                1,
                0,
                1,
                json.dumps(payload).encode(),
            ),
        )


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed"])
def test_historical_tasks_remain_readable_with_identity_and_candidate_refs(tmp_path, status):
    async def scenario():
        from powercontext.builtin.persistence.dream import DreamRepository

        path = tmp_path / "tasks.db"
        scope, candidates = await populate(path)
        seed_run(path, scope, candidates[1], status=status)
        with sqlite3.connect(path) as connection:
            before = connection.execute(
                "SELECT payload, principal_key, idempotency_key, request_generation FROM pc_dream_runs"
            ).fetchall()
        transform(path)
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute(
                    "SELECT payload, principal_key, idempotency_key, request_generation FROM pc_dream_runs"
                ).fetchall()
                == before
            )
        async with (
            open_builtin_contexts(BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))) as contexts,
            contexts.database.transaction() as connection,
        ):
            record = await DreamRepository().get(connection, scope, "legacy-run")
            assert record.run.status == status
            assert record.principal_id == "owner" and record.request_generation == 1
            assert await DreamRepository().find_request(connection, scope, "owner", record.request) == record
            if status == "succeeded":
                assert record.run.candidate is not None
                assert record.run.candidate.candidate_id == candidates[1].candidate_id

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["operation", "malformed_payload", "missing_evidence", "candidate_reference"])
def test_unknown_task_format_blocks_transformation_before_ddl(tmp_path, invalid):
    async def scenario():
        path = tmp_path / "unknown-task.db"
        scope, candidates = await populate(path)
        seed_run(path, scope, candidates[1], status="succeeded")
        with sqlite3.connect(path) as connection:
            raw = connection.execute("SELECT payload FROM pc_dream_runs").fetchone()[0]
            payload = json.loads(raw)
            if invalid == "operation":
                payload["request"]["operation"] = "future-operation"
            elif invalid == "missing_evidence":
                payload["request"]["artifacts"] = []
            elif invalid == "candidate_reference":
                payload["run"]["candidate"]["candidate_id"] = "missing"
            connection.execute(
                "UPDATE pc_dream_runs SET payload=?",
                (b"not JSON" if invalid == "malformed_payload" else json.dumps(payload).encode(),),
            )
        before = snapshot(path)
        reason = "dream_candidate_reference_invalid" if invalid == "candidate_reference" else "unsupported_task_format"
        with pytest.raises(DreamUpgradeError, match=reason):
            transform(path)
        assert snapshot(path) == before

    asyncio.run(scenario())


def test_missing_dream_index_is_not_recreated_during_startup(tmp_path):
    async def scenario():
        path = tmp_path / "index.db"
        settings = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
        async with open_builtin_contexts(settings) as contexts, contexts.database.transaction() as connection:
            await connection.exec_driver_sql("DROP INDEX ix_pc_dream_runs_proposal_fingerprint")
        before = snapshot(path)
        with pytest.raises(DreamSchemaNotReadyError, match="migration_required"):
            async with open_builtin_contexts(settings):
                pytest.fail("Missing Dream indexes require explicit migration")
        assert snapshot(path) == before

    asyncio.run(scenario())


def test_partial_framework_bundle_cannot_become_a_server_database(tmp_path):
    async def scenario():
        path = tmp_path / "partial.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
            connection.execute("INSERT INTO pc_schema_revision VALUES ('p0003')")
        before = snapshot(path)
        with pytest.raises(DreamSchemaNotReadyError, match="migration_framework_not_ready"):
            async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))):
                pytest.fail("A version-table marker cannot establish Server readiness")
        assert snapshot(path) == before

    asyncio.run(scenario())


def test_pending_legacy_run_resumes_and_replays_after_domain_transformation(tmp_path):
    from powercontext.builtin.runtime import CreateDreamRunRequest, GetDreamRunRequest, ListCandidatesRequest
    from tests.e2e.dream_support import open_dream_runtime, process_pending
    from tests.e2e.test_artifact_dreaming import Generator, MemoryPipeline, config
    from tests.e2e.test_artifact_dreaming import seed as seed_dream

    async def scenario():
        path = tmp_path / "resume.db"
        settings = config(SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
        async with open_dream_runtime(
            settings, candidate_pipeline=MemoryPipeline(), dream_generator=Generator()
        ) as runtime:
            scope, _, citation = await seed_dream(runtime)
            request = CreateDreamRunRequest(
                operation="refine_experience", memory_citations=(citation,), idempotency_key="pending-before-upgrade"
            )
            accepted = await runtime.dream.for_scope(scope).create(request)
        legacy_schema(path)
        # Represent the historical writer's payload without newly added fields.
        # Candidate SQL comes from the frozen historical fixture, not autogenerate.
        with sqlite3.connect(path) as connection:
            rows = connection.execute("SELECT run_id, payload FROM pc_dream_runs").fetchall()
            for run_id, raw in rows:
                payload = json.loads(raw)
                payload["request"].pop("tag_target", None)
                payload["run"].pop("tag_target", None)
                payload["run"].pop("reused", None)
                payload.pop("profile_policy", None)
                payload.pop("proposal_fingerprint", None)
                connection.execute(
                    "UPDATE pc_dream_runs SET payload=? WHERE run_id=?", (json.dumps(payload).encode(), run_id)
                )
            intents = connection.execute("SELECT * FROM pc_artifact_processing_intents").fetchall()
        transform(path)
        with sqlite3.connect(path) as connection:
            assert [row[:-1] for row in connection.execute("SELECT * FROM pc_artifact_processing_intents")] == intents
        async with open_dream_runtime(
            settings, candidate_pipeline=MemoryPipeline(), dream_generator=Generator()
        ) as runtime:
            dream = runtime.dream.for_scope(scope)
            assert (await dream.create(request)).run_id == accepted.run_id
            await process_pending(runtime)
            result = await dream.get(GetDreamRunRequest(run_id=accepted.run_id))
            assert result.status == "succeeded" and result.candidate is not None
            assert (await dream.create(request)).candidate == result.candidate
            candidates = await runtime.review.for_scope(scope).list(ListCandidatesRequest())
            assert len(candidates.candidates) == 1

    asyncio.run(scenario())
