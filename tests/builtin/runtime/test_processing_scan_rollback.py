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
from datetime import timedelta

import pytest
from sqlalchemy import event

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingBindingStateRepository
from powercontext.builtin.runtime import artifact_processing as processing
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from tests.builtin.runtime.test_processing_scheduler import _intent, _Launcher, _profile, _request


class _InjectedRollback(Exception):
    pass


class _FailingIntents(ArtifactProcessingIntentRepository):
    boundary: str | None = None

    async def admit(self, connection, scope_id, binding_name, scan_generation, /):
        result = await super().admit(connection, scope_id, binding_name, scan_generation)
        if self.boundary is not None and scope_id in {"b", "d"}:
            boundary, self.boundary = self.boundary, None
            if boundary == "cancel":
                raise asyncio.CancelledError
            raise _InjectedRollback
        return result


class _FailingBindingStates(ArtifactProcessingBindingStateRepository):
    fail_finish = False

    async def finish_scan(self, connection, binding_name, /):
        await super().finish_scan(connection, binding_name)
        if self.fail_finish:
            self.fail_finish = False
            raise _InjectedRollback


@pytest.mark.parametrize("boundary", ["start", "eligibility", "admit", "finish", "commit", "cancel"])
def test_automatic_page_rollback_retains_frontier_and_resumes_same_scan(tmp_path, monkeypatch, boundary):  # noqa: C901
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():  # noqa: C901 - exercise transaction failure boundaries
        async with _profile(tmp_path, "scan-rollback") as profile:
            scopes = ("a", "b", "c", "d", "e")
            for scope in scopes:
                await _request(profile.database, "memory-binding", scope, requested=False)
            intents = _FailingIntents()
            states = _FailingBindingStates()
            launcher = _Launcher(profile.database)
            fail_eligibility = False

            async def eligible_scopes(connection, scopes):
                nonlocal fail_eligibility
                if fail_eligibility:
                    fail_eligibility = False
                    raise _InjectedRollback
                return frozenset(scopes)

            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding",
                        "memory",
                        launcher,
                        max_workers=2,
                        automatic_processing_interval=timedelta(days=30),
                        automatic_scope_filter=eligible_scopes,
                    ),
                ),
                lease_mode="single-process",
                intents=intents,
                binding_states=states,
            )
            await supervisor._acquire()
            state = supervisor._families["memory-binding"]

            async def persisted():
                async with profile.database.transaction() as connection:
                    return await states.load(connection, "memory-binding")

            async def page_and_dispatch():
                await supervisor._discover_automatic(state)
                await supervisor._dispatch(state)
                await asyncio.gather(*(running.task for running in state.running.values()))
                await supervisor._reap(state)

            def frontier():
                return (
                    state.scan_after,
                    state.scan_generation,
                    state.scan_in_progress,
                    state.next_schedule_at,
                    state.next_discovery_at,
                    tuple(state.ready),
                    frozenset(state.queued),
                )

            try:
                # Leave a durable in-progress generation with a successfully
                # committed prefix. The final-page case fails while finishing it.
                for _ in range(0 if boundary == "start" else 2 if boundary == "finish" else 1):
                    await page_and_dispatch()
                before = frontier()
                persisted_before = await persisted()
                rows_before = [await _intent(profile.database, "memory-binding", scope) for scope in scopes]
                if boundary in {"start", "admit", "cancel"}:
                    intents.boundary = boundary
                elif boundary == "eligibility":
                    fail_eligibility = True
                elif boundary == "finish":
                    states.fail_finish = True

                def fail_commit(_connection):
                    raise _InjectedRollback

                if boundary == "commit":
                    event.listen(profile.database.engine.sync_engine, "commit", fail_commit, once=True)
                try:
                    with pytest.raises(asyncio.CancelledError if boundary == "cancel" else _InjectedRollback):
                        await supervisor._discover_automatic(state)
                finally:
                    if boundary == "commit":
                        event.remove(profile.database.engine.sync_engine, "commit", fail_commit)
                assert frontier() == before
                assert await persisted() == persisted_before
                assert [await _intent(profile.database, "memory-binding", scope) for scope in scopes] == rows_before

                # A later registration cannot extend a scan whose finite upper
                # bound already committed before the failed page.
                if persisted_before is not None:
                    await _request(profile.database, "memory-binding", "late", requested=False)
                for _ in range(4):
                    await page_and_dispatch()
                    saved = await persisted()
                    if saved is not None and not saved.scan_in_progress:
                        break
                assert saved is not None and not saved.scan_in_progress and saved.scan_generation == 1
                if persisted_before is not None:
                    assert saved.last_schedule_checkpoint_at == persisted_before.last_schedule_checkpoint_at
                    assert (await _intent(profile.database, "memory-binding", "late")).requested_generation == 0
                assert supervisor.family_status["memory"]["completed"] == len(scopes)
                assert [work.scope_id for work in launcher.assignments] == list(scopes)
                for scope in scopes:
                    row = await _intent(profile.database, "memory-binding", scope)
                    assert row.requested_generation == row.handled_generation == row.last_auto_scan_generation == 1
                assert state.next_schedule_at is not None
                assert state.next_schedule_at > asyncio.get_running_loop().time() + 29 * 86400
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())


def test_failed_admission_recovers_in_running_supervisor_without_waiting_for_next_interval(tmp_path, monkeypatch):
    from tests.builtin.runtime.test_processing_scheduler import _wait, _wait_scan_finished

    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "live-scan-rollback") as profile:
            for scope in ("a", "b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope, requested=False)
            states = ArtifactProcessingBindingStateRepository()
            async with profile.database.transaction() as connection:
                started = await states.start_scan(connection, "memory-binding")
            intents = _FailingIntents()
            intents.boundary = "admit"
            launcher = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding",
                        "memory",
                        launcher,
                        max_workers=2,
                        automatic_processing_interval=timedelta(days=30),
                    ),
                ),
                lease_mode="single-process",
                intents=intents,
                oceanbase_tick_seconds=0.01,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                assert [work.scope_id for work in launcher.assignments] == ["a", "b", "c", "d", "e"]
                completed = await _wait_scan_finished(profile.database, "memory-binding")
                assert completed is not None and not completed.scan_in_progress
                assert completed.scan_generation == started.scan_generation
                assert completed.last_schedule_checkpoint_at == started.last_schedule_checkpoint_at

    asyncio.run(scenario())
