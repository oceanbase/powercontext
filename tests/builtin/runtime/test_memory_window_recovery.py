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

"""Memory timeouts shrink durable windows without consuming failed evidence."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.decision_observations import DecisionPrivacyBoundary
from powercontext.builtin.inference import GenerationResult, InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.persistence.decision_observations import DecisionObservationRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.composition import _initialize_atomic_memory_authority
from powercontext.builtin.runtime.memory_write_gate import build_memory_write_gate
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.logging import JsonFormatter
from tests.e2e.dream_support import atomic_memory_pipeline, memory_source_text


class Pipeline:
    def __init__(self, maximum=1, error=None):
        self.maximum = maximum
        self.error = error
        self.windows = []

    async def generate(self, request):
        self.windows.append(tuple(source.source_ref.source_id for source in request.evidence))
        if self.error is not None:
            raise self.error
        if len(request.evidence) > self.maximum:
            raise InferenceTimeoutError("generate", 60)
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="fact", text=text, evidence_ids=(source.evidence_id,), description="Extracted fixture fact"
                    )
                    for source in request.evidence
                    if (text := memory_source_text(source)) is not None
                )
            )
        )


async def _ready_contexts(profile, **kwargs):
    contexts = RelationalContexts(database=profile.database, **kwargs)
    async with profile.database.transaction() as connection:
        await _initialize_atomic_memory_authority(connection)
        await contexts.atomic_memory.index.initialize(connection)
    return contexts


async def create_scope(contexts, count):
    scope = (
        await contexts.scopes.create(ScopeDraft(title="Recovery", summary="Recovery", idempotency_key=str(uuid4())))
    ).scope_id
    for index in range(count):
        await contexts.records.create_source(scope, "content", f"Fact {index}")
    return scope


def test_timeout_reduction_survives_reopen_and_resets_after_backlog(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
        pipeline = Pipeline()
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(profile, candidate_pipeline=atomic_memory_pipeline(pipeline))
            scope = await create_scope(contexts, 4)
            context = await contexts.get(scope)
            with pytest.raises(InferenceTimeoutError):
                await context.triggers.flush(limit=100)
            assert (await context.triggers.cursor()).sequence == 0

        # Reopen the database and rebuild the processor, as a new Worker does.
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(profile, candidate_pipeline=atomic_memory_pipeline(pipeline))
            context = await contexts.get(scope)
            with pytest.raises(InferenceTimeoutError):
                await context.triggers.flush(limit=100)
            assert (await context.triggers.cursor()).sequence == 0
            assert len(pipeline.windows[-1]) < len(pipeline.windows[0])
            for position in range(1, 5):
                result = await context.triggers.flush(limit=100)
                assert result.current_cursor == position
                assert result.memory_ref is None
                assert len((await contexts.atomic_memory.for_scope(scope).list()).items) == position
            assert tuple(item for window in pipeline.windows[2:] for item in window) == pipeline.windows[0]

            pipeline.maximum = 4
            for index in range(4):
                await contexts.records.create_source(scope, "content", f"New fact {index}")
            result = await context.triggers.flush(limit=100)
            assert result.source_count == 4
            assert result.current_cursor == 8

    asyncio.run(scenario())


def test_single_source_timeout_preserves_cursor_and_can_recover():
    async def scenario():
        pipeline = Pipeline(error=InferenceTimeoutError("generate", 60))
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(profile, candidate_pipeline=atomic_memory_pipeline(pipeline))
            scope = await create_scope(contexts, 1)
            context = await contexts.get(scope)
            for _ in range(2):
                with pytest.raises(InferenceTimeoutError):
                    await context.triggers.flush(limit=100)
                assert (await context.triggers.cursor()).sequence == 0
            pipeline.error = None
            assert (await context.triggers.flush(limit=100)).current_cursor == 1
            assert pipeline.windows[0] == pipeline.windows[-1]

    asyncio.run(scenario())


def test_atomic_memory_gate_finalizes_one_applied_observation_in_the_commit_transaction(
    tmp_path, caplog: pytest.LogCaptureFixture
):
    """A no-call gate still has one durable, final audit outcome after Atomic Memory publishes."""

    async def scenario():
        gate = build_memory_write_gate(
            None,
            enabled=True,
            privacy_boundary=DecisionPrivacyBoundary.NO_EXTERNAL_CALL,
        )
        assert gate is not None
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-gate.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(
                profile,
                candidate_pipeline=atomic_memory_pipeline(Pipeline()),
                memory_write_gate=gate,
            )
            scope = await create_scope(contexts, 1)
            context = await contexts.get(scope)

            assert (await context.triggers.flush(limit=1)).current_cursor == 1

            async with profile.database.transaction() as connection:
                observations = await DecisionObservationRepository().observations(connection, scope)
            assert len(observations) == 1
            observation = observations[0]
            assert observation.final_action == "memory_write_applied"
            assert observation.privacy_outcome == "no_external_call"
            assert observation.subject_refs

    caplog.set_level("INFO", logger="powercontext.builtin.runtime.decision_policy")
    asyncio.run(scenario())
    events = [record for record in caplog.records if getattr(record, "event", None) == "decision.observation"]
    assert [record.__dict__["final_action"] for record in events] == ["memory_write_applied"]


def test_atomic_memory_gate_emits_failed_outcome_after_late_transaction_rollback(
    tmp_path, caplog: pytest.LogCaptureFixture
):
    async def scenario():
        gate = build_memory_write_gate(
            None,
            enabled=True,
            privacy_boundary=DecisionPrivacyBoundary.NO_EXTERNAL_CALL,
        )
        assert gate is not None
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-gate-rollback.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(
                profile,
                candidate_pipeline=atomic_memory_pipeline(Pipeline()),
                memory_write_gate=gate,
            )
            scope = await create_scope(contexts, 1)
            context = await contexts.get(scope)
            async with profile.database.transaction() as connection:
                await connection.execute(
                    text(
                        "CREATE TRIGGER reject_memory_cursor "
                        "BEFORE INSERT ON pc_source_cursors "
                        "BEGIN SELECT RAISE(ABORT, 'cursor rejected'); END"
                    )
                )

            with pytest.raises(IntegrityError, match="cursor rejected"):
                await context.triggers.flush(limit=1)

            assert (await context.triggers.cursor()).sequence == 0
            assert not (await contexts.atomic_memory.for_scope(scope).list()).items
            async with profile.database.transaction() as connection:
                assert not await DecisionObservationRepository().observations(connection, scope)

    caplog.set_level("INFO", logger="powercontext.builtin.runtime.decision_policy")
    asyncio.run(scenario())
    events = [record for record in caplog.records if getattr(record, "event", None) == "decision.observation"]
    assert [record.__dict__["final_action"] for record in events] == ["memory_write_failed"]


def test_atomic_memory_audit_failure_does_not_block_shadow_write(tmp_path, caplog: pytest.LogCaptureFixture):
    async def scenario():
        gate = build_memory_write_gate(
            None,
            enabled=True,
            privacy_boundary=DecisionPrivacyBoundary.NO_EXTERNAL_CALL,
        )
        assert gate is not None
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-gate-audit.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(
                profile,
                candidate_pipeline=atomic_memory_pipeline(Pipeline()),
                memory_write_gate=gate,
            )
            scope = await create_scope(contexts, 1)
            context = await contexts.get(scope)
            async with profile.database.transaction() as connection:
                await connection.execute(
                    text(
                        "CREATE TRIGGER reject_decision_observation "
                        "BEFORE INSERT ON pc_decision_observations "
                        "BEGIN SELECT RAISE(ABORT, 'audit rejected'); END"
                    )
                )

            assert (await context.triggers.flush(limit=1)).current_cursor == 1
            assert len((await contexts.atomic_memory.for_scope(scope).list()).items) == 1
            async with profile.database.transaction() as connection:
                assert not await DecisionObservationRepository().observations(connection, scope)

    caplog.set_level("WARNING", logger="powercontext.builtin.runtime.atomic_memory_processing")
    asyncio.run(scenario())
    diagnostics = [
        record for record in caplog.records if getattr(record, "event", None) == "decision.observation_sink_failed"
    ]
    assert len(diagnostics) == 1


def test_atomic_memory_audit_rollback_does_not_consume_source_window(tmp_path):
    """An audit failure that rolls back the owner transaction must retain the source window."""

    async def scenario():
        gate = build_memory_write_gate(
            None,
            enabled=True,
            privacy_boundary=DecisionPrivacyBoundary.NO_EXTERNAL_CALL,
        )
        assert gate is not None
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-gate-audit-rollback.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(
                profile,
                candidate_pipeline=atomic_memory_pipeline(Pipeline()),
                memory_write_gate=gate,
            )
            scope = await create_scope(contexts, 1)
            context = await contexts.get(scope)
            async with profile.database.transaction() as connection:
                await connection.execute(
                    text(
                        "CREATE TRIGGER rollback_decision_observation "
                        "BEFORE INSERT ON pc_decision_observations "
                        "BEGIN SELECT RAISE(ROLLBACK, 'audit rolled back'); END"
                    )
                )

            with pytest.raises(OperationalError, match="savepoint"):
                await context.triggers.flush(limit=1)
            assert (await context.triggers.cursor()).sequence == 0
            assert not (await contexts.atomic_memory.for_scope(scope).list()).items

    asyncio.run(scenario())


def test_atomic_memory_audit_failure_log_omits_database_exception(tmp_path, caplog: pytest.LogCaptureFixture):
    """Audit diagnostics expose only a bounded exception class, never a driver payload or traceback."""

    marker = "x" * 8192

    async def scenario():
        gate = build_memory_write_gate(
            None,
            enabled=True,
            privacy_boundary=DecisionPrivacyBoundary.NO_EXTERNAL_CALL,
        )
        assert gate is not None
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-gate-audit-log.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(
                profile,
                candidate_pipeline=atomic_memory_pipeline(Pipeline()),
                memory_write_gate=gate,
            )
            scope = await create_scope(contexts, 1)
            context = await contexts.get(scope)
            async with profile.database.transaction() as connection:
                await connection.execute(
                    text(
                        "CREATE TRIGGER reject_decision_observation_with_marker "
                        "BEFORE INSERT ON pc_decision_observations "
                        f"BEGIN SELECT RAISE(ABORT, '{marker}'); END"
                    )
                )

            assert (await context.triggers.flush(limit=1)).current_cursor == 1

    caplog.set_level("WARNING", logger="powercontext.builtin.runtime.atomic_memory_processing")
    asyncio.run(scenario())
    diagnostic = next(
        record for record in caplog.records if getattr(record, "event", None) == "decision.observation_sink_failed"
    )
    rendered = JsonFormatter().format(diagnostic)
    assert diagnostic.exc_info is None
    assert diagnostic.__dict__["exception_type"] == "IntegrityError"
    assert marker not in rendered
    assert '"exception_type":"IntegrityError"' in rendered


@pytest.mark.parametrize("error", [InferenceTimeoutError("embed", 60), InferenceUnavailableError("generate")])
def test_non_extraction_failures_do_not_reduce_source_window(error):
    async def scenario():
        pipeline = Pipeline(maximum=4, error=error)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(profile, candidate_pipeline=atomic_memory_pipeline(pipeline))
            scope = await create_scope(contexts, 4)
            context = await contexts.get(scope)
            with pytest.raises(type(error)):
                await context.triggers.flush(limit=100)
            pipeline.error = None
            result = await context.triggers.flush(limit=100)
            assert result.current_cursor == 4
            assert pipeline.windows[0] == pipeline.windows[-1]

    asyncio.run(scenario())


def test_stale_timeout_cannot_rewind_concurrently_committed_cursor():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        retry_windows = []

        class DelayedTimeout:
            async def generate(self, request):
                retry_windows.append(tuple(source.source_ref.source_id for source in request.evidence))
                if len(retry_windows) > 1:
                    raise InferenceUnavailableError("generate")
                entered.set()
                await release.wait()
                raise InferenceTimeoutError("generate", 60)

        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            first = await _ready_contexts(profile, candidate_pipeline=atomic_memory_pipeline(DelayedTimeout()))
            second = await _ready_contexts(profile, candidate_pipeline=atomic_memory_pipeline(Pipeline(maximum=4)))
            scope = await create_scope(first, 4)
            first_context, second_context = await first.get(scope), await second.get(scope)
            task = asyncio.create_task(first_context.triggers.flush(limit=4))
            await asyncio.wait_for(entered.wait(), timeout=5)
            try:
                assert (await second_context.triggers.flush(limit=2)).current_cursor == 2
            finally:
                release.set()
            with pytest.raises(InferenceUnavailableError):
                await task
            assert retry_windows[1] == retry_windows[0][2:]
            assert (await second_context.triggers.cursor()).sequence == 2
            recovered = await second_context.triggers.flush(limit=4)
            assert recovered.previous_cursor == 2 and recovered.current_cursor == 4
            assert recovered.source_count == 2
            entries = (await second.atomic_memory.for_scope(scope).list()).items
            assert sorted(item.artifact.content.text for item in entries) == [f"Fact {index}" for index in range(4)]

    asyncio.run(scenario())


def test_failed_commit_retains_reduction_until_consumption_succeeds():
    async def scenario():
        pipeline = Pipeline()
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = await _ready_contexts(profile, candidate_pipeline=atomic_memory_pipeline(pipeline))
            scope = await create_scope(contexts, 2)
            context = await contexts.get(scope)
            with pytest.raises(InferenceTimeoutError):
                await context.triggers.flush(limit=100)
            assert (await context.triggers.flush(limit=1)).current_cursor == 1
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql(
                    "CREATE TRIGGER fail_memory_write BEFORE INSERT ON pc_artifacts "
                    "WHEN NEW.family = 'atomic-memory' "
                    "BEGIN SELECT RAISE(ABORT, 'commit failed'); END"
                )
            try:
                with pytest.raises(IntegrityError, match="commit failed"):
                    await contexts.process_memory(scope, 100)
            finally:
                async with profile.database.transaction() as connection:
                    await connection.exec_driver_sql("DROP TRIGGER fail_memory_write")
            assert (await context.triggers.cursor()).sequence == 1
            await contexts.records.create_source(scope, "content", "New input while retrying")
            # The failed final commit must roll back clearing the reduction too.
            assert (await context.triggers.flush(limit=100)).current_cursor == 2
            assert (await context.triggers.flush(limit=100)).current_cursor == 3

    asyncio.run(scenario())
