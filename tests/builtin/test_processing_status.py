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

"""Processing inspection over real SQLite records, without doing work on reads."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, insert, select, update
from sqlalchemy.exc import OperationalError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.catalog_changes.models import TagDreamTarget
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamRecord, DreamRun
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_LEASES_TABLE,
    DREAM_RUNS_TABLE,
    TOPIC_MEMORY_WORK_BLOCKS_TABLE,
    TOPIC_MEMORY_WORK_BUDGETS_TABLE,
)
from powercontext.builtin.processing_diagnostics import ProcessingStage
from powercontext.builtin.processing_status import (
    ProcessingStatusApplication,
    ProcessingStatusInspector,
    ProcessingStatusState,
)
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_runtime
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.runtime.processing_observation import observe_processing
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.scope.errors import ScopeNotFoundError
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sources import SourceCursor
from powercontext.builtin.statistics.models import ProcessingObservation
from powercontext.builtin.tags import ArtifactTagTarget


@asynccontextmanager
async def _runtime(tmp_path):
    async with open_builtin_runtime(
        BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'inspection.db'}"))
    ) as runtime:
        assert runtime.scopes is not None
        scopes = tuple([
            (await runtime.scopes.create(ScopeDraft(title=name, summary=name, idempotency_key=name))).scope_id
            for name in ("first", "second")
        ])
        yield runtime, scopes


async def _run_record(connection, scope, index, operation="derive_skill", status="succeeded", *, tag_family=None):
    family = {
        "derive_skill": "experience",
        "refine_experience": "experience",
        "revise_profile": "profile",
        "revise_memory": "atomic-memory",
        "refresh_handoff": "handoff",
        "revise_prompt": "prompt",
    }.get(operation, "experience")
    ref = ArtifactRef(family=family, artifact_id="profile" if family == "profile" else "target", revision=1)
    request = CreateDreamRunRequest(
        operation=operation,
        artifacts=(ref,),
        target=ref if operation not in {"derive_skill", "refine_experience", "revise_tags"} else None,
        tag_target=TagDreamTarget(
            target=ArtifactTagTarget(family=tag_family, artifact_id="target"),
            expected_etag='"tags:test"',
            basis_ref=ArtifactRef(family=tag_family, artifact_id="target", revision=1),
        )
        if tag_family is not None
        else None,
        idempotency_key=f"request-{index}",
    )
    accepted = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)
    record = DreamRecord(
        run=DreamRun(
            scope_id=scope,
            run_id=f"dr_{index}",
            operation=operation,
            status=status,
            outcome="no_change" if status == "succeeded" else None,
            error="private-error-text" if status == "failed" else None,
            accepted_at=accepted,
            completed_at=accepted if status in {"failed", "succeeded"} else None,
        ),
        request=request,
        principal_id="runtime",
        request_generation=1,
    )
    await DreamRepository().create(connection, record)
    return record


@pytest.mark.parametrize("family", ["memory", "atomic-memory", "profile", "experience"])
def test_real_cursor_is_not_an_intent_generation(tmp_path, family):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            scope = scopes[0]
            for index in range(3):
                await runtime.sources.for_scope(scope).capture(
                    CaptureSource(source_id=str(index), content="private", metadata={})
                )
            binding = FAMILY_BINDINGS["memory" if family == "atomic-memory" else family]
            async with runtime.primary_database.transaction() as connection:
                intents = ArtifactProcessingIntentRepository()
                await intents.ensure(connection, scope, binding)
                await intents.mark_dirty_many(connection, scope, (binding,), increment=10)
                intent = await intents.request(connection, scope, binding)
                await intents.acknowledge(
                    connection, scope, binding, intent.requested_generation, clean_generation=intent.dirty_generation
                )
                await SourceCursorRepository().save(
                    connection, scope, binding, SourceCursor(sequence=2), expected_generation=None
                )
            result = await ProcessingStatusApplication(runtime).for_scope(scope).status(family)
            assert result.progress is not None
            assert (result.progress.source_cursor, result.progress.source_head) == (2, 3)
            assert result.invocation is not None and result.invocation.state == "acknowledged"
            assert result.state is ProcessingStatusState.PENDING
            assert result.executor is None
            observations = await observe_processing(runtime.primary_database, (scope,), None)
            assert result.invocation.model_dump(exclude={"observed_at"}) == next(
                row for row in observations[scope] if row.binding == binding
            ).model_dump(exclude={"observed_at"})

    asyncio.run(scenario())


def test_unhandled_request_is_pending_without_source_work(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            async with runtime.primary_database.transaction() as connection:
                await ArtifactProcessingIntentRepository().request(connection, scopes[0], FAMILY_BINDINGS["memory"])
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status("memory")
            assert result.state is ProcessingStatusState.PENDING
            assert result.progress is not None and not result.progress.pending_inputs
            assert result.invocation is not None and result.invocation.state == "pending"
            assert result.executor is None

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["memory", "experience", "profile", "skill", "handoff", "prompt", "topic-memory"])
def test_empty_scope_has_no_proven_publication_or_executor(tmp_path, family):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status(family)
            assert result.state is ProcessingStatusState.IDLE
            assert result.executor is None and result.invocation is None and result.block is None
            assert result.dream_operations == () and result.dream_pending is False
            if family in {"skill", "handoff", "prompt"}:
                assert result.source_consumption_applicable is False and result.progress is None
            else:
                assert result.progress is not None and result.progress.source_cursor == result.progress.source_head == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("latest", ["failed", "succeeded"])
def test_historical_outcome_is_not_a_persistent_block(tmp_path, latest):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            async with runtime.primary_database.transaction() as connection:
                await _run_record(connection, scopes[0], 1, status="failed")
                if latest == "succeeded":
                    await _run_record(connection, scopes[0], 2)
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status("skill")
            assert result.state is ProcessingStatusState.IDLE
            assert result.block is None and result.dream_pending is False
            assert result.dream_operations[0].status == latest
            assert "private-error-text" not in repr(result)

    asyncio.run(scenario())


def test_family_filter_and_pending_query_precede_limits(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            async with runtime.primary_database.transaction() as connection:
                await _run_record(connection, scopes[0], 0, status="running")
                for index in range(1, 13):
                    await _run_record(connection, scopes[0], index)
                for index in range(13, 26):
                    await _run_record(connection, scopes[0], index, "refine_experience")
                await _run_record(connection, scopes[1], 100, status="queued")
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status("skill")
            assert len(result.dream_operations) == 10
            assert all(row.operation == "derive_skill" for row in result.dream_operations)
            assert result.dream_operations[0].status == "succeeded"
            assert result.dream_pending is True and result.state is ProcessingStatusState.PENDING
            assert result.executor is None
            other = await ProcessingStatusApplication(runtime).for_scope(scopes[1]).status("skill")
            assert [row.run_id for row in other.dream_operations] == ["dr_100"]

    asyncio.run(scenario())


def test_revise_tags_is_filtered_by_actual_target_family(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            async with runtime.primary_database.transaction() as connection:
                await _run_record(connection, scopes[0], 1, "revise_tags", "queued", tag_family="skill")
                for index in range(2, 15):
                    await _run_record(connection, scopes[0], index, "revise_tags", tag_family="experience")
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status("skill")
            assert [row.run_id for row in result.dream_operations] == ["dr_1"]
            assert result.dream_pending is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "family,operation",
    [("memory", "revise_memory"), ("experience", "refine_experience"), ("profile", "revise_profile")],
)
def test_source_and_dream_progress_are_independent(tmp_path, family, operation):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            async with runtime.primary_database.transaction() as connection:
                await _run_record(connection, scopes[0], 1, operation, "queued")
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status(family)
            assert result.progress is not None and not result.progress.pending_inputs
            assert result.dream_pending is True and result.state is ProcessingStatusState.PENDING
            assert result.dream_operations[0].operation == operation

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["skill", "memory"])
def test_failed_query_is_unavailable_not_idle(tmp_path, monkeypatch, family):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):

            async def failed(*args, **kwargs):
                raise OperationalError("private-sql", {}, RuntimeError("private-connection"))

            monkeypatch.setattr(ProcessingStatusInspector, "_dream_status", failed)
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status(family)
            assert result.state is ProcessingStatusState.UNKNOWN
            assert result.dream_pending is None and result.unavailable_reason == "dream_query_failed"
            assert "private" not in repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [b"{}", b"private-broken-json"])
def test_corrupt_dream_payload_is_unavailable_without_exposing_content(tmp_path, payload):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            async with runtime.primary_database.transaction() as connection:
                await _run_record(connection, scopes[0], 1)
                await connection.execute(
                    update(DREAM_RUNS_TABLE).where(DREAM_RUNS_TABLE.c.scope_id == scopes[0]).values(payload=payload)
                )
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status("skill")
            assert result.state is ProcessingStatusState.UNKNOWN
            assert result.dream_pending is None and result.unavailable_reason == "dream_query_failed"
            assert "private" not in repr(result)
            async with runtime.primary_database.transaction() as connection:
                assert await connection.scalar(select(DREAM_RUNS_TABLE.c.payload)) == payload

    asyncio.run(scenario())


async def _topic_budget(connection, scope, *, started=False, finished=False):
    binding = TOPIC_MEMORY_SOURCE_WINDOW_BINDING
    await connection.execute(
        insert(TOPIC_MEMORY_WORK_BUDGETS_TABLE).values(
            scope_id=scope,
            binding_name=binding,
            source_after=0,
            source_through=1,
            attempt_id="attempt-1",
            attempts=3,
            requests=4,
            tokens=120,
            failure_code="",
            attempt_source_through=1,
            started_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None) if started else None,
            finished_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None) if finished else None,
            supervisor_group="global" if started else None,
            supervisor_generation=1 if started else None,
            holder_id="owner" if started else None,
        )
    )


@pytest.mark.parametrize("persisted", [False, True])
def test_topic_legacy_projection_and_real_block_are_passive(tmp_path, persisted):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            scope = scopes[0]
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="one", content="private", metadata={})
            )
            async with runtime.primary_database.transaction() as connection:
                await _topic_budget(connection, scope)
                if persisted:
                    await connection.execute(
                        insert(TOPIC_MEMORY_WORK_BLOCKS_TABLE).values(
                            block_id="block-1",
                            attempt_id="attempt-1",
                            scope_id=scope,
                            binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                            source_after=0,
                            source_through=1,
                            budget_source_through=1,
                            target_source_through=1,
                            attempts=3,
                            requests=4,
                            tokens=120,
                            error_code="window_attempt_limit",
                            blocked_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
                        )
                    )
            statements = []

            def capture(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            engine = runtime.primary_database.engine.sync_engine
            event.listen(engine, "before_cursor_execute", capture)
            try:
                first = await ProcessingStatusApplication(runtime).for_scope(scope).status("topic-memory")
                second = await ProcessingStatusApplication(runtime).for_scope(scope).status("topic-memory")
            finally:
                event.remove(engine, "before_cursor_execute", capture)
            assert first.state is second.state is ProcessingStatusState.BLOCKED
            assert first.block is not None and first.block.persisted is persisted
            assert first.block.diagnostics is not None
            assert first.block.diagnostics.stage is ProcessingStage.GENERATION
            assert first.block.requests == 4 and first.block.tokens == 120
            if not persisted:
                assert first.block.block_id is None and first.block.blocked_at is None
            assert all(
                not any(word in sql.upper() for word in ("INSERT", "UPDATE", "DELETE", "FOR UPDATE"))
                for sql in statements
            )
            async with runtime.primary_database.transaction() as connection:
                blocks = (await connection.execute(select(TOPIC_MEMORY_WORK_BLOCKS_TABLE))).all()
                assert len(blocks) == int(persisted)

    asyncio.run(scenario())


@pytest.mark.parametrize("dream_pending", [None, False, True])
def test_topic_block_survives_unavailable_dream_observations(tmp_path, monkeypatch, dream_pending):
    async def dream_status(self, connection, scope, family):
        if dream_pending is None:
            raise OperationalError("query", {}, RuntimeError("private-dream-query"))
        return (), dream_pending

    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            scope = scopes[0]
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="one", content="private", metadata={})
            )
            async with runtime.primary_database.transaction() as connection:
                await _topic_budget(connection, scope)
            monkeypatch.setattr(ProcessingStatusInspector, "_dream_status", dream_status)
            result = await ProcessingStatusApplication(runtime).for_scope(scope).status("topic-memory")
            assert result.block is not None
            assert result.state is (
                ProcessingStatusState.PENDING if dream_pending is True else ProcessingStatusState.BLOCKED
            )
            assert result.dream_pending is dream_pending
            assert result.unavailable_reason == ("dream_query_failed" if dream_pending is None else None)
            assert "private" not in repr(result)

    asyncio.run(scenario())


def test_retry_removes_current_block_from_projection(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            scope = scopes[0]
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="one", content="private", metadata={})
            )
            async with runtime.primary_database.transaction() as connection:
                await _topic_budget(connection, scope)
            first = await ProcessingStatusApplication(runtime).for_scope(scope).status("topic-memory")
            assert first.block is not None
            async with runtime.primary_database.transaction() as connection:
                await connection.execute(update(TOPIC_MEMORY_WORK_BUDGETS_TABLE).values(attempts=1, attempt_id="new"))
            result = await ProcessingStatusApplication(runtime).for_scope(scope).status("topic-memory")
            assert result.block is None and result.state is ProcessingStatusState.PENDING

    asyncio.run(scenario())


def test_only_scope_bound_local_observations_identify_executor(tmp_path, monkeypatch):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            local = ProcessingObservation(
                binding=FAMILY_BINDINGS["memory"],
                family="memory",
                state="running",
                observed_at=datetime.now(UTC),
                scheduler_observed=True,
                worker_id="worker",
            )
            monkeypatch.setattr(
                ArtifactProcessingSupervisors,
                "observe_scope",
                lambda self, scope: (local,) if scope == scopes[0] else (),
            )
            supervisors = ArtifactProcessingSupervisors(())
            async with runtime.primary_database.transaction(consistent_snapshot=True) as connection:
                result = await ProcessingStatusInspector().status(
                    connection, scopes[0], "memory", supervisors=supervisors
                )
                other = await ProcessingStatusInspector().status(
                    connection, scopes[1], "memory", supervisors=supervisors
                )
            assert result.executor is local and other.executor is None
            restarted = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status("memory")
            assert restarted.executor is None

    asyncio.run(scenario())


@pytest.mark.parametrize("finished", [False, True])
def test_active_final_topic_attempt_is_not_reported_as_a_block(tmp_path, finished):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            scope = scopes[0]
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="one", content="private", metadata={})
            )
            async with runtime.primary_database.transaction() as connection:
                await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "owner")
                await _topic_budget(connection, scope, started=True, finished=finished)
            result = await ProcessingStatusApplication(runtime).for_scope(scope).status("topic-memory")
            assert (result.block is not None) is finished
            assert result.state is (ProcessingStatusState.BLOCKED if finished else ProcessingStatusState.PENDING)

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["memory", "experience", "profile", "skill", "handoff", "prompt", "topic-memory"])
def test_public_inspection_does_not_flush_usage_or_trigger_work(tmp_path, monkeypatch, family):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):

            def forbidden(*args, **kwargs):
                pytest.fail("inspection must not enter a usage-flushing or model-owning context")

            monkeypatch.setattr(runtime, "_scoped_operation", forbidden)
            if runtime._topic_memory_recovery is not None:
                monkeypatch.setattr(runtime._topic_memory_recovery, "status", forbidden)
            statements = []

            def capture(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            engine = runtime.primary_database.engine.sync_engine
            event.listen(engine, "before_cursor_execute", capture)
            try:
                for _ in range(2):
                    await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status(family)
            finally:
                event.remove(engine, "before_cursor_execute", capture)
            assert all(
                not any(word in sql.upper() for word in ("INSERT", "UPDATE", "DELETE", "FOR UPDATE"))
                for sql in statements
            )
            assert len(statements) < 25

    asyncio.run(scenario())


def test_snapshot_lease_uses_pre_snapshot_clock_not_later_time(tmp_path, monkeypatch):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            scope = scopes[0]
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="one", content="private", metadata={})
            )
            base = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
            async with runtime.primary_database.transaction() as connection:
                await _topic_budget(connection, scope, started=True)
                await connection.execute(
                    insert(ARTIFACT_PROCESSING_LEASES_TABLE).values(
                        supervisor_group="global",
                        holder_id="owner",
                        supervisor_generation=1,
                        lease_expires_at=base + timedelta(seconds=10),
                    )
                )
            calls = []

            async def clock(connection):
                calls.append(None)
                return base if len(calls) == 1 else base + timedelta(seconds=20)

            monkeypatch.setattr("powercontext.builtin.processing_status.database_utc_now", clock)
            original = ScopeRepository.get

            async def delayed_read(self, connection, selected, *args, **kwargs):
                await clock(connection)
                return await original(self, connection, selected, *args, **kwargs)

            monkeypatch.setattr(ScopeRepository, "get", delayed_read)
            result = await ProcessingStatusApplication(runtime).for_scope(scope).status("topic-memory")
            assert len(calls) >= 2
            assert result.state is ProcessingStatusState.PENDING
            assert result.block is None

    asyncio.run(scenario())


def test_nonexistent_scope_and_unsupported_family_stay_distinct(tmp_path):
    async def scenario():
        async with _runtime(tmp_path) as (runtime, scopes):
            with pytest.raises(ScopeNotFoundError):
                await ProcessingStatusApplication(runtime).for_scope("missing").status("skill")
            result = await ProcessingStatusApplication(runtime).for_scope(scopes[0]).status("custom-family")
            assert result.state is ProcessingStatusState.UNKNOWN
            assert result.unavailable_reason == "family_not_supported"
            assert result.progress is None and result.executor is None

    asyncio.run(scenario())
