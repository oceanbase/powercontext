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

"""Restoration follows live merge chains and preserves exact frozen content."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent, AtomicMemoryRecord
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

SCOPE = "cascade-restoration"


@dataclass(frozen=True)
class _MergeChain:
    inputs: tuple[AtomicMemoryRecord, ...]
    first_result: AtomicMemoryRecord
    revised_result: AtomicMemoryRecord
    endpoint: AtomicMemoryRecord | None
    history: tuple[AtomicMemoryRecord, ...]


async def _revise(contexts, record: AtomicMemoryRecord, text: str) -> AtomicMemoryRecord:
    await contexts.records.replace_artifact(
        SCOPE,
        "atomic-memory",
        record.ref.artifact_id,
        f'"revision:{record.ref.revision}"',
        ArtifactWrite(content={"kind": "fact", "text": text}),
    )
    return await contexts.atomic_memory.for_scope(SCOPE).get(record.ref.artifact_id)


async def _seed_chain(contexts, *, downstream: bool = True) -> _MergeChain:
    await contexts.get(SCOPE)
    memory = contexts.atomic_memory.for_scope(SCOPE)
    history = []
    inputs = []
    for name in ("A", "B", "D"):
        created = await contexts.records.create_artifact(
            SCOPE,
            "atomic-memory",
            ArtifactWrite(content={"kind": "fact", "text": f"Cascade {name} original observation."}),
        )
        first = await memory.get(created.artifact_id)
        revised = await _revise(contexts, first, f"Cascade {name} frozen observation with verified details.")
        history.extend((first, revised))
        inputs.append(revised)
    a, b, d = inputs
    c = (
        await memory.merge(
            (a.as_read(), b.as_read()), AtomicMemoryContent(kind="fact", text="Cascade C combines A and B.")
        )
    ).primary
    revised_c = await _revise(contexts, c, "Cascade C includes an additional result observation.")
    history.extend((c, revised_c))
    endpoint = None
    if downstream:
        e = (
            await memory.merge(
                (revised_c.as_read(), d.as_read()),
                AtomicMemoryContent(kind="fact", text="Cascade E combines C and D."),
            )
        ).primary
        endpoint = await _revise(contexts, e, "Cascade E includes a later endpoint observation.")
        history.extend((e, endpoint))
    return _MergeChain(tuple(inputs), c, revised_c, endpoint, tuple(history))


def _ref_identity(ref: ArtifactRef) -> tuple[str, str, int]:
    return ref.family, ref.artifact_id, ref.revision


async def _assert_active_visibility(memory, expected: tuple[AtomicMemoryRecord, ...]) -> None:
    visible = {_ref_identity(record.ref): record.artifact.content.text for record in expected}
    assert {
        _ref_identity(record.ref): record.artifact.content.text for record in (await memory.list()).items
    } == visible
    assert {
        _ref_identity(hit.hit.artifact_ref): hit.text for hit in (await memory.search("Cascade", mode="text")).hits
    } == visible


async def _assert_immutable_history(contexts, chain: _MergeChain) -> None:
    memory = contexts.atomic_memory.for_scope(SCOPE)
    for original in chain.history:
        historical = await memory.get(original.ref.artifact_id, revision=original.ref.revision)
        assert historical.artifact == original.artifact
        stored = await contexts.records.get_artifact_revision(
            SCOPE, "atomic-memory", original.ref.artifact_id, original.ref.revision
        )
        assert stored.content["text"] == original.artifact.content.text


@pytest.mark.parametrize("operation", ["restore_input", "undo_first_merge"])
def test_one_request_unwinds_successive_merges_to_exact_frozen_inputs(tmp_path: Path, operation: str) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cascade.db'}"))
        ) as contexts:
            chain = await _seed_chain(contexts)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            a, b, d = chain.inputs
            c, e = chain.revised_result, chain.endpoint
            assert e is not None
            await _assert_active_visibility(memory, (e,))

            if operation == "restore_input":
                result = await memory.restore(b.ref.artifact_id)
                assert result.primary_artifact_id == b.ref.artifact_id
            else:
                result = await memory.restore(c.ref.artifact_id, operation="undo_merge")
                assert result.primary_artifact_id == c.ref.artifact_id

            assert result.changed
            assert result.undo_merge_results == (e.ref.artifact_id, c.ref.artifact_id)
            assert {_ref_identity(ref) for ref in result.restored} == {
                (record.ref.family, record.ref.artifact_id, record.ref.revision + 1) for record in (a, b, d)
            }
            assert {_ref_identity(ref) for ref in result.retired} == {
                (record.ref.family, record.ref.artifact_id, record.ref.revision + 1) for record in (c, e)
            }
            assert {record.ref.artifact_id for record in result.records} == {
                record.ref.artifact_id for record in (a, b, c, d, e)
            }
            current_records = {}
            restored_inputs = []
            for frozen in (a, b, d):
                restored = await memory.get(frozen.ref.artifact_id)
                assert restored.ref.revision == frozen.ref.revision + 1
                assert restored.artifact.content == frozen.artifact.content.without_creation()
                assert restored.artifact.lineage.artifacts == (frozen.ref,)
                assert restored.state.state == "active"
                assert restored.state.merged_into_id is None
                current_records[restored.ref.artifact_id] = restored
                restored_inputs.append(restored)
            for merged in (c, e):
                retired = await memory.get(merged.ref.artifact_id)
                assert retired.ref.revision == merged.ref.revision + 1
                assert retired.artifact.content == merged.artifact.content.without_creation()
                assert retired.artifact.lineage.artifacts == (merged.ref,)
                assert retired.state.state == "retired"
                assert retired.state.merged_into_id is None
                current_records[retired.ref.artifact_id] = retired
            assert {record.ref.artifact_id: record for record in result.records} == current_records
            assert result.primary == current_records[result.primary_artifact_id]
            await _assert_active_visibility(memory, tuple(restored_inputs))
            await _assert_immutable_history(contexts, chain)

    asyncio.run(scenario())


def test_restoring_merged_result_history_only_unwinds_its_downstream_merge(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'result-history.db'}"))
        ) as contexts:
            chain = await _seed_chain(contexts)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            a, b, d = chain.inputs
            c, e = chain.revised_result, chain.endpoint
            assert e is not None
            merged_inputs = [await memory.get(record.ref.artifact_id) for record in (a, b)]

            result = await memory.restore(c.ref.artifact_id, revision=chain.first_result.ref.revision)

            restored = await memory.get(c.ref.artifact_id)
            assert result.changed
            assert result.primary == restored
            assert restored.ref.revision == c.ref.revision + 1
            assert restored.artifact.content == chain.first_result.artifact.content.without_creation()
            assert restored.state.state == "active"
            assert restored.state.merged_into_id is None
            assert result.undo_merge_results == (e.ref.artifact_id,)
            assert {_ref_identity(ref) for ref in result.restored} == {
                _ref_identity(restored.ref),
                (d.ref.family, d.ref.artifact_id, d.ref.revision + 1),
            }
            assert result.retired == (
                ArtifactRef(family=e.ref.family, artifact_id=e.ref.artifact_id, revision=e.ref.revision + 1),
            )
            assert {record.ref.artifact_id for record in result.records} == {
                record.ref.artifact_id for record in (c, d, e)
            }
            for merged in merged_inputs:
                current = await memory.get(merged.ref.artifact_id)
                assert current == merged
                assert current.state.state == "merged"
                assert current.state.merged_into_id == c.ref.artifact_id
            current_d = await memory.get(d.ref.artifact_id)
            assert current_d.ref.revision == d.ref.revision + 1
            assert current_d.artifact.content == d.artifact.content.without_creation()
            assert current_d.artifact.lineage.artifacts == (d.ref,)
            assert current_d.state.state == "active"
            assert current_d.state.merged_into_id is None
            retired = await memory.get(e.ref.artifact_id)
            assert retired.ref.revision == e.ref.revision + 1
            assert retired.artifact.content == e.artifact.content.without_creation()
            assert retired.artifact.lineage.artifacts == (e.ref,)
            assert retired.state.state == "retired"
            assert retired.state.merged_into_id is None
            assert {record.ref.artifact_id: record for record in result.records} == {
                record.ref.artifact_id: record for record in (restored, current_d, retired)
            }
            await _assert_active_visibility(memory, (restored, current_d))
            await _assert_immutable_history(contexts, chain)

    asyncio.run(scenario())


def test_ordinary_merge_result_revision_rollback_preserves_its_merged_inputs(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'result-rollback.db'}"))
        ) as contexts:
            chain = await _seed_chain(contexts, downstream=False)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            a, b, d = chain.inputs
            c = chain.revised_result
            merged_inputs = [await memory.get(record.ref.artifact_id) for record in (a, b)]

            result = await memory.restore(c.ref.artifact_id, revision=chain.first_result.ref.revision)

            restored = await memory.get(c.ref.artifact_id)
            assert result.changed
            assert result.records == (restored,)
            assert result.primary == restored
            assert restored.ref.revision == c.ref.revision + 1
            assert restored.artifact.content == chain.first_result.artifact.content.without_creation()
            assert restored.state.state == "active"
            assert restored.state.merged_into_id is None
            assert result.undo_merge_results == ()
            assert result.retired == ()
            assert result.restored == (restored.ref,)
            for merged in merged_inputs:
                assert await memory.get(merged.ref.artifact_id) == merged
                assert merged.state.state == "merged"
                assert merged.state.merged_into_id == c.ref.artifact_id
            assert await memory.get(d.ref.artifact_id) == d
            await _assert_active_visibility(memory, (restored, d))
            await _assert_immutable_history(contexts, chain)

    asyncio.run(scenario())
