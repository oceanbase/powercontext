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

"""Shared heads and exact lineage protect successful merge business state."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemory, AtomicMemoryContent, AtomicMemoryDraft
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryStateValue
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernanceRepository,
    ArtifactLifecycleState,
    InvalidArtifactLifecycleError,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACT_LINEAGE_ARTIFACTS_TABLE
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from tests.builtin.persistence.contract import HandoffContent, HandoffDraft, repository_profile


def test_merge_creation_marks_only_selected_exact_inputs_in_lineage_order() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories), profile.database.transaction() as connection:
            artifacts = repositories.artifacts
            first, second, evidence = tuple([
                await artifacts.create(
                    connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                )
                for identity in ("first", "second", "evidence")
            ])
            result = await artifacts.create_merge_result(
                connection,
                "project",
                "result",
                HandoffDraft(
                    content=HandoffContent(summary="merged"),
                    artifacts=(evidence.as_ref(), second.as_ref(), first.as_ref()),
                ),
                ((first.as_ref(), 0), (second.as_ref(), 0)),
            )
            assert await artifacts.merge_inputs(connection, "project", result.as_ref()) == (
                second.as_ref(),
                first.as_ref(),
            )
            rows = (
                (
                    await connection.execute(
                        select(ARTIFACT_LINEAGE_ARTIFACTS_TABLE)
                        .where(ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.artifact_id == "result")
                        .order_by(ARTIFACT_LINEAGE_ARTIFACTS_TABLE.c.ordinal)
                    )
                )
                .mappings()
                .all()
            )
            assert [row["is_merge_input"] for row in rows] == [False, True, True]
            ordinary = await artifacts.create(
                connection,
                "project",
                "ordinary",
                HandoffDraft(content=result.content, artifacts=result.lineage.artifacts),
            )
            assert await artifacts.merge_inputs(connection, "project", ordinary.as_ref()) == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("lifecycle,merged_into", (("deprecated", "result"), ("retired", None)))
def test_ordinary_revision_rejects_frozen_or_retired_identity(lifecycle: str, merged_into: str | None) -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories), profile.database.transaction() as connection:
            original = await repositories.artifacts.create(
                connection, "project", "input", HandoffDraft(content=HandoffContent(summary="original"))
            )
            await connection.execute(
                update(ARTIFACT_HEADS_TABLE)
                .where(ARTIFACT_HEADS_TABLE.c.artifact_id == "input")
                .values(
                    lifecycle_state=lifecycle,
                    merged_into_id=merged_into,
                )
            )
            with pytest.raises(InvalidArtifactLifecycleError):
                await repositories.artifacts.revise(
                    connection, "project", original, HandoffDraft(content=HandoffContent(summary="edited"))
                )
            if lifecycle == "retired":
                expected = await ArtifactGovernanceRepository().get(connection, "project", "handoff", "input")
                with pytest.raises(InvalidArtifactLifecycleError):
                    await repositories.artifacts.revise_for_restoration(
                        connection, "project", original, HandoffDraft(content=original.content), expected
                    )
            assert await repositories.artifacts.get(connection, "project", original.as_ref()) == original

    asyncio.run(scenario())


def test_ordinary_governance_cannot_unfreeze_inputs_or_retire_effective_result() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories), profile.database.transaction() as connection:
            for identity in ("input", "result"):
                await repositories.artifacts.create(
                    connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                )
            await connection.execute(
                update(ARTIFACT_HEADS_TABLE)
                .where(ARTIFACT_HEADS_TABLE.c.artifact_id == "input")
                .values(
                    lifecycle_state="deprecated",
                    merged_into_id="result",
                    governance_generation=1,
                )
            )
            governance = ArtifactGovernanceRepository()
            with pytest.raises(InvalidArtifactLifecycleError):
                await governance.transition(
                    connection, "project", "handoff", "input", 1, ArtifactLifecycleState.ACTIVE, None
                )
            with pytest.raises(InvalidArtifactLifecycleError):
                await governance.transition(
                    connection, "project", "handoff", "result", 0, ArtifactLifecycleState.RETIRED, None
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ("duplicate", "stale", "inactive", "different-family", "missing-lineage"))
def test_merge_creation_requires_distinct_current_active_same_family_inputs(invalid: str) -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories), profile.database.transaction() as connection:
            first, second = tuple([
                await repositories.artifacts.create(
                    connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                )
                for identity in ("first", "second")
            ])
            inputs = ((first.as_ref(), 0), (second.as_ref(), 0))
            lineage = (first.as_ref(), second.as_ref())
            if invalid == "duplicate":
                inputs = ((first.as_ref(), 0), (first.as_ref(), 0))
            elif invalid == "stale":
                inputs = ((first.as_ref(), 1), (second.as_ref(), 0))
            elif invalid == "inactive":
                await connection.execute(
                    update(ARTIFACT_HEADS_TABLE)
                    .where(ARTIFACT_HEADS_TABLE.c.artifact_id == "first")
                    .values(lifecycle_state="deprecated")
                )
            elif invalid == "different-family":
                inputs = ((ArtifactRef(family="report", artifact_id="first", revision=1), 0), (second.as_ref(), 0))
            elif invalid == "missing-lineage":
                lineage = (first.as_ref(),)
            with pytest.raises(InvalidArtifactLifecycleError):
                await repositories.artifacts.create_merge_result(
                    connection,
                    "project",
                    "result",
                    HandoffDraft(content=HandoffContent(summary="merged"), artifacts=lineage),
                    inputs,
                )
            assert await repositories.artifacts.revisions(connection, "project", "handoff", "result") == ()

    asyncio.run(scenario())


def test_atomic_state_adapter_uses_heads_without_separate_state_authority() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, _), profile.database.transaction() as connection:
            artifacts = ArtifactRepository((AtomicMemory,))
            states = AtomicMemoryStateRepository()
            original = await artifacts.create(
                connection,
                "project",
                "memory",
                AtomicMemoryDraft(content=AtomicMemoryContent(kind="decision", text="Keep the exact evidence.")),
            )
            state = await states.create(connection, "project", "memory")
            assert state == await states.get(connection, "project", "memory")
            assert (
                await states.transition(connection, "project", "memory", state, AtomicMemoryStateValue.ACTIVE) == state
            )
            forgotten = await states.transition(
                connection, "project", "memory", state, AtomicMemoryStateValue.FORGOTTEN
            )
            assert forgotten.state_version == 1
            revised = await artifacts.revise(
                connection,
                "project",
                original,
                AtomicMemoryDraft(
                    content=AtomicMemoryContent(kind="decision", text="Preserve exact historical evidence.")
                ),
            )
            assert revised.revision == 2
            assert await states.get(connection, "project", "memory") == forgotten
            active = await states.transition(connection, "project", "memory", forgotten, AtomicMemoryStateValue.ACTIVE)
            assert active.state_version == 2
            head = (
                (
                    await connection.execute(
                        select(ARTIFACT_HEADS_TABLE).where(ARTIFACT_HEADS_TABLE.c.artifact_id == "memory")
                    )
                )
                .mappings()
                .one()
            )
            assert head["lifecycle_state"] == "active" and head["merged_into_id"] is None
            assert head["governance_generation"] == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("historical", (False, True))
def test_atomic_restoration_uses_marked_refs_without_creation_payload(historical: bool) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig()) as contexts:
            application = contexts.atomic_memory
            memory = application.for_scope("project")
            await contexts.records.create_atomic_memories(
                "project",
                (
                    {"kind": "decision", "text": "Preserve the first exact rule."},
                    {"kind": "decision", "text": "Preserve the second exact rule."},
                ),
            )
            originals = (await memory.list()).items
            async with contexts.database.transaction() as connection:
                result = await application.artifacts.create_merge_result(
                    connection,
                    "project",
                    "shared-result",
                    AtomicMemoryDraft(
                        content=AtomicMemoryContent(kind="decision", text="Preserve both rules."),
                        artifacts=tuple(record.ref for record in originals),
                    ),
                    tuple((record.ref, record.state.state_version) for record in reversed(originals)),
                )
                await application.security.establish_owner(connection, "project", result.artifact_id, None)
                for record in originals:
                    await application.service.states.transition(
                        connection,
                        "project",
                        record.ref.artifact_id,
                        record.state,
                        AtomicMemoryStateValue.MERGED,
                        result.artifact_id,
                    )
                    await application.publisher.remove(connection, "project", record.ref.artifact_id)
            assert result.content.creation is None
            if historical:
                restored = await memory.restore(originals[0].ref.artifact_id, revision=1)
                assert restored.primary.ref.revision == 2
                assert restored.primary.artifact.content == originals[0].artifact.content.without_creation()
            else:
                restored = await memory.restore(result.artifact_id, operation="undo_merge")
                assert restored.primary.state.state is AtomicMemoryStateValue.RETIRED
            for original in originals:
                current = await memory.get(original.ref.artifact_id)
                assert current.state.state is AtomicMemoryStateValue.ACTIVE
                assert current.state.state_version == 2
                assert (await memory.get(original.ref.artifact_id, revision=1)).artifact == original.artifact
            assert len((await memory.list()).items) == 2

    asyncio.run(scenario())
