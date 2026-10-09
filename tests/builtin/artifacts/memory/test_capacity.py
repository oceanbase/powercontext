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
from sqlalchemy import func, select

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryStateValue
from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    MemoryCapacityBudget,
    MemoryCapacityExceededError,
    MemoryCompactionPolicy,
    MemoryEntryInput,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.canonical import memory_content_bytes
from powercontext.builtin.persistence.atomic_memory_schema import ATOMIC_MEMORY_STATES_TABLE
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.builtin.records import ArtifactWrite, BaseOperationNotSupportedError
from powercontext.builtin.runtime import BuiltinConfig, RuntimeCapabilities, open_builtin_contexts
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.builtin.tags import ArtifactTagTarget, TagFilter


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
        backend = RelationalMemoryBackend(
            database=contexts.database,
            scope_id=scope_id,
            artifacts=contexts.repositories.artifacts,
            index=contexts.index,
        )
        service = MemoryService(
            backend=backend,
            capacity_budget=MemoryCapacityBudget(
                max_active_entries=config.runtime.memory_max_active_entries,
                max_manifest_entries=config.runtime.memory_max_manifest_entries,
                max_manifest_bytes=config.runtime.memory_max_manifest_bytes,
            ),
            compaction=MemoryCompactionPolicy(
                enabled=config.runtime.memory_compaction_enabled,
                min_tombstone_revisions=config.runtime.memory_compaction_min_tombstone_revisions,
            ),
            max_history_revisions=config.runtime.memory_max_history_revisions,
        )
        yield contexts, scope_id, service, backend


def fact(number, **values):
    return MemoryEntryInput(kind="fact", text=f"Capacity project fact {number}.", **values)


async def row_counts(contexts, scope_id):
    async with contexts.database.connection() as connection:
        return tuple([
            await connection.scalar(select(func.count()).select_from(table).where(table.c.scope_id == scope_id))
            for table in (ARTIFACTS_TABLE, MEMORY_ENTRY_VERSIONS_TABLE, MEMORY_ENTRY_HEADS_TABLE)
        ])


async def atomic_row_counts(contexts, scope_id):
    async with contexts.database.connection() as connection:
        return tuple([
            await connection.scalar(select(func.count()).select_from(table).where(table.c.scope_id == scope_id))
            for table in (ARTIFACTS_TABLE, ATOMIC_MEMORY_STATES_TABLE, contexts.atomic_memory.index.table)
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


def test_atomic_forgetting_preserves_history_tags_and_current_projection(database_config):
    async def scenario():
        async with open_builtin_contexts(BuiltinConfig(database=database_config)) as contexts:
            scope_id = "capacity-" + uuid4().hex
            await contexts.get(scope_id)
            memory = contexts.atomic_memory.for_scope(scope_id)
            created = [
                await contexts.records.create_artifact(
                    scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(i).text})
                )
                for i in range(12)
            ]
            tagged, recent, *old = created
            target = ArtifactTagTarget(family="atomic-memory", artifact_id=tagged.artifact_id)
            empty = await contexts.records.get_tags(scope_id, target)
            tags = await contexts.records.replace_tags(scope_id, target, ("keep",), expected_etag=empty.etag)
            before = await atomic_row_counts(contexts, scope_id)
            for item in (tagged, *old):
                current = await memory.get(item.artifact_id)
                await memory.forget(
                    item.artifact_id,
                    expected_revision=current.ref.revision,
                    expected_state_version=current.state.state_version,
                )
            assert await atomic_row_counts(contexts, scope_id) == (before[0], before[1], 1)
            assert tuple(
                hit.hit.artifact_ref.artifact_id for hit in (await memory.search("Capacity project")).hits
            ) == (recent.artifact_id,)
            page = await memory.list(states=("forgotten",), limit=3)
            assert len(page.items) == 3 and page.next_cursor
            forgotten = list(page.items)
            while page.next_cursor:
                page = await memory.list(states=("forgotten",), limit=3, cursor=page.next_cursor)
                forgotten.extend(page.items)
            assert {item.ref.artifact_id for item in forgotten} == {item.artifact_id for item in (tagged, *old)}
            assert len(forgotten) == 11
            for item in created:
                exact = await contexts.records.get_artifact_revision(scope_id, "atomic-memory", item.artifact_id, 1)
                assert exact.revision == 1
                history = await contexts.records.list_artifact_revisions(
                    scope_id, "atomic-memory", item.artifact_id, limit=10, cursor=None
                )
                assert [revision.revision for revision in history.items] == [1]
            assert await contexts.records.get_tags(scope_id, target) == tags
            with pytest.raises(AtomicMemoryConflictError):
                await memory.forget(tagged.artifact_id, expected_revision=1, expected_state_version=0)
            assert await atomic_row_counts(contexts, scope_id) == (before[0], before[1], 1)
            restored = await memory.restore(tagged.artifact_id)
            assert restored.primary.state.state is AtomicMemoryStateValue.ACTIVE
            assert restored.primary.ref.revision == 1
            assert await contexts.records.get_tags(scope_id, target) == tags
            assert await atomic_row_counts(contexts, scope_id) == (*before[:2], 2)

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
            assert await service.revisions(first, through_revision=1) == (first,)
            assert await service.revisions(first, since_revision=1) == (second, third)
            assert await service.revisions(first, since_revision=1, through_revision=2) == (second,)
            assert await service.revisions(first, since_revision=3) == ()
            for bounds in (
                {"since_revision": -1},
                {"since_revision": 4},
                {"through_revision": 0},
                {"through_revision": 4},
                {"since_revision": 2, "through_revision": 1},
            ):
                with pytest.raises(ValueError):
                    await service.revisions(first, **bounds)
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
                assert await reader.revisions(first, through_revision=1) == (first,)
                assert await reader.revisions(first, since_revision=100) == (current,)
                assert await reader.get(first) == first
            expanded = MemoryService(backend=backend, max_history_revisions=101)
            history = await expanded.revisions(first)
            assert len(history) == 101
            assert history[0] == first and history[-1] == current

    asyncio.run(scenario())


def test_atomic_memory_ignores_legacy_capacity_and_rejects_collection_compaction(database_config):
    async def scenario():
        config = BuiltinConfig(
            database=database_config,
            runtime=RuntimeConfig(
                memory_max_active_entries=3,
                memory_max_manifest_entries=3,
                memory_compaction_enabled=True,
                memory_compaction_min_tombstone_revisions=0,
            ),
        )
        async with open_builtin_contexts(config) as contexts:
            scope_id = "capacity-" + uuid4().hex
            await contexts.get(scope_id)
            memory = contexts.atomic_memory.for_scope(scope_id)
            created = [
                await contexts.records.create_artifact(
                    scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(i).text})
                )
                for i in range(3)
            ]
            target = ArtifactTagTarget(family="atomic-memory", artifact_id=created[0].artifact_id)
            empty = await contexts.records.get_tags(scope_id, target)
            tags = await contexts.records.replace_tags(scope_id, target, ("keep",), expected_etag=empty.etag)
            for item in created[:2]:
                current = await memory.get(item.artifact_id)
                await memory.forget(
                    item.artifact_id, expected_revision=1, expected_state_version=current.state.state_version
                )
            runtime = BuiltinRuntime(
                provider=contexts,
                capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=("fts",)),
                atomic_memory_application=contexts.atomic_memory,
            )
            legacy = runtime.memory.for_scope(scope_id)
            before = await atomic_row_counts(contexts, scope_id)
            for _ in range(2):
                with pytest.raises(BaseOperationNotSupportedError, match="collection capacity"):
                    await legacy.capacity()
                with pytest.raises(BaseOperationNotSupportedError, match="collection compaction"):
                    await legacy.compact()
                assert await atomic_row_counts(contexts, scope_id) == before
            fourth = await contexts.records.create_artifact(
                scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(4).text})
            )
            assert fourth.artifact_id not in {item.artifact_id for item in created}
            assert await atomic_row_counts(contexts, scope_id) == (before[0] + 1, before[1] + 1, 2)
            assert len((await memory.list(states=("active", "forgotten"))).items) == 4
            assert await contexts.records.get_tags(scope_id, target) == tags
            for item in created[:2]:
                historical = await memory.get(item.artifact_id, revision=1)
                assert historical.state.state is AtomicMemoryStateValue.FORGOTTEN
                assert historical.ref.revision == 1
                assert historical.artifact.content.text == fact(created.index(item)).text

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


def test_atomic_forgetting_preserves_tags_added_before_commit(database_config, monkeypatch):
    async def scenario():
        async with open_builtin_contexts(BuiltinConfig(database=database_config)) as contexts:
            scope_id = "capacity-" + uuid4().hex
            await contexts.get(scope_id)
            created = [
                await contexts.records.create_artifact(
                    scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(i).text})
                )
                for i in range(2)
            ]
            memory = contexts.atomic_memory.for_scope(scope_id)
            target = ArtifactTagTarget(family="atomic-memory", artifact_id=created[0].artifact_id)
            empty = await contexts.records.get_tags(scope_id, target)
            original = contexts.atomic_memory.service.prepare_forget

            async def concurrent_tag(plan):
                await contexts.records.replace_tags(scope_id, target, ("newly protected",), expected_etag=empty.etag)
                return await original(plan)

            monkeypatch.setattr(contexts.atomic_memory.service, "prepare_forget", concurrent_tag)
            before = await atomic_row_counts(contexts, scope_id)
            current = await memory.get(created[0].artifact_id)
            forgotten = await memory.forget(
                created[0].artifact_id, expected_revision=1, expected_state_version=current.state.state_version
            )
            assert forgotten.primary.state.state is AtomicMemoryStateValue.FORGOTTEN
            assert forgotten.primary.ref.revision == 1
            assert await atomic_row_counts(contexts, scope_id) == (*before[:2], 1)
            assert (await contexts.records.get_tags(scope_id, target)).tags == ("newly protected",)
            assert (await memory.search("Capacity project", tag_filter=TagFilter(tags=("newly protected",)))).hits == ()
            assert (await memory.get(created[0].artifact_id, revision=1)).artifact == current.artifact
            restored = await memory.restore(created[0].artifact_id)
            assert restored.primary.state.state is AtomicMemoryStateValue.ACTIVE
            hits = (await memory.search("Capacity project", tag_filter=TagFilter(tags=("newly protected",)))).hits
            assert [hit.hit.artifact_ref for hit in hits] == [current.ref]
            assert await atomic_row_counts(contexts, scope_id) == before
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
