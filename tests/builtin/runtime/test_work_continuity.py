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
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.errors import InvalidStoredPayloadError
from powercontext.builtin.persistence.sources import SOURCES_TABLE
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    HandoffContent,
    HandoffSourceCitation,
    HandoffStatement,
    InvalidRuntimeRequestError,
    PreparedHandoff,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.work import (
    AcknowledgeHandoff,
    CreateWorkContract,
    CurrentWorkHandoff,
    HandoffCurrentWork,
    HandoffReceipt,
    ReceiverChecks,
    RecordTaskOutcome,
    TaskOutcome,
    WorkClaim,
    WorkContract,
)
from powercontext.sources import SourceRef

CONFIRMED_RECEIVER_CHECKS = ReceiverChecks(
    live_state="confirmed",
    capability="confirmed",
    authorization="confirmed",
)
UNCHECKED_RECEIVER_CHECKS = ReceiverChecks(
    live_state="not_checked",
    capability="not_checked",
    authorization="not_checked",
)


async def _create_scope(runtime: BuiltinRuntime, idempotency_key: str) -> str:
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(
        ScopeDraft(title="Work Test", summary="Work continuity test", idempotency_key=idempotency_key)
    )
    return scope.scope_id


def test_verified_work_claims_require_exact_evidence() -> None:
    with pytest.raises(ValidationError, match="verified Work claims require exact evidence"):
        WorkClaim(text="Tests pass.", basis="verified")

    with pytest.raises(ValidationError, match="declared Work claims cannot present evidence as verified"):
        WorkClaim(
            text="Tests pass.",
            basis="declared",
            evidence=(
                HandoffSourceCitation(
                    source_ref=SourceRef(source_type="content", source_id="test-output"),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="at most 31 items"):
        WorkClaim(
            text="The handoff reserves one citation for its captured boundary.",
            basis="verified",
            evidence=tuple(
                HandoffSourceCitation(
                    source_ref=SourceRef(source_type="content", source_id=f"evidence-{index}"),
                )
                for index in range(32)
            ),
        )


def test_acknowledgement_cannot_accept_unavailable_handoff_evidence() -> None:
    async def scenario() -> None:
        missing = HandoffSourceCitation(
            source_ref=SourceRef(source_type="content", source_id="missing-output"),
        )
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "unavailable-handoff-evidence")
            prepared = PreparedHandoff(
                scope_id=scope_id,
                base=None,
                content=HandoffContent(
                    objective="Continue a partially verified change.",
                    state=(HandoffStatement(text="The change was reported as implemented.", citations=(missing,)),),
                    disposition="continuable",
                ),
            )
            work = runtime.work.for_scope(scope_id)

            with pytest.raises(InvalidRuntimeRequestError, match="handoff-evidence-unavailable"):
                await work.acknowledge(
                    AcknowledgeHandoff(
                        source_id="receipt-accepted",
                        receiver="receiver-agent",
                        status="accepted",
                        selection="prepared",
                        receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                        prepared=prepared,
                    )
                )

            clarification = await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-clarification",
                    receiver="receiver-agent",
                    status="needs_clarification",
                    selection="prepared",
                    prepared=prepared,
                    message="The cited implementation output is unavailable.",
                )
            )

        assert clarification.resolution.evidence_checks[0].status == "unavailable"
        assert clarification.receipt.kind == "handoff-receipt"
        assert clarification.receipt.position == 1

    asyncio.run(scenario())


def test_acknowledgement_requires_an_inspected_target_and_receiver_checks() -> None:
    revision = ArtifactRef(family="handoff", artifact_id="handoff", revision=1)

    with pytest.raises(ValidationError, match="Input should be 'prepared' or 'exact'"):
        AcknowledgeHandoff.model_validate({
            "source_id": "receipt-latest",
            "receiver": "receiver-agent",
            "status": "accepted",
            "selection": "latest",
            "receiver_checks": CONFIRMED_RECEIVER_CHECKS.model_dump(),
        })

    with pytest.raises(ValidationError, match="requires all receiver checks"):
        AcknowledgeHandoff(
            source_id="receipt-missing-checks",
            receiver="receiver-agent",
            status="accepted",
            selection="exact",
            revision=revision,
        )

    with pytest.raises(ValidationError, match="requires all receiver checks"):
        AcknowledgeHandoff(
            source_id="receipt-unchecked",
            receiver="receiver-agent",
            status="accepted",
            selection="exact",
            receiver_checks=UNCHECKED_RECEIVER_CHECKS,
            revision=revision,
        )


def test_work_contract_rejects_a_verified_cross_record_claim_when_evidence_is_missing() -> None:
    async def scenario() -> None:
        contract = WorkContract(
            objective="Use only evidence-backed facts.",
            facts=(
                WorkClaim(
                    text="A regression test passed.",
                    basis="verified",
                    evidence=(
                        HandoffSourceCitation(
                            source_ref=SourceRef(source_type="content", source_id="missing-test-output"),
                        ),
                    ),
                ),
            ),
            in_scope=("Record a grounded delegation baseline.",),
            completion_criteria=("Reject unavailable verified evidence.",),
        )
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "missing-contract-evidence")
            with pytest.raises(LookupError):
                await runtime.work.for_scope(scope_id).create_contract(
                    CreateWorkContract(source_id="contract-1", contract=contract)
                )

    asyncio.run(scenario())


def test_continuity_projects_the_result_loop_in_stable_journal_order() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "continuity-result-loop")
            work = runtime.work.for_scope(scope_id)
            await work.create_contract(
                CreateWorkContract(
                    source_id="contract-1",
                    contract=WorkContract(
                        objective="Transfer an implementation safely.",
                        in_scope=("Implement the requested change.",),
                        completion_criteria=("Record the receiver's result.",),
                    ),
                )
            )
            prepared = await work.handoff_current(
                HandoffCurrentWork(
                    source_id="handoff-1",
                    handoff=CurrentWorkHandoff(
                        objective="Transfer an implementation safely.",
                        state=(WorkClaim(text="The implementation is ready for review."),),
                        disposition="continuable",
                        next_action=WorkClaim(text="Run the focused acceptance test."),
                    ),
                )
            )
            committed = await runtime.handoff.for_scope(scope_id).commit(prepared.handoff)
            before_acceptance = await work.continuity()
            acknowledgement = await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-1",
                    receiver="receiver-agent",
                    status="accepted",
                    selection="exact",
                    receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                    revision=committed.as_ref(),
                )
            )
            before_outcome = await work.continuity()
            await work.record_outcome(
                RecordTaskOutcome(
                    source_id="outcome-1",
                    outcome=TaskOutcome(
                        objective="Transfer an implementation safely.",
                        status="succeeded",
                        summary="The receiver completed the focused acceptance test.",
                        handoff_receipt_ref=acknowledgement.receipt.source_ref,
                        observations=(WorkClaim(text="The acceptance test passed."),),
                    ),
                )
            )
            complete = await work.continuity()

        assert before_acceptance.coverage.transfer_state == "awaiting_receipt"
        assert before_acceptance.coverage.outcome_state == "not_expected"
        assert before_outcome.coverage.transfer_state == "accepted"
        assert before_outcome.coverage.outcome_state == "awaiting_outcome"
        assert before_outcome.coverage.active_receipt_ref == acknowledgement.receipt.source_ref
        assert complete.coverage.transfer_state == "accepted"
        assert complete.coverage.outcome_state == "covered"
        assert complete.coverage.handoff_result_covered is True
        assert complete.coverage.active_receipt_ref == acknowledgement.receipt.source_ref
        assert complete.coverage.contract_records == 1
        assert complete.coverage.handoff_records == 1
        assert complete.coverage.acknowledgement_records == 1
        assert complete.coverage.outcome_records == 1
        assert [event.position for event in complete.events] == [1, 2, 3, 4]
        assert [event.kind for event in complete.events] == [
            "work-contract",
            "handoff-boundary",
            "handoff-receipt",
            "task-outcome",
        ]

    asyncio.run(scenario())


def test_continuity_does_not_cover_an_acceptance_with_an_unlinked_outcome() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "continuity-unlinked-outcome")
            work = runtime.work.for_scope(scope_id)
            prepared = await work.handoff_current(
                HandoffCurrentWork(
                    source_id="handoff-1",
                    handoff=CurrentWorkHandoff(
                        objective="Transfer one exact attempt.",
                        state=(WorkClaim(text="The attempt is ready."),),
                        disposition="continuable",
                    ),
                )
            )
            committed = await runtime.handoff.for_scope(scope_id).commit(prepared.handoff)
            await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-1",
                    receiver="receiver-agent",
                    status="accepted",
                    selection="exact",
                    receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                    revision=committed.as_ref(),
                )
            )
            await work.record_outcome(
                RecordTaskOutcome(
                    source_id="unlinked-outcome",
                    outcome=TaskOutcome(
                        objective="Complete unrelated work.",
                        status="succeeded",
                        summary="An unrelated attempt completed.",
                        observations=(WorkClaim(text="This result does not identify the accepted receipt."),),
                    ),
                )
            )

            continuity = await work.continuity()

        assert continuity.coverage.transfer_state == "accepted"
        assert continuity.coverage.outcome_state == "awaiting_outcome"
        assert continuity.coverage.handoff_result_covered is False

    asyncio.run(scenario())


def test_task_outcome_rejects_a_non_receipt_result_link() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "non-receipt-result-link")
            with pytest.raises(InvalidRuntimeRequestError, match="task-outcome-handoff-receipt"):
                await runtime.work.for_scope(scope_id).record_outcome(
                    RecordTaskOutcome(
                        source_id="outcome-1",
                        outcome=TaskOutcome(
                            objective="Link one exact accepted receipt.",
                            status="unknown",
                            summary="The requested Receipt does not exist.",
                            handoff_receipt_ref=SourceRef(
                                source_type="content",
                                source_id="missing-receipt",
                            ),
                            observations=(WorkClaim(text="No accepted Receipt was found."),),
                        ),
                    )
                )

    asyncio.run(scenario())


def test_continuity_excludes_malformed_work_records_without_failing_the_report() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "malformed-work-records")
            sources = runtime.sources.for_scope(scope_id)
            await sources.capture(CaptureSource(source_id="ordinary-1", content="ordinary", metadata={"kind": []}))
            await sources.capture(
                CaptureSource(source_id="malformed-1", content="{}", metadata={"kind": "task-outcome"})
            )

            continuity = await runtime.work.for_scope(scope_id).continuity()

        assert continuity.total_event_count == 0
        assert continuity.invalid_record_count == 1
        assert continuity.events == ()

    asyncio.run(scenario())


async def _corrupt_source_payload(runtime: BuiltinRuntime, scope_id: str, source_id: str) -> None:
    """Make one stored Source unreadable without changing its journal identity."""

    async with runtime._context(scope_id) as context:
        # The relational journal owns the only handle to the stored payload column.
        database = cast(Any, context.sources.journal)._database
        async with database.transaction() as connection:
            await connection.execute(
                update(SOURCES_TABLE)
                .where(SOURCES_TABLE.c.scope_id == scope_id, SOURCES_TABLE.c.source_id == source_id)
                .values(payload=b"\xff\xfestored-payload-is-not-decodable")
            )


async def _accept_exact_handoff(runtime: BuiltinRuntime, scope_id: str) -> tuple[ArtifactRef, SourceRef]:
    work = runtime.work.for_scope(scope_id)
    prepared = await work.handoff_current(
        HandoffCurrentWork(
            source_id="handoff-1",
            handoff=CurrentWorkHandoff(
                objective="Transfer one accepted attempt.",
                state=(WorkClaim(text="The attempt is ready."),),
                disposition="continuable",
            ),
        )
    )
    committed = await runtime.handoff.for_scope(scope_id).commit(prepared.handoff)
    acknowledgement = await work.acknowledge(
        AcknowledgeHandoff(
            source_id="receipt-1",
            receiver="receiver-agent",
            status="accepted",
            selection="exact",
            receiver_checks=CONFIRMED_RECEIVER_CHECKS,
            revision=committed.as_ref(),
        )
    )
    return committed.as_ref(), acknowledgement.receipt.source_ref


def test_outcome_rejects_a_receipt_reference_that_does_not_match_its_source_type() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "receipt-source-type-mismatch")
            work = runtime.work.for_scope(scope_id)
            _, receipt_ref = await _accept_exact_handoff(runtime, scope_id)

            mismatched = SourceRef(source_type="note", source_id=receipt_ref.source_id)
            assert mismatched.source_id == receipt_ref.source_id
            with pytest.raises(InvalidRuntimeRequestError) as rejection:
                await work.record_outcome(
                    RecordTaskOutcome(
                        source_id="outcome-1",
                        outcome=TaskOutcome(
                            objective="Reuse a receipt under the wrong Source type.",
                            status="unknown",
                            summary="The same source_id under another type is a different Source.",
                            handoff_receipt_ref=mismatched,
                            observations=(WorkClaim(text="No receipt exists under that Source type."),),
                        ),
                    )
                )
            continuity = await work.continuity()

        assert rejection.value.code == "task-outcome-handoff-receipt"
        assert continuity.coverage.outcome_records == 0
        assert continuity.coverage.outcome_state == "awaiting_outcome"

    asyncio.run(scenario())


def test_outcome_does_not_load_unrelated_unreadable_history() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "outcome-survives-unreadable-history")
            work = runtime.work.for_scope(scope_id)
            selected, receipt_ref = await _accept_exact_handoff(runtime, scope_id)
            sources = runtime.sources.for_scope(scope_id)
            await sources.capture(CaptureSource(source_id="rotten-1", content="ordinary", metadata={"kind": "note"}))
            await _corrupt_source_payload(runtime, scope_id, "rotten-1")

            outcome = await work.record_outcome(
                RecordTaskOutcome(
                    source_id="outcome-1",
                    outcome=TaskOutcome(
                        objective="Transfer one accepted attempt.",
                        status="succeeded",
                        summary="The result is recorded despite an unrelated unreadable Source.",
                        handoff_receipt_ref=receipt_ref,
                        observations=(WorkClaim(text="The receiver reported completion."),),
                    ),
                )
            )
            with pytest.raises(InvalidStoredPayloadError):
                await work.continuity(selected)

        assert outcome.kind == "task-outcome"
        assert outcome.source_ref.source_id == "outcome-1"

    asyncio.run(scenario())


def test_outcome_rejects_a_receipt_whose_content_is_malformed() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "malformed-receipt-content")
            work = runtime.work.for_scope(scope_id)
            selected, receipt_ref = await _accept_exact_handoff(runtime, scope_id)
            sources = runtime.sources.for_scope(scope_id)
            await sources.capture(
                CaptureSource(source_id="broken-receipt", content="{}", metadata={"kind": "handoff-receipt"})
            )
            # A readable receipt body stored under a different kind is still not a Handoff receipt.
            await sources.capture(
                CaptureSource(
                    source_id="receipt-other-kind",
                    content=HandoffReceipt(
                        receiver="receiver-agent",
                        status="accepted",
                        selection="exact",
                        selected_revision=selected,
                        receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                        evidence_status="available",
                    ).model_dump_json(by_alias=True),
                    metadata={"kind": "task-outcome"},
                )
            )

            for source_id in ("broken-receipt", "receipt-other-kind"):
                with pytest.raises(InvalidRuntimeRequestError) as rejection:
                    await work.record_outcome(
                        RecordTaskOutcome(
                            source_id=f"outcome-{source_id}",
                            outcome=TaskOutcome(
                                objective="Reject an unusable receipt body.",
                                status="unknown",
                                summary="Only a readable accepted exact receipt may be referenced.",
                                handoff_receipt_ref=SourceRef(source_type=receipt_ref.source_type, source_id=source_id),
                                observations=(WorkClaim(text="The referenced Source is not a valid receipt."),),
                            ),
                        )
                    )
                assert rejection.value.code == "task-outcome-handoff-receipt"

            continuity = await work.continuity()

        # Only a Source that declares a Work kind counts as invalid history; the unreadable
        # non-Work body stays outside the projection even though neither could be referenced.
        assert continuity.invalid_record_count == 2
        assert continuity.coverage.outcome_records == 0
        assert continuity.coverage.transfer_state == "accepted"
        assert continuity.coverage.outcome_state == "awaiting_outcome"

    asyncio.run(scenario())


def test_outcome_covers_an_accepted_exact_receipt_after_a_longer_history() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "outcome-exact-receipt-after-long-history")
            work = runtime.work.for_scope(scope_id)
            prepared = await work.handoff_current(
                HandoffCurrentWork(
                    source_id="handoff-1",
                    handoff=CurrentWorkHandoff(
                        objective="Resume one accepted attempt.",
                        state=(WorkClaim(text="The attempt was accepted by the receiver."),),
                        disposition="continuable",
                    ),
                )
            )
            committed = await runtime.handoff.for_scope(scope_id).commit(prepared.handoff)
            acknowledgement = await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-1",
                    receiver="receiver-agent",
                    status="accepted",
                    selection="exact",
                    receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                    revision=committed.as_ref(),
                )
            )
            sources = runtime.sources.for_scope(scope_id)
            for index in range(96):
                await sources.capture(
                    CaptureSource(
                        source_id=f"ordinary-{index}",
                        content=f"unrelated note {index}",
                        metadata={"kind": "analysis-note"},
                    )
                )

            outcome = await work.record_outcome(
                RecordTaskOutcome(
                    source_id="outcome-1",
                    outcome=TaskOutcome(
                        objective="Resume one accepted attempt.",
                        status="succeeded",
                        summary="The accepted attempt completed after unrelated records were appended.",
                        handoff_receipt_ref=acknowledgement.receipt.source_ref,
                        observations=(WorkClaim(text="The receiver reported completion."),),
                    ),
                )
            )
            covered = await work.continuity(committed.as_ref())

        assert outcome.kind == "task-outcome"
        assert outcome.position > acknowledgement.receipt.position
        assert covered.coverage.transfer_state == "accepted"
        assert covered.coverage.outcome_state == "covered"
        assert covered.coverage.handoff_result_covered is True
        assert covered.coverage.active_receipt_ref == acknowledgement.receipt.source_ref
        assert covered.total_event_count == 3
        assert covered.invalid_record_count == 0
        assert covered.truncated is False

    asyncio.run(scenario())


def test_outcome_rejects_a_receipt_that_is_not_accepted_exact() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "outcome-rejects-declined-receipt")
            work = runtime.work.for_scope(scope_id)
            prepared = await work.handoff_current(
                HandoffCurrentWork(
                    source_id="handoff-1",
                    handoff=CurrentWorkHandoff(
                        objective="Transfer one attempt that is not accepted.",
                        state=(WorkClaim(text="The attempt is ready."),),
                        disposition="continuable",
                    ),
                )
            )
            committed = await runtime.handoff.for_scope(scope_id).commit(prepared.handoff)
            declined = await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-declined",
                    receiver="receiver-agent",
                    status="declined",
                    selection="exact",
                    revision=committed.as_ref(),
                    message="The receiver declined the selection.",
                )
            )
            prepared_target = await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-prepared",
                    receiver="receiver-agent",
                    status="accepted",
                    selection="prepared",
                    receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                    prepared=prepared.handoff,
                )
            )

            for receipt_ref in (declined.receipt.source_ref, prepared_target.receipt.source_ref):
                with pytest.raises(InvalidRuntimeRequestError) as rejection:
                    await work.record_outcome(
                        RecordTaskOutcome(
                            source_id=f"outcome-{receipt_ref.source_id}",
                            outcome=TaskOutcome(
                                objective="Reject an unusable receipt link.",
                                status="unknown",
                                summary="Only an accepted exact Receipt may be referenced.",
                                handoff_receipt_ref=receipt_ref,
                                observations=(WorkClaim(text="The Receipt is not an accepted exact target."),),
                            ),
                        )
                    )
                assert rejection.value.code == "task-outcome-handoff-receipt"

            continuity = await work.continuity(committed.as_ref())

        assert continuity.coverage.transfer_state == "declined"
        assert continuity.coverage.outcome_records == 0
        assert continuity.coverage.handoff_result_covered is False

    asyncio.run(scenario())
