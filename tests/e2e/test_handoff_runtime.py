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
import json
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.handoff import HandoffScopeMismatchError
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ActivateHandoff,
    BuiltinConfig,
    CaptureSource,
    HandoffArtifactCitation,
    HandoffDraft,
    HandoffMemoryCitation,
    HandoffOmission,
    HandoffSourceCitation,
    HandoffStatement,
    PreparedHandoff,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.errors import RevisionConflictError


class _EchoHandoffPipeline:
    async def generate(self, request, /) -> HandoffDraft:
        citation = request.evidence[0].citation
        return HandoffDraft(
            objective=request.objective,
            state=(
                HandoffStatement(
                    text="The parser maps malformed input to a stable public error.",
                    citations=(citation,),
                ),
            ),
            disposition="continuable",
            next_action=HandoffStatement(
                text="Run public-interface regression tests.",
                citations=(citation,),
            ),
        )


def test_runtime_owns_handoff_trigger_activation_and_deduplication() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            handoff_pipeline=_EchoHandoffPipeline(),
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Handoff activation", idempotency_key="project")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="The parser maps malformed input to a stable public error.",
                    metadata={},
                )
            )
            handoffs = runtime.handoff.for_scope(scope.scope_id)
            generated = await handoffs.activate(
                ActivateHandoff(
                    boundary_source=source.source_ref,
                    objective="Complete parser error handling.",
                )
            )
            ignored = await handoffs.activate(
                ActivateHandoff(
                    boundary_source=source.source_ref,
                    objective="Complete parser error handling.",
                )
            )

            draft = generated.draft
            assert draft is not None
            assert generated.status == "generated"
            assert generated.current_position == source.sequence
            assert ignored.status == "ignored"
            assert ignored.draft is None
            assert draft.objective == "Complete parser error handling."
            assert draft.state[0].citations == (HandoffSourceCitation(source_ref=source.source_ref),)
            assert await handoffs.latest() is None
            assert (await runtime.capabilities()).handoff_generation is True

    asyncio.run(scenario())


def test_handoff_trigger_state_survives_runtime_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(
                url=f"sqlite+aiosqlite:///{tmp_path / 'trigger.db'}",
            )
        )
        async with open_builtin_runtime(config, handoff_pipeline=_EchoHandoffPipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Restarted Handoff trigger", idempotency_key="project")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="boundary-1",
                    content="The provider observed one participant boundary.",
                    metadata={},
                )
            )
            first = await runtime.handoff.for_scope(scope.scope_id).activate(
                ActivateHandoff(
                    boundary_source=source.source_ref,
                    objective="Transfer the current work.",
                )
            )

        async with open_builtin_runtime(config, handoff_pipeline=_EchoHandoffPipeline()) as runtime:
            repeated = await runtime.handoff.for_scope(scope.scope_id).activate(
                ActivateHandoff(
                    boundary_source=source.source_ref,
                    objective="Transfer the current work.",
                )
            )

        assert first.status == "generated"
        assert repeated.status == "ignored"
        assert repeated.current_position == source.sequence

    asyncio.run(scenario())


def test_handoff_runtime_supports_temporary_transfer_and_durable_milestones() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Handoff milestones", idempotency_key="project")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="The parser maps malformed input to a stable public error.",
                    metadata={"origin": "e2e"},
                )
            )
            memory = await runtime.memory.for_scope(scope.scope_id).remember(
                RememberMemoryRequest(
                    entries=(
                        MemoryEntryInput(
                            kind="decision",
                            text="Regression tests must use the public parser interface.",
                        ),
                    )
                )
            )
            assert memory.entry is not None
            source_citation = HandoffSourceCitation(source_ref=source.source_ref)
            memory_citation = HandoffMemoryCitation(memory_citation=memory.entry.citation)
            handoffs = runtime.handoff.for_scope(scope.scope_id)

            empty = await handoffs.continue_latest()
            prepared = await handoffs.finalize(
                HandoffDraft(
                    objective="Complete parser error handling.",
                    state=(
                        HandoffStatement(
                            text="Malformed input now maps to the public error.",
                            citations=(source_citation,),
                        ),
                    ),
                    disposition="continuable",
                    next_action=HandoffStatement(
                        text="Run public-interface regression tests.",
                        citations=(source_citation, memory_citation),
                    ),
                    omissions=(
                        HandoffOmission(
                            text="The latest test output was not captured.",
                        ),
                    ),
                )
            )

            assert empty.status == "empty"
            assert prepared.base is None
            assert await handoffs.latest() is None
            assert json.loads(handoffs.render(prepared, audience="human")) == json.loads(
                handoffs.render(prepared, audience="agent")
            )
            transferred = await handoffs.continue_from(prepared)
            assert transferred.status == "resolved"
            assert transferred.selection == "prepared"
            assert transferred.content == prepared.content
            assert transferred.selected_revision is None

            first = await handoffs.commit(prepared)
            retried = await handoffs.commit(prepared)
            assert retried == first
            assert first.revision == 1
            assert first.content == prepared.content
            assert first.lineage.sources == (source.source_ref,)
            assert first.lineage.artifacts == (memory.memory_ref,)
            assert await handoffs.revisions() == (first,)

            completed = await handoffs.finalize(
                HandoffDraft(
                    objective=prepared.content.objective,
                    state=(
                        HandoffStatement(
                            text="Public-interface regression tests pass.",
                            citations=(source_citation, memory_citation),
                        ),
                        HandoffStatement(
                            text="The previous milestone remains available for review.",
                            citations=(HandoffArtifactCitation(artifact_ref=first.as_ref()),),
                        ),
                    ),
                    disposition="complete",
                    next_action=None,
                )
            )
            second = await handoffs.commit(completed)
            latest = await handoffs.continue_latest()
            historical = await handoffs.continue_from(first.as_ref())

            assert second.revision == 2
            assert second.lineage.artifacts == (memory.memory_ref, first.as_ref())
            assert latest.status == "resolved"
            assert latest.selection == "latest"
            assert latest.selected_revision == second.as_ref()
            assert latest.current_revision == second.as_ref()
            assert historical.status == "resolved"
            assert historical.selection == "exact"
            assert historical.selected_revision == first.as_ref()
            assert historical.current_revision == second.as_ref()
            assert await handoffs.revision(first.as_ref()) == first
            assert await handoffs.revisions() == (first, second)

    asyncio.run(scenario())


def test_handoff_runtime_rejects_stale_and_cross_scope_use() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Handoff concurrency", idempotency_key="project")
            )
            other = await runtime.scopes.create(
                ScopeDraft(title="Other", summary="Cross-Scope Handoff", idempotency_key="other")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="A stable source for concurrent Handoffs.",
                    metadata={},
                )
            )
            citation = HandoffSourceCitation(source_ref=source.source_ref)
            handoffs = runtime.handoff.for_scope(scope.scope_id)
            initial = await handoffs.finalize(
                HandoffDraft(
                    objective="Coordinate the next milestone.",
                    state=(HandoffStatement(text="Initial state.", citations=(citation,)),),
                    disposition="continuable",
                )
            )
            first = await handoffs.commit(initial)
            session_a = await handoffs.finalize(
                HandoffDraft(
                    objective=initial.content.objective,
                    state=(HandoffStatement(text="Session A state.", citations=(citation,)),),
                    disposition="continuable",
                )
            )
            session_b = await handoffs.finalize(
                HandoffDraft(
                    objective=initial.content.objective,
                    state=(HandoffStatement(text="Session B state.", citations=(citation,)),),
                    disposition="continuable",
                )
            )

            current = await handoffs.commit(session_a)
            with pytest.raises(RevisionConflictError) as stale:
                await handoffs.commit(session_b)
            with pytest.raises(HandoffScopeMismatchError):
                await runtime.handoff.for_scope(other.scope_id).continue_from(session_a)

            assert stale.value.artifact == session_b.base
            assert stale.value.current == current
            assert await handoffs.revisions() == (first, current)

    asyncio.run(scenario())


def test_handoff_runtime_recovers_only_explicitly_committed_milestones(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(
                url=f"sqlite+aiosqlite:///{tmp_path / 'handoff.db'}",
            )
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Durable Handoff", idempotency_key="project")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="Persist only an explicitly selected milestone.",
                    metadata={},
                )
            )
            prepared = await runtime.handoff.for_scope(scope.scope_id).finalize(
                HandoffDraft(
                    objective="Recover the selected milestone.",
                    state=(
                        HandoffStatement(
                            text="The temporary Handoff is ready for transfer.",
                            citations=(HandoffSourceCitation(source_ref=source.source_ref),),
                        ),
                    ),
                    disposition="continuable",
                    next_action=HandoffStatement(
                        text="Commit the reviewed milestone.",
                        citations=(HandoffSourceCitation(source_ref=source.source_ref),),
                    ),
                )
            )
            transferred_value = prepared.model_dump_json(by_alias=True)

        async with open_builtin_runtime(config) as runtime:
            handoffs = runtime.handoff.for_scope(scope.scope_id)
            restored_temporary = PreparedHandoff.model_validate_json(transferred_value)

            assert (await handoffs.continue_latest()).status == "empty"
            assert (await handoffs.continue_from(restored_temporary)).status == "resolved"
            committed = await handoffs.commit(restored_temporary)

        async with open_builtin_runtime(config) as runtime:
            handoffs = runtime.handoff.for_scope(scope.scope_id)
            recovered = await handoffs.continue_latest()

            assert recovered.status == "resolved"
            assert recovered.selection == "latest"
            assert recovered.content == prepared.content
            assert recovered.selected_revision == committed.as_ref()
            assert recovered.current_revision == committed.as_ref()
            assert await handoffs.revisions() == (committed,)

    asyncio.run(scenario())
