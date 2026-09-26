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
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import event, func, select

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    MemoryCapacityBudget,
    MemoryCapacityExceededError,
    MemoryCompactionPolicy,
    MemoryEntryInput,
    MemoryEntryNotFoundError,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.canonical import memory_content_bytes
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.tags import MemoryEntryTagTarget
from powercontext.errors import RevisionConflictError


@pytest.fixture(params=("sqlite", "oceanbase"))
def database_config(request):
    if request.param == "sqlite":
        return SQLiteConfig()
    url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not url:
        pytest.skip("requires a disposable POWERCONTEXT_TEST_OCEANBASE_URL database")
    return OceanBaseConfig(url=SecretStr(url))


@asynccontextmanager
async def memory_context(database_config, **settings):
    config = BuiltinConfig(database=database_config, runtime=RuntimeConfig(**settings))
    async with open_builtin_contexts(config) as contexts:
        scope_id = "capacity-" + uuid4().hex
        context = await contexts.get(scope_id)
        backend = RelationalMemoryBackend(
            database=contexts.database,
            scope_id=scope_id,
            artifacts=contexts.repositories.artifacts,
            index=contexts.index,
        )
        yield contexts, scope_id, context.artifacts.memory, backend


def fact(number, **values):
    return MemoryEntryInput(kind="fact", text=f"Capacity project fact {number}.", **values)


async def row_counts(contexts, scope_id):
    async with contexts.database.connection() as connection:
        return tuple([
            await connection.scalar(select(func.count()).select_from(table).where(table.c.scope_id == scope_id))
            for table in (ARTIFACTS_TABLE, MEMORY_ENTRY_VERSIONS_TABLE, MEMORY_ENTRY_HEADS_TABLE)
        ])


@pytest.mark.parametrize("dimension", ["manifest_entries", "active_entries", "manifest_bytes"])
def test_refusal_is_deterministic_and_persists_nothing(database_config, dimension):
    async def scenario():
        async with memory_context(database_config) as (contexts, scope_id, service, backend):
            memory = await service.remember(memory=None, entries=(fact(1), fact(2)), mode="append")
            assert memory is not None
            budget = MemoryCapacityBudget(
                max_active_entries=2 if dimension != "manifest_bytes" else 100,
                max_manifest_entries=2 if dimension == "manifest_entries" else 100,
                max_manifest_bytes=1024 if dimension == "manifest_bytes" else 4_194_304,
            )
            limited = MemoryService(backend=backend, capacity_budget=budget)
            before = await row_counts(contexts, scope_id)
            errors = []
            for _ in range(2):
                with pytest.raises(MemoryCapacityExceededError) as caught:
                    await limited.remember(memory=memory, entries=(fact(3, reason="x" * 512),), mode="append")
                errors.append((caught.value.dimension, caught.value.limit, caught.value.observed))
            assert errors[0] == errors[1]
            assert errors[0][0] == dimension
            assert errors[0][2] > errors[0][1]
            if dimension == "manifest_bytes":
                plan = await service.plan_remember(memory=memory, entries=(fact(3, reason="x" * 512),), mode="append")
                assert errors[0][2] == len(memory_content_bytes(plan.result.content))
            assert await limited.head(memory.artifact_id) == memory
            assert await row_counts(contexts, scope_id) == before
            capacity = await limited.capacity(memory)
            assert capacity.manifest_bytes == len(memory_content_bytes(memory.content))
            assert capacity.active_entry_count == capacity.manifest_entry_count == 2

    asyncio.run(scenario())


def test_runtime_budget_and_over_budget_non_growth(database_config):
    async def scenario():
        async with memory_context(database_config, memory_max_active_entries=2, memory_max_manifest_entries=2) as (
            _,
            _,
            service,
            backend,
        ):
            memory = await service.remember(memory=None, entries=(fact(1), fact(2)), mode="append")
            assert memory is not None
            assert (await service.capacity(memory)).budget.max_manifest_entries == 2
            with pytest.raises(MemoryCapacityExceededError):
                await service.remember(memory=memory, entries=(fact(3),), mode="append")
            # Lower all ceilings below the existing state. A same-size revision
            # is allowed; its longer audit reason is then refused by the byte cap.
            entry = (await service.entries(memory))[0]
            large = await service.remember(
                memory=memory, entries=(fact(10, entry=entry, reason="x" * 512),), mode="append"
            )
            assert large is not None
            limited = MemoryService(
                backend=backend,
                capacity_budget=MemoryCapacityBudget(
                    max_active_entries=1,
                    max_manifest_entries=1,
                    max_manifest_bytes=1024,
                ),
            )
            assert (await limited.capacity(large)).exceeded == ("manifest_bytes", "manifest_entries", "active_entries")
            current_entry = next(value for value in await service.entries(large) if value.entry_id == entry.entry_id)
            smaller = await limited.remember(memory=large, entries=(fact(11, entry=current_entry),), mode="append")
            assert smaller is not None
            assert (await limited.capacity(smaller)).exceeded == ("manifest_entries", "active_entries")
            current_entry = next(value for value in await service.entries(smaller) if value.entry_id == entry.entry_id)
            with pytest.raises(MemoryCapacityExceededError, match="manifest_bytes"):
                await limited.remember(
                    memory=smaller, entries=(fact(12, entry=current_entry, reason="x" * 512),), mode="append"
                )

    asyncio.run(scenario())


def test_reactivation_checks_only_active_growth(database_config):
    async def scenario():
        async with memory_context(database_config) as (_, _, service, backend):
            memory = await service.remember(memory=None, entries=tuple(fact(i) for i in range(5)), mode="append")
            entries = await service.entries(memory)
            retired = await service.forget(memory, entries=entries[:2])
            limited = MemoryService(
                backend=backend,
                capacity_budget=MemoryCapacityBudget(
                    max_active_entries=4,
                    max_manifest_entries=4,
                    max_manifest_bytes=1024,
                ),
            )
            restored = await limited.reactivate(retired, entries=entries[:1])
            assert (await limited.capacity(restored)).active_entry_count == 4
            with pytest.raises(MemoryCapacityExceededError, match="active_entries"):
                await limited.reactivate(restored, entries=entries[1:2])
            relieved = await limited.forget(restored, entries=entries[:1], reason="relief")
            assert (await limited.capacity(relieved)).active_entry_count == 3

    asyncio.run(scenario())


def test_compaction_preserves_history_tags_and_projection_budget(database_config):
    async def scenario():
        async with memory_context(
            database_config, memory_compaction_enabled=True, memory_compaction_min_tombstone_revisions=1
        ) as (contexts, scope_id, service, backend):
            initial = await service.remember(memory=None, entries=tuple(fact(i) for i in range(12)), mode="append")
            entries = await service.entries(initial)
            tagged_entry, recent_entry, *old_entries = entries
            target = MemoryEntryTagTarget(artifact_id=initial.artifact_id, entry_id=tagged_entry.entry_id)
            empty = await contexts.records.get_tags(scope_id, target)
            tags = await contexts.records.replace_tags(scope_id, target, ("keep",), expected_etag=empty.etag)
            retired = await service.forget(initial, entries=(tagged_entry, *old_entries))
            current = await service.forget(retired, entries=(recent_entry,))
            assert (await service.capacity(current)).compactable_entry_count == len(old_entries)
            preview = await service.compact(current, dry_run=True, limit=3)
            assert len(preview.entry_ids) == 3
            assert preview.memory == current and preview.dry_run
            assert await service.head(current.artifact_id) == current
            before = await row_counts(contexts, scope_id)
            statements = []

            def record(_connection, _cursor, statement, _parameters, _context, _executemany):
                statements.append(statement.lower())

            engine = contexts.database.engine.sync_engine
            event.listen(engine, "before_cursor_execute", record)
            try:
                result = await service.compact(current, limit=3)
            finally:
                event.remove(engine, "before_cursor_execute", record)
            assert result.entry_ids == preview.entry_ids
            assert result.reclaimed_bytes == preview.reclaimed_bytes
            assert result.reclaimed_bytes == len(memory_content_bytes(current.content)) - len(
                memory_content_bytes(result.memory.content)
            )
            assert not any(
                statement.lstrip().startswith(("insert", "update", "delete"))
                and any(table in statement for table in ("pc_memory_entry_heads", "pc_memory_entry_fts"))
                for statement in statements
            )
            after = await row_counts(contexts, scope_id)
            assert after == (before[0] + 1, before[1], before[2])
            assert await service.get(initial) == initial
            dropped = next(entry for entry in old_entries if entry.entry_id in result.entry_ids)
            citation = MemoryCitation(
                memory_ref=initial.as_ref(), entry_id=dropped.entry_id, entry_version_id=dropped.entry_version_id
            )
            assert await service.validate_citation(citation) == dropped
            assert dropped.entry_id not in {entry.entry_id for entry in await service.entries(result.memory)}
            with pytest.raises(MemoryEntryNotFoundError):
                await service.reactivate(result.memory, entries=(dropped,))
            assert await contexts.records.get_tags(scope_id, target) == tags
            changes = await service.changes(result.memory)
            assert {change.op for change in changes[0].changes} == {"compact"}
            assert all(change.to_entry_version_id is None for change in changes[0].changes)
            with pytest.raises(RevisionConflictError):
                await service.compact(current)
            # A lowered deployment budget cannot block any relief operation.
            limited = MemoryService(
                backend=backend,
                capacity_budget=MemoryCapacityBudget(max_active_entries=2, max_manifest_entries=2),
                compaction=MemoryCompactionPolicy(enabled=True, min_tombstone_revisions=1),
            )
            compacted = await limited.compact(result.memory)
            assert len(compacted.memory.content.manifest.entries) == 1  # tagged tombstone survives
            appended = await limited.remember(memory=compacted.memory, entries=(fact("after relief"),), mode="append")
            assert appended is not None
            assert (await limited.capacity(appended)).exceeded == ()

    asyncio.run(scenario())


def test_compaction_defaults_age_and_reactivation_reset(database_config):
    async def scenario():
        async with memory_context(database_config, memory_compaction_min_tombstone_revisions=1) as (
            _,
            _,
            service,
            backend,
        ):
            initial = await service.remember(memory=None, entries=(fact(1), fact(2)), mode="append")
            entry, other = await service.entries(initial)
            retired = await service.forget(initial, entries=(entry,))
            assert not (await service.compact(retired, dry_run=True)).entry_ids
            aged = await service.forget(retired, entries=(other,))
            assert (await service.compact(aged, dry_run=True)).entry_ids == (entry.entry_id,)
            with pytest.raises(CapabilityNotSupportedError, match="compaction"):
                await service.compact(aged)
            restored = await service.reactivate(aged, entries=(entry,))
            retired_again = await service.forget(restored, entries=(entry,))
            preview = await service.compact(retired_again, dry_run=True)
            assert entry.entry_id not in preview.entry_ids
            enabled = MemoryService(backend=backend, compaction=MemoryCompactionPolicy(enabled=True))
            assert (await enabled.compact(retired_again)).memory == retired_again
            with pytest.raises(ValueError, match="limit"):
                await service.compact(retired_again, dry_run=True, limit=0)

    asyncio.run(scenario())


def test_history_window_refuses_before_loading_history(database_config, monkeypatch):
    async def scenario():
        async with memory_context(database_config, memory_max_history_revisions=2) as (_, _, service, backend):
            first = await service.remember(memory=None, entries=(fact(1),), mode="append")
            second = await service.remember(memory=first, entries=(fact(2),), mode="append")
            assert await service.revisions(first) == (first, second)
            third = await service.remember(memory=second, entries=(fact(3),), mode="append")
            with pytest.raises(CapabilityNotSupportedError, match="history-window"):
                await service.revisions(first)
            loaded = []
            original_get = backend.get

            async def record_get(reference):
                loaded.append(reference)
                return await original_get(reference)

            monkeypatch.setattr(backend, "get", record_get)
            bounded = MemoryService(backend=backend, max_history_revisions=2)
            with pytest.raises(CapabilityNotSupportedError, match="history-window"):
                await bounded.revisions(first)
            assert len(loaded) <= 2
            assert await service.revision(first.as_ref()) == first
            assert (await service.changes(third, since_revision=2))[0].memory_ref == third.as_ref()

    asyncio.run(scenario())


def test_default_history_window_and_explicit_override(database_config):
    async def scenario():
        async with memory_context(database_config) as (_, _, service, backend):
            first = await service.remember(memory=None, entries=(fact(1),), mode="append")
            current = first
            for number in range(2, 101):
                current = await service.remember(memory=current, entries=(fact(number),), mode="append")
            readers = (service, MemoryService(backend=backend))
            for reader in readers:
                assert len(await reader.revisions(first)) == 100
            current = await service.remember(memory=current, entries=(fact(101),), mode="append")
            for reader in readers:
                with pytest.raises(CapabilityNotSupportedError, match="history-window"):
                    await reader.revisions(first)
                assert await reader.get(first) == first
            expanded = MemoryService(backend=backend, max_history_revisions=101)
            history = await expanded.revisions(first)
            assert len(history) == 101
            assert history[0] == first and history[-1] == current

    asyncio.run(scenario())


def test_zero_age_compaction_recovers_full_memory_and_preserves_tags(database_config):
    async def scenario():
        async with memory_context(
            database_config,
            memory_max_active_entries=3,
            memory_max_manifest_entries=3,
            memory_compaction_enabled=True,
            memory_compaction_min_tombstone_revisions=0,
        ) as (contexts, scope_id, service, backend):
            initial = await service.remember(memory=None, entries=(fact(1), fact(2), fact(3)), mode="append")
            tagged, dropped, active = await service.entries(initial)
            target = MemoryEntryTagTarget(artifact_id=initial.artifact_id, entry_id=tagged.entry_id)
            empty = await contexts.records.get_tags(scope_id, target)
            tags = await contexts.records.replace_tags(scope_id, target, ("keep",), expected_etag=empty.etag)
            retired = await service.forget(initial, entries=(tagged, dropped))
            with pytest.raises(MemoryCapacityExceededError, match="manifest_entries"):
                await service.remember(memory=retired, entries=(fact(4),), mode="append")
            defaults = MemoryService(backend=backend)
            assert not (await defaults.compact(retired, dry_run=True)).entry_ids
            disabled = MemoryService(backend=backend, compaction=MemoryCompactionPolicy(min_tombstone_revisions=0))
            preview = await disabled.compact(retired, dry_run=True)
            assert preview.entry_ids == (dropped.entry_id,)
            assert await service.head(initial.artifact_id) == retired
            with pytest.raises(CapabilityNotSupportedError, match="compaction"):
                await disabled.compact(retired)
            result = await service.compact(retired)
            assert result.entry_ids == preview.entry_ids
            assert {item.entry_id for item in result.memory.content.manifest.entries} == {
                tagged.entry_id,
                active.entry_id,
            }
            appended = await service.remember(memory=result.memory, entries=(fact(4),), mode="append")
            assert (await service.capacity(appended)).exceeded == ()
            assert await contexts.records.get_tags(scope_id, target) == tags
            citation = MemoryCitation(
                memory_ref=initial.as_ref(), entry_id=dropped.entry_id, entry_version_id=dropped.entry_version_id
            )
            assert await service.validate_citation(citation) == dropped
            with pytest.raises(MemoryEntryNotFoundError):
                await service.reactivate(appended, entries=(dropped,))

    asyncio.run(scenario())


def test_compaction_reports_signed_complete_content_bytes(database_config):
    async def scenario():
        async with memory_context(
            database_config, memory_compaction_enabled=True, memory_compaction_min_tombstone_revisions=0
        ) as (_, _, service, _):
            initial = await service.remember(memory=None, entries=(fact(1),), mode="append")
            retired = await service.forget(initial, entries=await service.entries(initial))
            reason = "审" * 512
            preview = await service.compact(retired, dry_run=True, reason=reason)
            assert preview.reclaimed_bytes < 0
            result = await service.compact(retired, reason=reason)
            assert not result.memory.content.manifest.entries
            assert result.reclaimed_bytes == preview.reclaimed_bytes
            assert result.reclaimed_bytes == len(memory_content_bytes(retired.content)) - len(
                memory_content_bytes(result.memory.content)
            )
            assert (await service.capacity(result.memory)).manifest_bytes > (
                await service.capacity(retired)
            ).manifest_bytes
            unchanged = await service.compact(result.memory, reason=reason)
            assert unchanged.memory == result.memory
            assert unchanged.reclaimed_bytes == 0 and not unchanged.entry_ids

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "values",
    [
        {"memory_max_active_entries": 2, "memory_max_manifest_entries": 1},
        {"memory_max_manifest_bytes": 1023},
        {"memory_compaction_min_tombstone_revisions": -1},
        {"memory_max_history_revisions": 0},
    ],
)
def test_capacity_configuration_rejects_invalid_limits(values):
    with pytest.raises(ValidationError):
        RuntimeConfig(**values)


def test_compaction_rechecks_tags_added_after_eligibility(database_config, monkeypatch):
    async def scenario():
        async with memory_context(database_config) as (contexts, scope_id, writer, backend):
            service = MemoryService(
                backend=backend, compaction=MemoryCompactionPolicy(enabled=True, min_tombstone_revisions=1)
            )
            initial = await writer.remember(memory=None, entries=(fact(1), fact(2)), mode="append")
            entry, other = await writer.entries(initial)
            retired = await writer.forget(initial, entries=(entry,))
            current = await writer.forget(retired, entries=(other,))
            target = MemoryEntryTagTarget(artifact_id=current.artifact_id, entry_id=entry.entry_id)
            empty = await contexts.records.get_tags(scope_id, target)
            original = backend.any_tagged_entry_ids

            async def concurrent_tag(memory):
                observed = await original(memory)
                await contexts.records.replace_tags(scope_id, target, ("newly protected",), expected_etag=empty.etag)
                return observed

            monkeypatch.setattr(backend, "any_tagged_entry_ids", concurrent_tag)
            before = await row_counts(contexts, scope_id)
            with pytest.raises(CapabilityNotSupportedError, match="compaction-tag-conflict"):
                await service.compact(current)
            assert await service.head(current.artifact_id) == current
            assert await row_counts(contexts, scope_id) == before
            assert (await contexts.records.get_tags(scope_id, target)).tags == ("newly protected",)

    asyncio.run(scenario())


def test_deduplication_remains_available_over_budget(database_config):
    async def scenario():
        async with memory_context(database_config) as (_, _, writer, backend):
            first = await writer.remember(memory=None, entries=(fact(1), fact(2)), mode="append")
            entry = next(item for item in await writer.entries(first) if item.text == fact(2).text)
            duplicate = await writer.remember(memory=first, entries=(fact(1, entry=entry),), mode="append")
            service = MemoryService(
                backend=backend, capacity_budget=MemoryCapacityBudget(max_active_entries=1, max_manifest_entries=1)
            )
            deduplicated = await service.organize(duplicate, mode="dedupe")
            capacity = await service.capacity(deduplicated)
            assert capacity.active_entry_count == 1
            assert capacity.manifest_entry_count == 2
            assert capacity.exceeded == ("manifest_entries",)

    asyncio.run(scenario())
