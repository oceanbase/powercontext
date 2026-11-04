# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.work import StaleWorkClaimError, WorkRepository
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.models import MemoryFlushResult
from powercontext.builtin.runtime.work_handlers import (
    MEMORY_WORK_KIND,
    MemoryWorkHandler,
    memory_work_spec,
)
from powercontext.builtin.sources import ContentCapture, SourceCursor
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME


def _config(tmp_path):
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"))


async def _claim(contexts, spec):
    repository = WorkRepository()
    async with contexts.database.transaction() as connection:
        await repository.enqueue(connection, spec)
        claims = await repository.claim(
            connection,
            worker_id="worker-a",
            supported={MEMORY_WORK_KIND: frozenset({1})},
            lease_seconds=30,
            limit=1,
        )
    return claims[0]


class _StubPreparedWindow:
    """A prepared window whose publication records its connection."""

    def __init__(self, contexts, published):
        self._contexts = contexts
        self._published = published

    async def publish(self, connection, processing):
        await self._contexts.repositories.cursors.save(
            connection,
            "project",
            SOURCE_WINDOW_TRIGGER_NAME,
            SourceCursor(sequence=1),
            expected_generation=None,
        )
        self._published.append(connection)
        return MemoryFlushResult(
            previous_cursor=0,
            current_cursor=1,
            high_watermark=1,
            source_count=1,
            memory_ref=None,
        )

    def emit_finalized(self) -> None:
        pass

    def emit_failed(self) -> None:
        pass


def _stub_prepare_memory(contexts, published):
    async def fake_prepare_memory(scope_id: str, limit: int, **_: object) -> _StubPreparedWindow:
        return _StubPreparedWindow(contexts, published)

    return fake_prepare_memory


def test_memory_work_spec_tracks_the_unconsumed_source_window(tmp_path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            assert await memory_work_spec(contexts, "project", limit=10, max_attempts=3, payload_version=1) is None
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="remember this"))

            spec = await memory_work_spec(contexts, "project", limit=10, max_attempts=3, payload_version=1)
            assert spec is not None
            assert spec.kind == MEMORY_WORK_KIND
            assert spec.scope_id == "project"
            assert spec.payload["cursor_name"] == SOURCE_WINDOW_TRIGGER_NAME
            assert spec.payload["after"] == 0
            assert spec.payload["through"] == 1

    asyncio.run(scenario())


def test_memory_work_handler_defers_publication_to_the_fenced_commit(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="remember this"))
            spec = await memory_work_spec(contexts, "project", limit=10, max_attempts=3, payload_version=1)
            assert spec is not None
            claim = await _claim(contexts, spec)

            published: list[object] = []
            monkeypatch.setattr(contexts, "prepare_memory", _stub_prepare_memory(contexts, published))
            prepared = await MemoryWorkHandler(contexts, window_limit=10).prepare(claim)
            # Preparation publishes nothing; the domain write joins the fenced
            # Work completion transaction through the commit closure.
            assert prepared.result is None
            assert prepared.commit is not None
            assert published == []

            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                work = await repository.complete(connection, claim, None, commit=prepared.commit)
            assert work.status.value == "succeeded"
            assert len(published) == 1
            async with contexts.database.transaction() as connection:
                row = await contexts.repositories.cursors.load(connection, "project", SOURCE_WINDOW_TRIGGER_NAME)
            assert row is not None and row.cursor.sequence == 1

            repeated = await MemoryWorkHandler(contexts, window_limit=10).prepare(claim)
            assert repeated.result is not None
            assert repeated.result.code == "already_committed"

    asyncio.run(scenario())


def test_memory_publication_is_fenced_by_a_cancellation_before_commit(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="remember this"))
            spec = await memory_work_spec(contexts, "project", limit=10, max_attempts=3, payload_version=1)
            assert spec is not None
            claim = await _claim(contexts, spec)

            published: list[object] = []
            monkeypatch.setattr(contexts, "prepare_memory", _stub_prepare_memory(contexts, published))
            prepared = await MemoryWorkHandler(contexts, window_limit=10).prepare(claim)
            assert prepared.commit is not None

            # A cancel accepted while preparation is paused must fence the
            # later publication: the completion transaction sees cancel_requested
            # and rolls the domain write back with it.
            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                stored = await repository.get(connection, claim.work_id)
                await repository.cancel(
                    connection,
                    claim.work_id,
                    expected_version=stored.state_version,
                )
            with pytest.raises(StaleWorkClaimError):
                async with contexts.database.transaction() as connection:
                    await repository.complete(connection, claim, None, commit=prepared.commit)
            assert published == []
            async with contexts.database.transaction() as connection:
                row = await contexts.repositories.cursors.load(connection, "project", SOURCE_WINDOW_TRIGGER_NAME)
            assert row is None or row.cursor.sequence == 0

    asyncio.run(scenario())


def test_memory_work_handler_returns_the_trigger_result_without_a_commit(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="remember this"))
            spec = await memory_work_spec(contexts, "project", limit=10, max_attempts=3, payload_version=1)
            assert spec is not None
            claim = await _claim(contexts, spec)

            async def fake_prepare_memory(scope_id: str, limit: int, **_: object) -> MemoryFlushResult:
                # The trigger selected nothing to publish for this attempt.
                return MemoryFlushResult(
                    previous_cursor=1,
                    current_cursor=1,
                    high_watermark=1,
                    source_count=0,
                    memory_ref=None,
                )

            monkeypatch.setattr(contexts, "prepare_memory", fake_prepare_memory)
            prepared = await MemoryWorkHandler(contexts, window_limit=10).prepare(claim)
            assert prepared.commit is None
            assert prepared.result is not None
            assert prepared.result.code == "idle"
            assert prepared.result.payload["current_cursor"] == 1

    asyncio.run(scenario())


def test_memory_work_handler_pins_preparation_to_the_persisted_window(tmp_path, monkeypatch) -> None:
    """A Source captured after the Work record must stay outside this attempt's window."""

    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="remember this"))
            spec = await memory_work_spec(contexts, "project", limit=100, max_attempts=3, payload_version=1)
            assert spec is not None
            assert (spec.payload["after"], spec.payload["through"]) == (0, 1)
            claim = await _claim(contexts, spec)

            # The second Source lands between discovery and execution. Preparing the
            # live window would absorb it into this claim and report high_watermark=2.
            await context.sources.capture(ContentCapture(source_id="two", content="arrived later"))

            requested: dict[str, object] = {}

            async def fake_prepare_memory(scope_id: str, limit: int, *, window=None, **_: object):
                requested["window"] = window
                return _StubPreparedWindow(contexts, [])

            monkeypatch.setattr(contexts, "prepare_memory", fake_prepare_memory)
            prepared = await MemoryWorkHandler(contexts, window_limit=100).prepare(claim)

            assert prepared.commit is not None
            # The persisted boundaries, not the live high watermark (0, 2).
            assert requested["window"] == (0, 1)

    asyncio.run(scenario())


def test_prepare_memory_refuses_a_window_the_cursor_already_left(tmp_path) -> None:
    """A claim whose persisted window no longer matches the cursor must not publish."""

    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            await contexts.get("project")
            async with contexts.database.transaction() as connection:
                await contexts.repositories.cursors.save(
                    connection,
                    "project",
                    SOURCE_WINDOW_TRIGGER_NAME,
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )
            with pytest.raises(AtomicMemoryConflictError):
                await contexts.prepare_memory("project", 100, window=(0, 1))

    asyncio.run(scenario())
