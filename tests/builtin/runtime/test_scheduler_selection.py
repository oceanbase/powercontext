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

from sqlalchemy import update

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import SOURCE_CURSORS_TABLE
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from tests.e2e.dream_support import atomic_memory_pipeline


class _Extractor:
    async def generate(self, request):
        return GenerationResult(output=AtomicMemoryExtractionOutput())


def _config(tmp_path):
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'selection.db'}"),
        runtime=RuntimeConfig(source_window_limit=1, atomic_memory_related_mode="fts"),
    )


async def _scope(runtime, name):
    return await runtime.scopes.create(ScopeDraft(title=name, summary=name, idempotency_key=name))


async def _capture(runtime, scope_id, source_id):
    await runtime.sources.for_scope(scope_id).capture(
        CaptureSource(source_id=source_id, content=f"Fact {source_id}", metadata={})
    )


def test_buffered_windows_remain_eligible_without_new_sources(tmp_path):
    async def scenario():
        async with open_builtin_runtime(
            _config(tmp_path), candidate_pipeline=atomic_memory_pipeline(_Extractor())
        ) as runtime:
            assert runtime.atomic_memory is not None
            assert runtime.processor is not None
            scope = await _scope(runtime, "Buffered")
            await _capture(runtime, scope.scope_id, "one")
            await _capture(runtime, scope.scope_id, "two")
            await runtime.processor.run()
            assert (await runtime.atomic_memory.for_scope(scope.scope_id).cursor()).sequence == 1
            await runtime.processor.run()
            assert (await runtime.atomic_memory.for_scope(scope.scope_id).cursor()).sequence == 2

    asyncio.run(scenario())


def test_failed_scope_does_not_suppress_peers_or_concurrently_captured_work(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        attempted = []

        async def runner(scope_id, runtime):
            attempted.append(scope_id)
            if len(attempted) == 1:
                entered.set()
                await release.wait()
                raise RuntimeError("Injected first-Scope failure")  # noqa: TRY003
            return await runtime.atomic_memory.for_scope(scope_id).flush()

        async with open_builtin_runtime(
            _config(tmp_path),
            candidate_pipeline=atomic_memory_pipeline(_Extractor()),
            scheduled_source_runner=runner,
        ) as runtime:
            assert runtime.atomic_memory is not None
            assert runtime.processor is not None
            scopes = [await _scope(runtime, name) for name in ("First", "Second", "Late")]
            for scope in scopes:
                await _capture(runtime, scope.scope_id, "one")
            await runtime.atomic_memory.for_scope(scopes[2].scope_id).flush()
            tick = asyncio.create_task(runtime.processor.run())
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                await _capture(runtime, scopes[2].scope_id, "two")
            finally:
                release.set()
                await tick
            first_attempts = set(attempted)
            assert {scope.scope_id for scope in scopes[:2]} <= first_attempts
            await runtime.processor.run()
            for scope in scopes[:2]:
                assert (await runtime.atomic_memory.for_scope(scope.scope_id).cursor()).sequence == 1
            assert (await runtime.atomic_memory.for_scope(scopes[2].scope_id).cursor()).sequence == 2

    asyncio.run(scenario())


def test_invalid_cursor_isolated_from_other_scopes(tmp_path):
    async def scenario():
        async with open_builtin_runtime(
            _config(tmp_path), candidate_pipeline=atomic_memory_pipeline(_Extractor())
        ) as runtime:
            assert runtime.atomic_memory is not None
            assert runtime.processor is not None
            broken = await _scope(runtime, "Broken")
            peer = await _scope(runtime, "Peer")
            for scope in (broken, peer):
                await _capture(runtime, scope.scope_id, "one")
            await runtime.atomic_memory.for_scope(broken.scope_id).flush()
            async with runtime.primary_database.transaction() as connection:
                await connection.execute(
                    update(SOURCE_CURSORS_TABLE)
                    .where(SOURCE_CURSORS_TABLE.c.scope_id == broken.scope_id)
                    .values(cursor=b"invalid cursor")
                )
            await runtime.processor.run()
            assert (await runtime.atomic_memory.for_scope(peer.scope_id).cursor()).sequence == 1

    asyncio.run(scenario())


def test_refilled_scope_does_not_starve_other_selected_windows(tmp_path):
    async def scenario():
        active_scope = ""
        refill = 0

        async def runner(scope_id, runtime):
            nonlocal refill
            if scope_id == active_scope:
                refill += 1
                await _capture(runtime, scope_id, f"refill-{refill}")
            return await runtime.atomic_memory.for_scope(scope_id).flush()

        async with open_builtin_runtime(
            _config(tmp_path),
            candidate_pipeline=atomic_memory_pipeline(_Extractor()),
            scheduled_source_runner=runner,
        ) as runtime:
            assert runtime.atomic_memory is not None
            assert runtime.processor is not None
            scopes = [await _scope(runtime, name) for name in ("Active", "Peer A", "Peer B")]
            active_scope = min(scope.scope_id for scope in scopes)
            for scope in scopes:
                await _capture(runtime, scope.scope_id, "initial")
            await asyncio.wait_for(runtime.processor.run(), timeout=5)
            for scope in scopes:
                assert (await runtime.atomic_memory.for_scope(scope.scope_id).cursor()).sequence == 1
            await asyncio.wait_for(runtime.processor.run(), timeout=5)
            assert (await runtime.atomic_memory.for_scope(active_scope).cursor()).sequence == 2
            # The active Scope is still dirty after both ticks; its peers were
            # processed without waiting for that journal to become clean.
            result = await runtime.atomic_memory.for_scope(active_scope).flush()
            assert result.source_count == 1

    asyncio.run(scenario())
