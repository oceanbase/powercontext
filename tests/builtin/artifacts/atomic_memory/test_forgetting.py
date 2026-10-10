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
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryStateValue
from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
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


def fact(number: int) -> str:
    return f"Capacity project fact {number}."


async def atomic_row_counts(contexts, scope_id):
    async with contexts.database.connection() as connection:
        return tuple([
            await connection.scalar(select(func.count()).select_from(table).where(table.c.scope_id == scope_id))
            for table in (ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE, contexts.atomic_memory.index.table)
        ])


def test_atomic_forgetting_preserves_history_tags_and_current_projection(database_config):
    async def scenario():
        async with open_builtin_contexts(BuiltinConfig(database=database_config)) as contexts:
            scope_id = "capacity-" + uuid4().hex
            await contexts.get(scope_id)
            memory = contexts.atomic_memory.for_scope(scope_id)
            created = [
                await contexts.records.create_artifact(
                    scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(i)})
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
            assert restored.primary.ref.revision == 2
            assert restored.primary.artifact.content.text == fact(0)
            assert (await memory.get(tagged.artifact_id, revision=1)).artifact.content.text == fact(0)
            assert await contexts.records.get_tags(scope_id, target) == tags
            assert await atomic_row_counts(contexts, scope_id) == (before[0] + 1, before[1], 2)

    asyncio.run(scenario())


def test_atomic_memory_ignores_legacy_capacity_limits(database_config):
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
                    scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(i)})
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
            before = await atomic_row_counts(contexts, scope_id)
            fourth = await contexts.records.create_artifact(
                scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(4)})
            )
            assert fourth.artifact_id not in {item.artifact_id for item in created}
            assert await atomic_row_counts(contexts, scope_id) == (before[0] + 1, before[1] + 1, 2)
            assert len((await memory.list(states=("active", "forgotten"))).items) == 4
            assert await contexts.records.get_tags(scope_id, target) == tags
            for item in created[:2]:
                historical = await memory.get(item.artifact_id, revision=1)
                assert historical.state.state is AtomicMemoryStateValue.FORGOTTEN
                assert historical.ref.revision == 1
                assert historical.artifact.content.text == fact(created.index(item))

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
                    scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": fact(i)})
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
            assert restored.primary.ref.revision == current.ref.revision + 1
            assert restored.primary.artifact.content == current.artifact.content.without_creation()
            assert [hit.hit.artifact_ref for hit in hits] == [restored.primary.ref]
            assert (await memory.get(current.ref.artifact_id, revision=1)).artifact == current.artifact
            assert await atomic_row_counts(contexts, scope_id) == (before[0] + 1, *before[1:])
            assert (await contexts.records.get_tags(scope_id, target)).tags == ("newly protected",)

    asyncio.run(scenario())
